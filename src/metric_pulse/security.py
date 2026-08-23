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
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
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
    def dependency(user: User = Depends(get_current_user)) -> User:
        if ROLE_ORDER[Role(user.role)] < ROLE_ORDER[minimum]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return user

    return dependency


def bootstrap_admin(db: Session) -> User:
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
