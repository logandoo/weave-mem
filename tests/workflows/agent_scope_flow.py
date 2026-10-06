"""A4.7b workflow: agent 作用域跨端点流（real-HTTP trace）。

注册 → alpha 写概念 → beta 写概念 → 共享写 → alpha/beta 列表隔离 →
alpha recall 不见 beta → 跨 agent 详情 404 → PAT(beta) 免 header →
alpha 台账归因 → trace 落盘 tests/workflows/agent_scope.trace.log。

运行：./.venv/bin/python tests/workflows/agent_scope_flow.py
"""
import asyncio
import sys
import uuid
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8202"
TRACE = Path(__file__).resolve().parent / "agent_scope.trace.log"


def log(line: str) -> None:
    print(line)
    with TRACE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


async def main() -> int:
    suffix = uuid.uuid4().hex[:8]
    failed = 0

    def step(name: str, ok: bool, detail: str = "") -> None:
        nonlocal failed
        mark = "OK " if ok else "FAIL"
        if not ok:
            failed += 1
        log(f"--- {name} [{mark}] {detail}")

    log(f"=== agent_scope_flow {suffix} ===")
    async with httpx.AsyncClient(base_url=BASE, timeout=60.0) as c:
        r = await c.post("/api/auth/register", json={"username": f"flow_{suffix}", "password": "test123"})
        step("register", r.status_code == 201, f"<<< {r.status_code}")
        r = await c.post("/api/auth/login", json={"username": f"flow_{suffix}", "password": "test123"})
        tok = r.json()["access_token"]
        h = {"Authorization": f"Bearer {tok}"}
        ha = {**h, "X-Agent-Id": "alpha"}
        hb = {**h, "X-Agent-Id": "beta"}
        log(f"<<< login {r.status_code} token=***")

        for i in range(10):
            await c.post("/api/memory/concepts", headers=h, json={
                "canonical_name": f"flow{suffix}预热{i}", "description_short": f"bg{i}"})
        r = await c.post("/api/memory/concepts", headers=ha, json={
            "canonical_name": f"flow{suffix}ALPHA", "description_short": "alpha private"})
        alpha_id = r.json()["id"]
        step("alpha write", r.status_code == 201, f"<<< {r.status_code} id={alpha_id[:8]}")
        r = await c.post("/api/memory/concepts", headers=hb, json={
            "canonical_name": f"flow{suffix}BETA", "description_short": "beta private"})
        beta_id = r.json()["id"]
        step("beta write", r.status_code == 201, f"<<< {r.status_code}")
        r = await c.post("/api/memory/concepts", headers=h, json={
            "canonical_name": f"flow{suffix}SHARED", "description_short": "shared"})
        shared_id = r.json()["id"]
        step("shared write", r.status_code == 201, f"<<< {r.status_code}")

        r = await c.get("/api/memory/concepts?limit=200", headers=ha)
        names = {x["canonical_name"] for x in r.json()["concepts"]}
        step("alpha list isolation", f"flow{suffix}ALPHA" in names
             and f"flow{suffix}SHARED" in names and f"flow{suffix}BETA" not in names,
             f"n={len(names)}")

        r = await c.post("/api/memory/recall?include_meta=true", headers=ha,
                         json={"query": f"flow{suffix}BETA"})
        body = r.json()
        step("alpha recall excludes beta",
             beta_id not in set((body.get("meta") or {}).get("memory_ids") or [])
             and f"flow{suffix}BETA" not in (body.get("context") or ""), "")

        r = await c.get(f"/api/memory/concepts/{beta_id}", headers=ha)
        step("cross-agent detail 404", r.status_code == 404, f"<<< {r.status_code}")

        r = await c.post("/api/auth/tokens", headers=h, json={"name": "flow-beta", "agent_id": "beta"})
        pat = r.json()["token"]
        step("PAT beta created", r.status_code == 200 and r.json().get("agent_id") == "beta", "")
        r = await c.get("/api/memory/concepts?limit=200", headers={"Authorization": f"Bearer {pat}"})
        names_p = {x["canonical_name"] for x in r.json()["concepts"]}
        step("PAT scope without header", f"flow{suffix}BETA" in names_p and f"flow{suffix}ALPHA" not in names_p, "")

        await c.post("/api/memory/recall", headers=ha, json={"query": f"flow{suffix}ALPHA"})
        rows = []
        for _ in range(20):
            r = await c.get("/api/memory/recall_log", headers=ha)
            rows = (r.json().get("items") or [])
            if any(x.get("agent_id") == "alpha" for x in rows):
                break
            await asyncio.sleep(0.25)
        step("recall log attributed", all(x.get("agent_id") != "beta" for x in rows)
             and any(x.get("agent_id") == "alpha" for x in rows), f"n={len(rows)}")

    log(f"=== RESULT: {'ALL OK' if failed == 0 else f'{failed} FAILED'} ===")
    return failed


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
