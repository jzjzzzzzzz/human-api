from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from fastapi import Cookie, Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .config import Settings, get_settings
from .db import SessionLocal
from .errors import HumanApiError
from .models import BrowserSession

_hasher = PasswordHasher()


def digest(value: str, pepper: str) -> str:
    return hmac.new(pepper.encode(), value.encode(), hashlib.sha256).hexdigest()


def password_hash(password: str) -> str:
    return _hasher.hash(password)


def password_valid(encoded: str, password: str) -> bool:
    try:
        return _hasher.verify(encoded, password)
    except Exception:
        return False


async def session_scope():
    async with SessionLocal() as session:
        yield session


async def current_session(
    human_session: str | None = Cookie(default=None),
    db: AsyncSession = Depends(session_scope),
    settings: Settings = Depends(get_settings),
) -> BrowserSession:
    if not human_session:
        raise HumanApiError(401, "Authentication required.", "authentication_required")
    token_digest = digest(human_session, settings.session_token_pepper)
    record = await db.scalar(
        select(BrowserSession)
        .options(selectinload(BrowserSession.user))
        .where(
            BrowserSession.token_digest == token_digest,
            BrowserSession.expires_at > datetime.now(UTC),
        )
    )
    if record is None or not record.user.enabled:
        raise HumanApiError(401, "Session is invalid or expired.", "invalid_session")
    if record.user.role not in {"responder", "admin"}:
        raise HumanApiError(403, "Responder permission required.", "responder_required")
    return record


async def state_session(
    csrf: str | None = Header(default=None, alias="X-CSRF-Token"),
    record: BrowserSession = Depends(current_session),
) -> BrowserSession:
    if not csrf or not hmac.compare_digest(csrf, record.csrf_token):
        raise HumanApiError(403, "CSRF validation failed.", "csrf_failed")
    return record


async def admin_session(record: BrowserSession = Depends(state_session)) -> BrowserSession:
    if record.user.role != "admin":
        raise HumanApiError(403, "Administrator permission required.", "admin_required")
    return record


def new_browser_session(user_id: str, settings: Settings) -> tuple[BrowserSession, str]:
    raw = secrets.token_urlsafe(32)
    return (
        BrowserSession(
            id=str(uuid.uuid4()),
            token_digest=digest(raw, settings.session_token_pepper),
            csrf_token=secrets.token_urlsafe(24),
            user_id=user_id,
            expires_at=datetime.now(UTC) + timedelta(hours=12),
        ),
        raw,
    )
