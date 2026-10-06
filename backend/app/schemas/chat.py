from typing import Any, Dict, Optional

from pydantic import BaseModel


class UserResponse(BaseModel):
    id: str
    username: str
    created_at: str
    agent_permissions: Optional[Dict[str, Any]] = None


class LoginRequest(BaseModel):
    username: str
    password: str
    # Wave 1：可选 agent 身份（JWT 即携带作用域；缺省 None = 共享作用域）
    agent_id: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    user: UserResponse
