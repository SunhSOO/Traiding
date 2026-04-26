"""In-memory Red-Green strategy runtime manager."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any, Optional

from strategies.red_green import Candle, OrderIntent, RedGreenConfig, RedGreenStrategy, StrategyEvent

logger = logging.getLogger(__name__)


class StrategyManager:
    def __init__(self, mt5_bridge: Any = None):
        self.mt5_bridge = mt5_bridge
        self.strategy = RedGreenStrategy()
        self.running = False
        self.mode = "dry_run"
        self.events: list[StrategyEvent] = []
        self.last_error: Optional[str] = None
        self._task: Optional[asyncio.Task] = None

    def attach_mt5(self, mt5_bridge: Any) -> None:
        self.mt5_bridge = mt5_bridge

    def get_status(self) -> dict:
        cfg = self.strategy.config
        state = self.strategy.state
        return {
            "id": "red-green-main",
            "name": "Red-Green Signals + SL/TP v9",
            "status": "running" if self.running else "stopped",
            "mode": self.mode,
            "config": asdict(cfg),
            "state": asdict(state),
            "last_processed_time": self.strategy.last_processed_time.isoformat() if self.strategy.last_processed_time else None,
            "last_error": self.last_error,
            "events": [self._event_to_dict(e) for e in self.events[-80:]][::-1],
        }

    def update_config(self, data: dict) -> dict:
        current = asdict(self.strategy.config)
        allowed = set(current)
        unknown = sorted(set(data) - allowed)
        if unknown:
            return {"success": False, "error": f"Unknown config keys: {', '.join(unknown)}"}
        current.update(data)
        cfg = RedGreenConfig(**current)
        if cfg.live_enabled and cfg.dry_run:
            return {"success": False, "error": "live_enabled와 dry_run은 동시에 켤 수 없습니다."}
        self.strategy.set_config(cfg)
        self.mode = "live" if cfg.live_enabled else "dry_run" if cfg.dry_run else "demo"
        self._record("info", "config_updated", "전략 설정 갱신", asdict(cfg))
        return {"success": True, "status": self.get_status()}

    async def start(self) -> dict:
        if self.running:
            return {"success": True, "status": self.get_status()}
        self.running = True
        self._task = asyncio.create_task(self._loop())
        self._record("info", "started", "Red-Green 전략 런타임 시작", {})
        return {"success": True, "status": self.get_status()}

    async def stop(self) -> dict:
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._record("info", "stopped", "Red-Green 전략 런타임 중지", {})
        return {"success": True, "status": self.get_status()}

    async def run_once(self) -> dict:
        try:
            candles = self._load_candles()
            if not candles:
                self._record("warn", "no_candles", "캔들 데이터를 가져오지 못했습니다.", {})
                return {"success": False, "error": "캔들 데이터를 가져오지 못했습니다.", "status": self.get_status()}
            decision = self.strategy.on_candles(candles)
            for event in decision.events:
                self.events.append(event)
            execution_results = []
            for intent in decision.intents:
                execution_results.append(self._execute_intent(intent))
            return {
                "success": True,
                "decision": {
                    "indicator": asdict(decision.indicator) if decision.indicator else None,
                    "conditions": decision.conditions,
                    "intents": [asdict(i) for i in decision.intents],
                    "executions": execution_results,
                },
                "status": self.get_status(),
            }
        except Exception as exc:
            self.last_error = str(exc)
            logger.exception("Strategy run failed")
            self._record("error", "run_failed", "전략 실행 실패", {"error": str(exc)})
            return {"success": False, "error": str(exc), "status": self.get_status()}

    async def _loop(self) -> None:
        while self.running:
            await self.run_once()
            await asyncio.sleep(5)

    def _load_candles(self) -> list[Candle]:
        cfg = self.strategy.config
        if not self.mt5_bridge or not getattr(self.mt5_bridge, "connected", False):
            if cfg.dry_run:
                return self._demo_candles()
            return []
        rates = self.mt5_bridge.get_rates(cfg.symbol, cfg.timeframe, 220)
        candles = []
        for row in rates:
            raw_time = row.get("time")
            time_value = datetime.fromtimestamp(raw_time) if isinstance(raw_time, (int, float)) else raw_time
            candles.append(Candle(time_value, row["open"], row["high"], row["low"], row["close"], row.get("tick_volume", 0)))
        return candles

    @staticmethod
    def _demo_candles() -> list[Candle]:
        candles: list[Candle] = []
        base = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=15 * 220)
        price = 2300.0
        for i in range(220):
            wave = ((i % 24) - 12) * 0.08
            drift = 0.12 if i < 120 else -0.05
            price += drift + wave
            high = price + 1.8 + (i % 5) * 0.08
            low = price - 1.7 - (i % 7) * 0.06
            candles.append(Candle(base + timedelta(minutes=15 * i), price - 0.3, high, low, price, 100 + i))
        return candles

    def _execute_intent(self, intent: OrderIntent) -> dict:
        cfg = self.strategy.config
        if cfg.dry_run or not cfg.live_enabled:
            self._record("info", "dry_run_intent", "dry_run 주문 의도 기록", asdict(intent))
            return {"success": True, "dry_run": True, "intent": asdict(intent)}
        if not self.mt5_bridge or not getattr(self.mt5_bridge, "connected", False):
            self._record("error", "mt5_not_connected", "MT5 미연결로 주문 차단", asdict(intent))
            return {"success": False, "error": "MT5 not connected", "intent": asdict(intent)}

        if intent.action == "open" and intent.side:
            result = self.mt5_bridge.place_order(
                symbol=intent.symbol,
                order_type=intent.side,
                volume=intent.volume or cfg.volume,
                sl=intent.sl or 0,
                tp=intent.tp or 0,
                comment=intent.comment,
                magic=cfg.magic,
            )
            if result.get("success") and result.get("order"):
                self.strategy.state.ticket = result["order"]
            self._record("success" if result.get("success") else "error", "order_open", "MT5 진입 주문 결과", result)
            return result

        if intent.action == "close":
            ticket = intent.ticket or self.strategy.state.ticket
            result = self.mt5_bridge.close_position(ticket) if ticket else {"success": False, "error": "ticket 없음"}
            self._record("success" if result.get("success") else "error", "order_close", "MT5 청산 주문 결과", result)
            return result

        if intent.action == "modify_sl":
            ticket = intent.ticket or self.strategy.state.ticket
            result = self.mt5_bridge.modify_position_sl(ticket, intent.sl) if ticket and intent.sl else {"success": False, "error": "ticket 또는 sl 없음"}
            self._record("success" if result.get("success") else "error", "sl_modify", "MT5 SL 수정 결과", result)
            return result

        return {"success": False, "error": f"Unknown intent action: {intent.action}"}

    def _record(self, level: str, event: str, message: str, data: dict) -> None:
        self.events.append(StrategyEvent(datetime.now(UTC), level, event, message, data))
        self.events = self.events[-200:]

    @staticmethod
    def _event_to_dict(event: StrategyEvent) -> dict:
        return {
            "time": event.time.isoformat(),
            "level": event.level,
            "event": event.event,
            "message": event.message,
            "data": event.data,
        }
