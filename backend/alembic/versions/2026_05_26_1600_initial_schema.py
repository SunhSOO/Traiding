"""initial schema

Phase 0.2 — creates all 11 Phase-0 tables, enables the TimescaleDB
extension (used in Phase 1 for time-series ingestion tables), and
seeds `secret_metadata` with the full catalog of expected keys.

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-26 16:00:00
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Seed payload for secret_metadata so the catalog is populated the
# moment the DB is created. Adding a new secret key in code should
# also add a row here.
SECRET_CATALOG: list[tuple[str, str, str]] = [
    ("JWT_SECRET_KEY", "Symmetric secret for issuing/verifying JWTs.", "auth"),
    ("DART_API_KEY", "OpenDART API key (free, https://opendart.fss.or.kr).", "data.fundamental.kr,data.disclosures.kr"),
    ("BIGKINDS_ACCESS_KEY", "BIGKinds research API key for KR news archive.", "data.news.kr"),
    ("NAVER_CLIENT_ID", "Naver Developers Search API client id.", "data.news.kr"),
    ("NAVER_CLIENT_SECRET", "Naver Developers Search API client secret.", "data.news.kr"),
    ("BOK_ECOS_API_KEY", "Bank of Korea ECOS macro data API.", "data.macro.kr"),
    ("SEC_USER_AGENT", "Identifying User-Agent for SEC EDGAR requests.", "data.fundamental.us,data.disclosures.us"),
    ("FRED_API_KEY", "St Louis Fed FRED macro data API (free).", "data.macro.us"),
    ("GROQ_API_KEY", "Groq inference API key (free tier).", "core.llm"),
    ("GEMINI_API_KEY", "Google AI Studio Gemini API key (free tier).", "core.llm"),
    ("MT5_LOGIN", "MetaTrader 5 account login (FX/gold only).", "brokers.mt5"),
    ("MT5_PASSWORD", "MetaTrader 5 account password.", "brokers.mt5"),
    ("MT5_SERVER", "MetaTrader 5 server address.", "brokers.mt5"),
]


def upgrade() -> None:
    # ── TimescaleDB extension (optional) ──
    # Used by Phase 1 time-series tables for partitioning + chunk drops.
    # Plain PostgreSQL works fine for the rest of the schema; the
    # extension just unlocks hypertable conversions later. Wrap in a
    # SAVEPOINT so a failed CREATE EXTENSION doesn't abort the outer
    # migration transaction (alembic wraps each upgrade in one).
    conn = op.get_bind()
    try:
        with conn.begin_nested():
            conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE")
    except Exception as e:
        import warnings
        warnings.warn(
            f"TimescaleDB extension not installed: {e}. "
            "Phase 1 ingestion still works on plain PostgreSQL; you "
            "just lose hypertable partitioning. Install per "
            "https://docs.timescale.com/self-hosted/latest/install/",
            stacklevel=2,
        )

    # ── securities ──
    op.create_table(
        "securities",
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column("ticker", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("name_en", sa.String(length=256), nullable=True),
        sa.Column("isin", sa.String(length=12), nullable=True),
        sa.Column("cik", sa.String(length=10), nullable=True),
        sa.Column("corp_code", sa.String(length=8), nullable=True),
        sa.Column("exchange", sa.String(length=16), nullable=True),
        sa.Column("index_membership", sa.String(length=64), nullable=True),
        sa.Column("sector", sa.String(length=128), nullable=True),
        sa.Column("industry", sa.String(length=128), nullable=True),
        sa.Column("listed_date", sa.Date(), nullable=True),
        sa.Column("delisted_date", sa.Date(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("market", "ticker", name="pk_securities"),
    )
    op.create_index("ix_securities_active_market", "securities", ["is_active", "market"])
    op.create_index("ix_securities_sector", "securities", ["market", "sector"])
    op.create_index("ix_securities_isin", "securities", ["isin"])
    op.create_index("ix_securities_cik", "securities", ["cik"])
    op.create_index("ix_securities_corp_code", "securities", ["corp_code"])
    op.create_index("ix_securities_market", "securities", ["market"])

    # ── exchange_calendars ──
    op.create_table(
        "exchange_calendars",
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("is_trading_day", sa.Boolean(), nullable=False),
        sa.Column("session_open_local", sa.Time(), nullable=True),
        sa.Column("session_close_local", sa.Time(), nullable=True),
        sa.Column("open_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_utc", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("market", "session_date", name="pk_exchange_calendars"),
    )
    op.create_index("ix_exchange_calendars_market", "exchange_calendars", ["market"])

    # ── fx_rates ──
    op.create_table(
        "fx_rates",
        sa.Column("rate_date", sa.Date(), nullable=False),
        sa.Column("base_currency", sa.String(length=3), nullable=False),
        sa.Column("quote_currency", sa.String(length=3), nullable=False),
        sa.Column("rate", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("as_of_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("rate_date", "base_currency", "quote_currency", name="pk_fx_rates"),
    )

    # ── risk_snapshots (created BEFORE decision_audit because of FK) ──
    op.create_table(
        "risk_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("snapshot_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column("ticker", sa.String(length=16), nullable=True),
        sa.Column("max_lot_pass", sa.Boolean(), nullable=False),
        sa.Column("max_lot_observed", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("max_lot_limit", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("daily_loss_pass", sa.Boolean(), nullable=False),
        sa.Column("daily_loss_observed", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("daily_loss_limit", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("consecutive_loss_pass", sa.Boolean(), nullable=False),
        sa.Column("consecutive_loss_observed", sa.Integer(), nullable=True),
        sa.Column("consecutive_loss_limit", sa.Integer(), nullable=True),
        sa.Column("max_positions_pass", sa.Boolean(), nullable=False),
        sa.Column("max_positions_observed", sa.Integer(), nullable=True),
        sa.Column("max_positions_limit", sa.Integer(), nullable=True),
        sa.Column("spread_pass", sa.Boolean(), nullable=False),
        sa.Column("spread_observed_bps", sa.Numeric(precision=8, scale=3), nullable=True),
        sa.Column("spread_limit_bps", sa.Numeric(precision=8, scale=3), nullable=True),
        sa.Column("symbol_allowed_pass", sa.Boolean(), nullable=False),
        sa.Column("all_passed", sa.Boolean(), nullable=False),
        sa.Column("failures", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_risk_snapshots_market_ts", "risk_snapshots", ["market", "snapshot_ts"])
    op.create_index("ix_risk_snapshots_ticker", "risk_snapshots", ["ticker"])
    op.create_index("ix_risk_snapshots_market", "risk_snapshots", ["market"])

    # ── decision_audit ──
    op.create_table(
        "decision_audit",
        sa.Column("id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column("ticker", sa.String(length=16), nullable=False),
        sa.Column("decision_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fundamental_score", sa.Numeric(precision=6, scale=3), nullable=True),
        sa.Column("fundamental_confidence", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("technical_score", sa.Numeric(precision=6, scale=3), nullable=True),
        sa.Column("technical_confidence", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("information_score", sa.Numeric(precision=6, scale=3), nullable=True),
        sa.Column("information_confidence", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("composite_score", sa.Numeric(precision=6, scale=3), nullable=True),
        sa.Column("composite_confidence", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("size_value", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("size_currency", sa.String(length=3), nullable=True),
        sa.Column("risk_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("model_version", sa.String(length=64), nullable=True),
        sa.Column("corrects_id", PG_UUID(as_uuid=True), nullable=True),
        sa.Column("weights", JSONB(), nullable=True),
        sa.Column("gate_results", JSONB(), nullable=True),
        sa.Column("inputs_snapshot", JSONB(), nullable=True),
        sa.Column("llm_outputs", JSONB(), nullable=True),
        sa.Column("execution_result", JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["risk_snapshot_id"], ["risk_snapshots.id"]),
        sa.ForeignKeyConstraint(["corrects_id"], ["decision_audit.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_decision_audit_market_ticker_ts", "decision_audit", ["market", "ticker", "decision_ts"])
    op.create_index("ix_decision_audit_action_ts", "decision_audit", ["action", "decision_ts"])
    op.create_index("ix_decision_audit_ticker", "decision_audit", ["ticker"])
    op.create_index("ix_decision_audit_market", "decision_audit", ["market"])
    op.create_index("ix_decision_audit_risk_snapshot_id", "decision_audit", ["risk_snapshot_id"])
    op.create_index("ix_decision_audit_corrects_id", "decision_audit", ["corrects_id"])

    # ── data_freshness ──
    op.create_table(
        "data_freshness",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("market", sa.String(length=8), nullable=True),
        sa.Column("scope", sa.String(length=64), nullable=False),
        sa.Column("last_success_ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("rows_last_run", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "market", "scope", name="uq_data_freshness_key"),
    )

    # ── secret_metadata ──
    op.create_table(
        "secret_metadata",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("required_for", sa.Text(), nullable=True),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("key"),
    )
    # Seed the catalog.
    op.bulk_insert(
        sa.table(
            "secret_metadata",
            sa.column("key", sa.String),
            sa.column("description", sa.Text),
            sa.column("required_for", sa.Text),
        ),
        [
            {"key": k, "description": d, "required_for": r}
            for (k, d, r) in SECRET_CATALOG
        ],
    )

    # ── paper_accounts ──
    op.create_table(
        "paper_accounts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("base_currency", sa.String(length=3), nullable=False),
        sa.Column("initial_balance", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("current_balance", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_paper_accounts_name"),
    )

    # ── paper_positions ──
    op.create_table(
        "paper_positions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column("ticker", sa.String(length=16), nullable=False),
        sa.Column("side", sa.String(length=4), nullable=False),
        sa.Column("volume", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("entry_price", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("current_price", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("sl", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("tp", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("entry_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["paper_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_paper_positions_account_market_ticker",
        "paper_positions",
        ["account_id", "market", "ticker"],
    )
    op.create_index("ix_paper_positions_market", "paper_positions", ["market"])

    # ── paper_trades ──
    op.create_table(
        "paper_trades",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column("ticker", sa.String(length=16), nullable=False),
        sa.Column("side", sa.String(length=4), nullable=False),
        sa.Column("volume", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("entry_price", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("exit_price", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("entry_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exit_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pnl", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("pnl_base_ccy", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("commission", sa.Numeric(precision=18, scale=4), nullable=False, server_default="0"),
        sa.Column("tax", sa.Numeric(precision=18, scale=4), nullable=False, server_default="0"),
        sa.Column("slippage_bps", sa.Numeric(precision=8, scale=3), nullable=True),
        sa.Column("decision_audit_id", sa.String(length=36), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["account_id"], ["paper_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_paper_trades_account_close_ts", "paper_trades", ["account_id", "exit_ts"]
    )
    op.create_index("ix_paper_trades_market_ticker", "paper_trades", ["market", "ticker"])
    op.create_index("ix_paper_trades_market", "paper_trades", ["market"])

    # ── users ──
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=256), nullable=False),
        sa.Column("full_name", sa.String(length=128), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_live_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )


def downgrade() -> None:
    # Reverse order of upgrade() so FK constraints don't block drops.
    op.drop_table("users")
    op.drop_index("ix_paper_trades_market", table_name="paper_trades")
    op.drop_index("ix_paper_trades_market_ticker", table_name="paper_trades")
    op.drop_index("ix_paper_trades_account_close_ts", table_name="paper_trades")
    op.drop_table("paper_trades")
    op.drop_index("ix_paper_positions_market", table_name="paper_positions")
    op.drop_index("ix_paper_positions_account_market_ticker", table_name="paper_positions")
    op.drop_table("paper_positions")
    op.drop_table("paper_accounts")
    op.drop_table("secret_metadata")
    op.drop_table("data_freshness")
    op.drop_index("ix_decision_audit_corrects_id", table_name="decision_audit")
    op.drop_index("ix_decision_audit_risk_snapshot_id", table_name="decision_audit")
    op.drop_index("ix_decision_audit_market", table_name="decision_audit")
    op.drop_index("ix_decision_audit_ticker", table_name="decision_audit")
    op.drop_index("ix_decision_audit_action_ts", table_name="decision_audit")
    op.drop_index("ix_decision_audit_market_ticker_ts", table_name="decision_audit")
    op.drop_table("decision_audit")
    op.drop_index("ix_risk_snapshots_market", table_name="risk_snapshots")
    op.drop_index("ix_risk_snapshots_ticker", table_name="risk_snapshots")
    op.drop_index("ix_risk_snapshots_market_ts", table_name="risk_snapshots")
    op.drop_table("risk_snapshots")
    op.drop_table("fx_rates")
    op.drop_index("ix_exchange_calendars_market", table_name="exchange_calendars")
    op.drop_table("exchange_calendars")
    op.drop_index("ix_securities_market", table_name="securities")
    op.drop_index("ix_securities_corp_code", table_name="securities")
    op.drop_index("ix_securities_cik", table_name="securities")
    op.drop_index("ix_securities_isin", table_name="securities")
    op.drop_index("ix_securities_sector", table_name="securities")
    op.drop_index("ix_securities_active_market", table_name="securities")
    op.drop_table("securities")
    # We don't drop the timescaledb extension; it may be shared with
    # other databases on the same cluster.
