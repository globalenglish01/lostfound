"""匹配引擎单测（不需要数据库）。

覆盖设计文档里最关键的几条铁律：
- Missing != Mismatch（Available Evidence Normalization）
- 语义 0.97 也压不过型号冲突
- black vs dark gray 不是冲突
- CRITICAL 冲突封顶匹配等级
- Evidence Bonus 必须有上限
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai.llm_provider import RuleLLM                       # noqa: E402
from app.ai.normalize import canonical_color, color_family     # noqa: E402
from app.matching import features as F                         # noqa: E402
from app.matching.conflicts import aggregate_penalty, detect_conflicts  # noqa: E402
from app.matching.engine import match_pair                     # noqa: E402
from app.matching.retrieval import rrf_fuse                    # noqa: E402
from app.matching.scoring import compute_score                 # noqa: E402

TZ = timezone.utc


def attr(code, value, source="USER"):
    return {"attribute_code": code, "value_text": value, "source": source}


# ---------------------------------------------------------------------------
# 场景 1：教科书级正例 —— iPhone 15 Pro + 猫咪贴纸
# ---------------------------------------------------------------------------

def _iphone_case():
    lost = {
        "id": "L1",
        "category": "smartphone",
        "brand": "Apple",
        "model": "iPhone 15 Pro",
        "attributes": [attr("brand", "Apple"), attr("model", "iPhone 15 Pro"),
                       attr("color", "黑色"), attr("case", "透明")],
        "distinctive": ["猫咪贴纸"],
        "location": {"id": 10, "name": "新宿站", "ancestors": [], "lat": 35.6896, "lon": 139.7006},
        "lost_at_start": datetime(2026, 8, 27, 19, 0, tzinfo=TZ),
        "lost_at_end": datetime(2026, 8, 27, 22, 0, tzinfo=TZ),
        "raw_description": "昨晚在新宿站丢了黑色 iPhone 15 Pro，透明手机壳，背面有猫咪贴纸",
    }
    found = {
        "id": "F1",
        "category": "smartphone",
        "brand": "Apple",
        "model": "iPhone 15 Pro",
        "attributes": [attr("brand", "Apple", "STAFF"), attr("model", "iPhone 15 Pro", "STAFF"),
                       attr("color", "深灰色", "STAFF"), attr("case", "透明", "STAFF")],
        "distinctive": ["猫咪图案"],
        "location": {"id": 10, "name": "新宿站", "ancestors": [], "lat": 35.6896, "lon": 139.7006},
        "found_at": datetime(2026, 8, 27, 20, 10, tzinfo=TZ),
        "raw_description": "新宿站拾获深灰色 iPhone 15 Pro，透明保护套，背面有猫图案",
    }
    return lost, found


def test_textbook_positive_scores_high():
    lost, found = _iphone_case()
    r = match_pair(lost, found, semantic_cosine=0.91, bm25_rank=2.0, with_llm=False)
    assert r["final_score"] >= 85, r["dimension_scores"]
    assert r["match_level"] in {"HIGH", "VERY_HIGH"}
    assert r["conflict_penalty"] == 0
    # 拾获时间落在丢失区间内
    assert r["dimension_scores"]["time"] == 100.0
    # 同一 location id
    assert r["dimension_scores"]["location"] == 100.0


def test_image_missing_does_not_score_zero():
    """图片缺失时不能给 0 分，而是不参与评分。"""
    lost, found = _iphone_case()
    r = match_pair(lost, found, semantic_cosine=0.91, with_llm=False)
    assert r["dimension_scores"]["image"] is None
    assert "image" in r["unknown_features"]
    assert "image" not in r["used_weights"]


def test_black_vs_dark_gray_is_not_a_conflict():
    lost, found = _iphone_case()
    r = match_pair(lost, found, semantic_cosine=0.91, with_llm=False)
    assert all(c["field_name"] != "color" for c in r["conflicts"])
    assert color_family("黑色") == color_family("深灰色") == "black"
    assert canonical_color("ブラック") == "black"


# ---------------------------------------------------------------------------
# 场景 2：Pro vs Pro Max —— 语义极高但必须被压下去
# ---------------------------------------------------------------------------

def test_model_conflict_beats_high_semantic():
    lost, found = _iphone_case()
    found = {**found, "model": "iPhone 15 Pro Max",
             "attributes": [attr("brand", "Apple", "STAFF"),
                            attr("model", "iPhone 15 Pro Max", "STAFF"),
                            attr("color", "深灰色", "STAFF"),
                            attr("case", "透明", "STAFF")]}
    r = match_pair(lost, found, semantic_cosine=0.97, bm25_rank=5.0, with_llm=False)

    assert any(c["field_name"] == "model" and c["severity"] == "CRITICAL"
               for c in r["conflicts"])
    assert r["conflict_penalty"] >= 70
    assert r["final_score"] < 50, r["final_score"]
    # 铁律：即使 99 分，只要存在 CRITICAL 冲突，也不能进入 HIGH
    assert r["match_level"] in {"LOW", "IGNORE"}


def test_imei_conflict_rejects_outright():
    lost, found = _iphone_case()
    lost = {**lost, "attributes": lost["attributes"] + [attr("imei", "123456789012345")]}
    found = {**found, "attributes": found["attributes"]
             + [attr("imei", "999999999999999", "STAFF")]}
    r = match_pair(lost, found, semantic_cosine=0.99, with_llm=False)
    assert r["rejected"] is True
    assert r["final_score"] == 0.0
    assert r["match_level"] == "REJECT"


def test_brand_conflict_is_major():
    lost, found = _iphone_case()
    found = {**found, "brand": "Samsung", "model": "Galaxy S24",
             "attributes": [attr("brand", "Samsung", "STAFF"),
                            attr("model", "Galaxy S24", "STAFF")]}
    r = match_pair(lost, found, semantic_cosine=0.88, with_llm=False)
    severities = {c["severity"] for c in r["conflicts"]}
    assert "MAJOR" in severities or "CRITICAL" in severities
    assert r["final_score"] < 60


# ---------------------------------------------------------------------------
# Available Evidence Normalization
# ---------------------------------------------------------------------------

def test_unknown_is_not_conflict():
    lost = {"id": "L", "category": "wallet", "brand": None, "model": None,
            "attributes": [attr("color", "黑色")], "distinctive": [],
            "location": None, "lost_at_start": None, "lost_at_end": None}
    found = {"id": "F", "category": "wallet", "brand": "Prada", "model": None,
             "attributes": [attr("color", "黑色", "STAFF"), attr("brand", "Prada", "STAFF")],
             "distinctive": [], "location": None, "found_at": None}
    r = match_pair(lost, found, with_llm=False)
    # Lost 侧没提 brand -> 不得算作冲突，也不得算作匹配
    assert r["conflicts"] == []
    assert r["dimension_scores"]["location"] is None
    assert r["dimension_scores"]["time"] is None


def test_weights_renormalize_when_features_missing():
    lost, found = _iphone_case()
    full = match_pair(lost, found, semantic_cosine=0.9, bm25_rank=1.0, with_llm=False)
    partial = match_pair({**lost, "location": None}, {**found, "location": None},
                         semantic_cosine=0.9, bm25_rank=1.0, with_llm=False)
    assert "location" in full["used_weights"]
    assert "location" not in partial["used_weights"]
    # 分母重新归一化后，缺一个满分维度不应把分数拉到接近 0
    assert partial["final_score"] > 70


# ---------------------------------------------------------------------------
# 各维度函数
# ---------------------------------------------------------------------------

def test_time_decay():
    base = datetime(2026, 8, 27, 19, 0, tzinfo=TZ)
    s0 = F.time_score(base, base, base, 24).score
    s24 = F.time_score(base, base, base + timedelta(hours=24), 24).score
    s48 = F.time_score(base, base, base + timedelta(hours=48), 24).score
    assert s0 == 100.0
    assert 36 < s24 < 38          # 100 * e^-1
    assert 13 < s48 < 14          # 100 * e^-2
    assert s0 > s24 > s48


def test_time_range_containment():
    start = datetime(2026, 8, 27, 19, 0, tzinfo=TZ)
    end = datetime(2026, 8, 27, 22, 0, tzinfo=TZ)
    inside = F.time_score(start, end, datetime(2026, 8, 27, 20, 10, tzinfo=TZ), 24)
    assert inside.score == 100.0


def test_location_hierarchy_beats_coordinates():
    """新宿站 vs 新宿站南口：靠 location 树而非经纬度。"""
    station = {"id": 10, "name": "新宿站", "ancestors": [], "lat": 35.6896, "lon": 139.7006}
    exit_south = {"id": 11, "name": "新宿站南口", "ancestors": [10],
                  "lat": 35.6880, "lon": 139.7000}
    s = F.location_score(station, exit_south).score
    assert s == 95.0


def test_location_distance_decay():
    a = {"id": 1, "name": "A", "ancestors": [], "lat": 35.0, "lon": 139.0}
    b = {"id": 2, "name": "B", "ancestors": [], "lat": 35.0045, "lon": 139.0}  # ~500m
    s = F.location_score(a, b, tau_m=500).score
    assert 30 < s < 45


def test_distinctive_similar_sticker():
    s = F.distinctive_score(["猫咪贴纸"], ["hello kitty 贴纸"]).score
    assert s is not None and s >= 70


def test_distinctive_missing_is_skipped():
    assert F.distinctive_score([], ["猫咪贴纸"]).score is None


def test_semantic_never_alone_decides():
    """只有语义证据时，分数不应达到自动推荐档。"""
    lost = {"id": "L", "category": None, "brand": None, "model": None,
            "attributes": [], "distinctive": [], "location": None,
            "lost_at_start": None, "lost_at_end": None}
    found = {"id": "F", "category": None, "brand": None, "model": None,
             "attributes": [], "distinctive": [], "location": None, "found_at": None}
    r = match_pair(lost, found, semantic_cosine=0.99, with_llm=False)
    assert r["dimension_scores"]["semantic"] == 99.0
    assert r["match_level"] != "VERY_HIGH"
    assert r["confidence"] < 0.6


# ---------------------------------------------------------------------------
# 冲突聚合 / Bonus 上限
# ---------------------------------------------------------------------------

def test_penalty_aggregation_is_not_linear():
    assert aggregate_penalty([]) == 0.0
    assert aggregate_penalty([10]) == 10.0
    # 10 + 8/2 + 6/2 = 17，而不是 24
    assert aggregate_penalty([10, 8, 6]) == 17.0


def test_evidence_bonus_capped():
    lost, found = _iphone_case()
    lost = {**lost, "distinctive": ["猫咪贴纸", "背面裂痕", "刻字 ABC"],
            "attributes": lost["attributes"] + [attr("serial_number", "ABC12345")]}
    found = {**found, "distinctive": ["猫咪贴纸", "背面裂痕", "刻字 ABC"],
             "attributes": found["attributes"] + [attr("serial_number", "ABC12345", "STAFF")]}
    r = match_pair(lost, found, semantic_cosine=0.95, with_llm=False)
    assert r["evidence_bonus"] <= 10.0
    assert r["final_score"] <= 100.0


def test_conflict_detection_ignores_missing_side():
    report = detect_conflicts([attr("model", "iPhone 15 Pro")], [], {}, {})
    assert report.conflicts == []
    assert report.penalty == 0.0


# ---------------------------------------------------------------------------
# RRF
# ---------------------------------------------------------------------------

def test_rrf_rewards_multi_channel_recall():
    fused = rrf_fuse({
        "structured": ["A", "B"],
        "keyword": ["B", "C"],
        "vector": ["B", "A"],
    }, k=60)
    assert fused[0].item_id == "B"          # 三路都召回
    assert set(fused[0].sources) == {"structured", "keyword", "vector"}


# ---------------------------------------------------------------------------
# 规则 LLM
# ---------------------------------------------------------------------------

def test_rule_llm_extraction():
    out = RuleLLM().extract("昨天晚上在新宿站丢了一部黑色 iPhone 15 Pro，透明手机壳，背面有猫咪贴纸")
    assert out["category"]["value"] == "smartphone"
    assert out["brand"]["value"] == "Apple"
    assert out["model"]["value"] == "iphone 15 pro"
    assert out["color"]["value"] == "black"
    assert out["location"]["name"] == "Shinjuku Station"
    assert out["distinctive_features"], "应抓到猫咪贴纸"
    assert out["raw_description"].startswith("昨天晚上")


def test_rule_llm_never_invents():
    out = RuleLLM().extract("丢了一个包")
    assert out["brand"]["value"] is None
    assert out["model"]["value"] is None
    assert out["serial_numbers"] == []


def test_rule_llm_does_not_override_algorithm_score():
    lost, found = _iphone_case()
    r = match_pair(lost, found, semantic_cosine=0.91, with_llm=True)
    assert r["llm"]["decision"] in {"MATCH", "LIKELY_MATCH"}
    # LLM 有自己的 confidence，但分数仍是算法的
    assert r["final_score"] == r["algorithm_score"]
    assert "explanation" in r


def test_llm_cannot_rescue_a_critical_conflict():
    lost, found = _iphone_case()
    found = {**found, "model": "iPhone 15 Pro Max",
             "attributes": [attr("model", "iPhone 15 Pro Max", "STAFF")]}
    r = match_pair(lost, found, semantic_cosine=0.99, with_llm=True)
    assert r["llm"]["decision"] == "NOT_MATCH"
    assert r["recommended_action"] == "DO_NOT_RECOMMEND"


@pytest.mark.parametrize("score,expected", [
    (98, "VERY_HIGH"), (90, "HIGH"), (75, "MEDIUM"), (60, "LOW"), (10, "IGNORE"),
])
def test_match_levels(score, expected):
    from app.matching.conflicts import ConflictReport
    from app.matching.scoring import resolve_level
    level, _ = resolve_level(score, ConflictReport())
    assert level == expected
