"""从 SAP-C02 题库原文（PDF 转出的 txt）解析完整题目。

这是唯一保留了**完整题干 + 完整选项**的来源。
下游的课程 JSON 与 optimal_study_set.json 都把题干硬截断了
（分别是 140 / 120 字，且一道题的选项都没保存），拿那些做判重等于比对半截文本。

PDF 抽文本有两个必须处理的坑：
  1. 硬换行按版面宽度断，会把词切开：「启用A\\nurora自动扩展」
  2. 题号前常带列表序号，且 # 与数字后可能没有空格：「5. 问题 #5一家公司……」

按用户要求，只取**题干与选项**；答案与解析不带入
（该题库的答案标注本身有 46% 的自相矛盾，不适合作为判重依据）。
"""
from __future__ import annotations

import re
from pathlib import Path

from .loader import Question, build_question

# 一行的开头如果是这些，就是新块，不能与上一行合并
_NEW_BLOCK = re.compile(
    r"^\s*(?:\d+\s*[\.、]\s*)?问题\s*#\s*\d+"      # 题目
    r"|^\s*[A-E]\s*[\.、)]\s"                       # 选项
    r"|^\s*答案\s*[：:]"
    r"|^\s*解析\s*[：:]"
    r"|^\s*正确答案\s*[：:]"
)

_QUESTION_SPLIT = re.compile(r"(?:^|\n)\s*(?:\d+\s*[\.、]\s*)?问题\s*#\s*(\d+)\s*")
_OPTION = re.compile(r"^\s*([A-E])\s*[\.、)]\s*(.*)$")
_ANSWER = re.compile(r"^\s*(?:正确)?答案\s*[：:]\s*([A-E](?:\s*[,，、和and\s]+[A-E])*)", re.I)
_STOP = re.compile(r"^\s*(?:解析|说明|Explanation)\s*[：:]")
# 多选靠题干里的「（选择两个）」判定，不靠答案行：
# 答案行只标了 10 道多选，题干提示却有 82 道——答案标注本身不可靠。
_MULTI = re.compile(r"选择\s*[两二三四2-4]\s*[个项]|choose\s+(?:two|three)", re.I)
_MULTI_N = {"两": 2, "二": 2, "2": 2, "三": 3, "3": 3, "四": 4, "4": 4}


def multi_count(stem: str) -> int:
    """题干要求选几项；单选返回 1。"""
    m = _MULTI.search(stem or "")
    if not m:
        return 1
    n = re.search(r"选择\s*([两二三四2-4])", stem)
    if n:
        return _MULTI_N.get(n.group(1), 2)
    return 3 if re.search(r"three", stem, re.I) else 2


def unwrap(text: str, *, new_block=None, cjk: bool = True) -> str:
    """把 PDF 的硬换行还原成逻辑行。

    中英文的换行规则正好相反，必须分开处理，否则一定出错：

      中文版按**版面宽度**硬断，会把词从中间切开：
          「启用A」+「urora自动扩展」  ->  直接粘，不能补空格
      英文版按**空格**换行，行尾就是词边界：
          「in an」+「on-premises」    ->  必须补空格，否则粘成 "anon-premises"
    """
    pattern = new_block or _NEW_BLOCK
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            out.append("")
            continue
        if out and out[-1] and not pattern.match(line):
            prev = out[-1]
            sep = ""
            if not cjk and prev[-1:].isascii() and line[:1].isascii():
                # 英文行尾是词边界；连字符结尾的断词除外（"on-\nline"）
                sep = "" if prev.endswith("-") else " "
            elif cjk:
                # 中文版里两种情况都存在，靠大小写区分：
                #   「启用A」+「urora自动扩展」 小写接续 -> 词被切开，直接粘
                #   「Amazon」+「S3存储桶」     大写开头 -> 词边界，必须补空格
                # 不补的话会粘成 "AmazonS3"，词典里的 "amazon s3" 就永远匹配不上，
                # 实测因此丢了 19% 题目的服务抽取。
                if prev[-1:].islower() and line[:1].isupper():
                    sep = " "
            out[-1] = prev + sep + line
        else:
            out.append(line)
    return "\n".join(out)


