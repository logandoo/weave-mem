# weave-mem 裁剪：从 chatbot app/api/auth.py 原样摘取，每行内容与原文一致。
# 解耦点：删除 assistant_service（create_default_assistant_if_needed）、
# workspace_service（ensure_user_workspace）、agent_permissions 导入与调用，
# 及 /me/permissions 端点；保留 memory_service.ensure_user_agent_state
# （weave-mem 有 memory_service，注册/登录即建 user_agent_states 行，
# memory_scheduler 水位线扫描依赖）。

from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.database import get_db, User, UserSession
from app.schemas.chat import UserResponse, LoginRequest, TokenResponse
from app.services.auth_service import hash_password, verify_password, create_access_token
from app.services.memory_service import ensure_user_agent_state
from app.core.deps import get_current_user
from datetime import datetime

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _user_response(user) -> UserResponse:
    return UserResponse(
        id=user.id,
        username=user.username,
        created_at=user.created_at.isoformat(),
    )


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(request: LoginRequest, db: AsyncSession = Depends(get_db)):
    if not request.username or not request.password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户名和密码不能为空"
        )
    if len(request.username) < 2 or len(request.username) > 50:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户名长度必须在2-50个字符之间"
        )
    if len(request.password) < 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="密码长度不能少于6个字符"
        )

    result = await db.execute(select(User).where(User.username == request.username))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="用户名已存在"
        )

    user = User(
        username=request.username,
        password_hash=await hash_password(request.password),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    await ensure_user_agent_state(db, user.id)

    return _user_response(user)


@router.post("/login", response_model=TokenResponse)
async def login(request: Request, login_req: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == login_req.username))
    user = result.scalar_one_or_none()

    if not user or not await verify_password(login_req.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password"
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled"
        )

    user.last_login_at = datetime.utcnow()
    user.last_login_ip = request.client.host if request.client else None
    await db.commit()

    agent_id = None
    if login_req.agent_id is not None:
        from app.services.memory_scope import normalize_agent_id, ensure_agent_registered
        try:
            agent_id = normalize_agent_id(login_req.agent_id)
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid agent_id (allowed: [A-Za-z0-9._-]{1,64})")
        if agent_id:
            await ensure_agent_registered(user.id, agent_id)

    await ensure_user_agent_state(db, user.id)

    access_token = create_access_token(user.id, user.username, agent_id=agent_id)

    user_agent = request.headers.get("user-agent", "")

    user_session = UserSession(
        user_id=user.id,
        session_token=access_token,
        ip_address=request.client.host if request.client else None,
        user_agent=user_agent,
        last_active_at=datetime.utcnow()
    )
    db.add(user_session)
    await db.commit()

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        user=_user_response(user),
    )


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(__import__("app.core.deps", fromlist=["get_current_user"]).get_current_user)
):
    return _user_response(current_user)




@router.post("/tokens")
async def create_pat_token(
    body: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建个人访问令牌（PAT）：明文仅本次响应返回，之后不可再查（memos 借鉴）。

    Wave 1：可选 body.agent_id 绑定作用域（此后该 token 免 header 即为该 agent）。
    """
    body = body or {}
    name = str(body.get("name") or "").strip()[:100]
    agent_id = None
    if body.get("agent_id") is not None:
        from app.services.memory_scope import normalize_agent_id, ensure_agent_registered
        try:
            agent_id = normalize_agent_id(body.get("agent_id"))
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid agent_id (allowed: [A-Za-z0-9._-]{1,64})")
        if agent_id:
            await ensure_agent_registered(current_user.id, agent_id)
    from app.services.auth_service import create_pat
    tid, plain = await create_pat(db, current_user.id, name, agent_id=agent_id)
    await db.commit()
    return {"id": tid, "name": name, "agent_id": agent_id, "token": plain, "token_type": "bearer"}


@router.get("/tokens")
async def list_pat_tokens(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """当前用户 PAT 列表（不含明文与哈希）。"""
    from app.db.database import PersonalAccessToken
    result = await db.execute(
        select(PersonalAccessToken)
        .where(PersonalAccessToken.user_id == current_user.id)
        .order_by(PersonalAccessToken.created_at.desc())
    )
    return [
        {"id": t.id, "name": t.name, "agent_id": t.agent_id,
         "created_at": str(t.created_at) if t.created_at else None,
         "last_used_at": str(t.last_used_at) if t.last_used_at else None,
         "revoked": t.revoked_at is not None}
        for t in result.scalars().all()
    ]


@router.delete("/tokens/{token_id}")
async def revoke_pat_token(
    token_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """撤销 PAT（软删除：revoked_at 置位）。"""
    from app.db.database import PersonalAccessToken
    result = await db.execute(
        select(PersonalAccessToken).where(
            PersonalAccessToken.id == token_id,
            PersonalAccessToken.user_id == current_user.id,
        )
    )
    pat = result.scalar_one_or_none()
    if pat is None:
        raise HTTPException(status_code=404, detail="Token not found")
    pat.revoked_at = datetime.utcnow()
    await db.commit()
    return {"revoked": token_id}


@router.post("/logout")
async def logout(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(__import__("app.core.deps", fromlist=["get_current_user"]).get_current_user)
):
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        result = await db.execute(
            select(UserSession).where(
                UserSession.user_id == current_user.id,
                UserSession.session_token == token
            )
        )
        session = result.scalar_one_or_none()
        if session:
            await db.delete(session)
            await db.commit()

    return {"message": "Logged out successfully"}