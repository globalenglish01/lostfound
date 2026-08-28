"""数据访问层：物品建档、Embedding 落库、匹配结果持久化。"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from .ai.embedding_provider import content_hash, get_embedding_provider
from .ai.normalize import canonical_attribute_code, canonical_text_for_attributes
from .config import settings
from .db import vector_literal


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------

_ITEM_SQL = """
SELECT r.id::text, r.record_type, r.status, r.category_id, c.code AS category_code,
       r.brand, r.model, r.raw_description, r.normalized_text
FROM item_records r
LEFT JOIN item_categories c ON c.id = r.category_id
WHERE r.id = :item_id
"""

_ATTR_SQL = """
SELECT attribute_code, value_text, value_number, value_boolean, value_json,
       source, source_type, confidence, is_secret
FROM item_attributes WHERE item_id = :item_id
"""

_LOST_SQL = """
SELECT lost_at, lost_at_start, lost_at_end, lost_location_id
FROM lost_reports WHERE item_id = :item_id LIMIT 1
"""

_FOUND_SQL = """
SELECT found_at, found_location_id, storage_location, custody_status
FROM found_reports WHERE item_id = :item_id LIMIT 1
"""

_LOCATION_SQL = """
WITH RECURSIVE chain AS (
    SELECT id, name, parent_id, latitude, longitude, 0 AS depth
    FROM locations WHERE id = :loc_id
    UNION ALL
    SELECT l.id, l.name, l.parent_id, l.latitude, l.longitude, chain.depth + 1
    FROM locations l JOIN chain ON l.id = chain.parent_id
)
SELECT id, name, latitude, longitude, depth FROM chain ORDER BY depth
"""


def load_location(session: Session, loc_id: int | None) -> dict[str, Any] | None:
    if not loc_id:
        return None
    rows = session.execute(text(_LOCATION_SQL), {"loc_id": loc_id}).fetchall()
    if not rows:
        return None
    head = rows[0]
    return {
        "id": head[0],
        "name": head[1],
        "lat": float(head[2]) if head[2] is not None else None,
        "lon": float(head[3]) if head[3] is not None else None,
        # ancestors[0] 是最近的父节点
        "ancestors": [r[0] for r in rows[1:]],
    }


def load_item_bundle(session: Session, item_id: str) -> dict[str, Any] | None:
    """把一条记录展开成匹配引擎需要的完整结构。"""
    row = session.execute(text(_ITEM_SQL), {"item_id": item_id}).fetchone()
    if row is None:
        return None

    attrs = [
        {
            "attribute_code": canonical_attribute_code(a[0]),
            "value_text": a[1],
            "value_number": float(a[2]) if a[2] is not None else None,
            "value_boolean": a[3],
            "value_json": a[4],
            "source": a[5],
            "source_type": a[6],
            "confidence": float(a[7]) if a[7] is not None else None,
            "is_secret": a[8],
        }
        for a in session.execute(text(_ATTR_SQL), {"item_id": item_id}).fetchall()
    ]

    bundle: dict[str, Any] = {
        "id": row[0],
        "record_type": row[1],
        "status": row[2],
        "category_id": row[3],
        "category": row[4],
        "brand": row[5],
        "model": row[6],
        "raw_description": row[7],
        "normalized_text": row[8],
        "attributes": [a for a in attrs if a["attribute_code"] != "distinctive"],
        "distinctive": [a["value_text"] for a in attrs
                        if a["attribute_code"] == "distinctive" and a["value_text"]],
        "location": None,
        "lost_at_start": None,
        "lost_at_end": None,
        "found_at": None,
    }

    if row[1] == "LOST":
        ev = session.execute(text(_LOST_SQL), {"item_id": item_id}).fetchone()
        if ev:
            bundle["lost_at_start"] = ev[1] or ev[0]
            bundle["lost_at_end"] = ev[2] or ev[0]
            bundle["location"] = load_location(session, ev[3])
    else:
        ev = session.execute(text(_FOUND_SQL), {"item_id": item_id}).fetchone()
        if ev:
            bundle["found_at"] = ev[0]
            bundle["location"] = load_location(session, ev[1])
            bundle["storage_location"] = ev[2]
            bundle["custody_status"] = ev[3]
    return bundle


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

_EMBED_UPSERT = """
INSERT INTO embeddings (id, item_id, embedding_type, model_provider, model_name,
                        model_version, dimensions, content_text, content_hash,
                        embedding, status)
VALUES (:id, :item_id, :etype, :provider, :model, :version, :dim, :content,
        :hash, CAST(:vec AS vector), 'ACTIVE')
