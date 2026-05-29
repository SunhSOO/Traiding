# Alembic migrations

Database schema for woonam-auto-trading lives here. All schema changes
go through Alembic; no ad-hoc DDL.

## Usage

```bash
cd backend
# Apply all pending migrations
alembic upgrade head

# Roll back one revision
alembic downgrade -1

# Generate a new revision after editing core/models/*.py
alembic revision --autogenerate -m "describe change"

# Inspect history
alembic history
alembic current
```

## Conventions

- One physical change per revision. Never combine "add column X" with
  "rename column Y" — they should be separate revisions.
- Always write `downgrade()` that actually reverses `upgrade()`. We
  use rollbacks during development; one-way migrations break that.
- For TimescaleDB hypertables, the migration that creates the parent
  table also calls `op.execute("SELECT create_hypertable(...)")`.
- Run `alembic upgrade head` after pulling from `main` even if you
  don't think you need it — somebody might have added a column.

## Initial schema (revision 0001)

Tables created:
- `securities` — universe master (KR/US tickers, active + delisted)
- `exchange_calendars` — per-market trading-day cache
- `fx_rates` — daily FX rates with as_of stamp
- `decision_audit` — every trading decision (append-only)
- `risk_snapshots` — 6-limit risk check results
- `data_freshness` — last-update timestamps per data source
- `secret_metadata` — catalog of expected secret keys (no values)
- `paper_accounts` / `paper_positions` / `paper_trades` — virtual trading
- `users` — operator accounts (JWT auth in Phase 0.4)
