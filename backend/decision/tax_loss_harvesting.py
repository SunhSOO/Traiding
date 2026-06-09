"""Tax Loss Harvesting (TLH) — Wave 1.

Logic:
1. For each open paper_position with unrealized loss > threshold:
   - Identify wash-sale window (30 days before + 30 days after for US,
     KR has no wash-sale rule but 1-month re-entry caution still applies).
   - Check if any signal exists for the same/similar ticker.
2. If no fresh BUY signal AND loss > tax_benefit_threshold:
   - SELL to realize loss (counts toward yearly tax offset)
   - Lock the ticker out of buy list for 31 days (US wash-sale).
3. Optionally substitute with sector ETF or peer (same-sector different
   ticker) to maintain exposure.

For KR:
- 대주주 양도소득세 적용 (보유 한도 초과 시). 우리는 개인 운영 가정.
- 12월 말 매도 → 1월 재매수 패턴 (세금 연도 분리).

Tracking:
- `wash_sale_locks` (in-memory cache, persisted to DB if running paper)
- `realized_losses_ytd` (per market, per year)

This module is called by decision/runner.py before order placement.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date as DateType, datetime, timedelta
from typing import Optional

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from core.models.paper import PaperPosition, PaperTrade


@dataclass
class TaxConfig:
    market: str
    wash_sale_days: int = 31         # US 30-day rule + 1 day buffer
    tlh_loss_threshold_pct: float = -0.05   # realize loss > 5%
    tlh_min_loss_value: float = 100.0       # absolute $ min
    ytd_loss_cap: float = 3000.0    # US tax: $3k/year deductible
    substitute_strategy: str = "sector_etf"  # 'sector_etf' | 'peer' | 'none'

    @classmethod
    def for_market(cls, market: str) -> "TaxConfig":
        if market == "US":
            return cls(
                market="US", wash_sale_days=31,
                tlh_loss_threshold_pct=-0.05,
                tlh_min_loss_value=100.0,
                ytd_loss_cap=3000.0,
                substitute_strategy="sector_etf",
            )
        # KR — wash-sale rule 없음. 대주주 외엔 양도소득세 없음 (현재 가정).
        return cls(
            market="KR", wash_sale_days=7,   # 보수적 buffer
            tlh_loss_threshold_pct=-0.05,
            tlh_min_loss_value=0.0,    # no benefit threshold for non-대주주
            ytd_loss_cap=0.0,
            substitute_strategy="sector_etf",
        )


@dataclass
class TLHDecision:
    ticker: str
    action: str               # "harvest_sell" | "skip" | "substitute"
    loss_value: float
    substitute_ticker: Optional[str] = None
    reason: str = ""


@dataclass
class WashSaleLock:
    """Tracks tickers blocked from new BUY due to recent loss realization."""
    market: str
    ticker: str
    blocked_until: DateType
    realized_loss: float


# In-memory cache; production should persist to DB
_LOCKS: dict[tuple[str, str], WashSaleLock] = {}


def is_locked(market: str, ticker: str, as_of: DateType) -> bool:
    key = (market, ticker)
    lock = _LOCKS.get(key)
    if lock is None:
        return False
    return as_of <= lock.blocked_until


def evaluate_position(
    position: PaperPosition,
    current_price: float,
    as_of: DateType,
    config: TaxConfig,
) -> TLHDecision:
    """Determine if this position is a TLH candidate."""
    entry_price = float(position.entry_price)
    volume = float(position.volume)
    market_value = current_price * volume
    cost_basis = entry_price * volume

    if position.side == "BUY":
        pnl = market_value - cost_basis
    else:
        pnl = cost_basis - market_value

    if cost_basis <= 0:
        return TLHDecision(ticker=position.ticker, action="skip",
                            loss_value=0.0, reason="zero cost basis")

    pnl_pct = pnl / cost_basis
    if pnl >= 0 or pnl_pct > config.tlh_loss_threshold_pct:
        return TLHDecision(
            ticker=position.ticker, action="skip",
            loss_value=pnl, reason=f"loss {pnl_pct:.1%} not below {config.tlh_loss_threshold_pct:.1%}",
        )

    if abs(pnl) < config.tlh_min_loss_value:
        return TLHDecision(
            ticker=position.ticker, action="skip",
            loss_value=pnl, reason=f"abs loss {abs(pnl):.0f} below {config.tlh_min_loss_value}",
        )

    # Determine substitute (sector ETF / peer)
    sub = None
    if config.substitute_strategy == "sector_etf":
        sub = _sector_etf_for(position.market, position.ticker)

    return TLHDecision(
        ticker=position.ticker, action="harvest_sell" if not sub else "substitute",
        loss_value=pnl, substitute_ticker=sub,
        reason=f"realize loss {pnl:.0f} ({pnl_pct:.1%}); block {config.wash_sale_days}d",
    )


def register_wash_sale_lock(
    market: str, ticker: str, sold_date: DateType, realized_loss: float,
    config: TaxConfig,
) -> None:
    """Add ticker to wash-sale lock list after TLH sell."""
    blocked_until = sold_date + timedelta(days=config.wash_sale_days)
    _LOCKS[(market, ticker)] = WashSaleLock(
        market=market, ticker=ticker,
        blocked_until=blocked_until, realized_loss=realized_loss,
    )


def _sector_etf_for(market: str, ticker: str) -> Optional[str]:
    """Map a ticker to its sector ETF for substitution."""
    if market != "US":
        return None
    sector_map = {
        "TECH": "XLK", "FINANCIALS": "XLF", "HEALTHCARE": "XLV",
        "ENERGY": "XLE", "CONS_DISC": "XLY", "CONS_STAPLES": "XLP",
        "INDUSTRIALS": "XLI", "MATERIALS": "XLB", "UTILITIES": "XLU",
        "REAL_ESTATE": "XLRE", "COMMUNICATION": "XLC",
    }
    # Caller can pass sector from Security; for now infer would require
    # extra DB lookup — defer to integration in decision/runner.py
    return None


def list_active_locks(as_of: DateType) -> list[WashSaleLock]:
    return [
        lock for lock in _LOCKS.values()
        if as_of <= lock.blocked_until
    ]


def get_ytd_realized_loss(
    session: Session, market: str, year: int,
) -> float:
    """Sum realized losses from paper_trades for given year."""
    start = DateType(year, 1, 1)
    end = DateType(year, 12, 31)
    rows = list(session.execute(
        select(PaperTrade.pnl)
        .where(and_(
            PaperTrade.market == market,
            PaperTrade.exit_ts.isnot(None),
            PaperTrade.exit_ts >= datetime.combine(start, datetime.min.time(), tzinfo=UTC),
            PaperTrade.exit_ts <= datetime.combine(end, datetime.max.time(), tzinfo=UTC),
            PaperTrade.pnl < 0,
        ))
    ).all())
    return sum(float(r[0]) for r in rows) if rows else 0.0
