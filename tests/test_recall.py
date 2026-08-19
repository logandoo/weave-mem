"""weave-mem 验收测试 2：召回管线（无 embedding provider → BM25/文本路径）。

流程：注册独立用户 → 经 POST /concepts（服务层）写入 8 个概念 →
POST /recall 断言 mode=="text" 且 context 命中写入的概念 →
直接调用服务层 retrieve_and_build_context（内存管线可运行性证明，
chatbot 的 recall 是内部服务，无 HTTP 端点）→ 检查 BM25 命中。

运行：./.venv/bin/python tests/test_recall.py
日志：tests/test_recall.log（bash 重定向）
"""
import asyncio
import sys
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

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
    uname = f"recall_{suffix}"
    keyword = f"recallkws{suffix}"

    async with httpx.AsyncClient(base_url=BASE, timeout=60.0) as c:
        r = await c.post("/api/auth/register", json={"username": uname, "password": "test123"})
        check("register", r.status_code == 201, f"status={r.status_code}")
        r = await c.post("/api/auth/login", json={"username": uname, "password": "test123"})
        token = r.json()["access_token"]
        uid = r.json()["user"]["id"]
        h = {"Authorization": f"Bearer {token}"}

        # 写入 8 个概念（>5 越过冷启动回退，确保进入 Stage 1 BM25 检索）
        names = []
        for i in range(8):
            name = f"{keyword}主题{i}号"
            names.append(name)
            r = await c.post("/api/memory/concepts",
                             json={"canonical_name": name,
                                   "description_short": f"关于{keyword}的第{i}个偏好"},
                             headers=h)
            check(f"POST /concepts #{i}", r.status_code == 201, f"status={r.status_code} name={name}")

        # HTTP 召回端点
        r = await c.post("/api/memory/recall", json={"query": f"{keyword} 主题"}, headers=h)
        body = r.json()
        check("POST /recall mode=text", r.status_code == 200 and body.get("mode") == "text",
              f"status={r.status_code} mode={body.get('mode')}")
        ctx = body.get("context") or ""
        hit = any(n in ctx for n in names[:4])
        check("recall context 命中写入概念（BM25 词法路径）", len(ctx) > 0 and hit,
              f"ctx_len={len(ctx)} hit={hit}")

        # 直接调用服务层检索函数（chatbot recall 为内部服务，无 HTTP 端点）
        # 证明检索管线本身可运行：retrieve_and_build_context 返回注入上下文
        from app.db.database import AsyncSessionLocal
        from app.services.memory_retrieval_service import retrieve_and_build_context

        async with AsyncSessionLocal() as db:
            ctx2 = await retrieve_and_build_context(
                db, uid,
                [{"role": "user", "content": f"用户偏好 {keyword} 主题"}],
            )
            check("服务层 retrieve_and_build_context 可运行", isinstance(ctx2, str) and len(ctx2) > 0,
                  f"ctx_len={len(ctx2)}")
            hit2 = any(n in ctx2 for n in names[:4])
            check("服务层召回命中 BM25 概念", hit2, f"hit={hit2}")

    print(f"\n==== 结果: {passed} passed, {failed} failed ====")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
