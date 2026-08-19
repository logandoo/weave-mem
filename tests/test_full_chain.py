"""weave-mem 验收测试 6：记忆写入 → 召回整条逻辑链（端到端）。

链路 1（手动写入路径，真实 HTTP）：
  注册 → POST /concepts 写入概念 → GET /concepts/{id} 详情 → POST /recall 命中（BM25 文本通道）

链路 2（自动提炼闭环，mock embedding/LLM，服务层）：
  ingest_raw_unit ×3（潜意识摄入）→ subconscious_log 落库
  → scan_recurrence（递归检测 + LLM 提炼）→ 概念落库（promoted）
  → HTTP POST /recall 命中自动提炼的概念

运行：./.venv/bin/python tests/test_full_chain.py
"""
import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import httpx

from app.db.database import AsyncSessionLocal

BASE = "http://127.0.0.1:8202"
passed = 0
failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS  {name} {detail}")
    else:
        failed += 1
        print(f"FAIL  {name} {detail}")


class FakeLLM:
    def __init__(self, payload: dict):
        self._payload = payload

    async def complete_chat(self, messages, **kwargs):
        return json.dumps(self._payload, ensure_ascii=False)


async def main() -> None:
    suffix = uuid.uuid4().hex[:8]
    uname = f"chain_{suffix}"
    keyword = f"chainkws{suffix}"
    async with httpx.AsyncClient(base_url=BASE, timeout=60.0) as c:
        r = await c.post("/api/auth/register", json={"username": uname, "password": "test123"})
        check("注册", r.status_code == 201, f"status={r.status_code}")
        r = await c.post("/api/auth/login", json={"username": uname, "password": "test123"})
        token = r.json()["access_token"]
        uid = r.json()["user"]["id"]
        h = {"Authorization": f"Bearer {token}"}

        # ===== 链路 1：手动写入 → 召回（8 个概念越过冷启动阈值 → Stage1 BM25）=====
        statuses = []
        cid = None
        for i in range(8):
            r = await c.post("/api/memory/concepts", headers=h, json={
                "canonical_name": f"{keyword}主题{i}号",
                "description_short": f"关于{keyword}的第{i}个偏好",
                "importance": 0.9,
            })
            statuses.append(r.status_code)
            if i == 0:
                cid = r.json()["id"]
        check("概念写入 ×8 全 201", all(s == 201 for s in statuses), f"statuses={statuses}")

        r = await c.get(f"/api/memory/concepts/{cid}", headers=h)
        check("概念详情 200", r.status_code == 200 and r.json()["canonical_name"] == f"{keyword}主题0号",
              f"status={r.status_code} name={r.json().get('canonical_name')}")

        r = await c.post("/api/memory/recall", headers=h, json={"query": f"{keyword} 主题"})
        body = r.json()
        ctx = body.get("context") or ""
        hit0 = f"{keyword}主题0号" in ctx
        check("召回命中写入概念（BM25）", body.get("mode") == "text" and hit0,
              f"mode={body.get('mode')} hit={hit0} ctx_len={len(ctx)}")

        # ===== 链路 2：潜意识摄入 → 自动提炼 → 召回 =====
        from app.services import memory_embedding_service as mes
        from app.services import memory_llm_factory as mlf
        from app.services.memory_subconscious_service import ingest_raw_unit, scan_recurrence
        from sqlalchemy import text

        import app.services.memory_subconscious_service as mss
        orig_embed = mes.embed_text
        orig_embed_mss = mss.embed_text
        orig_neighbors = mes.find_neighbors_for_unit
        orig_llm = mlf._memory_llm

        from app.core.config import get_config
        _dim = int(get_config().memory.get("embedding_dim", 1536))

        async def fake_embed(text):
            return [0.1] * _dim

        async def fake_neighbors(db, user_id, emb, unit_id, created_at, top_k=5):
            result = await db.execute(
                text("SELECT id, raw_text FROM subconscious_log WHERE user_id = :uid AND id != :uid2 AND promoted = FALSE"),
                {"uid": user_id, "uid2": unit_id},
            )
            rows = result.fetchall()[:top_k]
            return [{"id": r[0], "raw_text": r[1], "similarity": 0.9} for r in rows]

        mes.embed_text = fake_embed
        mss.embed_text = fake_embed
        mes.find_neighbors_for_unit = fake_neighbors
        mlf._memory_llm = lambda kind: FakeLLM({
            "episodic": {"narrative": f"用户反复提及{keyword}相关工作", "valid_from": None, "merge_with_episode_id": None},
            "concepts": [{
                "canonical_name": f"{keyword}自动提炼概念",
                "description_short": f"由{keyword}潜意识链自动提炼",
                "description_full": "",
                "aliases": [],
                "match_existing_id": None,
                "cluster_suggestion": None,
                "source_trust": "user_stated",
                "memory_type": "semantic",
                "importance": 0.8,
                "source_unit_ids": [],
                "event_time": None,
            }],
        })
        try:
            async with AsyncSessionLocal() as db:
                unit_ids = []
                for i in range(4):
                    uid_unit = await ingest_raw_unit(db, uid, "message", f"{keyword}单元{i}号内容", [])
                    check(f"潜意识摄入 #{i}", uid_unit is not None, f"unit_id={uid_unit}")
                    unit_ids.append(uid_unit)
                await db.commit()

                count = await scan_recurrence(db, uid)
                check("scan_recurrence 触发提炼", count >= 1, f"promoted={count}")
                await db.commit()

                row = (await db.execute(text(
                    "SELECT COUNT(*) FROM memory_concepts WHERE user_id = :uid AND canonical_name = :n"
                ), {"uid": uid, "n": f"{keyword}自动提炼概念"})).fetchone()
                check("自动提炼概念落库", row[0] == 1, f"count={row[0]}")

                promoted = (await db.execute(text(
                    "SELECT COUNT(*) FROM subconscious_log WHERE user_id = :uid AND promoted = TRUE"
                ), {"uid": uid})).fetchone()
                check("单元标记 promoted", promoted[0] == 4, f"promoted={promoted[0]}")
        finally:
            mes.embed_text = orig_embed
            mss.embed_text = orig_embed_mss
            mes.find_neighbors_for_unit = orig_neighbors
            mlf._memory_llm = orig_llm

        # 自动提炼概念 → HTTP 上下文命中（经总览基底路径；测试进程直写 DB，
        # 服务器进程 BM25 索引不刷新属测试隔离伪影——生产提炼与 HTTP 写入同在
        # 服务器进程，create_concept 内 _update_bm25_on_concept_change 会更新索引）
        # 注（A4.9 Minor-11）：总览路径命中依赖 overview 预算（9 概念 < 800 字符
        # 且 BM25-only gate<0.5 不收缩）；若 overview_max_concepts 配置变更需复验。
        r = await c.post("/api/memory/recall", headers=h, json={"query": f"{keyword} 自动提炼"})
        ctx2 = r.json().get("context") or ""
        check("上下文命中自动提炼概念（总览路径）", f"{keyword}自动提炼概念" in ctx2,
              f"hit={f'{keyword}自动提炼概念' in ctx2} ctx_len={len(ctx2)}")

        # 服务层验证：索引刷新后 BM25 可命中提炼概念（证明陈旧仅为进程隔离）
        from app.services.memory_bm25 import get_name_index, get_desc_index
        async with AsyncSessionLocal() as db:
            concept_row = (await db.execute(text(
                "SELECT id FROM memory_concepts WHERE user_id = :uid AND canonical_name = :n"
            ), {"uid": uid, "n": f"{keyword}自动提炼概念"})).fetchone()
            cid2 = concept_row[0] if concept_row else None
            nidx = await get_name_index(db, uid)
            didx = await get_desc_index(db, uid)
            n_hit = cid2 in [d for d, s in nidx.search(f"{keyword} 自动提炼", k=10)]
            d_hit = cid2 in [d for d, s in didx.search(f"{keyword} 自动提炼", k=10)]
            check("索引刷新后 BM25 命中提炼概念", n_hit or d_hit, f"n_hit={n_hit} d_hit={d_hit} cid={cid2}")

    print(f"\n==== 结果: {passed} passed, {failed} failed ====")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
