"""题目等价性的特征维度与硬约束。

等价的定义（缺一不可）：

    两道题等价  ⟺  场景相同  ∧  约束相同  ∧  正确答案的做法相同

只看「像不像」会把 SAP 题库整片误判——同一个服务组合会反复出现几十次，
「公司在 AWS 上有基础设施，需要跨账户跨区域部署」这样的开头是模板化的。
真正区分两道题的是**约束（求什么最优）**和**正确选项在做什么**。

这正是失物系统那条铁律的同构：
    iPhone 15 Pro vs iPhone 15 Pro Max，语义 0.97，但不是同一台手机。
    同服务同场景的两道题，语义 0.96，但不是同一道题。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.matching.conflicts import Conflict, ConflictReport, aggregate_penalty  # noqa: E402
from app.matching.features import FeatureScore                                  # noqa: E402

from .loader import Question                                                    # noqa: E402

DIMENSIONS = ("stem", "answer", "services", "kps", "constraints", "keyword")

# 权重：题干与选项各占大头，约束是判别器。
#
# 注意 "answer" 这一维在两种数据下含义不同：
#   有完整选项时 -> 全部选项拼起来（不依赖答案，而答案有 46% 的标注分歧）
#   只有解析时   -> 正确选项的分析文本
# 两种都在回答同一个问题：「这道题在考哪种做法」。
WEIGHTS: dict[str, float] = {
    "stem": 0.26,
    "answer": 0.26,
    "services": 0.14,
    "kps": 0.14,
    "constraints": 0.15,
    "keyword": 0.05,
}

LEVEL_CONFIG = {
    "match_levels": [
        # 阈值是实测校准出来的，不是拍的：
        # 在 524 道真题上，唯一确定的真重复（#84/#85，题干与选项逐字相同）得 100 分，
        # 其余最高只有 91.6，而人工核验显示 82~92 这一段里近一半是「同主题不同考点」。
        # 所以「可合并」的门槛设在 95：宁可漏合，也不能把陷阱对合并掉。
        {"min": 95, "max": 100, "level": "DUPLICATE", "action": "AUTO_MERGE"},
        {"min": 88, "max": 95, "level": "LIKELY_DUPLICATE", "action": "HUMAN_REVIEW"},
        {"min": 78, "max": 88, "level": "SAME_TOPIC", "action": "KEEP_BOTH"},
        {"min": 60, "max": 78, "level": "RELATED", "action": "KEEP_BOTH"},
        {"min": 0, "max": 60, "level": "DISTINCT", "action": "KEEP_BOTH"},
    ],
    # 有 CRITICAL 冲突（题型不同 / 约束互斥）时，最高只能到 SAME_TOPIC，
    # 也就是「同一片知识，但不是同一道题」——绝不允许被合并掉。
    "critical_conflict_max_level": "SAME_TOPIC",
    "no_identity_evidence_max_level": "RELATED",
    "identity_dimensions": ["answer", "services", "kps"],
    "evidence_coverage_caps": [
        {"below": 0.35, "max_level": "RELATED"},
        {"below": 0.60, "max_level": "SAME_TOPIC"},
    ],
    "evidence_bonus_max": 0.0,      # 题库场景不需要额外奖励，证据本来就密
}


# ---------------------------------------------------------------------------
# 集合相似度
# ---------------------------------------------------------------------------

def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _set_feature(name: str, a: set[str], b: set[str],
                 explain: str) -> FeatureScore:
    if not a or not b:
        return FeatureScore(name, name, None, lost_value=sorted(a) or None,
                            found_value=sorted(b) or None,
                            explanation="任一侧为空，不参与评分")
    j = _jaccard(a, b)
    shared = sorted(a & b)
    return FeatureScore(name, name, round(j * 100, 4), 1.0,
                        sorted(a), sorted(b),
                        "EXACT_MATCH" if j == 1.0 else "PARTIAL_MATCH",
                        f"{explain}：共有 {len(shared)}/{len(a | b)}"
                        + (f" · {', '.join(shared[:6])}" if shared else ""))


# ---------------------------------------------------------------------------
# 特征
# ---------------------------------------------------------------------------

def build_features(a: Question, b: Question, *,
                   stem_cosine: float | None,
                   answer_cosine: float | None,
                   keyword_sim: float | None) -> dict[str, FeatureScore]:
    feats: dict[str, FeatureScore] = {}

    feats["stem"] = (
        FeatureScore("stem", "stem", round(max(0.0, stem_cosine) * 100, 4), 1.0,
                     a.stem[:60], b.stem[:60], "SEMANTIC_MATCH",
                     f"题干余弦 {stem_cosine:.4f}")
        if stem_cosine is not None else
        FeatureScore("stem", "stem", None, explanation="无题干向量"))

    # 正确选项在做什么——这是「同一道题」最强的证据
    feats["answer"] = (
        FeatureScore("answer", "answer", round(max(0.0, answer_cosine) * 100, 4), 1.0,
                     a.answer_text[:60], b.answer_text[:60], "SEMANTIC_MATCH",
                     f"正确选项余弦 {answer_cosine:.4f}")
        if answer_cosine is not None else
        FeatureScore("answer", "answer", None,
                     explanation="任一侧没有可用的正确选项文本，不参与评分"))

    feats["services"] = _set_feature("services", a.services, b.services, "涉及服务")
    feats["kps"] = _set_feature("kps", a.kps, b.kps, "知识点")
    feats["constraints"] = _set_feature("constraints", a.constraints, b.constraints,
                                        "优化目标")

    feats["keyword"] = (
        FeatureScore("keyword", "keyword", round(max(0.0, keyword_sim) * 100, 4), 1.0,
                     relation="PARTIAL_MATCH", explanation=f"字符 3-gram {keyword_sim:.4f}")
        if keyword_sim is not None else
        FeatureScore("keyword", "keyword", None, explanation="无关键词重叠"))
    return feats


# ---------------------------------------------------------------------------
# 硬约束
# ---------------------------------------------------------------------------

# 互斥的优化目标：同时出现在两题的**不同侧**时，说明考的不是一回事
_MUTUALLY_EXCLUSIVE = [
    # 作用范围：新建 vs 存量。同一个合规要求，这两者的解法完全不同
    # （EBS 默认加密 / SCP  vs  快照-复制-替换），是考试最经典的陷阱对。
    {"scope_new", "scope_existing"},
    {"cost", "performance"},
    {"cost", "availability"},
    {"downtime", "cost"},
    {"migration_effort", "performance"},
    {"speed_to_implement", "cost"},
]


def detect_conflicts(a: Question, b: Question) -> ConflictReport:
    report = ConflictReport()

    # 要求选几项：AWS 考试里「选一个」「选两个」「选三个」是结构不同的题。
    # 用题干里的「（选择两个）」判定，不用答案——该题库的答案标注不可靠。
    if a.select_n != b.select_n:
        report.conflicts.append(Conflict(
            field_name="select_n", severity="CRITICAL",
            lost_value=a.select_n, found_value=b.select_n, penalty=60.0,
            reason="要求选择的项数不同，不可能是同一道题"))

    # 优化目标互斥
    only_a, only_b = a.constraints - b.constraints, b.constraints - a.constraints
    for pair in _MUTUALLY_EXCLUSIVE:
        if (only_a & pair) and (only_b & pair) and not (only_a & only_b):
            report.conflicts.append(Conflict(
                field_name="constraints", severity="CRITICAL",
                lost_value=sorted(only_a & pair), found_value=sorted(only_b & pair),
                penalty=45.0,
                reason="优化目标互斥：场景可以一样，但求的东西不同就是两道题"))
            break

    # 服务集合完全不相交
    if a.services and b.services and not (a.services & b.services):
        report.conflicts.append(Conflict(
            field_name="services", severity="MAJOR",
            lost_value=sorted(a.services), found_value=sorted(b.services),
            penalty=35.0, reason="涉及的 AWS 服务完全不重叠"))

    report.penalty = aggregate_penalty([c.penalty for c in report.conflicts])
    return report
