"""Embedding 模型迁移。

设计铁律（DESIGN.md §7）：换模型**不要 UPDATE 覆盖**。
新模型以新的 model_name/model_version 并存写入，验证通过后再把旧向量置为 DEPRECATED。
不同模型产生的向量不能直接比较，混用会让检索静默劣化。

    python -m scripts.reembed                 # 用当前 provider 为所有记录补向量
    python -m scripts.reembed --activate      # 补完后把其它模型的向量置为 DEPRECATED
    python -m scripts.reembed --status        # 只看各模型的向量分布
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import text                                       # noqa: E402

from app.ai.embedding_provider import get_embedding_provider      # noqa: E402
from app.db import get_sessionmaker                               # noqa: E402
from app import repository as repo                                # noqa: E402


def status() -> None:
    Session = get_sessionmaker()
    with Session() as s:
        rows = s.execute(text("""
            SELECT model_name, model_version, embedding_type, status,
                   dimensions, COUNT(*)
            FROM embeddings
            GROUP BY 1,2,3,4,5 ORDER BY 1,2,3
        """)).fetchall()
    provider = get_embedding_provider()
    print(f"当前 provider: {provider.name} / {provider.model} / {provider.version} "
          f"/ dim={provider.dim}")
    for r in rows:
        print(f"  {r[0]:<58} {r[1]:<4} {r[2]:<11} {r[3]:<11} dim={r[4]:<5} {r[5]}")


def reembed(activate: bool) -> None:
    provider = get_embedding_provider()
    Session = get_sessionmaker()
    print(f"用 {provider.name} / {provider.model} 重新生成向量 ...")

    with Session() as s:
        ids = [r[0] for r in s.execute(
            text("SELECT id::text FROM item_records ORDER BY created_at")).fetchall()]
        print(f"  共 {len(ids)} 条记录")
        written = skipped = 0
        for i, item_id in enumerate(ids, 1):
            bundle = repo.load_item_bundle(s, item_id)
            if bundle is None:
                continue
            # content_hash 未变则跳过，省掉大量 Embedding 调用
            res = repo.build_embeddings(s, bundle)
            written += sum(1 for v in res.values() if v)
            skipped += sum(1 for v in res.values() if not v)
            if i % 50 == 0:
                s.commit()
                print(f"  ... {i}/{len(ids)}")
        s.commit()
        print(f"  写入 {written} 条，跳过（hash 未变）{skipped} 条")

        if activate:
            r = s.execute(text("""
                UPDATE embeddings SET status = 'DEPRECATED', updated_at = NOW()
                WHERE status = 'ACTIVE'
                  AND NOT (model_name = :m AND model_version = :v)
            """), {"m": provider.model, "v": provider.version})
            s.commit()
            print(f"  旧模型向量置为 DEPRECATED：{r.rowcount} 条")
        else:
            print("  未加 --activate：旧向量仍是 ACTIVE，两套模型并存。")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--activate", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    if args.status:
        status()
        return
    reembed(args.activate)
    print()
    status()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
