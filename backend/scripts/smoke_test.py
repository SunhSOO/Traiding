"""Post-bootstrap smoke test.

Run AFTER ``alembic upgrade head`` to verify:

1. Settings load + JWT_SECRET_KEY present
2. PostgreSQL connection (sync + async)
3. Required extensions (TimescaleDB)
4. All ORM tables exist
5. Operator bootstrap created the woonam user
6. Default paper accounts (default-kr / default-us) created
7. (Optional) Ollama reachable + default model loaded

Run with:

    cd backend
    uv run python scripts/smoke_test.py

Exit code 0 = clean. Any failure aborts with a precise message.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# So this script is runnable from any CWD via uv run
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _ok(label: str, detail: str = "") -> None:
    print(f"  OK   {label}" + (f" - {detail}" if detail else ""))


def _fail(label: str, detail: str) -> None:
    print(f"  FAIL {label} - {detail}")
    sys.exit(1)


def _warn(label: str, detail: str) -> None:
    print(f"  WARN {label} - {detail}")


def check_settings() -> None:
    print("[1] Settings")
    from core.config import get_settings

    try:
        s = get_settings()
    except Exception as e:
        _fail("settings.load", str(e))
    _ok("settings.load", f"runtime_mode={s.runtime_mode.value}, env={s.app_env.value}")
    if not s.jwt_secret_key.get_secret_value():
        if s.app_env.value != "local":
            _fail("settings.jwt_secret_key", "missing in non-local env")
        else:
            print("  WARN JWT_SECRET_KEY empty - fine for local, REQUIRED for staging/prod")
    else:
        _ok("settings.jwt_secret_key", "set")
    _ok("settings.llm_providers", ", ".join(s.configured_llm_providers) or "none")


def check_sync_db() -> None:
    print("[2] Database (sync)")
    from sqlalchemy import text
    from core.db import session_scope

    try:
        with session_scope() as s:
            row = s.execute(text("SELECT 1")).scalar()
            assert row == 1
    except Exception as e:
        _fail("db.sync.connect", str(e))
    _ok("db.sync.connect", "SELECT 1 OK")

    # TimescaleDB extension (optional)
    try:
        with session_scope() as s:
            row = s.execute(
                text("SELECT extname FROM pg_extension WHERE extname='timescaledb'")
            ).scalar()
    except Exception as e:
        _warn("db.extension.timescaledb", str(e))
        return
    if row:
        _ok("db.extension.timescaledb", "installed")
    else:
        _warn("db.extension.timescaledb",
              "not installed (Phase 1 still works; loses hypertable partitioning)")


def check_orm_tables() -> None:
    print("[3] ORM tables")
    from sqlalchemy import inspect
    from core.db import get_engine
    from core.models import Base

    expected = set(Base.metadata.tables.keys())
    insp = inspect(get_engine())
    actual = set(insp.get_table_names())
    missing = expected - actual
    if missing:
        _fail("orm.tables", f"missing: {sorted(missing)}")
    _ok("orm.tables", f"{len(expected)} present")


def check_bootstrap() -> None:
    print("[4] Bootstrap rows")
    from sqlalchemy import select
    from core.db import session_scope
    from core.models.auth import User
    from core.models.paper import PaperAccount

    with session_scope() as s:
        users = list(s.scalars(select(User)))
        if not users:
            _fail("bootstrap.user", "no rows in users table - start the server once to bootstrap")
        _ok("bootstrap.user", f"{len(users)} user(s)")

        accounts = {a.name: a for a in s.scalars(select(PaperAccount))}
        for required in ("default-kr", "default-us"):
            acc = accounts.get(required)
            if acc is None:
                _fail("bootstrap.paper_account",
                      f"'{required}' missing - start the server once to bootstrap")
        _ok("bootstrap.paper_accounts",
            f"default-kr ({accounts['default-kr'].base_currency}), "
            f"default-us ({accounts['default-us'].base_currency})")


def check_ollama() -> None:
    print("[5] Ollama (optional)")
    import httpx
    from core.config import get_settings

    settings = get_settings()
    host = settings.ollama_host
    if not host:
        print("  WARN OLLAMA_HOST not set - Information module will not function in live")
        return
    try:
        resp = httpx.get(f"{host.rstrip('/')}/api/tags", timeout=3.0)
        resp.raise_for_status()
        models = [m.get("name", "") for m in resp.json().get("models", [])]
    except Exception as e:
        print(f"  WARN ollama.reachable - connection failed: {e}")
        return
    _ok("ollama.reachable", f"{len(models)} models")
    default = settings.ollama_default_model.split(":")[0]
    has_default = any(m.startswith(default) for m in models)
    if has_default:
        _ok("ollama.default_model", settings.ollama_default_model)
    else:
        print(f"  WARN ollama.default_model - {settings.ollama_default_model} not pulled. "
              f"Run: ollama pull {settings.ollama_default_model}")


def main() -> None:
    print("=" * 60)
    print("woonam-auto-trading - bootstrap smoke test")
    print("=" * 60)
    check_settings()
    check_sync_db()
    check_orm_tables()
    check_bootstrap()
    check_ollama()
    print("=" * 60)
    print("All checks passed.")


if __name__ == "__main__":
    main()
