"""Search API：用户输入 -> Query Understanding -> Hybrid Retrieval -> Matching -> Top K。

注意这里搜索的是「一段自然语言」，不是一条已建档记录，因此走临时向量，
不写库、不产生 match_candidates。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..ai.embedding_provider import get_embedding_provider
from ..ai.extraction import query_understanding
from ..ai.normalize import canonical_text_for_attributes, strip_report_boilerplate
from ..config import settings
from ..db import get_session
from ..matching.engine import match_pair
from ..matching.retrieval import hybrid_retrieve
from .. import repository as repo
from ..schemas import SearchIn

router = APIRouter(tags=["search"])

_HIDDEN_LEVELS = {"IGNORE", "REJECT"}


@router.post("/api/search")
def search(payload: SearchIn, session: Session = Depends(get_session)):
    parsed = query_understanding(payload.query)

    provider = get_embedding_provider()
    # 查询侧与记录侧必须用同一套预处理，否则向量不在同一个分布上
    text_vec = provider.embed(strip_report_boilerplate(payload.query), kind="query")
    attr_text = canonical_text_for_attributes({
        "category": parsed.get("category"),
        "brand": parsed.get("brand"),
        "model": parsed.get("model"),
        **{a["attribute_code"]: a["value_text"] for a in parsed["attributes"]},
    })
    attr_vec = (provider.embed(attr_text, kind="query")
                if len(attr_text.splitlines()) >= 2 else None)

    category_id = None
    if parsed.get("category"):
        row = session.execute(text("SELECT id FROM item_categories WHERE code = :c"),
                              {"c": parsed["category"]}).fetchone()
        category_id = row[0] if row else None

    # 查询没有自身 item_id，用全零 UUID 占位以复用同一套 SQL
    candidates = hybrid_retrieve(
        session,
        source_id="00000000-0000-0000-0000-000000000000",
        target_type=payload.type,
        category_id=category_id,
        query_text=parsed["normalized_text"],
        text_vector=text_vec,
        attr_vector=attr_vec,
    )

    # 查询里抽到的地点也要参与打分（占 15% 权重），不能白抽
    query_location = None
    if parsed.get("location_name"):
        loc_row = session.execute(text(
            "SELECT id FROM locations "
            "WHERE normalized_name = lower(:n) OR aliases @> CAST(:alias AS jsonb) LIMIT 1"
        ), {"n": parsed["location_name"],
            "alias": f'["{parsed["location_name"]}"]'}).fetchone()
        if loc_row:
            query_location = repo.load_location(session, loc_row[0])

    query_bundle = {
        "id": None,
        "category": parsed.get("category"),
        "category_candidates": parsed.get("category_candidates") or [],
        "category_uncertain": parsed.get("category_uncertain", False),
        "brand": parsed.get("brand"),
        "model": parsed.get("model"),
        "attributes": [a for a in parsed["attributes"]
                       if a["attribute_code"] != "distinctive"],
        "distinctive": [a["value_text"] for a in parsed["attributes"]
                        if a["attribute_code"] == "distinctive"],
        "location": query_location,
        "raw_description": payload.query,
    }

    results = []
    for cand in candidates[: settings.scoring_limit]:
        other = repo.load_item_bundle(session, cand.item_id)
        if other is None:
            continue
        lost, found = ((query_bundle, other) if payload.type == "FOUND"
                       else (other, query_bundle))
        result = match_pair(lost, found,
                            semantic_cosine=cand.semantic_cosine,
                            bm25_rank=cand.bm25_rank,
                            retrieval_score=round(cand.rrf_score, 6),
                            with_llm=False)
        result["record_id"] = cand.item_id
        result["raw_description"] = other.get("raw_description")
        result["retrieval_channels"] = cand.sources
        results.append(result)

    results.sort(key=lambda r: r["final_score"], reverse=True)
    if not payload.include_low:
        results = [r for r in results if r["match_level"] not in _HIDDEN_LEVELS]

    return {
        "query_understanding": parsed,
        "total_candidates": len(candidates),
        "results": [
            {
                "record_id": r["record_id"],
                "raw_description": r["raw_description"],
                "match_score": r["final_score"],
                "match_level": r["match_level"],
                "confidence": r["confidence"],
                "recommended_action": r["recommended_action"],
                "dimension_scores": r["dimension_scores"],
                "conflicts": r["conflicts"],
                # UI 不要只显示 94%：把系统真实算出的证据一并返回
                "matched_evidence": [e for e in r["evidences"] if not e["is_conflict"]],
                "retrieval_channels": r["retrieval_channels"],
            }
            for r in results[: payload.top_k]
        ],
    }
