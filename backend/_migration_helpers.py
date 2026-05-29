"""Shared helpers for migration files.

Centralised so all hypertable conversions follow the same
graceful-degradation pattern on plain PostgreSQL installations
without the TimescaleDB extension."""
from __future__ import annotations

import warnings

from alembic import op


def try_create_hypertable(
    table: str, time_column: str, *, chunk_interval: str,
) -> None:
    """Convert ``table`` into a TimescaleDB hypertable partitioned on
    ``time_column``. Falls back to a no-op (with a warning) when the
    extension isn't installed.

    Uses a SAVEPOINT so a failure doesn't poison the outer migration
    transaction — plain PostgreSQL setups still get the table, just
    without hypertable partitioning."""
    conn = op.get_bind()
    sql = (
        f"SELECT create_hypertable('{table}', '{time_column}', "
        f"chunk_time_interval => INTERVAL '{chunk_interval}', "
        "if_not_exists => TRUE)"
    )
    try:
        with conn.begin_nested():
            conn.exec_driver_sql(sql)
    except Exception as e:
        warnings.warn(
            f"TimescaleDB hypertable conversion skipped for '{table}': {e}. "
            "Table created as plain PostgreSQL — loses partitioning "
            "but stays queryable.",
            stacklevel=2,
        )
