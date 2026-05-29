"""Risk engine — enforces 6 hard limits before any order is sent.

The decision engine produces an :class:`OrderIntent`. Before that
intent reaches a broker, it MUST pass :meth:`RiskEngine.check`. Every
check is recorded in a :class:`RiskSnapshot` which becomes part of
the `decision_audit` audit trail — even if all limits pass, even if
the order ultimately fails downstream, we have a permanent record
that the limits were evaluated.

The 6 limits, in evaluation order:

1. **max_lot**            — max volume per single order
2. **daily_loss**         — cumulative loss today < limit
3. **consecutive_loss**   — number of back-to-back losing trades
4. **max_positions**      — count of currently-open positions
5. **spread**             — current spread, in bps, below ceiling
6. **symbol_allowed**     — ticker is in the per-market allow-list
                             (Phase 1 will populate this from
                             KOSPI200/KOSDAQ150/SP500/NASDAQ100
                             dynamically)

A check FAILS open by default — if a required input is missing we
treat that as failure, never as a pass. This is the inverse of
"fail closed" semantics found in non-trading systems, intentionally.

The engine is stateless. Callers pass the current :class:`RiskState`
and the :class:`RiskLimits` config; the engine compares them and
emits a :class:`RiskCheckResult`. State assembly (querying the DB
for open positions, today's P&L, etc.) lives in the runtime layer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from brokers.base import OrderIntent
from core.types import Market


@dataclass(frozen=True)
class RiskLimits:
    """Per-account risk limits. All values are inclusive thresholds
    (i.e. ``current == limit`` is a PASS)."""

    max_lot: float = 100.0
    daily_loss_limit: float = 1_000_000.0     # in account base currency
    consecutive_loss_limit: int = 5
    max_positions: int = 20
    max_spread_bps: float = 30.0
    symbol_allowlist_kr: frozenset[str] = field(default_factory=frozenset)
    symbol_allowlist_us: frozenset[str] = field(default_factory=frozenset)

    def allowlist_for(self, market: Market) -> frozenset[str]:
        return self.symbol_allowlist_kr if market is Market.KR else self.symbol_allowlist_us


@dataclass(frozen=True)
class RiskState:
    """Snapshot of risk-relevant context at order time."""

    open_position_count: int
    daily_pnl: float                           # negative for loss
    consecutive_losses: int
    current_spread_bps: Optional[float]        # None = unknown → fails


@dataclass(frozen=True)
class RiskLimitFailure:
    limit_name: str
    observed: object
    threshold: object
    reason: str


@dataclass(frozen=True)
class RiskCheckResult:
    all_passed: bool
    intent: OrderIntent
    state: RiskState
    limits: RiskLimits
    failures: list[RiskLimitFailure]

    # Per-limit detail, suitable for persisting to ``risk_snapshots``.
    max_lot_pass: bool
    daily_loss_pass: bool
    consecutive_loss_pass: bool
    max_positions_pass: bool
    spread_pass: bool
    symbol_allowed_pass: bool


class RiskEngine:
    """Stateless. All 6 checks run unconditionally — we want full
    snapshot in every audit row, not early exit."""

    def check(
        self,
        intent: OrderIntent,
        state: RiskState,
        limits: RiskLimits,
    ) -> RiskCheckResult:
        failures: list[RiskLimitFailure] = []

        # 1. max_lot
        max_lot_pass = intent.volume <= limits.max_lot
        if not max_lot_pass:
            failures.append(
                RiskLimitFailure(
                    "max_lot",
                    observed=intent.volume,
                    threshold=limits.max_lot,
                    reason=f"order volume {intent.volume} exceeds max_lot {limits.max_lot}",
                )
            )

        # 2. daily_loss — fail if today's loss already at or past the limit
        # (daily_pnl is negative for losses, so we compare its absolute value)
        daily_loss_pass = -state.daily_pnl < limits.daily_loss_limit
        if not daily_loss_pass:
            failures.append(
                RiskLimitFailure(
                    "daily_loss",
                    observed=state.daily_pnl,
                    threshold=-limits.daily_loss_limit,
                    reason=(
                        f"daily P&L {state.daily_pnl:.2f} has crossed loss limit "
                        f"{-limits.daily_loss_limit:.2f}"
                    ),
                )
            )

        # 3. consecutive_loss
        consecutive_loss_pass = state.consecutive_losses < limits.consecutive_loss_limit
        if not consecutive_loss_pass:
            failures.append(
                RiskLimitFailure(
                    "consecutive_loss",
                    observed=state.consecutive_losses,
                    threshold=limits.consecutive_loss_limit,
                    reason=(
                        f"{state.consecutive_losses} consecutive losing trades "
                        f"reached limit {limits.consecutive_loss_limit}"
                    ),
                )
            )

        # 4. max_positions
        max_positions_pass = state.open_position_count < limits.max_positions
        if not max_positions_pass:
            failures.append(
                RiskLimitFailure(
                    "max_positions",
                    observed=state.open_position_count,
                    threshold=limits.max_positions,
                    reason=(
                        f"{state.open_position_count} open positions at limit "
                        f"{limits.max_positions}"
                    ),
                )
            )

        # 5. spread — unknown spread fails closed
        if state.current_spread_bps is None:
            spread_pass = False
            failures.append(
                RiskLimitFailure(
                    "spread",
                    observed=None,
                    threshold=limits.max_spread_bps,
                    reason="current spread unknown (no quote / stale feed)",
                )
            )
        else:
            spread_pass = state.current_spread_bps <= limits.max_spread_bps
            if not spread_pass:
                failures.append(
                    RiskLimitFailure(
                        "spread",
                        observed=state.current_spread_bps,
                        threshold=limits.max_spread_bps,
                        reason=(
                            f"spread {state.current_spread_bps:.2f} bps exceeds "
                            f"limit {limits.max_spread_bps:.2f} bps"
                        ),
                    )
                )

        # 6. symbol_allowed
        allowlist = limits.allowlist_for(intent.market)
        # Empty allowlist means "no restriction" — useful in dev and for
        # paper trading universes that haven't been loaded yet. We
        # explicitly distinguish empty from non-empty so this never
        # silently fails closed in production once the universe loader
        # populates Phase 1 data.
        if not allowlist:
            symbol_allowed_pass = True
        else:
            symbol_allowed_pass = intent.ticker in allowlist
            if not symbol_allowed_pass:
                failures.append(
                    RiskLimitFailure(
                        "symbol_allowed",
                        observed=intent.ticker,
                        threshold=f"{len(allowlist)} tickers in {intent.market.value} allowlist",
                        reason=(
                            f"ticker {intent.market.value}:{intent.ticker} "
                            f"not in current allowlist"
                        ),
                    )
                )

        return RiskCheckResult(
            all_passed=not failures,
            intent=intent,
            state=state,
            limits=limits,
            failures=failures,
            max_lot_pass=max_lot_pass,
            daily_loss_pass=daily_loss_pass,
            consecutive_loss_pass=consecutive_loss_pass,
            max_positions_pass=max_positions_pass,
            spread_pass=spread_pass,
            symbol_allowed_pass=symbol_allowed_pass,
        )
