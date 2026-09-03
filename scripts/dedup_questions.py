"""题库查重 CLI。

同一个匹配引擎的第二个领域：把「这两条记录是不是同一件物品」换成
「这两道题是不是同一个考点」。评分公式、可用证据归一化、冲突封顶全部复用，
换掉的只是维度、权重和硬约束。

    # StudyAthena 的 AWS SAP 课程 JSON
    python -m scripts.dedup_questions --studyathena D:/My/StudyAthena/content/lessons_story/zh

    # 通用 JSONL
    python -m scripts.dedup_questions --jsonl questions.jsonl
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from domain.questionbank.dedup import DEFAULT_THRESHOLD, dedup          # noqa: E402
from domain.questionbank.loader import (                                # noqa: E402
    answer_disagreements,
    load_jsonl,
    load_studyathena,
)
from domain.questionbank.report import write_json, write_markdown       # noqa: E402
from domain.questionbank.sapc02 import load_sapc02                     # noqa: E402
from domain.questionbank.sources import load_complete                  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--studyathena", help="StudyAthena 课程 JSON 目录")
    src.add_argument("--jsonl", help="通用 JSONL 题库")
    src.add_argument("--aws-sap", help="content/aws_sap 目录：只收题干与选项都完整的题")
    src.add_argument("--sapc02", help="SAP-C02 题库原文 txt（PDF 转出），唯一保留完整题干+选项的来源")
    ap.add_argument("--pattern", default="aws-sap-*.json")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--top-k", type=int, default=8, help="向量 kNN 的每题候选数")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 题（调试用）")
    ap.add_argument("--out-dir", default=str(ROOT / "docs"))
    args = ap.parse_args()

    started = time.perf_counter()
    if args.studyathena:
        qs = load_studyathena(args.studyathena, args.pattern)
    elif args.aws_sap:
        qs = load_complete(args.aws_sap)
    elif args.sapc02:
        qs = load_sapc02(args.sapc02)
    else:
        qs = load_jsonl(args.jsonl)
    if args.limit:
        qs = qs[: args.limit]
    if not qs:
        print("没有载入到任何题目")
        return

    with_answer = sum(1 for q in qs if q.answer)
    with_options = sum(1 for q in qs if q.options)
    trunc = sum(1 for q in qs if q.stem_truncated)
    print(f"载入 {len(qs)} 题（有选项 {with_options}，有答案 {with_answer}，题干截断 {trunc}）")
    if trunc:
        print(f"⚠ {trunc} 道题的题干被上游截断，判重结果对这些题不可信")
    dis = answer_disagreements(qs)
    if dis:
        print(f"⚠ 答案标注分歧 {len(dis)} 题（与查重无关，但会同时显示给学习者）")

    res = dedup(qs, threshold=args.threshold, top_k=args.top_k,
                progress=lambda m: print(m, flush=True))

    out = Path(args.out_dir)
    write_markdown(res, out / "QUESTION_DEDUP.md")
    write_json(res, out / "question_kept.json", out / "question_dedup.json")

    dups = res.duplicate_clusters
    elapsed = time.perf_counter() - started
    print()
    print("=" * 58)
    print(f"  题目总数        {len(qs)}")
    print(f"  去重后独立考题  {len(res.kept)}")
    print(f"  等价类          {len(dups)} 组，覆盖 {sum(c.size for c in dups)} 题")
    print(f"  耗时            {elapsed:.1f}s")
    print("=" * 58)
    for c in dups[:8]:
        rep = res.questions[c.representative]
        title = (rep.title or rep.stem)[:46]
        print(f"  {c.size} 种问法 · {c.representative} · {title}")
    print()
    print(f"报告：{out / 'QUESTION_DEDUP.md'}")
    print(f"独立题集：{out / 'question_kept.json'}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
