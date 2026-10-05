"""weave-mem 同步 wave P1 断言（T5 采纳闭环 + T6 一致性加权）。

用例出自 docs/openapi.json（§A4.7 文档驱动）：
POST /api/memory/adoption 契约 + 写回链；apply_cross_modal_consistency 纯函数。
运行：./.venv/bin/python tests/test_adoption.py（服务需在跑）
"""
import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import httpx
from sqlalchemy import text

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


async def _user(c: httpx.AsyncClient) -> tuple[str, str]:
    suffix = uuid.uuid4().hex[:8]
    uname = f"adopt_{suffix}"
    r = await c.post("/api/auth/register", json={"username": uname, "password": "test123"})
    assert r.status_code == 201, r.text
    r = await c.post("/api/auth/login", json={"username": uname, "password": "test123"})
    return r.json()["user"]["id"], r.json()["access_token"]


async def t5_contract(c: httpx.AsyncClient) -> None:
    r = await c.post("/api/memory/adoption", json={"injected_ids": ["x"], "answer_text": "y"})
    check("T5 401 未认证", r.status_code == 401, f"status={r.status_code}")
    uid, tok = await _user(c)
    h = {"Authorization": f"Bearer {tok}"}
    r = await c.post("/api/memory/adoption", headers=h, json={"injected_ids": ["x"]})
    check("T5 422 缺 answer_text", r.status_code == 422, f"status={r.status_code}")
    r = await c.post("/api/memory/adoption", headers=h, json={"injected_ids": "x", "answer_text": "y"})
    check("T5 422 injected_ids 非列表", r.status_code == 422, f"status={r.status_code}")
    r = await c.post("/api/memory/adoption", headers=h, json={"injected_ids": [], "answer_text": "无命中"})
    check("T5 200 空注入", r.status_code == 200 and r.json().get("adopted") == 0,
          f"status={r.status_code} body={r.json() if r.status_code == 200 else r.text}")
    return uid, tok


async def t5_writeback(c: httpx.AsyncClient) -> None:
    uid, tok = await _user(c)
    h = {"Authorization": f"Bearer {tok}"}
    cid = str(uuid.uuid4())
    cid2 = str(uuid.uuid4())
    async with AsyncSessionLocal() as db:
        for _id, name in ((cid, "同步采纳靶标A"), (cid2, "同步采纳邻居B")):
            await db.execute(text(
                "INSERT INTO memory_concepts (id, user_id, canonical_name, description_short, status, "
                "weight, stability, importance, importance_evaluated, source_trust, source_type, memory_type, "
                "activation_strength, recurrence_count, hot_forget_count, needs_review, "
                "created_at, updated_at, valid_from) "
                "VALUES (:id, :uid, :name, 'p1', 'active', 0.5, 14, 0.5, FALSE, 'user_confirmed', "
                "'user_input', 'fact', 1.0, 0, 0, FALSE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"),
                {"id": _id, "uid": uid, "name": name})
        await db.execute(text(
            "INSERT INTO concept_relations (id, user_id, source_id, target_id, relation_type, weight, created_at) "
            "VALUES (:rid, :uid, :s, :t, 'related_to', 0.50, CURRENT_TIMESTAMP)"),
            {"rid": str(uuid.uuid4()), "uid": uid, "s": cid, "t": cid2})
        await db.commit()
    r = await c.post("/api/memory/adoption", headers=h, json={
        "injected_ids": [cid, cid2], "answer_text": "这回答引用了同步采纳靶标A，但没提邻居。"})
    body = r.json() if r.status_code == 200 else {}
    check("T5 200 命中契约", r.status_code == 200 and body.get("adopted") == 1
          and body.get("matched") == [cid] and body.get("relation_bumped") == 1,
          f"status={r.status_code} body={body}")
    async with AsyncSessionLocal() as db:
        w = (await db.execute(text("SELECT weight FROM memory_concepts WHERE id = :id"), {"id": cid})).scalar()
        check("T5 concept weight +0.02", abs(float(w) - 0.52) < 1e-6, f"weight={w}")
        rw = (await db.execute(text(
            "SELECT weight FROM concept_relations WHERE source_id = :s"), {"s": cid})).scalar()
        check("T5 relation 边权 +0.02", abs(float(rw) - 0.52) < 1e-6, f"weight={rw}")
    from app.services.memory_adoption_service import (
        adopted_concepts_recent, record_answer_adoption)
    # _ADOPTED_RECENT 是进程内缓存（HTTP 落在服务进程）——同进程直调验证缓存语义
    async with AsyncSessionLocal() as db:
        await record_answer_adoption(db, uid, [cid], "再次提到同步采纳靶标A")
        await db.commit()
    check("T5 adopted 缓存收录", cid in adopted_concepts_recent(), f"recent={list(adopted_concepts_recent())[:5]}")
    # 边权封顶：0.99 + 0.02 → 1.0（cap）
    async with AsyncSessionLocal() as db:
        await db.execute(text(
            "INSERT INTO concept_relations (id, user_id, source_id, target_id, relation_type, weight, created_at) "
            "VALUES (:rid, :uid, :s, :t, 'related_to', 0.99, CURRENT_TIMESTAMP)"),
            {"rid": str(uuid.uuid4()), "uid": uid, "s": cid2, "t": cid})
        await db.commit()
        await record_answer_adoption(db, uid, [cid2], "同步采纳邻居B 出现")
        await db.commit()
        rw2 = (await db.execute(text(
            "SELECT weight FROM concept_relations WHERE source_id = :s"), {"s": cid2})).scalar()
        check("T5 relation 边权封顶 1.0", abs(float(rw2) - 1.0) < 1e-6, f"weight={rw2}")
        await db.execute(text("DELETE FROM concept_relations WHERE user_id = :uid"), {"uid": uid})
        await db.execute(text("DELETE FROM memory_concepts WHERE user_id = :uid"), {"uid": uid})
        await db.commit()
    async with AsyncSessionLocal() as db:
        await db.execute(text("DELETE FROM concept_relations WHERE source_id = :s"), {"s": cid})
        await db.execute(text("DELETE FROM memory_concepts WHERE id IN (:a, :b)"), {"a": cid, "b": cid2})
        await db.commit()


