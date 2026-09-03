"""题库查重：分块召回 → 逐对打分 → 聚类 → 选代表。

560 道题两两比较是 15.6 万对，全量算向量余弦没必要。
和失物系统一样先收缩候选，再对候选做精算：

    N 题  ─ 向量 kNN ∪ 服务倒排 ∪ 字符 3-gram ─→  候选对
          ─ 硬约束 + 六维打分 ─→  等价对
          ─ 并查集 ─→  等价类（同一考点的不同问法）
"""
from __future__ import annotations

import collections
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.ai.embedding_provider import cosine, get_embedding_provider   # noqa: E402
from app.matching.scoring import compute_score                        # noqa: E402

from . import features as F                                           # noqa: E402
from .loader import Question                                          # noqa: E402

# 达到这个分数才算**可合并的重复**。
# 低于它但仍高于 SAME_TOPIC 下限的，是「同一考点的不同问法」——
# 这些**必须保留**，而且正是最有复习价值的部分。
DEFAULT_THRESHOLD = 95.0
# 「同考点不同问法」的下限：用于复习清单，不用于合并
TOPIC_THRESHOLD = 78.0


# ---------------------------------------------------------------------------
# 字符 3-gram（关键词通道，兼作分块）
# ---------------------------------------------------------------------------

def _grams(text: str, n: int = 3) -> collections.Counter:
    t = re.sub(r"\s+", "", text or "")
    return collections.Counter(t[i:i + n] for i in range(max(0, len(t) - n + 1)))


def _tfidf(docs: list[collections.Counter]) -> list[dict[str, float]]:
    df: collections.Counter = collections.Counter()
    for g in docs:
        df.update(g.keys())
    n = len(docs)
    out: list[dict[str, float]] = []
    for g in docs:
        v = {k: (1 + math.log(c)) * math.log(n / df[k])
             for k, c in g.items() if df[k] > 1}
        nrm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        out.append({k: x / nrm for k, x in v.items()})
    return out


# ---------------------------------------------------------------------------
# 结果结构
# ---------------------------------------------------------------------------

@dataclass
class Pair:
    a: str
    b: str
    score: float
    level: str
    dims: dict[str, float | None]
    conflicts: list[dict]
    penalty: float


@dataclass
class Cluster:
    representative: str
    members: list[str]
    size: int
    reason: str = ""
    pairs: list[Pair] = field(default_factory=list)


@dataclass
class DedupResult:
    questions: dict[str, Question]
    pairs: list[Pair]
    clusters: list[Cluster]
    kept: list[str]
    topic_pairs: list[Pair]
    embedding_model: str
    threshold: float

    @property
    def duplicate_clusters(self) -> list[Cluster]:
        return [c for c in self.clusters if c.size > 1]


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def _embed_all(texts: list[str], label: str, progress) -> list[list[float] | None]:
    provider = get_embedding_provider()
    out: list[list[float] | None] = []
    for i, t in enumerate(texts, 1):
        out.append(provider.embed(t, kind="passage") if t.strip() else None)
        if progress and i % 50 == 0:
            progress(f"  {label} 向量 {i}/{len(texts)}")
    return out


def _candidate_pairs(qs: list[Question], kw_vecs: list[dict[str, float]],
                     stem_vecs: list[list[float] | None],
                     top_k: int) -> set[tuple[int, int]]:
    """三路分块召回，取并集。任一路漏了，其余两路还能兜住。"""
    n = len(qs)
    cands: set[tuple[int, int]] = set()

    # 1) 服务倒排：至少共享一个 AWS 服务
    by_service: dict[str, list[int]] = collections.defaultdict(list)
    for i, q in enumerate(qs):
        for s in q.services:
            by_service[s].append(i)
    for ids in by_service.values():
        if len(ids) > 120:          # 太泛的服务（S3/EC2）不单独成块，交给另外两路
            continue
        for x in range(len(ids)):
            for y in range(x + 1, len(ids)):
                cands.add((ids[x], ids[y]))

    # 2) 字符 3-gram 倒排
    inv: dict[str, list[int]] = collections.defaultdict(list)
    for i, v in enumerate(kw_vecs):
        for k in v:
            inv[k].append(i)
    for i, v in enumerate(kw_vecs):
        acc: dict[int, float] = collections.defaultdict(float)
        for k, x in v.items():
            if len(inv[k]) > 60:
                continue
            for j in inv[k]:
                if j > i:
                    acc[j] += x * kw_vecs[j].get(k, 0.0)
        for j, sim in acc.items():
            if sim >= 0.25:
                cands.add((i, j))

    # 3) 题干向量 kNN
    for i in range(n):
        if stem_vecs[i] is None:
            continue
        sims = [(cosine(stem_vecs[i], stem_vecs[j]), j)
                for j in range(n) if j != i and stem_vecs[j] is not None]
        sims.sort(reverse=True)
        for _, j in sims[:top_k]:
            cands.add((min(i, j), max(i, j)))
    return cands


