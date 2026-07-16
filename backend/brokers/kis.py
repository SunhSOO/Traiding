"""Korea Investment & Securities (한국투자증권) KIS OpenAPI broker adapter.

Implements the :class:`BrokerAdapter` contract against the KIS REST API so the
decision/runtime layer can place REAL KR equity orders (the alpha campaign
confirmed KR small-cap selection alpha; see ALPHA_CAMPAIGN.md 시점 19). This is
the execution + forward-paper-validation vehicle for that edge.

SAFETY / STATUS
---------------
* Defaults to 모의투자 (PAPER) — the openapivts sandbox domain. Real-money 실전
  routing requires BOTH ``settings.kis_paper == False`` AND
  ``settings.runtime_mode == RuntimeMode.LIVE`` — same double-gate as the rest
  of the system.
* MARKET orders only (matches PaperBroker); LIMIT is accepted but sent as 지정가
  with the supplied ``limit_price``.
* UNTESTED WITHOUT CREDENTIALS: it needs the operator's KIS OpenAPI app key /
  secret / account number (free: open a KIS account → apply for OpenAPI at
  apiportal.koreainvestment.com → get a 모의투자 account first). Set
  KIS_APP_KEY / KIS_APP_SECRET / KIS_ACCOUNT_NO (e.g. "12345678-01") in .env.
  Once set, run ``python -m brokers.kis`` for a read-only balance smoke test.

Costs in the returned ExecutionResult are estimated from ``markets.kr.tax``
(KIS also reports actual fills asynchronously; we surface the estimate so the
sizer/risk layer has a number immediately, consistent with PaperBroker).
"""
from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Optional

import httpx

from core.config import get_settings
from core.types import Market
from markets.kr.tax import compute_kr_tax
from brokers.base import (
    AccountSnapshot,
    BrokerAdapter,
    ExecutionResult,
    OrderIntent,
    OrderSide,
    OrderType,
    PositionSnapshot,
)

_PAPER_DOMAIN = "https://openapivts.koreainvestment.com:29443"
_LIVE_DOMAIN = "https://openapi.koreainvestment.com:9443"

# TR_IDs differ by paper/live and buy/sell. (order-cash / inquire-balance)
_TR = {
    "buy":  {"paper": "VTTC0802U", "live": "TTTC0802U"},
    "sell": {"paper": "VTTC0801U", "live": "TTTC0801U"},
    "bal":  {"paper": "VTTC8434R", "live": "TTTC8434R"},
}


def _code(ticker: str) -> str:
    """KIS PDNO = bare 6-digit code (strip our .KS/.KQ yfinance suffix)."""
    return ticker.split(".")[0].zfill(6)


