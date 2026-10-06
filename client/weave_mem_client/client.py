"""MemoryClient — weave-mem HTTP API 类型化异步薄层（32 路径 1:1 对齐 openapi.json）。

设计口径：
- 手写薄层而非 OpenAPI codegen（零重工具链；方法签名即文档）；
- 每方法 = 一个端点，参数/返回 dict 保真（服务端 schema 为准）；
- 通用 request() 兜底任意路径（MCP 薄转发沿用同一入口——单源）。
"""
from __future__ import annotations

from typing import Any, Optional

import httpx


class MemoryClient:
    """weave-mem 异步客户端（可作 async context manager）。"""

    def __init__(self, base_url: str, username: str = "", password: str = "", token: str = "",
                 timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.token = token
        self.timeout = timeout
        self._client = httpx.AsyncClient(timeout=timeout)
        self._authenticated = False

    # ---------- 会话 ----------
    async def __aenter__(self) -> "MemoryClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def ensure_auth(self) -> None:
        """有凭据则登录/缓存；无凭据不拦——透传给服务端 401（配置错与权限错分开）。"""
        if self._authenticated:
            return
        if self.token:
            self._authenticated = True
            return
        if not self.username:
            return
        self.token = await self.login(self.username, self.password)
        self._authenticated = True

    async def request(self, method: str, path: str, *, auth: bool = True, **kwargs) -> Any:
        if auth:
            await self.ensure_auth()
        # 自带头与鉴权头合并（双审 I1：裸传 headers= 会与关键字参数相撞）
        extra = dict(kwargs.pop("headers", None) or {})
        if auth and self.token:
            extra.setdefault("Authorization", f"Bearer {self.token}")
        resp = await self._client.request(method, f"{self.base_url}{path}", headers=extra, **kwargs)
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {"raw": resp.text}

    # ---------- 鉴权 / PAT ----------
    async def register(self, username: str, password: str) -> dict:
        return await self.request("POST", "/api/auth/register", auth=False,
                                  json={"username": username, "password": password})

    async def login(self, username: str, password: str) -> str:
        body = await self.request("POST", "/api/auth/login", auth=False,
                                  json={"username": username, "password": password})
        self.token = body["access_token"]
        self._authenticated = True
        return self.token

    async def logout(self) -> Any:
        """登出当前会话。注意：清除本地 token（含构造传入的 PAT——服务端 PAT 仍有效）；
        若构造给了 username/password，下次调用会自动重登。"""
        out = await self.request("POST", "/api/auth/logout")
        self._authenticated = False
        self.token = ""
        return out

    async def me(self) -> dict:
        return await self.request("GET", "/api/auth/me")

    async def create_token(self, name: str = "", **body: Any) -> dict:
        payload = {"name": name, **body}
        return await self.request("POST", "/api/auth/tokens", json=payload)

    async def list_tokens(self) -> Any:
        return await self.request("GET", "/api/auth/tokens")

    async def revoke_token(self, token_id: str) -> Any:
        return await self.request("DELETE", f"/api/auth/tokens/{token_id}")

    # ---------- 根 / 健康 ----------
    async def root(self) -> dict:
        """GET / — 服务根信息（openapi 第 32 条路径）。"""
        return await self.request("GET", "/", auth=False)

    async def healthz(self) -> dict:
        return await self.request("GET", "/healthz", auth=False)

    async def api_health(self) -> dict:
        return await self.request("GET", "/api/health", auth=False)

    # ---------- 概念 ----------
    async def list_concepts(self, limit: Optional[int] = None) -> Any:
        """概念列表 → {"concepts": [...], "count": N}（按 importance/weight 倒序）。"""
        params = {"limit": limit} if limit is not None else {}
        return await self.request("GET", "/api/memory/concepts", params=params)

    async def create_concept(self, **body: Any) -> dict:
        return await self.request("POST", "/api/memory/concepts", json=body)

    async def get_concept(self, concept_id: str) -> dict:
        return await self.request("GET", f"/api/memory/concepts/{concept_id}")

    async def delete_concept(self, concept_id: str) -> Any:
        return await self.request("DELETE", f"/api/memory/concepts/{concept_id}")

    async def forget_concept(self, concept_id: str) -> Any:
        return await self.request("POST", f"/api/memory/concepts/{concept_id}/forget")

    # ---------- 召回 / 采纳 / 台账 ----------
    async def recall(self, query: str, include_meta: bool = False, **body: Any) -> dict:
        payload = {"query": query, **body}
        return await self.request("POST", "/api/memory/recall",
                                  params={"include_meta": str(include_meta).lower()}, json=payload)

    async def adoption(self, injected_ids: Optional[list[str]] = None,
                       answer_text: str = "", **body: Any) -> dict:
        payload = {"injected_ids": list(injected_ids or []), "answer_text": answer_text, **body}
        return await self.request("POST", "/api/memory/adoption", json=payload)

    async def recall_log(self, before_id: Optional[str] = None, limit: int = 50) -> dict:
        params: dict[str, Any] = {"limit": limit}
        if before_id:
            params["before_id"] = before_id
        return await self.request("GET", "/api/memory/recall_log", params=params)

    # ---------- 摄入 / 情节 / 梦境 / 澄清 ----------
    async def ingest(self, content: str, unit_kind: str = "message",
                     source_ids: Optional[list[str]] = None, **body: Any) -> dict:
        payload = {"content": content, "unit_kind": unit_kind,
                   "source_ids": list(source_ids or []), **body}
        return await self.request("POST", "/api/memory/ingest", json=payload)

    async def list_episodes(self, limit: Optional[int] = None) -> Any:
        params = {"limit": limit} if limit is not None else {}
        return await self.request("GET", "/api/memory/episodes", params=params)

    async def list_dreams(self, limit: Optional[int] = None) -> Any:
        params = {"limit": limit} if limit is not None else {}
        return await self.request("GET", "/api/memory/dreams", params=params)

    async def list_clarifications(self, limit: Optional[int] = None) -> Any:
        params = {"limit": limit} if limit is not None else {}
        return await self.request("GET", "/api/memory/clarifications", params=params)

    async def process_clarification(self, user_message: str, **body: Any) -> dict:
        return await self.request("POST", "/api/memory/clarifications/process",
                                  json={"user_message": user_message, **body})

    async def apply_clarification(self, clarification_id: str) -> Any:
        return await self.request("POST", f"/api/memory/clarifications/{clarification_id}/apply")

    async def revert_clarification(self, clarification_id: str) -> Any:
        return await self.request("POST", f"/api/memory/clarifications/{clarification_id}/revert")

    # ---------- 状态 / 治理 / 擦除 ----------
    async def memory_status(self) -> dict:
        return await self.request("GET", "/api/memory/status")

    async def cost_governance_status(self) -> dict:
        return await self.request("GET", "/api/memory/cost_governance/status")

    async def reset_cost_governance(self, user_id: str) -> Any:
        return await self.request("PUT", f"/api/memory/{user_id}/cost_governance/reset")

    async def gdpr_erase(self) -> Any:
        return await self.request("DELETE", "/api/memory/all")

    # ---------- admin ----------
    async def admin_list_users(self) -> Any:
        return await self.request("GET", "/api/admin/users")

    async def admin_set_user_role(self, user_id: str, role: str) -> Any:
        return await self.request("PUT", f"/api/admin/users/{user_id}/role", json={"role": role})

    async def admin_reload_config(self) -> Any:
        return await self.request("POST", "/api/admin/reload-config")

    async def admin_migration_run(self, **body: Any) -> Any:
        return await self.request("POST", "/api/admin/memory/migration/run", json=body)

    async def admin_migration_rollback(self, **body: Any) -> Any:
        return await self.request("POST", "/api/admin/memory/migration/rollback", json=body)

    async def admin_migration_status(self, user_id: Optional[str] = None) -> Any:
        params = {"user_id": user_id} if user_id else {}
        return await self.request("GET", "/api/admin/memory/migration/status", params=params)
