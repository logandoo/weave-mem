"""weave-mem 同步 wave P0 断言（T1-T4：no-key 守卫 / 复活锚 / 原子权重+answer_cited / billing_class）。

运行：./.venv/bin/python tests/test_sync_p0.py（服务需在跑；T2-T4 用活库会话）
日志：tests/test_sync_p0.log（bash 重定向）
"""
import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import httpx
from sqlalchemy import text

from app.core.config import get_config
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


def _set_mem(key: str, val) -> None:
    cfg = get_config()
    cfg._config.setdefault("memory", {})[key] = val


async def t1_key_resolution() -> None:
    """F-1：显式 base + 空键 → wire no-key，绝不回落全局 LLM key。"""
    cfg = get_config()
    saved_mem = dict(cfg._config.get("memory", {}))
    saved_api = dict(cfg._config.get("api", {}))
    cfg._config.setdefault("api", {})["api_key"] = "GLOBAL-MAIN-KEY"
    try:
        from app.services.memory_embedding_service import _get_embedding_api_key

        _set_mem("embedding_api_base", "")
        _set_mem("embedding_api_key", "")
        check("T1 embedding 无 base 空键→全局 key（保序）", _get_embedding_api_key() == "GLOBAL-MAIN-KEY")

        _set_mem("embedding_api_base", "http://emb.example/v1")
        _set_mem("embedding_api_key", "")
        check("T1 embedding 有 base 空键→no-key", _get_embedding_api_key() == "no-key")

        _set_mem("embedding_api_key", "EMB-KEY")
        check("T1 embedding 有 base 有 key→该 key", _get_embedding_api_key() == "EMB-KEY")

        from app.services.llm_service import LLMService

        svc = LLMService(custom_api_url="http://llm.example/v1", custom_api_key="")
        check("T1 llm_service 自定义端点空键→no-key",
              getattr(svc.client, "api_key", None) == "no-key")
        svc2 = LLMService(custom_api_url="http://llm.example/v1", custom_api_key="K2")
        check("T1 llm_service 自定义端点有 key→该 key",
              getattr(svc2.client, "api_key", None) == "K2")

        from app.services.provider_router import OpenAIAdapter, ProviderConfig

        kw = OpenAIAdapter(ProviderConfig(name="x", base_url="http://p.example/v1", api_key="")).get_client_kwargs()
        check("T1 provider_router 显式端点空键→no-key", kw.get("api_key") == "no-key")
        kw2 = OpenAIAdapter(ProviderConfig(name="x", base_url="http://p.example/v1", api_key="K3")).get_client_kwargs()
        check("T1 provider_router 显式端点有 key→该 key", kw2.get("api_key") == "K3")
    finally:
        cfg._config["memory"] = saved_mem
        cfg._config["api"] = saved_api


async def _uid(c: httpx.AsyncClient) -> str:
    suffix = uuid.uuid4().hex[:8]
    uname = f"syncp0_{suffix}"
    r = await c.post("/api/auth/register", json={"username": uname, "password": "test123"})
    assert r.status_code == 201, r.text
    r = await c.post("/api/auth/login", json={"username": uname, "password": "test123"})
    return r.json()["user"]["id"]


async def t2_resurrect_anchor() -> None:
    """F-2：复活必须刷新 weight_decayed_at（否则次夜旧锚衰变回 floor）。"""
    from app.services.memory_weight_service import try_cold_resurrect

    async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as c:
        uid = await _uid(c)
    cid = str(uuid.uuid4())
    async with AsyncSessionLocal() as db:
        await db.execute(text(
            "INSERT INTO memory_concepts (id, user_id, canonical_name, description_short, status, "
            "weight, stability, importance, importance_evaluated, source_trust, source_type, memory_type, "
            "activation_strength, recurrence_count, hot_forget_count, needs_review, "
            "weight_decayed_at, created_at, updated_at, valid_from) "
            "VALUES (:id, :uid, 'syncp0锚测试', 'f2', 'cold_forgotten', 0.1, 14, 0.5, FALSE, 'user_confirmed', "
            "'user_input', 'fact', 0.0, 0, 0, FALSE, "
            "'2020-01-01 00:00:00', '2020-01-01 00:00:00', '2020-01-01 00:00:00', '2020-01-01 00:00:00')"),
            {"id": cid, "uid": uid})
        await db.commit()
        got = await try_cold_resurrect(db, "提到了 syncp0锚测试 的消息", uid)
        await db.commit()
        check("T2 复活命中", cid in got, f"got={got}")
        row = (await db.execute(text(
            "SELECT weight_decayed_at, status FROM memory_concepts WHERE id = :id"), {"id": cid})).fetchone()
        check("T2 复活后 weight_decayed_at 已刷新", row is not None and row[0] is not None
              and str(row[0])[:4] > "2020", f"row={row}")
        await db.execute(text("DELETE FROM memory_concepts WHERE id = :id"), {"id": cid})
        await db.commit()


