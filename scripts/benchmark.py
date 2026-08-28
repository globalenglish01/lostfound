"""一条命令跑完整评测，产出可复现的报告。

README 里写着 Recall@1 = 96.2%，但读者无法验证——所以这个脚本存在：
清库 → 灌语料 → 灌评测目标 → 跑 53 条对抗查询 → 输出 Markdown + JSON 报告。

    docker compose exec api python -m scripts.benchmark

选项：
    --keep          不清库（在已有数据上追加评测）
    --distractors N 干扰项数量（默认 240）
    --out PATH      报告输出路径（默认 docs/BENCHMARK.md）
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from scripts.eval_synonyms import FOUND_RECORDS, QUERIES        # noqa: E402
from scripts.seed_corpus import build as build_corpus           # noqa: E402

BASE = os.getenv("LF_API_BASE", "http://127.0.0.1:8080")


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _request(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def post(path: str, body: dict) -> dict:
    return _request("POST", path, body)


def get(path: str) -> dict:
    return _request("GET", path)


def wait_for_api(timeout: float = 120.0) -> dict:
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            return get("/health")
        except (urllib.error.URLError, OSError) as exc:       # noqa: PERF203
            last = exc
            time.sleep(2)
    raise RuntimeError(f"API 未就绪（{BASE}）：{last}")


# ---------------------------------------------------------------------------
# 数据准备
# ---------------------------------------------------------------------------

def reset_database() -> None:
    """清空业务数据，保留主数据（类别 / 地点树 / 品牌 / 属性定义）。"""
    from sqlalchemy import text

    from app.db import get_sessionmaker

    with get_sessionmaker()() as s:
        s.execute(text("TRUNCATE item_records CASCADE"))
        s.commit()


def seed(distractors: int) -> dict[str, str]:
    print(f"  灌入 {len(FOUND_RECORDS)} 条评测目标 ...")
    ids = {k: post("/api/found", {**rec, "auto_match": False})["item_id"]
           for k, rec in FOUND_RECORDS.items()}

    rows = build_corpus(distractors)
    print(f"  灌入 {len(rows)} 条同类同色干扰项 ...")
    for i, row in enumerate(rows, 1):
        post("/api/found", row)
        if i % 80 == 0:
            print(f"    ... {i}/{len(rows)}")
    return ids


# ---------------------------------------------------------------------------
# 评测
# ---------------------------------------------------------------------------

def _lang_of(style: str) -> str:
    """ja-hiragana / zh2 / romaji -> ja / zh / romaji（去掉变体后缀和序号）。"""
    import re as _re
    return _re.sub(r"\d+$", "", style.split("-")[0])


def run_queries(ids: dict[str, str], top_k: int = 100) -> list[dict]:
    rows: list[dict] = []
    total = sum(len(v) for v in QUERIES.values())
    done = 0
    for key, queries in QUERIES.items():
        target = ids[key]
        for style, q in queries:
            out = post("/api/search", {"query": q, "type": "FOUND",
                                       "top_k": top_k, "include_low": True})
            results = out["results"]
            hit = next(((i + 1, r) for i, r in enumerate(results)
                        if r["record_id"] == target), None)
            rows.append({
                "item": key,
                "style": style,
                "lang": _lang_of(style),
                "query": q,
                "rank": hit[0] if hit else None,
                "score": hit[1]["match_score"] if hit else None,
                "level": hit[1]["match_level"] if hit else None,
                "channels": hit[1]["retrieval_channels"] if hit else [],
                "understood_category": out["query_understanding"].get("category"),
                "candidates": out["total_candidates"],
            })
            done += 1
            if done % 10 == 0:
                print(f"    ... {done}/{total}")
    return rows


def summarize(rows: list[dict], top_k: int) -> dict:
    n = len(rows)
    ranks = [r["rank"] for r in rows]

    def at(k: int) -> float:
        return sum(1 for x in ranks if x and x <= k) / n

    langs = sorted({r["lang"] for r in rows})
    per_lang = {}
    for lang in langs:
        sub = [r for r in rows if r["lang"] == lang]
        per_lang[lang] = {
            "n": len(sub),
            "recall@1": sum(1 for r in sub if r["rank"] == 1) / len(sub),
            "recall@3": sum(1 for r in sub if r["rank"] and r["rank"] <= 3) / len(sub),
        }

    return {
        "queries": n,
        "recall@1": at(1),
        "recall@3": at(3),
        "recall@10": at(10),
        "mrr": statistics.fmean([1 / r if r else 0.0 for r in ranks]),
        "outside_topk": sum(1 for x in ranks if x is None),
        "top_k": top_k,
        "per_language": per_lang,
        "failures": [r for r in rows if r["rank"] != 1],
    }


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------

LANG_LABEL = {"ja": "日本語", "zh": "中文", "en": "English", "romaji": "ローマ字"}


def write_report(path: Path, env: dict, summary: dict, rows: list[dict],
                 corpus_size: int, elapsed: float) -> None:
    s = summary
    lines: list[str] = []
    a = lines.append

    a("# Benchmark — 同义表达对抗评测")
    a("")
    a("> 本文件由 `python -m scripts.benchmark` 自动生成，不要手工编辑。")
    a("")
    a("同一件物品，只用**一种**写法登记；再用**说法完全不同**的查询去找它。")
    a("覆盖 日 / 中 / 英 × 汉字・平假名・片假名・罗马字・口语・古语。")
    a("")
    a("## 运行环境")
    a("")
    a("| 项 | 值 |")
    a("|---|---|")
    a(f"| 算法版本 | `{env.get('algorithm_version')}` |")
    a(f"| LLM provider | `{env.get('llm_provider')}` |")
    a(f"| Embedding provider | `{env.get('embedding_provider')}` |")
    a(f"| Embedding model | `{env.get('embedding_model', '-')}` |")
    a(f"| 语料规模 | {corpus_size} 条 FOUND 记录（{corpus_size - len(FOUND_RECORDS)} 条同类同色干扰项） |")
    a(f"| 查询数 | {s['queries']} |")
    a(f"| 耗时 | {elapsed:.1f}s |")
    a("")
    a("## 总体结果")
    a("")
    a("| 指标 | 值 |")
    a("|---|---|")
    a(f"| **Recall@1** | **{s['recall@1']:.1%}** |")
    a(f"| Recall@3 | {s['recall@3']:.1%} |")
    a(f"| Recall@10 | {s['recall@10']:.1%} |")
    a(f"| MRR | {s['mrr']:.3f} |")
    a(f"| 落在 Top{s['top_k']} 之外 | {s['outside_topk']} |")
    a("")
    a("## 按语言")
    a("")
    a("| 语言 | 查询数 | Recall@1 | Recall@3 |")
    a("|---|---|---|---|")
    for lang, v in s["per_language"].items():
        a(f"| {LANG_LABEL.get(lang, lang)} | {v['n']} | {v['recall@1']:.1%} | {v['recall@3']:.1%} |")
    a("")

    if s["failures"]:
        a("## 未进入第 1 位的查询")
        a("")
        a("失败案例一律如实列出，不做粉饰。")
        a("")
        a("| 物品 | 表述风格 | 查询 | 排名 | 分数 |")
        a("|---|---|---|---|---|")
        for f in s["failures"]:
            rank = f["rank"] if f["rank"] else f"> {s['top_k']}"
            score = f"{f['score']:.1f}" if f["score"] is not None else "-"
            a(f"| {f['item']} | `{f['style']}` | {f['query']} | {rank} | {score} |")
        a("")
    else:
        a("## 未进入第 1 位的查询")
        a("")
        a("无。")
        a("")

    a("## 全部查询明细")
    a("")
    a("| | 物品 | 表述风格 | 查询 | 排名 | 分数 | 召回通道 |")
    a("|---|---|---|---|---|---|---|")
    for r in rows:
        mark = "✅" if r["rank"] == 1 else ("🟡" if r["rank"] and r["rank"] <= 3 else "❌")
        rank = r["rank"] if r["rank"] else f"> {s['top_k']}"
        score = f"{r['score']:.1f}" if r["score"] is not None else "-"
        ch = ", ".join(c.replace("vector_", "vec:") for c in r["channels"]) or "-"
        a(f"| {mark} | {r['item']} | `{r['style']}` | {r['query']} | {rank} | {score} | {ch} |")
    a("")
    a("## 复现方法")
    a("")
    a("```bash")
    a("docker compose up -d --build")
    a("docker compose exec api python -m scripts.benchmark")
    a("```")
    a("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="不清库")
    ap.add_argument("--distractors", type=int, default=240)
    ap.add_argument("--out", default=str(ROOT / "docs" / "BENCHMARK.md"))
    ap.add_argument("--json", default=str(ROOT / "docs" / "benchmark.json"))
    ap.add_argument("--top-k", type=int, default=100)
    args = ap.parse_args()

    started = time.perf_counter()
    print(f"[1/4] 等待 API（{BASE}）...")
    health = wait_for_api()
    env = dict(health)
    try:
        cfg = get("/api/admin/config")
        env["embedding_model"] = cfg.get("embedding_model")
        env["llm_model"] = cfg.get("llm_model")
    except Exception:                                          # noqa: BLE001
        pass
    print(f"      provider: llm={env.get('llm_provider')} "
          f"embedding={env.get('embedding_provider')} / {env.get('embedding_model')}")

    if not args.keep:
        print("[2/4] 清空业务数据（保留主数据）...")
        reset_database()
    else:
        print("[2/4] --keep：跳过清库")

    print("[3/4] 灌入语料 ...")
    ids = seed(args.distractors)

    print("[4/4] 跑对抗查询 ...")
    rows = run_queries(ids, top_k=args.top_k)
    summary = summarize(rows, args.top_k)
    elapsed = time.perf_counter() - started
    corpus = len(FOUND_RECORDS) + args.distractors

    write_report(Path(args.out), env, summary, rows, corpus, elapsed)
    Path(args.json).write_text(json.dumps({
        "environment": env, "corpus_size": corpus,
        "elapsed_seconds": round(elapsed, 1),
        "summary": {k: v for k, v in summary.items() if k != "failures"},
        "results": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print("=" * 56)
    print(f"  Recall@1  {summary['recall@1']:.1%}")
    print(f"  Recall@3  {summary['recall@3']:.1%}")
    print(f"  Recall@10 {summary['recall@10']:.1%}")
    print(f"  MRR       {summary['mrr']:.3f}")
    print(f"  未进第 1 位 {len(summary['failures'])}/{summary['queries']}")
    print("=" * 56)
    print(f"报告：{args.out}")
    print(f"JSON：{args.json}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
