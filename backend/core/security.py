"""FastAPI security dependencies.

These plug into route handlers so a protected endpoint just needs:

    from core.security import CurrentUser, RequireLiveUser

    @router.post("/sensitive")
    async def sensitive(user: CurrentUser):
        ...

    @router.post("/flip-live")
    async def flip_live(user: RequireLiveUser):
        ...

``CurrentUser`` rejects with 401 if no/expired/invalid token is
presented. ``RequireLiveUser`` additionally rejects with 403 when
the user is authenticated but lacks ``is_live_enabled=True``.

Order of operations on each request:
1. Extract bearer token from ``Authorization: Bearer <jwt>``
2. Decode + signature check + expiry check
3. Look up the user by ``sub`` in the DB
4. Confirm ``is_active=True``
5. (RequireLiveUser only) confirm ``is_live_enabled=True``

Step 3 is a DB hit per request; that's intentional so revoking a
user (setting is_active=False) is instantly effective without
waiting for the token to expire.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import AuthError, decode_access_token, is_token_expired
from core.db import get_async_db
from core.models.auth import User
from core.users import get_user

# Token URL must match the actual /api/auth/login route.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


async def _resolve_user(
    token: str,
    db: AsyncSession,
) -> User:
    try:
        payload = decode_access_token(token)
    except AuthError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        ) from e

    if is_token_expired(payload):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token expired",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # We're async on the route but sync on the user lookup — use a
    # sync session via the async wrapper since users table is tiny.
    # Run inside the async session for transactional consistency.
    from sqlalchemy import select

    stmt = select(User).where(User.username == payload.subject)
    result = await db.execute(stmt)
    user = result.scalars().first()

    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="user not found or inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[AsyncSession, Depends(get_async_db)],
) -> User:
    return await _resolve_user(token, db)


async def require_live_user(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    if not user.is_live_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "user lacks is_live_enabled flag — operator must explicitly "
                "enable live trading on their account before live actions are permitted"
            ),
        )
    return user


# Public type aliases for ergonomic use in routes
CurrentUser = Annotated[User, Depends(get_current_user)]
RequireLiveUser = Annotated[User, Depends(require_live_user)]
