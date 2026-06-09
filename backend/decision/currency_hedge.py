"""Currency Hedging — Wave 1.

목표: KR 거주자가 US 주식을 보유할 때 USDKRW 변동에 의한 수익 노이즈를
줄이고, 위험 조정 후 수익률을 개선한다.

선택된 구현 (Best, 현재 활성화):
─────────────────────────────────────────────────────────
**Volatility-Weighted Dynamic Hedge Ratio (VWDH)**

USD 익스포저 H_t에 대해 다음 헤지 비율을 결정한다.

    hedge_ratio_t = clip(
        beta_xy_t * (sigma_FX_t / sigma_eq_t)
        * confidence_t,
        0.0, 1.0
    )

  - beta_xy_t : USD 자산 일일 수익 vs USDKRW 일일 변화의 21d 회귀 베타
  - sigma_FX_t : USDKRW 21d 실현변동성 (EWMA, λ=0.94)
  - sigma_eq_t : US 자산 포트폴리오 21d 실현변동성
  - confidence_t : 환율 추세 강도 (|ema20 - ema60| / atr)에 따라 0.5~1.0 스케일

논리:
  - FX 변동성이 자산 변동성보다 크면 적극적 헤지 (비율 ↑)
  - 베타가 음의 상관일 때 (US가 오를 때 원화도 함께 강세) 자연헤지 효과
    있어 헤지 비율 감소
  - 추세가 약하면 (range-bound) confidence 낮춰 비용 절감

실행:
  - 헤지 도구: USDKRW forward (1M / 3M roll), KOSPI200 USD short ETF,
    또는 currency-hedged ETF (HEDJ-like) 대체.
  - 우리는 보유한 KIS API 환경에선 KRW 매도 + USD 매수 currency swap을
    직접 실행 어려우므로, **HEDJ 류 ETF로 부분 노출 대체**로 구현하는
    paper 시뮬레이션 우선.

대안 리스트 (User W.6 — "다른 가능성을 리스트업해서 기억해놔"):
─────────────────────────────────────────────────────────
A. **Static Hedge (50%)**
   가장 단순. 모든 USD 포지션의 절반을 항상 헤지.
   문헌: Perold-Schulman 1988, "Free Lunch in Currency Hedging".
   장: 구현 zero-cost. 단: 동적 정보 무시.

B. **Mean-Variance Optimal Hedge Ratio**
   h* = (μ_FX - r_KR) / σ_FX^2 - cov(R_eq, R_FX)/σ_FX^2
   장: 이론적 최적. 단: μ_FX 추정 어려움 + 모수 안정성 낮음.

C. **Minimum-Variance Hedge Ratio (MVHR)**
   h* = ρ * (σ_eq / σ_FX)
   장: VWDH의 special case (confidence=1). 단순.

D. **Regime-Conditional Hedge**
   HMM regime ∈ {risk_off, neutral, risk_on}에 따라 사전 룩업
   {1.0, 0.5, 0.2} 적용. 위기 시 100% 헤지, 호황 시 약하게.
   장: regime ↔ 환위험 상관 강한 시기에 효과적.

E. **Carry-Adjusted Hedge**
   금리차 (FedFunds - BOK base rate) > 0이면 헤지 비용 발생
   (forward roll-down). 비용 < 변동성 benefit일 때만 헤지.
   장: 무거운 헤지의 carry drag 제거.

F. **Options-Based Asymmetric Hedge**
   USDKRW put 매입으로 downside만 보호. premium 비용 발생.
   장: upside는 유지. 단: 비용 + 유동성 부족 (KR 개인 옵션 한정).

G. **Currency Overlay via Forwards (Roll-Yield Capture)**
   1M USDKRW forward 매도 + 만기 시 roll. carry positive (KRW 금리 < USD)
   인 경우 추가 수익. 장: 자연스러운 carry 캡처. 단: forward 시장 접근
   한국 개인은 제한적.

H. **Currency-Hedged ETF 대체 (가장 현실적 대안)**
   미국 상장 currency-hedged Korea / international ETFs 활용
   (HEDJ는 European, KORU는 KOSPI 3x 무헤지). KR 상장 헤지형 미국 ETF
   (예: 'TIGER 미국S&P500선물(H)') 보유로 자동 헤지.
   장: 구현 가장 쉬움. 단: 종목 선택권 제한.

I. **Pair Trade: USDKRW vs Sector Beta**
   US 자산이 export-heavy KR 기업과 음의 상관일 때 KR export-heavy
   short으로 환위험 일부 상쇄. 장: 외환 도구 없이도 가능.
   단: 상관 불안정.

J. **Multi-Currency Basket Hedge**
   USDKRW 단일 헤지 대신 DXY 가중 바스켓 (EUR, JPY, GBP) 헤지로
   diversification. 우리는 US-only이므로 효과 미미.

K. **Volatility-Targeted Dynamic Hedge**
   포트폴리오 전체 변동성 목표 (예: 15%/yr) 고정. 환변동성 분해 후
   헤지 비율 자동 결정. 변동성 타게팅과 결합.

이 모듈은 paper 시뮬레이션 단계에서 모드 A (Static 50%)와 모드 VWDH
중 선택 가능하게 빌드. 실제 forward/option 시장 접근 가능해지면
G, F로 확장.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as DateType
from typing import Literal, Optional

import numpy as np
import pandas as pd


HedgeMode = Literal[
    "vwdh",          # Volatility-Weighted Dynamic Hedge (default best)
    "static_50",     # Static 50% (alternative A)
    "mvhr",          # Minimum-Variance (alternative C)
    "regime",        # Regime-Conditional (alternative D, needs hmm output)
    "carry",         # Carry-Adjusted (alternative E)
    "none",          # No hedge (baseline)
]


@dataclass
class HedgeConfig:
    base_currency: str = "KRW"
    foreign_currency: str = "USD"
    mode: HedgeMode = "vwdh"
    window_days: int = 21
    ewma_lambda: float = 0.94
    max_ratio: float = 1.0
    min_ratio: float = 0.0
    cost_bps_per_unit: float = 2.0    # 2bp roll cost per unit hedge
    confidence_floor: float = 0.5


@dataclass
class HedgeDecision:
    as_of: DateType
    mode: HedgeMode
    hedge_ratio: float
    beta_xy: Optional[float] = None
    sigma_fx: Optional[float] = None
    sigma_eq: Optional[float] = None
    confidence: Optional[float] = None
    reason: str = ""


def _ewma_vol(returns: pd.Series, lam: float = 0.94) -> float:
    """EWMA realized vol (annualized assumed)."""
    if len(returns) < 5:
        return float("nan")
    weights = np.array([lam ** (len(returns) - 1 - i) for i in range(len(returns))])
    weights /= weights.sum()
    mean = np.sum(weights * returns.values)
    var = np.sum(weights * (returns.values - mean) ** 2)
    return float(np.sqrt(var) * np.sqrt(252))


def compute_hedge_ratio(
    us_portfolio_returns: pd.Series,   # daily returns of USD-denominated equity book
    usdkrw_returns: pd.Series,         # daily ΔUSDKRW
    config: HedgeConfig,
    as_of: DateType,
    regime: Optional[str] = None,
    carry_yield_diff: Optional[float] = None,
) -> HedgeDecision:
    """Compute optimal hedge ratio for given config and history."""
    window = config.window_days

    if config.mode == "none":
        return HedgeDecision(as_of=as_of, mode="none",
                              hedge_ratio=0.0, reason="hedge disabled")

    if config.mode == "static_50":
        return HedgeDecision(as_of=as_of, mode="static_50",
                              hedge_ratio=0.5, reason="constant 50% per Perold-Schulman")

    # Align series
    aligned = pd.concat([us_portfolio_returns, usdkrw_returns], axis=1,
                        keys=["eq", "fx"]).dropna().tail(max(window, 60))
    if len(aligned) < window:
        return HedgeDecision(as_of=as_of, mode=config.mode,
                              hedge_ratio=0.5,
                              reason=f"insufficient history ({len(aligned)}), fallback 50%")

    sigma_fx = _ewma_vol(aligned["fx"], config.ewma_lambda)
    sigma_eq = _ewma_vol(aligned["eq"], config.ewma_lambda)
    cov_xy = aligned[["eq", "fx"]].cov().iloc[0, 1]
    var_fx = aligned["fx"].var()
    beta_xy = float(cov_xy / var_fx) if var_fx > 0 else 0.0

    if config.mode == "mvhr":
        rho = aligned["eq"].corr(aligned["fx"])
        if not np.isfinite(rho) or sigma_fx == 0:
            ratio = 0.5
        else:
            ratio = float(rho * sigma_eq / sigma_fx)
        ratio = max(config.min_ratio, min(config.max_ratio, ratio))
        return HedgeDecision(as_of=as_of, mode="mvhr",
                              hedge_ratio=ratio, beta_xy=beta_xy,
                              sigma_fx=sigma_fx, sigma_eq=sigma_eq,
                              reason=f"MVHR rho={rho:+.3f}")

    if config.mode == "regime":
        if regime in ("crisis", "risk_off"):
            ratio = 1.0
        elif regime in ("calm_bull", "risk_on"):
            ratio = 0.2
        else:
            ratio = 0.5
        return HedgeDecision(as_of=as_of, mode="regime",
                              hedge_ratio=ratio, beta_xy=beta_xy,
                              sigma_fx=sigma_fx, sigma_eq=sigma_eq,
                              reason=f"regime={regime}")

    if config.mode == "carry":
        # Hedge cost ≈ carry_yield_diff * hedge_horizon (bps/year)
        # Apply only if vol benefit > carry cost
        if carry_yield_diff is None:
            carry_yield_diff = 0.0
        vol_benefit_bps = sigma_fx * 10000
        carry_cost_bps = max(0.0, carry_yield_diff) * 10000
        if vol_benefit_bps > carry_cost_bps * 2:
            ratio = 0.8
        elif vol_benefit_bps > carry_cost_bps:
            ratio = 0.5
        else:
            ratio = 0.1
        return HedgeDecision(as_of=as_of, mode="carry",
                              hedge_ratio=ratio, sigma_fx=sigma_fx,
                              reason=f"vol_bps={vol_benefit_bps:.0f} vs carry_bps={carry_cost_bps:.0f}")

    # Default: VWDH
    if sigma_eq <= 0 or sigma_fx <= 0:
        return HedgeDecision(as_of=as_of, mode="vwdh",
                              hedge_ratio=0.5,
                              reason="degenerate vol, fallback 50%")

    # Confidence: trend strength of USDKRW
    fx_levels = (1 + aligned["fx"]).cumprod()
    ema20 = fx_levels.ewm(span=20, adjust=False).mean().iloc[-1]
    ema60 = fx_levels.ewm(span=60, adjust=False).mean().iloc[-1]
    atr_proxy = aligned["fx"].abs().rolling(14).mean().iloc[-1]
    if atr_proxy and atr_proxy > 0:
        trend = abs((ema20 - ema60) / (ema60 * atr_proxy))
        confidence = float(min(1.0, max(config.confidence_floor, 0.5 + trend * 5)))
    else:
        confidence = config.confidence_floor

    raw = beta_xy * (sigma_fx / sigma_eq) * confidence
    ratio = max(config.min_ratio, min(config.max_ratio, raw))

    return HedgeDecision(
        as_of=as_of, mode="vwdh",
        hedge_ratio=ratio, beta_xy=beta_xy,
        sigma_fx=sigma_fx, sigma_eq=sigma_eq,
        confidence=confidence,
        reason=f"beta={beta_xy:+.2f} sigma_fx/eq={sigma_fx/sigma_eq:.2f} conf={confidence:.2f}",
    )


def hedge_pnl_overlay(
    portfolio_value_krw: float,
    usd_exposure_value_usd: float,
    usdkrw_return: float,        # daily Δ as fraction
    hedge_ratio: float,
    cost_bps: float = 0.0,
) -> dict:
    """Compute the KRW-denominated P&L impact of the hedge for one period."""
    notional_hedged_usd = usd_exposure_value_usd * hedge_ratio
    # Hedge pays opposite of FX move (short USDKRW for KR investor)
    hedge_pnl_krw = -notional_hedged_usd * usdkrw_return
    hedge_cost_krw = notional_hedged_usd * (cost_bps / 10000.0) * abs(usdkrw_return)
    net_pnl_krw = hedge_pnl_krw - hedge_cost_krw
    return {
        "hedge_pnl_krw": hedge_pnl_krw,
        "hedge_cost_krw": hedge_cost_krw,
        "net_pnl_krw": net_pnl_krw,
        "notional_hedged_usd": notional_hedged_usd,
    }
