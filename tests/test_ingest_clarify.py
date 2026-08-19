"""weave-mem 验收测试 3：新端点（ingest + clarifications/process）HTTP 工作流。

从 README 核心 API 表写出的测试用例：
1. POST /api/memory/ingest 未认证 401
2. POST /api/memory/ingest 缺 content 422
3. POST /api/memory/ingest 无 embedding provider → 503（§9.5 降级语义）
4. POST /api/memory/clarifications/process 未认证 401
5. POST /api/memory/clarifications/process 缺 user_message 422
6. POST /api/memory/clarifications/process 无信号词 → 200 detected=false
7. POST /api/memory/clarifications/process 含信号词（"其实不是"）→ 200 detected=true
   （clarification 字段：LLM 不可用时为 null，结构仍为 200）

运行：./.venv/bin/python tests/test_ingest_clarify.py
日志：tests/test_ingest_clarify.log（bash 重定向）
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
    uname = f"ingest_{suffix}"
    async with httpx.AsyncClient(base_url=BASE, timeout=60.0) as c:
        r = await c.post("/api/auth/register", json={"username": uname, "password": "test123"})
        check("register", r.status_code == 201, f"status={r.status_code}")
        r = await c.post("/api/auth/login", json={"username": uname, "password": "test123"})
        token = r.json()["access_token"]
        h = {"Authorization": f"Bearer {token}"}

        # 1. ingest 未认证 401
        r = await c.post("/api/memory/ingest", json={"content": "未认证测试"})
        check("ingest 未认证 401", r.status_code == 401, f"status={r.status_code}")

        # 2. ingest 缺 content 422
        r = await c.post("/api/memory/ingest", json={}, headers=h)
        check("ingest 缺 content 422", r.status_code == 422, f"status={r.status_code}")

        # 3. ingest 无 embedding provider → 503（当前环境未配置，与 recall mode:text 同源）
        r = await c.post("/api/memory/ingest",
                         json={"content": f"测试摄入内容{suffix}：用户偏好蓝色主题", "unit_kind": "message"},
                         headers=h)
        check("ingest 无 provider 503", r.status_code == 503, f"status={r.status_code} detail={r.text[:120]}")

        # 3b. ingest provider 已配置但请求失败 → 502（ASGI 同进程 patch 生效；
        #     token 为真实 HTTP 登录所得，JWT 跨进程有效；embedding 实调 127.0.0.1:1 失败返回 None）
        import app.api.memory as mem_mod
        from app.main import app
        from httpx import ASGITransport
        orig_cmb = mem_mod.get_config_memory_base
        mem_mod.get_config_memory_base = lambda: "http://127.0.0.1:1/v1"
        try:
            async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=60.0) as ac:
                r = await ac.post("/api/memory/ingest",
                                  json={"content": f"502 分支测试{suffix}", "unit_kind": "message"},
                                  headers=h)
        finally:
            mem_mod.get_config_memory_base = orig_cmb
        check("ingest provider 故障 502", r.status_code == 502, f"status={r.status_code} detail={r.text[:100]}")

        # 4. process 未认证 401
        r = await c.post("/api/memory/clarifications/process", json={"user_message": "其实不是这样"})
        check("process 未认证 401", r.status_code == 401, f"status={r.status_code}")

        # 5. process 缺 user_message 422
        r = await c.post("/api/memory/clarifications/process", json={}, headers=h)
        check("process 缺 user_message 422", r.status_code == 422, f"status={r.status_code}")

        # 6. process 无信号词 → 200 detected=false
        r = await c.post("/api/memory/clarifications/process",
                         json={"user_message": "今天天气不错"}, headers=h)
        body = r.json()
        check("process 无信号词 detected=false", r.status_code == 200 and body.get("detected") is False,
              f"status={r.status_code} body={body}")

        # 7. process 含信号词 → 200 detected=true
        r = await c.post("/api/memory/clarifications/process",
                         json={"user_message": "其实不是这样，我说的是蓝色主题"}, headers=h)
        body = r.json()
        check("process 信号词 detected=true", r.status_code == 200 and body.get("detected") is True,
              f"status={r.status_code} body={body}")
        check("process 响应含 clarification 键", "clarification" in body, f"keys={list(body.keys())}")

    print(f"\n==== 结果: {passed} passed, {failed} failed ====")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
