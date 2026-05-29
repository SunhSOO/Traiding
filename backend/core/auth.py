"""Authentication primitives — password hashing + JWT issue/verify.

DB-touching pieces (user lookup, bootstrap user creation, FastAPI
``get_current_user`` dependency) live in this module too but are
isolated from the pure primitives so they can be unit-tested without
PostgreSQL.

We use:
- **passlib + bcrypt** for password hashing. Salted, slow by design,
  industry-standard. We rely on passlib's deprecation handling so
  upgrading to a newer scheme later (argon2id) is a one-config-line
  change.
- **python-jose** for JWT. HS256 (symmetric). Keys come from
  ``Settings.jwt_secret_key`` — startup gate refuses to enter live
  mode unless this is set.

Live-mode flip (``RUNTIME_MODE=live``) requires an authenticated user
whose ``is_live_enabled=True`` flag is set. The dependency for this
gate lives in routes (Phase 5); the primitive predicate lives here
for testability.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Optional

from core.config import get_settings


# ── Lazy imports so the module is import-safe before `uv sync` ──
def _passlib_context():
    from passlib.context import CryptContext

    return CryptContext(schemes=["bcrypt"], deprecated="auto")


def _jose_jwt():
    from jose import jwt

    return jwt


class AuthError(RuntimeError):
    """Raised on any auth primitive failure that the caller should treat
    as 'rejected' rather than 'unexpected exception'."""


@dataclass(frozen=True)
class TokenPayload:
    """Decoded JWT contents. We deliberately keep this narrow — adding
    more claims means more attack surface."""

    subject: str          # username
    issued_at: datetime
    expires_at: datetime
    is_live_enabled: bool = False


# ── Password hashing ──
def hash_password(plaintext: str) -> str:
    if not plaintext or not isinstance(plaintext, str):
        raise AuthError("password must be a non-empty string")
    # bcrypt has a 72-byte ceiling; we don't truncate silently — long
    # passphrases are accepted up to that limit, beyond which we error
    # so the operator notices.
    if len(plaintext.encode("utf-8")) > 72:
        raise AuthError("password exceeds 72 bytes (bcrypt limit)")
    return _passlib_context().hash(plaintext)


def verify_password(plaintext: str, hashed: str) -> bool:
    if not plaintext or not hashed:
        return False
    try:
        return _passlib_context().verify(plaintext, hashed)
    except Exception:
        return False


# ── JWT ──
def create_access_token(
    *,
    subject: str,
    is_live_enabled: bool = False,
    expires_minutes: Optional[int] = None,
) -> str:
    settings = get_settings()
    secret = settings.jwt_secret_key.get_secret_value()
    if not secret:
        raise AuthError("JWT_SECRET_KEY is not configured")

    minutes = expires_minutes if expires_minutes is not None else settings.jwt_expire_minutes
    now = datetime.now(UTC)
    exp = now + timedelta(minutes=minutes)

    payload: dict[str, Any] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "live": bool(is_live_enabled),
    }
    return _jose_jwt().encode(payload, secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> TokenPayload:
    settings = get_settings()
    secret = settings.jwt_secret_key.get_secret_value()
    if not secret:
        raise AuthError("JWT_SECRET_KEY is not configured")

    try:
        decoded = _jose_jwt().decode(
            token, secret, algorithms=[settings.jwt_algorithm]
        )
    except Exception as e:  # python-jose raises a family of exception types
        raise AuthError(f"invalid token: {e}") from e

    try:
        return TokenPayload(
            subject=str(decoded["sub"]),
            issued_at=datetime.fromtimestamp(int(decoded["iat"]), tz=UTC),
            expires_at=datetime.fromtimestamp(int(decoded["exp"]), tz=UTC),
            is_live_enabled=bool(decoded.get("live", False)),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise AuthError(f"token payload malformed: {e}") from e


def is_token_expired(payload: TokenPayload, *, now: Optional[datetime] = None) -> bool:
    if now is None:
        now = datetime.now(UTC)
    return now >= payload.expires_at