def parse(text: str, *, split=None, new_block=None, cjk: bool = True) -> list[dict]:
    """切出 [{no, stem, options, answer, select_n}]。"""
    body = unwrap(text, new_block=new_block, cjk=cjk)
    parts = (split or _QUESTION_SPLIT).split(body)
    # parts = [前言, no1, block1, no2, block2, ...]
    items: list[dict] = []
    for i in range(1, len(parts) - 1, 2):
        no, block = parts[i], parts[i + 1]
        stem_lines: list[str] = []
        options: dict[str, str] = {}
        answer: list[str] = []
        current: str | None = None

        for line in block.splitlines():
            if _STOP.match(line):
                break
            am = _ANSWER.match(line)
            if am and options:                       # 选项之后出现的才是答案行
                answer = sorted(set(re.findall(r"[A-E]", am.group(1))))
                break
            om = _OPTION.match(line)
            if om:
                current = om.group(1)
                options[current] = om.group(2).strip()
                continue
            if current:
                options[current] = (options[current] + line.strip()).strip()
            elif line.strip():
                stem_lines.append(line.strip())

        stem = "".join(stem_lines).strip()
        if stem and len(options) >= 3:
            items.append({"no": no, "stem": stem,
                          "options": {k: v for k, v in options.items() if v.strip()},
                          "answer": answer,
                          "select_n": multi_count(stem)})
    return items


def load_sapc02(path: str | Path, *, with_answer: bool = False) -> list[Question]:
    """解析题库原文，返回 Question 列表。

    with_answer=False（默认）时不带入答案：
    该题库的答案标注自相矛盾率 46%，不适合当判重依据；
    而且有了完整选项之后，判重靠「题干 + 全部选项」本来就够。
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    out: list[Question] = []
    for it in parse(text):
        if len(it["options"]) < 3:
            continue
        raw_q = f"问题 #{it['no']} {it['stem']}\n" + "\n".join(
            f"{k}. {v}" for k, v in sorted(it["options"].items()))
        raw_a = (f"✅ 正确答案: {', '.join(it['answer'])}"
                 if with_answer and it["answer"] else "")
        q = build_question(qid=f"sap-{it['no']}", raw_q=raw_q, raw_a=raw_a,
                           title=f"AWS SAP-C02 真题 #{it['no']}", path=str(path))
        q.options = dict(it["options"])
        q.option_texts = dict(it["options"])
        # 全部选项拼起来 = 「这道题在考哪几种做法」，不依赖答案
        q.answer_text = " ".join(v for _, v in sorted(it["options"].items()))
        q.source_no = it["no"]
        q.stem_truncated = False
        # 题干标记优先：答案行只标了 10 道多选，题干提示却有 82 道
        q.select_n = it["select_n"] if it["select_n"] > 1 else max(1, len(it["answer"]))
        q.qtype = "multi" if q.select_n > 1 else "single"
        out.append(q)
    return out


# ---------------------------------------------------------------------------
# 英文版
# ---------------------------------------------------------------------------
# 「Question #4A company is running...」——编号后面直接接题干，中间没有空格。
# 题干常以 "A company" 开头，但不会被误判成选项 A：
# 选项必须是「字母 + 点 + 空格」，"A company" 没有点。
# 行首带列表序号：「4. Question #4A company is running...」
_EN_SPLIT = re.compile(r"(?:^|\n)\s*(?:\d+\s*[\.、]\s*)?Question\s*#?\s*(\d+)\s*", re.I)
_EN_NEW_BLOCK = re.compile(
    r"^\s*(?:\d+\s*[\.、]\s*)?Question\s*#?\s*\d+"
    r"|^\s*[A-E]\s*[\.)]\s"
    r"|^\s*(?:Correct\s+)?Answer\s*[:：]"
    r"|^\s*Explanation\s*[:：]",
    re.I)
_EN_MULTI = re.compile(r"\(\s*choose\s+(two|three|four)", re.I)
_EN_N = {"two": 2, "three": 3, "four": 4}


def load_sapc02_en(path: str | Path) -> list[Question]:
    """英文版题库原文。同样只取题干与选项。"""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    out: list[Question] = []
    for it in parse(text, split=_EN_SPLIT, new_block=_EN_NEW_BLOCK, cjk=False):
        m = _EN_MULTI.search(it["stem"])
        n = _EN_N.get(m.group(1).lower(), 2) if m else 1
        raw_q = f"Question #{it['no']} {it['stem']}\n" + "\n".join(
            f"{k}. {v}" for k, v in sorted(it["options"].items()))
        q = build_question(qid=f"sap-en-{it['no']}", raw_q=raw_q, raw_a="",
                           title=f"AWS SAP-C02 #{it['no']} (EN)", path=str(path))
        q.options = dict(it["options"])
        q.option_texts = dict(it["options"])
        q.answer_text = " ".join(v for _, v in sorted(it["options"].items()))
        q.source_no = it["no"]
        q.stem_truncated = False
        q.select_n = n
        q.qtype = "multi" if n > 1 else "single"
        out.append(q)
    return out
