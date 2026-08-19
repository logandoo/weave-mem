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


class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    user: UserResponse
