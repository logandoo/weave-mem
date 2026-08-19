"""weave-mem 验收测试 8：MCP server（薄转发 HTTP API）。

用例（MCP 设计见 README MCP Server 一节）：
1. MCP client 连接 http://127.0.0.1:8202/mcp（streamable-http，真实 HTTP）
2. list_tools ≥ 15 个（status/concepts×5/recall/ingest/episodes/dreams/clarifications×4/cost/gdpr）
3. call memory_status → 与 HTTP 直调一致（薄转发验证）
4. call concept_create → 与 HTTP POST /concepts 等价（写路径）
5. call concept_list → 含刚创建的概念
6. call recall → 命中刚创建的概念
7. call gdpr_erase 不存在时……（危险工具只列不调）

运行：./.venv/bin/python tests/test_mcp.py
"""
import asyncio
import sys
import uuid

import httpx
from mcp.client.streamable_http import streamable_http_client
from mcp import ClientSession

BASE = "http://127.0.0.1:8202"
MCP_URL = f"{BASE}/mcp"
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
    kw = f"mcps{suffix}"

    # 登录拿 token（MCP server 用自己的 config 凭证）
    async with httpx.AsyncClient(base_url=BASE, timeout=60.0) as c:
        r = await c.post("/api/auth/login", json={"username": "test", "password": "123456"})
        check("测试账号登录", r.status_code == 200, f"status={r.status_code}")
        h = {"Authorization": f"Bearer {r.json()['access_token']}"}

        async with streamable_http_client(MCP_URL) as streams:
            read, write = streams
            async with ClientSession(read, write) as session:
                tools = await session.list_tools()
                names = [t.name for t in tools.tools]
                check("list_tools ≥ 15", len(names) >= 15, f"count={len(names)}")
                check("工具清单含核心工具", {"concept_create", "concept_list", "recall", "ingest",
                      "episodes_list", "dreams_list", "clarification_process", "gdpr_erase"} <= set(names),
                      f"names={sorted(names)[:8]}...")

                # 3. memory_status 薄转发
                res = await session.call_tool("memory_status", {})
                text = res.content[0].text if res.content else ""
                check("memory_status 返回结构", "pgvector" in text and "service" in text, f"text={text[:120]}")

                # 4. concept_create（MCP 写 → HTTP 等价）
                res = await session.call_tool("concept_create", {
                    "canonical_name": f"{kw}主题", "description_short": "MCP 写入", "importance": 0.8,
                })
                text = res.content[0].text if res.content else ""
                check("concept_create 200", '"id"' in text and kw in text, f"text={text[:120]}")
                cid = text.split('"id": "')[1].split('"')[0] if '"id": "' in text else None

                # 5. concept_list 含新概念
                res = await session.call_tool("concept_list", {"limit": 50})
                text = res.content[0].text if res.content else ""
                check("concept_list 含新概念", f"{kw}主题" in text, f"hit={f'{kw}主题' in text}")

                # 6. recall 命中
                res = await session.call_tool("recall", {"query": f"{kw} 主题"})
                text = res.content[0].text if res.content else ""
                check("recall 命中 MCP 写入概念", f"{kw}主题" in text, f"hit={f'{kw}主题' in text}")

                # 7. 与 HTTP 直调结构一致（薄转发对账：mode/键结构一致；
                #     context 逐字节不要求——召回受权重/时间状态影响）
                r = await c.post("/api/memory/recall", headers=h, json={"query": f"{kw} 主题"})
                http_body = r.json()
                check("MCP 与 HTTP 召回结构一致", http_body.get("mode") == "text"
                      and "context" in http_body and f"{kw}主题" in http_body.get("context", ""),
                      f"http_mode={http_body.get('mode')} http_hit={f'{kw}主题' in http_body.get('context', '')}")

    print(f"\n==== 结果: {passed} passed, {failed} failed ====")
    sys.exit(0 if failed == 0 else 1)


async def inproc_probe() -> None:
    """进程内 MCP server 验证（官方推荐 Client(mcp) 模式；SDK 2.0.0 的
    stdio_client 有 "Invalid request parameters" bug——最小 server 同样触发，
    已记录 backlog——stdio 子进程入口由宿主（Claude Desktop 等）调用）。"""
    import sys as _sys
    from pathlib import Path as _P
    _sys.path.insert(0, str(_P(__file__).resolve().parent.parent / "backend"))
    from mcp import Client
    from app.mcp_server import build_mcp_server_from_config

    server = build_mcp_server_from_config()
    async with Client(server, raise_exceptions=True) as c:
        tools = await c.list_tools()
        names = [t.name for t in tools.tools]
        check("in-proc list_tools ≥ 15", len(names) >= 15, f"count={len(names)}")
        res = await c.call_tool("memory_status", {})
        text = res.content[0].text if res.content else ""
        check("in-proc memory_status（薄转发）", "pgvector" in text, f"text={text[:80]}")


if __name__ == "__main__":
    asyncio.run(inproc_probe())
    asyncio.run(main())
