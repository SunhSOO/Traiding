"""Declarative base + shared typed columns for all models.

`Market` lives in `core.types` (no SQLAlchemy dep) and is re-exported
here for ergonomic `from core.models.base import Market` access.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, MappedAsDataclass, mapped_column

from core.types import Market  # re-exported

__all__ = [
    "Base",
    "Market",
    "PK_BigInt",
    "CreatedAt",
    "UpdatedAt",
    "market_column",
]


class Base(DeclarativeBase):
    """Single declarative base for all ORM models.

    We deliberately do NOT use ``MappedAsDataclass`` on Base because
    several models need imperative customisation that the dataclass
    integration makes awkward (composite PKs, JSON columns, etc.).
    Individual models can opt in by also inheriting MappedAsDataclass
    if they want dataclass behaviour.
    """

    type_annotation_map = {
        # Force UUID columns to use PostgreSQL's native uuid type.
        uuid.UUID: PG_UUID(as_uuid=True),
    }


# ── Reusable typed columns ──
PK_BigInt = Annotated[
    int,
    mapped_column(BigInteger, primary_key=True, autoincrement=True),
]

CreatedAt = Annotated[
    datetime,
    mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
]

UpdatedAt = Annotated[
    datetime,
    mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    ),
]


def market_column():
    """Reusable column factory: a Market enum stored as VARCHAR(8)."""
    return mapped_column(String(8), nullable=False, index=True)
