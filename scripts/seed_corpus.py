"""生成干扰语料。

只有 7 条记录时 Recall@1 接近 100% 毫无意义——随便排都对。
本脚本按类别 × 颜色 × 材质 × 特征组合出几百条**日语工作人员口吻**的拾获记录，
让评测里的正确答案必须从一堆同类物品中被挑出来。

    python -m scripts.seed_corpus --count 240
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import urllib.request
from pathlib import Path

BASE = os.getenv("LF_API_BASE", "http://127.0.0.1:8080")

TEMPLATES: dict[str, tuple[str, list[str], list[str], list[str]]] = {
    # category: (名詞, 色, 素材/形状, 特徴)
    "bag": ("バッグ",
            ["赤い", "白い", "紺色の", "緑の", "ベージュの", "灰色の", "ピンクの"],
            ["トートバッグ", "ショルダーバッグ", "ハンドバッグ", "リュックサック", "ボストンバッグ"],
            ["中に書類が入っています", "大きめです", "小さめです", "ロゴなし",
             "内側に名前の記載あり", "持ち手が革製です"]),
    "wallet": ("財布",
               ["黒い", "赤い", "白い", "青い", "ピンクの", "緑の"],
               ["二つ折り財布", "長財布", "コインケース", "がま口財布"],
               ["小銭のみ入っています", "カードが1枚入っています", "現金なし",
                "チャック付きです", "使用感があります"]),
    "umbrella": ("傘",
                 ["赤い", "白い", "黒い", "透明の", "緑の", "黄色い"],
                 ["長傘", "折り畳み傘", "ビニール傘", "日傘"],
                 ["水玉模様です", "無地です", "骨が1本折れています", "持ち手が木製です"]),
    "smartphone": ("スマートフォン",
                   ["白い", "青い", "赤い", "金色の", "紫の"],
                   ["Android端末", "スマートフォン", "携帯電話"],
                   ["画面にひびがあります", "手帳型ケース付き", "ケースなし",
                    "リングストラップ付き"]),
    "earbuds": ("イヤホン",
                ["黒い", "青い", "ピンクの", "緑の"],
                ["有線イヤホン", "ワイヤレスイヤホン", "ヘッドホン"],
                ["ケースなし", "片方だけです", "充電ケース付き"]),
    "water_bottle": ("水筒",
                     ["赤い", "白い", "黒い", "ピンクの", "黄色い", "緑の"],
                     ["ステンレスボトル", "プラスチック製の水筒", "タンブラー", "マグボトル"],
                     ["名前シールが貼ってあります", "キャラクターの絵柄です",
                      "中身は空です", "500mlくらいです"]),
    "keys": ("鍵",
             ["銀色の", "黒い", "金色の"],
             ["鍵束", "スマートキー", "自転車の鍵"],
             ["キーホルダー付き", "3本まとまっています", "ぬいぐるみのチャーム付き"]),
    "laptop": ("ノートパソコン",
               ["黒い", "白い", "赤い"],
               ["ノートパソコン", "タブレット"],
               ["電源アダプタ付き", "ステッカーが貼ってあります", "15インチくらいです"]),
    "clothing": ("上着",
                 ["黒い", "紺色の", "ベージュの", "赤い", "灰色の"],
                 ["ジャケット", "コート", "マフラー", "手袋", "帽子"],
                 ["Mサイズです", "ポケットに何も入っていません", "少し汚れがあります"]),
    "book": ("本",
             ["白い", "青い", "赤い"],
             ["文庫本", "手帳", "ノート"],
             ["書き込みがあります", "しおりが挟まっています", "カバー付き"]),
}

LOCATIONS = ["新宿站", "东京站", "涩谷站", "新宿站南口", "新宿站东口", "羽田机场"]


def post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def build(count: int) -> list[dict]:
    rows: list[dict] = []
    pools = {
        cat: list(itertools.product(colors, kinds, feats))
        for cat, (_, colors, kinds, feats) in TEMPLATES.items()
    }
    idx = 0
    while len(rows) < count:
        progressed = False
        for cat, pool in pools.items():
            if idx >= len(pool):
                continue
            progressed = True
            color, kind, feat = pool[idx]
            rows.append({
                "description": f"{color}{kind}の拾得物です。{feat}。",
                "location_name": LOCATIONS[len(rows) % len(LOCATIONS)],
                "auto_match": False,
            })
            if len(rows) >= count:
                break
        if not progressed:
            break
        idx += 1
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=240)
    args = ap.parse_args()

    rows = build(args.count)
    print(f"生成 {len(rows)} 条干扰记录，开始登记 ...")
    ok = 0
    for i, row in enumerate(rows, 1):
        try:
            post("/api/found", row)
            ok += 1
        except Exception as exc:                      # noqa: BLE001
            print(f"  [{i}] 失败: {exc}")
        if i % 40 == 0:
            print(f"  ... {i}/{len(rows)}")
    print(f"完成：{ok}/{len(rows)}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
