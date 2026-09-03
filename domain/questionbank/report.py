"""查重报告。

三份产出：
  1. 去重后的独立考题集（JSON）—— 复习时按这个刷
  2. 同一考点的不同问法（Markdown）—— **这才是重点**：
     不是把重复题删掉就完了，而是把「同一个考点被换了几种问法」摆出来，
     免得考试时换个说法就不认识。
  3. 全量明细（JSON）—— 可复核，可回归
"""
from __future__ import annotations

import json
from pathlib import Path

from .dedup import DedupResult
from .loader import answer_disagreements

LEVEL_LABEL = {
    "DUPLICATE": "重复",
    "LIKELY_DUPLICATE": "疑似重复",
    "SAME_TOPIC": "同考点不同题",
    "RELATED": "相关",
    "DISTINCT": "无关",
}


def _short(text: str, n: int = 80) -> str:
    t = " ".join((text or "").split())
    return t[:n] + ("…" if len(t) > n else "")


def write_markdown(res: DedupResult, path: Path) -> None:
    qs = res.questions
    dups = res.duplicate_clusters
    covered = sum(c.size for c in dups)
    lines: list[str] = []
    w = lines.append

    w("# 题库查重报告")
    w("")
    w("> 本文件由 `python -m scripts.dedup_questions` 自动生成，不要手工编辑。")
    w("")
    w("## 概况")
    w("")
    w("| 项 | 值 |")
    w("|---|---|")
    w(f"| 题目总数 | {len(qs)} |")
    w(f"| 去重后独立考题 | **{len(res.kept)}** |")
    w(f"| 可合并的重复 | {len(dups)} 组，{covered - len(dups)} 题可删 |")
    w(f"| 同考点不同问法（**保留**） | {len(res.topic_pairs)} 对 |")
    w(f"| 合并阈值 | {res.threshold:.0f} 分 |")
    w(f"| 向量模型 | `{res.embedding_model}` |")
    w("")

    if dups:
        sizes = {}
        for c in dups:
            sizes[c.size] = sizes.get(c.size, 0) + 1
        w("### 等价类大小分布")
        w("")
        w("| 一个考点有几种问法 | 组数 |")
        w("|---|---|")
        for k in sorted(sizes):
            w(f"| {k} 种 | {sizes[k]} |")
        w("")

    w("## 一、可合并的重复（题干与选项几乎逐字相同）")
    w("")
    w(f"门槛 {res.threshold:.0f} 分。这个阈值是实测校准的，不是拍的：")
    w("在 524 道真题上人工核验过，82~92 分那一段里近一半是「同主题但不同考点」，")
    w("其中 #320（加密**新建**的 EBS 卷）vs #370（加密**已有**的卷）如果被合并，")
    w("等于删掉一个完整考点。**宁可漏合，也不能合错。**")
    w("")
    if not dups:
        w("没有达到合并门槛的题组。")
        w("")
    else:
        for idx, c in enumerate(dups, 1):
            rep = qs[c.representative]
            w(f"### {idx}. {_short(rep.title or rep.stem, 60)}")
            w("")
            w(f"- **代表题**：`{c.representative}`"
              + (f" · 真题 #{rep.source_no}" if rep.source_no else ""))
            w(f"- **共 {c.size} 种问法** · {c.reason}")
            if rep.services:
                w(f"- 涉及服务：{', '.join(sorted(rep.services))}")
            if rep.constraints:
                w(f"- 优化目标：{', '.join(sorted(rep.constraints))}")
            w("")
            w("| | 题号 | 答案 | 问法 |")
            w("|---|---|---|---|")
            for m in c.members:
                q = qs[m]
                mark = "★" if m == c.representative else " "
                no = f"#{q.source_no}" if q.source_no else m
                w(f"| {mark} | `{no}` | {''.join(q.answer) or '-'} | {_short(q.stem, 92)} |")
            w("")
            if c.pairs:
                p = c.pairs[0]
                dims = ", ".join(f"{k} {v:.0f}" for k, v in p.dims.items()
                                 if v is not None)
                w(f"<sub>最高相似对 `{p.a}` ↔ `{p.b}`：{p.score:.1f} 分（{dims}）</sub>")
                w("")

    # ---- 同考点不同问法：复习清单 ----
    w("## 二、同一考点的不同问法（**保留，不要删**）")
    w("")
    w("**这是本报告最该看的部分。**")
    w("下面每一对考的是同一片知识，但换了场景、换了措辞——")
    w("考场上任何一种问法都可能出现。把两边都看一遍，确认换个说法仍然认得，")
    w("而不是只记住了某一种问法的字面。")
    w("")
    if not res.topic_pairs:
        w("没有找到这一档的题对。")
        w("")
    else:
        for idx, p in enumerate(res.topic_pairs, 1):
            qa, qb = qs[p.a], qs[p.b]
            dims = ", ".join(f"{k} {v:.0f}" for k, v in p.dims.items() if v is not None)
            shared = sorted(qa.services & qb.services)
            w(f"### {idx}. #{qa.source_no} ↔ #{qb.source_no} · {p.score:.1f} 分")
            w("")
            if shared:
                w(f"- 共同服务：{', '.join(shared)}")
            if qa.constraints & qb.constraints:
                w(f"- 共同优化目标：{', '.join(sorted(qa.constraints & qb.constraints))}")
            w(f"- 维度：{dims}")
            w("")
            w("| 题号 | 选几项 | 问法 |")
            w("|---|---|---|")
            for q in (qa, qb):
                w(f"| `#{q.source_no}` | {q.select_n} | {_short(q.stem, 150)} |")
            w("")

    # 高分但被硬约束拦下的：最值得人工看一眼
    blocked = [p for p in res.pairs
               if p.level in ("SAME_TOPIC", "RELATED") and p.penalty > 0][:20]
    if blocked:
        w("## 看着像、但判定为不同题")
        w("")
        w("这些题对语义高度接近，被硬约束拦下了。**如果这里出现误拦，说明规则要调**；")
        w("如果拦得对，说明它们正是「换汤不换药看似一样、其实考点不同」的陷阱题。")
        w("")
        w("| 题 A | 题 B | 分数 | 判定 | 拦截原因 |")
        w("|---|---|---|---|---|")
        for p in blocked:
            why = "；".join(f"{c['field_name']}({c['severity']})" for c in p.conflicts)
            w(f"| `{p.a}` | `{p.b}` | {p.score:.1f} | {LEVEL_LABEL.get(p.level, p.level)} | {why} |")
        w("")

    dis = answer_disagreements(qs.values())
    if dis:
        w("## 附：答案标注分歧")
        w("")
        w(f"{len(dis)} / {len(qs)} 道题里，上游原始标注与逐选项分析的结论不一致，")
        w("而且两行会同时渲染给学习者。**这与查重无关，但比重复题更该先修。**")
        w("")
        w("| 题目 | 上游标注 | 分析结论 |")
        w("|---|---|---|")
        for q in dis[:25]:
            w(f"| `{q.qid}` | {''.join(q.answer_header)} | {''.join(q.answer)} |")
        if len(dis) > 25:
            w(f"| … | 共 {len(dis)} 条 | |")
        w("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def write_json(res: DedupResult, kept_path: Path, detail_path: Path) -> None:
    qs = res.questions
    kept_path.parent.mkdir(parents=True, exist_ok=True)

    kept_path.write_text(json.dumps({
        "total": len(qs),
        "kept": len(res.kept),
        "threshold": res.threshold,
        "embedding_model": res.embedding_model,
        "questions": [
            {
                "qid": q,
                "source_no": qs[q].source_no,
                "title": qs[q].title,
                "answer": list(qs[q].answer),
                "services": sorted(qs[q].services),
                "constraints": sorted(qs[q].constraints),
            }
            for q in res.kept
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    detail_path.write_text(json.dumps({
        "threshold": res.threshold,
        "embedding_model": res.embedding_model,
        "clusters": [
            {
                "representative": c.representative,
                "size": c.size,
                "members": [
                    {"qid": m, "source_no": qs[m].source_no,
                     "answer": list(qs[m].answer), "stem": qs[m].stem}
                    for m in c.members
                ],
                "top_pairs": [
                    {"a": p.a, "b": p.b, "score": p.score, "level": p.level,
                     "dims": p.dims}
                    for p in c.pairs[:5]
                ],
            }
            for c in res.duplicate_clusters
        ],
        # 同考点不同问法：下游的覆盖优化要靠它强制保留这些配对
        "topic_pairs": [
            {"a": p.a, "b": p.b, "score": p.score, "level": p.level, "dims": p.dims}
            for p in res.topic_pairs
        ],
        "pairs": [
            {"a": p.a, "b": p.b, "score": p.score, "level": p.level,
             "penalty": p.penalty, "dims": p.dims, "conflicts": p.conflicts}
            for p in res.pairs[:400]
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
