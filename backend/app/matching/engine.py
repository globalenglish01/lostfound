"""Matching Engine —— 端到端编排。

    Hybrid Retrieval -> Hard Constraint -> Feature Matching
      -> Scoring -> Re-ranking(LLM/ML) -> Explanation -> Human Confirmation

`match_pair()` 是纯函数（不碰数据库），便于单测与回归；
`run_matching()` 负责数据库侧的完整流程与落库。
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy.orm import Session

from ..ai.llm_provider import get_llm_provider
from ..config import dimension_weights, settings
from .. import repository as repo
from . import features as F
from .conflicts import detect_conflicts
from .retrieval import Candidate, hybrid_retrieve
from .scoring import compute_score


# ---------------------------------------------------------------------------
# 单对匹配（纯函数）
# ---------------------------------------------------------------------------

def match_pair(lost: dict[str, Any], found: dict[str, Any],
               *, semantic_cosine: float | None = None,
               bm25_rank: float | None = None,
               image_cosine: float | None = None,
               retrieval_score: float | None = None,
               with_llm: bool = True) -> dict[str, Any]:
    """计算一对 Lost/Found 的完整匹配结果。"""
    category_code = lost.get("category") or found.get("category")
    weights = dimension_weights(category_code)

    taxonomy = {**(lost.get("taxonomy") or {}), **(found.get("taxonomy") or {})}

    feats: dict[str, F.FeatureScore] = {
        "category": F.category_score(lost, found, taxonomy),
        "attribute": F.attribute_score(lost.get("attributes") or [],
                                       found.get("attributes") or [],
                                       category_code),
        "location": F.location_score(lost.get("location"), found.get("location"),
                                     float(weights.get("location_tau_m", 500))),
        "time": F.time_score(lost.get("lost_at_start"), lost.get("lost_at_end"),
                             found.get("found_at"),
                             float(weights.get("time_tau_hours", 24))),
        "distinctive": F.distinctive_score(lost.get("distinctive") or [],
                                           found.get("distinctive") or []),
        "semantic": F.semantic_score(semantic_cosine),
        "keyword": F.keyword_score(bm25_rank),
        "image": F.image_score(image_cosine),
    }

    report = detect_conflicts(
        lost.get("attributes") or [], found.get("attributes") or [],
        {"category": lost.get("category"), "brand": lost.get("brand"),
         "model": lost.get("model"), "source": "USER"},
        {"category": found.get("category"), "brand": found.get("brand"),
         "model": found.get("model"), "source": "STAFF"},
    )

    result = compute_score(feats, report, category_code)

    payload: dict[str, Any] = {
        "lost_item_id": lost.get("id"),
        "found_item_id": found.get("id"),
        "retrieval_score": retrieval_score,
        "base_score": result.base_score,
        "conflict_penalty": result.conflict_penalty,
        "evidence_bonus": result.evidence_bonus,
        # 算法分与 LLM 判断分开存放，LLM 不得覆盖 algorithm_score
        "algorithm_score": result.final_score,
        "final_score": result.final_score,
        "confidence": result.confidence,
        "match_level": result.match_level,
        "recommended_action": result.recommended_action,
        "rejected": result.rejected,
        "dimension_scores": result.dimension_scores,
        "used_weights": result.used_weights,
        "evidences": result.evidences,
        "unknown_features": result.unknown_features,
        "conflicts": report.as_dicts(),
        "algorithm_version": settings.algorithm_version,
    }

    if with_llm:
        llm = get_llm_provider()
        analysis = llm.analyze_match({
            "lost": _llm_view(lost),
            "found": _llm_view(found),
            "algorithm_score": result.final_score,
            "semantic_score": result.dimension_scores.get("semantic"),
            "keyword_score": result.dimension_scores.get("keyword"),
            "image_score": result.dimension_scores.get("image"),
            "category_score": result.dimension_scores.get("category"),
            "attribute_score": result.dimension_scores.get("attribute"),
            "location_score": result.dimension_scores.get("location"),
            "time_score": result.dimension_scores.get("time"),
            "distinctive_score": result.dimension_scores.get("distinctive"),
            "conflicts": report.as_dicts(),
            "evidences": result.evidences,
            "unknown_features": result.unknown_features,
        })
        payload["llm"] = analysis
        payload["explanation"] = llm.explain({
            "decision": analysis.get("decision"),
            "score": result.final_score,
            "confidence": result.confidence,
            "supporting_evidence": analysis.get("supporting_evidence"),
            "conflicting_evidence": analysis.get("conflicting_evidence"),
            "unknown_evidence": analysis.get("unknown_evidence"),
            "recommended_action": analysis.get("recommended_action"),
        })
        # LLM 只能收紧建议动作，不能放宽（也永远不改分数）
        if analysis.get("recommended_action") == "DO_NOT_RECOMMEND":
            payload["recommended_action"] = "DO_NOT_RECOMMEND"

    return payload


def _llm_view(bundle: dict[str, Any]) -> dict[str, Any]:
    """喂给 LLM 的精简结构：不要把数据库一股脑扔过去，也不要泄露 secret 属性。"""
    return {
        "category": bundle.get("category"),
        "brand": bundle.get("brand"),
        "model": bundle.get("model"),
        "attributes": {a["attribute_code"]: a.get("value_text")
                       for a in bundle.get("attributes") or []
                       if a.get("value_text") and not a.get("is_secret")},
        "distinctive": bundle.get("distinctive") or [],
        "location": (bundle.get("location") or {}).get("name"),
        "time": {
            "lost_from": _iso(bundle.get("lost_at_start")),
            "lost_to": _iso(bundle.get("lost_at_end")),
            "found_at": _iso(bundle.get("found_at")),
        },
        "raw_description": bundle.get("raw_description"),
    }


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else value


# ---------------------------------------------------------------------------
# 数据库侧完整流程
# ---------------------------------------------------------------------------

def run_matching(session: Session, source_item_id: str, *,
                 trigger: str = "MANUAL",
                 top_k: int | None = None,
                 persist: bool = True) -> dict[str, Any]:
    """新增/更新一条记录后触发的双向匹配。

    LOST 进来就去搜 FOUND，FOUND 进来就反查历史 LOST。
    """
    started = time.perf_counter()
    source = repo.load_item_bundle(session, source_item_id)
    if source is None:
        raise ValueError(f"记录不存在: {source_item_id}")

    repo.build_embeddings(session, source)
    vectors = repo.query_vectors(session, source_item_id)

    target_type = "FOUND" if source["record_type"] == "LOST" else "LOST"
    query_text = source.get("normalized_text") or source.get("raw_description") or ""

    candidates: list[Candidate] = hybrid_retrieve(
        session,
        source_id=source_item_id,
        target_type=target_type,
        category_id=source.get("category_id"),
        query_text=query_text,
        text_vector=vectors.get("TEXT"),
        attr_vector=vectors.get("ATTRIBUTES"),
        image_vector=vectors.get("IMAGE"),
    )

    run_id = str(uuid.uuid4())
    scored: list[dict[str, Any]] = []

    # Hard Constraint + Feature Matching：只对漏斗收窄后的候选做精算
    for cand in candidates[: settings.scoring_limit]:
        other = repo.load_item_bundle(session, cand.item_id)
        if other is None:
            continue
        lost, found = ((source, other) if source["record_type"] == "LOST"
                       else (other, source))
        result = match_pair(
            lost, found,
            semantic_cosine=cand.semantic_cosine,
            bm25_rank=cand.bm25_rank,
            image_cosine=cand.image_cosine,
            retrieval_score=round(cand.rrf_score, 6),
            with_llm=False,           # 精排阶段才调 LLM
        )
        result["retrieval_channels"] = cand.sources
        scored.append(result)

    scored.sort(key=lambda r: r["final_score"], reverse=True)

    # Re-ranking：只对 Top N 调用 LLM 做证据分析与解释
    llm = get_llm_provider()
    for result in scored[: settings.rerank_limit]:
        lost = repo.load_item_bundle(session, result["lost_item_id"])
        found = repo.load_item_bundle(session, result["found_item_id"])
        analysis = llm.analyze_match({
            "lost": _llm_view(lost), "found": _llm_view(found),
            "algorithm_score": result["algorithm_score"],
            "semantic_score": result["dimension_scores"].get("semantic"),
            "keyword_score": result["dimension_scores"].get("keyword"),
            "image_score": result["dimension_scores"].get("image"),
            "category_score": result["dimension_scores"].get("category"),
            "attribute_score": result["dimension_scores"].get("attribute"),
            "location_score": result["dimension_scores"].get("location"),
            "time_score": result["dimension_scores"].get("time"),
            "distinctive_score": result["dimension_scores"].get("distinctive"),
            "conflicts": result["conflicts"],
            "evidences": result["evidences"],
            "unknown_features": result["unknown_features"],
        })
        result["llm"] = analysis
        result["explanation"] = llm.explain({
            "decision": analysis.get("decision"),
            "score": result["algorithm_score"],
            "confidence": result["confidence"],
            "supporting_evidence": analysis.get("supporting_evidence"),
            "conflicting_evidence": analysis.get("conflicting_evidence"),
            "unknown_evidence": analysis.get("unknown_evidence"),
            "recommended_action": analysis.get("recommended_action"),
        })
        if analysis.get("recommended_action") == "DO_NOT_RECOMMEND":
            result["recommended_action"] = "DO_NOT_RECOMMEND"

    duration_ms = int((time.perf_counter() - started) * 1000)

    if persist:
        repo.insert_matching_run(
            session, run_id=run_id, trigger=trigger, source_item_id=source_item_id,
            candidate_count=len(candidates), duration_ms=duration_ms,
            retrieval_config={
                "structured_limit": settings.structured_limit,
                "keyword_limit": settings.keyword_limit,
                "vector_limit": settings.vector_limit,
                "rrf_k": settings.rrf_k,
            },
            ranking_config={
                "scoring_limit": settings.scoring_limit,
                "rerank_limit": settings.rerank_limit,
                "weights": dimension_weights(source.get("category")),
            },
        )
        for result in scored:
            if result["match_level"] == "IGNORE" and not result["rejected"]:
                continue          # 低于阈值的不落库，避免候选表爆炸
            repo.save_match(session, lost_id=result["lost_item_id"],
                            found_id=result["found_item_id"],
                            run_id=run_id, result=result)

    limit = top_k or settings.rerank_limit
    return {
        "run_id": run_id,
        "source_item_id": source_item_id,
        "trigger": trigger,
        "candidate_count": len(candidates),
        "scored_count": len(scored),
        "duration_ms": duration_ms,
        "results": scored[:limit],
    }
