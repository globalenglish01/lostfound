"""同义表达对抗评测。

失物系统真正的难点：登记的人和找东西的人几乎不会用同一个词。
日语尤其严重——同一个「包」可以是 かばん / 鞄 / バッグ / リュック / デイパック / 背嚢，
再加上中文、英文、罗马字、口语、儿童用语、方言。

本脚本：
1) 用「工作人员口吻」登记一批 FOUND 记录
2) 用大量**说法完全不同但语义相同**的 query 去搜
3) 统计 Recall@1 / @3 / @10，并打印每个 miss 的实际排名与召回通道

    python -m scripts.eval_synonyms                 # 全量
    python -m scripts.eval_synonyms --only bag      # 只跑某个物品
    python -m scripts.eval_synonyms --seed-only     # 只灌数据
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8080"

# --------------------------------------------------------------------------
# 登记侧：工作人员的写法（每个物品只有这一种表述进库）
# --------------------------------------------------------------------------
FOUND_RECORDS = {
    "bag": {
        "description": "黒いリュックサックを拾いました。ナイロン製で、正面に白いロゴが入っています。",
        "location_name": "新宿站",
    },
    "bottle": {
        "description": "青いステンレス製の水筒の落とし物です。容量は500mlくらいです。",
        "location_name": "东京站",
    },
    "umbrella": {
        "description": "紺色の折り畳み傘を拾得しました。花柄の模様が入っています。",
        "location_name": "涩谷站",
    },
    "wallet": {
        "description": "茶色い革の長財布の拾得物です。カードが数枚入っています。",
        "location_name": "新宿站南口",
    },
    "earbuds": {
        "description": "白いワイヤレスイヤホンを保管しています。充電ケース付きです。",
        "location_name": "新宿站东口",
    },
    "sake": {
        "description": "日本酒の一升瓶の忘れ物です。紙で包装されています。",
        "location_name": "羽田机场",
    },
    "laptop": {
        "description": "銀色のノートパソコンの拾得物です。画面は13インチくらいです。",
        "location_name": "东京站",
    },
}

# --------------------------------------------------------------------------
# 检索侧：故意用完全不同的说法
#   ja-katakana / ja-hiragana / ja-kanji / ja-colloquial / ja-old / zh / en / romaji
# --------------------------------------------------------------------------
QUERIES: dict[str, list[tuple[str, str]]] = {
    "bag": [
        ("ja-katakana", "黒いバッグをなくしました"),
        ("ja-katakana2", "ブラックのバックパックを落としました"),
        ("ja-hiragana", "くろいかばんをおとしました"),
        ("ja-kanji", "黒い鞄を紛失しました"),
        ("ja-colloquial", "黒いリュック落としちゃった"),
        ("ja-old", "黒色の背嚢を失くしました"),
        ("ja-alt", "黒いデイパックが見つかりません"),
        ("zh", "丢了一个黑色双肩包"),
        ("zh2", "黑色背包不见了"),
        ("en", "lost a black backpack"),
        ("romaji", "kuroi kaban wo nakushimashita"),
    ],
    "bottle": [
        ("ja-katakana", "青いタンブラーをなくしました"),
        ("ja-katakana2", "ブルーのマイボトルを落としました"),
        ("ja-hiragana", "あおいすいとうをおとしました"),
        ("ja-kanji", "青色の魔法瓶を紛失"),
        ("ja-colloquial", "青いボトル忘れちゃった"),
        ("zh", "丢了一个蓝色保温杯"),
        ("zh2", "蓝色不锈钢水壶不见了"),
        ("en", "lost a blue thermos flask"),
    ],
    "umbrella": [
        ("ja-hiragana", "こんいろのかさをなくしました"),
        ("ja-katakana", "ネイビーのアンブレラを落としました"),
        ("ja-kanji", "紺色の雨傘を紛失しました"),
        ("ja-colloquial", "紺の折りたたみ忘れた"),
        ("zh", "丢了一把深蓝色折叠伞"),
        ("zh2", "藏青色带花纹的伞不见了"),
        ("en", "lost a navy folding umbrella with flower pattern"),
    ],
    "wallet": [
        ("ja-hiragana", "ちゃいろのさいふをなくしました"),
        ("ja-katakana", "ブラウンのウォレットを落としました"),
        ("ja-kanji", "茶色の札入れを紛失"),
        ("ja-alt", "茶色いがま口を失くしました"),
        ("ja-colloquial", "茶色の財布どっかいった"),
        ("zh", "丢了一个棕色皮夹"),
        ("zh2", "咖啡色真皮长款钱包不见了"),
        ("en", "lost a brown leather long wallet"),
    ],
    "earbuds": [
        ("ja-katakana", "白いワイヤレスイヤフォンをなくしました"),
        ("ja-brand", "白いエアポッズを落としました"),
        ("ja-hiragana", "しろいみみにつけるやつをなくした"),
        ("ja-alt", "白い無線ヘッドホンを紛失"),
        ("zh", "丢了白色无线耳机"),
        ("zh2", "白色蓝牙耳机连充电盒不见了"),
        ("en", "lost white wireless earbuds with charging case"),
    ],
    "sake": [
        ("ja-polite", "お酒の瓶を忘れました"),
        ("ja-alt", "地酒の一升瓶を置き忘れました"),
        ("ja-colloquial", "日本酒の瓶忘れてきた"),
        ("ja-kanji", "清酒の大瓶を紛失"),
        ("zh", "落了一瓶日本清酒"),
        ("en", "left a bottle of sake"),
    ],
    "laptop": [
        ("ja-katakana", "シルバーのノートPCをなくしました"),
        ("ja-alt", "銀色のラップトップを落としました"),
        ("ja-hiragana", "ぎんいろのぱそこんをなくした"),
        ("zh", "丢了一台银色笔记本电脑"),
        ("zh2", "13寸银色手提电脑不见了"),
        ("en", "lost a silver 13 inch laptop"),
    ],
}


def post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def seed() -> dict[str, str]:
    """登记 FOUND 记录，返回 key -> item_id。"""
    ids: dict[str, str] = {}
    for key, rec in FOUND_RECORDS.items():
        out = post("/api/found", {**rec, "auto_match": False})
        ids[key] = out["item_id"]
        print(f"  {key:9s} -> {out['item_id']}  category={out['category']}")
    return ids


def evaluate(ids: dict[str, str], only: str | None) -> None:
    rows: list[tuple[str, str, str, int | None, float | None, list[str]]] = []
    for key, queries in QUERIES.items():
        if only and key != only:
            continue
        target = ids[key]
        for style, q in queries:
            out = post("/api/search", {"query": q, "type": "FOUND",
                                       "top_k": 100, "include_low": True})
            results = out["results"]
            rank = next((i + 1 for i, r in enumerate(results)
                         if r["record_id"] == target), None)
            score = next((r["match_score"] for r in results
                          if r["record_id"] == target), None)
            channels = next((r["retrieval_channels"] for r in results
                             if r["record_id"] == target), [])
            rows.append((key, style, q, rank, score, channels))

    total = len(rows)
    at1 = sum(1 for r in rows if r[3] == 1)
    at3 = sum(1 for r in rows if r[3] and r[3] <= 3)
    at10 = sum(1 for r in rows if r[3] and r[3] <= 10)
    miss = [r for r in rows if r[3] is None]

    print("\n" + "=" * 78)
    print(f"{'物品':<9}{'表述风格':<14}{'排名':>5}{'分数':>8}  查询")
    print("-" * 78)
    for key, style, q, rank, score, ch in rows:
        mark = "  " if rank == 1 else ("~ " if rank and rank <= 3 else "X ")
        print(f"{mark}{key:<8}{style:<14}"
              f"{(rank if rank else '-'):>5}"
              f"{(f'{score:.1f}' if score is not None else '-'):>8}  {q}")

    print("=" * 78)
    print(f"Recall@1  {at1}/{total} = {at1/total:.1%}")
    print(f"Recall@3  {at3}/{total} = {at3/total:.1%}")
    print(f"Recall@10 {at10}/{total} = {at10/total:.1%}")
    print(f"完全召回不到 {len(miss)}/{total}")
    if miss:
        print("\n召回失败的查询：")
        for key, style, q, *_ in miss:
            print(f"  [{key}/{style}] {q}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--seed-only", action="store_true")
    ap.add_argument("--ids", help="复用已登记的 item_id（JSON 文件）")
    args = ap.parse_args()

    ids_path = Path(__file__).with_name("_eval_ids.json")
    if args.ids or (ids_path.exists() and not args.seed_only):
        ids = json.loads(Path(args.ids or ids_path).read_text(encoding="utf-8"))
        print(f"复用已登记记录：{ids_path.name}")
    else:
        print("登记 FOUND 记录（工作人员口吻）...")
        ids = seed()
        ids_path.write_text(json.dumps(ids, ensure_ascii=False, indent=2),
                            encoding="utf-8")

    if args.seed_only:
        return
    evaluate(ids, args.only)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
