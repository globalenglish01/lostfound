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


def _find_first(text: str, table: dict[str, list[str]]) -> tuple[str | None, str | None]:
    """在描述里找词典命中，返回 (canonical, 原文片段)。"""
    low = norm_text(text)
    best: tuple[int, str, str] | None = None
    for canon, aliases in table.items():
        if canon.startswith("_"):
            continue
        for alias in [canon, *aliases]:
            a = norm_text(alias)
            if not a:
                continue
            pos = low.find(a)
            if pos >= 0 and (best is None or len(a) > len(best[2])):
                best = (pos, canon, alias)
    if best is None:
        return None, None
    return best[1], best[2]


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

        cat, cat_src = _find_first(text, syn.get("category", {}))
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

        return {
            "category": field(canonical_category(cat) if cat else None, cat_src, 0.9),
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
