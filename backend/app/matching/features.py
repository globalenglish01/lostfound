"""八大维度特征打分。

所有 s_i 归一化到 0~100。任一侧缺失 -> 返回 None，该项**不参与评分**
（Available Evidence Normalization），而不是记 0 分。

    Missing != Mismatch
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..ai.normalize import (
    canonical_attribute_code,
    color_family,
    model_tokens,
    norm_text,
    text_overlap,
)
from ..config import attribute_profile, matching_weights, reliability_of


@dataclass
class FeatureScore:
    """一条特征证据。`score is None` 表示证据缺失，不参与加权。"""

    name: str                       # 维度名：category/attribute/location/...
    field_name: str                 # 具体字段名
    score: float | None
    reliability: float = 1.0
    lost_value: Any = None
    found_value: Any = None
    relation: str = "UNKNOWN"
    explanation: str = ""
    weight: float = 0.0             # 由 scoring 层回填
    contribution: float = 0.0
    is_conflict: bool = False
    severity: str | None = None
    children: list["FeatureScore"] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Category
# ---------------------------------------------------------------------------

def category_score(lost: dict, found: dict, taxonomy: dict[str, str] | None = None) -> FeatureScore:
    """完全相同 100 / 同一父类 70 / 相关 30 / 无关 0。"""
    rel = matching_weights()["category_relation_scores"]
    a, b = lost.get("category"), found.get("category")
    if not a or not b:
        return FeatureScore("category", "category", None, lost_value=a, found_value=b,
                            explanation="任一侧未知类别，不参与评分")
    if norm_text(a) == norm_text(b):
        return FeatureScore("category", "category", float(rel["SAME"]), 1.0, a, b,
                            "EXACT_MATCH", "类别完全一致")
    taxonomy = taxonomy or {}
    pa, pb = taxonomy.get(a), taxonomy.get(b)
    if pa and pb and pa == pb:
        return FeatureScore("category", "category", float(rel["SAME_PARENT"]), 1.0, a, b,
                            "PARTIAL_MATCH", f"同属父类 {pa}")
    if (pa and pa == b) or (pb and pb == a):
        return FeatureScore("category", "category", float(rel["RELATED"]), 1.0, a, b,
                            "PARTIAL_MATCH", "存在父子层级关系")
    return FeatureScore("category", "category", float(rel["UNRELATED"]), 1.0, a, b,
                        "MAJOR_CONFLICT", "类别不相关", is_conflict=True, severity="MAJOR")


# ---------------------------------------------------------------------------
# Attribute（二级加权）
# ---------------------------------------------------------------------------

def _attr_similarity(code: str, a: str, b: str) -> tuple[float, str]:
    """单个属性的相似度 0~100 与关系类型。"""
    na, nb = norm_text(a), norm_text(b)
    if not na or not nb:
        return 0.0, "UNKNOWN"
    if na == nb:
        return 100.0, "EXACT_MATCH"

    if code == "color":
        fa, fb = color_family(a), color_family(b)
        if fa and fa == fb:
            # 「黑色」vs「深灰色」：同族，高分但不满分
            return 80.0, "SEMANTIC_MATCH"
        return 0.0, "MINOR_CONFLICT"

    if code in {"model"}:
        ta, tb = model_tokens(a), model_tokens(b)
        if ta == tb:
            return 100.0, "EXACT_MATCH"
        # 一个是另一个的前缀（iPhone 15 Pro vs iPhone 15 Pro Max）-> 冲突交给 conflicts 模块
        if ta[: len(tb)] == tb or tb[: len(ta)] == ta:
            return 30.0, "PARTIAL_MATCH"
        return 0.0, "MAJOR_CONFLICT"

    if code in {"serial_number", "imei", "passport_number"}:
        return (100.0, "EXACT_MATCH") if na == nb else (0.0, "CRITICAL_CONFLICT")

    # 通用：字符级重叠（中日文没有空格，不能用词集合）
    if na in nb or nb in na:
        return 85.0, "PARTIAL_MATCH"
    ratio = text_overlap(a, b)
    if ratio >= 0.75:
        return round(60 + 40 * ratio, 2), "SEMANTIC_MATCH"
    if ratio >= 0.4:
        return round(50 + 30 * ratio, 2), "PARTIAL_MATCH"
    return 0.0, "MINOR_CONFLICT"


def attribute_score(lost_attrs: list[dict], found_attrs: list[dict],
                    category_code: str | None) -> FeatureScore:
    """S_a = sum(w_i * s_i) / sum(w_i)，属性权重来自 category profile。

    lost_attrs / found_attrs 元素形如
        {"attribute_code": "model", "value_text": "iPhone 15 Pro", "source": "USER"}
    """
    profile = attribute_profile(category_code)
    lost_map = {canonical_attribute_code(a.get("attribute_code")): a for a in lost_attrs}
    found_map = {canonical_attribute_code(a.get("attribute_code")): a for a in found_attrs}

    children: list[FeatureScore] = []
    num = den = 0.0
    for code in sorted(set(lost_map) & set(found_map)):
        if code in {"distinctive"}:      # distinctive 单独成一个维度
            continue
        la, fa = lost_map[code], found_map[code]
        lv, fv = la.get("value_text"), fa.get("value_text")
        if not lv or not fv:
            children.append(FeatureScore("attribute", code, None, lost_value=lv, found_value=fv,
                                         explanation="任一侧缺失，不参与评分"))
            continue
        s, relation = _attr_similarity(code, lv, fv)
        w = float(profile.get(code, profile.get("default", 1)))
        # 每条证据带自己的可靠性（取两侧较低者，用户可能记错）
        r = min(reliability_of(la.get("source")), reliability_of(fa.get("source")))
        num += w * r * s
        den += w * r
        children.append(FeatureScore("attribute", code, s, r, lv, fv, relation,
                                     weight=w, contribution=w * r * s))

    if den == 0:
        return FeatureScore("attribute", "attribute", None,
                            explanation="双方没有可比对的共同属性", children=children)
    return FeatureScore("attribute", "attribute", round(num / den, 4), 1.0,
                        relation="AGGREGATE", children=children,
                        explanation=f"{len([c for c in children if c.score is not None])} 项属性加权")


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------

EARTH_R = 6371000.0


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))


def location_zone(lost_loc: dict | None, found_loc: dict | None) -> str | None:
    """靠 location 树判断关系，解决「新宿站」vs「新宿站南口」。"""
    if not lost_loc or not found_loc:
        return None
    if lost_loc.get("id") and lost_loc["id"] == found_loc.get("id"):
        return "SAME_LOCATION"
    la, fa = lost_loc.get("ancestors") or [], found_loc.get("ancestors") or []
    lid, fid = lost_loc.get("id"), found_loc.get("id")
    if lid in fa or fid in la:
        return "SAME_FACILITY"
    common = set(la) & set(fa)
    if common:
        # 共同祖先越深（越靠近叶子），关系越紧密
        depth = max(la.index(c) for c in common if c in la)
        return "NEARBY" if depth == 0 else "SAME_AREA"
    return None


def location_score(lost_loc: dict | None, found_loc: dict | None,
                   tau_m: float = 500.0) -> FeatureScore:
    cfg = matching_weights()["location_zone_scores"]
    if not lost_loc or not found_loc:
        return FeatureScore("location", "location", None,
                            lost_value=(lost_loc or {}).get("name"),
                            found_value=(found_loc or {}).get("name"),
                            explanation="任一侧无地点信息，不参与评分")

    zone = location_zone(lost_loc, found_loc)
    if zone:
        s = float(cfg[zone])
        return FeatureScore("location", "location", s, 1.0,
                            lost_loc.get("name"), found_loc.get("name"),
                            "EXACT_MATCH" if zone == "SAME_LOCATION" else "SEMANTIC_MATCH",
                            f"地点关系：{zone}")

    if all(lost_loc.get(k) is not None for k in ("lat", "lon")) and \
       all(found_loc.get(k) is not None for k in ("lat", "lon")):
        d = haversine(lost_loc["lat"], lost_loc["lon"], found_loc["lat"], found_loc["lon"])
        s = 100.0 * math.exp(-d / max(tau_m, 1.0))
        return FeatureScore("location", "location", round(s, 4), 1.0,
                            lost_loc.get("name"), found_loc.get("name"), "SEMANTIC_MATCH",
                            f"直线距离约 {int(d)}m（tau={int(tau_m)}m）")

    if norm_text(lost_loc.get("name")) == norm_text(found_loc.get("name")):
        return FeatureScore("location", "location", float(cfg["SAME_LOCATION"]), 1.0,
                            lost_loc.get("name"), found_loc.get("name"), "EXACT_MATCH",
                            "地点名称一致")
    return FeatureScore("location", "location", float(cfg["UNRELATED"]), 1.0,
                        lost_loc.get("name"), found_loc.get("name"), "MINOR_CONFLICT",
                        "地点无法建立关联")


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------

def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def time_score(lost_start: datetime | None, lost_end: datetime | None,
               found_at: datetime | None, tau_hours: float = 24.0) -> FeatureScore:
    """S_t = 100 * exp(-dt / tau)。时间**不能作为绝对条件**。

    用户给的常是区间（昨晚 7~9 点）；found_at 落在区间内 -> 100。
    """
    if found_at is None or (lost_start is None and lost_end is None):
        return FeatureScore("time", "time", None,
                            explanation="时间信息不足，不参与评分")
    found_at = _aware(found_at)
    start = _aware(lost_start) if lost_start else None
    end = _aware(lost_end) if lost_end else None

    if start and end and start <= found_at <= end:
        return FeatureScore("time", "time", 100.0, 1.0,
                            f"{start.isoformat()} ~ {end.isoformat()}", found_at.isoformat(),
                            "EXACT_MATCH", "拾获时间落在丢失时间区间内")

    ref_deltas = []
    if start:
        ref_deltas.append(abs((found_at - start).total_seconds()))
    if end:
        ref_deltas.append(abs((found_at - end).total_seconds()))
    dt_h = min(ref_deltas) / 3600.0
    s = 100.0 * math.exp(-dt_h / max(tau_hours, 0.1))
    return FeatureScore("time", "time", round(s, 4), 1.0,
                        (start or end).isoformat(), found_at.isoformat(), "SEMANTIC_MATCH",
                        f"时间差约 {dt_h:.1f} 小时（tau={tau_hours}h）")


# ---------------------------------------------------------------------------
# Distinctive Feature
# ---------------------------------------------------------------------------

def distinctive_score(lost_feats: list[str], found_feats: list[str]) -> FeatureScore:
    """明确相同 100 / 高度相似 90 / 可能相同 70 / 未知 skip / 不一致 20 / 冲突 0。

    「猫咪贴纸」vs「Hello Kitty 贴纸」-> 90
    """
    rel = matching_weights()["distinctive_relation_scores"]
    lost_feats = [f for f in (lost_feats or []) if f]
    found_feats = [f for f in (found_feats or []) if f]
    if not lost_feats or not found_feats:
        return FeatureScore("distinctive", "distinctive", None,
                            lost_value=lost_feats or None, found_value=found_feats or None,
                            explanation="任一侧没有独特特征，不参与评分")

    children: list[FeatureScore] = []
    best_per_lost: list[float] = []
    for lf in lost_feats:
        nl = norm_text(lf)
        best, partner, relation = 0.0, None, "CONFLICT"
        for ff in found_feats:
            nf = norm_text(ff)
            if nl == nf:
                s, r = float(rel["EXACT"]), "EXACT_MATCH"
            elif nl in nf or nf in nl:
                s, r = float(rel["HIGHLY_SIMILAR"]), "SEMANTIC_MATCH"
            else:
                ratio = text_overlap(lf, ff)
                if ratio >= 0.75:
                    s, r = float(rel["HIGHLY_SIMILAR"]), "SEMANTIC_MATCH"
                elif ratio >= 0.4:
                    # 「猫咪贴纸」vs「猫咪图案」/「Hello Kitty 贴纸」
                    s, r = float(rel["POSSIBLY_SAME"]), "PARTIAL_MATCH"
                else:
                    # 同为「贴纸」类但内容不同 -> 不一致而非冲突
                    s, r = float(rel["INCONSISTENT"]), "MINOR_CONFLICT"
            if s > best:
                best, partner, relation = s, ff, r
        best_per_lost.append(best)
        children.append(FeatureScore("distinctive", "distinctive_feature", best, 1.0,
                                     lf, partner, relation))

    avg = sum(best_per_lost) / len(best_per_lost)
    return FeatureScore("distinctive", "distinctive", round(avg, 4), 1.0,
                        lost_feats, found_feats, "AGGREGATE", children=children,
                        explanation=f"{len(children)} 项独特特征比对")


# ---------------------------------------------------------------------------
# Semantic / Keyword / Image
# ---------------------------------------------------------------------------

def semantic_score(cosine_value: float | None) -> FeatureScore:
    if cosine_value is None:
        return FeatureScore("semantic", "semantic", None, explanation="无可用向量")
    return FeatureScore("semantic", "semantic", round(max(0.0, cosine_value) * 100, 4), 1.0,
                        relation="SEMANTIC_MATCH",
                        explanation=f"cosine={cosine_value:.4f}（语义永远不能单独决定匹配）")


def keyword_score(bm25_rank: float | None) -> FeatureScore:
    """PostgreSQL ts_rank -> 0~100。rank/(rank+1) 做有界归一化。"""
    if bm25_rank is None:
        return FeatureScore("keyword", "keyword", None, explanation="无关键词命中")
    s = 100.0 * (bm25_rank / (bm25_rank + 1.0))
    return FeatureScore("keyword", "keyword", round(s, 4), 1.0,
                        relation="PARTIAL_MATCH", explanation=f"ts_rank={bm25_rank:.4f}")


def image_score(cosine_value: float | None) -> FeatureScore:
    """图片缺失时**绝不能给 0**，而是不参与评分。"""
    if cosine_value is None:
        return FeatureScore("image", "image", None, explanation="任一侧无图片，不参与评分")
    return FeatureScore("image", "image", round(max(0.0, cosine_value) * 100, 4), 1.0,
                        relation="SEMANTIC_MATCH", explanation=f"image cosine={cosine_value:.4f}")
