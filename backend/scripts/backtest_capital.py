"""2-month no-look-ahead capital backtest.

"Exclude the most recent 2 months, retrain, then auto-trade across those 2
months as if live (knowing nothing of the future), starting from a fixed
cash balance."

Protocol (walk-forward, long-only):
  * test window = last ~42 trading days of the data (our prices end ~2026-06).
  * rebalance every `step` trading days. At each rebalance date R:
      - retrain the rank model on rows whose 21d label is fully realised by
        R (date <= R - 21 trading days; nothing at/after R is seen),
      - buy the top-decile by predicted rank, EQUAL WEIGHT, all-in,
      - hold to the next rebalance, realise P&L from actual closes,
      - charge a round-trip cost on turnover.
  * track the cash balance (KR won / US dollar).
  * benchmark = buy-and-hold equal-weight of the whole universe.

Caveat: ONE 2-month window = a single sample; long-only; the walk-forward
IC is the robust edge. This is an illustrative live-style run.

Usage:
    uv run python scripts/backtest_capital.py --market US --start-cash 1000 --step 10
    uv run python scripts/backtest_capital.py --market KR --start-cash 1000000 --step 10
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import lightgbm as lgb
from sqlalchemy import text

from core.db import session_scope
from scripts.train_lgbm import ALL_FEATURE_COLS

RANK = "rank_fwd_21d"


def close_panel(market: str, tickers, d0, d1) -> pd.DataFrame:
    with session_scope() as s:
        rows = s.execute(text(
            "SELECT trade_date, ticker, close FROM daily_prices "
            "WHERE market=:m AND trade_date BETWEEN :a AND :b"
        ), {"m": market, "a": d0, "b": d1}).all()
    p = pd.DataFrame(rows, columns=["date", "ticker", "close"])
    p["date"] = pd.to_datetime(p["date"])
    return p.pivot_table(index="date", columns="ticker", values="close", aggfunc="first")


def _build_period(market: str, start: str, end: str) -> pd.DataFrame:
    """Build the feature matrix for an arbitrary past window [start, end]
    (ISO). Caches both the raw build and the labelled+cross-section matrix
    so re-runs skip the slow build. Lets us backtest historical bear markets
    (2020 COVID, 2022) using the 10y price/fundamental data — news/regime
    features are NaN/0 back then but they're not in the model's top-50."""
    from datetime import date, timedelta
    cache = Path(f"var/_bt_period_{market}_{start}_{end}.parquet")
    if cache.exists():
        print(f"[bt] loading period cache {cache}", flush=True)
        return pd.read_parquet(cache)
    from core.db import session_scope as _ss
    from training.features import build_feature_matrix
    from training.features_cross_section import apply_cross_section_features
    from training.labels_multi import attach_labels, load_close_panel
    s0, e0 = date.fromisoformat(start), date.fromisoformat(end)
    raw = Path(f"var/_bt_raw_{market}_{start}_{end}.parquet")
    if raw.exists():
        df = pd.read_parquet(raw)
    else:
        print(f"[bt] building {market} {s0}..{e0} (slow) ...", flush=True)
        with _ss() as s:
            df, _ = build_feature_matrix(s, market=market, start=s0, end=e0)
        df.to_parquet(raw)
        print(f"[bt] raw build rows={len(df)} -> {raw}", flush=True)
    with _ss() as s:
        close = load_close_panel(s, market=market, start=s0 - timedelta(days=10), end=e0)
    df = attach_labels(df, close)
    df = apply_cross_section_features(df)
    df.to_parquet(cache)
    print(f"[bt] period matrix rows={len(df)} cols={df.shape[1]} -> {cache}", flush=True)
    return df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="US")
    ap.add_argument("--start-cash", type=float, default=1000.0)
    ap.add_argument("--months", type=int, default=2)
    ap.add_argument("--step", type=int, default=10, help="rebalance every N trading days")
    ap.add_argument("--topk", type=int, default=50)
    ap.add_argument("--decile", type=float, default=0.1)
    ap.add_argument("--cost", type=float, default=None, help="round-trip cost frac (default KR .003/US .001)")
    ap.add_argument("--full", action="store_true", help="walk the whole period (burn-in then to end)")
    ap.add_argument("--regime-gate", action="store_true",
                    help="go to CASH when market regime is risk_off/crisis (bear defense)")
    ap.add_argument("--build-start", type=str, default=None,
                    help="build matrix for a past window [build-start, build-end] (ISO)")
    ap.add_argument("--build-end", type=str, default=None)
    ap.add_argument("--vix-gate", type=float, default=None,
                    help="proxy bear defense: cash when VIX percentile(252d) >= this (e.g. 0.8)")
    ap.add_argument("--long-short", action="store_true",
                    help="market-neutral: long top decile, SHORT bottom decile (can profit in bear)")
    ap.add_argument("--borrow", type=float, default=0.0,
                    help="extra per-rebalance short borrow cost frac (long-short only)")
    args = ap.parse_args()

    cost = args.cost if args.cost is not None else (0.003 if args.market == "KR" else 0.001)
    if args.build_start:
        df = _build_period(args.market, args.build_start, args.build_end)
    else:
        df = pd.read_parquet(f"var/_fs_ab_{args.market}_365.parquet")
    df["date"] = pd.to_datetime(df["date"])
    dates = np.sort(df["date"].unique())
    feats = [c for c in ALL_FEATURE_COLS if c in df.columns]
    test_td = int(args.months * 21)
    start_idx = 63 if args.full else len(dates) - test_td   # full: ~3mo training burn-in
    rebal_idxs = list(range(start_idx, len(dates) - 1, args.step))
    px = close_panel(args.market, None, pd.Timestamp(dates[start_idx-1]).date(),
                     pd.Timestamp(dates[-1]).date())

    # Regime series (for bear-defense gating): date -> label, ffilled.
    reg = None
    if args.regime_gate:
        with session_scope() as s:
            rr = s.execute(text(
                "SELECT ts, label FROM market_regime WHERE market=:m ORDER BY ts"
            ), {"m": args.market}).all()
        reg = pd.Series({pd.Timestamp(t): str(l).lower() for t, l in rr}).sort_index()

    def regime_at(d):
        if reg is None or reg.empty:
            return None
        s = reg[reg.index <= d]
        return s.iloc[-1] if len(s) else None

    base = dict(n_estimators=400, num_leaves=31, learning_rate=0.03,
                min_child_samples=100, subsample=0.7, colsample_bytree=0.6,
                reg_lambda=5.0, verbose=-1)

    print(f"[bt] {args.market}  test {pd.Timestamp(dates[start_idx]).date()}..{pd.Timestamp(dates[-1]).date()} "
          f"({test_td}td, ~{args.months}mo)  rebalances={len(rebal_idxs)} step={args.step}td  "
          f"cost={cost*100:.1f}%/rebal  start={args.start_cash:,.0f}", flush=True)

    cash = args.start_cash
    bench = args.start_cash
    log = []
    for j, i in enumerate(rebal_idxs):
        R = dates[i]
        exit_i = rebal_idxs[j+1] if j+1 < len(rebal_idxs) else len(dates)-1
        E = dates[exit_i]
        cut = dates[max(0, i-21)]                       # 21d embargo
        tr = df[df["date"] <= cut].dropna(subset=[RANK])
        atR = df[df["date"] == R].copy()
        if len(tr) < 500 or atR.empty:
            continue
        sel = lgb.LGBMRegressor(**base).fit(tr[feats].astype(float), tr[RANK].astype(float))
        top = pd.Series(sel.feature_importances_, index=feats).sort_values(ascending=False).head(args.topk).index.tolist()
        model = lgb.LGBMRegressor(**base).fit(tr[top].astype(float), tr[RANK].astype(float))
        atR["score"] = model.predict(atR[top].astype(float))
        atR["pct"] = atR["score"].rank(pct=True)
        picks = atR[atR["pct"] >= 1 - args.decile]["ticker"].tolist()

        # Bear defense: risk_off / crisis (HMM, 2024+) OR high VIX percentile
        # (proxy, available 10y) -> hold CASH this period (no position).
        rg = regime_at(R)
        gated = args.regime_gate and rg in ("risk_off", "crisis")
        if args.vix_gate is not None and "vix_pctile_252d" in atR.columns and len(atR):
            vp = float(atR["vix_pctile_252d"].iloc[0])
            if pd.notna(vp) and vp >= args.vix_gate:
                gated = True
                rg = f"vixpct={vp:.2f}"
        if gated:
            picks = []

        # realised equal-weight return R->E from actual closes
        def ret(tickers):
            r = []
            for t in tickers:
                if t in px.columns and R in px.index and E in px.index:
                    a, b = px.at[R, t], px.at[E, t]
                    if pd.notna(a) and pd.notna(b) and a > 0:
                        r.append(b/a - 1)
            return float(np.mean(r)) if r else 0.0
        allr = ret([c for c in px.columns])
        if args.long_short and not gated:
            # Market-neutral: long top decile, short bottom decile. Return is
            # the rank SPREAD (long - short). Net-0 market exposure -> can be
            # positive even when the market falls. 2 legs -> 2x cost + borrow.
            shorts = atR[atR["pct"] <= args.decile]["ticker"].tolist()
            long_r, short_r = ret(picks), ret(shorts)
            port = (long_r - short_r) - 2 * cost - args.borrow
        else:
            port = 0.0 if gated else ret(picks)      # cash earns 0% (ignore rate)
            if not gated:
                port -= cost
        cash *= (1 + port)
        bench *= (1 + allr)
        log.append((pd.Timestamp(R).date(), pd.Timestamp(E).date(), len(picks), port, allr, cash))
        tag = (f"  [CASH:{rg}]" if gated else
               (f"  [{rg}]" if args.regime_gate or args.vix_gate else ""))
        if args.long_short and not gated:
            tag = f"  [L/S {long_r*100:+.1f}/{short_r*100:+.1f}]"
        print(f"  [{j+1:>2}] {pd.Timestamp(R).date()}->{pd.Timestamp(E).date()}  "
              f"picks={len(picks):>3}  port={port*100:+6.2f}%  bench={allr*100:+6.2f}%  "
              f"cash={cash:,.0f}{tag}", flush=True)

    tot = cash/args.start_cash - 1
    btot = bench/args.start_cash - 1
    unit = "원" if args.market == "KR" else "$"
    L = pd.DataFrame(log, columns=["R", "E", "n", "port", "bench", "cash"])
    eq = L["cash"].values / args.start_cash
    mdd = float((eq / np.maximum.accumulate(eq) - 1).min()) if len(eq) else 0.0
    win = float((L["port"] > L["bench"]).mean()) if len(L) else 0.0
    # down-market subset: rebalances where benchmark fell
    dn = L[L["bench"] < 0]
    dn_port = float(dn["port"].mean()) if len(dn) else float("nan")
    dn_bench = float(dn["bench"].mean()) if len(dn) else float("nan")
    print(f"\n===== {args.market} {'전기간' if args.full else f'{args.months}개월'} 자동매매 (no look-ahead) =====")
    print(f"  기간:  {L['R'].iloc[0]} ~ {L['E'].iloc[-1]}  ({len(L)} 리밸런스)")
    print(f"  시작:  {args.start_cash:,.0f}{unit}")
    print(f"  종료(전략):  {cash:,.0f}{unit}   ({tot*100:+.2f}%)")
    print(f"  종료(벤치):  {bench:,.0f}{unit}   ({btot*100:+.2f}%)")
    print(f"  초과수익:   {(tot-btot)*100:+.2f}%p   (비용 {cost*100:.1f}%/리밸)")
    print(f"  전략 최대낙폭(MDD): {mdd*100:.2f}%   벤치 상회 리밸런스: {win*100:.0f}%")
    if len(dn):
        print(f"  ▼ 하락구간({len(dn)}회, 벤치<0): 전략 평균 {dn_port*100:+.2f}% vs 벤치 {dn_bench*100:+.2f}%")


if __name__ == "__main__":
    main()
