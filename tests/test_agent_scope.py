"""weave-mem 验收：agent 身份命名空间（Wave 1，agent_id 贯通）。

A. 纯函数单元（services/memory_scope）
B. HTTP 作用域工作流（准则 1-7，真实 HTTP + psql 落库核对）
C. 服务层晋升作用域（准则 5 落库 + 晋升继承；mock embedding/LLM，
   test_full_chain 同款——本环境无 embedding provider，HTTP ingest 走 503）

运行：./.venv/bin/python tests/test_agent_scope.py
日志：tests/test_agent_scope.log（bash 重定向）
"""
import asyncio
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import httpx

BASE = "http://127.0.0.1:8202"
PSQL = ["psql", "-h", "127.0.0.1", "-U", "postgres", "-d", "weave_mem", "-t", "-A", "-c"]

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


def psql(sql: str) -> str:
    r = subprocess.run(PSQL + [sql], capture_output=True, text=True)
    return (r.stdout or "").strip()


# ---------- A. 纯函数 ----------
def unit_checks() -> None:
    try:
        from app.services import memory_scope as ms
    except Exception as exc:  # RED: module absent
        check("memory_scope 模块可导入", False, f"exc={exc!r}")
        return

    check("normalize: 空→None", ms.normalize_agent_id(None) is None
          and ms.normalize_agent_id("") is None and ms.normalize_agent_id("  ") is None)
    check("normalize: 合法 key 保留", ms.normalize_agent_id("claude-code.v1_x") == "claude-code.v1_x")
    for bad in ["bad agent!", "a" * 65, "中文", "a/b", "a:b"]:
        try:
            ms.normalize_agent_id(bad)
            check(f"normalize: 非法 {bad[:12]!r} 抛错", False, "no raise")
        except ValueError:
            check(f"normalize: 非法 {bad[:12]!r} 抛错", True)

    check("scope_sql: 有 agent", ms.agent_scope_sql("alpha") == "AND (agent_id IS NULL OR agent_id = :agent_scope)")
    check("scope_sql: 无 agent", ms.agent_scope_sql(None) == "AND agent_id IS NULL")
    check("scope_sql: 别名列", ms.agent_scope_sql("alpha", "c.agent_id") == "AND (c.agent_id IS NULL OR c.agent_id = :agent_scope)")
    check("scope_params: 有 agent", ms.agent_scope_params("alpha") == {"agent_scope": "alpha"})
    check("scope_params: 无 agent", ms.agent_scope_params(None) == {})


class FakeLLM:
    def __init__(self, payload: dict):
        self._payload = payload

    async def complete_chat(self, messages, **kwargs):
        return json.dumps(self._payload, ensure_ascii=False)


