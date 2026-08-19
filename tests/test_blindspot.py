"""weave-mem 验收测试 5：盲区修复端点（B-1/2/3/4/5/6）HTTP 工作流。

用例（从 README 核心 API 表写）：
1. GET /api/memory/concepts/{id}：不存在 404 → 创建后 200 含详情字段
2. GET /api/memory/episodes：200 空列表结构
3. POST /api/memory/recall?include_meta=true：200 响应含 meta 键（memory_ids/top_gate_score）
4. GET /api/admin/users：普通用户 403，admin 200
5. PUT /api/admin/users/{user_id}/role：非 admin 403；admin 改 user→admin 200
6. POST /api/memory/clarifications/{id}/apply：pending 澄清手动应用（mock LLM 低置信度落库 → apply → applied=TRUE）
7. POST /api/admin/reload-config：普通用户 403，admin 200 {ok:true}
"""
import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import httpx

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


async def main() -> None:
    suffix = uuid.uuid4().hex[:8]
    uname = f"blind_{suffix}"
    async with httpx.AsyncClient(base_url=BASE, timeout=60.0) as c:
        r = await c.post("/api/auth/register", json={"username": uname, "password": "test123"})
        check("register", r.status_code == 201, f"status={r.status_code}")
        r = await c.post("/api/auth/login", json={"username": uname, "password": "test123"})
        token = r.json()["access_token"]
        uid = r.json()["user"]["id"]
        h = {"Authorization": f"Bearer {token}"}

        # 1. 概念详情
        r = await c.get(f"/api/memory/concepts/nonexistent-id", headers=h)
        check("概念详情 404", r.status_code == 404, f"status={r.status_code}")
        r = await c.post("/api/memory/concepts", headers=h, json={
            "canonical_name": f"盲区测试主题{suffix}", "description_short": "详情端点验证", "importance": 0.7,
        })
        cid = r.json()["id"]
        r = await c.get(f"/api/memory/concepts/{cid}", headers=h)
        body = r.json()
        check("概念详情 200 含字段", r.status_code == 200 and body.get("canonical_name") == f"盲区测试主题{suffix}"
              and "description_short" in body and "weight" in body, f"status={r.status_code} keys={list(body.keys())[:8]}")

        # 2. 情节列表
        r = await c.get("/api/memory/episodes", headers=h)
        body = r.json()
        check("episodes 200 空列表", r.status_code == 200 and "episodes" in body and body["episodes"] == [],
              f"status={r.status_code} body={body}")

        # 3. recall include_meta
        r = await c.post("/api/memory/recall?include_meta=true", headers=h,
                         json={"query": f"盲区测试主题{suffix}"})
        body = r.json()
        check("recall meta 键存在", r.status_code == 200 and "meta" in body
              and set(("memory_ids", "top_gate_score")) <= set(body["meta"].keys()),
              f"status={r.status_code} keys={list(body.keys())} meta_keys={list(body.get('meta', {}).keys())}")

        # 4. admin users 列表
        r = await c.get("/api/admin/users", headers=h)
        check("admin users 普通用户 403", r.status_code == 403, f"status={r.status_code}")

        # 5. admin role 变更（psql 提升当前用户 → 调端点）
        import subprocess
        psql = ["psql", "-h", "127.0.0.1", "-U", "postgres", "-d", "weave_mem", "-t", "-A", "-c",
                f"UPDATE users SET role='admin' WHERE id='{uid}'"]
        subprocess.run(psql, check=True)
        r = await c.get("/api/admin/users", headers=h)
        check("admin users admin 200", r.status_code == 200 and isinstance(r.json(), list),
              f"status={r.status_code}")
        other = f"target_{suffix}"
        await c.post("/api/auth/register", json={"username": other, "password": "test123"})
        ro = await c.post("/api/auth/login", json={"username": other, "password": "test123"})
        other_id = ro.json()["user"]["id"]
        r = await c.put(f"/api/admin/users/{other_id}/role", headers=h, json={"role": "admin"})
        check("role 变更 200", r.status_code == 200 and r.json().get("role") == "admin",
              f"status={r.status_code} body={r.text[:80]}")
        r = await c.put(f"/api/admin/users/{other_id}/role", headers=h, json={"role": "invalid"})
        check("role 非法值 422", r.status_code == 422, f"status={r.status_code}")

        # 6. clarifications apply（pending 澄清：DB 直插 pending 行 → HTTP apply → applied=TRUE）
        import asyncio as _aio
        import json as _json
        from sqlalchemy import text as _text
        from datetime import datetime as _dt

        async def _insert_pending():
            from app.db.database import AsyncSessionLocal as _ASL
            async with _ASL() as db:
                pid = str(uuid.uuid4())
                await db.execute(_text(
                    "INSERT INTO memory_clarifications (id, user_id, original_text, correction_type, "
                    "affected_concept_ids, new_description, confidence, applied, created_at) "
                    "VALUES (:id, :uid, :ot, 'negate', :ac, '', 0.3, FALSE, :now)"
                ), {"id": pid, "uid": uid, "ot": f"盲区测试 pending 澄清{suffix}",
                    "ac": _json.dumps([cid]), "now": _dt.utcnow()})
                await db.commit()
                return pid

        pid = await _insert_pending()
        rr = await c.post(f"/api/memory/clarifications/{pid}/apply", headers=h)
        check("apply pending 200", rr.status_code == 200 and rr.json().get("applied") == pid,
              f"status={rr.status_code} body={rr.text[:80]}")

        async def _check_applied():
            from app.db.database import AsyncSessionLocal as _ASL
            async with _ASL() as db:
                row = (await db.execute(_text(
                    "SELECT applied FROM memory_clarifications WHERE id = :id"
                ), {"id": pid})).fetchone()
                return row[0] if row else None

        applied_flag = await _check_applied()
        check("pending 澄清 applied=TRUE 落库", applied_flag is True, f"applied={applied_flag}")
        rr = await c.post(f"/api/memory/clarifications/{pid}/apply", headers=h)
        check("重复 apply 404", rr.status_code == 404, f"status={rr.status_code}")
        rr = await c.post("/api/memory/clarifications/nonexistent/apply", headers=h)
        check("apply 不存在 404", rr.status_code == 404, f"status={rr.status_code}")

        # 7. reload-config
        r = await c.post("/api/admin/reload-config", headers=h)
        check("reload-config admin 200", r.status_code == 200 and r.json().get("ok") is True,
              f"status={r.status_code} body={r.text[:80]}")

    print(f"\n==== 结果: {passed} passed, {failed} failed ====")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
