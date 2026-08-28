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

_STRUCTURED_SQL = """
SELECT r.id::text AS id
FROM item_records r
WHERE r.record_type = :target_type
  AND r.status = 'ACTIVE'
  AND r.id::text <> :source_id
  AND (CAST(:category_id AS bigint) IS NULL
       OR r.category_id IS NULL
       OR r.category_id = CAST(:category_id AS bigint))
ORDER BY r.created_at DESC
LIMIT :limit
"""


def structured_retrieval(session: Session, source_id: str, target_type: str,
                         category_id: int | None, limit: int | None = None) -> list[str]:
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
    """三路召回 -> RRF -> Top N。返回带各通道原始分的候选。"""
    pool = structured_retrieval(session, source_id, target_type, category_id)
    if not pool:
        return []

    channels: dict[str, list[str]] = {"structured": pool[: settings.keyword_limit]}

    kw = keyword_retrieval(session, pool, query_text)
    channels["keyword"] = [i for i, _ in kw]
    kw_map = dict(kw)

    vec_text = vector_retrieval(session, pool, text_vector or [], "TEXT")
    channels["vector_text"] = [i for i, _ in vec_text]
    sem_map = dict(vec_text)

    if attr_vector:
        vec_attr = vector_retrieval(session, pool, attr_vector, "ATTRIBUTES")
        channels["vector_attr"] = [i for i, _ in vec_attr]
        for item_id, sim in vec_attr:
            # 属性向量与文本向量取较高者作为该候选的语义相似度
            sem_map[item_id] = max(sem_map.get(item_id, 0.0), sim)

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
