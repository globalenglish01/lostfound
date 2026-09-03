"""题库载入。

支持两种来源：
  - StudyAthena 的 AWS SAP 课程 JSON（每个文件包一道真题）
  - 通用 JSONL（{"id","stem","options","answer","kps"} 每行一条）

载入时只做**抽取与归一**，不做任何判断——判重是下游的事。
原始题干一律保留在 `raw`，永远不被覆盖（与失物系统的 raw_description 同一条纪律）。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .normalize import (
    canonical_kps,
    extract_constraints,
    extract_services,
    split_question,
    strip_boilerplate,
)

_ANSWER_CHECKED = re.compile(r"✅\s*正确答案\s*[：:]\s*([A-E](?:\s*[,，、]\s*[A-E])*)")
# 真实数据里 `q` 字段被硬截断到 140 字且不含选项；
# 选项只以「逐选项分析」的形式活在 `a` 字段里：
#     **A.** ❌ 错误
#     > 分析：……
# 那段分析就是「这个选项在做什么」的最佳可得表述。
_OPTION_ANALYSIS = re.compile(
    r"\*\*\s*([A-E])\s*[\.．]\s*\*\*[^\n]*\n+"      # 选项标题行：**A.** ❌ 错误
    r"((?:[ \t]*>[^\n]*\n?)+)"                      # 紧随其后的引用块就是分析正文
)
_RATIONALE = re.compile(r"\*\*解题思路[：:]?\*\*\s*(.+?)(?=\*\*逐选项分析|\Z)", re.S)
_ANSWER_HEADER = re.compile(r"\*\*正确答案\s*[：:]\s*([A-E](?:\s*[,，、]\s*[A-E])*)\s*\*\*")
_SOURCE_NO = re.compile(r"问题\s*#\s*(\d+)")


@dataclass
class Question:
    qid: str
    raw: str                                  # 原始题目文本，永不覆盖
    stem: str                                 # 题干（去掉选项与通用设问）
    options: dict[str, str] = field(default_factory=dict)
    answer: tuple[str, ...] = ()              # 正确选项字母（以 ✅ 为准）
    answer_header: tuple[str, ...] = ()       # 上游原始标注，仅用于报告分歧
    answer_text: str = ""                     # 正确选项在做什么——判重的关键特征
    rationale: str = ""                       # 解题思路：题干被截断后，靠它补回考点信息
    option_texts: dict[str, str] = field(default_factory=dict)
    stem_truncated: bool = False
    kps: set[str] = field(default_factory=set)
    services: set[str] = field(default_factory=set)
    constraints: set[str] = field(default_factory=set)
    qtype: str = "single"                     # single / multi
    select_n: int = 1                         # 要求选几项（题干里的「选择两个」）
    source_no: str | None = None
    title: str = ""
    path: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        for k in ("kps", "services", "constraints"):
            d[k] = sorted(d[k])
        d["answer"] = list(self.answer)
        d["answer_header"] = list(self.answer_header)
        return d


def _letters(m: re.Match | None) -> tuple[str, ...]:
    if not m:
        return ()
    return tuple(sorted(set(re.findall(r"[A-E]", m.group(1)))))


def parse_option_analyses(raw_a: str) -> dict[str, str]:
    """从答案解析里抽出每个选项的分析文本。"""
    out: dict[str, str] = {}
    for m in _OPTION_ANALYSIS.finditer(raw_a or ""):
        letter = m.group(1)
        body = re.sub(r"^\s*>\s*", "", m.group(2) or "", flags=re.M)
        body = re.sub(r"^\s*分析\s*[：:]\s*", "", body.strip())
        body = re.sub(r"\s+", " ", body).strip()
        if body and letter not in out:
            out[letter] = body
    return out


def build_question(*, qid: str, raw_q: str, raw_a: str = "", kps=None,
                   title: str = "", path: str = "") -> Question:
    stem_full, options = split_question(raw_q)
    answer = _letters(_ANSWER_CHECKED.search(raw_a))
    header = _letters(_ANSWER_HEADER.search(raw_a))
    if not answer:
        answer = header

    # 选项优先取题干里的；题干没有（真实数据的常态）就退到答案里的逐选项分析
    if not options:
        options = parse_option_analyses(raw_a)
    rm = _RATIONALE.search(raw_a or "")
    rationale = re.sub(r"\s+", " ", rm.group(1)).strip()[:600] if rm else ""

    answer_text = " ".join(options.get(a, "") for a in answer).strip()
    kp_set = canonical_kps(kps)
    stem = strip_boilerplate(stem_full)

    # 服务与约束要从**题干 + 全部选项 + 解题思路 + 知识点**一起抽。
    #
    # 只看题干会漏掉大量信息：实测 524 道题里有 92 道题干完全不点服务名，
    # 服务只出现在选项里（「A. 使用 Amazon EC2 Auto Scaling…」）。
    # 漏掉选项 = 19% 的题服务集合为空 = 这些题在判重时少了一整个维度。
    #
    # 解题思路同样要算：题干被上游截断时，「最小停机时间」这类关键约束
    # 往往正好落在被切掉的那一截里。
    options_text = " ".join(options.values())
    services = extract_services(stem_full, options_text, rationale, " ".join(kp_set))
    constraints = extract_constraints(stem_full, options_text, rationale, " ".join(kp_set))

    no = _SOURCE_NO.search(raw_q or "")
    return Question(
        qid=qid,
        raw=raw_q or "",
        stem=stem,
        options=options,
        answer=answer,
        answer_header=header,
        answer_text=answer_text,
        kps=kp_set,
        services=services,
        constraints=constraints,
        rationale=rationale,
        option_texts=options,
        stem_truncated=bool(stem_full) and stem_full.rstrip()[-1:] not in "。？?！!）)”\"",
        qtype="multi" if len(answer) > 1 else "single",
        # 「要求选几项」优先看题干里的「（选择两个）」——那是最可靠的信号；
        # 题干没写时退回答案字母数。两个信号都要，缺一个就会漏掉这条硬约束。
        select_n=max(1, len(answer)),
        source_no=no.group(1) if no else None,
        title=title,
        path=path,
    )


def load_studyathena(root: str | Path, pattern: str = "aws-sap-*.json") -> list[Question]:
    """载入 StudyAthena 的课程 JSON。"""
    root = Path(root)
    out: list[Question] = []
    for f in sorted(root.glob(pattern)):
        try:
            o = json.loads(f.read_text(encoding="utf-8"))
        except Exception:                                   # noqa: BLE001
            continue
        iqs = o.get("interview_questions") or []
        if not iqs:
            continue
        iq = iqs[0]
        q = build_question(
            qid=o.get("slug") or f.stem,
            raw_q=iq.get("q") or "",
            raw_a=iq.get("a") or "",
            kps=o.get("knowledge_points"),
            title=o.get("title") or "",
            path=str(f),
        )
        if q.stem.strip():
            out.append(q)
    return out


def load_jsonl(path: str | Path) -> list[Question]:
    """通用格式：每行 {"id","stem","options"?,"answer"?,"kps"?}。"""
    out: list[Question] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        o = json.loads(line)
        opts = o.get("options") or {}
        raw_q = o.get("stem", "")
        if opts:
            raw_q += "\n" + "\n".join(f"{k}. {v}" for k, v in sorted(opts.items()))
        ans = o.get("answer") or []
        raw_a = f"✅ 正确答案: {', '.join(ans)}" if ans else ""
        out.append(build_question(qid=str(o.get("id")), raw_q=raw_q, raw_a=raw_a,
                                  kps=o.get("kps"), title=o.get("title", "")))
    return out


def answer_disagreements(questions: Iterable[Question]) -> list[Question]:
    """两个答案字段互相矛盾的题（上游标注 vs 逐选项分析结论）。"""
    return [q for q in questions
            if q.answer and q.answer_header and q.answer != q.answer_header]
