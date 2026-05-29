"""User repository + bootstrap.

Thin wrapper around the ``users`` table. Lookup, create, authenticate,
bootstrap-on-first-start. Authentication functions here return the
ORM user row (or None) so callers can inspect ``is_live_enabled``
without a second query.

The bootstrap function creates exactly one operator account on first
launch using ``Settings.bootstrap_username`` / ``bootstrap_password``.
Once a user exists, bootstrap is a no-op so the credentials in .env
become inert (operator should rotate via the API in Phase 5).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.auth import AuthError, hash_password, verify_password
from core.config import get_settings
from core.logging import get_logger
from core.models.auth import User

log = get_logger(__name__)


def get_user(session: Session, username: str) -> Optional[User]:
    stmt = select(User).where(User.username == username)
    return session.scalars(stmt).first()


def list_users(session: Session) -> list[User]:
    return list(session.scalars(select(User).order_by(User.id)))


def create_user(
    session: Session,
    *,
    username: str,
    password: str,
    full_name: Optional[str] = None,
    is_live_enabled: bool = False,
) -> User:
    """Create a new operator. Raises if the username already exists."""
    if get_user(session, username) is not None:
        raise AuthError(f"user {username!r} already exists")
    user = User(
        username=username,
        password_hash=hash_password(password),
        full_name=full_name,
        is_active=True,
        is_live_enabled=is_live_enabled,
    )
    session.add(user)
    session.flush()
    log.info("user.created", username=username, is_live_enabled=is_live_enabled)
    return user


def authenticate(session: Session, username: str, password: str) -> Optional[User]:
    """Return the user if credentials match, else None.

    Never logs the attempted password. Updates ``last_login_at`` on
    success. Constant-time-ish: we always run verify_password, even
    when the user doesn't exist, so we don't leak existence via
    timing.
    """
    user = get_user(session, username)
    if user is None:
        # Spend approximately the same time as a real verify so timing
        # analysis can't distinguish "unknown user" from "wrong pw".
        verify_password(password, "$2b$12$invalid.invalid.invalid.invalid.invalid.invalid.invalid")
        return None
    if not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    user.last_login_at = datetime.now(UTC)
    session.flush()
    return user


def set_password(session: Session, user: User, new_password: str) -> None:
    user.password_hash = hash_password(new_password)
    session.flush()
    log.info("user.password_changed", username=user.username)


def set_live_enabled(session: Session, user: User, enabled: bool) -> None:
    user.is_live_enabled = enabled
    session.flush()
    log.info("user.live_flag_changed", username=user.username, enabled=enabled)


def bootstrap_if_empty(session: Session) -> Optional[User]:
    """Create the single operator from .env credentials IF AND ONLY IF
    the users table is empty.

    Idempotent: after the first call, returns None on every subsequent
    invocation so it's safe to call from FastAPI's lifespan handler
    on every startup.
    """
    existing = list_users(session)
    if existing:
        return None

    settings = get_settings()
    username = settings.bootstrap_username
    password = settings.bootstrap_password.get_secret_value()
    if not password:
        log.warning(
            "bootstrap.skipped",
            reason="BOOTSTRAP_PASSWORD not set; the system has zero users "
                   "and login will fail until one is created out-of-band.",
        )
        return None

    user = create_user(
        session,
        username=username,
        password=password,
        full_name="woonam operator",
        is_live_enabled=False,
    )
    log.info(
        "bootstrap.user_created",
        username=username,
        note="rotate this password through the API as soon as possible",
    )
    return user