async def service_layer_checks(uname: str, uid: str) -> None:
    """C 段：ingest 落 agent_id/conversation_id + 晋升继承（mock embedding/LLM）。"""
    from app.db.database import AsyncSessionLocal
    from app.services import memory_embedding_service as mes
    from app.services import memory_llm_factory as mlf
    import app.services.memory_subconscious_service as mss
    from app.core.config import get_config
    from sqlalchemy import text

    _dim = int(get_config().memory.get("embedding_dim", 1536))
    orig_embed_mss = mss.embed_text
    orig_neighbors = mes.find_neighbors_for_unit
    orig_llm = mlf._memory_llm

    async def fake_embed(text):
        return [0.1] * _dim

    async def fake_neighbors(db, user_id, emb, unit_id, created_at, top_k=5, agent_id=None):
        result = await db.execute(
            text("SELECT id, raw_text FROM subconscious_log WHERE user_id = :uid AND id != :uid2 AND promoted = FALSE"),
            {"uid": user_id, "uid2": unit_id},
        )
        rows = result.fetchall()[:top_k]
        return [{"id": r[0], "raw_text": r[1], "similarity": 0.9} for r in rows]

    kw = f"scope{kwd_suffix}"
    fake_payload = {
        "episode": {"narrative": f"{kw}自动提炼的情节", "participants": [], "locations": []},
        "concepts": [{
            "canonical_name": f"{kw}自动概念",
            "description_short": f"关于{kw}的自动概念",
            "description_full": f"关于{kw}的自动概念详情",
            "aliases": [], "source_trust": "user_stated",
            "memory_type": "semantic", "importance": 0.6,
        }],
    }

    mss.embed_text = fake_embed
    mes.find_neighbors_for_unit = fake_neighbors
    mlf._memory_llm = lambda *a, **k: FakeLLM(fake_payload)
    try:
        async with AsyncSessionLocal() as db:
            unit_id = await mss.ingest_raw_unit(
                db, uid, "message", f"{kw}单元内容一", ["claude-code:session1:1"],
                agent_id="claude-code", conversation_id=f"cc-{kwd_suffix}",
            )
            await db.commit()
            row = psql(f"SELECT agent_id, conversation_id FROM subconscious_log WHERE id='{unit_id}'")
            check("ingest 落 agent_id+conversation_id", row == f"claude-code|cc-{kwd_suffix}", f"row={row!r}")

            # 补足同 agent 邻居以满足复发计数（count_threshold=3 → 头单元需 3 邻居）
            for i in range(3):
                await mss.ingest_raw_unit(db, uid, "message", f"{kw}单元内容{i + 2}", [],
                                          agent_id="claude-code", conversation_id=f"cc-{kwd_suffix}")
            await db.commit()
            promoted = await mss.scan_recurrence(db, uid, agent_id="claude-code")
            await db.commit()
            check("scan_recurrence 晋升 ≥1", promoted >= 1, f"promoted={promoted}")
            got = psql(f"SELECT agent_id, COUNT(*) FROM memory_concepts WHERE user_id='{uid}' AND canonical_name LIKE '{kw}%' GROUP BY agent_id")
            check("晋升概念继承 agent_id", got == "claude-code|1", f"row={got!r}")

            # 反向：beta 复发扫描不得晋升 alpha 单元
            promoted_beta = await mss.scan_recurrence(db, uid, agent_id="beta")
            await db.commit()
            check("beta 扫描不见 alpha 单元", promoted_beta == 0, f"promoted={promoted_beta}")

            # consolidation 单作用域：alpha 运行只动 alpha UAS 行；共享行水位不动
            from app.services.memory_consolidation_service import run_consolidation
            r = await run_consolidation(db, uid, agent_id="alpha")
            await db.commit()
            check("consolidation(alpha) 完成", isinstance(r, dict) and ("status" in r or "dedup_merged" in r),
                  f"result={r}")
            rows = psql(
                f"SELECT COALESCE(agent_id,'<null>')||':'||(last_consolidation_at IS NOT NULL) "
                f"FROM user_agent_states WHERE user_id='{uid}' ORDER BY (agent_id IS NULL) DESC, agent_id")
            check("consolidation 水位仅落 alpha 行", "alpha:true" in rows and "<null>:false" in rows, f"rows={rows!r}")

            # 澄清跨 agent 不可见（get_recent_clarifications 作用域）
            from app.services.memory_clarification_service import (
                get_recent_clarifications, process_clarification)
            mlf._memory_llm = lambda *a, **k: FakeLLM({
                "is_correction": True, "correction_type": "negate",
                "affected_concept_ids": [], "new_description": "",
                "confidence": 0.95,
            })
            await process_clarification(db, uid, f"{kw}beta专属澄清内容", agent_id="beta")
            await db.commit()
            clar_alpha = await get_recent_clarifications(db, uid, days=3, agent_id="alpha")
            clar_beta = await get_recent_clarifications(db, uid, days=3, agent_id="beta")
            check("澄清不串 agent",
                  all(kw not in (x.get("original_text") or "") for x in clar_alpha)
                  and any(kw in (x.get("original_text") or "") for x in clar_beta),
                  f"alpha_rows={len(clar_alpha)} beta_rows={len(clar_beta)}")
    finally:
        mss.embed_text = orig_embed_mss
        mes.find_neighbors_for_unit = orig_neighbors
        mlf._memory_llm = orig_llm


