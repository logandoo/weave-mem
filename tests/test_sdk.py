"""weave-mem SDK wave 断言：weave-mem-client 发布包端到端（对活服务，经 SDK 方法）。

运行：./.venv/bin/python tests/test_sdk.py（服务需在跑；client 包需可导入）
"""
import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import httpx  # noqa: F401  （对照用）

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
    from weave_mem_client import MemoryClient, __version__

    suffix = uuid.uuid4().hex[:8]
    uname = f"sdk_{suffix}"
    async with MemoryClient(BASE, timeout=60.0) as c:
        # 鉴权面
        u = await c.register(uname, "test123")
        check("SDK register", u.get("username") == uname, f"u={u}")
        tok = await c.login(uname, "test123")
        check("SDK login 返回 token", bool(tok), f"tok={tok[:12]}...")
        me = await c.me()
        check("SDK me", me.get("username") == uname, f"me={me}")

        # PAT 面
        pat = await c.create_token(name="sdk-pat")
        check("SDK create_token", bool(pat.get("token") or pat.get("access_token") or pat.get("id")),
              f"keys={sorted(pat)[:6]}")
        toks = await c.list_tokens()
        check("SDK list_tokens", isinstance(toks, (list, dict)), f"type={type(toks).__name__}")

        # 概念面
        concept = await c.create_concept(canonical_name=f"SDK靶标{suffix}",
                                         description_short="sdk", importance=0.9)
        cid = concept.get("id")
        check("SDK create_concept", bool(cid), f"id={cid}")
        got = await c.get_concept(cid)
        check("SDK get_concept", got.get("id") == cid)
        lst = await c.list_concepts(limit=50)
        rows = lst.get("concepts", []) if isinstance(lst, dict) else lst
        check("SDK list_concepts 含新概念", any((it.get("id") == cid) for it in rows),
              f"type={type(lst).__name__} n={len(rows)}")

        # 召回/采纳/台账面
        rec = await c.recall(query=f"SDK靶标{suffix}", include_meta=True)
        check("SDK recall 结构", rec.get("mode") in ("text", "embedding") and "context" in rec,
              f"mode={rec.get('mode')}")
        ad = await c.adoption(injected_ids=[cid], answer_text=f"回答引用 SDK靶标{suffix}")
        check("SDK adoption", ad.get("adopted") == 1 and ad.get("matched") == [cid], f"ad={ad}")
        rl = await c.recall_log(limit=5)
        check("SDK recall_log", "items" in rl and "total" in rl, f"keys={sorted(rl)}")

        # 摄入/澄清/状态面
        try:
            await c.ingest(content="SDK 摄入测试内容", unit_kind="message")
            check("SDK ingest 响应结构", True)
        except Exception as e:
            check("SDK ingest 503/502 语义（无 provider）", "503" in str(e) or "502" in str(e), f"e={e}")
        cl = await c.list_clarifications()
        check("SDK list_clarifications", isinstance(cl, (list, dict)), f"type={type(cl).__name__}")
        st = await c.memory_status()
        check("SDK memory_status", st.get("status") == "ok" or "pgvector" in st, f"st={st}")
        hz = await c.healthz()
        check("SDK healthz（免鉴权）", hz.get("status") == "ok", f"hz={hz}")

        # 遗忘/擦除面
        fd = await c.forget_concept(cid)
        check("SDK forget_concept", isinstance(fd, dict), f"type={type(fd).__name__}")

    # 错误面：401 未认证（新客户端不带凭据）
    async with MemoryClient(BASE, timeout=30.0) as anon:
        try:
            await anon.list_concepts()
            check("SDK 未认证 401 语义", False, "expected raise")
        except Exception as e:
            check("SDK 未认证 401 语义", "401" in str(e), f"e={e}")
    check("SDK 包版本可读", bool(__version__), f"v={__version__}")

    # 自带头合并（双审 I1）：headers= 不再 TypeError，且鉴权头不被覆盖
    async with MemoryClient(BASE, username=uname, password="test123", timeout=30.0) as c2:
        body = await c2.request("GET", "/api/memory/status", headers={"X-Trace": "sdk"})
        check("SDK request 自带头合并", body.get("status") == "ok", f"body={body}")
        root = await c2.root()
        check("SDK root（第 32 路径）", isinstance(root, dict) and root, f"root={root}")

    print(f"\n==== 结果: {passed} passed, {failed} failed ====")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