async def t3_atomic_weight() -> None:
    """F-3：原子更新语义 + answer_cited 信号 +0.02。"""
    from app.services.memory_weight_service import apply_reinforcement_signal

    async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as c:
        uid = await _uid(c)
    cid = str(uuid.uuid4())
    async with AsyncSessionLocal() as db:
        await db.execute(text(
            "INSERT INTO memory_concepts (id, user_id, canonical_name, description_short, status, "
            "weight, stability, importance, importance_evaluated, source_trust, source_type, memory_type, "
            "activation_strength, recurrence_count, hot_forget_count, needs_review, "
            "created_at, updated_at, valid_from) "
            "VALUES (:id, :uid, 'syncp0权重测试', 'f3', 'active', 0.5, 14, 0.5, FALSE, 'user_confirmed', "
            "'user_input', 'fact', 1.0, 0, 0, FALSE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"),
            {"id": cid, "uid": uid})
        await db.commit()
        await apply_reinforcement_signal(db, cid, "recall_reference")
        await apply_reinforcement_signal(db, cid, "recall_reference")
        await db.commit()
        w = (await db.execute(text("SELECT weight FROM memory_concepts WHERE id = :id"), {"id": cid})).scalar()
        check("T3 两次 recall_reference 累加 0.06", abs(float(w) - 0.56) < 1e-6, f"weight={w}")
        await apply_reinforcement_signal(db, cid, "answer_cited")
        await db.commit()
        w2 = (await db.execute(text("SELECT weight FROM memory_concepts WHERE id = :id"), {"id": cid})).scalar()
        check("T3 answer_cited +0.02", abs(float(w2) - 0.58) < 1e-6, f"weight={w2}")
        await apply_reinforcement_signal(db, cid, "no_such_signal")
        await db.commit()
        w3 = (await db.execute(text("SELECT weight FROM memory_concepts WHERE id = :id"), {"id": cid})).scalar()
        check("T3 未知信号零变化", abs(float(w3) - float(w2)) < 1e-6, f"weight={w3}")
        await db.execute(text("DELETE FROM memory_concepts WHERE id = :id"), {"id": cid})
        await db.commit()


async def t4_billing_class() -> None:
    """F-5：读写分离——读遥测不进降级计数。"""
    from app.services.memory_cost_governance_service import (
        get_user_degrade_status, record_llm_call, record_llm_call_bg)

    async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as c:
        uid = await _uid(c)
    async with AsyncSessionLocal() as db:
        for _ in range(3):
            await record_llm_call(db, uid, "concept_extract", billing_class="write")
        for _ in range(5):
            await record_llm_call(db, uid, "query_expand", billing_class="read")
        await db.commit()
        rows = (await db.execute(text(
            "SELECT billing_class, COUNT(*) FROM memory_llm_calls WHERE user_id = :uid GROUP BY billing_class"),
            {"uid": uid})).fetchall()
        got = {r[0]: r[1] for r in rows}
        check("T4 落库 write=3/read=5", got.get("write") == 3 and got.get("read") == 5, f"got={got}")
        st = await get_user_degrade_status(db, uid)
        check("T4 降级计数只含 write", st.get("today_calls", -1) == 3, f"status={st}")
        await record_llm_call_bg(uid, "query_expand", billing_class="read")
        n = (await db.execute(text(
            "SELECT COUNT(*) FROM memory_llm_calls WHERE user_id = :uid AND billing_class = 'read'"),
            {"uid": uid})).scalar()
        check("T4 bg 记录器写 read 行", n == 6, f"n={n}")
        await db.execute(text("DELETE FROM memory_llm_calls WHERE user_id = :uid"), {"uid": uid})
        await db.commit()


