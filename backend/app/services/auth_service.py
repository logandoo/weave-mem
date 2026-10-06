import asyncio
import jwt
import bcrypt
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import get_config

config = get_config()

SECRET_KEY = config.security_jwt_secret_key
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 7


async def hash_password(password: str) -> str:
    salt = await asyncio.to_thread(bcrypt.gensalt)
    hashed = await asyncio.to_thread(bcrypt.hashpw, password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


async def verify_password(password: str, hashed: str) -> bool:
    return await asyncio.to_thread(
        bcrypt.checkpw, password.encode("utf-8"), hashed.encode("utf-8")
    )


def create_access_token(user_id: str, username: str, agent_id: Optional[str] = None) -> str:
    expire = datetime.utcnow() + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": user_id,
        "username": username,
        "exp": expire,
        "iat": datetime.utcnow(),
        "jti": str(uuid.uuid4())
    }
    if agent_id:
        payload["agent_id"] = agent_id
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def generate_pat() -> str:
    """生成 PAT 明文（wm_ 前缀 + urlsafe 随机串）。"""
    return "wm_" + secrets.token_urlsafe(32)


def hash_pat(token: str) -> str:
    """PAT 只存 sha256 哈希，库中不落明文（memos 同款安全实践）。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def create_pat(db: AsyncSession, user_id: str, name: str = "",
                     agent_id: Optional[str] = None) -> tuple[str, str]:
    """创建 PAT：返回 (id, 明文)。明文仅此一次可见。agent_id 可绑定作用域。"""
    from app.db.database import PersonalAccessToken
    plain = generate_pat()
    pat = PersonalAccessToken(
        id=str(uuid.uuid4()), user_id=user_id, token_hash=hash_pat(plain),
        name=name or "default", agent_id=agent_id,
    )
    db.add(pat)
    await db.flush()
    return pat.id, plain


async def validate_pat(db: AsyncSession, token: str) -> Optional[tuple[str, Optional[str]]]:
    """校验 PAT：有效返回 (user_id, agent_id)，否则 None。命中后更新 last_used_at。"""
    from app.db.database import PersonalAccessToken
    if not token.startswith("wm_"):
        return None
    result = await db.execute(
        select(PersonalAccessToken).where(
            PersonalAccessToken.token_hash == hash_pat(token),
            PersonalAccessToken.revoked_at.is_(None),
        )
    )
    pat = result.scalar_one_or_none()
    if pat is None:
        return None
    pat.last_used_at = datetime.utcnow()
    await db.flush()
    return pat.user_id, pat.agent_id
