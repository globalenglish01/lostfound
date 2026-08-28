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


def canonical(section: str, value: str | None) -> str | None:
    """把一个值归一到词典里的 canonical 形式；查不到则返回归一化文本。"""
    n = norm_text(value)
    if not n:
        return None
    table = _build_reverse(section)
    if n in table:
        return table[n]
    # 允许「黑色苹果手机」这种整句里包含关键词的情况
    for alias, canon in table.items():
        if alias and len(alias) >= 2 and alias in n:
            return canon
    return n


def canonical_category(value: str | None) -> str | None:
    return canonical("category", value)


def canonical_brand(value: str | None) -> str | None:
    return canonical("brand", value)


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
