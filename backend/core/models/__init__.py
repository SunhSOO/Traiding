"""SQLAlchemy ORM models for woonam-auto-trading.

All tables use `(market, ticker)` composite keys where applicable so
KR/US data are never mistakenly mixed. Every table that stores
point-in-time data carries an `as_of_ts` column so look-ahead bias
can be diagnosed by query inspection alone.

The `Base` here is the SINGLE declarative base for the project.
Alembic's `env.py` imports `Base` and `target_metadata = Base.metadata`,
which means every model that subclasses `Base` becomes part of the
migration scope automatically.

Import every model module here so `Base.metadata` knows about them
at import time (a missing import = a missing table in migrations).
"""
from __future__ import annotations

from core.models.base import Base, Market
from core.models.universe import ExchangeCalendar, FxRate, Security
from core.models.audit import (
    DataFreshness,
    DecisionAudit,
    RiskSnapshot,
    SecretMetadata,
)
from core.models.paper import PaperAccount, PaperPosition, PaperTrade
from core.models.auth import User
from core.models.prices import DailyPrice, MacroSeries, UniverseMembership
from core.models.financials import FinancialFact
from core.models.disclosures import Disclosure
from core.models.news import NewsArticle, NewsTickerMention
from core.models.scores import ModuleScore
from core.models.classifications import ArticleClassification
from core.models.training import ClusterWeights, TickerClusterAssignment
from core.models.backfill import BackfillProgress
from core.models.overrides import ClusterWeightOverride
from core.models.backtest import BacktestRunRow
from core.models.regime import MarketRegime
from core.models.integrated import MarketRead, SelectionBasket

__all__ = [
    "Base",
    "Market",
    # universe
    "Security",
    "ExchangeCalendar",
    "FxRate",
    "UniverseMembership",
    # prices / macro
    "DailyPrice",
    "MacroSeries",
    # fundamentals
    "FinancialFact",
    # disclosures
    "Disclosure",
    # news
    "NewsArticle",
    "NewsTickerMention",
    # scores
    "ModuleScore",
    # classifications
    "ArticleClassification",
    # training
    "TickerClusterAssignment",
    "ClusterWeights",
    "ClusterWeightOverride",
    # backfill + backtest
    "BackfillProgress",
    "BacktestRunRow",
    # regime
    "MarketRegime",
    # integrated strategy (selection→execution)
    "MarketRead",
    "SelectionBasket",
    # audit / meta
    "DecisionAudit",
    "RiskSnapshot",
    "DataFreshness",
    "SecretMetadata",
    # paper trading
    "PaperAccount",
    "PaperPosition",
    "PaperTrade",
    # auth
    "User",
]
