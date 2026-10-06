"""Agent 身份命名空间助手（Wave 1：agent_id 贯通）。

语义约定（全服务层单源）：
- `agent_id IS NULL` = 用户级共享记忆（历史数据回填 + 显式无 agent 客户端）。
- 非 NULL = 该 agent 私有。
- 读：`agent_scope_sql()` → 共享 + 自身；写：调用方 agent 直接 stamp（None → NULL）。

本模块同时提供 `ensure_agent_registered`：当请求携带 agent 身份时，best-effort
登记 `agents` 注册表行与 per-agent `user_agent_states` 行（进程内缓存去重，
失败吞掉只留日志——作用域是同一 user 内的命名空间，不是用户间安全边界）。
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)

AGENT_KEY_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# 进程内登记缓存（(user_id, agent_id) → True）；容量上限防无界增长。
_ENSURED: dict[tuple[str, str], bool] = {}
_ENSURED_CAP = 4096

_AGENT_KINDS = ("claude-code", "codex", "opencode", "dsh")


def normalize_agent_id(raw) -> Optional[str]:
    """空/空白 → None；合法 key 原样返回；非法抛 ValueError（调用方转 422）。"""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if not AGENT_KEY_RE.match(s):
        raise ValueError(f"invalid agent id: {raw!r}")
    return s


def agent_scope_sql(agent_id: Optional[str], column: str = "agent_id") -> str:
    """读作用域 SQL 片段（含前导 AND）。

    有 agent → `AND (col IS NULL OR col = :agent_scope)`；无 agent → `AND col IS NULL`。
    参数名固定 `:agent_scope`（配合 agent_scope_params 使用）。
    """
    if agent_id:
        return f"AND ({column} IS NULL OR {column} = :agent_scope)"
    return f"AND {column} IS NULL"


def agent_scope_params(agent_id: Optional[str]) -> dict:
    """读作用域参数（无 agent 时为空 dict，SQL 片段也无占位符）。"""
    return {"agent_scope": agent_id} if agent_id else {}


def _infer_kind(agent_id: str) -> str:
    low = agent_id.lower()
    for k in _AGENT_KINDS:
        if low.startswith(k):
            return k
    return "manual"


async def ensure_agent_registered(user_id: str, agent_id: Optional[str]) -> None:
    """best-effort 登记 agent（注册表 + per-agent UAS）。失败只留痕，不阻断请求。"""
    if not agent_id:
        return
    cache_key = (str(user_id), str(agent_id))
    if _ENSURED.get(cache_key):
        return
    try:
        from app.db.database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            await _ensure_agent_row(db, user_id, agent_id)
            await _ensure_state_row(db, user_id, agent_id)
            await db.commit()
        if len(_ENSURED) >= _ENSURED_CAP:
            _ENSURED.clear()
        _ENSURED[cache_key] = True
    except Exception:
        logger.warning("ensure_agent_registered failed for user=%s agent=%s", user_id, agent_id,
                       exc_info=True)


async def _ensure_agent_row(db, user_id: str, agent_id: str) -> None:
    from app.db.database import Agent

    existing = (await db.execute(
        select(Agent.id).where(Agent.user_id == user_id, Agent.agent_key == agent_id)
    )).scalar_one_or_none()
    if existing:
        await db.execute(
            update(Agent).where(Agent.id == existing).values(last_seen_at=datetime.utcnow())
        )
        return
    try:
        async with db.begin_nested():
            db.add(Agent(id=str(uuid.uuid4()), user_id=user_id, agent_key=agent_id,
                         display_name=agent_id, kind=_infer_kind(agent_id)))
            await db.flush()
    except IntegrityError:
        pass


async def _ensure_state_row(db, user_id: str, agent_id: str) -> None:
    from app.db.database import UserAgentState

    existing = (await db.execute(
        select(UserAgentState.id).where(UserAgentState.user_id == user_id,
                                        UserAgentState.agent_id == agent_id)
    )).scalar_one_or_none()
    if existing:
        return
    try:
        async with db.begin_nested():
            db.add(UserAgentState(id=str(uuid.uuid4()), user_id=user_id, agent_id=agent_id,
                                  agent_name=agent_id[:120]))
            await db.flush()
    except IntegrityError:
        pass