ON CONFLICT (item_id, embedding_type, model_name, model_version)
DO UPDATE SET content_text = EXCLUDED.content_text,
              content_hash = EXCLUDED.content_hash,
              embedding    = EXCLUDED.embedding,
              status       = 'ACTIVE',
              updated_at   = NOW()
"""

_EMBED_HASH_SQL = """
SELECT content_hash FROM embeddings
WHERE item_id = :item_id AND embedding_type = :etype
  AND model_name = :model AND model_version = :version
"""


def upsert_embedding(session: Session, item_id: str, embedding_type: str,
                     content: str) -> bool:
    """写入/更新一条 embedding。content_hash 未变则跳过，返回 False。"""
    provider = get_embedding_provider()
    digest = content_hash(content)
    existing = session.execute(text(_EMBED_HASH_SQL), {
        "item_id": item_id, "etype": embedding_type,
        "model": provider.model, "version": provider.version,
    }).fetchone()
    if existing and existing[0] == digest:
        return False

    vec = provider.embed(content)
    session.execute(text(_EMBED_UPSERT), {
        "id": str(uuid.uuid4()),
        "item_id": item_id,
        "etype": embedding_type,
        "provider": provider.name,
        "model": provider.model,
        "version": provider.version,
        "dim": provider.dim,
        "content": content,
        "hash": digest,
        "vec": vector_literal(vec),
    })
    return True


def build_embeddings(session: Session, bundle: dict[str, Any]) -> dict[str, bool]:
    """为一条记录生成 TEXT + ATTRIBUTES 两个向量。"""
    text_content = bundle.get("normalized_text") or bundle.get("raw_description") or ""
    attr_payload: dict[str, Any] = {
        "category": bundle.get("category"),
        "brand": bundle.get("brand"),
        "model": bundle.get("model"),
    }
    for a in bundle.get("attributes", []):
        if a.get("value_text"):
            attr_payload[a["attribute_code"]] = a["value_text"]
    if bundle.get("distinctive"):
        attr_payload["distinctive feature"] = bundle["distinctive"]

    return {
        "TEXT": upsert_embedding(session, bundle["id"], "TEXT", text_content),
        "ATTRIBUTES": upsert_embedding(session, bundle["id"], "ATTRIBUTES",
                                       canonical_text_for_attributes(attr_payload)),
    }


def query_vectors(session: Session, item_id: str) -> dict[str, list[float]]:
    rows = session.execute(text(
        "SELECT embedding_type, embedding::text FROM embeddings "
        "WHERE item_id = :item_id AND status = 'ACTIVE'"
    ), {"item_id": item_id}).fetchall()
    out: dict[str, list[float]] = {}
    for etype, raw in rows:
        out[etype] = [float(x) for x in raw.strip("[]").split(",") if x]
    return out


# ---------------------------------------------------------------------------
# 匹配结果持久化
# ---------------------------------------------------------------------------

_RUN_INSERT = """
INSERT INTO matching_runs (id, trigger_type, source_item_id, algorithm_version,
                           embedding_model, retrieval_config, ranking_config,
                           candidate_count, duration_ms)
VALUES (:id, :trigger, :source, :algo, :emodel, CAST(:rcfg AS jsonb),
        CAST(:kcfg AS jsonb), :count, :ms)
"""


def insert_matching_run(session: Session, *, run_id: str, trigger: str, source_item_id: str,
                        candidate_count: int, duration_ms: int,
                        retrieval_config: dict, ranking_config: dict) -> None:
    session.execute(text(_RUN_INSERT), {
        "id": run_id,
        "trigger": trigger,
        "source": source_item_id,
        "algo": settings.algorithm_version,
        "emodel": get_embedding_provider().model,
        "rcfg": json.dumps(retrieval_config, ensure_ascii=False),
        "kcfg": json.dumps(ranking_config, ensure_ascii=False),
        "count": candidate_count,
        "ms": duration_ms,
    })


_CAND_UPSERT = """
INSERT INTO match_candidates (
    id, lost_item_id, found_item_id, run_id, retrieval_score,
    category_score, attribute_score, location_score, time_score, distinctive_score,
    semantic_score, keyword_score, image_score, conflict_penalty, evidence_bonus,
    final_score, confidence, match_level, llm_decision, llm_confidence,
    recommended_action, status, algorithm_version)
VALUES (:id, :lost, :found, :run, :retrieval,
        :category, :attribute, :location, :time, :distinctive,
        :semantic, :keyword, :image, :penalty, :bonus,
        :final, :confidence, :level, :llm_decision, :llm_confidence,
        :action, 'PENDING', :algo)