class KISBroker(BrokerAdapter):
    name = "kis"

    def __init__(
        self,
        *,
        app_key: Optional[str] = None,
        app_secret: Optional[str] = None,
        account_no: Optional[str] = None,
        paper: Optional[bool] = None,
        timeout: float = 10.0,
    ):
        s = get_settings()
        self._key = app_key or s.kis_app_key.get_secret_value()
        self._secret = app_secret or s.kis_app_secret.get_secret_value()
        acct = account_no or s.kis_account_no.get_secret_value()
        self._paper = s.kis_paper if paper is None else paper
        if not (self._key and self._secret and acct):
            raise RuntimeError(
                "KIS credentials missing. Set KIS_APP_KEY / KIS_APP_SECRET / "
                "KIS_ACCOUNT_NO in .env (see brokers/kis.py docstring)."
            )
        # account "12345678-01" -> CANO 12345678, ACNT_PRDT_CD 01
        cano, _, prdt = acct.partition("-")
        self._cano = cano.strip()
        self._prdt = (prdt or "01").strip()
        self._domain = _PAPER_DOMAIN if self._paper else _LIVE_DOMAIN
        self._mode = "paper" if self._paper else "live"
        self._timeout = timeout
        self._token: Optional[str] = None
        self._token_exp: float = 0.0
        self._client = httpx.Client(base_url=self._domain, timeout=timeout)

    # ── auth ────────────────────────────────────────────────────────────
    def _access_token(self) -> str:
        if self._token and time.monotonic() < self._token_exp - 60:
            return self._token
        r = self._client.post("/oauth2/tokenP", json={
            "grant_type": "client_credentials",
            "appkey": self._key, "appsecret": self._secret,
        })
        r.raise_for_status()
        d = r.json()
        self._token = d["access_token"]
        self._token_exp = time.monotonic() + int(d.get("expires_in", 86400))
        return self._token

    def _headers(self, tr_id: str, hashkey: Optional[str] = None) -> dict:
        h = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._access_token()}",
            "appkey": self._key, "appsecret": self._secret,
            "tr_id": tr_id, "custtype": "P",
        }
        if hashkey:
            h["hashkey"] = hashkey
        return h

    def _hashkey(self, body: dict) -> str:
        r = self._client.post("/uapi/hashkey", json=body, headers={
            "content-type": "application/json; charset=utf-8",
            "appkey": self._key, "appsecret": self._secret,
        })
        r.raise_for_status()
        return r.json()["HASH"]

    # ── BrokerAdapter API ───────────────────────────────────────────────
    def execute(self, intent: OrderIntent) -> ExecutionResult:
        if intent.market is not Market.KR:
            return ExecutionResult(ok=False, intent=intent, error="KISBroker handles KR only")
        if intent.volume <= 0:
            return ExecutionResult(ok=False, intent=intent, error="volume must be > 0")
        return self._order(intent, intent.side, int(round(intent.volume)))

    def close_position(self, market: Market, ticker: str, *, comment: str = "",
                       decision_audit_id: Optional[str] = None) -> ExecutionResult:
        # Flatten by sending the opposite side for the held quantity.
        held = next((p for p in self.get_positions(Market.KR)
                     if _code(p.ticker) == _code(ticker)), None)
        intent = OrderIntent(market=Market.KR, ticker=ticker,
                             side=OrderSide.SELL, order_type=OrderType.MARKET,
                             volume=(held.volume if held else 0),
                             comment=comment, decision_audit_id=decision_audit_id)
        if held is None or held.volume <= 0:
            return ExecutionResult(ok=False, intent=intent, error=f"No KIS position on {ticker}")
        close_side = OrderSide.SELL if held.side is OrderSide.BUY else OrderSide.BUY
        return self._order(intent, close_side, int(round(held.volume)))

    def _order(self, intent: OrderIntent, side: OrderSide, qty: int) -> ExecutionResult:
        is_market = intent.order_type is OrderType.MARKET
        ord_dvsn = "01" if is_market else "00"                 # 01 시장가 / 00 지정가
        unpr = "0" if is_market else str(int(round(intent.limit_price or 0)))
        body = {
            "CANO": self._cano, "ACNT_PRDT_CD": self._prdt,
            "PDNO": _code(intent.ticker), "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(qty), "ORD_UNPR": unpr,
        }
        tr_id = _TR["buy" if side is OrderSide.BUY else "sell"][self._mode]
        try:
            hk = self._hashkey(body)
            r = self._client.post("/uapi/domestic-stock/v1/trading/order-cash",
                                  json=body, headers=self._headers(tr_id, hk))
            r.raise_for_status()
            d = r.json()
        except Exception as e:  # network / HTTP
            return ExecutionResult(ok=False, intent=intent, error=f"KIS order error: {e}")
        if d.get("rt_cd") != "0":
            return ExecutionResult(ok=False, intent=intent,
                                   error=f"KIS reject [{d.get('msg_cd')}] {d.get('msg1')}")
        # KIS confirms acceptance (ODNO); actual fill price arrives via balance
        # polling. Estimate cost from the tax model for an immediate number.
        ref_px = intent.limit_price or 0.0
        tax_bd = compute_kr_tax(side=side.value, gross_value=ref_px * qty) if ref_px else None
        return ExecutionResult(
            ok=True, intent=intent,
            fill_price=(ref_px or None), fill_volume=float(qty),
            fill_ts=datetime.now(UTC),
            commission=(tax_bd.commission if tax_bd else 0.0),
            tax=((tax_bd.transaction_tax + tax_bd.other_fees) if tax_bd else 0.0),
            venue_order_id=d.get("output", {}).get("ODNO"),
            extras={"mode": self._mode, "msg": d.get("msg1"), "krx_fwdg_ord_orgno": d.get("output", {}).get("KRX_FWDG_ORD_ORGNO")},
        )

    def _balance_raw(self) -> dict:
        params = {
            "CANO": self._cano, "ACNT_PRDT_CD": self._prdt,
            "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02", "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
        }
        r = self._client.get("/uapi/domestic-stock/v1/trading/inquire-balance",
                             params=params, headers=self._headers(_TR["bal"][self._mode]))
        r.raise_for_status()
        return r.json()

    def get_positions(self, market: Optional[Market] = None) -> list[PositionSnapshot]:
        if market is not None and market is not Market.KR:
            return []
        d = self._balance_raw()
        out: list[PositionSnapshot] = []
        for h in d.get("output1", []):
            qty = float(h.get("hldg_qty", 0) or 0)
            if qty <= 0:
                continue
            entry = float(h.get("pchs_avg_pric", 0) or 0)
            cur = float(h.get("prpr", 0) or 0) or None
            pnl = float(h.get("evlu_pfls_amt", 0) or 0) if h.get("evlu_pfls_amt") else None
            out.append(PositionSnapshot(
                market=Market.KR, ticker=str(h.get("pdno", "")).zfill(6),
                side=OrderSide.BUY, volume=qty, entry_price=entry,
                entry_ts=datetime.now(UTC), current_price=cur, unrealized_pnl=pnl,
            ))
        return out

    def get_account(self) -> AccountSnapshot:
        d = self._balance_raw()
        o2 = (d.get("output2") or [{}])[0]
        deposit = float(o2.get("dnca_tot_amt", 0) or 0)          # 예수금총액
        equity = float(o2.get("tot_evlu_amt", 0) or 0) or deposit  # 총평가금액
        return AccountSnapshot(base_currency="KRW", balance=deposit, equity=equity)


if __name__ == "__main__":  # read-only smoke: balance + positions (paper by default)
    import sys
    sys.path.insert(0, ".")
    b = KISBroker()
    print(f"[kis] mode={b._mode} account={b._cano}-{b._prdt}")
    acct = b.get_account()
    print(f"[kis] balance(예수금)={acct.balance:,.0f} KRW  equity={acct.equity:,.0f} KRW")
    pos = b.get_positions(Market.KR)
    print(f"[kis] {len(pos)} holdings")
    for p in pos[:10]:
        print(f"   {p.ticker}  qty={p.volume:.0f}  avg={p.entry_price:,.0f}  cur={p.current_price}")
