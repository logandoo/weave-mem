from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.database import get_db, User
from app.services.auth_service import decode_access_token

security = HTTPBearer()


@dataclass
class Principal:
    """请求主体：user + 可选 agent 作用域（Wave 1）。

    agent_id 来源优先级：`X-Agent-Id` 请求头 > JWT claim / PAT 绑定。
    缺省 None = 共享作用域（历史客户端行为不变）。
    """

    user: User
    agent_id: Optional[str] = None


async def _load_active_user(db: AsyncSession, user_id: str) -> User:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is inactive",
        )
    return user


async def get_user_from_token(token: str, db: AsyncSession) -> User:
    payload = decode_access_token(token)

    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    return await _load_active_user(db, user_id)


def _apply_header_scope(request: Request, agent_id: Optional[str]) -> Optional[str]:
    """X-Agent-Id 头覆盖 token 绑定（同 user 内命名空间，非安全边界）；非法 → 422。"""
    raw = request.headers.get("x-agent-id")
    if raw is None or not raw.strip():
        return agent_id
    from app.services.memory_scope import normalize_agent_id

    try:
        return normalize_agent_id(raw)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="invalid X-Agent-Id (allowed: [A-Za-z0-9._-]{1,64})",
        )


async def get_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> Principal:
    """单次认证解析（JWT 优先，PAT 兜底），返回 user + agent 作用域。"""
    token = credentials.credentials

    payload = decode_access_token(token)
    if payload is not None and payload.get("sub"):
        user = await _load_active_user(db, payload["sub"])
        return Principal(user=user, agent_id=_apply_header_scope(request, payload.get("agent_id")))

    from app.services.auth_service import validate_pat

    pat = await validate_pat(db, token)
    if not pat:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_id, pat_agent = pat
    user = await _load_active_user(db, user_id)
    return Principal(user=user, agent_id=_apply_header_scope(request, pat_agent))


async def get_current_user(
    principal: Principal = Depends(get_principal),
) -> User:
    """双通道认证（JWT 优先，PAT 兜底）——端点既有签名不变。"""
    return principal.user


async def get_agent_scope(
    principal: Principal = Depends(get_principal),
) -> Optional[str]:
    """当前请求的 agent 作用域（None = 共享）。

    携带 agent 身份时 best-effort 登记注册表/UAS 行（进程内缓存；失败不阻断）。
    """
    if principal.agent_id:
        from app.services.memory_scope import ensure_agent_registered

        await ensure_agent_registered(principal.user.id, principal.agent_id)
    return principal.agent_id
