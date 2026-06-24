"""Alpha lab — fast A/B harness for alpha-improvement levers.

Reuses the prebuilt 2018-2024 multi-regime matrices (var/_bt_period_{m}_2018-01-01_2024-01-01.parquet)
so every experiment is just a retrain+walk-forward (~minutes), no rebuild.

Each run_experiment(...) does an expanding-window, no-look-ahead walk-forward
(21d embargo, retrain each step) and returns the HONEST metrics we trust:
  alpha (strategy - equal-weight benchmark, total + per-year), MDD, IC,
  and the VIX-regime breakdown.

Config levers:
  label        : 'rank' (cross-sectional rank of fwd ret) | 'ret' (raw) |
                 'mn'   (market-neutral residual = ret - date-mean; targets pure alpha)
  topk         : # features by importance
  regime_cond  : per-VIX-bucket models (momentum<->mean-reversion switch)
  portfolio    : 'long' (top decile) | 'ls' (long top - short bottom, market-neutral)
  vix_gate     : cash when vix percentile >= this (bear defense) | None
  model        : 'lgbm' (more to come: xgb/cat/ensemble)

Usage:
    uv run python scripts/alpha_lab.py --market KR --configs baseline,regime,mn,ls,gate
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import lightgbm as lgb
from scipy.stats import spearmanr
from sqlalchemy import text

from core.db import session_scope
from scripts.train_lgbm import ALL_FEATURE_COLS

PERIOD = "2018-01-01_2024-01-01"
BASE = dict(n_estimators=300, num_leaves=31, learning_rate=0.04, min_child_samples=100,
            subsample=0.7, colsample_bytree=0.6, reg_lambda=5.0, verbose=-1, n_jobs=-1,
            random_state=42)


def _base(seed):
    b = dict(BASE); b["random_state"] = seed; return b


def _mlp_predict(Xtr, ytr, Xte, seed, *, epochs=40, batch=4096,
                 hidden=(256, 128, 64), dropout=0.1, lr=1e-3, arch="mlp"):
    """GPU MLP on the SAME reselected feature set as the GBDT path. Inputs are
    standardized with TRAIN-fold stats only (point-in-time safe). Output scale
    is irrelevant downstream (portfolio uses rank(pct)). CPU threads capped so a
    parallel CPU job (e.g. Wave-1 LightGBM) isn't starved."""
    import torch
    import torch.nn as nn
    torch.manual_seed(seed); np.random.seed(seed)
    torch.set_num_threads(2)
    dev = "cuda" if torch.cuda.is_available() else "cpu"

    xtr = Xtr.fillna(0.0).to_numpy(dtype="float32")
    xte = Xte.fillna(0.0).to_numpy(dtype="float32")
    mu = xtr.mean(0); sd = xtr.std(0) + 1e-6
    xtr = (xtr - mu) / sd; xte = (xte - mu) / sd
    y = ytr.to_numpy(dtype="float32")
    y = (y - y.mean()) / (y.std() + 1e-9)
    xt = torch.tensor(xtr, device=dev); yt = torch.tensor(y, device=dev).view(-1, 1)

    d = xtr.shape[1]
    layers = []
    for h in hidden:
        layers += [nn.Linear(d, h), nn.BatchNorm1d(h), nn.ReLU(), nn.Dropout(dropout)]
        d = h
    layers += [nn.Linear(d, 1)]
    net = nn.Sequential(*layers).to(dev)
    opt = torch.optim.Adam(net.parameters(), lr=lr, weight_decay=1e-5)
    lossf = nn.MSELoss()
    n = xt.shape[0]
    net.train()
    for _ in range(epochs):
        perm = torch.randperm(n, device=dev)
        for k in range(0, n, batch):
            idx = perm[k:k + batch]
            if idx.numel() < 2:
                continue
            opt.zero_grad()
            loss = lossf(net(xt[idx]), yt[idx]); loss.backward(); opt.step()
    net.eval()
    with torch.no_grad():
        return net(torch.tensor(xte, device=dev)).cpu().numpy().ravel()


