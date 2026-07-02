"""Walk-forward backtest of the INTEGRATED pipeline over history.

Answers the core integration question EARLY (instead of waiting months of
paper): **does the technical timing gate add value on top of the alpha
basket?**

HONEST PROTOCOL (no look-ahead): at each rebalance date ``t`` a fresh rank
model is trained ONLY on rows whose 21d label is fully realised by ``t``
(``date <= t − 21 trading days`` embargo). The production bundle is trained on
the WHOLE 2018-2024 span, so using it here would be in-sample — instead we
walk-forward-retrain so every prediction at ``t`` is genuinely out-of-sample.

Non-overlapping ``step``-day rebalances; four compounded series:
  * benchmark       — equal-weight the whole tradeable universe
  * alpha-only      — equal-weight the OOS top-decile basket, fully invested
  * timing (cash)   — enter only basket names whose technical score ≥ ENTRY_MIN
                      as-of ``t``; the rest stay CASH (honest integrated "wait")
  * timing (conc.)  — concentrate equally into the names that passed timing

Usage:
    uv run python scripts/backtest_integrated.py --market US --step 42 --with-timing
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import lightgbm as lgb

from core.db import session_scope
from core.types import Market
from decision.integrated_runner import ENTRY_MIN
from scripts.train_lgbm import ALL_FEATURE_COLS

RANK = "rank_fwd_21d"     # training target (cross-sectional rank)
RET = "ret_fwd_21d"       # realized forward return
EMBARGO = 21              # trading days — must cover the label horizon


def _tech_score_by_trade_date(session, market, ticker, as_of_date, lookback=300):
    """Technical score using TRADE_DATE-based bar loading (bypasses `as_of_ts`).

    In this DB every historical bar's ``as_of_ts`` is the bulk-load timestamp
    (~2026-06), so the production PIT guard (``as_of_ts <= as_of``) rejects ALL
    bars for historical dates → score_one_ticker returns None. For a backtest
    the honest look-ahead guard is ``trade_date <= as_of``."""
    from sqlalchemy import text
    from technical.indicators import from_ohlcv
    from technical.score import score_technical
    rows = session.execute(text(
        "SELECT open, high, low, close, volume FROM daily_prices "
        "WHERE market=:m AND ticker=:t AND trade_date <= :d "
        "ORDER BY trade_date DESC LIMIT :n"),
        {"m": market.value, "t": ticker, "d": as_of_date, "n": lookback}).all()
    if len(rows) < 30:
        return None
    rows = rows[::-1]
    ctx = from_ohlcv(
        opens=[float(r[0]) for r in rows], highs=[float(r[1]) for r in rows],
        lows=[float(r[2]) for r in rows], closes=[float(r[3]) for r in rows],
        volumes=[float(r[4]) for r in rows])
    return score_technical(ctx).score


def _compound(rets):
    eq = 1.0
    for r in rets:
        eq *= (1.0 + r)
    return eq - 1.0


def _cagr(total_ret, n_windows, days_per_window):
    years = max(n_windows * days_per_window / 252.0, 1e-9)
    return (1.0 + total_ret) ** (1.0 / years) - 1.0


def _sharpe_like(rets, windows_per_year):
    a = np.asarray(rets, float)
    if len(a) < 2 or a.std(ddof=1) == 0:
        return 0.0
    return float(a.mean() / a.std(ddof=1) * np.sqrt(windows_per_year))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="US", choices=["KR", "US"])
    ap.add_argument("--step", type=int, default=63, help="trading days between rebalances")
    ap.add_argument("--train-window", type=int, default=756, help="rolling train window (trading days; 0=expanding)")
    ap.add_argument("--decile", type=float, default=0.1)
    ap.add_argument("--cache", default=None)
    ap.add_argument("--with-timing", action="store_true", help="technical timing overlay (slower)")
    ap.add_argument("--tech-only", action="store_true",
                    help="TECHNICAL-ONLY cell: no alpha selection — equal-weight the whole "
                         "universe's technical-timing passers (fills the 2×2 matrix)")
    ap.add_argument("--production", action="store_true",
                    help="use the PRODUCTION training recipe per fold (per-market label "
                         "US=tb/KR=mn + per-date z-score + top-50 select + |label| weight) "
                         "instead of the naive rank proxy")
    ap.add_argument("--sweep", action="store_true",
                    help="GRID sweep of (basket decile × technical entry threshold × merge "
                         "method) from ONE expensive pass — ranks every config by Sharpe")
    ap.add_argument("--regime-split", action="store_true",
                    help="classify each rebalance window by benchmark sign (UP vs DOWN) and "
                         "compare concentrate-vs-cash WITHIN each — tests the regime-adaptive "
                         "merge directly (fills e.g. the US-bear cell) free of market/step confound")
    ap.add_argument("--direction-test", action="store_true",
                    help="THE honest test of the switch itself: does the price breadth (200d-SMA, "
                         "known at t) PREDICT the forward benchmark direction? If not, the "
                         "regime-adaptive merge has no live edge (needs hindsight it lacks).")
    ap.add_argument("--direction-gated", action="store_true",
                    help="PAYOFF test: route concentrate/cash by a LEARNED market-direction model "
                         "(trend+momentum+VIX+regime) predicted at t. Does gated beat cash-always?")
    ap.add_argument("--pooled-direction", action="store_true",
                    help="train the direction model on BOTH markets pooled (more samples — helps "
                         "the thin/noisy market). Used with --direction-gated.")
    ap.add_argument("--norm-mode", choices=["zscore", "winsor"], default="zscore",
                    help="per-date feature normalization (production default zscore; winsor clips ±3)")
    args = ap.parse_args()

    cache = args.cache or f"var/_bt_period_{args.market}_2018-01-01_2024-01-01.parquet"
    df = pd.read_parquet(cache)
    df["date"] = pd.to_datetime(df["date"])
    dates = np.sort(df["date"].unique())
    feats = [c for c in ALL_FEATURE_COLS if c in df.columns]
    market = Market(args.market)

    # ── DIRECTION TEST: can the switch (price breadth at t) predict the forward
    #    benchmark direction? No model — just breadth(t) vs realized fwd return. ──
    if args.direction_test:
        from scipy.stats import spearmanr
        reb_idx = list(range(EMBARGO + 252, len(dates), args.step))
        pts = []   # (breadth_t, fwd_bench)
        for i in reb_idx:
            t = dates[i]
            at_t = df[df["date"] == t].dropna(subset=[RET]).copy()
            if len(at_t) < 30 or "px_vs_sma200" not in at_t.columns:
                continue
            pv = pd.to_numeric(at_t["px_vs_sma200"], errors="coerce").dropna()
            if not len(pv):
                continue
            breadth = float((pv > 0).mean())            # fraction above 200d SMA, known at t
            fwd = float(at_t[RET].mean())               # realized forward benchmark return
            pts.append((breadth, fwd))
        b = np.array([p[0] for p in pts]); f = np.array([p[1] for p in pts])
        rho = spearmanr(b, f).correlation
        # switch at breadth median (proxy for the exposure≥0.6 concentrate gate)
        thr = float(np.median(b))
        hi = f[b >= thr]; loo = f[b < thr]
        # directional hit-rate: does breadth>0.5 predict fwd>0?
        pred_up = b >= 0.5
        acc = float((pred_up == (f > 0)).mean())
        base_up = float((f > 0).mean())
        print(f"\n===== DIRECTION TEST ({args.market}, {len(pts)} windows) =====")
        print(f"  breadth(t) vs forward bench return  Spearman rho = {rho:+.3f}")
        print(f"  high-breadth(>={thr:.2f}) fwd mean {np.mean(hi)*100:+.2f}%  (n={len(hi)})")
        print(f"  low-breadth (< {thr:.2f}) fwd mean {np.mean(loo)*100:+.2f}%  (n={len(loo)})")
        print(f"  spread (hi-lo) = {(np.mean(hi)-np.mean(loo))*100:+.2f}%p  "
              f"{'→ 스위치가 방향 예측력 있음' if np.mean(hi)>np.mean(loo) else '→ 예측력 없음/역전'}")
        print(f"  directional hit-rate (breadth>0.5 → up): {acc*100:.0f}%  (baseline up-rate {base_up*100:.0f}%)")
        return

    # ── PRODUCTION recipe setup (once, PIT-safe): per-market label + per-date
    #    cross-section z-score. Feature selection + |label| weighting happen
    #    per fold inside the loop. Blitz is already a feature column. ──
    target_col, use_prod = RANK, (args.production or args.sweep or args.regime_split or args.direction_gated)
    if use_prod:
        df["mn_fwd_21d"] = df[RET] - df.groupby("date")[RET].transform("mean")
        if args.market == "US":
            from scripts.alpha_lab import _tb_label, _close_panel
            px = _close_panel(args.market, df["date"].min().date(), df["date"].max().date())
            df["tbmn"] = _tb_label(df, px)
            target_col = "tbmn"
        else:
            target_col = "mn_fwd_21d"
        df = df.dropna(subset=[target_col]).copy()
        g = df.groupby("date")
        df[feats] = (df[feats] - g[feats].transform("mean")) / (g[feats].transform("std") + 1e-9)
        if args.norm_mode == "winsor":
            df[feats] = df[feats].clip(-3, 3)
        print(f"[prod] label={target_col} + per-date {args.norm_mode} on {len(feats)} feats", flush=True)

    params = dict(n_estimators=(300 if use_prod else 150), num_leaves=31,
                  learning_rate=(0.03 if use_prod else 0.05), min_child_samples=100,
                  subsample=0.7, colsample_bytree=0.6, reg_lambda=5.0, verbose=-1, n_jobs=-1)

    reb_idx = list(range(EMBARGO + 252, len(dates), args.step))  # need ≥1y history before first

    # ── TECHNICAL-ONLY cell (no alpha model): equal-weight universe timing-passers ──
    if args.tech_only:
        t_bench, t_tech, t_inv = [], [], []
        for i in reb_idx:
            t = dates[i]
            at_t = df[df["date"] == t].dropna(subset=[RET]).copy()
            if len(at_t) < 30:
                continue
            td = pd.Timestamp(t).date()
            passed = []
            with session_scope() as s:
                for tkr, r in zip(at_t["ticker"], at_t[RET]):
                    score = _tech_score_by_trade_date(s, market, str(tkr), td)
                    if score is not None and score >= ENTRY_MIN:
                        passed.append(float(r))
            t_bench.append(float(at_t[RET].mean()))
            t_inv.append(len(passed) / len(at_t))
            t_tech.append(float(np.mean(passed)) if passed else 0.0)
            print(f"  {td}  univ={len(at_t)}  passers={len(passed)} ({t_inv[-1]*100:.0f}%)  "
                  f"tech/win={t_tech[-1]*100:+.2f}%", flush=True)
        wpy = 252.0 / args.step

        def _line(name, series):
            tot = _compound(series)
            print(f"  {name:<22} tot {tot*100:+7.1f}%  CAGR {_cagr(tot,len(series),args.step)*100:+6.1f}%  "
                  f"/win {np.mean(series)*100:+5.2f}%  Sharpe~{_sharpe_like(series,wpy):+.2f}  "
                  f"hit {np.mean([r>0 for r in series])*100:.0f}%")
        print(f"\n===== TECH-ONLY cell ({args.market}, {len(t_tech)} rebalances × {args.step}d) =====")
        _line("benchmark(EW univ)", t_bench)
        _line("technical-only", t_tech)
        print(f"  avg invested fraction (technical-only): {np.mean(t_inv)*100:.0f}%")
        return

    # ── GRID SWEEP: (basket decile × technical entry threshold × merge method) ──
    if args.sweep:
        from collections import defaultdict
        DECILES = [0.05, 0.10, 0.20]
        THRS = [None, -20.0, -10.0, 0.0, 10.0, 20.0]   # None = no timing (alpha-only)
        acc = defaultdict(list); bench_s = []
        maxdec = max(DECILES)
        for i in reb_idx:
            t = dates[i]; train_cut = dates[i - EMBARGO]
            lo = dates[max(0, i - EMBARGO - args.train_window)] if args.train_window else dates[0]
            tr = df[(df["date"] <= train_cut) & (df["date"] > lo)].dropna(subset=[target_col])
            at_t = df[df["date"] == t].dropna(subset=[RET]).copy()
            if len(tr) < 5000 or len(at_t) < 30:
                continue
            sw = np.abs(tr[target_col].values)
            sel = lgb.LGBMRegressor(**params).fit(
                tr[feats].astype(float), tr[target_col].astype(float), sample_weight=sw)
            cols = pd.Series(sel.feature_importances_, index=feats).sort_values(
                ascending=False).head(50).index.tolist()
            model = lgb.LGBMRegressor(**params).fit(
                tr[cols].astype(float), tr[target_col].astype(float), sample_weight=sw)
            at_t["score"] = model.predict(at_t[cols].astype(float))
            at_t["rank_pct"] = at_t["score"].rank(pct=True)
            bench_s.append(float(at_t[RET].mean()))
            wide = at_t[at_t["rank_pct"] >= 1 - maxdec]
            td = pd.Timestamp(t).date()
            tsc = {}
            with session_scope() as s:
                for tkr in wide["ticker"]:
                    tsc[str(tkr)] = _tech_score_by_trade_date(s, market, str(tkr), td)
            for dec in DECILES:
                basket = at_t[at_t["rank_pct"] >= 1 - dec]
                n_b = max(len(basket), 1)
                rets = list(zip(basket["ticker"].astype(str), basket[RET].astype(float)))
                for thr in THRS:
                    if thr is None:
                        passed = [r for _, r in rets]
                    else:
                        passed = [r for tk, r in rets if tsc.get(tk) is not None and tsc[tk] >= thr]
                    acc[(dec, thr, "cash")].append(sum(passed) / n_b)
                    acc[(dec, thr, "conc")].append(float(np.mean(passed)) if passed else 0.0)
            print(f"  swept {td} (top{int(maxdec*100)}%={len(tsc)} scored)", flush=True)
        wpy = 252.0 / args.step
        rows = []
        for (dec, thr, meth), series in acc.items():
            rows.append((_sharpe_like(series, wpy), _compound(series), dec, thr, meth,
                         float(np.mean(series))))
        rows.sort(reverse=True)
        b_tot, b_sh = _compound(bench_s), _sharpe_like(bench_s, wpy)
        # persist first (so an encoding hiccup on print can't lose the expensive run)
        out = pd.DataFrame([{"decile": d, "entry": ("none" if th is None else th),
                             "method": m, "total": t, "sharpe": s, "per_win": w}
                            for s, t, d, th, m, w in rows])
        out.to_csv(f"var/_sweep_{args.market}.csv", index=False)
        print(f"\n===== SWEEP ({args.market}, {len(bench_s)} rebalances x {args.step}d) - Sharpe rank =====")
        print(f"  {'benchmark(EW univ)':<26} tot {b_tot*100:+7.1f}%  Sharpe {b_sh:+.2f}")
        print(f"  {'cfg basket/entry/method':<26} {'tot':>8}  {'Sharpe':>7}  {'/win':>7}")
        for s, t, d, th, m, w in rows:
            tag = f"{int(d*100)}%/{'none' if th is None else int(th)}/{m}"
            print(f"  {tag:<26} {t*100:+7.1f}%  {s:+.2f}  {w*100:+6.2f}%")
        return

    # ── DIRECTION-GATED: route concentrate/cash by a LEARNED direction model ──
    if args.direction_gated:
        from decision.market_direction import build_market_frame
        dparams = dict(n_estimators=200, num_leaves=15, learning_rate=0.03,
                       min_child_samples=20, subsample=0.8, colsample_bytree=0.7,
                       reg_lambda=5.0, verbose=-1)
        mf = build_market_frame(df.assign(date=df["date"]))
        mf_pool = None
        if args.pooled_direction:
            other = "KR" if args.market == "US" else "US"
            ocache = f"var/_bt_period_{other}_2018-01-01_2024-01-01.parquet"
            mfo = build_market_frame(pd.read_parquet(ocache))
            mf_pool = pd.concat([mf, mfo], ignore_index=True).sort_values("date")
            print(f"[dir] pooled direction frame: {len(mf)}(this)+{len(mfo)}({other}) dates", flush=True)
        mf_feats = [c for c in mf.columns if c not in ("date", "fwd")]
        mf = mf.reset_index(drop=True)
        dec = args.decile
        bench_s, cash_s, conc_s, gated_s, pred_up, dscores = [], [], [], [], [], []
        for i in reb_idx:
            t = dates[i]; train_cut = dates[i - EMBARGO]
            lo = dates[max(0, i - EMBARGO - args.train_window)] if args.train_window else dates[0]
            tr = df[(df["date"] <= train_cut) & (df["date"] > lo)].dropna(subset=[target_col])
            at_t = df[df["date"] == t].dropna(subset=[RET]).copy()
            if len(tr) < 5000 or len(at_t) < 30:
                continue
            # direction model: train on market frame(s) up to train_cut, predict at t
            src = mf_pool if mf_pool is not None else mf
            mtr = src[src["date"] <= train_cut]
            if len(mtr) < 252:
                continue
            dmodel = lgb.LGBMRegressor(**dparams).fit(mtr[mf_feats].astype(float), mtr["fwd"].astype(float))
            mrow = mf[mf["date"] == t]
            if mrow.empty:
                continue
            dscore = float(dmodel.predict(mrow[mf_feats].astype(float))[0])
            # alpha + timing
            sw = np.abs(tr[target_col].values)
            sel = lgb.LGBMRegressor(**params).fit(tr[feats].astype(float), tr[target_col].astype(float), sample_weight=sw)
            cols = pd.Series(sel.feature_importances_, index=feats).sort_values(ascending=False).head(50).index.tolist()
            model = lgb.LGBMRegressor(**params).fit(tr[cols].astype(float), tr[target_col].astype(float), sample_weight=sw)
            at_t["score"] = model.predict(at_t[cols].astype(float))
            at_t["rank_pct"] = at_t["score"].rank(pct=True)
            bench_s.append(float(at_t[RET].mean()))
            basket = at_t[at_t["rank_pct"] >= 1 - dec]
            n_b = max(len(basket), 1)
            td = pd.Timestamp(t).date()
            passed = []
            with session_scope() as s:
                for tkr, r in zip(basket["ticker"].astype(str), basket[RET].astype(float)):
                    sc = _tech_score_by_trade_date(s, market, tkr, td)
                    if sc is not None and sc >= ENTRY_MIN:
                        passed.append(float(r))
            cash_r = sum(passed) / n_b
            conc_r = float(np.mean(passed)) if passed else 0.0
            cash_s.append(cash_r); conc_s.append(conc_r); dscores.append(dscore)
            print(f"  {td}  dir={dscore*100:+.2f}%  cash={cash_r*100:+.2f}% conc={conc_r*100:+.2f}%", flush=True)
        wpy = 252.0 / args.step
        cash_a, conc_a, dsc = np.array(cash_s), np.array(conc_s), np.array(dscores)
        def _ln(name, series):
            tot = _compound(list(series))
            print(f"  {name:<26} tot {tot*100:+7.1f}%  Sharpe {_sharpe_like(list(series),wpy):+.2f}  /win {np.mean(series)*100:+.2f}%")
        pool = " POOLED" if mf_pool is not None else ""
        print(f"\n===== DIRECTION-GATED{pool} ({args.market}, {len(cash_s)} windows) =====")
        _ln("benchmark", bench_s)
        _ln("cash-always", cash_a)
        _ln("concentrate-always", conc_a)
        # confidence-threshold sweep: concentrate only when dscore > thr, else cash
        for thr in (0.0, 0.003, 0.005, 0.01, 0.015):
            gated = np.where(dsc > thr, conc_a, cash_a)
            up_rate = float((dsc > thr).mean())
            g = _compound(list(gated))
            print(f"  gated@dir>{thr*100:+.1f}%   tot {g*100:+7.1f}%  Sharpe {_sharpe_like(list(gated),wpy):+.2f}  "
                  f"/win {np.mean(gated)*100:+.2f}%  (concentrate {up_rate*100:.0f}%)")
        return

    # ── REGIME-SPLIT: per-window (bench, cash, conc) then bucket by benchmark sign ──
    if args.regime_split:
        dec = args.decile
        wins = []   # (bench, cash, conc)
        for i in reb_idx:
            t = dates[i]; train_cut = dates[i - EMBARGO]
            lo = dates[max(0, i - EMBARGO - args.train_window)] if args.train_window else dates[0]
            tr = df[(df["date"] <= train_cut) & (df["date"] > lo)].dropna(subset=[target_col])
            at_t = df[df["date"] == t].dropna(subset=[RET]).copy()
            if len(tr) < 5000 or len(at_t) < 30:
                continue
            sw = np.abs(tr[target_col].values)
            sel = lgb.LGBMRegressor(**params).fit(
                tr[feats].astype(float), tr[target_col].astype(float), sample_weight=sw)
            cols = pd.Series(sel.feature_importances_, index=feats).sort_values(
                ascending=False).head(50).index.tolist()
            model = lgb.LGBMRegressor(**params).fit(
                tr[cols].astype(float), tr[target_col].astype(float), sample_weight=sw)
            at_t["score"] = model.predict(at_t[cols].astype(float))
            at_t["rank_pct"] = at_t["score"].rank(pct=True)
            bench_r = float(at_t[RET].mean())
            basket = at_t[at_t["rank_pct"] >= 1 - dec]
            n_b = max(len(basket), 1)
            td = pd.Timestamp(t).date()
            passed = []
            with session_scope() as s:
                for tkr, r in zip(basket["ticker"].astype(str), basket[RET].astype(float)):
                    sc = _tech_score_by_trade_date(s, market, tkr, td)
                    if sc is not None and sc >= ENTRY_MIN:
                        passed.append(float(r))
            cash_r = sum(passed) / n_b
            conc_r = float(np.mean(passed)) if passed else 0.0
            wins.append((bench_r, cash_r, conc_r))
            print(f"  {td}  bench={bench_r*100:+.2f}%  cash={cash_r*100:+.2f}%  conc={conc_r*100:+.2f}%", flush=True)
        up = [(c, k) for b, c, k in wins if b > 0]
        dn = [(c, k) for b, c, k in wins if b <= 0]
        print(f"\n===== REGIME-SPLIT ({args.market}, {len(wins)} windows, basket {int(dec*100)}%) =====")
        for name, bucket in [("UP  (bench>0)", up), ("DOWN(bench<=0)", dn)]:
            if not bucket:
                print(f"  {name}: (없음)"); continue
            mc = float(np.mean([c for c, _ in bucket])); mk = float(np.mean([k for _, k in bucket]))
            win = "집중" if mk > mc else "현금"
            print(f"  {name}  n={len(bucket):<3} cash/win {mc*100:+.2f}%  conc/win {mk*100:+.2f}%  → {win} 우세")
        return

    bench, alpha, tim_cash, tim_conc, invested = [], [], [], [], []
    n_used = 0

    for i in reb_idx:
        t = dates[i]
        train_cut = dates[i - EMBARGO]
        lo = dates[max(0, i - EMBARGO - args.train_window)] if args.train_window else dates[0]
        tr = df[(df["date"] <= train_cut) & (df["date"] > lo)].dropna(subset=[target_col])
        at_t = df[df["date"] == t].dropna(subset=[RET]).copy()
        if len(tr) < 5000 or len(at_t) < 30:
            continue
        if use_prod:
            sw = np.abs(tr[target_col].values)                      # |label| weighting
            sel = lgb.LGBMRegressor(**params).fit(
                tr[feats].astype(float), tr[target_col].astype(float), sample_weight=sw)
            cols = pd.Series(sel.feature_importances_, index=feats).sort_values(
                ascending=False).head(50).index.tolist()            # top-50 select
            model = lgb.LGBMRegressor(**params).fit(
                tr[cols].astype(float), tr[target_col].astype(float), sample_weight=sw)
        else:
            cols = feats
            model = lgb.LGBMRegressor(**params).fit(tr[cols].astype(float), tr[RANK].astype(float))
        at_t["score"] = model.predict(at_t[cols].astype(float))
        at_t["rank_pct"] = at_t["score"].rank(pct=True)
        basket = at_t[at_t["rank_pct"] >= 1 - args.decile]
        if basket.empty:
            continue
        n_used += 1
        bench.append(float(at_t[RET].mean()))
        alpha.append(float(basket[RET].mean()))

        if args.with_timing:
            td = pd.Timestamp(t).date()
            passed = []
            with session_scope() as s:
                for tkr, r in zip(basket["ticker"], basket[RET]):
                    score = _tech_score_by_trade_date(s, market, str(tkr), td)
                    if score is not None and score >= ENTRY_MIN:
                        passed.append(float(r))
            n_b = len(basket)
            invested.append(len(passed) / n_b)
            tim_cash.append(sum(passed) / n_b)
            tim_conc.append(float(np.mean(passed)) if passed else 0.0)
        print(f"  [{n_used}] {pd.Timestamp(t).date()}  train<= {pd.Timestamp(train_cut).date()} "
              f"(n_tr={len(tr)})  basket={len(basket)}  alpha/win={alpha[-1]*100:+.2f}%", flush=True)

    wpy = 252.0 / args.step

    def line(name, series):
        tot = _compound(series)
        print(f"  {name:<22} tot {tot*100:+7.1f}%  CAGR {_cagr(tot,len(series),args.step)*100:+6.1f}%  "
              f"/win {np.mean(series)*100:+5.2f}%  Sharpe~{_sharpe_like(series,wpy):+.2f}  "
              f"hit {np.mean([r>0 for r in series])*100:.0f}%")

    print(f"\n===== integrated WALK-FORWARD backtest ({args.market}, {n_used} rebalances × {args.step}d) =====")
    line("benchmark(EW univ)", bench)
    line("alpha-only(basket)", alpha)
    if args.with_timing:
        line("timing(cash)", tim_cash)
        line("timing(concentrated)", tim_conc)
        a, tc, tk = _compound(alpha), _compound(tim_cash), _compound(tim_conc)
        print(f"\n  avg invested fraction (timing): {np.mean(invested)*100:.0f}%")
        print(f"  timing vs alpha-only  →  cash {(tc-a)*100:+.1f}%p, concentrated {(tk-a)*100:+.1f}%p (total)")
        print(f"  → 판정: {'타이밍 게이트가 도움' if tk > a else '타이밍 게이트 무익/유해(알파 단독 우위)'}")
    print(f"  alpha vs benchmark    →  {(_compound(alpha)-_compound(bench))*100:+.1f}%p total (선정 알파 초과수익)")


if __name__ == "__main__":
    main()