ON CONFLICT (lost_item_id, found_item_id) DO UPDATE SET
    run_id = EXCLUDED.run_id,
    retrieval_score = EXCLUDED.retrieval_score,
    category_score = EXCLUDED.category_score,
    attribute_score = EXCLUDED.attribute_score,
    location_score = EXCLUDED.location_score,
    time_score = EXCLUDED.time_score,
    distinctive_score = EXCLUDED.distinctive_score,
    semantic_score = EXCLUDED.semantic_score,
    keyword_score = EXCLUDED.keyword_score,
    image_score = EXCLUDED.image_score,
    conflict_penalty = EXCLUDED.conflict_penalty,
    evidence_bonus = EXCLUDED.evidence_bonus,
    final_score = EXCLUDED.final_score,
    confidence = EXCLUDED.confidence,
    match_level = EXCLUDED.match_level,
    llm_decision = EXCLUDED.llm_decision,
    llm_confidence = EXCLUDED.llm_confidence,
    recommended_action = EXCLUDED.recommended_action,
    algorithm_version = EXCLUDED.algorithm_version,
    updated_at = NOW()
RETURNING id
"""

_EVID_INSERT = """
INSERT INTO match_evidences (id, candidate_id, evidence_type, field_name, lost_value,
                             found_value, relation, similarity_score, weight,
                             reliability, contribution, is_conflict, severity, explanation)
VALUES (:id, :candidate, :etype, :field, :lost, :found, :relation, :sim, :weight,
        :reliability, :contribution, :conflict, :severity, :explanation)
"""


def save_match(session: Session, *, lost_id: str, found_id: str, run_id: str | None,
               result: dict[str, Any]) -> str:
    """写 match_candidates + match_evidences（先清旧证据再写新的）。"""
    dims = result["dimension_scores"]
    row = session.execute(text(_CAND_UPSERT), {
        "id": str(uuid.uuid4()),
        "lost": lost_id,
        "found": found_id,
        "run": run_id,
        "retrieval": result.get("retrieval_score"),
        "category": dims.get("category"),
        "attribute": dims.get("attribute"),
        "location": dims.get("location"),
        "time": dims.get("time"),
        "distinctive": dims.get("distinctive"),
        "semantic": dims.get("semantic"),
        "keyword": dims.get("keyword"),
        "image": dims.get("image"),
        "penalty": result["conflict_penalty"],
        "bonus": result["evidence_bonus"],
        "final": result["final_score"],
        "confidence": result["confidence"],
        "level": result["match_level"],
        "llm_decision": (result.get("llm") or {}).get("decision"),
        "llm_confidence": (result.get("llm") or {}).get("confidence"),
        "action": result["recommended_action"],
        "algo": settings.algorithm_version,
    }).fetchone()
    candidate_id = row[0]

    session.execute(text("DELETE FROM match_evidences WHERE candidate_id = :cid"),
                    {"cid": candidate_id})
    for ev in result.get("evidences", []):
        session.execute(text(_EVID_INSERT), {
            "id": str(uuid.uuid4()),
            "candidate": candidate_id,
            "etype": ev["evidence_type"],
            "field": ev["field_name"],
            "lost": ev["lost_value"],
            "found": ev["found_value"],
            "relation": ev["relation"],
            "sim": ev["similarity_score"],
            "weight": ev["weight"],
            "reliability": ev["reliability"],
            "contribution": ev["contribution"],
            "conflict": ev["is_conflict"],
            "severity": ev["severity"],
            "explanation": ev["explanation"],
        })
    return str(candidate_id)


def record_audit(session: Session, *, actor_id: str | None, action: str,
                 entity_type: str, entity_id: str,
                 before: dict | None = None, after: dict | None = None) -> None:
    session.execute(text(
        "INSERT INTO audit_logs (id, actor_id, action, entity_type, entity_id, "
        "before_data, after_data) VALUES (:id, :actor, :action, :etype, :eid, "
        "CAST(:before AS jsonb), CAST(:after AS jsonb))"
    ), {
        "id": str(uuid.uuid4()),
        "actor": actor_id,
        "action": action,
        "etype": entity_type,
        "eid": entity_id,
        "before": json.dumps(before, ensure_ascii=False, default=str) if before else None,
        "after": json.dumps(after, ensure_ascii=False, default=str) if after else None,
    })


def utcnow() -> datetime:
    from datetime import timezone
    return datetime.now(timezone.utc)
