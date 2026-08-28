"""抽取规则/模型变更后重跑 AI 理解层。

设计文档 §6：AI 输出必须可追溯、可重跑。
词典改了、抽取模型升级了，已入库的记录不能停在旧结果上——
否则「昨天入库的算 bag、今天入库的算 wallet」，匹配质量会静默劣化。

原始描述 raw_description 永远不动，只重算 category / attributes / normalized_text，
并把新的抽取结果追加到 ai_analyses（旧记录保留，可比对）。

    python -m scripts.reextract              # 重跑全部
    python -m scripts.reextract --only-null  # 只补 category 为空的
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import uuid                                                       # noqa: E402

from sqlalchemy import text                                       # noqa: E402

from app.ai import extraction                                     # noqa: E402
from app.db import get_sessionmaker                               # noqa: E402
from app import repository as repo                                # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only-null", action="store_true",
                    help="只处理 category_id 为空的记录")
    args = ap.parse_args()

    Session = get_sessionmaker()
    where = "WHERE category_id IS NULL" if args.only_null else ""
    with Session() as s:
        rows = s.execute(text(
            f"SELECT id::text, raw_description FROM item_records {where} ORDER BY created_at"
        )).fetchall()
        print(f"待重跑 {len(rows)} 条")

        changed = 0
        for i, (item_id, desc) in enumerate(rows, 1):
            parsed = extraction.extract(desc)
            core = parsed["core"]
            cat = s.execute(text("SELECT id FROM item_categories WHERE code = :c"),
                            {"c": core.get("category")}).fetchone()
            before = s.execute(text(
                "SELECT category_id FROM item_records WHERE id = :i"), {"i": item_id}
            ).fetchone()[0]
            new_cat = cat[0] if cat else None
            if before != new_cat:
                changed += 1

            s.execute(text(
                "UPDATE item_records SET category_id = :cat, brand = :brand, "
                "model = :model, normalized_text = :norm, version = version + 1 "
                "WHERE id = :id"
            ), {"cat": new_cat, "brand": core.get("brand"), "model": core.get("model"),
                "norm": parsed["normalized_text"], "id": item_id})

            # 只清掉 AI 产出的属性，人工/OCR 录入的保留
            s.execute(text(
                "DELETE FROM item_attributes WHERE item_id = :i AND source = 'AI'"),
                {"i": item_id})
            for a in parsed["attributes"]:
                s.execute(text(
                    "INSERT INTO item_attributes (id, item_id, attribute_code, value_text,"
                    " original_value, source, source_type, confidence) "
                    "VALUES (:id, :item, :code, :val, :orig, 'AI', :st, :conf)"
                ), {"id": str(uuid.uuid4()), "item": item_id,
                    "code": a["attribute_code"], "val": a["value_text"],
                    "orig": a.get("original_value"),
                    "st": a.get("source_type", "EXPLICIT"), "conf": a.get("confidence")})

            extraction.save_analysis(s, item_id, parsed)     # 追加，不覆盖旧版本
            bundle = repo.load_item_bundle(s, item_id)
            if bundle is not None:
                repo.build_embeddings(s, bundle)

            if i % 50 == 0:
                s.commit()
                print(f"  ... {i}/{len(rows)}")
        s.commit()
        print(f"完成：{len(rows)} 条重跑，其中 {changed} 条类别发生变化")

        left = s.execute(text(
            "SELECT COUNT(*) FROM item_records WHERE category_id IS NULL")).fetchone()[0]
        print(f"仍未能分类：{left} 条")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
