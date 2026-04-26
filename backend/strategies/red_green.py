"""Red-Green Signals + SL/TP strategy engine.

The implementation mirrors the Pine Script state order documented in
docs/product-specs/red-green-auto-trading.md.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from math import sqrt
from typing import Optional


@dataclass(frozen=True)
class Candle:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0


@dataclass
class RedGreenConfig:
    symbol: str = "XAUUSD"
    timeframe: str = "M15"
    volume: float = 0.01
    magic: int = 234901
    dry_run: bool = True
    live_enabled: bool = False
    k: int = 3
    d: int = 3
    d1_input: int = 10
    mult: float = 2.0
    short_period: int = 7
    mid_period: int = 21
    long_period: int = 50
    atr_periods: int = 10
    atr_multiplier: float = 3.0
    tp1_mult: float = 1.5
    max_spread_points: int = 80


@dataclass
class PositionState:
    pos_state: int = 0
    pos_entry: Optional[float] = None
    pos_sl: Optional[float] = None
    pos_tp1: Optional[float] = None
    tp1_hit: bool = False
    trail_active: bool = False
    show_trail: bool = False
    ticket: Optional[int] = None


@dataclass(frozen=True)
class IndicatorPoint:
    time: datetime
    close: float
    upperline1: float
    upperline2: float
    lowerline1: float
    lowerline2: float
    kizun: float
    up: float
    dn: float
    trend: int


@dataclass
class StrategyEvent:
    time: datetime
    level: str
    event: str
    message: str
    data: dict


@dataclass
class OrderIntent:
    action: str
    symbol: str
    side: Optional[str] = None
    volume: Optional[float] = None
    sl: Optional[float] = None
    tp: Optional[float] = None
    ticket: Optional[int] = None
    comment: str = "SUPERRICH Red-Green"


@dataclass
class StrategyDecision:
    indicator: Optional[IndicatorPoint]
    state: PositionState
    events: list[StrategyEvent]
    intents: list[OrderIntent]
    conditions: dict


def _sma(values: list[float], period: int) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(values)
    if period <= 0:
        return out
    rolling = 0.0
    for i, value in enumerate(values):
        rolling += value
        if i >= period:
            rolling -= values[i - period]
        if i >= period - 1:
            out[i] = rolling / period
    return out


def _stdev(values: list[float], period: int) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(values)
    if period <= 0:
        return out
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        mean = sum(window) / period
        out[i] = sqrt(sum((v - mean) ** 2 for v in window) / period)
    return out


def _highest(values: list[float], period: int) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(values)
    for i in range(period - 1, len(values)):
        out[i] = max(values[i - period + 1 : i + 1])
    return out


def _lowest(values: list[float], period: int) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(values)
    for i in range(period - 1, len(values)):
        out[i] = min(values[i - period + 1 : i + 1])
    return out


def _atr(candles: list[Candle], period: int) -> list[Optional[float]]:
    trs: list[float] = []
    for i, candle in enumerate(candles):
        if i == 0:
            trs.append(candle.high - candle.low)
            continue
        prev_close = candles[i - 1].close
        trs.append(max(candle.high - candle.low, abs(candle.high - prev_close), abs(candle.low - prev_close)))

    out: list[Optional[float]] = [None] * len(candles)
    if len(trs) < period:
        return out
    out[period - 1] = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        out[i] = ((out[i - 1] or trs[i]) * (period - 1) + trs[i]) / period
    return out


class RedGreenStrategy:
    """Pure strategy calculation and state transition engine."""

    def __init__(self, config: Optional[RedGreenConfig] = None, state: Optional[PositionState] = None):
        self.config = config or RedGreenConfig()
        self.state = state or PositionState()
        self._last_processed_time: Optional[datetime] = None

    @property
    def last_processed_time(self) -> Optional[datetime]:
        return self._last_processed_time

    def set_config(self, config: RedGreenConfig) -> None:
        self.config = config

    def reset(self) -> None:
        self.state = PositionState()
        self._last_processed_time = None

    def calculate(self, candles: list[Candle]) -> list[Optional[IndicatorPoint]]:
        cfg = self.config
        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]

        basis = _sma(closes, cfg.mid_period)
        dev = _stdev(closes, cfg.mid_period)
        high_short = _highest(highs, cfg.short_period)
        low_short = _lowest(lows, cfg.short_period)
        high_mid = _highest(highs, cfg.mid_period)
        low_mid = _lowest(lows, cfg.mid_period)
        high_long = _highest(highs, cfg.long_period)
        low_long = _lowest(lows, cfg.long_period)
        atr = _atr(candles, cfg.atr_periods)

        points: list[Optional[IndicatorPoint]] = [None] * len(candles)
        trend = 1
        prev_up: Optional[float] = None
        prev_dn: Optional[float] = None

        for i, candle in enumerate(candles):
            if basis[i] is None or dev[i] is None or high_long[i] is None or atr[i] is None:
                continue

            upper = (basis[i] or 0) + cfg.mult * (dev[i] or 0)
            lower = (basis[i] or 0) - cfg.mult * (dev[i] or 0)
            short_mid_avg = ((high_short[i] or 0) + (low_short[i] or 0) + (high_mid[i] or 0) + (low_mid[i] or 0)) / 4
            long_avg = ((high_long[i] or 0) + (low_long[i] or 0)) / 2
            upperline1 = (short_mid_avg + upper) / 2
            upperline2 = (long_avg + upper) / 2
            lowerline1 = (short_mid_avg + lower) / 2
            lowerline2 = (long_avg + lower) / 2
            kizun = ((high_short[i] or 0) + (low_short[i] or 0) + (high_mid[i] or 0) + (low_mid[i] or 0) + (high_long[i] or 0) + (low_long[i] or 0)) / 6

            hl2 = (candle.high + candle.low) / 2
            raw_up = hl2 - cfg.atr_multiplier * (atr[i] or 0)
            raw_dn = hl2 + cfg.atr_multiplier * (atr[i] or 0)
            up1 = prev_up if prev_up is not None else raw_up
            dn1 = prev_dn if prev_dn is not None else raw_dn
            prev_close = candles[i - 1].close if i > 0 else candle.close
            up = max(raw_up, up1) if prev_close > up1 else raw_up
            dn = min(raw_dn, dn1) if prev_close < dn1 else raw_dn

            if trend == -1 and candle.close > dn1:
                trend = 1
            elif trend == 1 and candle.close < up1:
                trend = -1

            prev_up = up
            prev_dn = dn
            points[i] = IndicatorPoint(
                time=candle.time,
                close=candle.close,
                upperline1=upperline1,
                upperline2=upperline2,
                lowerline1=lowerline1,
                lowerline2=lowerline2,
                kizun=kizun,
                up=up,
                dn=dn,
                trend=trend,
            )
        return points

    def on_candles(self, candles: list[Candle]) -> StrategyDecision:
        if len(candles) < max(self.config.long_period, self.config.mid_period, self.config.atr_periods) + 2:
            return StrategyDecision(None, self.state, [], [], {"ready": False, "reason": "lookback 부족"})

        points = self.calculate(candles)
        indicator = points[-1]
        prev = points[-2]
        if indicator is None or prev is None:
            return StrategyDecision(None, self.state, [], [], {"ready": False, "reason": "지표 계산 준비 중"})
        if self._last_processed_time == indicator.time:
            return StrategyDecision(indicator, self.state, [], [], {"ready": True, "duplicate_bar": True})

        decision = self._process_bar(candles[-1], indicator, prev)
        self._last_processed_time = indicator.time
        return decision

    def _process_bar(self, candle: Candle, point: IndicatorPoint, prev: IndicatorPoint) -> StrategyDecision:
        state = self.state
        events: list[StrategyEvent] = []
        intents: list[OrderIntent] = []

        cloud_green = point.upperline1 >= point.upperline2
        cloud_red = point.upperline1 < point.upperline2
        long_match = point.trend == 1 and cloud_green
        short_match = point.trend == -1 and cloud_red
        prev_long_match = prev.trend == 1 and prev.upperline1 >= prev.upperline2
        prev_short_match = prev.trend == -1 and prev.upperline1 < prev.upperline2
        fresh_long = long_match and not prev_long_match
        fresh_short = short_match and not prev_short_match
        go_long = fresh_long and state.pos_state != 1
        go_short = fresh_short and state.pos_state != -1

        def add_event(level: str, event: str, message: str, data: Optional[dict] = None) -> None:
            events.append(StrategyEvent(candle.time, level, event, message, data or {}))

        if state.pos_state == 1 and state.tp1_hit and state.pos_entry is not None:
            if point.up > state.pos_entry:
                state.trail_active = True
            if state.trail_active and state.pos_sl is not None and point.up > state.pos_sl:
                state.pos_sl = point.up
                intents.append(OrderIntent("modify_sl", self.config.symbol, sl=state.pos_sl, ticket=state.ticket))
                add_event("info", "trail_update", "롱 트레일링 SL 상향", {"sl": state.pos_sl})
            state.show_trail = True

        if state.pos_state == -1 and state.tp1_hit and state.pos_entry is not None:
            if point.dn < state.pos_entry:
                state.trail_active = True
            if state.trail_active and state.pos_sl is not None and point.dn < state.pos_sl:
                state.pos_sl = point.dn
                intents.append(OrderIntent("modify_sl", self.config.symbol, sl=state.pos_sl, ticket=state.ticket))
                add_event("info", "trail_update", "숏 트레일링 SL 하향", {"sl": state.pos_sl})
            state.show_trail = True

        sl_hit_long = state.pos_state == 1 and state.pos_sl is not None and candle.low <= state.pos_sl and not state.tp1_hit
        sl_hit_short = state.pos_state == -1 and state.pos_sl is not None and candle.high >= state.pos_sl and not state.tp1_hit
        tp1_hit_long = state.pos_state == 1 and state.pos_tp1 is not None and not state.tp1_hit and candle.high >= state.pos_tp1
        tp1_hit_short = state.pos_state == -1 and state.pos_tp1 is not None and not state.tp1_hit and candle.low <= state.pos_tp1
        trail_hit_long = state.pos_state == 1 and state.tp1_hit and state.pos_sl is not None and candle.low <= state.pos_sl
        trail_hit_short = state.pos_state == -1 and state.tp1_hit and state.pos_sl is not None and candle.high >= state.pos_sl

        if tp1_hit_long or tp1_hit_short:
            state.tp1_hit = True
            state.pos_sl = state.pos_entry
            state.trail_active = False
            state.show_trail = True
            intents.append(OrderIntent("modify_sl", self.config.symbol, sl=state.pos_sl, ticket=state.ticket))
            add_event("success", "tp1_hit", "TP1 도달, SL 본절 이동", {"sl": state.pos_sl})

        if sl_hit_long or sl_hit_short or trail_hit_long or trail_hit_short:
            reason = "trail_exit" if trail_hit_long or trail_hit_short else "sl_hit"
            intents.append(OrderIntent("close", self.config.symbol, ticket=state.ticket, comment=f"SUPERRICH {reason}"))
            add_event("warn", reason, "청산 조건 도달", {"state": asdict(state)})
            self.state = PositionState()
            state = self.state

        close_long = go_short and state.pos_state == 1
        close_short = go_long and state.pos_state == -1
        if close_long or close_short:
            intents.append(OrderIntent("close", self.config.symbol, ticket=state.ticket, comment="SUPERRICH opposite signal"))
            add_event("warn", "opposite_close", "반대 신호로 기존 포지션 청산", {"state": asdict(state)})
            self.state = PositionState()
            state = self.state

        if go_long and state.pos_state != 1:
            if point.kizun < candle.close:
                risk = candle.close - point.kizun
                state.pos_state = 1
                state.pos_entry = candle.close
                state.pos_sl = point.kizun
                state.pos_tp1 = candle.close + risk * self.config.tp1_mult
                state.tp1_hit = False
                state.trail_active = False
                state.show_trail = False
                intents.append(OrderIntent("open", self.config.symbol, "BUY", self.config.volume, state.pos_sl, state.pos_tp1))
                add_event("success", "long_entry", "롱 진입 신호", {"entry": state.pos_entry, "sl": state.pos_sl, "tp1": state.pos_tp1})
            else:
                add_event("info", "long_rejected", "롱 신호 무효: kizun이 종가보다 낮지 않음", {"kizun": point.kizun, "close": candle.close})

        if go_short and state.pos_state != -1:
            if point.kizun > candle.close:
                risk = point.kizun - candle.close
                state.pos_state = -1
                state.pos_entry = candle.close
                state.pos_sl = point.kizun
                state.pos_tp1 = candle.close - risk * self.config.tp1_mult
                state.tp1_hit = False
                state.trail_active = False
                state.show_trail = False
                intents.append(OrderIntent("open", self.config.symbol, "SELL", self.config.volume, state.pos_sl, state.pos_tp1))
                add_event("success", "short_entry", "숏 진입 신호", {"entry": state.pos_entry, "sl": state.pos_sl, "tp1": state.pos_tp1})
            else:
                add_event("info", "short_rejected", "숏 신호 무효: kizun이 종가보다 높지 않음", {"kizun": point.kizun, "close": candle.close})

        conditions = {
            "ready": True,
            "cloud_green": cloud_green,
            "cloud_red": cloud_red,
            "trend": point.trend,
            "fresh_long": fresh_long,
            "fresh_short": fresh_short,
            "go_long": go_long,
            "go_short": go_short,
        }
        return StrategyDecision(point, self.state, events, intents, conditions)
