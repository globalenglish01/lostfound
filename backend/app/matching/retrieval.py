"""Candidate Retrieval：三路召回 + RRF 融合。

    1,000,000 -> Structured Filter -> BM25(500) U Vector(500) -> RRF -> Top 1000

三路结果不要简单覆盖；同时被多路召回的物品应自然获得更高排名。
Structured Filter 必须先跑：绝不是「100 万条全量 Embedding 检索」。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import settings
from ..db import vector_literal


@dataclass
class Candidate:
    item_id: str
    ranks: dict[str, int] = field(default_factory=dict)
    semantic_cosine: float | None = None
    image_cosine: float | None = None
    bm25_rank: float | None = None
    rrf_score: float = 0.0
    sources: list[str] = field(default_factory=list)


def rrf_fuse(channels: dict[str, list[str]], k: int | None = None) -> list[Candidate]:
    """Reciprocal Rank Fusion：RRF(d) = sum_i 1 / (k + rank_i(d))，k 默认 60。"""
    k = k or settings.rrf_k
    table: dict[str, Candidate] = {}
    for channel, ids in channels.items():
        for rank, item_id in enumerate(ids, start=1):
            c = table.setdefault(item_id, Candidate(item_id=item_id))
            c.ranks[channel] = rank
            c.sources.append(channel)
            c.rrf_score += 1.0 / (k + rank)
    return sorted(table.values(), key=lambda c: c.rrf_score, reverse=True)


# ---------------------------------------------------------------------------
# 结构化过滤（作为其余两路的候选池）
# ---------------------------------------------------------------------------

_BASE_POOL_SQL = """
SELECT r.id::text AS id
FROM item_records r
WHERE r.record_type = :target_type
  AND r.status = 'ACTIVE'
  AND r.id::text <> :source_id
ORDER BY r.created_at DESC
LIMIT :limit
"""

_STRUCTURED_SQL = """
SELECT r.id::text AS id
FROM item_records r
WHERE r.record_type = :target_type
  AND r.status = 'ACTIVE'
  AND r.id::text <> :source_id
  AND r.category_id = CAST(:category_id AS bigint)
ORDER BY r.created_at DESC
LIMIT :limit
"""


def base_pool(session: Session, source_id: str, target_type: str,
              limit: int | None = None) -> list[str]:
    """只施加「安全的硬过滤」：记录类型 + 状态。

    category **不能**放进这里。类别是 AI 推断出来的，会错：
    "left a bottle of sake" 里 bottle 比 sake 长，会被判成 water_bottle，
    一旦拿它做门禁，那瓶清酒就永远不可能被召回——这是最危险的一类漏召。
    类别的作用体现在下面的 structured 通道和 category_score 维度上，而不是生杀大权。
    """
    rows = session.execute(text(_BASE_POOL_SQL), {
        "source_id": source_id,
        "target_type": target_type,
        "limit": limit or settings.structured_limit,
    }).fetchall()
    return [r[0] for r in rows]


def structured_retrieval(session: Session, source_id: str, target_type: str,
                         category_id: int | None, limit: int | None = None) -> list[str]:
    """高精度通道：类别命中的记录。作为 RRF 的一路，不是门禁。"""
    if category_id is None:
        return []
    rows = session.execute(text(_STRUCTURED_SQL), {
        "source_id": source_id,
        "target_type": target_type,
        "category_id": category_id,
        "limit": limit or settings.structured_limit,
    }).fetchall()
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# Keyword（PostgreSQL FTS + trigram 兜底；V2 可换 OpenSearch BM25）
#
# 注意：PostgreSQL 的 'simple' 分词器不切中日文，纯 CJK 查询在 FTS 下必然 0 命中。
# 这正是设计文档 §8 说「日文/中文复杂分词要上 ES/OpenSearch」的原因。
# V1 用 pg_trgm 相似度兜底，保证 keyword 通道在中日文下仍然有效。
# ---------------------------------------------------------------------------

_KEYWORD_SQL = """
SELECT r.id::text AS id, ts_rank_cd(r.search_vector, q.query) AS rank
FROM item_records r,
     plainto_tsquery('simple', :query_text) AS q(query)
WHERE r.id::text = ANY(:pool)
  AND r.search_vector @@ q.query
ORDER BY rank DESC
LIMIT :limit
"""


_TRIGRAM_SQL = """
SELECT r.id::text AS id,
       similarity(coalesce(r.normalized_text, r.raw_description), :query_text) AS rank