async def t1b_decay_guard_production() -> None:
    """衰变写回乐观守卫经生产路径验证（双审 F1/F3）：
    run_weight_decay 的 SELECT 与 UPDATE 之间并发 boost → 写回让权、热度腿跳过。"""
    from sqlalchemy import text as _t
    from app.services.memory_weight_service import (
        apply_reinforcement_signal, run_weight_decay)

    async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as c:
        uid = await _uid(c)
    cid = str(uuid.uuid4())
    async with AsyncSessionLocal() as db:
        await db.execute(_t(
            "INSERT INTO memory_concepts (id, user_id, canonical_name, description_short, status, "
            "weight, stability, importance, importance_evaluated, source_trust, source_type, memory_type, "
            "activation_strength, recurrence_count, hot_forget_count, needs_review, "
            "weight_decayed_at, created_at, updated_at, valid_from) "
            "VALUES (:id, :uid, '衰变守卫靶标', 'guard', 'active', 0.5, 1, 0.5, FALSE, 'user_confirmed', "
            "'user_input', 'semantic', 1.0, 0, 0, FALSE, '2020-01-01 00:00:00', "
            "'2020-01-01 00:00:00', '2020-01-01 00:00:00', '2020-01-01 00:00:00')"),
            {"id": cid, "uid": uid})
        await db.commit()

    boosted = {"done": False}
    async with AsyncSessionLocal() as db_a:
        orig_execute = db_a.execute
        fired = {"n": 0}

        async def interleaving_execute(*args, **kwargs):
            result = await orig_execute(*args, **kwargs)
            stmt = str(args[0]) if args else ""
            if fired["n"] == 0 and "FROM memory_concepts WHERE user_id" in stmt and "SELECT" in stmt.upper():
                fired["n"] += 1
                # SELECT 已返回 → 并发 boost（另一会话）制造写回竞争
                async with AsyncSessionLocal() as db_b:
                    await apply_reinforcement_signal(db_b, cid, "recall_reference")
                    await db_b.commit()
                boosted["done"] = True
            return result

        db_a.execute = interleaving_execute
        res = await run_weight_decay(db_a, uid)
        await db_a.commit()
    check("decay 交错 boost 已发生", boosted["done"] is True, f"res={res}")
    async with AsyncSessionLocal() as db:
        row = (await db.execute(_t(
            "SELECT weight, status, hot_forget_count FROM memory_concepts WHERE id = :id"),
            {"id": cid})).fetchone()
        await db.execute(_t("DELETE FROM memory_concepts WHERE id = :id"), {"id": cid})
        await db.commit()
    check("decay 写回让权（boost 保留 0.53）", row is not None and abs(float(row[0]) - 0.53) < 1e-6,
          f"row={row}")
    check("decay 热度/状态腿跳过", row is not None and row[1] == "active" and int(row[2]) == 0,
          f"row={row}")


