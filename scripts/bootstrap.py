"""初始化数据库：建表 + 灌入主数据 + 可选 demo 数据。

    python -m scripts.bootstrap            # 建表 + 主数据
    python -m scripts.bootstrap --demo     # 再灌入演示用的 Lost/Found 记录
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import text                                    # noqa: E402

from app.db import get_engine, get_sessionmaker, init_schema   # noqa: E402

# (code, parent_code, name, level, location_tau_m, time_tau_hours)
CATEGORIES = [
    ("electronics", None, "电子产品", 1, None, None),
    ("smartphone", "electronics", "手机", 2, 500, 24),
    ("laptop", "electronics", "笔记本电脑", 2, 500, 24),
    ("tablet", "electronics", "平板", 2, 500, 24),
    ("earbuds", "electronics", "无线耳机", 2, 300, 24),
    ("camera", "electronics", "相机", 2, 500, 24),
    ("personal", None, "随身物品", 1, None, None),
    ("wallet", "personal", "钱包", 2, 400, 36),
    ("bag", "personal", "包", 2, 500, 36),
    ("umbrella", "personal", "雨伞", 2, 300, 48),
    ("keys", "personal", "钥匙", 2, 300, 48),
    ("water_bottle", "personal", "水杯", 2, 300, 48),
    ("documents", None, "证件", 1, None, None),
    ("passport", "documents", "护照", 2, 1000, 72),
    ("id_card", "documents", "身份证", 2, 1000, 72),
    ("clothing", None, "衣物", 1, None, None),
    ("jewelry", "personal", "首饰", 2, 300, 48),
]

# (name, type, parent_name, lat, lon, aliases)
LOCATIONS = [
    ("日本", "COUNTRY", None, None, None, ["Japan", "japan"]),
    ("东京", "CITY", "日本", 35.6812, 139.7671, ["東京", "Tokyo", "tokyo"]),
    ("新宿区", "WARD", "东京", 35.6938, 139.7034, ["新宿區", "Shinjuku"]),
    ("新宿站", "STATION", "新宿区", 35.6896, 139.7006,
     ["新宿駅", "Shinjuku Station", "shinjuku station", "新宿"]),
    ("新宿站南口", "EXIT", "新宿站", 35.6880, 139.7000,
     ["新宿駅南口", "Shinjuku Station South Exit"]),
    ("新宿站东口", "EXIT", "新宿站", 35.6905, 139.7020, ["新宿駅東口"]),
    ("涩谷站", "STATION", "东京", 35.6580, 139.7016,
     ["渋谷駅", "Shibuya Station", "shibuya"]),
    ("东京站", "STATION", "东京", 35.6812, 139.7671, ["東京駅", "Tokyo Station"]),
    ("羽田机场", "AIRPORT", "东京", 35.5494, 139.7798,
     ["羽田空港", "Haneda Airport", "HND"]),
]

BRANDS = [
    ("Apple", ["苹果", "アップル", "apple"]),
    ("Samsung", ["三星", "サムスン", "samsung"]),
    ("Sony", ["索尼", "ソニー", "sony"]),
    ("Louis Vuitton", ["LV", "路易威登", "ルイヴィトン"]),
    ("Prada", ["普拉达", "プラダ"]),
    ("Nintendo", ["任天堂", "ニンテンドー"]),
]

# 属性定义：(category_code, attribute_code, name, weight, conflict_severity)
ATTRIBUTE_DEFS = [
    ("smartphone", "imei", "IMEI", 10, "CRITICAL"),
    ("smartphone", "serial_number", "序列号", 10, "CRITICAL"),
    ("smartphone", "model", "型号", 8, "CRITICAL"),
    ("smartphone", "brand", "品牌", 6, "MAJOR"),
    ("smartphone", "distinctive", "独特特征", 6, "NONE"),
    ("smartphone", "case", "手机壳", 4, "MINOR"),
    ("smartphone", "color", "颜色", 2, "MINOR"),
    ("wallet", "contents", "内含物品", 8, "NONE"),
    ("wallet", "brand", "品牌", 5, "MAJOR"),
    ("wallet", "pattern", "图案", 5, "MINOR"),
    ("wallet", "color", "颜色", 3, "MINOR"),
    ("wallet", "material", "材质", 3, "MINOR"),
    ("umbrella", "pattern", "图案", 6, "MINOR"),
    ("umbrella", "handle", "伞柄", 5, "MINOR"),
    ("umbrella", "color", "颜色", 4, "MINOR"),
    ("passport", "passport_number", "护照号", 10, "CRITICAL"),
    ("passport", "nationality", "国籍", 10, "MAJOR"),
]


def seed_master_data() -> None:
    Session = get_sessionmaker()
    with Session() as s:
        for code, parent, name, level, tau_m, tau_h in CATEGORIES:
            s.execute(text("""
                INSERT INTO item_categories (code, parent_id, name, level,
                                             location_tau_m, time_tau_hours)
                VALUES (:code,
                        (SELECT id FROM item_categories WHERE code = :parent),
                        :name, :level, :tau_m, :tau_h)
                ON CONFLICT (code) DO UPDATE
                    SET name = EXCLUDED.name,
                        location_tau_m = EXCLUDED.location_tau_m,
                        time_tau_hours = EXCLUDED.time_tau_hours
            """), {"code": code, "parent": parent, "name": name, "level": level,
                   "tau_m": tau_m, "tau_h": tau_h})

        for name, ltype, parent, lat, lon, aliases in LOCATIONS:
            exists = s.execute(text("SELECT id FROM locations WHERE name = :n"),
                               {"n": name}).fetchone()
            if exists:
                continue
            import json
            s.execute(text("""
                INSERT INTO locations (name, normalized_name, location_type, parent_id,
                                       latitude, longitude, aliases)
                VALUES (:name, :norm, :ltype,
                        (SELECT id FROM locations WHERE name = :parent),
                        :lat, :lon, CAST(:aliases AS jsonb))
            """), {"name": name, "norm": name.lower(), "ltype": ltype, "parent": parent,
                   "lat": lat, "lon": lon,
                   "aliases": json.dumps(aliases, ensure_ascii=False)})

        for name, aliases in BRANDS:
            import json
            exists = s.execute(text("SELECT id FROM brands WHERE name = :n"),
                               {"n": name}).fetchone()
            if exists:
                continue
            s.execute(text("""
                INSERT INTO brands (name, normalized_name, aliases)
                VALUES (:name, :norm, CAST(:aliases AS jsonb))
            """), {"name": name, "norm": name.lower(),
                   "aliases": json.dumps(aliases, ensure_ascii=False)})

        for cat, code, name, weight, severity in ATTRIBUTE_DEFS:
            s.execute(text("""
                INSERT INTO attribute_definitions (category_id, attribute_code,
                        attribute_name, importance_weight, conflict_severity)
                VALUES ((SELECT id FROM item_categories WHERE code = :cat),
                        :code, :name, :weight, :severity)
                ON CONFLICT (category_id, attribute_code) DO UPDATE
                    SET importance_weight = EXCLUDED.importance_weight,
                        conflict_severity = EXCLUDED.conflict_severity
            """), {"cat": cat, "code": code, "name": name,
                   "weight": weight, "severity": severity})
        s.commit()


def seed_demo() -> None:
    """灌入演示数据：一条正例 + 一条 Pro/Pro Max 陷阱 + 一条无关物品。"""
    import uuid
    from datetime import datetime, timezone

    from app.ai import extraction
    from app import repository as repo

    Session = get_sessionmaker()
    TZ = timezone.utc

    found_items = [
        ("新宿站南口拾获深灰色 Apple iPhone 15 Pro，透明保护套，背面有猫咪图案",
         "新宿站南口", datetime(2026, 8, 27, 20, 10, tzinfo=TZ), "Locker A-23"),
        ("东京站拾获黑色 iPhone 15 Pro Max，透明壳，背面有猫咪贴纸",
         "东京站", datetime(2026, 8, 27, 21, 0, tzinfo=TZ), "Locker B-07"),
        ("涩谷站拾获黑色长款钱包，皮质，内有数张会员卡",
         "涩谷站", datetime(2026, 8, 27, 18, 0, tzinfo=TZ), "Locker C-01"),
    ]

    with Session() as s:
        for desc, loc, at, storage in found_items:
            parsed = extraction.extract(desc)
            item_id = str(uuid.uuid4())
            cat = s.execute(text("SELECT id FROM item_categories WHERE code = :c"),
                            {"c": parsed["core"].get("category")}).fetchone()
            s.execute(text("""
                INSERT INTO item_records (id, record_type, status, category_id, brand,
                                          model, raw_description, normalized_text)
                VALUES (:id, 'FOUND', 'ACTIVE', :cat, :brand, :model, :raw, :norm)
            """), {"id": item_id, "cat": cat[0] if cat else None,
                   "brand": parsed["core"].get("brand"),
                   "model": parsed["core"].get("model"),
                   "raw": desc, "norm": parsed["normalized_text"]})
            for a in parsed["attributes"]:
                s.execute(text("""
                    INSERT INTO item_attributes (id, item_id, attribute_code, value_text,
                            original_value, source, source_type, confidence)
                    VALUES (:id, :item, :code, :val, :orig, 'AI', :st, :conf)
                """), {"id": str(uuid.uuid4()), "item": item_id,
                       "code": a["attribute_code"], "val": a["value_text"],
                       "orig": a.get("original_value"),
                       "st": a.get("source_type", "EXPLICIT"),
                       "conf": a.get("confidence")})
            loc_row = s.execute(text("SELECT id FROM locations WHERE name = :n"),
                                {"n": loc}).fetchone()
            s.execute(text("""
                INSERT INTO found_reports (id, item_id, found_at, found_location_id,
                                           storage_location)
                VALUES (:id, :item, :at, :loc, :store)
            """), {"id": str(uuid.uuid4()), "item": item_id, "at": at,
                   "loc": loc_row[0] if loc_row else None, "store": storage})
            extraction.save_analysis(s, item_id, parsed)
            bundle = repo.load_item_bundle(s, item_id)
            repo.build_embeddings(s, bundle)
        s.commit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true", help="额外灌入演示记录")
    args = parser.parse_args()

    print("[1/3] 建表 ...")
    init_schema()
    print("[2/3] 灌入主数据（类别 / 地点树 / 品牌 / 属性定义）...")
    seed_master_data()
    if args.demo:
        print("[3/3] 灌入演示 FOUND 记录 ...")
        seed_demo()
    else:
        print("[3/3] 跳过演示数据（加 --demo 可灌入）")
    print("完成。DB:", get_engine().url.render_as_string(hide_password=True))


if __name__ == "__main__":
    main()
