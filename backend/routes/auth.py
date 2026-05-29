"""Auth routes — login, identity, password rotation, live-flag toggle.

Login follows the OAuth2 password flow shape that FastAPI's
``OAuth2PasswordBearer`` expects (``application/x-www-form-urlencoded``
with ``username`` and ``password`` fields). This makes the auto-generated
OpenAPI docs immediately usable.

We do NOT issue refresh tokens — sessions are short (8h default) and
the operator just logs in again. Refresh tokens add attack surface for
a single-user local app.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import create_access_token, hash_password, verify_password
from core.config import get_settings
from core.db import get_async_db
from core.logging import get_logger
from core.models.auth import User
from core.security import CurrentUser, RequireLiveUser

log = get_logger(__name__)
router = APIRouter()


# ── Schemas ──
class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int


class UserResponse(BaseModel):
    id: int
    username: str
    full_name: str | None
    is_active: bool
    is_live_enabled: bool


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class LiveFlagChange(BaseModel):
    enable: bool
    confirmation: str   # operator must type exactly "I UNDERSTAND THE RISK"


# ── Routes ──
@router.post("/login", response_model=TokenResponse)
async def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[AsyncSession, Depends(get_async_db)],
) -> TokenResponse:
    settings = get_settings()

    stmt = select(User).where(User.username == form.username)
    result = await db.execute(stmt)
    user = result.scalars().first()

    if user is None or not user.is_active:
        # Spend timing on a dummy verify so we don't leak existence
        verify_password(form.password, "$2b$12$invalid.invalid.invalid.invalid.invalid.invalid.invalid")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
    if not verify_password(form.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")

    token = create_access_token(
        subject=user.username,
        is_live_enabled=user.is_live_enabled,
    )
    log.info("auth.login", username=user.username)
    return TokenResponse(
        access_token=token,
        expires_in_minutes=settings.jwt_expire_minutes,
    )


@router.get("/me", response_model=UserResponse)
async def me(user: CurrentUser) -> UserResponse:
    return UserResponse(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        is_active=user.is_active,
        is_live_enabled=user.is_live_enabled,
    )


@router.post("/password")
async def change_password(
    payload: PasswordChange,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
) -> dict[str, str]:
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="current password incorrect")
    user.password_hash = hash_password(payload.new_password)
    await db.commit()
    log.info("auth.password_changed", username=user.username)
    return {"status": "ok"}


@router.post("/live-flag")
async def set_live_flag(
    payload: LiveFlagChange,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_db)],
) -> dict[str, str | bool]:
    """Toggle the operator's ``is_live_enabled`` flag.

    Enabling requires typing the exact safety phrase to prevent
    accidental flips. Disabling does not (we want kill-switch behaviour
    to be friction-free).
    """
    if payload.enable:
        if payload.confirmation != "I UNDERSTAND THE RISK":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    'to enable live trading, send confirmation="I UNDERSTAND THE RISK" '
                    "exactly. This is a deliberate friction so live mode cannot be "
                    "flipped on by a click in the wrong panel."
                ),
            )
    user.is_live_enabled = payload.enable
    await db.commit()
    log.info("auth.live_flag_changed", username=user.username, enabled=payload.enable)
    return {"username": user.username, "is_live_enabled": user.is_live_enabled}


@router.post("/_test/live")
async def _test_live_dep(_: RequireLiveUser) -> dict[str, str]:
    """Smoke endpoint — returns ok ONLY when the caller has both a
    valid token AND ``is_live_enabled=True``. Used by integration
    tests in Phase 5; not a real product feature."""
    return {"status": "ok"}
