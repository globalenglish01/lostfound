"""中英文题库跨语言对齐。

把「中文版 #4」和「English #4」配起来——但**不看题号**，只看内容。
题号本身是 ground truth，所以这既是一个实用工具，也是一份带标准答案的
跨语言检索评测：523 道题在中英文两版都存在，配对正确率可以直接算出来。

这正好复用了失物系统里那条能力：
    「黑色双肩包」和「黒いリュックサック」是同一个包
    「一家公司在本地运行两层 Web 应用」和 "A company is running a two-tier
     web-based application on-premises" 是同一道题

    python -m scripts.align_bilingual \\
        --zh data/SAPC02_Chinese.txt --en data/SAPC02_English.txt
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from app.ai.embedding_provider import cosine, get_embedding_provider   # noqa: E402
from app.matching.scoring import compute_score                         # noqa: E402
from domain.questionbank import features as F                          # noqa: E402
from domain.questionbank.sapc02 import load_sapc02, load_sapc02_en     # noqa: E402


def embed_all(texts: list[str], label: str, say) -> list[list[float]]:
    p = get_embedding_provider()
    out = []
    for i, t in enumerate(texts, 1):
        out.append(p.embed(t, kind="passage"))
        if i % 100 == 0:
            say(f"  {label} {i}/{len(texts)}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zh", required=True)
    ap.add_argument("--en", required=True)
    ap.add_argument("--top-k", type=int, default=20, help="向量召回多少条交给重排")
    ap.add_argument("--out-dir", default=str(ROOT / "docs"))
    args = ap.parse_args()
    say = lambda m: print(m, flush=True)                      # noqa: E731

    started = time.perf_counter()
    zh = load_sapc02(args.zh)
    en = load_sapc02_en(args.en)
    say(f"中文 {len(zh)} 题，英文 {len(en)} 题")

    en_by_no = {q.source_no: q for q in en}
    pairs = [(q, en_by_no[q.source_no]) for q in zh if q.source_no in en_by_no]
    say(f"两版都存在的题号 {len(pairs)}（作为 ground truth）")

    say("生成向量 ...")
    # 题干 + 全部选项一起编码：只用题干时，中英文的「一家公司……」模板句
    # 会让所有题彼此都很像，选项才是真正的指纹。
    zh_txt = [f"{q.stem} {q.answer_text}"[:1200] for q, _ in pairs]
    en_txt = [f"{q.stem} {q.answer_text}"[:1200] for _, q in pairs]
    zv = embed_all(zh_txt, "中文", say)
    ev = embed_all(en_txt, "英文", say)

    say("跨语言检索 + 结构化重排 ...")
    rows = []
    n = len(pairs)
    cand_scores: dict[int, list[tuple[float, int]]] = {}
    for i in range(n):
        sims = sorted(((cosine(zv[i], ev[j]), j) for j in range(n)), reverse=True)
        rank_vec = next((r + 1 for r, (_, j) in enumerate(sims) if j == i), None)

        # 重排：向量负责把正确答案捞进 TopK，结构化属性负责在 TopK 里挑对。
        # 服务名、优化目标、要求选几项——这些经过归一化之后是**语言无关**的，
        # 中文题抽出 {S3, IAM}，英文题也抽出 {S3, IAM}。
        cand = sims[: args.top_k]
        reranked = []
        for vs, j in cand:
            feats = F.build_features(pairs[i][0], pairs[j][1],
                                     stem_cosine=vs, answer_cosine=vs,
                                     keyword_sim=None)
            report = F.detect_conflicts(pairs[i][0], pairs[j][1])
            res = compute_score(feats, report, weights=F.WEIGHTS,
                                dimensions=F.DIMENSIONS, level_config=F.LEVEL_CONFIG)
            reranked.append((res.final_score, j))
        reranked.sort(reverse=True)
        cand_scores[i] = reranked
        rank = next((r + 1 for r, (_, j) in enumerate(reranked) if j == i), None)

        rows.append({
            "no": pairs[i][0].source_no,
            "rank": rank,
            "rank_vector_only": rank_vec,
            "score": round(sims[0][0], 4),
            "self_score": round(next(s for s, j in sims if j == i), 4),
            "top1_no": pairs[reranked[0][1]][0].source_no,
        })
        if (i + 1) % 100 == 0:
            say(f"  {i + 1}/{n}")

    # ---- 全局指派 ----
    # 前面每道题各自取 argmax，是把一个**一一对应**问题当成了 523 次独立检索。
    # 「每道中文题只能配一道英文题」是很强的约束，不用等于白扔：
    # 一道英文题被两道中文题抢时，让分高的那道拿走，另一道去找自己的次优。
    # 贪心指派是匈牙利算法的近似，O(n² log n)，在这个规模上足够。
    say("全局一一指派 ...")
    flat = sorted(((sc, i, j) for i, lst in cand_scores.items() for sc, j in lst),
                  reverse=True)
    taken_zh: set[int] = set()
    taken_en: set[int] = set()
    assign: dict[int, int] = {}
    for sc, i, j in flat:
        if i in taken_zh or j in taken_en:
            continue
        assign[i] = j
        taken_zh.add(i)
        taken_en.add(j)
    for r, i in zip(rows, range(n)):
        r["assigned_ok"] = assign.get(i) == i
        r["assigned_to"] = pairs[assign[i]][0].source_no if i in assign else None
    assigned_acc = sum(1 for r in rows if r["assigned_ok"]) / n

    at = lambda k: sum(1 for r in rows if r["rank"] and r["rank"] <= k) / n     # noqa: E731
    at_v = lambda k: sum(1 for r in rows                                        # noqa: E731
                         if r["rank_vector_only"] and r["rank_vector_only"] <= k) / n
    elapsed = time.perf_counter() - started
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    misses = [r for r in rows if not r["assigned_ok"]]
    lines = [
        "# 中英文题库跨语言对齐",
        "",
        "> 由 `python -m scripts.align_bilingual` 自动生成。",
        "",
        "把中文版和英文版的同一道题配起来，**过程中不看题号**——题号只用来算对错。",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| 中文题数 | {len(zh)} |",
        f"| 英文题数 | {len(en)} |",
        f"| 两版都有（ground truth） | {n} |",
        f"| 仅向量 Top1 | {at_v(1):.1%} |",
        f"| **向量 + 结构化重排 Top1** | **{at(1):.1%}** |",
        f"| Top3（重排后） | {at(3):.1%} |",
        f"| Top10（重排后） | {at(10):.1%} |",
        f"| 仅向量 Top{args.top_k}（重排的召回上限） | {at_v(args.top_k):.1%} |",
        f"| **全局一一指派准确率** | **{assigned_acc:.1%}** |",
        f"| 向量模型 | `{get_embedding_provider().model}` |",
        f"| 耗时 | {elapsed:.1f}s |",
        "",
    ]
    if misses:
        lines += ["## 未在第 1 位配对的题", "",
                  "| 题号 | 重排后排名 | 与自身的相似度 | 指派给了 |", "|---|---|---|---|"]
        for r in misses[:40]:
            lines.append(f"| #{r['no']} | {r['rank'] or '>K'} | {r['self_score']} "
                         f"| #{r['assigned_to'] or '-'} |")
        if len(misses) > 40:
            lines.append(f"| … | 共 {len(misses)} 条 | | |")
        lines.append("")
    (out / "BILINGUAL_ALIGN.md").write_text("\n".join(lines), encoding="utf-8")

    (out / "bilingual_align.json").write_text(json.dumps({
        "zh_total": len(zh), "en_total": len(en), "pairs": n,
        "recall@1": at(1), "recall@3": at(3), "recall@10": at(10),
        "recall@1_vector_only": at_v(1), "rerank_top_k": args.top_k,
        "assignment_accuracy": assigned_acc,
        "embedding_model": get_embedding_provider().model,
        "results": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print("=" * 52)
    print(f"  配对题数   {n}")
    print(f"  仅向量 Top1      {at_v(1):.1%}")
    print(f"  + 结构化重排 Top1 {at(1):.1%}")
    print(f"  Top3             {at(3):.1%}")
    print(f"  Top10            {at(10):.1%}")
    print(f"  + 全局一一指派    {assigned_acc:.1%}")
    print("=" * 52)
    print(f"报告：{out / 'BILINGUAL_ALIGN.md'}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
