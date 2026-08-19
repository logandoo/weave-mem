"""weave-mem 验收测试 7：个人访问令牌（PAT）——创建/列表/认证/撤销（memos 借鉴）。

用例（PAT 设计见 README 核心 API 表）：
1. JWT 登录后 POST /api/auth/tokens 创建 → 200 返回明文 token（wm_ 前缀）且响应不含 token_hash
2. GET /api/auth/tokens 列表含新 token（name/created_at，无明文）
3. PAT 作为 Bearer 调 /api/memory/status → 200（双通道认证生效）
4. DELETE /api/auth/tokens/{id} 撤销 → 200
5. 撤销后 PAT 调 /api/memory/status → 401
6. DB 验证：token_hash 为 sha256 十六进制，明文 token 不在库中
7. 未认证创建 token → 401
"""
import asyncio
import hashlib
import sys
import uuid

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
    uname = f"pat_{suffix}"
    async with httpx.AsyncClient(base_url=BASE, timeout=60.0) as c:
        r = await c.post("/api/auth/register", json={"username": uname, "password": "test123"})
        check("register", r.status_code == 201, f"status={r.status_code}")
        r = await c.post("/api/auth/login", json={"username": uname, "password": "test123"})
        token = r.json()["access_token"]
        h = {"Authorization": f"Bearer {token}"}

        # 7. 未认证创建 → 401
        r = await c.post("/api/auth/tokens", json={"name": "x"})
        check("未认证创建 401", r.status_code == 401, f"status={r.status_code}")

        # 1. 创建 PAT
        r = await c.post("/api/auth/tokens", headers=h, json={"name": f"test-pat-{suffix}"})
        body = r.json()
        pat = body.get("token", "")
        check("创建 200 返回明文", r.status_code == 200 and pat.startswith("wm_") and "token_hash" not in body,
              f"status={r.status_code} prefix={pat[:6]} keys={list(body.keys())}")
        tid = body.get("id")

        # 2. 列表
        r = await c.get("/api/auth/tokens", headers=h)
        items = r.json()
        mine = [x for x in items if x.get("id") == tid]
        check("列表含新 token", r.status_code == 200 and len(mine) == 1 and "token" not in str(items),
              f"status={r.status_code} count={len(items)}")

        # 3. PAT 认证生效
        ph = {"Authorization": f"Bearer {pat}"}
        r = await c.get("/api/memory/status", headers=ph)
        check("PAT 调 /status 200", r.status_code == 200, f"status={r.status_code}")

        # 4. 撤销
        r = await c.delete(f"/api/auth/tokens/{tid}", headers=h)
        check("撤销 200", r.status_code == 200, f"status={r.status_code}")

        # 5. 撤销后 401
        r = await c.get("/api/memory/status", headers=ph)
        check("撤销后 401", r.status_code == 401, f"status={r.status_code}")

        # 6. DB 哈希验证
        from pathlib import Path
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
        from app.db.database import AsyncSessionLocal
        from sqlalchemy import text as _t
        async with AsyncSessionLocal() as db:
            row = (await db.execute(_t(
                "SELECT token_hash, revoked_at FROM personal_access_tokens WHERE id = :id"
            ), {"id": tid})).fetchone()
        check("token_hash 为 sha256 且明文不落库", row is not None
              and len(row[0]) == 64 and pat not in row[0] and row[1] is not None,
              f"hash_len={len(row[0]) if row else None} revoked={row[1] if row else None}")

    print(f"\n==== 结果: {passed} passed, {failed} failed ====")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