FROM item_records r
WHERE r.id::text = ANY(:pool)
  AND similarity(coalesce(r.normalized_text, r.raw_description), :query_text) > 0.05
ORDER BY rank DESC
LIMIT :limit
"""


def keyword_retrieval(session: Session, pool: list[str], query_text: str,
                      limit: int | None = None) -> list[tuple[str, float]]:
    if not pool or not query_text.strip():
        return []
    params = {
        "pool": pool,
        "query_text": query_text,
        "limit": limit or settings.keyword_limit,
    }
    rows = session.execute(text(_KEYWORD_SQL), params).fetchall()
    if not rows:
        # CJK 在 'simple' 分词下 FTS 无命中 -> trigram 兜底
        rows = session.execute(text(_TRIGRAM_SQL), params).fetchall()
    return [(r[0], float(r[1])) for r in rows]


# ---------------------------------------------------------------------------
# Vector（pgvector HNSW，cosine）
# ---------------------------------------------------------------------------

_VECTOR_SQL = """
SELECT e.item_id::text AS id,
       1 - (e.embedding <=> CAST(:qvec AS vector)) AS similarity
FROM embeddings e
WHERE e.item_id::text = ANY(:pool)
  AND e.embedding_type = :etype
  AND e.status = 'ACTIVE'
ORDER BY e.embedding <=> CAST(:qvec AS vector)
LIMIT :limit
"""


def vector_retrieval(session: Session, pool: list[str], query_vector: list[float],
                     embedding_type: str = "TEXT",
                     limit: int | None = None) -> list[tuple[str, float]]:
    if not pool or not query_vector:
        return []
    rows = session.execute(text(_VECTOR_SQL), {
        "pool": pool,
        "qvec": vector_literal(query_vector),
        "etype": embedding_type,
        "limit": limit or settings.vector_limit,
    }).fetchall()
    return [(r[0], float(r[1])) for r in rows]


# ---------------------------------------------------------------------------
# Hybrid
# ---------------------------------------------------------------------------

def hybrid_retrieve(session: Session, *, source_id: str, target_type: str,
                    category_id: int | None, query_text: str,
                    text_vector: list[float] | None,
                    attr_vector: list[float] | None = None,
                    image_vector: list[float] | None = None,
                    limit: int | None = None) -> list[Candidate]:
    """三路召回 -> RRF -> Top N。返回带各通道原始分的候选。

    pool 只按 record_type + status 收缩；三路（structured / keyword / vector）
    各自独立召回，再由 RRF 融合——同时被多路命中的自然排前。
    """
    pool = base_pool(session, source_id, target_type)
    if not pool:
        return []

    channels: dict[str, list[str]] = {}
    structured = structured_retrieval(session, source_id, target_type, category_id)
    if structured:
        channels["structured"] = structured[: settings.keyword_limit]

    kw = keyword_retrieval(session, pool, query_text)
    channels["keyword"] = [i for i, _ in kw]
    kw_map = dict(kw)

    vec_text = vector_retrieval(session, pool, text_vector or [], "TEXT")
    channels["vector_text"] = [i for i, _ in vec_text]
    sem_map = dict(vec_text)

    if attr_vector:
        # ATTRIBUTES 向量只参与**召回**，绝不并入 semantic 分数：
        #  a) 属性稀疏时会退化——记录的 canonical text 就是 "color: black"，
        #     和只抽到颜色的查询完全相同，余弦 = 1.0，语义直接满分；
        #  b) 属性的相似度已经由 attribute 维度（25%~32% 权重）计过一次，
        #     再算进 semantic 就是同一份证据计两遍。
        vec_attr = vector_retrieval(session, pool, attr_vector, "ATTRIBUTES")
        channels["vector_attr"] = [i for i, _ in vec_attr]

    img_map: dict[str, float] = {}
    if image_vector:
        vec_img = vector_retrieval(session, pool, image_vector, "IMAGE")
        channels["vector_image"] = [i for i, _ in vec_img]
        img_map = dict(vec_img)

    fused = rrf_fuse(channels)
    for c in fused:
        c.bm25_rank = kw_map.get(c.item_id)
        c.semantic_cosine = sem_map.get(c.item_id)
        c.image_cosine = img_map.get(c.item_id)
    return fused[: (limit or settings.fusion_limit)]