def dedup(questions: list[Question], *, threshold: float = DEFAULT_THRESHOLD,
          top_k: int = 8, progress=None) -> DedupResult:
    qs = questions
    n = len(qs)
    say = progress or (lambda *_: None)

    say(f"[1/4] 生成向量（{n} 题 × 2）...")
    stem_vecs = _embed_all([q.stem for q in qs], "题干", say)
    ans_vecs = _embed_all([q.answer_text for q in qs], "正确选项", say)

    say("[2/4] 分块召回候选对 ...")
    kw_vecs = _tfidf([_grams(q.stem + " " + q.answer_text) for q in qs])
    cands = _candidate_pairs(qs, kw_vecs, stem_vecs, top_k)
    say(f"  候选对 {len(cands)}（全量两两为 {n * (n - 1) // 2}）")

    say("[3/4] 逐对打分 ...")
    pairs: list[Pair] = []
    for done, (i, j) in enumerate(sorted(cands), 1):
        a, b = qs[i], qs[j]
        stem_cos = (cosine(stem_vecs[i], stem_vecs[j])
                    if stem_vecs[i] and stem_vecs[j] else None)
        ans_cos = (cosine(ans_vecs[i], ans_vecs[j])
                   if ans_vecs[i] and ans_vecs[j] else None)
        kw = sum(x * kw_vecs[j].get(k, 0.0) for k, x in kw_vecs[i].items()) or None

        feats = F.build_features(a, b, stem_cosine=stem_cos,
                                 answer_cosine=ans_cos, keyword_sim=kw)
        report = F.detect_conflicts(a, b)
        res = compute_score(feats, report, weights=F.WEIGHTS,
                            dimensions=F.DIMENSIONS, level_config=F.LEVEL_CONFIG)
        if res.final_score >= min(threshold, TOPIC_THRESHOLD):
            pairs.append(Pair(a.qid, b.qid, res.final_score, res.match_level,
                              res.dimension_scores, report.as_dicts(),
                              res.conflict_penalty))
        if done % 2000 == 0:
            say(f"  {done}/{len(cands)}")
    pairs.sort(key=lambda p: -p.score)

    say("[4/4] 聚类 ...")
    clusters = _cluster(qs, [p for p in pairs if p.score >= threshold])
    kept = [c.representative for c in clusters]
    # 同考点不同问法：不合并，单独成一份复习清单
    topic_pairs = [p for p in pairs
                   if TOPIC_THRESHOLD <= p.score < threshold and not p.conflicts]
    return DedupResult(questions={q.qid: q for q in qs}, pairs=pairs,
                       clusters=clusters, kept=kept, topic_pairs=topic_pairs,
                       embedding_model=get_embedding_provider().model,
                       threshold=threshold)


def _cluster(qs: list[Question], pairs: list[Pair]) -> list[Cluster]:
    """并查集把等价对合成等价类。"""
    parent = {q.qid: q.qid for q in qs}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for p in pairs:
        ra, rb = find(p.a), find(p.b)
        if ra != rb:
            parent[rb] = ra

    groups: dict[str, list[str]] = collections.defaultdict(list)
    for q in qs:
        groups[find(q.qid)].append(q.qid)

    by_id = {q.qid: q for q in qs}
    pair_by_key = collections.defaultdict(list)
    for p in pairs:
        pair_by_key[find(p.a)].append(p)

    out: list[Cluster] = []
    for root, members in groups.items():
        members.sort()
        # 代表题：证据最全的那道（选项齐、知识点多、题干长）
        rep = max(members, key=lambda m: (
            len(by_id[m].options), len(by_id[m].kps), len(by_id[m].stem)))
        reason = ""
        if len(members) > 1:
            ps = sorted(pair_by_key[root], key=lambda p: -p.score)
            reason = (f"最高相似 {ps[0].score:.1f}" if ps else "")
        out.append(Cluster(representative=rep, members=members,
                           size=len(members), reason=reason,
                           pairs=sorted(pair_by_key[root], key=lambda p: -p.score)))
    out.sort(key=lambda c: (-c.size, c.representative))
    return out
