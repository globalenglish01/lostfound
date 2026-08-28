"""AI Understanding Layer：自然语言 -> 结构化物品信息。

原始描述永远不被覆盖；AI 的产物一律带 source / source_type / confidence 落到
`item_attributes`，并把整份原始输出留档到 `ai_analyses`，模型升级后可重跑比对。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import settings
from . import prompts
from .llm_provider import get_llm_provider
from .normalize import (
    canonical_attribute_code,
    canonical_brand,
    canonical_category,
    canonical_color,
    canonical_model,
    norm_text,
)

# 抽取结果里这些键直接进 item_attributes
_ATTRIBUTE_KEYS = ("color", "material", "size", "case")


def extract(description: str) -> dict[str, Any]:
    """调用 LLM/规则抽取并做标准化。"""
    raw = get_llm_provider().extract(description)
    return normalize_extraction(raw, description)


def normalize_extraction(raw: dict[str, Any], description: str) -> dict[str, Any]:
    """把 Prompt schema 输出规整成内部结构（属性标准化在这里发生）。"""

    def val(key: str) -> tuple[Any, Any, float, str]:
        node = raw.get(key) or {}
        if not isinstance(node, dict):
            return node, node, 0.7, "EXPLICIT"
        return (node.get("value"), node.get("original"),
                float(node.get("confidence") or 0.0),
                node.get("source_type") or "EXPLICIT")

    category, category_orig, category_conf, category_st = val("category")
    brand, brand_orig, brand_conf, brand_st = val("brand")
    model, model_orig, model_conf, model_st = val("model")

    core = {
        "category": canonical_category(category),
        "brand": canonical_brand(brand),
        "model": canonical_model(model),
        "raw_description": description,
    }

    attributes: list[dict[str, Any]] = []

    def push(code: str, value, original, conf, stype):
        if value in (None, "", []):
            return
        attributes.append({
            "attribute_code": canonical_attribute_code(code),
            "value_text": str(value),
            "original_value": original,
            "source": "AI",
            "source_type": stype,
            "confidence": conf,
        })

    push("brand", core["brand"], brand_orig, brand_conf, brand_st)
    push("model", core["model"], model_orig, model_conf, model_st)
    for key in _ATTRIBUTE_KEYS:
        v, orig, conf, stype = val(key)
        if key == "color":
            v = canonical_color(v)
        push(key, v, orig, conf, stype)

    for feat in raw.get("distinctive_features") or []:
        if isinstance(feat, dict):
            push("distinctive", feat.get("value"), feat.get("original"),
                 float(feat.get("confidence") or 0.7),
                 feat.get("source_type") or "EXPLICIT")
        elif feat:
            push("distinctive", norm_text(str(feat)), str(feat), 0.7, "EXPLICIT")

    for item in raw.get("contents") or []:
        push("contents", item, item, 0.7, "EXPLICIT")

    for sn in raw.get("serial_numbers") or []:
        if isinstance(sn, dict):
            code = "imei" if str(sn.get("type", "")).upper() == "IMEI" else "serial_number"
            push(code, sn.get("value"), sn.get("value"), 0.99, "EXPLICIT")
        elif sn:
            push("serial_number", sn, sn, 0.99, "EXPLICIT")

    location = raw.get("location") or {}
    timing = raw.get("time") or {}

    normalized_text = _canonical_description(core, attributes, description)

    return {
        "core": core,
        "attributes": attributes,
        "location_name": location.get("name"),
        "time_from": timing.get("from"),
        "time_to": timing.get("to"),
        "normalized_text": normalized_text,
        "confidence": max([category_conf, brand_conf, model_conf, 0.0]),
        "raw": raw,
    }


def _canonical_description(core: dict[str, Any], attributes: list[dict[str, Any]],
                           description: str) -> str:
    """normalized_text：标准化后的可检索文本（同时喂 FTS 与 TEXT Embedding）。"""
    parts = [description]
    for key in ("category", "brand", "model"):
        if core.get(key):
            parts.append(str(core[key]))
    parts.extend(a["value_text"] for a in attributes if a.get("value_text"))
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        n = norm_text(p)
        if n and n not in seen:
            seen.add(n)
            out.append(str(p))
    return " ".join(out)


_ANALYSIS_INSERT = """
INSERT INTO ai_analyses (id, item_id, analysis_type, model_provider, model_name,
                         prompt_version, input_hash, result_json, confidence)
VALUES (:id, :item_id, :atype, :provider, :model, :pversion, :ihash,
        CAST(:result AS jsonb), :confidence)
"""


def save_analysis(session: Session, item_id: str, extraction: dict[str, Any]) -> None:
    llm = get_llm_provider()
    session.execute(text(_ANALYSIS_INSERT), {
        "id": str(uuid.uuid4()),
        "item_id": item_id,
        "atype": "ATTRIBUTE_EXTRACTION",
        "provider": llm.name,
        "model": llm.model,
        "pversion": prompts.PROMPT_VERSION_EXTRACTION,
        "ihash": hashlib.sha256(
            (extraction["core"].get("raw_description") or "").encode("utf-8")
        ).hexdigest(),
        "result": json.dumps(extraction["raw"], ensure_ascii=False),
        "confidence": extraction.get("confidence"),
    })


def query_understanding(query: str) -> dict[str, Any]:
    """Search API 的查询理解：与建档抽取共用同一套抽取器。"""
    parsed = extract(query)
    return {
        "query": query,
        "category": parsed["core"].get("category"),
        "brand": parsed["core"].get("brand"),
        "model": parsed["core"].get("model"),
        "location_name": parsed.get("location_name"),
        "attributes": parsed["attributes"],
        "normalized_text": parsed["normalized_text"],
        "algorithm_version": settings.algorithm_version,
    }
