"""Sequence-model lab — cross-sectional LSTM on the SAME cache/label/eval bar.

MLP (point-in-time cross-section) already lost to GBDT. The remaining deep lever
is a SEQUENCE model that sees each stock's recent *trajectory* (L days of the
top-K features) instead of one snapshot. We test whether temporal structure adds
selection skill (OOS rank-IC) over GBDT+normalize (mn_norm).

Design (no look-ahead):
  * per-date cross-sectional z-score (PIT safe, same-date only) — the validated prep.
  * top-K features selected ONCE on the initial window (sequence-building needs a
    fixed feature set; noted as a small deviation from per-rebalance reselect).
  * expanding walk-forward, step=42, 21d embargo. Each rebalance: train an LSTM on
    sequences ending at any date <= cut, predict the cross-section at R, rank ->
    top-decile long vs equal-weight bench. Report alpha/yr, IC, IC+%, conc5, vix.

Usage: uv run python scripts/seq_lab.py --market US --seeds 42,1 --epochs 10
"""
from __future__ import annotations
import argparse, sys
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
SEL = dict(n_estimators=300, num_leaves=31, learning_rate=0.04, min_child_samples=100,
           subsample=0.7, colsample_bytree=0.6, reg_lambda=5.0, verbose=-1, n_jobs=-1)


def _close_panel(market, lo, hi):
    with session_scope() as s:
        rows = s.execute(text("SELECT trade_date,ticker,close FROM daily_prices "
                              "WHERE market=:m AND trade_date BETWEEN :a AND :b"),
                         {"m": market, "a": lo, "b": hi}).all()
    p = pd.DataFrame(rows, columns=["date", "ticker", "close"]); p["date"] = pd.to_datetime(p["date"])
    return p.pivot_table(index="date", columns="ticker", values="close", aggfunc="first")


def _train_lstm(Xtr, ytr, Xte, seed, *, epochs, hidden=48, batch=1024, lr=1e-3):
    """Xtr:(n,L,K) ytr:(n,) Xte:(m,L,K) -> preds:(m,). CPU threads capped."""
    import torch, torch.nn as nn
    torch.manual_seed(seed); np.random.seed(seed); torch.set_num_threads(2)
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    K = Xtr.shape[2]

    class Net(nn.Module):
        def __init__(self):
            super().__init__()
            self.lstm = nn.LSTM(K, hidden, batch_first=True)
            self.head = nn.Sequential(nn.Linear(hidden, 32), nn.ReLU(), nn.Linear(32, 1))

        def forward(self, x):
            o, _ = self.lstm(x)
            return self.head(o[:, -1, :])

    net = Net().to(dev)
    opt = torch.optim.Adam(net.parameters(), lr=lr, weight_decay=1e-5)
    lossf = nn.MSELoss()
    y = (ytr - ytr.mean()) / (ytr.std() + 1e-9)
    xt = torch.tensor(Xtr, dtype=torch.float32)
    yt = torch.tensor(y, dtype=torch.float32, device=dev).view(-1, 1)
    n = xt.shape[0]
    net.train()
    for _ in range(epochs):
        perm = torch.randperm(n)
        for k in range(0, n, batch):
            idx = perm[k:k + batch]
            if idx.numel() < 2:
                continue
            xb = xt[idx].to(dev)
            opt.zero_grad(); loss = lossf(net(xb), yt[idx]); loss.backward(); opt.step()
    net.eval()
    with torch.no_grad():
        preds = []
        xe = torch.tensor(Xte, dtype=torch.float32)
        for k in range(0, xe.shape[0], 4096):
            preds.append(net(xe[k:k + 4096].to(dev)).cpu().numpy().ravel())
    return np.concatenate(preds) if preds else np.array([])


