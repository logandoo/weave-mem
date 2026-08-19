"""weave-mem 验收测试 1：12 端点 HTTP 工作流（httpx 真实请求）。

覆盖：注册/登录 → concepts 空列表 → dreams → clarifications →
cost_governance status/reset（本人）→ DELETE 不存在 404 → forget 不存在 404 →
admin 迁移三端点（role=admin，经 psql 提升；普通用户 403）→
兼容端点 status / POST concepts / POST recall（mode:text）→
DELETE /all（GDPR，独立用户）→ /healthz pgvector。

运行：./.venv/bin/python tests/test_api.py
日志：tests/test_api.log（bash 重定向）
"""
import asyncio
import json
import subprocess
import sys
import uuid

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


async def main() -> None:
    suffix = uuid.uuid4().hex[:8]
    uname = f"memuser_{suffix}"
    async with httpx.AsyncClient(base_url=BASE, timeout=60.0) as c:
        # ---------- 1. 注册 / 登录 ----------
        r = await c.post("/api/auth/register", json={"username": uname, "password": "test123"})
        check("register", r.status_code == 201, f"status={r.status_code}")

        r = await c.post("/api/auth/login", json={"username": uname, "password": "test123"})
        check("login", r.status_code == 200 and "access_token" in r.json(), f"status={r.status_code}")
        token = r.json()["access_token"]
        uid = r.json()["user"]["id"]
        h = {"Authorization": f"Bearer {token}"}

        # ---------- 2. GET concepts（空列表）----------
        r = await c.get("/api/memory/concepts", headers=h)
        check("GET /concepts", r.status_code == 200 and r.json().get("concepts") == [],
              f"status={r.status_code} concepts={r.json().get('count')}")

        # ---------- 3. GET dreams ----------
        r = await c.get("/api/memory/dreams", headers=h)
        check("GET /dreams", r.status_code == 200 and r.json().get("dreams") == [],
              f"status={r.status_code}")

        # ---------- 4. GET clarifications ----------
        r = await c.get("/api/memory/clarifications", headers=h)
        check("GET /clarifications", r.status_code == 200 and r.json().get("clarifications") == [],
              f"status={r.status_code}")

        # ---------- 5. cost governance status ----------
        r = await c.get("/api/memory/cost_governance/status", headers=h)
        body = r.json()
        check("GET /cost_governance/status", r.status_code == 200 and isinstance(body, dict),
              f"status={r.status_code} keys={sorted(body.keys())}")

        # ---------- 6. PUT cost_governance reset（本人）----------
        r = await c.put(f"/api/memory/{uid}/cost_governance/reset", headers=h)
        check("PUT /cost_governance/reset", r.status_code == 200 and r.json().get("reset") == uid,
              f"status={r.status_code} body={r.json()}")

        # ---------- 7. DELETE concepts/{不存在id} → 404 ----------
        r = await c.delete("/api/memory/concepts/00000000-0000-0000-0000-000000000000", headers=h)
        check("DELETE /concepts/{missing} 404", r.status_code == 404, f"status={r.status_code}")

        # ---------- 8. POST concepts/{不存在id}/forget → 404 ----------
        r = await c.post("/api/memory/concepts/00000000-0000-0000-0000-000000000000/forget", headers=h)
        check("POST /concepts/{missing}/forget 404", r.status_code == 404, f"status={r.status_code}")

        # ---------- 8b. POST clarifications/{不存在id}/revert → 404 ----------
        r = await c.post("/api/memory/clarifications/00000000-0000-0000-0000-000000000000/revert", headers=h)
        check("POST /clarifications/{missing}/revert 404", r.status_code == 404, f"status={r.status_code}")

        # ---------- 9. admin 迁移端点（chatbot 鉴权=JWT + users.role=='admin'）----------
        aname = f"memadmin_{suffix}"
        r = await c.post("/api/auth/register", json={"username": aname, "password": "test123"})
        check("admin register", r.status_code == 201, f"status={r.status_code}")
        subprocess.run(
            PSQL + [f"UPDATE users SET role='admin' WHERE username='{aname}'"],
            check=True, capture_output=True,
        )
        r = await c.post("/api/auth/login", json={"username": aname, "password": "test123"})
        atoken = r.json()["access_token"]
        ah = {"Authorization": f"Bearer {atoken}"}

        r = await c.get("/api/admin/memory/migration/status", headers=ah)
        check("GET /admin/memory/migration/status", r.status_code == 200 and "users" in r.json(),
              f"status={r.status_code} users={len(r.json().get('users', []))}")

        r = await c.post("/api/admin/memory/migration/run", json={"dry_run": True}, headers=ah)
        check("POST /admin/memory/migration/run (dry_run)", r.status_code == 200 and "dry_run" in r.json(),
              f"status={r.status_code}")

        r = await c.post("/api/admin/memory/migration/rollback", json={}, headers=ah)
        check("POST /admin/memory/migration/rollback (无 user_id → 400)",
              r.status_code == 400, f"status={r.status_code}")

        r = await c.get("/api/admin/memory/migration/status", headers=h)
        check("普通用户访问 admin 端点 → 403", r.status_code == 403, f"status={r.status_code}")

        # ---------- 10. 兼容端点 /status ----------
        r = await c.get("/api/memory/status", headers=h)
        body = r.json()
        check("GET /api/memory/status", r.status_code == 200 and body.get("pgvector") is True,
              f"status={r.status_code} pgvector={body.get('pgvector')}")

        # ---------- 11. 兼容端点 POST /concepts（服务层写入）----------
        r = await c.post("/api/memory/concepts",
                         json={"canonical_name": f"验收概念{suffix}", "description_short": "api 测试"},
                         headers=h)
        check("POST /concepts", r.status_code == 201 and "id" in r.json(),
              f"status={r.status_code} id={r.json().get('id')}")

        # ---------- 12. 兼容端点 POST /recall（无 embedding → BM25/文本路径）----------
        r = await c.post("/api/memory/recall", json={"query": f"验收概念{suffix}"}, headers=h)
        body = r.json()
        check("POST /recall", r.status_code == 200 and body.get("mode") == "text",
              f"status={r.status_code} mode={body.get('mode')} ctx_len={len(body.get('context') or '')}")

        # ---------- 13. GDPR DELETE /all（独立用户）----------
        gname = f"memgdpr_{suffix}"
        r = await c.post("/api/auth/register", json={"username": gname, "password": "test123"})
        check("gdpr register", r.status_code == 201, f"status={r.status_code}")
        r = await c.post("/api/auth/login", json={"username": gname, "password": "test123"})
        gtoken = r.json()["access_token"]
        gh = {"Authorization": f"Bearer {gtoken}"}
        await c.post("/api/memory/concepts",
                     json={"canonical_name": "将被擦除", "description_short": "gdpr"}, headers=gh)
        r = await c.delete("/api/memory/all", headers=gh)
        check("DELETE /all", r.status_code == 200 and r.json().get("deleted") == "all",
              f"status={r.status_code} body={r.json()}")
        r = await c.get("/api/memory/concepts", headers=gh)
        check("DELETE /all 后 concepts 为空", r.status_code == 200 and r.json().get("count") == 0,
              f"count={r.json().get('count')}")

        # ---------- 14. /healthz ----------
        r = await c.get("/healthz")
        check("/healthz", r.status_code == 200 and r.json().get("pgvector") is True,
              f"status={r.status_code} pgvector={r.json().get('pgvector')}")

    print(f"\n==== 结果: {passed} passed, {failed} failed ====")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
