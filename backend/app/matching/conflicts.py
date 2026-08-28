"""Hard Constraint / Conflict Detection。

设计铁律：
- 明确身份冲突（IMEI / 序列号）-> 直接 REJECT，语义 0.97 也不行
- 型号冲突（iPhone 15 Pro vs iPhone 15 Pro Max）-> CRITICAL 惩罚
- 颜色 black vs dark gray **绝不能** REJECT（同族）
- Hard Constraint 不要太多；属性分级 Hard / Soft / Semantic
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..ai.normalize import canonical_attribute_code, color_family, model_tokens, norm_text
from ..config import conflict_rules


@dataclass
class Conflict:
    field_name: str
    severity: str            # CRITICAL / MAJOR / MINOR
    lost_value: Any
    found_value: Any
    penalty: float
    reason: str
    reject: bool = False


@dataclass
class ConflictReport:
    conflicts: list[Conflict] = field(default_factory=list)
    rejected: bool = False
    penalty: float = 0.0

    @property
    def has_critical(self) -> bool:
        return any(c.severity == "CRITICAL" for c in self.conflicts)

    def as_dicts(self) -> list[dict[str, Any]]:
        return [
            {
                "field_name": c.field_name,
                "severity": c.severity,
                "lost_value": c.lost_value,
                "found_value": c.found_value,
                "penalty": c.penalty,
                "reason": c.reason,
                "reject": c.reject,
            }
            for c in self.conflicts
        ]


# ---------------------------------------------------------------------------
# 比较器
# ---------------------------------------------------------------------------

def _cmp_exact(a: str, b: str) -> bool:
    """True = 冲突。"""
    return norm_text(a) != norm_text(b)


def _cmp_normalized(a: str, b: str) -> bool:
    na, nb = norm_text(a), norm_text(b)
    if na == nb:
        return False
    # 互为包含视为不冲突（Apple vs Apple Inc.）
    return not (na in nb or nb in na)


def _cmp_color(a: str, b: str) -> bool:
    """同族颜色不冲突：black / dark gray / 深色。"""
    fa, fb = color_family(a), color_family(b)
    if fa is None or fb is None:
        return False
    return fa != fb


def _cmp_model(a: str, b: str) -> bool:
    """型号比较。

    完全一致 -> 不冲突。其余一律冲突。

    特别地，「一个是另一个的前缀且剩余部分是档位词」（iPhone 15 Pro vs iPhone 15 Pro Max）
    正是本系统最想抓住的那类冲突——语义相似度会非常高，但它们是两台不同的手机。
    """
    ta, tb = model_tokens(a), model_tokens(b)
    if not ta or not tb:
        return False              # UNKNOWN != CONFLICT
    return ta != tb


_COMPARATORS = {
    "exact": _cmp_exact,
    "normalized": _cmp_normalized,
    "color": _cmp_color,
    "model": _cmp_model,
}


# ---------------------------------------------------------------------------
# 检测
# ---------------------------------------------------------------------------

def _values(attrs: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for a in attrs or []:
        code = canonical_attribute_code(a.get("attribute_code"))
        if code and a.get("value_text"):
            out.setdefault(code, a)
    return out


def detect_conflicts(lost_attrs: list[dict], found_attrs: list[dict],
                     lost_core: dict | None = None,
                     found_core: dict | None = None) -> ConflictReport:
    """检测冲突并聚合惩罚。

    lost_core / found_core 提供 category / brand / model 这类主表字段，
    与 item_attributes 合并后统一比对。
    """
    cfg = conflict_rules()
    lost_map = _values(lost_attrs)
    found_map = _values(found_attrs)
    for key in ("category", "brand", "model"):
        if (lost_core or {}).get(key) and key not in lost_map:
            lost_map[key] = {"attribute_code": key, "value_text": lost_core[key],
                             "source": lost_core.get("source", "USER")}
        if (found_core or {}).get(key) and key not in found_map:
            found_map[key] = {"attribute_code": key, "value_text": found_core[key],
                              "source": found_core.get("source", "STAFF")}

    report = ConflictReport()
    for rule in cfg["rules"]:
        code = rule["attribute"]
        la, fa = lost_map.get(code), found_map.get(code)
        if not la or not fa:
            continue                       # UNKNOWN != CONFLICT
        lv, fv = la["value_text"], fa["value_text"]
        cmp_fn = _COMPARATORS[rule.get("comparator", "normalized")]
        if not cmp_fn(lv, fv):
            continue
        severity = rule["severity"]
        penalty = float(rule.get("penalty", cfg["severity_penalty"][severity]))
        conflict = Conflict(
            field_name=code,
            severity=severity,
            lost_value=lv,
            found_value=fv,
            penalty=penalty,
            reason=rule.get("reason", ""),
            reject=bool(rule.get("reject", False)),
        )
        report.conflicts.append(conflict)
        if conflict.reject:
            report.rejected = True

    report.penalty = aggregate_penalty([c.penalty for c in report.conflicts])
    return report


def aggregate_penalty(penalties: list[float]) -> float:
    """最严重一条全额 + 其余的一半。

    避免多条 MINOR 线性叠加把一个本来正确的匹配打穿。
    """
    if not penalties:
        return 0.0
    ordered = sorted(penalties, reverse=True)
    return round(ordered[0] + sum(ordered[1:]) / 2.0, 4)