async def t13_cluster_embedding() -> None:
    """F-4a：簇 embedding 写路径（成员向量均值 + 维度 + 最近簇排序）。"""
    from app.db.database import IS_SQLITE
    if IS_SQLITE:
        print("SKIP  T13（SQLite 无向量列）")
        return
    from app.services.memory_cluster_service import (
        _update_cluster_embedding, add_concept_to_cluster)
    from app.services.memory_consolidation_service import _find_nearest_cluster

    async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as c:
        uid = await _uid(c)
    dim = 4  # 直插短向量（列类型 vector(1536)——按实际列宽适配）
    async with AsyncSessionLocal() as db:
        col = (await db.execute(text(
            "SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a "
            "WHERE a.attrelid = 'memory_concepts'::regclass AND a.attname = 'embedding'"))).scalar()
        dim = int(col.split("(")[1].rstrip(")")) if col and "(" in col else dim
        mk = lambda x: "[" + ",".join([str(x)] * dim) + "]"
        c1, c2, cl_a, cl_b = (str(uuid.uuid4()) for _ in range(4))
        for _id, name, vec in ((c1, "T13成员一", mk(0.1)), (c2, "T13成员二", mk(0.3))):
            await db.execute(text(
                "INSERT INTO memory_concepts (id, user_id, canonical_name, description_short, status, "
                "weight, stability, importance, importance_evaluated, source_trust, source_type, memory_type, "
                "activation_strength, recurrence_count, hot_forget_count, needs_review, embedding, "
                "created_at, updated_at, valid_from) "
                "VALUES (:id, :uid, :name, 't13', 'active', 0.5, 14, 0.5, FALSE, 'user_confirmed', "
                "'user_input', 'fact', 1.0, 0, 0, FALSE, CAST(:v AS vector), "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"),
                {"id": _id, "uid": uid, "name": name, "v": vec})
        for _id, name in ((cl_a, "T13簇甲"),):
            await db.execute(text(
                "INSERT INTO memory_clusters (id, user_id, name, member_count, weight) "
                "VALUES (:id, :uid, :nm, 0, 0.5)"),
                {"id": _id, "uid": uid, "nm": name})
        await db.commit()
        await add_concept_to_cluster(db, uid, cl_a, c1)
        await add_concept_to_cluster(db, uid, cl_a, c2)
        await db.commit()
        row = (await db.execute(text(
            "SELECT embedding IS NOT NULL, embedding_model IS NOT NULL, member_count "
            "FROM memory_clusters WHERE id = :id"), {"id": cl_a})).fetchone()
        check("T13 入簇后簇 embedding 非 NULL + 溯源落库", row is not None and row[0] is True and row[1] is True,
              f"row={row}")
        n = await _update_cluster_embedding(db, cl_a)
        check("T13 均值聚合成员数", n == 2, f"n={n}")
        await db.execute(text(
            "INSERT INTO memory_clusters (id, user_id, name, member_count, weight, embedding) "
            "VALUES (:id, :uid, :nm, 1, 0.5, CAST(:v AS vector))"),
            {"id": cl_b, "uid": uid, "nm": "T13簇乙", "v": mk(0.9)})
        await db.commit()
        nearest = await _find_nearest_cluster(db, uid, cl_a)
        check("T13 最近簇=真实距离最近", nearest == cl_b, f"nearest={nearest}")
        import subprocess as _sp
        r = _sp.run([sys.executable, "backend/scripts/backfill_cluster_embeddings.py"],
                    capture_output=True, text=True, timeout=60)
        check("T13 回填脚本 dry-run 可用", r.returncode == 0 and "dry-run" in (r.stdout + r.stderr),
              f"rc={r.returncode} out={r.stdout[-120:]}")
        await db.execute(text("DELETE FROM concept_cluster_members WHERE cluster_id IN (:a, :b)"),
                         {"a": cl_a, "b": cl_b})
        await db.execute(text("DELETE FROM memory_clusters WHERE id IN (:a, :b)"), {"a": cl_a, "b": cl_b})
        await db.execute(text("DELETE FROM memory_concepts WHERE id IN (:a, :b)"), {"a": c1, "b": c2})
        await db.commit()


async def t1_rmw_atomic() -> None:
    """遗留清理：衰变写回乐观守卫不覆盖并发改动 + bulk boost 原子累加。"""
    from sqlalchemy import text as _t
    from app.services.memory_weight_service import _bulk_update_concepts

    async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as c:
        uid = await _uid(c)
    cid = str(uuid.uuid4())
    async with AsyncSessionLocal() as db:
        await db.execute(_t(
            "INSERT INTO memory_concepts (id, user_id, canonical_name, description_short, status, "
            "weight, stability, importance, importance_evaluated, source_trust, source_type, memory_type, "
            "activation_strength, recurrence_count, hot_forget_count, needs_review, "
            "created_at, updated_at, valid_from) "
            "VALUES (:id, :uid, 'RMW原子靶标', 'rmw', 'active', 0.5, 14, 0.5, FALSE, 'user_confirmed', "
            "'user_input', 'semantic', 1.0, 0, 0, FALSE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"),
            {"id": cid, "uid": uid})
        await db.commit()
        # 乐观守卫语义：过期 old 值的写回 → rowcount 0（不覆盖）
        r = await db.execute(_t(
            "UPDATE memory_concepts SET weight = 0.1 WHERE id = :id AND COALESCE(weight, 0) = :old"),
            {"id": cid, "old": 0.4})
        check("RMW 衰变写回守卫拒过期值", (r.rowcount or 0) == 0, f"rowcount={r.rowcount}")
        w0 = (await db.execute(_t("SELECT weight FROM memory_concepts WHERE id = :id"), {"id": cid})).scalar()
        check("RMW 被拒后权重未被覆盖", abs(float(w0) - 0.5) < 1e-6, f"w={w0}")
        r2 = await db.execute(_t(
            "UPDATE memory_concepts SET weight = 0.1 WHERE id = :id AND COALESCE(weight, 0) = :old"),
            {"id": cid, "old": 0.5})
        check("RMW 守卫放行当前值", (r2.rowcount or 0) == 1)
        await db.execute(_t("UPDATE memory_concepts SET weight = 0.5 WHERE id = :id"), {"id": cid})
        await db.commit()
        await _bulk_update_concepts(db, [cid], 0.03, user_id=uid)
        await _bulk_update_concepts(db, [cid], 0.03, user_id=uid)
        await db.commit()
        w1 = (await db.execute(_t("SELECT weight FROM memory_concepts WHERE id = :id"), {"id": cid})).scalar()
        check("RMW bulk 原子累加 0.56", abs(float(w1) - 0.56) < 1e-6, f"w={w1}")
        await db.execute(_t("DELETE FROM memory_concepts WHERE id = :id"), {"id": cid})
        await db.commit()


async def main() -> None:
    await t1_key_resolution()
    await t2_resurrect_anchor()
    await t3_atomic_weight()
    await t4_billing_class()
    await t1_rmw_atomic()
    await t1b_decay_guard_production()
    await t13_cluster_embedding()
    print(f"\n==== 结果: {passed} passed, {failed} failed ====")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
