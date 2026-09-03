"""题库领域的术语归一。

两个轴必须分开，合在一起就毁了区分度：

    services  粗粒度、高召回 —— 「这两道题都涉及 S3」
    concepts  细粒度、有区分度 —— 「一道考跨区域复制，一道考访问点」

「Amazon S3」和「Amazon S3 跨区域复制」按子串合并会把后者抹平成前者，
于是所有 S3 题看起来都一样。这和失物系统里单字「包」误命中「包装」是同一类错误：
**归一化过头，比不归一化更危险，因为它是静默的。**
"""
from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"

_WS = re.compile(r"[\s　]+")
_PUNCT = re.compile(r"[·・.,，。、!！?？:：;；\-_/\\()（）\[\]【】\"'“”‘’]")


@lru_cache(maxsize=1)
def terms() -> dict:
    return json.loads((CONFIG_DIR / "qb_terms.json").read_text(encoding="utf-8"))


def norm(value: str | None) -> str:
    if not value:
        return ""
    s = unicodedata.normalize("NFKC", str(value)).strip().lower()
    s = _PUNCT.sub(" ", s)
    return _WS.sub(" ", s).strip()


# ---------------------------------------------------------------------------
# 服务抽取
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _service_patterns() -> list[tuple[str, str]]:
    """(canonical, 归一后的别名) 按别名长度降序，长的先匹配。"""
    out: list[tuple[int, str, str]] = []
    for canon, aliases in terms()["services"].items():
        for alias in [canon, *aliases]:
            a = norm(alias)
            if a:
                out.append((len(a), canon, a))
    out.sort(key=lambda x: -x[0])
    return [(c, a) for _, c, a in out]


_ASCII_TOKEN = re.compile(r"[a-z0-9]+")


def _alias_hit(hay: str, alias: str) -> bool:
    """短的纯 ASCII 别名（s3 / ec2 / waf / dx）必须按词边界匹配。

    否则 "dx" 会命中 "index"、"scp" 会命中 "描述scp"，
    在几百道题上会积累成大量假阳性。
    """
    if _ASCII_TOKEN.fullmatch(alias) and len(alias) <= 4:
        return alias in _ASCII_TOKEN.findall(hay)
    return alias in hay


def extract_services(*texts: str) -> set[str]:
    """从题干 / 选项 / 知识点里抽出涉及的 AWS 服务集合。"""
    hay = norm(" ".join(t for t in texts if t))
    if not hay:
        return set()
    found: set[str] = set()
    for canon, alias in _service_patterns():
        if canon in found:
            continue
        if _alias_hit(hay, alias):
            found.add(canon)
    return found


# ---------------------------------------------------------------------------
# 约束 / 优化目标抽取（判别两题是否同一考点的核心特征）
# ---------------------------------------------------------------------------

def extract_constraints(*texts: str) -> set[str]:
    """场景一样、服务一样，但一个求成本最优、一个求最小停机 —— 那是两道不同的题。"""
    hay = norm(" ".join(t for t in texts if t))
    if not hay:
        return set()
    found: set[str] = set()
    for canon, aliases in terms()["constraints"].items():
        for alias in aliases:
            a = norm(alias)
            if a and a in hay:
                found.add(canon)
                break
    return found


# ---------------------------------------------------------------------------
# 知识点归一（只归并真同义）
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _kp_synonym_map() -> dict[str, str]:
    table: dict[str, str] = {}
    for canon, aliases in terms().get("kp_manual_synonyms", {}).items():
        table[norm(canon)] = canon
        for a in aliases:
            table[norm(a)] = canon
    return table


@lru_cache(maxsize=4096)
def canonical_kp(name: str | None) -> str:
    """知识点归一。

    只做两件事，绝不做包含式合并：
      1. 去掉 AWS / Amazon / 亚马逊 前缀（「AWS Lambda」≡「Lambda」）
      2. 查人工同义表（「成本效益」≡「成本优化」）
    「Amazon S3 跨区域复制」保持独立，不会被并进「Amazon S3」。
    """
    n = norm(name)
    if not n:
        return ""
    for prefix in terms()["vendor_prefixes"]:
        p = norm(prefix) + " "
        if n.startswith(p):
            n = n[len(p):]
            break
    return _kp_synonym_map().get(n, n)


def canonical_kps(names) -> set[str]:
    out = set()
    for raw in names or []:
        n = raw.get("name") if isinstance(raw, dict) else raw
        c = canonical_kp(n)
        if c:
            out.add(c)
    return out


# ---------------------------------------------------------------------------
# 题干清洗
# ---------------------------------------------------------------------------

_LEAD_NO = re.compile(r"^\s*(?:问题|Question)\s*#\s*\d+\s*", re.I)
_OPTION_LINE = re.compile(r"^\s*\**\s*([A-E])\s*[\.、)．]\s*", re.M)
# 题干末尾的设问句：几乎每题都有，语义上没有区分度，反而拉高所有题的相似度
_TAIL_QUESTION = re.compile(
    r"(哪(个|些|项|种)[^。？?]*[？?]|"
    r"(解决方案架构师|公司|团队)?应该(怎么做|如何|采取哪些)[^。？?]*[？?]|"
    r"which\s+(solution|combination|option)[^.?]*\?)",
    re.I)


def split_question(raw: str) -> tuple[str, dict[str, str]]:
    """把原始题目文本拆成 (题干, {选项字母: 选项文本})。"""
    text = _LEAD_NO.sub("", (raw or "").strip())
    marks = list(_OPTION_LINE.finditer(text))
    if not marks:
        return text.strip(), {}
    stem = text[: marks[0].start()].strip()
    options: dict[str, str] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        options[m.group(1)] = text[m.end(): end].strip()
    return stem, options


def strip_boilerplate(stem: str) -> str:
    """去掉「哪个解决方案能满足这些要求？」这类通用设问。

    560 道题里几乎每道都有，留着只会让所有题的余弦整体抬高，
    压缩掉真正有区分度的部分——和失物系统剥「なくしました / lost a」同一个道理。
    """
    s = _TAIL_QUESTION.sub(" ", stem or "")
    return _WS.sub(" ", s).strip() or (stem or "").strip()