def _add_residual_feats(df, which="all"):
    """Wave-3 explicit residual/idiosyncratic signals derived from existing
    trailing columns (point-in-time safe; cross-sectional mean is same-date).
      idio_vol      = total vol * sqrt(1 - corr_mkt^2)   (low-idio-vol anomaly)
      resid_mom     = ret_Nd - beta * market_ret_Nd      (market-adjusted momentum)
    which: 'all' | 'iv' (idio_vol only) | 'rm' (resid_mom only) — for ablation
    (idio_vol is a vol factor => tilt-prone; resid_mom is a documented robust
    factor). Isolate which one actually adds selection skill before adopting."""
    df = df.copy()
    new = []
    if which in ("all", "iv"):
        for h in (63, 252):
            v, c = f"vol_{h}d", f"corr_mkt_{h}d"
            if v in df.columns and c in df.columns:
                df[f"idio_vol_{h}d"] = df[v] * np.sqrt(np.clip(1 - df[c] ** 2, 0, 1))
                new.append(f"idio_vol_{h}d")
    if which in ("all", "rm"):
        beta = df["beta_252d"] if "beta_252d" in df.columns else 1.0
        for h in (63, 126, 252):
            r = f"ret_{h}d"
            if r in df.columns:
                mkt_mom = df.groupby("date")[r].transform("mean")
                df[f"resid_mom_{h}d"] = df[r] - beta * mkt_mom
                new.append(f"resid_mom_{h}d")
    if which == "blitz":
        # PROPER Blitz(2011) residual momentum: daily CAPM residual, accumulated
        # over 12-1m (skip last month), STANDARDIZED by residual vol (t-stat-like).
        # Documented to be more robust than price momentum. Needs ret_1d + beta.
        d = df.sort_values(["ticker", "date"])
        beta = d["beta_252d"] if "beta_252d" in d.columns else 1.0
        mkt1 = d.groupby("date")["ret_1d"].transform("mean")
        d["_resid"] = d["ret_1d"] - beta * mkt1
        g = d.groupby("ticker")["_resid"]
        for win, tag in ((231, "12m"), (105, "6m")):
            s = g.transform(lambda x: x.shift(21).rolling(win, min_periods=win // 2).sum())
            v = g.transform(lambda x: x.shift(21).rolling(win, min_periods=win // 2).std())
            d[f"resid_mom_blitz_{tag}"] = s / (v * np.sqrt(win) + 1e-9)
            new.append(f"resid_mom_blitz_{tag}")
        df = d.drop(columns=["_resid"])
    return df, new


def _add_wave5_feats(df):
    """Wave-5 orthogonal signals: short-term residual REVERSAL (mean-reversion,
    opposite of Blitz momentum), seasonality (month/turn-of-month), and pairwise
    interactions (vol×mom, beta×mom) the tree can't form as a single split."""
    df = df.copy(); new = []
    beta = df["beta_252d"] if "beta_252d" in df.columns else 1.0
    for h in (5, 10, 21):
        r = f"ret_{h}d"
        if r in df.columns:
            mkt = df.groupby("date")[r].transform("mean")
            df[f"resid_rev_{h}d"] = df[r] - beta * mkt
            new.append(f"resid_rev_{h}d")
    mo = df["date"].dt.month
    df["seas_month_sin"] = np.sin(2 * np.pi * mo / 12)
    df["seas_month_cos"] = np.cos(2 * np.pi * mo / 12)
    dom = df["date"].dt.day
    df["seas_turn_of_month"] = ((dom <= 3) | (dom >= 26)).astype(float)
    new += ["seas_month_sin", "seas_month_cos", "seas_turn_of_month"]
    for a, b in [("vol_252d", "ret_252d"), ("beta_252d", "ret_252d"), ("vol_21d", "ret_21d")]:
        if a in df.columns and b in df.columns:
            df[f"inter_{a}_x_{b}"] = df[a] * df[b]; new.append(f"inter_{a}_x_{b}")
    return df, new


def _add_macrodeep_feats(df, which="all"):
    """Wave-4 macro-deep: explicit factor×regime/macro interactions (factor-timing).
    which: 'all' | 'vix'(defensive×vix-state) | 'reg'(×regime) | 'macro'(yield/credit).
    All point-in-time safe (backward cols; cross-sec dispersion is same-date)."""
    df = df.copy(); new = []
    has = lambda *cs: all(c in df.columns for c in cs)
    rs = (df["regime_risk_on"].fillna(0) - df["regime_crisis"].fillna(0)
          if has("regime_risk_on", "regime_crisis") else None)
    vixp = df["vix_pctile_252d"] if "vix_pctile_252d" in df.columns else None
    for m in ("ret_21d", "ret_63d", "ret_252d", "resid_mom_blitz_12m"):
        if m in df.columns and rs is not None and which in ("all", "reg"):
            df[f"md_{m}_x_reg"] = df[m] * rs; new.append(f"md_{m}_x_reg")
        if m in df.columns and vixp is not None and which in ("all", "vix"):
            df[f"md_{m}_x_vixp"] = df[m] * vixp; new.append(f"md_{m}_x_vixp")
    if which in ("all", "vix"):
        for v in ("vol_252d", "beta_252d"):
            if v in df.columns and vixp is not None:
                df[f"md_{v}_x_vixp"] = df[v] * vixp; new.append(f"md_{v}_x_vixp")
    if which in ("all", "macro"):
        if has("earnings_yield", "yield_curve_2_10"):
            df["md_ey_x_yc"] = df["earnings_yield"] * df["yield_curve_2_10"]; new.append("md_ey_x_yc")
        if has("beta_252d", "hy_credit_spread"):
            df["md_beta_x_credit"] = df["beta_252d"] * df["hy_credit_spread"]; new.append("md_beta_x_credit")
        if has("beta_252d", "real_yield_21d_chg"):
            df["md_beta_x_ryld"] = df["beta_252d"] * df["real_yield_21d_chg"]; new.append("md_beta_x_ryld")
    if which in ("all", "reg") and "ret_21d" in df.columns:
        df["md_xs_disp_21d"] = df.groupby("date")["ret_21d"].transform("std"); new.append("md_xs_disp_21d")
    return df, new


def _close_panel(market, lo, hi):
    with session_scope() as s:
        rows = s.execute(text(
            "SELECT trade_date,ticker,close FROM daily_prices "
            "WHERE market=:m AND trade_date BETWEEN :a AND :b"), {"m": market, "a": lo, "b": hi}).all()
    p = pd.DataFrame(rows, columns=["date", "ticker", "close"]); p["date"] = pd.to_datetime(p["date"])
    return p.pivot_table(index="date", columns="ticker", values="close", aggfunc="first")


def _tb_label(df, px, H=21, k=1.5):
    """Triple-barrier (López) path-aware label: scan the next H days; the return
    is taken at the FIRST touch of ±k·vol_21d·sqrt(H) (profit-take / stop), else
    the H-day return (time barrier). Then market-neutralized. Captures the return
    a stop/target trade would actually realize, vs the fixed-horizon return."""
    P = px.sort_index().astype(float)
    vol = df.pivot_table(index="date", columns="ticker", values="vol_21d").reindex(
        index=P.index, columns=P.columns)
    bar = k * vol * np.sqrt(H)
    touched = pd.DataFrame(False, index=P.index, columns=P.columns)
    tb = pd.DataFrame(np.nan, index=P.index, columns=P.columns)
    for h in range(1, H + 1):
        cr = P.shift(-h) / P - 1.0
        hu = (cr >= bar) & (~touched)
        hd = (cr <= -bar) & (~touched)
        tb = tb.mask(hu, cr).mask(hd, cr)
        touched = touched | hu | hd
    tb = tb.mask(~touched, P.shift(-H) / P - 1.0)
    m = tb.stack().rename("_tb"); m.index.names = ["date", "ticker"]
    d2 = df.merge(m.reset_index(), on=["date", "ticker"], how="left")
    d2["_tbmn"] = d2["_tb"] - d2.groupby("date")["_tb"].transform("mean")
    return d2["_tbmn"].values


def _vix_bucket(v):
    if not np.isfinite(v):
        return 1
    return 0 if v < 0.4 else (2 if v >= 0.7 else 1)


def run_experiment(df, market, *, label="rank", topk=50, regime_cond=False,
                   portfolio="long", vix_gate=None, decile=0.1, step=21, cost=None,
                   reselect=False, seed=42, model="lgbm", regfeat=False, normalize=False,
                   wave3=False, wave5=False, sample_weight=None, model_params=None,
                   embargo=21, macrodeep=False, shortvol=False, drop_prefix=None):
    cost = cost if cost is not None else (0.003 if market == "KR" else 0.001)
    feats = [c for c in ALL_FEATURE_COLS if c in df.columns]
    if wave3:
        df, _extra = _add_residual_feats(df, which=(wave3 if isinstance(wave3, str) else "all"))
        feats = feats + _extra
    if wave5:
        df, _extra5 = _add_wave5_feats(df)
        feats = feats + _extra5
    if macrodeep:
        df, _extramd = _add_macrodeep_feats(df, which=(macrodeep if isinstance(macrodeep, str) else "all"))
        feats = feats + _extramd
    if shortvol:   # short-volume features (augment_shortvol.py adds sv_* cols)
        feats = feats + [c for c in df.columns if c.startswith("sv_") and c not in feats]
    if drop_prefix:   # ablation: remove feature columns by prefix (e.g. drop Blitz)
        feats = [c for c in feats if not c.startswith(drop_prefix)]
    dates = np.sort(df["date"].unique())
    px = _close_panel(market, pd.Timestamp(dates[0]).date(), pd.Timestamp(dates[-1]).date())

    if regfeat:
        # #1 regime-as-feature: inject market-level trend/vol so the TREE can
        # split on regime and learn regime-conditional feature behaviour.
        mkt = px.mean(axis=1)
        trend = pd.DataFrame({"date": mkt.index,
                              "mkt_trail_63d": mkt.pct_change(63).values,
                              "mkt_trail_21d": mkt.pct_change(21).values,
                              "mkt_vol_21d": mkt.pct_change().rolling(21).std().values})
        df = df.merge(trend, on="date", how="left")
        feats = feats + ["mkt_trail_63d", "mkt_trail_21d", "mkt_vol_21d"]

    # target column
    if label == "rank":
        tgt = "rank_fwd_21d"
    elif label == "ret":
        tgt = "ret_fwd_21d"
    elif label == "mn":   # market-neutral residual fwd return
        df = df.copy()
        df["mn_fwd_21d"] = df["ret_fwd_21d"] - df.groupby("date")["ret_fwd_21d"].transform("mean")
        tgt = "mn_fwd_21d"
    elif label == "mnmh":  # multi-horizon mn: blend per-date-standardized residuals @5/21/63d
        df = df.copy()
        parts = []
        for h in (5, 21, 63):
            c = f"ret_fwd_{h}d"
            if c in df.columns:
                r = df[c] - df.groupby("date")[c].transform("mean")
                parts.append(r / (r.groupby(df["date"]).transform("std") + 1e-9))
        df["mnmh"] = sum(parts) / len(parts)
        tgt = "mnmh"
    elif label == "tb":   # triple-barrier (path-aware, market-neutralized) label
        df = df.copy()
        df["tbmn"] = _tb_label(df, px)
        tgt = "tbmn"
    elif label == "sn":   # sector-neutral residual (subtract date-sector mean)
        df = df.copy()
        with session_scope() as s:
            secmap = {t: sec for t, sec in s.execute(text(
                "SELECT ticker, COALESCE(sector,'NA') FROM securities WHERE market=:m"),
                {"m": market}).all()}
        df["_sec"] = df["ticker"].map(secmap).fillna("NA")
        df["sn_fwd_21d"] = df["ret_fwd_21d"] - df.groupby(["date", "_sec"])["ret_fwd_21d"].transform("mean")
        tgt = "sn_fwd_21d"
    elif label == "vadj":  # vol-adjusted market-neutral residual (Sharpe-like)
        df = df.copy()
        mnr = df["ret_fwd_21d"] - df.groupby("date")["ret_fwd_21d"].transform("mean")
        vol = df["vol_21d"] if "vol_21d" in df.columns else df.groupby("ticker")["ret_fwd_21d"].transform("std")
        df["vadj_fwd_21d"] = mnr / (vol.abs() + 1e-4)
        tgt = "vadj_fwd_21d"
    else:
        raise ValueError(label)

    if normalize:
        # cross-sectional per-date feature transform (standard quant prep).
        # method: "z"/True z-score | "rank" percentile | "winsor" clipped-z.
        method = "z" if normalize is True else str(normalize)
        g = df.groupby("date")
        if method == "rank":
            z = df[feats].groupby(df["date"]).rank(pct=True) - 0.5
        else:
            z = (df[feats] - g[feats].transform("mean")) / (g[feats].transform("std") + 1e-9)
            if method == "winsor":
                z = z.clip(-3, 3)
            if method == "neutral" and "beta_252d" in df.columns:
                # factor-neutralize: residualize every z-feature vs (z-scored) beta
                # per date — removes unintended market-beta exposure from features.
                b = ((df["beta_252d"] - g["beta_252d"].transform("mean"))
                     / (g["beta_252d"].transform("std") + 1e-9)).fillna(0.0)
                date_idx = df["date"]
                zb = z.mul(b, axis=0).groupby(date_idx).transform("sum")
                bb = (b * b).groupby(date_idx).transform("sum") + 1e-9
                z = z - zb.div(bb, axis=0).mul(b, axis=0)
        z.columns = [c + "_z" for c in feats]
        df = pd.concat([df, z], axis=1)
        feats = list(z.columns)

    start_idx = 63
    rebal = list(range(start_idx, len(dates) - 1, step))
    # Feature selection ONCE on the initial training window (past data only;
    # importance is stable enough that per-rebalance reselection isn't worth
    # the 5x cost). 'top' is reused for every rebalance.
    init_cut = dates[max(0, start_idx - 21)]
    tr0 = df[df["date"] <= init_cut].dropna(subset=[tgt])
    sel = lgb.LGBMRegressor(**_base(seed)).fit(tr0[feats].astype(float), tr0[tgt].astype(float))
    top = pd.Series(sel.feature_importances_, index=feats).sort_values(ascending=False).head(topk).index.tolist()
    if regime_cond and "vix_pctile_252d" in df.columns:
        df = df.assign(_vb=df["vix_pctile_252d"].apply(_vix_bucket))

    cash = bench = 1.0
    log = []
    ics = []   # OOS rank-IC: does score actually rank realized fwd returns? (selection skill)
    for j, i in enumerate(rebal):
        R = dates[i]; E = dates[rebal[j+1]] if j+1 < len(rebal) else dates[-1]
        cut = dates[max(0, i-embargo)]
        tr = df[df["date"] <= cut].dropna(subset=[tgt])
        atR = df[df["date"] == R].copy()
        if len(tr) < 500 or atR.empty:
            continue
        vp = float(atR["vix_pctile_252d"].iloc[0]) if "vix_pctile_252d" in atR.columns else np.nan
        if regime_cond and "_vb" in df.columns:
            tr_b = tr[tr["_vb"] == _vix_bucket(vp)]
            tr_use = tr_b if len(tr_b) > 500 else tr
        else:
            tr_use = tr
        top_r = top
        if reselect:  # re-select features from this window's past data
            selr = lgb.LGBMRegressor(**_base(seed)).fit(tr[feats].astype(float), tr[tgt].astype(float))
            top_r = pd.Series(selr.feature_importances_, index=feats).sort_values(ascending=False).head(topk).index.tolist()
        Xtr, ytr, Xte = tr_use[top_r].astype(float), tr_use[tgt].astype(float), atR[top_r].astype(float)
        if model in ("ridge", "lasso", "enet"):
            from sklearn.linear_model import Ridge, Lasso, ElasticNet
            lm = {"ridge": Ridge(alpha=10.0),
                  "lasso": Lasso(alpha=1e-4, max_iter=2000),
                  "enet": ElasticNet(alpha=1e-4, l1_ratio=0.5, max_iter=2000)}[model]
            lm.fit(Xtr.fillna(0.0), ytr)
            atR["score"] = lm.predict(Xte.fillna(0.0))
        elif model == "xgb":
            import xgboost as xgb
            xm = xgb.XGBRegressor(n_estimators=300, max_depth=5, learning_rate=0.04,
                                  subsample=0.7, colsample_bytree=0.6, reg_lambda=5.0,
                                  random_state=seed, n_jobs=-1, tree_method="hist")
            xm.fit(Xtr, ytr); atR["score"] = xm.predict(Xte)
        elif model == "cat":
            from catboost import CatBoostRegressor
            cm = CatBoostRegressor(iterations=300, depth=5, learning_rate=0.04,
                                   l2_leaf_reg=5.0, random_seed=seed, verbose=0,
                                   allow_writing_files=False)
            cm.fit(Xtr.fillna(0.0), ytr); atR["score"] = cm.predict(Xte.fillna(0.0))
        elif model == "et":
            from sklearn.ensemble import ExtraTreesRegressor
            em = ExtraTreesRegressor(n_estimators=300, max_features=0.5, min_samples_leaf=50,
                                     random_state=seed, n_jobs=-1)
            em.fit(Xtr.fillna(0.0), ytr); atR["score"] = em.predict(Xte.fillna(0.0))
        elif model == "ens3":
            # seed-ensemble: 3 LGBM seeds, rank-averaged — cuts the ±5-9%/yr seed
            # variance we measured. Same |label| weighting if requested.
            swe = (np.abs(ytr.values) + 1e-6) if sample_weight == "abslabel" else None
            ps = []
            for k in range(3):
                b = _base(seed * 7 + k * 101)
                if model_params:
                    b.update(model_params)
                mm = lgb.LGBMRegressor(**b).fit(Xtr, ytr, sample_weight=swe)
                ps.append(pd.Series(mm.predict(Xte)).rank(pct=True).values)
            atR["score"] = np.mean(ps, axis=0)
        elif model == "ltr":
            # learning-to-rank (LambdaMART): optimize per-date RANKING directly
            # (objective matches the eval = rank-IC), vs L2 regression of the value.
            tr_s = tr_use.sort_values("date")
            rel = tr_s.groupby("date")[tgt].rank(pct=True).mul(30).round().astype(int).values
            grp = tr_s.groupby("date").size().values
            rk = lgb.LGBMRanker(objective="lambdarank", n_estimators=300, num_leaves=31,
                                learning_rate=0.04, min_child_samples=100, subsample=0.7,
                                colsample_bytree=0.6, reg_lambda=5.0, random_state=seed,
                                n_jobs=-1, verbose=-1)
            rk.fit(tr_s[top_r].astype(float), rel, group=grp)
            atR["score"] = rk.predict(Xte)
        elif model == "mlp":
            atR["score"] = _mlp_predict(Xtr, ytr, Xte, seed)
        elif model == "mlp_wide":
            atR["score"] = _mlp_predict(Xtr, ytr, Xte, seed, hidden=(512, 256, 128), dropout=0.2)
        elif model == "mlp_ens":   # avg of 3 MLP seeds (rank-blended) — variance cut
            ps = [pd.Series(_mlp_predict(Xtr, ytr, Xte, seed + k)).rank(pct=True).values for k in range(3)]
            atR["score"] = np.mean(ps, axis=0)
        elif model == "ens":
            from sklearn.ensemble import HistGradientBoostingRegressor as HGB
            m = lgb.LGBMRegressor(**_base(seed)).fit(Xtr, ytr)
            h = HGB(max_iter=300, learning_rate=0.05, max_leaf_nodes=31,
                    l2_regularization=1.0, random_state=seed).fit(Xtr, ytr)
            atR["score"] = (pd.Series(m.predict(Xte)).rank(pct=True).values
                            + pd.Series(h.predict(Xte)).rank(pct=True).values) / 2.0
        else:
            sw = None
            if sample_weight == "recency":      # up-weight recent rows (linear by date rank)
                sw = 0.5 + tr_use["date"].rank(pct=True).values
            elif sample_weight == "abslabel":   # focus on big movers (|residual return|)
                sw = np.abs(ytr.values) + 1e-6
            elif sample_weight in ("uniq", "uniqabs"):
                # López avg-uniqueness ~ 1/concurrency of overlapping 21d labels.
                # (cross-sectional uniform-horizon => ~constant in interior; tests
                # whether overlap down-weighting helps at all.)
                ud = np.sort(tr_use["date"].unique())
                idx = pd.Series(np.arange(len(ud)), index=ud)
                di = tr_use["date"].map(idx).values
                conc = np.clip(np.minimum(di + 1, 21), 1, None)  # # of overlapping label-starts
                sw = 1.0 / conc
                if sample_weight == "uniqabs":
                    sw = sw * (np.abs(ytr.values) + 1e-6)
            elif sample_weight in ("disp", "dispabs"):
                # per-date dispersion weighting: up-weight dates with high cross-
                # sectional label spread (more learnable signal that day).
                dsp = tr_use.assign(_y=ytr.values).groupby("date")["_y"].transform("std")
                sw = (dsp.values + 1e-6)
                if sample_weight == "dispabs":
                    sw = sw * (np.abs(ytr.values) + 1e-6)
            mp = _base(seed)
            if model_params:
                mp.update(model_params)
            m = lgb.LGBMRegressor(**mp).fit(Xtr, ytr, sample_weight=sw)
            atR["score"] = m.predict(Xte)
        atR["pct"] = atR["score"].rank(pct=True)

        gated = vix_gate is not None and np.isfinite(vp) and vp >= vix_gate

        def ret(tks):
            r = [px.at[E, t]/px.at[R, t]-1 for t in tks
                 if t in px.columns and R in px.index and E in px.index
                 and pd.notna(px.at[R, t]) and pd.notna(px.at[E, t]) and px.at[R, t] > 0]
            return float(np.mean(r)) if r else 0.0

        longset = atR[atR["pct"] >= 1-decile]
        longs = longset["ticker"].tolist()
        if gated:
            port = 0.0
        elif portfolio == "ls":
            shorts = atR[atR["pct"] <= decile]["ticker"].tolist()
            port = (ret(longs) - ret(shorts)) - 2*cost
        elif portfolio == "conv":     # conviction-weighted longs (by score rank)
            w = (longset["pct"] - (1-decile)); w = w / (w.sum() + 1e-9)
            rr = [(float(px.at[E, t])/float(px.at[R, t])-1, wi) for t, wi in zip(longs, w)
                  if t in px.columns and R in px.index and E in px.index
                  and pd.notna(px.at[R, t]) and pd.notna(px.at[E, t]) and px.at[R, t] > 0]
            port = (sum(r*wi for r, wi in rr)/sum(wi for _, wi in rr) if rr else 0.0) - cost
        elif portfolio == "meta":     # meta-labeling: 2nd classifier sizes longs by P(mn>0)
            cm = lgb.LGBMClassifier(**_base(seed)).fit(Xtr, (ytr > 0).astype(int))
            pup = cm.predict_proba(longset[top_r].astype(float))[:, 1]
            w = np.clip(pup - 0.5, 0, None); w = w / (w.sum() + 1e-9)
            rr = [(float(px.at[E, t])/float(px.at[R, t])-1, wi) for t, wi in zip(longs, w)
                  if t in px.columns and R in px.index and E in px.index
                  and pd.notna(px.at[R, t]) and pd.notna(px.at[E, t]) and px.at[R, t] > 0]
            port = (sum(r*wi for r, wi in rr)/sum(wi for _, wi in rr) if rr else 0.0) - cost
        else:
            port = ret(longs) - cost
        allr = ret(list(px.columns))
        # OOS rank-IC (demean is monotone => irrelevant for spearman ranks)
        if R in px.index and E in px.index:
            fr = (px.loc[E] / px.loc[R] - 1)
            frv = pd.to_numeric(atR["ticker"].map(fr), errors="coerce").to_numpy(dtype=float)
            scv = pd.to_numeric(atR["score"], errors="coerce").to_numpy(dtype=float)
            mok = np.isfinite(frv) & np.isfinite(scv)
            if mok.sum() > 10:
                ics.append(spearmanr(scv[mok], frv[mok]).correlation)
        cash *= (1+port); bench *= (1+allr)
        log.append((port, allr, cash, vp))

    L = pd.DataFrame(log, columns=["port", "bench", "cash", "vix"])
    if L.empty:
        return {}
    ic_arr = np.array([x for x in ics if np.isfinite(x)])
    n_years = (pd.Timestamp(dates[-1]) - pd.Timestamp(dates[start_idx])).days / 365.25
    tot = cash - 1; btot = bench - 1
    eq = L["cash"].values
    mdd = float((eq/np.maximum.accumulate(eq) - 1).min())
    # concentration: how much of total alpha comes from the best 5 rebalances?
    # ~100%+ => a few lucky folds carry it (artifact/regime luck); ~30-50% => spread/robust.
    fa = (L["port"] - L["bench"]).values
    conc5 = float(np.sort(fa)[-5:].sum() / fa.sum()) if abs(fa.sum()) > 1e-9 else float("nan")
    vb = {}
    for nm, msk in [("lo", L.vix < 0.4), ("mid", (L.vix >= 0.4) & (L.vix < 0.7)), ("hi", L.vix >= 0.7)]:
        s = L[msk]
        vb[nm] = round((s["port"]-s["bench"]).mean()*100, 2) if len(s) else None
    return {"ret": round(tot*100, 1), "bench": round(btot*100, 1),
            "alpha_total": round((tot-btot)*100, 1),
            "alpha_yr": round((tot-btot)/n_years*100, 2),
            "mdd": round(mdd*100, 1), "win": round((L.port > L.bench).mean()*100),
            "ic": round(float(ic_arr.mean()), 4) if len(ic_arr) else None,
            "ic_pos": round(float((ic_arr > 0).mean())*100) if len(ic_arr) else None,
            "conc5": round(conc5*100) if np.isfinite(conc5) else None,
            "vix_alpha": vb, "n": len(L)}


def run_regime_suite(df, market, *, seed=42, step=21, decile=0.1, cost=None,
                     reg_lookback=63, up_thr=0.03, dn_thr=-0.03):
    """No-look-ahead online regime router. Each rebalance: (1) classify the
    market state from the TRAILING eq-weight return (up/side/down), (2) pick
    the lever with the best PAST realised return in that same regime, (3) use
    it. Lever menu = {mn_long, mn_ls(long-short), cash}. The regime->lever map
    is learned online from past rebalances only.

    Reports the router vs always-mn_long vs benchmark, and which lever it
    chose per regime."""
    cost = cost if cost is not None else (0.003 if market == "KR" else 0.001)
    feats = [c for c in ALL_FEATURE_COLS if c in df.columns]
    df = df.copy()
    df["mn_fwd_21d"] = df["ret_fwd_21d"] - df.groupby("date")["ret_fwd_21d"].transform("mean")
    tgt = "mn_fwd_21d"
    dates = np.sort(df["date"].unique())
    px = _close_panel(market, pd.Timestamp(dates[0]).date(), pd.Timestamp(dates[-1]).date())
    mkt = px.mean(axis=1)                         # eq-weight index level proxy

    def trail_at(R):
        s = mkt[mkt.index <= R]
        if len(s) < reg_lookback + 1:
            return 0.0
        return float(s.iloc[-1] / s.iloc[-(reg_lookback+1)] - 1)

    def regime_of(t):
        return "up" if t >= up_thr else ("dn" if t <= dn_thr else "side")

    start_idx = 63
    rebal = list(range(start_idx, len(dates) - 1, step))
    rows = []   # per rebalance: dict(date, reg, allr, mn_long, mn_ls, cash)
    for j, i in enumerate(rebal):
        R = dates[i]; E = dates[rebal[j+1]] if j+1 < len(rebal) else dates[-1]
        cut = dates[max(0, i-21)]
        tr = df[df["date"] <= cut].dropna(subset=[tgt]); atR = df[df["date"] == R].copy()
        if len(tr) < 500 or atR.empty:
            continue
        sel = lgb.LGBMRegressor(**_base(seed)).fit(tr[feats].astype(float), tr[tgt].astype(float))
        top = pd.Series(sel.feature_importances_, index=feats).sort_values(ascending=False).head(50).index.tolist()
        m = lgb.LGBMRegressor(**_base(seed)).fit(tr[top].astype(float), tr[tgt].astype(float))
        atR["pct"] = pd.Series(m.predict(atR[top].astype(float))).rank(pct=True).values

        def ret(tks):
            r = [px.at[E, t]/px.at[R, t]-1 for t in tks
                 if t in px.columns and R in px.index and E in px.index
                 and pd.notna(px.at[R, t]) and pd.notna(px.at[E, t]) and px.at[R, t] > 0]
            return float(np.mean(r)) if r else 0.0
        longs = atR[atR["pct"] >= 1-decile]["ticker"].tolist()
        shorts = atR[atR["pct"] <= decile]["ticker"].tolist()
        tr_v = trail_at(R)
        rows.append({"R": R, "trail": tr_v, "reg": regime_of(tr_v),
                     "allr": ret(list(px.columns)),
                     "mn_long": ret(longs) - cost,
                     "mn_ls": (ret(longs) - ret(shorts)) - 2*cost,
                     "cash": 0.0})

    levers = ["mn_long", "mn_ls", "cash"]
    # Four meta-strategies on the SAME per-rebalance lever returns:
    #  base   = always mn_long
    #  router = hard pick best-past-in-regime lever (#switch)
    #  expo   = mn_long scaled by regime exposure (#2 risk overlay: dn->0.5)
    #  soft   = continuous blend mn_long<->mn_ls by trailing-return (#3)
    c = {"base": 1.0, "router": 1.0, "expo": 1.0, "soft": 1.0}
    bench = 1.0
    hist, chosen = [], {"up": {}, "dn": {}, "side": {}}
    for x in rows:
        same = [h for h in hist if h["reg"] == x["reg"]]
        pick = "mn_long" if len(same) < 3 else max(levers, key=lambda lv: np.mean([h[lv] for h in same]))
        chosen[x["reg"]][pick] = chosen[x["reg"]].get(pick, 0) + 1
        expo = 0.5 if x["reg"] == "dn" else 1.0
        w = float(np.clip((x["trail"] - dn_thr) / (up_thr - dn_thr), 0.0, 1.0))  # 0 in dn, 1 in up
        c["base"] *= (1 + x["mn_long"])
        c["router"] *= (1 + x[pick])
        c["expo"] *= (1 + expo * x["mn_long"])
        c["soft"] *= (1 + (w * x["mn_long"] + (1 - w) * x["mn_ls"]))
        bench *= (1 + x["allr"])
        hist.append(x)
    ny = (pd.Timestamp(dates[-1]) - pd.Timestamp(dates[start_idx])).days / 365.25
    out = {k: round((v - bench) / ny * 100, 2) for k, v in c.items()}  # alpha/yr each
    out["chosen"] = chosen; out["n"] = len(rows)
    return out


CONFIGS = {
    "baseline":   dict(label="rank", regime_cond=False, portfolio="long"),
    "mn":         dict(label="mn",   regime_cond=False, portfolio="long"),
    "regime":     dict(label="rank", regime_cond=True,  portfolio="long"),
    "regime_mn":  dict(label="mn",   regime_cond=True,  portfolio="long"),
    "ls":         dict(label="rank", regime_cond=False, portfolio="ls"),
    "gate":       dict(label="rank", regime_cond=False, portfolio="long", vix_gate=0.7),
    "regime_gate":dict(label="rank", regime_cond=True,  portfolio="long", vix_gate=0.7),
    # reselect (per-rebalance feature selection) variants — reconcile vs backtest_capital
    "baseline_rs":dict(label="rank", regime_cond=False, portfolio="long", reselect=True),
    "mn_rs":      dict(label="mn",   regime_cond=False, portfolio="long", reselect=True),
    "regime_rs":  dict(label="rank", regime_cond=True,  portfolio="long", reselect=True),
    "regime_mn_rs":dict(label="mn",  regime_cond=True,  portfolio="long", reselect=True),
    # mn-label lever stack (build on the validated winner)
    "mn_top30":   dict(label="mn", regime_cond=False, portfolio="long", reselect=True, topk=30),
    "mn_top100":  dict(label="mn", regime_cond=False, portfolio="long", reselect=True, topk=100),
    "mn_ls":      dict(label="mn", regime_cond=False, portfolio="ls",   reselect=True),
    "sn_rs":      dict(label="sn", regime_cond=False, portfolio="long", reselect=True),
    "mn_ens":     dict(label="mn", regime_cond=False, portfolio="long", reselect=True, model="ens"),
    "mn_regfeat": dict(label="mn", regime_cond=False, portfolio="long", reselect=True, regfeat=True),
    # untested categories: feature preprocessing, linear model, vol-adj label
    "mn_norm":    dict(label="mn",   regime_cond=False, portfolio="long", reselect=True, normalize=True),
    "mn_ridge":   dict(label="mn",   regime_cond=False, portfolio="long", reselect=True, normalize=True, model="ridge"),
    "mn_ridge_raw":dict(label="mn",  regime_cond=False, portfolio="long", reselect=True, model="ridge"),
    "vadj_rs":    dict(label="vadj", regime_cond=False, portfolio="long", reselect=True),
    # Wave 2 — preprocessing variants, model classes, portfolio
    "mn_rank":    dict(label="mn", reselect=True, normalize="rank"),
    "mn_winsor":  dict(label="mn", reselect=True, normalize="winsor"),
    "mn_enet":    dict(label="mn", reselect=True, normalize=True, model="enet"),
    "mn_lasso":   dict(label="mn", reselect=True, normalize=True, model="lasso"),
    "mn_xgb":     dict(label="mn", reselect=True, normalize=True, model="xgb"),
    "mn_cat":     dict(label="mn", reselect=True, normalize=True, model="cat"),
    "mn_et":      dict(label="mn", reselect=True, normalize=True, model="et"),
    "mn_conv":    dict(label="mn", reselect=True, normalize=True, portfolio="conv"),
    # Wave 3 — explicit residual/idiosyncratic signals on top of mn_norm
    "mn_w3":      dict(label="mn", reselect=True, normalize=True, wave3=True),
    "mn_w3_raw":  dict(label="mn", reselect=True, wave3=True),
    "mn_w3_rm":   dict(label="mn", reselect=True, normalize=True, wave3="rm"),  # resid-mom only
    "mn_w3_iv":   dict(label="mn", reselect=True, normalize=True, wave3="iv"),  # idio-vol only
    "mn_blitz":   dict(label="mn", reselect=True, normalize=True, wave3="blitz"),  # proper Blitz resid-mom
    # Wave 5 — orthogonal features, sample weighting, portfolio variants (on mn_norm+Blitz base)
    "mn_w5":       dict(label="mn", reselect=True, normalize=True, wave5=True),
    "mn_swrec":    dict(label="mn", reselect=True, normalize=True, sample_weight="recency"),
    "mn_swabs":    dict(label="mn", reselect=True, normalize=True, sample_weight="abslabel"),
    "mn_dec05":    dict(label="mn", reselect=True, normalize=True, decile=0.05),
    "mn_dec20":    dict(label="mn", reselect=True, normalize=True, decile=0.20),
    # Wave 6 — factor-neutralization, and the Wave5 winner stacked
    "mn_neutral":  dict(label="mn", reselect=True, normalize="neutral"),
    "mn_swabs_neu":dict(label="mn", reselect=True, normalize="neutral", sample_weight="abslabel"),
    # Wave 7 — multi-horizon label. 63d horizon needs a 63d embargo or it LEAKS
    # (21d embargo => 245%/yr, IC 0.13 artifact). Fair test uses embargo=63 for
    # BOTH the candidate and the baseline.
    "mn_mh_emb":   dict(label="mnmh", reselect=True, normalize=True, sample_weight="abslabel", embargo=63),
    "mn_swabs_emb":dict(label="mn",   reselect=True, normalize=True, sample_weight="abslabel", embargo=63),
    # triple-barrier (path-aware) label, on the mn_norm+swabs base
    "mn_tb":       dict(label="tb", reselect=True, normalize=True, sample_weight="abslabel"),
    # Optuna-tuned LGBM hyperparams (US OOS-IC search) — validate cross-market
    # sample-treatment battery (vs mn_swabs baseline)
    "mn_uniq":      dict(label="mn", reselect=True, normalize=True, sample_weight="uniq"),
    "mn_uniqabs":   dict(label="mn", reselect=True, normalize=True, sample_weight="uniqabs"),
    # Wave-4 macro-deep interaction features (on mn_norm+blitz+swabs base)
    "mn_ltr":       dict(label="mn", reselect=True, normalize=True, model="ltr"),
    "mn_sv":        dict(label="mn", reselect=True, normalize=True, sample_weight="abslabel", shortvol=True),
    "mn_ens3":      dict(label="mn", reselect=True, normalize=True, sample_weight="abslabel", model="ens3"),
    # === A) drop-one-out ablation of the final stack ===
    # KR final = mn_swabs (mn+norm+abslabel+blitz). US final = mn_tb (tb+norm+abslabel+blitz).
    "kr_full":      dict(label="mn", reselect=True, normalize=True, sample_weight="abslabel"),
    "kr_drop_norm": dict(label="mn", reselect=True, normalize=False, sample_weight="abslabel"),
    "kr_drop_sw":   dict(label="mn", reselect=True, normalize=True, sample_weight=None),
    "kr_drop_blitz":dict(label="mn", reselect=True, normalize=True, sample_weight="abslabel", drop_prefix="resid_mom_blitz"),
    "kr_drop_mn":   dict(label="rank", reselect=True, normalize=True, sample_weight="abslabel"),
    "us_full":      dict(label="tb", reselect=True, normalize=True, sample_weight="abslabel"),
    "us_drop_norm": dict(label="tb", reselect=True, normalize=False, sample_weight="abslabel"),
    "us_drop_sw":   dict(label="tb", reselect=True, normalize=True, sample_weight=None),
    "us_drop_blitz":dict(label="tb", reselect=True, normalize=True, sample_weight="abslabel", drop_prefix="resid_mom_blitz"),
    "us_drop_tb":   dict(label="mn", reselect=True, normalize=True, sample_weight="abslabel"),
    # === B) re-test rejected levers ON TOP OF the full stack (synergy check) ===
    "kr_b_winsor":  dict(label="mn", reselect=True, normalize="winsor", sample_weight="abslabel"),
    "kr_b_md":      dict(label="mn", reselect=True, normalize=True, sample_weight="abslabel", macrodeep=True),
    "kr_b_cat":     dict(label="mn", reselect=True, normalize=True, sample_weight="abslabel", model="cat"),
    "us_b_winsor":  dict(label="tb", reselect=True, normalize="winsor", sample_weight="abslabel"),
    "us_b_md":      dict(label="tb", reselect=True, normalize=True, sample_weight="abslabel", macrodeep=True),
    "us_b_cat":     dict(label="tb", reselect=True, normalize=True, sample_weight="abslabel", model="cat"),
    "mn_md":        dict(label="mn", reselect=True, normalize=True, sample_weight="abslabel", macrodeep=True),
    "mn_md_vix":    dict(label="mn", reselect=True, normalize=True, sample_weight="abslabel", macrodeep="vix"),
    "mn_md_reg":    dict(label="mn", reselect=True, normalize=True, sample_weight="abslabel", macrodeep="reg"),
    "mn_md_macro":  dict(label="mn", reselect=True, normalize=True, sample_weight="abslabel", macrodeep="macro"),
    "mn_meta":      dict(label="mn", reselect=True, normalize=True, sample_weight="abslabel", portfolio="meta"),
    "mn_disp":      dict(label="mn", reselect=True, normalize=True, sample_weight="disp"),
    "mn_dispabs":   dict(label="mn", reselect=True, normalize=True, sample_weight="dispabs"),
    "mn_swabs_tuned": dict(label="mn", reselect=True, normalize=True, sample_weight="abslabel",
                           model_params=dict(num_leaves=26, learning_rate=0.0345, n_estimators=550,
                                             min_child_samples=195, subsample=0.72,
                                             colsample_bytree=0.60, reg_lambda=9.37)),
    # Wave 4 — GPU deep learning (same cache/walk-forward/eval bar)
    "mn_mlp":     dict(label="mn", reselect=True, model="mlp"),
    "mn_mlp_wide":dict(label="mn", reselect=True, model="mlp_wide"),
    "mn_mlp_ens": dict(label="mn", reselect=True, model="mlp_ens"),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="KR")
    ap.add_argument("--configs", default="baseline,mn,regime,ls,gate")
    ap.add_argument("--seeds", default="42", help="comma-separated seeds; >1 => mean±std")
    ap.add_argument("--step", type=int, default=21)
    ap.add_argument("--router", action="store_true", help="run the online regime router")
    args = ap.parse_args()
    seeds = [int(s) for s in args.seeds.split(",")]
    cache = Path(f"var/_bt_period_{args.market}_{PERIOD}.parquet")
    df = pd.read_parquet(cache); df["date"] = pd.to_datetime(df["date"])
    print(f"[lab] {args.market} rows={len(df):,} seeds={seeds} step={args.step}", flush=True)

    if args.router:
        strat = ["base", "router", "expo", "soft"]
        acc = {s: [] for s in strat}
        for sd in seeds:
            r = run_regime_suite(df, args.market, seed=sd, step=args.step)
            for s in strat:
                acc[s].append(r[s])
            print(f"  seed{sd}: " + "  ".join(f"{s}={r[s]:+.1f}" for s in strat) +
                  f"  chosen={r['chosen']}", flush=True)
        print(f"\n{'strategy':<10} {'alpha/yr mean±std':>20} {'min..max':>14}")
        for s in strat:
            a = np.array(acc[s])
            tag = "  (baseline)" if s == "base" else (
                "  ✓BEATS" if a.mean() > np.array(acc['base']).mean() + 1 else "  ✗")
            print(f"{s:<10} {a.mean():>10.2f} ± {a.std():>5.2f}%/yr {a.min():>6.1f}..{a.max():<5.1f}{tag}")
        return
    if len(seeds) > 1:
        print(f"{'config':<13} {'alpha/yr mean±std':>22} {'min..max':>14} {'MDD~':>7} {'IC':>8} {'IC+%':>5} {'conc5':>6}")
    else:
        print(f"{'config':<13} {'alpha/yr':>9} {'alpha_tot':>9} {'MDD':>7} {'win%':>5} {'IC':>8} {'IC+%':>5} {'conc5':>6}  vix(lo/mid/hi)")
    for name in args.configs.split(","):
        cfg = dict(CONFIGS[name.strip()]); cfg["step"] = args.step
        ays, mdds, iccs, last = [], [], [], None
        for sd in seeds:
            m = run_experiment(df, args.market, seed=sd, **cfg)
            if m:
                ays.append(m["alpha_yr"]); mdds.append(m["mdd"]); last = m
                if m.get("ic") is not None:
                    iccs.append(m["ic"])
        if not ays:
            continue
        ic_m = np.mean(iccs) if iccs else float("nan")
        c5 = last.get("conc5") if last.get("conc5") is not None else 0
        if len(seeds) > 1:
            a = np.array(ays)
            print(f"{name:<13} {a.mean():>11.2f} ± {a.std():>5.2f}%/yr {a.min():>6.1f}..{a.max():<5.1f} "
                  f"{np.mean(mdds):>6.1f}% {ic_m:>+8.4f} {last['ic_pos'] if last.get('ic_pos') is not None else 0:>4}% {c5:>5}%", flush=True)
        else:
            print(f"{name:<13} {last['alpha_yr']:>8.2f}% {last['alpha_total']:>8.1f}% {last['mdd']:>6.1f}% "
                  f"{last['win']:>4}% {ic_m:>+8.4f} {last['ic_pos'] if last.get('ic_pos') is not None else 0:>4}% {c5:>5}%  {last['vix_alpha']}", flush=True)


if __name__ == "__main__":
    main()
