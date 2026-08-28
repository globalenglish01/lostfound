"""API 请求/响应模型。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class LostReportIn(BaseModel):
    description: str = Field(..., description="用户原话，原封不动保存")
    category: str | None = None
    brand: str | None = None
    model: str | None = None
    location_name: str | None = None
    lost_at_start: datetime | None = None
    lost_at_end: datetime | None = None
    circumstances: str | None = None
    reported_by: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict,
                                       description="人工补充属性，source=USER")
    secret_attributes: dict[str, str] = Field(
        default_factory=dict,
        description="归还验证用，不对外展示（如「钱包里有一张黄色会员卡」）")
    auto_match: bool = True


class FoundReportIn(BaseModel):
    description: str
    category: str | None = None
    brand: str | None = None
    model: str | None = None
    location_name: str | None = None
    found_at: datetime | None = None
    storage_location: str | None = None
    found_by: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)
    secret_attributes: dict[str, str] = Field(default_factory=dict)
    auto_match: bool = True


class ItemCreated(BaseModel):
    item_id: str
    record_type: str
    category: str | None
    normalized_text: str
    attributes: list[dict[str, Any]]
    match: dict[str, Any] | None = None


class SearchIn(BaseModel):
    query: str
    type: str = Field("FOUND", description="要搜索的目标类型：FOUND 或 LOST")
    top_k: int = 20
    include_low: bool = False


class MatchDecisionIn(BaseModel):
    decision: str = Field(..., description="CONFIRMED / REJECTED / DEFERRED")
    decided_by: str | None = None
    decided_by_role: str | None = None
    reason: str | None = None


class ReturnIn(BaseModel):
    candidate_id: str | None = None
    returned_to: str | None = None
    returned_by: str | None = None
    verification_method: str = "STAFF_CONFIRMATION"
    verification_result: bool = True
    notes: str | None = None


class ExtractIn(BaseModel):
    description: str