def t6_consistency() -> None:
    from app.services.memory_retrieval_service import (
        RetrievalCandidate, apply_cross_modal_consistency)

    def cand(cid: str, score: float, lex, dense, cal=None) -> RetrievalCandidate:
        meta = {}
        if lex is not None:
            meta["lex_rank"] = lex
        if dense is not None:
            meta["dense_rank"] = dense
        if cal is not None:
            meta["calibrated_score"] = cal
        return RetrievalCandidate(id=cid, tier="concept", content="c", score=score, metadata=meta)

    c1 = cand("v1", 0.5, 1, 2, cal=0.5)
    c2 = cand("v2", 0.5, 1, 8, cal=0.5)
    c3 = cand("u1", 0.5, 1, 2, cal=0.5)
    c4 = cand("v3", 0.5, None, 2)
    apply_cross_modal_consistency([c1, c2, c3, c4], verified_ids={"v1", "v2", "v3"})
    check("T6 双靠前 +bonus", abs(c1.score - 0.55) < 1e-6, f"score={c1.score}")
    check("T6 bonus 镜像 calibrated_score", abs(c1.metadata["calibrated_score"] - 0.55) < 1e-6,
          f"cal={c1.metadata['calibrated_score']}")
    check("T6 悬殊 -damp", abs(c2.score - 0.47) < 1e-6, f"score={c2.score}")
    check("T6 damp 镜像 calibrated_score", abs(c2.metadata["calibrated_score"] - 0.47) < 1e-6,
          f"cal={c2.metadata['calibrated_score']}")
    check("T6 非 verified 不动", abs(c3.score - 0.5) < 1e-6, f"score={c3.score}")
    check("T6 缺 rank 快照不动", abs(c4.score - 0.5) < 1e-6, f"score={c4.score}")
    c5 = cand("v4", 0.01, 1, 9, cal=0.01)
    apply_cross_modal_consistency([c5], verified_ids={"v4"})
    check("T6 damp 下限 0", abs(c5.score - 0.0) < 1e-6, f"score={c5.score}")
    c6 = cand("v5", 0.5, 1, 2)
    apply_cross_modal_consistency([c6], verified_ids=set())
    check("T6 空 verified 恒不动", abs(c6.score - 0.5) < 1e-6, f"score={c6.score}")


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=60.0) as c:
        await t5_contract(c)
        await t5_writeback(c)
    t6_consistency()
    print(f"\n==== 结果: {passed} passed, {failed} failed ====")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
