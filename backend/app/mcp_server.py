"""weave-mem MCP server — 当前 HTTP API 的薄转发（memos 借鉴）。

每个工具 handler 内部通过 httpx 调用 weave-mem 自身 HTTP API（默认
http://127.0.0.1:8202，config [mcp] base_url 可覆盖），不直连服务层：
鉴权、参数校验、序列化全部复用 HTTP 端点。

传输：
- streamable-http：挂载进 FastAPI（main.py），端点 /mcp
- stdio：python -m app.mcp（宿主进程启动，走 stdin/stdout）

认证：config [mcp] token（PAT，优先）或 username/password（启动时 login）。
"""
import logging

import httpx

from mcp.server import MCPServer

logger = logging.getLogger(__name__)


class MemoryClient:
    """weave-mem HTTP 薄客户端：登录缓存 token，所有工具调用走真实 HTTP。"""

    def __init__(self, base_url: str, username: str = "", password: str = "", token: str = "",
                 timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.token = token
        self.timeout = timeout
        self._client = httpx.AsyncClient(timeout=timeout)
        self._authenticated = False

    async def ensure_auth(self) -> None:
        if self._authenticated:
            return
        if self.token:
            self._authenticated = True
            return
        if not self.username:
            raise RuntimeError("MCP 认证未配置：config [mcp] 需 token 或 username/password")
        resp = await self._client.post(
            f"{self.base_url}/api/auth/login",
            json={"username": self.username, "password": self.password},
        )
        resp.raise_for_status()
        self.token = resp.json()["access_token"]
        self._authenticated = True

    async def request(self, method: str, path: str, **kwargs) -> dict:
        await self.ensure_auth()
        headers = {"Authorization": f"Bearer {self.token}"}
        resp = await self._client.request(method, f"{self.base_url}{path}", headers=headers, **kwargs)
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    async def aclose(self) -> None:
        await self._client.aclose()


def create_mcp_server(base_url: str, username: str = "", password: str = "", token: str = "",
                     timeout: float = 60.0) -> MCPServer:
    """构造 MCP server（工具 = HTTP 薄转发）。"""
    mcp = MCPServer(
        "weave-mem",
        version="1.0.0",
        description="weave-mem 记忆系统：概念/召回/摄入/澄清/梦境/擦除（薄转发 HTTP API）",
        instructions=(
            "所有工具调用 weave-mem HTTP API。写入类工具（concept_create/ingest/...）"
            "会真实修改记忆；gdpr_erase 为全量擦除，使用前必须确认。"
        ),
    )
    client = MemoryClient(base_url, username, password, token, timeout=timeout)

    @mcp.tool()
    async def memory_status() -> dict:
        """查看记忆子系统状态（pgvector 可用性、embedding provider 配置）。"""
        return await client.request("GET", "/api/memory/status")

    @mcp.tool()
    async def concept_list(limit: int = 50) -> dict:
        """列出当前用户概念（按 importance/weight 排序）。"""
        return await client.request("GET", f"/api/memory/concepts?limit={max(1, min(limit, 200))}")

    @mcp.tool()
    async def concept_get(concept_id: str) -> dict:
        """按 id 获取概念详情（含 aliases/stability/last_recalled_at/metadata_json）。"""
        return await client.request("GET", f"/api/memory/concepts/{concept_id}")

    @mcp.tool()
    async def concept_create(
        canonical_name: str,
        description_short: str = "",
        description_full: str = "",
        memory_type: str = "semantic",
        importance: float = 0.5,
        source_trust: str = "user_stated",
    ) -> dict:
        """写入概念（同名 upsert 自动合并）。"""
        return await client.request(
            "POST", "/api/memory/concepts",
            json={
                "canonical_name": canonical_name,
                "description_short": description_short,
                "description_full": description_full,
                "memory_type": memory_type,
                "importance": importance,
                "source_trust": source_trust,
            },
        )

    @mcp.tool()
    async def concept_delete(concept_id: str) -> dict:
        """物理删除概念（连同关系与集群成员）。"""
        return await client.request("DELETE", f"/api/memory/concepts/{concept_id}")

    @mcp.tool()
    async def concept_forget(concept_id: str) -> dict:
        """遗忘概念（soft delete：valid_to 置位、weight 归零）。"""
        return await client.request("POST", f"/api/memory/concepts/{concept_id}/forget")

    @mcp.tool()
    async def recall(query: str, include_meta: bool = False) -> dict:
        """召回记忆（自动选择向量/文本通道）；include_meta 返回 memory_ids/top_gate_score。"""
        return await client.request(
            "POST", f"/api/memory/recall?include_meta={str(include_meta).lower()}",
            json={"query": query},
        )

    @mcp.tool()
    async def ingest(content: str, unit_kind: str = "message") -> dict:
        """潜意识摄入：文本写入 SubconsciousLog，调度器自动提炼概念（需 embedding provider）。"""
        return await client.request(
            "POST", "/api/memory/ingest",
            json={"content": content, "unit_kind": unit_kind},
        )

    @mcp.tool()
    async def episodes_list(limit: int = 10) -> dict:
        """情节记忆列表。"""
        return await client.request("GET", f"/api/memory/episodes?limit={max(1, min(limit, 50))}")

    @mcp.tool()
    async def dreams_list(limit: int = 10) -> dict:
        """梦境记忆列表。"""
        return await client.request("GET", f"/api/memory/dreams?limit={max(1, min(limit, 50))}")

    @mcp.tool()
    async def clarifications_list(limit: int = 50) -> dict:
        """澄清记录列表（含 applied 状态）。"""
        return await client.request("GET", f"/api/memory/clarifications?limit={max(1, min(limit, 200))}")

    @mcp.tool()
    async def clarification_process(user_message: str) -> dict:
        """澄清处理：信号词预筛 + LLM 判定，高置信度自动应用。"""
        return await client.request(
            "POST", "/api/memory/clarifications/process",
            json={"user_message": user_message},
        )

    @mcp.tool()
    async def clarification_apply(clarification_id: str) -> dict:
        """手动应用 pending 澄清（confidence < 0.8 落库后的确认途径）。"""
        return await client.request("POST", f"/api/memory/clarifications/{clarification_id}/apply")

    @mcp.tool()
    async def clarification_revert(clarification_id: str) -> dict:
        """回退已应用澄清（negate/refine/add_constraint 可逆）。"""
        return await client.request("POST", f"/api/memory/clarifications/{clarification_id}/revert")

    @mcp.tool()
    async def cost_governance_status() -> dict:
        """成本治理状态（降级级别/触发原因/今日调用量）。"""
        return await client.request("GET", "/api/memory/cost_governance/status")

    @mcp.tool()
    async def gdpr_erase() -> dict:
        """GDPR 全量擦除：清空当前用户全部记忆（DB+文件层）。危险操作，确认后使用。"""
        return await client.request("DELETE", "/api/memory/all")

    return mcp


def build_mcp_server_from_config() -> MCPServer:
    """从 config.toml [mcp] 段构造（stdio 入口与 FastAPI 挂载共用）。"""
    from app.core.config import get_config
    cfg = get_config()
    mcp_cfg = cfg.mcp or {}
    return create_mcp_server(
        base_url=str(mcp_cfg.get("base_url") or "http://127.0.0.1:8202"),
        username=str(mcp_cfg.get("username") or ""),
        password=str(mcp_cfg.get("password") or ""),
        token=str(mcp_cfg.get("token") or ""),
        timeout=float(mcp_cfg.get("timeout", 60.0)),
    )
