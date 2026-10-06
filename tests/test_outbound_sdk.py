"""weave-mem SDK wave 断言：出向 embedding 归一官方 SDK（stub /embeddings 实证）。

stub 服务捕获请求（路径/头/体），验证：
1. embed_text 经官方 AsyncOpenAI SDK 走 POST {base}/embeddings 且解析向量；
2. no-key 哨兵语义：显式 base 空键 → Authorization: Bearer no-key（不落全局 key）；
3. provider_router.embedding_available 探测同款（available/dim 读取）。
运行：./.venv/bin/python tests/test_outbound_sdk.py
"""
import asyncio
import json
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

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


class _StubHandler(BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        _StubHandler.seen.append({
            "path": self.path,
            "auth": self.headers.get("Authorization") or "",
            "user_agent": self.headers.get("User-Agent") or "",
            "body": body,
        })
        if self.path.endswith("/embeddings"):
            payload = json.dumps({
                "object": "list",
                "data": [{"object": "embedding", "embedding": [0.1, 0.2, 0.3, 0.4], "index": 0}],
                "model": "stub",
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


async def main() -> None:
    from app.core.config import get_config
    from app.services.memory_embedding_service import embed_text
    from app.services.provider_router import ProviderRouter

    server = HTTPServer(("127.0.0.1", 0), _StubHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}/v1"

    cfg = get_config()
    saved = dict(cfg._config.get("memory", {}))
    try:
        cfg._config.setdefault("memory", {})
        cfg._config["memory"]["embedding_api_base"] = base
        cfg._config["memory"]["embedding_api_key"] = ""
        cfg._config["memory"]["embedding_model"] = "stub-model"
        _StubHandler.seen.clear()

        vec = await embed_text("hello sdk")
        check("SDK embedding 解析向量", vec == [0.1, 0.2, 0.3, 0.4], f"vec={vec}")
        hit = _StubHandler.seen[-1] if _StubHandler.seen else {}
        check("SDK 走 /v1/embeddings 精确端点", hit.get("path") == "/v1/embeddings",
              f"path={hit.get('path')}")
        check("SDK 请求体含 model=stub-model", b"stub-model" in (hit.get("body") or b""),
              f"body={hit.get('body')[:80]}")
        check("no-key 哨兵头语义", hit.get("auth") == "Bearer no-key", f"auth={hit.get('auth')!r}")
        check("官方 SDK UA（OpenAI/Python）", "OpenAI" in (hit.get("user_agent") or ""),
              f"ua={hit.get('user_agent')!r}")

        cfg._config["memory"]["embedding_api_key"] = "EMB-KEY-1"
        _StubHandler.seen.clear()
        await embed_text("hello again")
        hit2 = _StubHandler.seen[-1] if _StubHandler.seen else {}
        check("显式键随请求", hit2.get("auth") == "Bearer EMB-KEY-1", f"auth={hit2.get('auth')!r}")

        # provider_router.embedding_available 探测——探测的是**主 provider**
        # （api.base_url）是否暴露 /embeddings，配置面是 [api] 而非 embedding_*
        saved_api = dict(cfg._config.get("api", {}))
        try:
            cfg._config.setdefault("api", {})
            cfg._config["api"]["base_url"] = base
            cfg._config["api"]["api_key"] = ""
            _StubHandler.seen.clear()
            router = ProviderRouter()
            ok, dim = await router.embedding_available()
            check("SDK 探测 available/dim", ok is True and dim == 4, f"ok={ok} dim={dim}")
            hit3 = _StubHandler.seen[-1] if _StubHandler.seen else {}
            check("探测同为 no-key 语义", hit3.get("auth") == "Bearer no-key", f"auth={hit3.get('auth')!r}")
            check("探测同为官方 SDK UA", "OpenAI" in (hit3.get("user_agent") or ""),
                  f"ua={hit3.get('user_agent')!r}")
        finally:
            cfg._config["api"] = saved_api
    finally:
        cfg._config["memory"] = saved
        server.shutdown()

    # C1 回归（双审 Critical）：无 embedding 端点时 fail-closed——绝不向
    # api.openai.com 等任何外部端点外发（旧 httpx 空 base 即拒，SDK 传 None 会外泄）
    try:
        cfg._config.setdefault("memory", {})
        cfg._config["memory"]["embedding_api_base"] = ""
        cfg._config.setdefault("api", {})
        saved_main = dict(cfg._config["api"])
        cfg._config["api"]["base_url"] = ""
        cfg._config["api"]["api_key"] = ""
        _StubHandler.seen.clear()
        vec = await embed_text("机密文本不得外发")
        check("C1 空 base fail-closed 返回 None", vec is None, f"vec={vec}")
        check("C1 零请求外发", len(_StubHandler.seen) == 0, f"seen={len(_StubHandler.seen)}")
        cfg._config["api"] = saved_main
    finally:
        pass

    print(f"\n==== 结果: {passed} passed, {failed} failed ====")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
