"""考纲覆盖分析 + 最优选题。

回答两个问题：

  1. **盲区**：考纲上有、但题池里一道题都碰不到的考点有哪些？
     这是「刷完这 N 道就能高分」这类说法唯一真正的前提，
     而它经常不成立——覆盖优化只能在题池够得到的范围内做到最优。

  2. **最优选题**：在够得到的范围内，最少要多少道题才能把每个考点覆盖 k 次？
     这是一个最大覆盖 / 集合覆盖问题，贪心即可（近似比 1-1/e，实践中接近最优）。

注意「覆盖」和「会做」是两回事：覆盖保证「每个考点你都练过」，
但同一个考点换个问法你未必认得——那要靠「同考点不同问法」的配对清单来补。
"""
from __future__ import annotations

import collections
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Blueprint:
    """考纲：domain -> task -> knowledge items。"""

    kb_title: dict[str, str] = field(default_factory=dict)
    kb_task: dict[str, str] = field(default_factory=dict)
    kb_task_title: dict[str, str] = field(default_factory=dict)
    kb_domain: dict[str, str] = field(default_factory=dict)
    domain_title: dict[str, str] = field(default_factory=dict)

    @property
    def all_kb(self) -> list[str]:
        return list(self.kb_title)


def load_blueprint(path: str | Path) -> Blueprint:
    """载入 AWS 考纲 JSON（exam_guide_*.json）。"""
    g = json.loads(Path(path).read_text(encoding="utf-8"))
    bp = Blueprint()
    for dom in g.get("domains", []):
        did = dom.get("id", "")
        bp.domain_title[did] = dom.get("title", "")
        for t in dom.get("tasks", []):
            tid = t.get("id", "")
            for i, k in enumerate(t.get("knowledge") or [], 1):
                kid = f"{tid}.K{i}"
                bp.kb_title[kid] = k
                bp.kb_task[kid] = tid
                bp.kb_task_title[kid] = t.get("title", "")
                bp.kb_domain[kid] = did
    return bp


@dataclass
class CoverageReport:
    blueprint_total: int
    pool_size: int
    reachable: list[str]          # 题池能覆盖的考点
    blind: list[str]              # 题池完全够不到的考点
    per_kb_count: dict[str, int]  # 每个考点被多少道题覆盖
    by_domain: dict[str, tuple[int, int]]   # domain -> (盲区数, 总数)

    @property
    def blind_ratio(self) -> float:
        return len(self.blind) / self.blueprint_total if self.blueprint_total else 0.0


def analyze(bp: Blueprint, question_kbs: dict[str, set[str]]) -> CoverageReport:
    """question_kbs: 题号 -> 该题覆盖的考点集合。"""
    per_kb: collections.Counter = collections.Counter()
    for kbs in question_kbs.values():
        for kb in kbs:
            per_kb[kb] += 1

    reachable = [k for k in bp.all_kb if per_kb.get(k)]
    blind = [k for k in bp.all_kb if not per_kb.get(k)]

    by_domain: dict[str, tuple[int, int]] = {}
    tot = collections.Counter(bp.kb_domain[k] for k in bp.all_kb)
    miss = collections.Counter(bp.kb_domain[k] for k in blind)
    for d in sorted(tot):
        by_domain[d] = (miss.get(d, 0), tot[d])

    return CoverageReport(
        blueprint_total=len(bp.all_kb), pool_size=len(question_kbs),
        reachable=reachable, blind=blind, per_kb_count=dict(per_kb),
        by_domain=by_domain)


def greedy_select(question_kbs: dict[str, set[str]], targets: list[str],
                  k: int = 1, *, must_include: set[str] | None = None,
                  tie_break=None) -> list[str]:
    """贪心最大覆盖：选最少的题，让每个 target 被覆盖至少 k 次。

    must_include 里的题**无条件先放进来**——用于强制保留
    「同一考点的不同问法」那些配对：它们在覆盖意义上是冗余的，
    但正是训练「换个说法还认不认得」的材料，不能被优化掉。
    """
    need: collections.Counter = collections.Counter({t: k for t in targets})
    chosen: list[str] = []
    remaining = dict(question_kbs)

    for qid in sorted(must_include or ()):
        if qid in remaining:
            chosen.append(qid)
            for kb in remaining.pop(qid):
                if need[kb] > 0:
                    need[kb] -= 1

    while any(v > 0 for v in need.values()):
        best_id, best_gain = None, 0
        for qid, kbs in remaining.items():
            gain = sum(1 for kb in kbs if need[kb] > 0)
            if gain > best_gain or (gain == best_gain and gain > 0 and tie_break
                                    and best_id and tie_break(qid) > tie_break(best_id)):
                best_id, best_gain = qid, gain
        if not best_id or best_gain == 0:
            break                      # 剩下的需求题池满足不了
        chosen.append(best_id)
        for kb in remaining.pop(best_id):
            if need[kb] > 0:
                need[kb] -= 1
    return chosen


def coverage_of(question_kbs: dict[str, set[str]], chosen: list[str]) -> collections.Counter:
    c: collections.Counter = collections.Counter()
    for qid in chosen:
        for kb in question_kbs.get(qid, ()):
            c[kb] += 1
    return c
