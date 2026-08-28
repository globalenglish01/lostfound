"""匹配候选、人工确认、归还闭环。

AI 可以 Recommend，但不得 Authorize：
    AI 推荐 -> 工作人员确认 -> 用户身份验证 -> 正式归还
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..ai.llm_provider import get_llm_provider
from ..db import get_session
from ..matching.engine import run_matching
from .. import repository as repo
from ..schemas import MatchDecisionIn, ReturnIn

router = APIRouter(tags=["matches"])


@router.post("/api/items/{item_id}/rematch")
def rematch(item_id: str, top_k: int = 20, session: Session = Depends(get_session)):
    """手动重跑匹配（换了权重/模型之后很有用）。"""
    return run_matching(session, item_id, trigger="MANUAL", top_k=top_k)


@router.get("/api/items/{item_id}/matches")
def list_matches(item_id: str, limit: int = 20, min_score: float = 50.0,
                 session: Session = Depends(get_session)):
    """已落库的候选，不重算——这正是 match_candidates 存在的意义。"""
    rows = session.execute(text("""
        SELECT c.id::text, c.lost_item_id::text, c.found_item_id::text,
               c.final_score, c.confidence, c.match_level, c.recommended_action,
               c.conflict_penalty, c.evidence_bonus, c.status,
               c.llm_decision, c.llm_confidence, c.created_at,
               r.raw_description
        FROM match_candidates c
        JOIN item_records r
          ON r.id = CASE WHEN c.lost_item_id = :item THEN c.found_item_id
                         ELSE c.lost_item_id END
        WHERE (c.lost_item_id = :item OR c.found_item_id = :item)
          AND c.final_score >= :min_score
        ORDER BY c.final_score DESC
        LIMIT :limit
    """), {"item": item_id, "min_score": min_score, "limit": limit}).fetchall()

    out = []
    for r in rows:
        evid = session.execute(text("""
            SELECT evidence_type, field_name, lost_value, found_value, relation,
                   similarity_score, contribution, is_conflict, severity, explanation
            FROM match_evidences WHERE candidate_id = :cid
            ORDER BY is_conflict, contribution DESC
        """), {"cid": r[0]}).fetchall()
        out.append({
            "candidate_id": r[0],
            "lost_item_id": r[1],
            "found_item_id": r[2],
            "counterpart_description": r[13],
            "final_score": float(r[3]) if r[3] is not None else None,
            "confidence": float(r[4]) if r[4] is not None else None,
            "match_level": r[5],
            "recommended_action": r[6],
            "conflict_penalty": float(r[7]),
            "evidence_bonus": float(r[8]),
            "status": r[9],
            "llm_decision": r[10],
            "llm_confidence": float(r[11]) if r[11] is not None else None,
            "created_at": r[12],
            "evidences": [
                {
                    "evidence_type": e[0], "field_name": e[1],
                    "lost_value": e[2], "found_value": e[3], "relation": e[4],
                    "similarity_score": float(e[5]) if e[5] is not None else None,
                    "contribution": float(e[6]) if e[6] is not None else None,
                    "is_conflict": e[7], "severity": e[8], "explanation": e[9],
                }
                for e in evid
            ],
        })
    return {"item_id": item_id, "matches": out}


@router.get("/api/matches/{candidate_id}/explanation")
def explanation(candidate_id: str, session: Session = Depends(get_session)):
    """把已算好的证据转成人话。LLM 不得重算分数、不得改变决策。"""
    cand = session.execute(text(
        "SELECT final_score, confidence, llm_decision, match_level, recommended_action "
        "FROM match_candidates WHERE id = :cid"), {"cid": candidate_id}).fetchone()
    if cand is None:
        raise HTTPException(404, "候选不存在")

    rows = session.execute(text(
        "SELECT field_name, lost_value, found_value, similarity_score, is_conflict, "
        "severity, explanation FROM match_evidences WHERE candidate_id = :cid"),
        {"cid": candidate_id}).fetchall()

    supporting = [
        {"feature": r[0], "lost_value": r[1], "found_value": r[2],
         "strength": "STRONG" if float(r[3] or 0) >= 95 else
                     "MODERATE" if float(r[3] or 0) >= 75 else "WEAK",
         "reason": r[6]}
        for r in rows if not r[4]
    ]
    conflicting = [
        {"feature": r[0], "lost_value": r[1], "found_value": r[2],
         "severity": r[5], "reason": r[6]}
        for r in rows if r[4]
    ]

    return get_llm_provider().explain({
        "decision": cand[2] or cand[3],
        "score": float(cand[0]) if cand[0] is not None else None,
        "confidence": float(cand[1]) if cand[1] is not None else None,
        "supporting_evidence": supporting,
        "conflicting_evidence": conflicting,
        "unknown_evidence": [],
        "recommended_action": cand[4],
    })


@router.post("/api/matches/{candidate_id}/decision")
def decide(candidate_id: str, payload: MatchDecisionIn,
           session: Session = Depends(get_session)):
    """人工确认。这条数据同时是 AI Feedback Loop 的训练样本：
    CONFIRMED = Positive Pair，高分被 REJECTED = Hard Negative。
    """
    if payload.decision not in {"CONFIRMED", "REJECTED", "DEFERRED"}:
        raise HTTPException(400, "decision 必须是 CONFIRMED / REJECTED / DEFERRED")

    cand = session.execute(text(
        "SELECT final_score, lost_item_id::text, found_item_id::text "
        "FROM match_candidates WHERE id = :cid"), {"cid": candidate_id}).fetchone()
    if cand is None:
        raise HTTPException(404, "候选不存在")

    session.execute(text(
        "INSERT INTO match_decisions (id, candidate_id, decision, decided_by, "
        "decided_by_role, reason, score_at_decision) "
        "VALUES (:id, :cid, :d, :by, :role, :reason, :score)"
    ), {
        "id": str(uuid.uuid4()), "cid": candidate_id, "d": payload.decision,
        "by": payload.decided_by, "role": payload.decided_by_role,
        "reason": payload.reason, "score": cand[0],
    })
    session.execute(text(
        "UPDATE match_candidates SET status = :s, updated_at = NOW() WHERE id = :cid"
    ), {"s": payload.decision, "cid": candidate_id})

    if payload.decision == "CONFIRMED":
        session.execute(text(
            "UPDATE item_records SET status = 'MATCHED' WHERE id IN (:a, :b)"
        ), {"a": cand[1], "b": cand[2]})

    repo.record_audit(session, actor_id=payload.decided_by, action="MATCH_DECIDED",
                      entity_type="match_candidates", entity_id=candidate_id,
                      after={"decision": payload.decision, "score": float(cand[0] or 0)})
    return {"candidate_id": candidate_id, "decision": payload.decision}


@router.post("/api/items/{item_id}/return")
def do_return(item_id: str, payload: ReturnIn, session: Session = Depends(get_session)):
    """正式归还。AI 不参与授权，只提供证据。"""
    session.execute(text(
        "INSERT INTO return_records (id, item_id, matched_candidate_id, returned_to, "
        "returned_by, verification_method, verification_result, notes) "
        "VALUES (:id, :item, :cand, :to, :by, :method, :result, :notes)"
    ), {
        "id": str(uuid.uuid4()), "item": item_id, "cand": payload.candidate_id,
        "to": payload.returned_to, "by": payload.returned_by,
        "method": payload.verification_method, "result": payload.verification_result,
        "notes": payload.notes,
    })
    session.execute(text(
        "UPDATE item_records SET status = 'RETURNED' WHERE id = :item"), {"item": item_id})
    repo.record_audit(session, actor_id=payload.returned_by, action="ITEM_RETURNED",
                      entity_type="item_records", entity_id=item_id,
                      after={"method": payload.verification_method})
    return {"item_id": item_id, "status": "RETURNED"}


@router.get("/api/items/{item_id}/secret-questions")
def secret_questions(item_id: str, session: Session = Depends(get_session)):
    """Secret Attribute：只返回「该问什么」，绝不返回答案。"""
    rows = session.execute(text(
        "SELECT attribute_code FROM item_attributes "
        "WHERE item_id = :item AND is_secret = TRUE"), {"item": item_id}).fetchall()
    return {
        "item_id": item_id,
        "questions": [f"请描述该物品的 {r[0]}" for r in rows],
    }


@router.post("/api/items/{item_id}/verify-secret")
def verify_secret(item_id: str, answers: dict[str, str],
                  session: Session = Depends(get_session)):
    """核对 secret attribute 作为归还验证证据。"""
    from ..ai.normalize import canonical_attribute_code, norm_text

    rows = session.execute(text(
        "SELECT attribute_code, value_text FROM item_attributes "
        "WHERE item_id = :item AND is_secret = TRUE"), {"item": item_id}).fetchall()
    if not rows:
        return {"item_id": item_id, "verified": False, "reason": "该物品没有登记 secret attribute"}

    normalized = {canonical_attribute_code(k): norm_text(v) for k, v in answers.items()}
    checked = []
    for code, expected in rows:
        given = normalized.get(canonical_attribute_code(code))
        ok = bool(given) and (given == norm_text(expected)
                              or given in norm_text(expected)
                              or norm_text(expected) in given)
        checked.append({"attribute": code, "matched": ok})
    verified = all(c["matched"] for c in checked)
    return {"item_id": item_id, "verified": verified, "details": checked}
