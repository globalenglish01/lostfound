"""AI 理解层回归测试。

这些用例全部来自真实评测中踩到的坑，每一条都对应一次线上级别的静默漏召。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai.llm_provider import RuleLLM, _alias_hit             # noqa: E402
from app.ai.normalize import (                                   # noqa: E402
    canonical_category,
    color_family,
    strip_report_boilerplate,
    text_overlap,
)
from app.matching.conflicts import detect_conflicts             # noqa: E402
from app.matching.engine import match_pair                      # noqa: E402


def cat(text: str) -> str | None:
    return RuleLLM().extract(text)["category"]["value"]


# ---------------------------------------------------------------------------
# 单字汉字别名的边界
# ---------------------------------------------------------------------------

def test_single_kanji_alias_not_matched_inside_compound():
    """「紙で包装されています」里的「包」不能把清酒判成包。"""
    assert cat("日本酒の一升瓶の忘れ物です。紙で包装されています。") == "sake"


@pytest.mark.parametrize("text,expected", [
    ("黒い鞄を紛失しました", "bag"),          # 鞄 左右都是假名 -> 命中
    ("紺の折りたたみ忘れた", "umbrella"),
    ("自転車の鍵の拾得物です", "keys"),
    ("文庫本の拾得物です", "book"),
])
def test_single_kanji_alias_matched_between_kana(text, expected):
    """反向：不能因为怕误伤就把单字汉字一刀切禁掉，日语里它们就是物品名。"""
    assert cat(text) == expected


def test_ascii_alias_respects_word_boundary():
    assert _alias_hit("baggage claim", "bag") is False
    assert _alias_hit("a black bag", "bag") is True
    assert _alias_hit("monkey", "key") is False


# ---------------------------------------------------------------------------
# 歧义类别：UNKNOWN != CONFLICT
# ---------------------------------------------------------------------------

def test_ambiguous_category_keeps_all_candidates():
    """left a bottle of sake：bottle 比 sake 长，不能因此判成水壶把清酒排除。"""
    node = RuleLLM().extract("left a bottle of sake")["category"]
    assert set(node["candidates"]) >= {"water_bottle", "sake"}
    assert node["source_type"] == "UNCERTAIN"


def test_ambiguous_category_does_not_create_conflict():
    lost = {"id": "L", "category": "water_bottle",
            "category_candidates": ["water_bottle", "sake"], "category_uncertain": True,
            "attributes": [], "distinctive": [], "location": None,
            "lost_at_start": None, "lost_at_end": None}
    found = {"id": "F", "category": "sake", "category_candidates": ["sake"],
             "attributes": [], "distinctive": [], "location": None, "found_at": None}
    r = match_pair(lost, found, semantic_cosine=0.6, with_llm=False)
    assert r["conflicts"] == []
    # 候选集合有交集 -> 按命中算，但可靠性下调
    assert r["dimension_scores"]["category"] == 100.0


def test_uncertain_category_is_skipped_in_conflicts():
    report = detect_conflicts([], [],
                              {"category": "bag", "category_uncertain": True},
                              {"category": "wallet"})
    assert report.conflicts == []


# ---------------------------------------------------------------------------
# 颜色
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["青色の魔法瓶を紛失", "あおいすいとうをおとしました",
                                  "ブルーのマイボトルを落としました", "蓝色不锈钢水壶不见了"])
def test_blue_variants(text):
    assert RuleLLM().extract(text)["color"]["value"] == "blue"


def test_color_family_across_scripts():
    assert color_family("黒い") == color_family("ブラック") == color_family("深灰色") == "black"


# ---------------------------------------------------------------------------
# 报案套话剥离
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,kept", [
    ("黒いリュックサックを拾いました。ナイロン製です。", "黒いリュックサック"),
    ("黒い鞄を紛失しました", "黒い鞄"),
    ("left a bottle of sake", "bottle of sake"),
    ("丢了一个黑色双肩包", "黑色双肩包"),
])
def test_strip_boilerplate(raw, kept):
    assert kept in strip_report_boilerplate(raw)


def test_strip_boilerplate_never_returns_empty():
    """整句都是套话时必须退回原文，不能生成空向量。"""
    assert strip_report_boilerplate("なくしました").strip()


# ---------------------------------------------------------------------------
# 属性证据厚度
# ---------------------------------------------------------------------------

def test_single_attribute_match_is_not_full_confidence():
    """只对上一个颜色不能拿满权重，否则一堆同色无关物品会顶到前面。"""
    lost = {"id": "L", "category": None, "attributes":
            [{"attribute_code": "color", "value_text": "black", "source": "USER"}],
            "distinctive": [], "location": None,
            "lost_at_start": None, "lost_at_end": None}
    found = {"id": "F", "category": None, "attributes":
             [{"attribute_code": "color", "value_text": "black", "source": "STAFF"}],
             "distinctive": [], "location": None, "found_at": None}
    thin = match_pair(lost, found, semantic_cosine=0.5, with_llm=False)

    rich_attrs_l = lost["attributes"] + [
        {"attribute_code": "brand", "value_text": "Apple", "source": "USER"},
        {"attribute_code": "model", "value_text": "iPhone 15 Pro", "source": "USER"}]
    rich_attrs_f = found["attributes"] + [
        {"attribute_code": "brand", "value_text": "Apple", "source": "STAFF"},
        {"attribute_code": "model", "value_text": "iPhone 15 Pro", "source": "STAFF"}]
    rich = match_pair({**lost, "attributes": rich_attrs_l},
                      {**found, "attributes": rich_attrs_f},
                      semantic_cosine=0.5, with_llm=False)

    # 两边 attribute 分都是 100，但证据厚度不同 -> 最终分必须拉开
    assert thin["dimension_scores"]["attribute"] == 100.0
    assert rich["dimension_scores"]["attribute"] == 100.0
    assert rich["final_score"] > thin["final_score"]


# ---------------------------------------------------------------------------
# CJK 字符级重叠
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("a,b,low", [
    ("猫咪贴纸", "猫咪图案", 0.4),
    ("猫咪贴纸", "hello kitty 贴纸", 0.4),
])
def test_cjk_overlap(a, b, low):
    assert text_overlap(a, b) >= low


def test_cjk_overlap_unrelated():
    assert text_overlap("猫咪贴纸", "黑色雨伞") == 0.0


# ---------------------------------------------------------------------------
# 不许编造
# ---------------------------------------------------------------------------

def test_extraction_never_invents_brand():
    out = RuleLLM().extract("黒いバッグをなくしました")
    assert out["brand"]["value"] is None
    assert out["model"]["value"] is None
    assert out["serial_numbers"] == []


def test_unknown_word_yields_no_dictionary_category():
    """词典里没有「コインケース」——严格模式下不能瞎填，只能靠零样本兜底。"""
    assert canonical_category("コインケース") is None


# ---------------------------------------------------------------------------
# 零样本分类阈值必须量纲无关
# ---------------------------------------------------------------------------

def test_zero_shot_thresholds_are_scale_free(monkeypatch):
    """换模型不能因为余弦分布被压缩就整体失灵。

    同一组「相对形状」的相似度，一份散布在 0.1~0.7（paraphrase 系列），
    一份压缩在 0.7~0.95（e5 系列），判定结果必须一致。
    """
    from app.ai import classify

    def run(sims: list[float]) -> str | None:
        protos = [(f"cat{i}", [s]) for i, s in enumerate(sims)]
        monkeypatch.setattr(classify, "_prototypes", lambda: protos)
        monkeypatch.setattr(classify, "cosine", lambda a, b: b[0])
        monkeypatch.setattr(
            classify, "get_embedding_provider",
            lambda: type("P", (), {"embed": staticmethod(lambda t, kind="passage": [1.0])})())
        return classify.zero_shot_category("x")[0]

    wide = [0.70, 0.40, 0.35, 0.30, 0.28, 0.25, 0.22, 0.20]
    # 同样的形状，线性压缩到 e5 的取值范围
    lo, hi = 0.70, 0.95
    span = max(wide) - min(wide)
    narrow = [lo + (v - min(wide)) / span * (hi - lo) for v in wide]

    assert run(wide) == run(narrow) == "cat0"


def test_zero_shot_refuses_when_no_clear_winner(monkeypatch):
    """所有类别都差不多时必须留空，绝不硬猜。"""
    from app.ai import classify

    protos = [(f"cat{i}", [s]) for i, s in enumerate([0.61, 0.60, 0.60, 0.59, 0.59, 0.58])]
    monkeypatch.setattr(classify, "_prototypes", lambda: protos)
    monkeypatch.setattr(classify, "cosine", lambda a, b: b[0])
    monkeypatch.setattr(
        classify, "get_embedding_provider",
        lambda: type("P", (), {"embed": staticmethod(lambda t, kind="passage": [1.0])})())

    assert classify.zero_shot_category("x")[0] is None
