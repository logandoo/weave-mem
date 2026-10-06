"""weave-mem-client — weave-mem 记忆服务的类型化异步客户端 SDK。

覆盖 docs/openapi.json 全部 32 路径：鉴权/PAT、概念、召回、摄入、采纳、
召回台账、澄清、成本治理、GDPR、admin、healthz。

用法：
    from weave_mem_client import MemoryClient

    async with MemoryClient("http://127.0.0.1:8202", username="u", password="p") as c:
        concept = await c.create_concept(canonical_name="项目", description_short="d")
        rec = await c.recall(query="项目", include_meta=True)

鉴权三态：构造时给 token（PAT，推荐）；或 username/password（首调自动登录缓存）；
或逐请求走 request() 自带头。错误统一抛 RuntimeError("HTTP <code>: <body>")。
"""
from weave_mem_client.client import MemoryClient

__version__ = "0.1.0"
__all__ = ["MemoryClient", "__version__"]
