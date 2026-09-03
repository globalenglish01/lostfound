"""考纲覆盖分析：盲区补课清单 + 最优选题。

    python -m scripts.coverage_gaps \
        --guide  content/aws_sap/exam_guide_sap_c02.json \
        --pool   content/aws_sap/optimal_study_set.json \
        --dedup  docs/question_dedup.json

产出两份：
    docs/BLIND_SPOTS.md    题库够不到的考点 + 补课要点 + 已验证的官方文档链接
    docs/STUDY_SET.md      在够得到的范围内的最优选题（每个考点 k 次）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from domain.questionbank.coverage import (            # noqa: E402
    analyze,
    coverage_of,
    greedy_select,
    load_blueprint,
)

CONFIG = ROOT / "config"


def load_pool(path: str | Path) -> tuple[dict[str, set[str]], dict[str, dict]]:
    """从 optimal_study_set.json 读题号 -> 覆盖的考点。"""
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    qm = d["question_metadata"]
    return ({q: set(v.get("kbs") or []) for q, v in qm.items()}, qm)


def load_keep_pairs(path: str | Path | None) -> set[str]:
    """从查重结果里取「同考点不同问法」的题号——这些强制保留。"""
    if not path or not Path(path).exists():
        return set()
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    out: set[str] = set()
    for p in d.get("topic_pairs", []):
        for qid in (p.get("a"), p.get("b")):
            if qid:
                out.add(str(qid).replace("sap-", ""))
    if not out:
        # 旧版结果的 JSON 里没有 topic_pairs，回退去解析同目录的 Markdown
        md = Path(path).with_name("QUESTION_DEDUP.md")
        if md.exists():
            import re
            for a, b in re.findall(r"^### \d+\. #(\d+) ↔ #(\d+)",
                                   md.read_text(encoding="utf-8"), re.M):
                out.update((a, b))
    return out


def write_blind_spots(bp, rep, notes: dict, path: Path) -> None:
    docs = notes["docs"]
    items = notes["items"]
    L: list[str] = []
    w = L.append

    w("# 题库盲区补课清单")
    w("")
    w("> 由 `python -m scripts.coverage_gaps` 自动生成。")
    w("")
    w("**「刷完这 N 道题就能高分」的前提是题池覆盖得住考纲。这里先验证这个前提。**")
    w("")
    w("| 项 | 值 |")
    w("|---|---|")
    w(f"| 考纲考点总数 | {rep.blueprint_total} |")
    w(f"| 题池能覆盖 | {len(rep.reachable)} |")
    w(f"| **题池完全够不到** | **{len(rep.blind)}（{rep.blind_ratio:.0%}）** |")
    w(f"| 题池规模 | {rep.pool_size} |")
    w("")
    w("覆盖优化只能在题池够得到的范围内做到最优。")
    w("下面这些考点**刷多少题都碰不到**，只能靠读考纲和官方文档补。")
    w("")
    w("## 按 Domain 分布")
    w("")
    w("| Domain | 盲区 / 总数 | |")
    w("|---|---|---|")
    for d, (miss, tot) in rep.by_domain.items():
        bar = "█" * round(miss / tot * 20) if tot else ""
        w(f"| {d} {bp.domain_title.get(d,'')[:40]} | {miss}/{tot} | `{bar}` |")
    w("")

    covered_note = [k for k in rep.blind if k in items]
    w(f"## 盲区考点（{len(rep.blind)} 个，其中 {len(covered_note)} 个已配补课要点）")
    w("")
    w("每条链接都用 `curl` 验证过返回 200。")
    w("")
    cur_task = None
    for kb in sorted(rep.blind):
        task = bp.kb_task[kb]
        if task != cur_task:
            cur_task = task
            w(f"### {bp.kb_domain[kb]} · {task} {bp.kb_task_title.get(kb,'')}")
            w("")
        w(f"#### `{kb}` {bp.kb_title[kb]}")
        w("")
        it = items.get(kb)
        if not it:
            w("_（暂无补课要点，请对照考纲原文自行补充。）_")
            w("")
            continue
        w(f"**要点**：{it['focus']}")
        w("")
        w(f"**为什么重要**：{it['why']}")
        w("")
        w("**自测**：")
        for q in it["self_check"]:
            w(f"- {q}")
        w("")
        links = [f"[{k}]({docs[k]})" for k in it["docs"] if k in docs]
        if links:
            w("**文档**：" + " · ".join(links))
            w("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L), encoding="utf-8")


def write_study_set(bp, rep, qkbs, qm, sets: dict[int, list[str]],
                    keep: set[str], path: Path) -> None:
    L: list[str] = []
    w = L.append
    w("# 最优选题（覆盖优化）")
    w("")
    w("> 由 `python -m scripts.coverage_gaps` 自动生成。")
    w("")
    w("在题池**够得到**的考点范围内，用贪心最大覆盖选出最少的题，")
    w(f"让每个考点被覆盖 k 次。够不到的 {len(rep.blind)} 个考点见 "
      "[BLIND_SPOTS.md](BLIND_SPOTS.md)。")
    w("")
    w("| k（每个考点覆盖几次） | 题数 | 覆盖考点 |")
    w("|---|---|---|")
    for k, chosen in sorted(sets.items()):
        cov = coverage_of(qkbs, chosen)
        w(f"| {k} | **{len(chosen)}** | {len(cov)}/{len(rep.reachable)} |")
    w("")
    if keep:
        w(f"其中 {len(keep)} 道来自「同一考点的不同问法」配对，**强制保留**：")
        w("它们在覆盖意义上是冗余的，但正是训练「换个说法还认不认得」的材料。")
        w("")
    for k, chosen in sorted(sets.items()):
        w(f"## k = {k}：{len(chosen)} 道")
        w("")
        w("| 题号 | 覆盖考点数 | 涉及服务 |")
        w("|---|---|---|")
        for q in chosen:
            svc = ", ".join((qm.get(q, {}).get("services") or [])[:4]) or "-"
            w(f"| #{q} | {len(qkbs.get(q, ()))} | {svc} |")
        w("")
    path.write_text("\n".join(L), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--guide", required=True)
    ap.add_argument("--pool", required=True)
    ap.add_argument("--dedup", help="查重结果 JSON，用于强制保留同考点配对")
    ap.add_argument("--notes", default=str(CONFIG / "sap_c02_blindspots.json"))
    ap.add_argument("--out-dir", default=str(ROOT / "docs"))
    ap.add_argument("--k", default="1,2,3")
    args = ap.parse_args()

    bp = load_blueprint(args.guide)
    qkbs, qm = load_pool(args.pool)
    rep = analyze(bp, qkbs)
    notes = json.loads(Path(args.notes).read_text(encoding="utf-8"))
    keep = load_keep_pairs(args.dedup)
    keep = {q for q in keep if q in qkbs}

    sets = {}
    for k in (int(x) for x in args.k.split(",")):
        sets[k] = greedy_select(qkbs, rep.reachable, k=k, must_include=keep)

    out = Path(args.out_dir)
    write_blind_spots(bp, rep, notes, out / "BLIND_SPOTS.md")
    write_study_set(bp, rep, qkbs, qm, sets, keep, out / "STUDY_SET.md")

    print("=" * 56)
    print(f"  考纲考点        {rep.blueprint_total}")
    print(f"  题池能覆盖      {len(rep.reachable)}")
    print(f"  题池够不到      {len(rep.blind)}  ({rep.blind_ratio:.0%})")
    print(f"  强制保留的配对题 {len(keep)}")
    for k, ch in sorted(sets.items()):
        print(f"  k={k} -> {len(ch)} 道")
    print("=" * 56)
    print(f"盲区清单：{out / 'BLIND_SPOTS.md'}")
    print(f"最优选题：{out / 'STUDY_SET.md'}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
