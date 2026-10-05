#!/usr/bin/env python3
"""F-4a：memory_clusters.embedding 幂等回填（成员概念向量均值 + embedding_model 溯源）。

默认 dry-run（只报告将回填数量）；--apply 实际写入。上游 3378f9907 A1 同款。
运行：.venv/bin/python backend/scripts/backfill_cluster_embeddings.py [--apply]
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db.database import AsyncSessionLocal, IS_SQLITE


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际写入（默认 dry-run）")
    args = ap.parse_args()
    if IS_SQLITE:
        print("SQLite 模式无向量列——跳过")
        return 0
    from app.services.memory_cluster_service import _update_cluster_embedding
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(text(
            "SELECT id FROM memory_clusters WHERE embedding IS NULL"))).fetchall()
        print(f"NULL 簇 embedding: {len(rows)}")
        if not args.apply:
            print("dry-run：未写入（--apply 执行回填）")
            return 0
        done = 0
        for (cid,) in rows:
            n = await _update_cluster_embedding(db, cid)
            if n:
                done += 1
        await db.commit()
        print(f"回填完成: {done}/{len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