async def main() -> None:
    global kwd_suffix
    kwd_suffix = uuid.uuid4().hex[:8]
    unit_checks()

    suffix = uuid.uuid4().hex[:8]
    uname = f"scope_{suffix}"
    async with httpx.AsyncClient(base_url=BASE, timeout=60.0) as c:
        r = await c.post("/api/auth/register", json={"username": uname, "password": "test123"})
        check("注册", r.status_code == 201, f"status={r.status_code}")
        r = await c.post("/api/auth/login", json={"username": uname, "password": "test123"})
        token = r.json()["access_token"]
        uid = r.json()["user"]["id"]
        h = {"Authorization": f"Bearer {token}"}
        ha = {**h, "X-Agent-Id": "alpha"}
        hb = {**h, "X-Agent-Id": "beta"}

        # 预热：≥10 条共享概念越过冷启动阈值（recall 走正常管线）
        for i in range(10):
            r = await c.post("/api/memory/concepts", headers=h, json={
                "canonical_name": f"预热{suffix}主题{i}号",
                "description_short": f"预热{suffix}第{i}个背景",
            })
        check("预热 10 条 201", r.status_code == 201, f"status={r.status_code}")

        # 准则 1：写入 stamp
        r = await c.post("/api/memory/concepts", headers=ha, json={
            "canonical_name": f"{kwd_suffix}ALPHA标记", "description_short": f"{kwd_suffix}alpha 私有"})
        check("alpha 写概念 201", r.status_code == 201, f"status={r.status_code}")
        alpha_id = r.json()["id"]
        r = await c.post("/api/memory/concepts", headers=hb, json={
            "canonical_name": f"{kwd_suffix}BETA标记", "description_short": f"{kwd_suffix}beta 私有"})
        beta_id = r.json()["id"]
        r = await c.post("/api/memory/concepts", headers=h, json={
            "canonical_name": f"{kwd_suffix}SHARED标记", "description_short": f"{kwd_suffix}共享"})
        shared_id = r.json()["id"]
        check("agent_id 落库（alpha/beta/NULL）",
              psql(f"SELECT agent_id FROM memory_concepts WHERE id='{alpha_id}'") == "alpha"
              and psql(f"SELECT agent_id FROM memory_concepts WHERE id='{beta_id}'") == "beta"
              and psql(f"SELECT agent_id FROM memory_concepts WHERE id='{shared_id}'") == "",
              f"a={psql(f'SELECT agent_id FROM memory_concepts WHERE id={chr(39)}{alpha_id}{chr(39)}')}")

        # 准则 2：列表作用域
        r = await c.get("/api/memory/concepts?limit=200", headers=ha)
        names_a = {x["canonical_name"] for x in r.json()["concepts"]}
        r = await c.get("/api/memory/concepts?limit=200", headers=hb)
        names_b = {x["canonical_name"] for x in r.json()["concepts"]}
        r = await c.get("/api/memory/concepts?limit=200", headers=h)
        names_n = {x["canonical_name"] for x in r.json()["concepts"]}
        check("alpha 列表含 alpha+共享、不含 beta",
              f"{kwd_suffix}ALPHA标记" in names_a and f"{kwd_suffix}SHARED标记" in names_a
              and f"{kwd_suffix}BETA标记" not in names_a)
        check("beta 列表对称", f"{kwd_suffix}BETA标记" in names_b and f"{kwd_suffix}ALPHA标记" not in names_b)
        check("无 agent 列表仅共享",
              f"{kwd_suffix}SHARED标记" in names_n and f"{kwd_suffix}ALPHA标记" not in names_n
              and f"{kwd_suffix}BETA标记" not in names_n)

        # 准则 3：recall 作用域（可见性通道 = 注入 context 或 meta.memory_ids）
        r = await c.post("/api/memory/recall?include_meta=true", headers=ha,
                         json={"query": f"{kwd_suffix}ALPHA标记"})
        body_own = r.json()
        ids_own = set((body_own.get("meta") or {}).get("memory_ids") or [])
        check("alpha recall 可见自身私有", alpha_id in ids_own or f"{kwd_suffix}ALPHA标记" in (body_own.get("context") or ""),
              f"ids={len(ids_own)}")
        r = await c.post("/api/memory/recall?include_meta=true", headers=ha,
                         json={"query": f"{kwd_suffix}SHARED标记"})
        body_a = r.json()
        meta = (body_a.get("meta") or {})
        ids_a = set(meta.get("memory_ids") or [])
        check("alpha recall 可见共享", shared_id in ids_a or f"{kwd_suffix}SHARED标记" in (body_a.get("context") or ""),
              f"ids={len(ids_a)} ctx_has={(body_a.get('context') or '').find(kwd_suffix) >= 0} status={r.status_code}")
        r = await c.post("/api/memory/recall?include_meta=true", headers=ha,
                         json={"query": f"{kwd_suffix}BETA标记"})
        body_b = r.json()
        meta = (body_b.get("meta") or {})
        check("alpha recall 不见 beta", beta_id not in set(meta.get("memory_ids") or [])
              and f"{kwd_suffix}BETA标记" not in (body_b.get("context") or ""))

        # 准则 7：跨 agent 详情/删除/遗忘 404
        check("alpha 读 beta 详情 404", (await c.get(f"/api/memory/concepts/{beta_id}", headers=ha)).status_code == 404)
        check("alpha 遗忘 beta 404", (await c.post(f"/api/memory/concepts/{beta_id}/forget", headers=ha)).status_code == 404)
        check("alpha 删除 beta 404", (await c.delete(f"/api/memory/concepts/{beta_id}", headers=ha)).status_code == 404)

        # 准则 4：agent 绑定 PAT
        r = await c.post("/api/auth/tokens", headers=h, json={"name": "beta-pat", "agent_id": "beta"})
        check("创建 beta 绑定 PAT", r.status_code == 200 and r.json().get("agent_id") == "beta", f"body={r.text[:120]}")
        beta_pat = r.json()["token"]
        hp = {"Authorization": f"Bearer {beta_pat}"}
        r = await c.get("/api/memory/concepts?limit=200", headers=hp)
        names_p = {x["canonical_name"] for x in r.json()["concepts"]}
        check("PAT 免 header 按 beta 作用域", f"{kwd_suffix}BETA标记" in names_p and f"{kwd_suffix}ALPHA标记" not in names_p)
        check("PAT 列表含 agent_id 归因",
              psql(f"SELECT agent_id FROM personal_access_tokens WHERE user_id='{uid}' AND agent_id='beta'") == "beta")

        # 准则 5：非法 agent key 422
        r = await c.post("/api/memory/concepts", headers={**h, "X-Agent-Id": "bad agent!"},
                         json={"canonical_name": "x", "description_short": "y"})
        check("非法 agent key 写 422", r.status_code == 422, f"status={r.status_code}")
        r = await c.post("/api/memory/ingest", headers={**h, "X-Agent-Id": "bad agent!"},
                         json={"content": "合法内容但 agent 非法"})
        check("非法 agent key ingest 422", r.status_code == 422, f"status={r.status_code}")

        # 准则 6：recall_log 归因与作用域
        # 触发 alpha / beta 各一次召回（fire-and-forget 台账），轮询落库
        await c.post("/api/memory/recall", headers=ha, json={"query": f"{kwd_suffix}ALPHA标记"})
        await c.post("/api/memory/recall", headers=hb, json={"query": f"{kwd_suffix}BETA标记"})
        rows = ""
        for _ in range(20):
            rows = psql(f"SELECT agent_id, COUNT(*) FROM memory_recall_log WHERE user_id='{uid}' GROUP BY agent_id ORDER BY agent_id")
            if "alpha" in rows and "beta" in rows:
                break
            await asyncio.sleep(0.25)
        check("台账按 agent 落库", "alpha" in rows and "beta" in rows, f"rows={rows!r}")
        r = await c.get("/api/memory/recall_log", headers=ha)
        items = r.json().get("items") or []
        check("alpha 台账列表无 beta 行", all(x.get("agent_id") != "beta" for x in items)
              and any(x.get("agent_id") == "alpha" for x in items),
              f"n={len(items)} total={r.json().get('total')}")

        # C 段：服务层晋升作用域
        await service_layer_checks(uname, uid)


kwd_suffix = ""
if __name__ == "__main__":
    asyncio.run(main())
    print(f"\nRESULT: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
