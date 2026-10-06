# weave-mem-client

Typed async client SDK for the [weave-mem](../README.md) memory service — 32 API paths
1:1 against `docs/openapi.json`. The same `MemoryClient` powers the MCP thin-forward layer.

```bash
pip install -e ./client
```

```python
from weave_mem_client import MemoryClient

async with MemoryClient("http://127.0.0.1:8202", token="<PAT>") as c:
    concept = await c.create_concept(canonical_name="项目", description_short="d")
    rec = await c.recall(query="项目", include_meta=True)
```

Auth: PAT (`token=`), username/password (auto-login, cached), or bring-your-own headers via
`request(method, path, headers=...)`. Errors raise `RuntimeError("HTTP <code>: …")`;
unauthenticated calls surface the server's 401. Requires Python 3.11+, `httpx`.
