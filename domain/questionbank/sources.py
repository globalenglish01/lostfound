"""完整题目的载入器。

只收**题干完整且带完整选项**的题。理由是实测出来的：

课程 JSON 里的 560 道题，题干被硬截断到 140 字（483 道恰好触顶，82% 断在句中），
且一道题都没有保存选项。上游的 `optimal_study_set.json` 同样截断（120 字）。
拿半截题干做判重，等于拿被随机裁掉后半段的文本比相似度——
前半段模板化程度极高（「一家公司在 AWS 上运行……」），会把大量无关题判成一样。

**宁可只用 86 道完整的，也不要 560 道残缺的。**
残缺数据算出来的高相似度是假的，而且假得很有说服力。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .loader import Question, build_question

# disagreements.md 的块结构：
#   ## 题 #2 · 答案 **C** · status=...
#   > 完整题干……
#   - **A.** 选项正文
_BLOCK = re.compile(r"\n##\s+题\s*#")
_STEM = re.compile(r"\n>\s*(.+?)\n")
_OPTION = re.compile(r"^-\s*\*\*([A-E])\.\*\*\s*(.+)$", re.M)
_KPS = re.compile(r"\*\*题目自带 KPs\*\*[：:]\s*(.+)")


def _mk(qid: str, no: str, stem: str, options: dict[str, str],
        kps=None, multi: bool | None = None, source: str = "") -> Question:
    """只用题干 + 选项建题；答案与解析一律不带入。"""
    raw = f"问题 #{no} {stem}\n" + "\n".join(
        f"{k}. {v}" for k, v in sorted(options.items()))
    q = build_question(qid=qid, raw_q=raw, raw_a="", kps=kps or [],
                       title=f"AWS SAP 真题 #{no}", path=source)
    q.options = dict(options)
    q.option_texts = dict(options)
    # 全部选项拼起来当作「这道题在考什么做法」的表述。
    # 有完整选项时这比「只看正确选项」更好：不依赖答案，而答案本身有 46% 的标注分歧。
    q.answer_text = " ".join(v for _, v in sorted(options.items()))
    if multi is not None:
        q.qtype = "multi" if multi else "single"
    q.source_no = no
    q.stem_truncated = False
    return q


def load_generated(root: str | Path) -> list[Question]:
    """content/aws_sap/generated_questions/*.json"""
    out: list[Question] = []
    for f in sorted(Path(root).glob("*.json")):
        try:
            o = json.loads(f.read_text(encoding="utf-8"))
        except Exception:                                   # noqa: BLE001
            continue
        opts = o.get("options") or {}
        stem = o.get("question") or ""
        no = str(o.get("question_number") or f.stem)
        if len(opts) < 3 or not stem.strip():
            continue
        out.append(_mk(f"q{no}", no, re.sub(r"^\s*问题\s*#\s*\d+\s*", "", stem),
                       {k: str(v) for k, v in opts.items()},
                       kps=o.get("knowledge_points"),
                       multi=bool(o.get("multi_select")),
                       source=str(f)))
    return out


def load_disagreements(path: str | Path) -> list[Question]:
    """content/aws_sap/disagreements.md —— 待审题里保留了完整原文。"""
    text = Path(path).read_text(encoding="utf-8")
    out: list[Question] = []
    for block in _BLOCK.split(text)[1:]:
        m = re.match(r"(\d+)", block)
        if not m:
            continue
        no = m.group(1)
        stem = _STEM.search(block)
        opts = dict(_OPTION.findall(block))
        if not stem or len(opts) < 3:
            continue
        kps_m = _KPS.search(block)
        kps = [k.strip() for k in re.split(r"[、,，]", kps_m.group(1))] if kps_m else []
        out.append(_mk(f"q{no}", no, stem.group(1).strip(),
                       {k: v.strip() for k, v in opts.items()},
                       kps=kps, source=str(path)))
    return out


def load_complete(aws_sap_dir: str | Path) -> list[Question]:
    """把两个完整来源合起来，按题号去重（题号相同即同一道真题）。"""
    root = Path(aws_sap_dir)
    found: dict[str, Question] = {}
    for q in load_generated(root / "generated_questions"):
        found.setdefault(q.source_no or q.qid, q)
    dis = root / "disagreements.md"
    if dis.exists():
        for q in load_disagreements(dis):
            found.setdefault(q.source_no or q.qid, q)
    return [found[k] for k in sorted(found, key=lambda x: int(x) if x.isdigit() else 0)]
