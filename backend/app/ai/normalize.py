"""属性标准化层。

这一层很容易被忽略，但极其重要：
    苹果 / Apple / iPhone / アイフォン / IPHONE  -> Apple
    黑色 / 黑 / 黒 / ブラック / dark black       -> black
    新宿駅 / 新宿站 / Shinjuku Station           -> 同一个 location
不依赖原始文字是否一致，是整个匹配的前置条件。
"""
from __future__ import annotations

import re
import unicodedata

from ..config import attribute_weights, conflict_rules, synonyms

_WS = re.compile(r"[\s　]+")
_PUNCT = re.compile(r"[·・.,，。、!！?？:：;；\-_/\\()（）\[\]【】\"'“”‘’]")


def norm_text(value: str | None) -> str:
    """NFKC + 小写 + 去标点空白，用于所有词典查表与精确比较。"""
    if not value:
        return ""
    s = unicodedata.normalize("NFKC", str(value)).strip().lower()
    s = _PUNCT.sub(" ", s)
    return _WS.sub(" ", s).strip()


def _build_reverse(section: str) -> dict[str, str]:
    table: dict[str, str] = {}
    for canonical, aliases in synonyms().get(section, {}).items():
        if canonical.startswith("_"):
            continue
        table[norm_text(canonical)] = canonical
        for alias in aliases:
            table[norm_text(alias)] = canonical
    return table


def canonical(section: str, value: str | None, strict: bool = False) -> str | None:
    """把一个值归一到词典里的 canonical 形式。

    strict=True 时词典未命中返回 None（用于 category / brand 这类必须落到主数据的字段）；
    strict=False 时返回归一化文本（用于 color / material 这类允许自由值的属性）。
    """
    n = norm_text(value)
    if not n:
        return None
    table = _build_reverse(section)
    if n in table:
        return table[n]
    # 允许「黑色苹果手机」这种整句里包含关键词的情况；
    # 单字别名不做子串匹配（「包装」不是「包」），最长别名优先
    for alias in sorted((a for a in table if len(a) >= 2), key=len, reverse=True):
        if alias in n:
            return table[alias]
    return None if strict else n


def canonical_category(value: str | None) -> str | None:
    """类别必须落到主数据里的 code，查不到就返回 None，不要污染字段。"""
    return canonical("category", value, strict=True)


def canonical_brand(value: str | None) -> str | None:
    return canonical("brand", value, strict=True)


def canonical_color(value: str | None) -> str | None:
    return canonical("color", value)


def canonical_attribute_code(code: str | None) -> str:
    """属性代码归一：手机壳/保护壳/case_type -> case。"""
    if not code:
        return ""
    alias = attribute_weights().get("_alias", {})
    n = str(code).strip()
    if n in alias:
        return alias[n]
    low = n.lower()
    for k, v in alias.items():
        if k.startswith("_"):
            continue
        if k.lower() == low:
            return v
    return low


def color_family(value: str | None) -> str | None:
    """颜色归族：black / dark gray / 深色 属于同一族，不构成冲突。"""
    n = norm_text(value)
    if not n:
        return None
    for family, members in conflict_rules()["color_families"].items():
        for m in members:
            if norm_text(m) == n:
                return family
    for family, members in conflict_rules()["color_families"].items():
        for m in members:
            mn = norm_text(m)
            if mn and mn in n:
                return family
    return n


_MODEL_SPLIT = re.compile(r"[^a-z0-9]+")


def model_tokens(value: str | None) -> list[str]:
    n = norm_text(value)
    if not n:
        return []
    return [t for t in _MODEL_SPLIT.split(n) if t]


def canonical_model(value: str | None) -> str | None:
    toks = model_tokens(value)
    return " ".join(toks) if toks else None


def canonical_text_for_attributes(attrs: dict[str, object]) -> str:
    """结构化属性 -> canonical text，供 ATTRIBUTES Embedding 使用。

    category: smartphone
    brand: Apple
    model: iPhone 15 Pro
    ...
    """
    lines: list[str] = []
    for key in sorted(attrs):
        value = attrs[key]
        if value in (None, "", [], {}):
            continue
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(v) for v in value if v)
            if not value:
                continue
        lines.append(f"{key}: {value}")
    return "\n".join(lines)


def _char_set(value: str) -> set[str]:
    return {c for c in norm_text(value).replace(" ", "") if c.strip()}


def text_overlap(a: str | None, b: str | None) -> float:
    """重叠系数 |A∩B| / min(|A|,|B|)，字符级。

    中日文没有空格，靠 split() 做词集合会把「猫咪贴纸」和「猫咪图案」判成毫无关系，
    因此这里统一用字符集合 + 词集合取较大者。
    """
    sa, sb = _char_set(a or ""), _char_set(b or "")
    if not sa or not sb:
        return 0.0
    char_ratio = len(sa & sb) / min(len(sa), len(sb))

    ta = {t for t in norm_text(a or "").split() if t}
    tb = {t for t in norm_text(b or "").split() if t}
    token_ratio = (len(ta & tb) / min(len(ta), len(tb))) if ta and tb else 0.0
    return max(char_ratio, token_ratio)


# ---------------------------------------------------------------------------
# 报案套话剥离
# ---------------------------------------------------------------------------
# 「黒いリュックサックを拾いました」和「黒い鞄を紛失しました」的句向量里，
# 「拾いました / 紛失しました」这类模板动词占了很大权重，把真正区分物品的名词稀释掉了。
# 做 embedding 前先把这些套话剥掉，只留下**物品本身的描述**。
_BOILERPLATE = [
    # 日本語：拾得側
    "の拾得物です", "の落とし物です", "の忘れ物です", "拾得物です", "落とし物です", "忘れ物です",
    "を拾いました", "を拾得しました", "拾得しました", "を保管しています", "保管しています",
    "を預かっています", "が届いています",
    # 日本語：遺失側
    "をなくしました", "を無くしました", "を失くしました", "なくしました", "無くしました",
    "を落としました", "落としました", "落としちゃった", "落とした",
    "を紛失しました", "紛失しました", "紛失", "を忘れました", "忘れました", "忘れてきた",
    "を置き忘れました", "置き忘れました", "が見つかりません", "見つかりません",
    "どっかいった", "をなくした", "なくした", "忘れた",
    # 中文
    "丢了一个", "丢了一台", "丢了一把", "丢了一部", "丢了", "丢失了", "丢失",
    "不见了", "找不到了", "找不到", "落了一瓶", "落了", "掉了",
    # English
    "i lost a", "i lost", "lost a", "lost my", "lost", "left a", "left my", "left",
    "missing a", "missing", "i cannot find", "cannot find",
]


def strip_report_boilerplate(text: str | None) -> str:
    """剥掉「丢了 / なくしました / lost a」这类报案套话，只保留物品描述。"""
    if not text:
        return ""
    s = str(text)
    for phrase in sorted(_BOILERPLATE, key=len, reverse=True):
        s = s.replace(phrase, " ")
        s = s.replace(phrase.upper(), " ")
        s = s.replace(phrase.title(), " ")
    s = re.sub(r"[。．.、,，!！?？]+", " ", s)
    s = _WS.sub(" ", s).strip()
    # 全剥没了说明整句都是套话，退回原文，宁可噪声也不能变成空向量
    return s or str(text).strip()
