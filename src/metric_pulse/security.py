"""密码、登录会话和基于角色的访问控制。

密码使用随机盐 PBKDF2 哈希；浏览器只保存 HttpOnly 会话标识，数据库保存会话期限。角色
按查看者、操作员、审核员、管理员递增，路由通过依赖注入声明最低权限。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_session
from .models import LoginSession, Role, User


def hash_password(password: str, *, iterations: int = 600_000) -> str:
    """使用随机 128 位盐生成自描述 PBKDF2-SHA256 密码串。"""

    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    """解析密码串并以恒定时间比较摘要；格式错误统一视为验证失败。"""

    try:
        algorithm, raw_iterations, raw_salt, raw_digest = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode(),
            base64.b64decode(raw_salt),
            int(raw_iterations),
        )
        return hmac.compare_digest(digest, base64.b64decode(raw_digest))
    except ValueError, TypeError:
        return False


def create_login_session(db: Session, user: User) -> LoginSession:
    """创建高熵服务端会话并立即提交，使响应设置 Cookie 前记录已经存在。"""

    settings = get_settings()
    session = LoginSession(
        id=secrets.token_urlsafe(32),
        user_id=user.id,
        expires_at=datetime.now(UTC) + timedelta(hours=settings.session_ttl_hours),
    )
    db.add(session)
    db.commit()
    return session


def get_current_user(
    db: Session = Depends(get_session),
    token: str | None = Cookie(default=None, alias=get_settings().session_cookie_name),
) -> User:
    """从 HttpOnly Cookie 解析当前有效用户；过期或停用账户均返回 401。"""

    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    login = db.scalar(select(LoginSession).where(LoginSession.id == token))
    if not login or login.expires_at.replace(tzinfo=UTC) <= datetime.now(UTC):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    user = db.get(User, login.user_id)
    if not user or not user.active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User inactive")
    return user


ROLE_ORDER = {Role.VIEWER: 0, Role.OPERATOR: 1, Role.REVIEWER: 2, Role.ADMIN: 3}


def require_role(minimum: Role):
    """生成 FastAPI 权限依赖，允许最低角色及其上级角色访问。"""

    def dependency(user: User = Depends(get_current_user)) -> User:
        if ROLE_ORDER[Role(user.role)] < ROLE_ORDER[minimum]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return user

    return dependency


def bootstrap_admin(db: Session) -> User:
    """首次启动时幂等创建管理员；已有账号不会被环境变量密码覆盖。"""

    settings = get_settings()
    existing = db.scalar(select(User).where(User.username == settings.bootstrap_username))
    if existing:
        return existing
    user = User(
        username=settings.bootstrap_username,
        password_hash=hash_password(settings.bootstrap_password),
        role=Role.ADMIN,
    )
    db.add(user)
    db.commit()
    return user
