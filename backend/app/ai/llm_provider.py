"""LLM Provider（可插拔）。

默认 `rule`：纯规则实现，不调用任何外部付费 API，CI/离线可跑。
生产可切 `openai_compatible`，指向企业自有网关或自托管模型。

无论用哪个 provider，边界不变：
    LLM 是 Evidence Analyst，不是万能裁判。
    LLM 不得覆盖 Algorithm Score（算法 52 分，LLM 觉得像也不能改成 90）。
"""
from __future__ import annotations

import json
import re
from typing import Any, Protocol

from ..config import settings
from . import prompts
from .normalize import (
    canonical_brand,
    canonical_category,
    canonical_color,
    canonical_model,
    norm_text,
)
from ..config import synonyms


class LLMProvider(Protocol):
    name: str
    model: str

    def extract(self, description: str) -> dict[str, Any]: ...

    def analyze_match(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def explain(self, payload: dict[str, Any]) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------
# 规则实现
# ---------------------------------------------------------------------------

_IMEI = re.compile(r"\b\d{15}\b")
_SERIAL = re.compile(r"\b(?=[A-Za-z0-9]{8,20}\b)(?=.*\d)(?=.*[A-Za-z])[A-Za-z0-9]{8,20}\b")
_MODEL_PATTERNS = [
    re.compile(r"iphone\s*\d{1,2}\s*(?:pro\s*max|pro|plus|mini)?", re.I),
    re.compile(r"galaxy\s*[a-z]?\d{1,3}\s*(?:ultra|plus|\+)?", re.I),
    re.compile(r"pixel\s*\d{1,2}\s*(?:pro|a)?", re.I),
    re.compile(r"xperia\s*\d{1,2}\s*(?:[ivx]+)?", re.I),
    re.compile(r"macbook\s*(?:air|pro)?\s*\d{0,2}", re.I),
    re.compile(r"ipad\s*(?:air|pro|mini)?\s*\d{0,2}", re.I),
    re.compile(r"airpods\s*(?:pro|max)?\s*\d?", re.I),
]
_DISTINCTIVE_MARKERS = ["贴纸", "シール", "sticker", "图案", "圖案", "柄", "pattern",
                        "刻字", "刻印", "engraving", "裂痕", "划痕", "傷", "crack",
                        "scratch", "挂件", "吊饰", "ストラップ", "strap"]
_CASE_MARKERS = ["手机壳", "手機殼", "手机套", "保护壳", "保护套", "保護ケース",
                 "ケース", "カバー", "case", "cover"]


_ASCII_WORD = re.compile(r"[a-z0-9]+")
# CJK 汉字（含扩展 A）。假名、拉丁字母、数字、标点都不算。
_IDEOGRAPH = re.compile(r"[一-鿿㐀-䶿]")


def _is_ideograph(ch: str) -> bool:
    return bool(ch) and bool(_IDEOGRAPH.match(ch))


def _alias_hit(haystack: str, alias: str) -> bool:
    """带边界判断的别名命中。

    三个坑：
    1) 纯 ASCII 别名（bag / key / case）必须按词边界匹配，
       否则 "bag" 命中 "baggage"、"key" 命中 "monkey"。
    2) 单个汉字别名（鞄 / 傘 / 鍵 / 本 / 包）不能裸做子串匹配：
       「紙で包装されています」里的「包」会把一瓶清酒判成包。
       但也不能一刀切禁掉——日语里「黒い鞄を紛失」的「鞄」正是物品本身。
       规则：单字汉字只有在**左右都不是汉字**时才算命中。
           黒い鞄を   -> 左「い」右「を」都是假名 -> 命中
           紙で包装   -> 右「装」是汉字（构成复合词）-> 不命中
           日本酒     -> 「酒」左边「本」是汉字 -> 不命中（更长的别名「日本酒」会先命中）
    3) 单个假名/字母别名歧义太大，只在整串相等时命中。
    """
    if _ASCII_WORD.fullmatch(alias):
        return any(w == alias for w in _ASCII_WORD.findall(haystack))
    if len(alias) == 1:
        if not _is_ideograph(alias):
            return haystack == alias
        for i, ch in enumerate(haystack):
            if ch != alias:
                continue
            left = haystack[i - 1] if i > 0 else ""
            right = haystack[i + 1] if i + 1 < len(haystack) else ""
            if not _is_ideograph(left) and not _is_ideograph(right):
                return True
        return False
    return alias in haystack


def _find_all(text: str, table: dict[str, list[str]]) -> list[tuple[str, str]]:
    """在描述里找**所有**词典命中，按别名长度降序返回 [(canonical, 原文片段)]。

    只取最长的一个会出事：「left a bottle of sake」里 bottle(6) 比 sake(4) 长，
    直接判成 water_bottle，那瓶清酒就再也匹配不上了。
    歧义必须原样保留，交给下游按「UNKNOWN != CONFLICT」处理。
    """
    low = norm_text(text)
    pairs: list[tuple[int, str, str]] = []
    for canon, aliases in table.items():
        if canon.startswith("_"):
            continue
        for alias in [canon, *aliases]:
            a = norm_text(alias)
            if a:
                pairs.append((len(a), canon, a))

    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for _, canon, alias in sorted(pairs, key=lambda c: -c[0]):
        if canon not in seen and _alias_hit(low, alias):
            seen.add(canon)
            out.append((canon, alias))
    return out


def _find_first(text: str, table: dict[str, list[str]]) -> tuple[str | None, str | None]:
    hits = _find_all(text, table)
    return hits[0] if hits else (None, None)


def _window(text: str, marker: str, before: int = 8) -> str:
    """取 marker 前 N 个字符，用来抓「猫咪贴纸」「透明手机壳」这类修饰语。"""
    low = text.lower()
    idx = low.find(marker.lower())
    if idx < 0:
        return ""
    start = max(0, idx - before)
    return text[start:idx + len(marker)].strip()


class RuleLLM:
    """确定性规则引擎：无外部依赖，输出与 Prompt schema 同构。"""

    name = "rule"
    model = "rule-engine-v1"

    # -- ① 抽取 ---------------------------------------------------------
    def extract(self, description: str) -> dict[str, Any]:
        text = description or ""
        syn = synonyms()

        cat_hits = _find_all(text, syn.get("category", {}))
        cat, cat_src = cat_hits[0] if cat_hits else (None, None)

        # 词典未命中 -> 用向量对类别原型做零样本分类兜底
        inferred_cat = False
        if not cat_hits:
            from .classify import is_enabled, zero_shot_category

            if is_enabled():
                from .normalize import strip_report_boilerplate

                guess, sim = zero_shot_category(strip_report_boilerplate(text))
                if guess:
                    cat, cat_src = guess, f"zero-shot({sim:.2f})"
                    inferred_cat = True
        brand, brand_src = _find_first(text, syn.get("brand", {}))
        color, color_src = _find_first(text, syn.get("color", {}))
        material, material_src = _find_first(text, syn.get("material", {}))
        loc, loc_src = _find_first(text, syn.get("location", {}))

        model_val = None
        model_src = None
        for pat in _MODEL_PATTERNS:
            m = pat.search(text)
            if m:
                model_src = m.group(0).strip()
                model_val = canonical_model(model_src)
                break
        # 型号能反推品牌
        if model_val and not brand:
            if model_val.startswith(("iphone", "ipad", "macbook", "airpods")):
                brand, brand_src = "Apple", model_src
            elif model_val.startswith("galaxy"):
                brand, brand_src = "Samsung", model_src
            elif model_val.startswith("xperia"):
                brand, brand_src = "Sony", model_src

        case_val = None
        case_src = None
        for marker in _CASE_MARKERS:
            frag = _window(text, marker)
            if frag:
                case_src = frag
                case_color = canonical_color(frag)
                case_val = case_color or "case"
                break

        distinctive: list[dict[str, Any]] = []
        for marker in _DISTINCTIVE_MARKERS:
            frag = _window(text, marker, before=6)
            if frag:
                distinctive.append({
                    "value": norm_text(frag),
                    "original": frag,
                    "confidence": 0.75,
                    "source_type": "EXPLICIT",
                })

        serials = [{"type": "IMEI", "value": s} for s in _IMEI.findall(text)]
        if not serials:
            for s in _SERIAL.findall(text):
                if not any(s.lower() in norm_text(x or "") for x in (model_src, brand_src)):
                    serials.append({"type": "SERIAL", "value": s})
                    break

        def field(value, original, conf, stype="EXPLICIT"):
            return {
                "value": value,
                "original": original,
                "confidence": conf if value else 0.0,
                "source_type": stype if value else "UNCERTAIN",
            }

        # 命中多个类别、或靠向量推断出来的 -> 标记为 UNCERTAIN，下游不得据此判冲突
        if inferred_cat:
            cat_candidates = [cat] if cat else []
            cat_node = field(cat, cat_src, 0.6, "INFERRED")
            cat_node["inferred"] = True
        else:
            cat_candidates = [canonical_category(c) or c for c, _ in cat_hits]
            cat_node = field(canonical_category(cat) if cat else None, cat_src,
                             0.9 if len(cat_hits) <= 1 else 0.5,
                             "EXPLICIT" if len(cat_hits) <= 1 else "UNCERTAIN")
        cat_node["candidates"] = cat_candidates

        return {
            "category": cat_node,
            "brand": field(canonical_brand(brand) if brand else None, brand_src, 0.9),
            "model": field(model_val, model_src, 0.88),
            "color": field(canonical_color(color) if color else None, color_src, 0.85),
            "material": field(material, material_src, 0.8),
            "size": field(None, None, 0.0),
            "case": field(case_val, case_src, 0.8),
            "distinctive_features": distinctive,
            "contents": [],
            "location": {
                "name": loc,
                "original": loc_src,
                "confidence": 0.85 if loc else 0.0,
                "source_type": "EXPLICIT" if loc else "UNCERTAIN",
            },
            "time": {"from": None, "to": None, "confidence": 0.0, "source_type": "UNCERTAIN"},
            "serial_numbers": serials,
            "uncertain_attributes": [],
            "raw_description": text,
        }

    # -- ② 匹配分析 -----------------------------------------------------
    def analyze_match(self, payload: dict[str, Any]) -> dict[str, Any]:
        """基于算法算出的证据做确定性裁决，绝不重算分数。"""
        score = float(payload.get("algorithm_score") or 0.0)
        conflicts = payload.get("conflicts") or []
        severities = {c.get("severity") for c in conflicts}

        supporting = [e for e in payload.get("evidences", []) if not e.get("is_conflict")]
        strong = [e for e in supporting
                  if e.get("evidence_type") in {"DISTINCTIVE", "ATTRIBUTE"}
                  and float(e.get("similarity_score") or 0) >= 95]

        if "CRITICAL" in severities:
            decision, action, conf = "NOT_MATCH", "DO_NOT_RECOMMEND", 0.95
        elif "MAJOR" in severities:
            decision, action, conf = "UNLIKELY_MATCH", "DO_NOT_RECOMMEND", 0.8
        elif score >= 95 and len(strong) >= 2:
            decision, action, conf = "MATCH", "AUTO_RECOMMEND", 0.93
        elif score >= 85:
            decision, action, conf = "LIKELY_MATCH", "HUMAN_REVIEW", 0.85
        elif score >= 70:
            decision, action, conf = "POSSIBLE_MATCH", "HUMAN_REVIEW", 0.65
        elif score >= 50:
            decision, action, conf = "UNLIKELY_MATCH", "DO_NOT_RECOMMEND", 0.55
        else:
            decision, action, conf = "NOT_MATCH", "DO_NOT_RECOMMEND", 0.6

        return {
            "decision": decision,
            "confidence": conf,
            "supporting_evidence": [
                {
                    "feature": e.get("field_name"),
                    "lost_value": e.get("lost_value"),
                    "found_value": e.get("found_value"),
                    "relation": e.get("relation"),
                    "strength": ("STRONG" if float(e.get("similarity_score") or 0) >= 95
                                 else "MODERATE" if float(e.get("similarity_score") or 0) >= 75
                                 else "WEAK"),
                    "reason": e.get("explanation") or "",
                }
                for e in supporting
            ],
            "conflicting_evidence": [
                {
                    "feature": c.get("field_name"),
                    "lost_value": c.get("lost_value"),
                    "found_value": c.get("found_value"),
                    "relation": f"{c.get('severity')}_CONFLICT",
                    "severity": c.get("severity"),
                    "reason": c.get("reason") or "",
                }
                for c in conflicts
            ],
            "unknown_evidence": [
                {"feature": f, "reason": "任一侧缺失该信息，按 Available Evidence 规则不参与评分"}
                for f in payload.get("unknown_features", [])
            ],
            "key_matching_features": [e.get("field_name") for e in strong],
            "key_conflicting_features": [c.get("field_name") for c in conflicts],
            "reasoning_summary": "",
            "recommended_action": action,
        }

    # -- ③ 解释 ---------------------------------------------------------
    def explain(self, payload: dict[str, Any]) -> dict[str, Any]:
        supporting = payload.get("supporting_evidence") or []
        conflicts = payload.get("conflicting_evidence") or []
        unknown = payload.get("unknown_evidence") or []
        score = payload.get("score")
        decision = payload.get("decision")

        titles = {
            "MATCH": "高度疑似同一物品",
            "LIKELY_MATCH": "高度疑似匹配",
            "POSSIBLE_MATCH": "可能匹配",
            "UNLIKELY_MATCH": "匹配可能性较低",
            "NOT_MATCH": "不建议作为匹配项",
        }
        strong_lines = [
            f"{e.get('feature')}：{e.get('lost_value')} / {e.get('found_value')}"
            for e in supporting if e.get("strength") == "STRONG"
        ]
        conflict_lines = [
            f"{c.get('feature')}（{c.get('severity')}）：{c.get('lost_value')} / {c.get('found_value')}"
            for c in conflicts
        ]
        summary_bits = []
        if strong_lines:
            summary_bits.append("主要依据：" + "；".join(strong_lines[:5]))
        if conflict_lines:
            summary_bits.append("存在差异：" + "；".join(conflict_lines[:3]))
        if not summary_bits:
            summary_bits.append("可用证据有限，建议人工核对。")

        return {
            "title": f"{titles.get(decision, decision)}（{score} 分）",
            "summary": "。".join(summary_bits),
            "strong_matches": strong_lines,
            "conflicts": conflict_lines,
            "uncertainties": [u.get("feature") for u in unknown],
            "recommended_action": payload.get("recommended_action", "HUMAN_REVIEW"),
        }


# ---------------------------------------------------------------------------
# OpenAI 兼容网关
# ---------------------------------------------------------------------------

class OpenAICompatibleLLM:
    """企业自有网关 / 自托管模型。使用 prompts.py 里的三个 Prompt。"""

    name = "openai_compatible"

    def __init__(self) -> None:
        self.model = settings.llm_model
        self.base_url = settings.llm_base_url.rstrip("/")
        self.api_key = settings.llm_api_key
        if not self.base_url:
            raise RuntimeError("LF_LLM_BASE_URL 未配置")
        self._fallback = RuleLLM()

    def _chat(self, system: str, user: str) -> dict[str, Any]:
        import httpx

        resp = httpx.post(
            f"{self.base_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0,
                "response_format": {"type": "json_object"},
            },
            timeout=settings.llm_timeout,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return _loads_json(content)

    def extract(self, description: str) -> dict[str, Any]:
        user = prompts.EXTRACTION_USER.format(
            description=description,
            description_json=json.dumps(description, ensure_ascii=False),
        )
        return self._chat(prompts.EXTRACTION_SYSTEM, user)

    def analyze_match(self, payload: dict[str, Any]) -> dict[str, Any]:
        user = prompts.MATCH_ANALYSIS_USER.format(
            lost_record=json.dumps(payload.get("lost"), ensure_ascii=False, indent=2),
            found_record=json.dumps(payload.get("found"), ensure_ascii=False, indent=2),
            semantic_score=payload.get("semantic_score"),
            keyword_score=payload.get("keyword_score"),
            image_score=payload.get("image_score"),
            category_score=payload.get("category_score"),
            attribute_score=payload.get("attribute_score"),
            location_score=payload.get("location_score"),
            time_score=payload.get("time_score"),
            distinctive_score=payload.get("distinctive_score"),
            conflicts=json.dumps(payload.get("conflicts"), ensure_ascii=False, indent=2),
        )
        return self._chat(prompts.MATCH_ANALYSIS_SYSTEM, user)

    def explain(self, payload: dict[str, Any]) -> dict[str, Any]:
        user = prompts.EXPLANATION_USER.format(
            decision=payload.get("decision"),
            score=payload.get("score"),
            confidence=payload.get("confidence"),
            supporting_evidence=json.dumps(payload.get("supporting_evidence"),
                                           ensure_ascii=False, indent=2),
            conflicting_evidence=json.dumps(payload.get("conflicting_evidence"),
                                            ensure_ascii=False, indent=2),
            unknown_evidence=json.dumps(payload.get("unknown_evidence"),
                                        ensure_ascii=False, indent=2),
        )
        return self._chat(prompts.EXPLANATION_SYSTEM, user)


def _loads_json(content: str) -> dict[str, Any]:
    """LLM 有时会包 ```json 围栏，剥掉再解析。"""
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


_PROVIDERS = {"rule": RuleLLM, "openai_compatible": OpenAICompatibleLLM}
_cache: LLMProvider | None = None


def get_llm_provider() -> LLMProvider:
    global _cache
    if _cache is None:
        cls = _PROVIDERS.get(settings.llm_provider)
        if cls is None:
            raise RuntimeError(f"未知 LLM provider: {settings.llm_provider}")
        _cache = cls()
    return _cache


def reset_llm_provider() -> None:
    global _cache
    _cache = None
