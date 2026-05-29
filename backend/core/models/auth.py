"""Authentication tables.

Single-user system (local PC operation), but built with multi-user
support so it remains usable if we later add an operator dashboard
behind a real LAN.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, CreatedAt


class User(Base):
    """Operator account. Passwords are stored as bcrypt hashes via
    passlib (see core/auth.py). The `is_live_enabled` flag is a
    separate gate from `is_active`: a user can be active (can log in
    and view dashboards) but blocked from flipping live trading on."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("username", name="uq_users_username"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    full_name: Mapped[Optional[str]] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_live_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        doc="Separate gate for toggling RUNTIME_MODE=live. Off by default.",
    )
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = CreatedAt