def run(market, *, seed, L=20, step=42, topk=40, epochs=10, cap=70000):
    cost = 0.003 if market == "KR" else 0.001
    df = pd.read_parquet(Path(f"var/_bt_period_{market}_{PERIOD}.parquet"))
    df["date"] = pd.to_datetime(df["date"])
    feats = [c for c in ALL_FEATURE_COLS if c in df.columns]
    df["mn"] = df["ret_fwd_21d"] - df.groupby("date")["ret_fwd_21d"].transform("mean")
    g = df.groupby("date")
    z = (df[feats] - g[feats].transform("mean")) / (g[feats].transform("std") + 1e-9)
    z.columns = [c + "_z" for c in feats]; df = pd.concat([df, z], axis=1); zf = list(z.columns)

    dates = np.sort(df["date"].unique())
    di = {d: i for i, d in enumerate(dates)}
    tickers = np.sort(df["ticker"].unique()); ti = {t: i for i, t in enumerate(tickers)}
    T, N, K = len(dates), len(tickers), topk
    px = _close_panel(market, pd.Timestamp(dates[0]).date(), pd.Timestamp(dates[-1]).date())

    # top-K on the initial window
    init_cut = dates[max(0, 63 - 21)]
    tr0 = df[df["date"] <= init_cut].dropna(subset=["mn"])
    sel = lgb.LGBMRegressor(**{**SEL, "random_state": seed}).fit(tr0[zf].astype(float), tr0["mn"].astype(float))
    top = pd.Series(sel.feature_importances_, index=zf).sort_values(ascending=False).head(topk).index.tolist()

    # dense tensors X:(T,N,K) Y:(T,N)
    X = np.full((T, N, K), np.nan, dtype=np.float32)
    Y = np.full((T, N), np.nan, dtype=np.float32)
    di_arr = df["date"].map(di).values; ti_arr = df["ticker"].map(ti).values
    for j, f in enumerate(top):
        X[di_arr, ti_arr, j] = df[f].astype(np.float32).values
    Y[di_arr, ti_arr] = df["mn"].astype(np.float32).values
    Xf = np.nan_to_num(X, nan=0.0)

    def seqs(end_idxs, tick_idxs):
        out = np.empty((len(end_idxs), L, K), dtype=np.float32)
        for q, (e, n) in enumerate(zip(end_idxs, tick_idxs)):
            out[q] = Xf[e - L + 1:e + 1, n, :]
        return out

    rebal = list(range(max(63, L), len(dates) - 1, step))
    cash = bench = 1.0; log = []; ics = []
    for jj, i in enumerate(rebal):
        R = dates[i]; E = dates[rebal[jj + 1]] if jj + 1 < len(rebal) else dates[-1]
        cut_i = max(0, i - 21)
        # training pairs: end index e in [L-1, cut_i], label finite
        es, ns = np.where(np.isfinite(Y[L - 1:cut_i + 1, :]))
        es = es + (L - 1)
        if len(es) > cap:
            sub = np.random.RandomState(seed).choice(len(es), cap, replace=False)
            es, ns = es[sub], ns[sub]
        if len(es) < 1000:
            continue
        Xtr = seqs(es, ns); ytr = Y[es, ns]
        # test cross-section at R
        te_n = np.where(np.isfinite(Y[i, :]))[0]
        if len(te_n) < 20:
            continue
        Xte = seqs(np.full(len(te_n), i), te_n)
        pred = _train_lstm(Xtr, ytr, Xte, seed, epochs=epochs)
        atR_t = tickers[te_n]
        score = pd.Series(pred, index=atR_t)
        pct = score.rank(pct=True)

        def ret(tks):
            r = [float(px.at[E, t]) / float(px.at[R, t]) - 1 for t in tks
                 if t in px.columns and R in px.index and E in px.index
                 and pd.notna(px.at[R, t]) and pd.notna(px.at[E, t]) and px.at[R, t] > 0]
            return float(np.mean(r)) if r else 0.0
        longs = pct[pct >= 0.9].index.tolist()
        port = ret(longs) - cost; allr = ret(list(px.columns))
        # IC vs realized fwd
        if R in px.index and E in px.index:
            fr = (px.loc[E] / px.loc[R] - 1)
            frv = pd.to_numeric(score.index.map(fr), errors="coerce").to_numpy(float)
            sv = score.to_numpy(float); m = np.isfinite(frv) & np.isfinite(sv)
            if m.sum() > 10:
                ics.append(spearmanr(sv[m], frv[m]).correlation)
        vp = float(df[df["date"] == R]["vix_pctile_252d"].iloc[0]) if "vix_pctile_252d" in df.columns else np.nan
        cash *= (1 + port); bench *= (1 + allr); log.append((port, allr, vp))

    L_ = pd.DataFrame(log, columns=["port", "bench", "vix"])
    if L_.empty:
        return {}
    ny = (pd.Timestamp(dates[-1]) - pd.Timestamp(dates[rebal[0]])).days / 365.25
    fa = (L_["port"] - L_["bench"]).values
    ic = np.array([x for x in ics if np.isfinite(x)])
    vb = {}
    for nm, msk in [("lo", L_.vix < 0.4), ("mid", (L_.vix >= 0.4) & (L_.vix < 0.7)), ("hi", L_.vix >= 0.7)]:
        s = L_[msk]; vb[nm] = round((s.port - s.bench).mean() * 100, 2) if len(s) else None
    return {"alpha_yr": round((cash - bench) / ny * 100, 2),
            "ic": round(float(ic.mean()), 4) if len(ic) else None,
            "ic_pos": round(float((ic > 0).mean()) * 100) if len(ic) else None,
            "conc5": round(float(np.sort(fa)[-5:].sum() / fa.sum() * 100)) if abs(fa.sum()) > 1e-9 else None,
            "vix": vb, "n": len(L_)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="US")
    ap.add_argument("--seeds", default="42")
    ap.add_argument("--epochs", type=int, default=10)
    ap.add_argument("--step", type=int, default=42)
    a = ap.parse_args()
    seeds = [int(s) for s in a.seeds.split(",")]
    print(f"[seq] {a.market} LSTM seeds={seeds} epochs={a.epochs} step={a.step}", flush=True)
    ays, ics = [], []
    for sd in seeds:
        m = run(a.market, seed=sd, step=a.step, epochs=a.epochs)
        if m:
            ays.append(m["alpha_yr"]); ics.append(m["ic"] if m["ic"] is not None else np.nan)
            print(f"  seed{sd}: alpha {m['alpha_yr']:+.2f}%/yr  IC {m['ic']:+.4f} "
                  f"IC+ {m['ic_pos']}%  conc5 {m['conc5']}%  vix {m['vix']}  n={m['n']}", flush=True)
    if ays:
        print(f"\nLSTM {a.market}: alpha {np.mean(ays):+.2f} ± {np.std(ays):.2f}%/yr  "
              f"IC {np.nanmean(ics):+.4f}   (vs mn_norm GBDT US~+14/KR~+9, IC~0.025/0.005)", flush=True)


if __name__ == "__main__":
    main()
