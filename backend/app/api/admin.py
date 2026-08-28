"""管理端：权重热更新、指标、模型迁移辅助。

最重要的指标不是 Accuracy —— 失物匹配最怕 False Positive。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..ai.embedding_provider import get_embedding_provider
from ..ai.llm_provider import get_llm_provider
from ..config import (
    attribute_weights,
    conflict_rules,
    matching_weights,
    reload_configs,
    settings,
)
from ..db import get_session

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/config")
def get_config():
    return {
        "algorithm_version": settings.algorithm_version,
        "llm_provider": get_llm_provider().name,
        "llm_model": get_llm_provider().model,
        "embedding_provider": get_embedding_provider().name,
        "embedding_model": get_embedding_provider().model,
        "embedding_dim": get_embedding_provider().dim,
        "image_provider": settings.image_provider,
        "image_model": settings.image_model if settings.image_provider != "disabled" else None,
        "matching_weights": matching_weights(),
        "attribute_weights": attribute_weights(),
        "conflict_rules": conflict_rules(),
        "retrieval": {
            "structured_limit": settings.structured_limit,
            "keyword_limit": settings.keyword_limit,
            "vector_limit": settings.vector_limit,
            "rrf_k": settings.rrf_k,
            "scoring_limit": settings.scoring_limit,
            "rerank_limit": settings.rerank_limit,
        },
    }


@router.post("/config/reload")
def reload_config():
    """改完 config/*.json 后热加载，无需重启。"""
    reload_configs()
    return {"reloaded": True, "algorithm_version": settings.algorithm_version}


@router.get("/metrics")
def metrics(session: Session = Depends(get_session)):
    """业务指标：AI Assist Recall / Wrong Recommendation Rate。

    - Wrong Recommendation Rate = 被推荐（HIGH 及以上）却被人工 REJECTED 的比例
      这是失物系统最该盯的指标，比 Accuracy 重要得多。
    """
    row = session.execute(text("""
        SELECT
          COUNT(*) FILTER (WHERE d.decision = 'CONFIRMED')                       AS confirmed,
          COUNT(*) FILTER (WHERE d.decision = 'REJECTED')                        AS rejected,
          COUNT(*) FILTER (WHERE d.decision = 'CONFIRMED'
                             AND c.match_level IN ('VERY_HIGH','HIGH'))          AS confirmed_high,
          COUNT(*) FILTER (WHERE d.decision = 'REJECTED'
                             AND c.match_level IN ('VERY_HIGH','HIGH'))          AS rejected_high,
          COUNT(*)                                                               AS total
        FROM match_decisions d
        JOIN match_candidates c ON c.id = d.candidate_id
    """)).fetchone()

    confirmed, rejected, confirmed_high, rejected_high, total = [int(x or 0) for x in row]
    recommended = confirmed_high + rejected_high

    counts = session.execute(text(
        "SELECT match_level, COUNT(*) FROM match_candidates GROUP BY match_level"
    )).fetchall()

    return {
        "decisions": {"confirmed": confirmed, "rejected": rejected, "total": total},
        "ai_assist_recall": round(confirmed_high / confirmed, 4) if confirmed else None,
        "wrong_recommendation_rate": (round(rejected_high / recommended, 4)
                                      if recommended else None),
        "precision_at_high": round(confirmed_high / recommended, 4) if recommended else None,
        "candidates_by_level": {r[0]: int(r[1]) for r in counts},
        "_note": "Accuracy 不是本系统的核心指标；False Positive 才是。",
    }


@router.get("/training-pairs")
def training_pairs(limit: int = 500, session: Session = Depends(get_session)):
    """导出 Positive / Hard Negative 对，供未来训练 Learning-to-Rank。"""
    rows = session.execute(text("""
        SELECT d.decision, c.final_score, c.category_score, c.attribute_score,
               c.location_score, c.time_score, c.distinctive_score,
               c.semantic_score, c.keyword_score, c.image_score,
               c.conflict_penalty, c.evidence_bonus,
               c.lost_item_id::text, c.found_item_id::text
        FROM match_decisions d
        JOIN match_candidates c ON c.id = d.candidate_id
        WHERE d.decision IN ('CONFIRMED', 'REJECTED')
        ORDER BY d.decided_at DESC LIMIT :limit
    """), {"limit": limit}).fetchall()

    def f(v):
        return float(v) if v is not None else None

    return {
        "count": len(rows),
        "pairs": [
            {
                "label": 1 if r[0] == "CONFIRMED" else 0,
                "hard_negative": r[0] == "REJECTED" and float(r[1] or 0) >= 85,
                "features": {
                    "final_score": f(r[1]), "category": f(r[2]), "attribute": f(r[3]),
                    "location": f(r[4]), "time": f(r[5]), "distinctive": f(r[6]),
                    "semantic": f(r[7]), "keyword": f(r[8]), "image": f(r[9]),
                    "conflict_penalty": f(r[10]), "evidence_bonus": f(r[11]),
                },
                "lost_item_id": r[12], "found_item_id": r[13],
            }
            for r in rows
        ],
    }


@router.get("/embedding-status")
def embedding_status(session: Session = Depends(get_session)):
    """模型迁移视图：V1/V2 并存，验证完再切 ACTIVE，不要 UPDATE 覆盖。"""
    rows = session.execute(text("""
        SELECT model_name, model_version, embedding_type, status,
               dimensions, COUNT(*)
        FROM embeddings
        GROUP BY model_name, model_version, embedding_type, status, dimensions
        ORDER BY model_name, model_version
    """)).fetchall()
    return {
        "current_model": get_embedding_provider().model,
        "current_version": get_embedding_provider().version,
        "buckets": [
            {"model_name": r[0], "model_version": r[1], "embedding_type": r[2],
             "status": r[3], "dimensions": int(r[4]), "count": int(r[5])}
            for r in rows
        ],
    }
