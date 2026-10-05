"""weave-mem 同步 wave P2 断言（T7 策略路由 / T8 D1 确定性边+P/L/T / T9 D2 / T10 D3 / T11 E1·A4c·W8）。

门控默认关 = 合入零行为变化（基线对照）；门开 = 各特性行为断言。
运行：./.venv/bin/python tests/test_p2_gates.py（服务需在跑）
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


def t7_strategy() -> None:
    from app.services.memory_retrieval_service import (
        STRATEGY_PROFILE_DEFAULTS, select_retrieval_profile, strategy_profile_params)

    class S0:
        keywords = ["苹果", "香蕉", "果盘"]

    cfg = get_config().memory_retrieval or {}
    # 2026-10-05 遗留清理：strategy_route_enabled 对齐上游开启态——断言改为
    # 显式关=零行为 + 配置态=路由生效
    check("T7 显式关恒 default", select_retrieval_profile("苹果 香蕉 果盘 都很好", S0(),
          dict(cfg, strategy_route_enabled=False)) == "default")
    check("T7 配置态已开启（对齐上游）", bool(cfg.get("strategy_route_enabled")) is True,
          f"val={cfg.get('strategy_route_enabled')}")
    on = dict(cfg, strategy_route_enabled=True)
    p = select_retrieval_profile("苹果 香蕉 果盘 主题", S0(), on)
    check("T7 门开高密度→entity_dense", p == "entity_dense", f"p={p}")
    p2 = select_retrieval_profile("从前有座山山里有座庙庙里有个老和尚在给小和尚讲故事讲的什么故事呢", S0(), on)
    check("T7 门开低密度→narrative", p2 == "narrative", f"p={p2}")
    check("T7 内置档参数", STRATEGY_PROFILE_DEFAULTS["entity_dense"]["stage2_relation_max_new"] == 12
          and STRATEGY_PROFILE_DEFAULTS["narrative"]["stage2_relation_max_new"] == 5)
    params = strategy_profile_params("entity_dense", dict(on, strategy={"entity_dense": {"stage2_relation_max_new": 99}}))
    check("T7 用户档覆盖内置", params.get("stage2_relation_max_new") == 99, f"params={params}")


def t9_d2_pure() -> None:
    from app.services.memory_retrieval_service import (
        RetrievalCandidate, _agpr_decay, _mmr_select, _rho_score, _text_similarity)

    check("T9 rho 公式", abs(_rho_score(0.5, 1.0) - 0.75) < 1e-6, f"got={_rho_score(0.5, 1.0)}")
    check("T9 agpr 衰减", abs(_agpr_decay(0.5, 0.8) - 0.2) < 1e-9)
    check("T9 bigram Jaccard", abs(_text_similarity("abc", "abd") - 1 / 3) < 1e-9,
          f"got={_text_similarity('abc', 'abd')}")
    a = RetrievalCandidate(id="a", tier="concept", content="苹果香蕉果盘", score=0.9, metadata={})
    b = RetrievalCandidate(id="b", tier="concept", content="苹果香蕉果盘", score=0.8, metadata={})
    c = RetrievalCandidate(id="c", tier="concept", content="完全不同的内容xyz", score=0.7, metadata={})
    sel = _mmr_select([a, b, c], k=2, lam=0.5)
    check("T9 MMR 去冗余优先多样性", [x.id for x in sel] == ["a", "c"], f"sel={[x.id for x in sel]}")
    sel1 = _mmr_select([a, b, c], k=1)
    check("T9 MMR k=1 取最高分", [x.id for x in sel1] == ["a"])


async def t9b_contradicts() -> None:
    """D2 contradicts 读侧降级行为（门内核）：互斥对只留高分端点。"""
    from app.services.memory_retrieval_service import RetrievalCandidate, _drop_contradicted
    from app.services.memory_cluster_service import create_relation
    async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as c:
        suffix = uuid.uuid4().hex[:8]
        uname = f"p2c_{suffix}"
        r = await c.post("/api/auth/register", json={"username": uname, "password": "test123"})
        assert r.status_code == 201, r.text
        r = await c.post("/api/auth/login", json={"username": uname, "password": "test123"})
        uid = r.json()["user"]["id"]
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    async with AsyncSessionLocal() as db:
        for _id, name in ((a, "T9互斥甲"), (b, "T9互斥乙")):
            await db.execute(text(
                "INSERT INTO memory_concepts (id, user_id, canonical_name, description_short, status, "
                "weight, stability, importance, importance_evaluated, source_trust, source_type, memory_type, "
                "activation_strength, recurrence_count, hot_forget_count, needs_review, "
                "created_at, updated_at, valid_from) "
                "VALUES (:id, :uid, :name, 't9', 'active', 0.5, 14, 0.5, FALSE, 'user_confirmed', "
                "'user_input', 'fact', 1.0, 0, 0, FALSE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"),
                {"id": _id, "uid": uid, "name": name})
        await create_relation(db, uid, a, b, "contradicts", weight=0.5)
        await db.commit()
        ca = RetrievalCandidate(id=a, tier="concept", content="甲", score=0.9,
                                metadata={"calibrated_score": 0.9})
        cb = RetrievalCandidate(id=b, tier="concept", content="乙", score=0.5,
                                metadata={"calibrated_score": 0.5})
        kept = await _drop_contradicted(db, uid, [ca, cb])
        check("T9b contradicts 保留高分端", [c.id for c in kept] == [a], f"kept={[c.id for c in kept]}")
        await db.execute(text("DELETE FROM concept_relations WHERE user_id = :uid"), {"uid": uid})
        await db.execute(text("DELETE FROM memory_concepts WHERE user_id = :uid"), {"uid": uid})
        await db.commit()


async def t8_d1() -> None:
    from app.services.memory_cluster_service import (
        build_deterministic_edges, create_relation, edge_read_whitelist, get_neighbors)
    cfg = get_config().memory_retrieval or {}
    check("T8 白名单门关→None", edge_read_whitelist(cfg) is None)
    wl = edge_read_whitelist(dict(cfg, edge_read_whitelist_enabled=True))
    check("T8 白名单语义类型轴", wl == ["causal", "temporal", "contradicts", "supports", "part_of"], f"wl={wl}")
    wl2 = edge_read_whitelist(dict(cfg, edge_read_whitelist_enabled=True, deterministic_edges_enabled=True))
    check("T8 白名单+确定性边→含 co_occurs", wl2 == ["causal", "temporal", "contradicts", "supports", "part_of", "co_occurs"],
          f"wl={wl2}")

    async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as c:
        suffix = uuid.uuid4().hex[:8]
        uname = f"p2_{suffix}"
        r = await c.post("/api/auth/register", json={"username": uname, "password": "test123"})
        assert r.status_code == 201, r.text
        r = await c.post("/api/auth/login", json={"username": uname, "password": "test123"})
        uid = r.json()["user"]["id"]
    a, b, u1 = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    async with AsyncSessionLocal() as db:
        for _id, name in ((a, "P2靶标甲"), (b, "P2靶标乙")):
            await db.execute(text(
                "INSERT INTO memory_concepts (id, user_id, canonical_name, description_short, status, "
                "weight, stability, importance, importance_evaluated, source_trust, source_type, memory_type, "
                "activation_strength, recurrence_count, hot_forget_count, needs_review, source_unit_ids, "
                "created_at, updated_at, valid_from) "
                "VALUES (:id, :uid, :name, 't8', 'active', 0.5, 14, 0.5, FALSE, 'user_confirmed', "
                "'user_input', 'fact', 1.0, 0, 0, FALSE, :u1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"),
                {"id": _id, "uid": uid, "name": name, "u1": f'["{u1}"]'})
        await db.commit()
        await create_relation(db, uid, a, b, "related_to", weight=0.5, edge_source="co_occurs")
        await db.commit()
        row = (await db.execute(text(
            "SELECT edge_source FROM concept_relations WHERE source_id = :s"), {"s": a})).fetchone()
        check("T8 edge_source 落库", row is not None and row[0] == "co_occurs", f"row={row}")
        await build_deterministic_edges(db, uid, [a, b], max_edges=20)
        await db.commit()
        n = (await db.execute(text(
            "SELECT COUNT(*) FROM concept_relations WHERE user_id = :uid AND edge_source = 'co_occurs'"),
            {"uid": uid})).scalar()
        check("T8 确定性边幂等（已有边不重复建）", n == 1, f"n={n}")
        neigh_keep = await get_neighbors(db, a, allowed_types=["related_to"])
        check("T8 白名单放行命中类型", len(neigh_keep) == 1 and neigh_keep[0]["relation_type"] == "related_to",
              f"neigh={neigh_keep}")
        neigh_drop = await get_neighbors(db, a, allowed_types=["causal"])
        check("T8 白名单过滤非命中类型", neigh_drop == [], f"neigh={neigh_drop}")
        # P/L/T 列
        await db.execute(text(
            "INSERT INTO memory_episodes (id, user_id, narrative, source_unit_ids, participants, locations, "
            "created_at, updated_at) VALUES (:id, :uid, 't8 事件', :u1, :p, :l, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"),
            {"id": str(uuid.uuid4()), "uid": uid, "u1": f'["{u1}"]', "p": '["张三"]', "l": '["北京"]'})
        await db.commit()
        r2 = (await db.execute(text(
            "SELECT participants, locations FROM memory_episodes WHERE user_id = :uid ORDER BY created_at DESC LIMIT 1"),
            {"uid": uid})).fetchone()
        check("T8 P/L 列可落库", r2 is not None and "张三" in (r2[0] or "") and "北京" in (r2[1] or ""), f"row={r2}")
        await db.execute(text("DELETE FROM concept_relations WHERE user_id = :uid"), {"uid": uid})
        await db.execute(text("DELETE FROM memory_episodes WHERE user_id = :uid"), {"uid": uid})
        await db.execute(text("DELETE FROM memory_concepts WHERE user_id = :uid"), {"uid": uid})
        await db.commit()


def t10_d3() -> None:
    from app.services.memory_consolidation_service import (
        _d3_fast_path_enabled, _merge_pair_action, _order_pairs_mst)
    cfg = get_config().memory_retrieval or {}
    check("T10 门默认关", _d3_fast_path_enabled() is False)
    saved = dict(cfg)
    cfg["merge_fast_path_enabled"] = True
    try:
        check("T10 门开可读（[memory.retrieval] 轴）", _d3_fast_path_enabled() is True)
    finally:
        cfg.pop("merge_fast_path_enabled", None)
    check("T10 灰区 fast", _merge_pair_action(0.01, 0.03, 0.15) == "fast")
    check("T10 灰区 llm", _merge_pair_action(0.05, 0.03, 0.15) == "llm")
    check("T10 灰区 skip", _merge_pair_action(0.20, 0.03, 0.15) == "skip")
    check("T10 NULL dist 保守 skip", _merge_pair_action(None, 0.03, 0.15) == "skip")
    pairs = [("x", "y", 0.5), ("a", "x", 0.1), ("b", "c", 0.2)]
    ordered = _order_pairs_mst(pairs, 0, 1, 2)
    check("T10 MST 低度数对优先", ordered[0][0] == "b" and ordered[0][1] == "c", f"ordered={ordered}")


def t11_helpers() -> None:
    from app.services.memory_retrieval_service import (
        _DEFAULT_USAGE_INSTRUCTION, _stage0_ceiling_ms)
    cfg = get_config().memory_retrieval or {}
    check("T11 stage0 硬顶默认 0（关）", _stage0_ceiling_ms(cfg) == 0)
    check("T11 stage0 硬顶可配", _stage0_ceiling_ms(dict(cfg, stage0_hard_ceiling_ms=50)) == 50)
    check("T11 E1 默认提示语 ≤60 字零数字", len(_DEFAULT_USAGE_INSTRUCTION) <= 60
          and not any(ch.isdigit() for ch in _DEFAULT_USAGE_INSTRUCTION),
          f"len={len(_DEFAULT_USAGE_INSTRUCTION)}")


async def t11_w8_link_expansion() -> None:
    """W8 unit→concept 链接扩候选行为：门开补缺（不改既有分）、门关零变化、来源标记。"""
    from app.services.memory_retrieval_service import RetrievalCandidate, _stage2b_link_expansion
    async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as c:
        suffix = uuid.uuid4().hex[:8]
        uname = f"p2w8_{suffix}"
        r = await c.post("/api/auth/register", json={"username": uname, "password": "test123"})
        assert r.status_code == 201, r.text
        r = await c.post("/api/auth/login", json={"username": uname, "password": "test123"})
        uid = r.json()["user"]["id"]
    u1, c1, c2 = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    async with AsyncSessionLocal() as db:
        for _id, name in ((c1, "W8已入候选"), (c2, "W8链接靶标")):
            await db.execute(text(
                "INSERT INTO memory_concepts (id, user_id, canonical_name, description_short, status, "
                "weight, stability, importance, importance_evaluated, source_trust, source_type, memory_type, "
                "activation_strength, recurrence_count, hot_forget_count, needs_review, source_unit_ids, "
                "created_at, updated_at, valid_from) "
                "VALUES (:id, :uid, :name, 'w8', 'active', 0.5, 14, 0.5, FALSE, 'user_confirmed', "
                "'user_input', 'fact', 1.0, 0, 0, FALSE, :u1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"),
                {"id": _id, "uid": uid, "name": name, "u1": f'["{u1}"]'})
        await db.commit()
        seed = RetrievalCandidate(id=u1, tier="subconscious", content="原文片段", score=0.8, metadata={})
        existing = RetrievalCandidate(id=c1, tier="concept", content="已入", score=0.9, metadata={"canonical_name": "W8已入候选"})
        off = await _stage2b_link_expansion(db, uid, [existing], [seed], [], enabled=False)
        check("W8 门关零变化", [c.id for c in off] == [c1])
        on = await _stage2b_link_expansion(db, uid, [existing], [seed], [], enabled=True)
        ids = [c.id for c in on]
        check("W8 门开补链接概念且不重复", c2 in ids and ids.count(c1) == 1, f"ids={ids}")
        linked = next(c for c in on if c.id == c2)
        check("W8 链接候选分/来源标记", abs(linked.score - 0.45) < 1e-6
              and linked.metadata.get("source") == "file_link_expansion",
              f"score={linked.score} src={linked.metadata.get('source')}")
        check("W8 既有候选分未被改写", abs(on[0].score - 0.9) < 1e-6, f"score={on[0].score}")
        await db.execute(text("DELETE FROM memory_concepts WHERE user_id = :uid"), {"uid": uid})
        await db.commit()


async def t12_agpr_grouping_e3() -> None:
    """遗留清理断言：AGPR 二跳传播 / assembly_grouping 分组包装 / E3 listwise。"""
    from app.core.config import get_config
    from app.services.memory_retrieval_service import (
        RetrievalCandidate, _build_injection_context, _stage2_description_expansion)
    from app.services.memory_cluster_service import create_relation
    from app.services.memory_listwise_verifier import _parse_keep_ids, verify_listwise
    from app.services.memory_retrieval_service import Stage0Result

    async with httpx.AsyncClient(base_url=BASE, timeout=60.0) as c:
        suffix = uuid.uuid4().hex[:8]
        uname = f"p2x_{suffix}"
        r = await c.post("/api/auth/register", json={"username": uname, "password": "test123"})
        assert r.status_code == 201, r.text
        r = await c.post("/api/auth/login", json={"username": uname, "password": "test123"})
        uid = r.json()["user"]["id"]
    a, b, d = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    async with AsyncSessionLocal() as db:
        for _id, name in ((a, "AGPR种子"), (b, "AGPR一跳"), (d, "AGPR二跳")):
            await db.execute(text(
                "INSERT INTO memory_concepts (id, user_id, canonical_name, description_short, status, "
                "weight, stability, importance, importance_evaluated, source_trust, source_type, memory_type, "
                "activation_strength, recurrence_count, hot_forget_count, needs_review, "
                "created_at, updated_at, valid_from) "
                "VALUES (:id, :uid, :name, 't12', 'active', 0.5, 14, 0.5, FALSE, 'user_confirmed', "
                "'user_input', 'fact', 1.0, 0, 0, FALSE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"),
                {"id": _id, "uid": uid, "name": name})
        await create_relation(db, uid, a, b, "related_to", weight=0.8)
        await create_relation(db, uid, b, d, "related_to", weight=0.8)
        await db.commit()
        cfg_mod = get_config()
        base_cfg = dict(cfg_mod.memory_retrieval or {})
        s0 = Stage0Result(keywords=["AGPR种子", "AGPR一跳"], query_type="general", include_expired=False)
        seed = RetrievalCandidate(id=a, tier="concept", content="种子", score=1.0,
                                  metadata={"canonical_name": "AGPR种子"})
        # 门关：一跳会进（relation_expansion 常规），二跳不进
        cfg_mod._config.setdefault("memory", {})
        cfg_mod._config["memory"]["retrieval"] = dict(base_cfg, agpr_enabled=False,
                                                      stage2_relation_expansion_enabled=True)
        try:
            off = await _stage2_description_expansion(db, uid, [seed], s0, "AGPR种子 查询词")
            check("AGPR 门关二跳不进", d not in [c.id for c in off], f"ids={[c.id for c in off]}")
            cfg_mod._config["memory"]["retrieval"] = dict(base_cfg, agpr_enabled=True,
                                                          stage2_relation_expansion_enabled=True)
            on = await _stage2_description_expansion(db, uid, [seed], s0, "AGPR种子 查询词")
            ids = {c.id: c for c in on}
            check("AGPR 门开二跳进入", d in ids, f"ids={list(ids)}")
            if d in ids:
                expected = float(ids[b].score) * 0.8 * 0.5
                check("AGPR 二跳分数=父分×边权×0.5", abs(ids[d].score - expected) < 1e-9
                      and ids[d].metadata.get("source") == "agpr_expansion",
                      f"score={ids[d].score} expected={expected}")
        finally:
            cfg_mod._config["memory"]["retrieval"] = base_cfg
        # grouping：_build_injection_context 门开 [相关记忆]，门关无
        c1 = RetrievalCandidate(id=a, tier="concept", content="甲内容", score=0.9,
                                metadata={"canonical_name": "AGPR种子", "calibrated_score": 0.9})
        e_id = str(uuid.uuid4())
        e1 = RetrievalCandidate(id=e_id, tier="episodic", content="事件内容", score=0.8,
                                metadata={"calibrated_score": 0.8, "valid_from": "2026-01-01",
                                          "narrative": "事件内容"})
        s1 = RetrievalCandidate(id=str(uuid.uuid4()), tier="subconscious", content="原文内容",
                                score=0.7, metadata={"calibrated_score": 0.7, "created_at": "2026-01-02"})
        cfg_mod._config["memory"]["retrieval"] = dict(base_cfg, assembly_grouping_enabled=False)
        try:
            ctx_off = await _build_injection_context(db, uid, [c1, e1, s1], s0, query_text="AGPR种子")
            check("grouping 门关无分组标记", "[相关记忆]" not in ctx_off, f"ctx={ctx_off[:80]!r}")
            cfg_mod._config["memory"]["retrieval"] = dict(base_cfg, assembly_grouping_enabled=True)
            ctx_on = await _build_injection_context(db, uid, [c1, e1, s1], s0, query_text="AGPR种子")
            # 概念段因入总览（overview_cids）被排除——episodic/subconscious 两级标签均应出现
            check("grouping 门开 [相关记忆] 包（事件+原文双标签）",
                  "[相关记忆]" in ctx_on and "[事件]" in ctx_on and "[原文]" in ctx_on,
                  f"ctx={ctx_on[:160]!r}")
        finally:
            cfg_mod._config["memory"]["retrieval"] = base_cfg
        await db.execute(text("DELETE FROM concept_relations WHERE user_id = :uid"), {"uid": uid})
        await db.execute(text("DELETE FROM memory_concepts WHERE user_id = :uid"), {"uid": uid})
        await db.commit()
    # E3
    check("E3 解析保留非 irrelevant", _parse_keep_ids(
        '{"items": [{"id": "x", "role": "relevant"}, {"id": "y", "role": "irrelevant"}]}', ["x", "y"]) == ["x"])
    check("E3 拒收候选外 id", _parse_keep_ids(
        '{"items": [{"id": "z", "role": "relevant"}]}', ["x"]) is None)
    check("E3 解析失败→None", _parse_keep_ids("not json", ["x"]) is None)
    out = await verify_listwise(uid, "q", [{"id": "x", "text": "t"}])
    check("E3 门关 fail-open None", out is None)


async def main() -> None:
    t7_strategy()
    t9_d2_pure()
    await t9b_contradicts()
    await t8_d1()
    t10_d3()
    t11_helpers()
    await t11_w8_link_expansion()
    await t12_agpr_grouping_e3()
    print(f"\n==== 结果: {passed} passed, {failed} failed ====")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
