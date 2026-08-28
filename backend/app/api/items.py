"""Lost Service / Found Service：登记 -> AI 抽取 -> Embedding -> 触发双向匹配。"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..ai import extraction
from ..ai.normalize import canonical_attribute_code, norm_text
from ..db import get_session
from ..matching.engine import run_matching
from .. import repository as repo
from ..schemas import ExtractIn, FoundReportIn, ItemCreated, LostReportIn

router = APIRouter(tags=["items"])


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _resolve_category_id(session: Session, code: str | None) -> int | None:
    if not code:
        return None
    row = session.execute(
        text("SELECT id FROM item_categories WHERE code = :code"), {"code": code}
    ).fetchone()
    return row[0] if row else None


def _resolve_location_id(session: Session, name: str | None) -> int | None:
    """按名称 / 别名解析到 location_id；找不到就新建一个叶子节点。"""
    if not name:
        return None
    n = norm_text(name)
    row = session.execute(text(
        "SELECT id FROM locations "
        "WHERE normalized_name = :n OR lower(name) = :n "
        "   OR aliases @> CAST(:alias AS jsonb) LIMIT 1"
    ), {"n": n, "alias": f'["{name}"]'}).fetchone()
    if row:
        return row[0]
    row = session.execute(text(
        "INSERT INTO locations (name, normalized_name, location_type) "
        "VALUES (:name, :n, 'UNKNOWN') RETURNING id"
    ), {"name": name, "n": n}).fetchone()
    return row[0]


def _insert_attributes(session: Session, item_id: str,
                       rows: list[dict[str, Any]]) -> None:
    for a in rows:
        session.execute(text(
            "INSERT INTO item_attributes (id, item_id, attribute_code, value_text, "
            "original_value, source, source_type, confidence, is_secret) "
            "VALUES (:id, :item, :code, :value, :orig, :source, :stype, :conf, :secret)"
        ), {
            "id": str(uuid.uuid4()),
            "item": item_id,
            "code": a["attribute_code"],
            "value": a.get("value_text"),
            "orig": a.get("original_value"),
            "source": a.get("source", "AI"),
            "stype": a.get("source_type", "EXPLICIT"),
            "conf": a.get("confidence"),
            "secret": bool(a.get("is_secret", False)),
        })


def _manual_attributes(mapping: dict[str, str], source: str,
                       is_secret: bool = False) -> list[dict[str, Any]]:
    return [
        {
            "attribute_code": canonical_attribute_code(k),
            "value_text": v,
            "original_value": v,
            "source": source,
            "source_type": "EXPLICIT",
            "confidence": 1.0 if source in {"STAFF", "ADMIN"} else 0.85,
            "is_secret": is_secret,
        }
        for k, v in (mapping or {}).items() if v
    ]


def _create_item(session: Session, *, record_type: str, description: str,
                 overrides: dict[str, Any], manual_attrs: list[dict[str, Any]],
                 created_by: str | None) -> tuple[str, dict[str, Any]]:
    parsed = extraction.extract(description)
    core = parsed["core"]
    for key in ("category", "brand", "model"):
        if overrides.get(key):
            core[key] = overrides[key]

    category_id = _resolve_category_id(session, core.get("category"))
    item_id = str(uuid.uuid4())
    session.execute(text(
        "INSERT INTO item_records (id, record_type, status, category_id, brand, model, "
        "raw_description, normalized_text, created_by) "
        "VALUES (:id, :rtype, 'ACTIVE', :cat, :brand, :model, :raw, :norm, :by)"
    ), {
        "id": item_id, "rtype": record_type, "cat": category_id,
        "brand": core.get("brand"), "model": core.get("model"),
        "raw": description, "norm": parsed["normalized_text"], "by": created_by,
    })

    # AI 属性在前，人工属性在后（人工可靠性更高，冲突检测按 code 取首条）
    _insert_attributes(session, item_id, manual_attrs + parsed["attributes"])
    extraction.save_analysis(session, item_id, parsed)
    return item_id, parsed


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

@router.post("/api/lost", response_model=ItemCreated)
def create_lost(payload: LostReportIn, session: Session = Depends(get_session)):
    item_id, parsed = _create_item(
        session, record_type="LOST", description=payload.description,
        overrides=payload.model_dump(),
        manual_attrs=(_manual_attributes(payload.attributes, "USER")
                      + _manual_attributes(payload.secret_attributes, "USER", True)),
        created_by=payload.reported_by,
    )
    session.execute(text(
        "INSERT INTO lost_reports (id, item_id, lost_at, lost_at_start, lost_at_end, "
        "lost_location_id, circumstances, reported_by) "
        "VALUES (:id, :item, :at, :start, :end, :loc, :circ, :by)"
    ), {
        "id": str(uuid.uuid4()), "item": item_id,
        "at": payload.lost_at_start,
        "start": payload.lost_at_start, "end": payload.lost_at_end,
        "loc": _resolve_location_id(session,
                                    payload.location_name or parsed.get("location_name")),
        "circ": payload.circumstances, "by": payload.reported_by,
    })
    session.flush()

    match = run_matching(session, item_id, trigger="LOST_CREATED") if payload.auto_match else None
    repo.record_audit(session, actor_id=payload.reported_by, action="LOST_CREATED",
                      entity_type="item_records", entity_id=item_id,
                      after={"description": payload.description})
    return ItemCreated(item_id=item_id, record_type="LOST",
                       category=parsed["core"].get("category"),
                       normalized_text=parsed["normalized_text"],
                       attributes=parsed["attributes"], match=match)


@router.post("/api/found", response_model=ItemCreated)
def create_found(payload: FoundReportIn, session: Session = Depends(get_session)):
    item_id, parsed = _create_item(
        session, record_type="FOUND", description=payload.description,
        overrides=payload.model_dump(),
        manual_attrs=(_manual_attributes(payload.attributes, "STAFF")
                      + _manual_attributes(payload.secret_attributes, "STAFF", True)),
        created_by=payload.found_by,
    )
    session.execute(text(
        "INSERT INTO found_reports (id, item_id, found_at, found_location_id, "
        "found_by, storage_location) VALUES (:id, :item, :at, :loc, :by, :store)"
    ), {
        "id": str(uuid.uuid4()), "item": item_id, "at": payload.found_at,
        "loc": _resolve_location_id(session,
                                    payload.location_name or parsed.get("location_name")),
        "by": payload.found_by, "store": payload.storage_location,
    })
    session.flush()

    match = run_matching(session, item_id, trigger="FOUND_CREATED") if payload.auto_match else None
    repo.record_audit(session, actor_id=payload.found_by, action="FOUND_CREATED",
                      entity_type="item_records", entity_id=item_id,
                      after={"description": payload.description})
    return ItemCreated(item_id=item_id, record_type="FOUND",
                       category=parsed["core"].get("category"),
                       normalized_text=parsed["normalized_text"],
                       attributes=parsed["attributes"], match=match)


@router.get("/api/items/{item_id}")
def get_item(item_id: str, session: Session = Depends(get_session)):
    bundle = repo.load_item_bundle(session, item_id)
    if bundle is None:
        raise HTTPException(404, "记录不存在")
    bundle["attributes"] = [a for a in bundle["attributes"] if not a.get("is_secret")]
    return bundle


@router.post("/api/ai/extract")
def ai_extract(payload: ExtractIn):
    """单独暴露抽取能力，方便前端做「确认 AI 理解是否正确」。"""
    return extraction.extract(payload.description)
