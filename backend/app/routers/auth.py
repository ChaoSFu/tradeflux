from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ..auth import create_access_token, require_auth
from ..config import settings

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest):
    if (
        body.username != settings.ADMIN_USERNAME
        or body.password != settings.ADMIN_PASSWORD
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )
    token = create_access_token(body.username)
    return LoginResponse(access_token=token, username=body.username)


@router.get("/me")
def me(username: str = Depends(require_auth)):
    """验证 token 有效性，返回当前用户信息。"""
    return {"username": username, "is_admin": True}
