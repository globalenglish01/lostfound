"""Lost & Found Matching Score v1.0 —— 最终评分公式。

    S_final = clip( sum(w_i * r_i * s_i) / sum(w_i * r_i)
                    - P_conflict + B_evidence,  0, 100 )

- 分母 sum(w_i * r_i) 只统计**可用证据**（Available Evidence Normalization）
- 每条证据带自己的可靠性 r_i，而不是 Score x Reliability 整体乘
- B_evidence 必须有上限，否则多个弱证据叠加会把错误匹配推到 100
- 即使 99 分，只要存在 CRITICAL 冲突，也不得进入 HIGH
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..config import dimension_weights, matching_weights, settings
from .conflicts import ConflictReport
from .features import FeatureScore

DIMENSIONS = ("category", "attribute", "location", "time",
              "distinctive", "semantic", "keyword", "image")


@dataclass
class ScoreResult:
    base_score: float
    conflict_penalty: float
    evidence_bonus: float
    final_score: float
    confidence: float
    match_level: str
    recommended_action: str
    rejected: bool
    dimension_scores: dict[str, float | None]
    used_weights: dict[str, float]
    evidences: list[dict[str, Any]] = field(default_factory=list)
    unknown_features: list[str] = field(default_factory=list)
    algorithm_version: str = settings.algorithm_version


def clip(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def evidence_bonus(features: dict[str, FeatureScore]) -> float:
    """额外证据奖励 B_evidence，封顶 B_max。

    奖励的是「多个**独立**强证据同时命中」，而不是弱证据的数量。
    「背面有一道明显裂痕」+「背面左上角有明显裂痕」才是强组合证据。
    """
    cfg = matching_weights()
    cap = float(cfg.get("evidence_bonus_max", 10.0))
    bonus = 0.0

    dist = features.get("distinctive")
    if dist and dist.score is not None:
        strong = [c for c in dist.children if (c.score or 0) >= 90]
        if len(strong) >= 1:
            bonus += 3.0 * min(len(strong), 2)          # 每条强独特证据 +3，最多两条

    attr = features.get("attribute")
    if attr:
        identity = [c for c in attr.children
                    if c.field_name in {"serial_number", "imei", "passport_number"}
                    and (c.score or 0) >= 100]
        if identity:
            bonus += 6.0                                # 强身份证据

    return round(min(bonus, cap), 4)


def confidence_of(features: dict[str, FeatureScore], report: ConflictReport) -> float:
    """Score != Confidence。

    Score 是「匹配程度有多高」，Confidence 是「系统对这个判断有多确定」，
    由**可用证据的覆盖度与可靠性**决定，而不是由分数决定。
    """
    weights = dimension_weights(None)
    covered = sum(weights.get(d, 0.0) for d in DIMENSIONS
                  if features.get(d) and features[d].score is not None)
    total = sum(weights.get(d, 0.0) for d in DIMENSIONS)
    coverage = covered / total if total else 0.0

    reliabilities = [f.reliability for f in features.values()
                     if f.score is not None and f.reliability]
    avg_r = sum(reliabilities) / len(reliabilities) if reliabilities else 0.7

    conf = 0.6 * coverage + 0.4 * avg_r
    if report.has_critical:
        conf = max(conf, 0.9)      # 明确冲突时，系统对「不是同一件」很确定
    return round(min(conf, 0.99), 4)


def resolve_level(score: float, report: ConflictReport, *,
                  coverage: float = 1.0,
                  has_identity_evidence: bool = True) -> tuple[str, str]:
    """分数 -> 匹配等级，并施加两条封顶规则。

    1. 存在 CRITICAL 冲突 -> 封顶（即使 99 分）
    2. 可用证据太弱 / 没有身份类证据 -> 封顶
       （Semantic、Keyword 是弱证据，永远不能单独决定匹配）
    """
    cfg = matching_weights()
    if report.rejected:
        return "REJECT", "DO_NOT_RECOMMEND"

    bands = cfg["match_levels"]
    order = [b["level"] for b in bands]                 # 高 -> 低
    level, action = "IGNORE", "DO_NOT_RECOMMEND"
    for band in bands:
        if band["min"] <= score <= band["max"]:
            level, action = band["level"], band["action"]
            break

    caps: list[str] = []
    if report.has_critical:
        caps.append(cfg.get("critical_conflict_max_level", "LOW"))
    if not has_identity_evidence:
        caps.append(cfg.get("no_identity_evidence_max_level", "LOW"))
    for rule in cfg.get("evidence_coverage_caps", []):
        if coverage < float(rule["below"]):
            caps.append(rule["max_level"])

    for cap in caps:
        if cap in order and order.index(level) < order.index(cap):
            level = cap
    if level != "IGNORE":
        action = next(b["action"] for b in bands if b["level"] == level)
    return level, action


def compute_score(features: dict[str, FeatureScore],
                  report: ConflictReport,
                  category_code: str | None = None) -> ScoreResult:
    weights = dimension_weights(category_code)

    num = den = 0.0
    dim_scores: dict[str, float | None] = {}
    used: dict[str, float] = {}
    unknown: list[str] = []

    for dim in DIMENSIONS:
        f = features.get(dim)
        w = float(weights.get(dim, 0.0))
        if f is None or f.score is None:
            dim_scores[dim] = None
            unknown.append(dim)
            continue
        r = float(f.reliability or 1.0)
        f.weight = w
        f.contribution = round(w * r * f.score, 4)
        num += w * r * f.score
        den += w * r
        dim_scores[dim] = round(f.score, 4)
        used[dim] = w

    base = round(num / den, 4) if den else 0.0
    bonus = evidence_bonus(features)
    penalty = report.penalty
    final = clip(base - penalty + bonus)

    if report.rejected:
        final = 0.0

    total_weight = sum(float(weights.get(d, 0.0)) for d in DIMENSIONS)
    coverage = (sum(used.values()) / total_weight) if total_weight else 0.0
    identity_dims = matching_weights().get("identity_dimensions",
                                           ["attribute", "distinctive", "category"])
    has_identity = any(dim_scores.get(d) is not None for d in identity_dims)

    level, action = resolve_level(final, report, coverage=coverage,
                                  has_identity_evidence=has_identity)
    conf = confidence_of(features, report)

    return ScoreResult(
        base_score=base,
        conflict_penalty=round(penalty, 4),
        evidence_bonus=bonus,
        final_score=round(final, 4),
        confidence=conf,
        match_level=level,
        recommended_action=action,
        rejected=report.rejected,
        dimension_scores=dim_scores,
        used_weights=used,
        evidences=flatten_evidences(features, report),
        unknown_features=unknown,
    )


def flatten_evidences(features: dict[str, FeatureScore],
                      report: ConflictReport) -> list[dict[str, Any]]:
    """展平成可直接写入 `match_evidences` 的行。Explainability 的唯一真实来源。"""
    rows: list[dict[str, Any]] = []

    def emit(f: FeatureScore, dim: str) -> None:
        if f.score is None:
            return
        rows.append({
            "evidence_type": dim.upper(),
            "field_name": f.field_name,
            "lost_value": _as_text(f.lost_value),
            "found_value": _as_text(f.found_value),
            "relation": f.relation,
            "similarity_score": round(f.score, 4),
            "weight": round(f.weight, 4),
            "reliability": round(f.reliability, 4),
            "contribution": round(f.contribution, 4),
            "is_conflict": f.is_conflict,
            "severity": f.severity,
            "explanation": f.explanation,
        })

    for dim, f in features.items():
        if f is None:
            continue
        if f.children:
            for child in f.children:
                emit(child, dim)
        else:
            emit(f, dim)

    for c in report.conflicts:
        rows.append({
            "evidence_type": "CONFLICT",
            "field_name": c.field_name,
            "lost_value": _as_text(c.lost_value),
            "found_value": _as_text(c.found_value),
            "relation": f"{c.severity}_CONFLICT",
            "similarity_score": 0.0,
            "weight": 0.0,
            "reliability": 1.0,
            "contribution": round(-c.penalty, 4),
            "is_conflict": True,
            "severity": c.severity,
            "explanation": c.reason,
        })
    return rows


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return str(value)
