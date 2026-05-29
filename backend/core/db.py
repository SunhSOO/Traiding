"""SQLAlchemy engine + session management.

We expose both a synchronous engine (used by Alembic migrations,
batch ingestion jobs, scripts) AND an async engine (used by FastAPI
routes). They point at the same physical database but use different
drivers (`psycopg` sync vs `asyncpg`).

Per-request session lifecycle for FastAPI routes is handled by the
`get_db()` and `get_async_db()` dependencies — they ensure the
session is closed even on exceptions.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from core.config import get_settings


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Sync engine. Used by Alembic and any script that doesn't run
    in an async event loop."""
    settings = get_settings()
    return create_engine(
        settings.database_url,
        echo=settings.db_echo_sql,
        pool_pre_ping=True,
        # Conservative pool sizing for a single-PC operator. Bump if
        # concurrent ingestion jobs need more.
        pool_size=5,
        max_overflow=10,
        pool_recycle=1800,
    )


@lru_cache(maxsize=1)
def get_async_engine() -> AsyncEngine:
    """Async engine. Used by FastAPI route handlers."""
    settings = get_settings()
    return create_async_engine(
        settings.database_url_async,
        echo=settings.db_echo_sql,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        pool_recycle=1800,
    )


@lru_cache(maxsize=1)
def _sync_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(
        bind=get_engine(),
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )


@lru_cache(maxsize=1)
def _async_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=get_async_engine(),
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Context manager for a sync session with automatic commit/rollback.

    Use in scripts and batch jobs:

        with session_scope() as s:
            s.add(obj)
    """
    session = _sync_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@asynccontextmanager
async def async_session_scope() -> AsyncGenerator[AsyncSession, None]:
    """Async equivalent of `session_scope`. Same auto commit/rollback
    semantics."""
    session = _async_sessionmaker()()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


# ── FastAPI dependencies ──
def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency for sync routes (rare; prefer async)."""
    session = _sync_sessionmaker()()
    try:
        yield session
    finally:
        session.close()


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for async routes."""
    session = _async_sessionmaker()()
    try:
        yield session
    finally:
        await session.close()


def reset_engines() -> None:
    """For tests: drop cached engines so a fresh DATABASE_URL takes effect."""
    get_engine.cache_clear()
    get_async_engine.cache_clear()
    _sync_sessionmaker.cache_clear()
    _async_sessionmaker.cache_clear()
