"""Fast single-split EXCESS check: train ≤2022-06, test 2022-06..2023-06.
Per-date top-decile return MINUS universe mean (excess = skill beyond market
beta), for lgbm / ridge / lgbm+ridge ensemble. Settles whether ridge/ens's
higher IC translates to money once beta is removed. US + KR."""
import sys; sys.path.insert(0, ".")
import numpy as np, pandas as pd, lightgbm as lgb
from sklearn.linear_model import Ridge
from scripts.train_lgbm import ALL_FEATURE_COLS

TGT, RET = "rank_fwd_21d", "ret_fwd_21d"

for MARKET in ("US", "KR"):
    df = pd.read_parquet(f"var/_bt_period_{MARKET}_2018-01-01_2024-01-01.parquet")
    df["date"] = pd.to_datetime(df["date"])
    feats = [c for c in ALL_FEATURE_COLS if c in df.columns]
    tr = df[df["date"] <= "2022-06-01"].dropna(subset=[TGT])
    te = df[(df["date"] > "2022-06-01") & (df["date"] <= "2023-06-01")].dropna(subset=[RET]).reset_index(drop=True)
    sw = np.abs(tr[TGT].values - 0.5)
    Xtr, ytr, Xte = tr[feats].astype(float), tr[TGT].astype(float), te[feats].astype(float)
    sc = {}
    sc["lgbm"] = lgb.LGBMRegressor(n_estimators=500, learning_rate=0.03, num_leaves=31,
                                   n_jobs=-1, verbose=-1).fit(Xtr, ytr, sample_weight=sw).predict(Xte)
    Xtr_c, Xte_c = Xtr.replace([np.inf,-np.inf], np.nan), Xte.replace([np.inf,-np.inf], np.nan)
    med = Xtr_c.median(); mu = Xtr_c.fillna(med).mean(); sd = Xtr_c.fillna(med).std()+1e-9
    Ztr = ((Xtr_c.fillna(med)-mu)/sd).clip(-10,10).fillna(0.0)
    Zte = ((Xte_c.fillna(med)-mu)/sd).clip(-10,10).fillna(0.0)
    sc["ridge"] = Ridge(alpha=10.0).fit(Ztr, ytr, sample_weight=sw).predict(Zte)
    sc["ens"] = pd.Series(sc["lgbm"]).rank().values + pd.Series(sc["ridge"]).rank().values

    te2 = te.copy(); te2["_y"] = pd.to_numeric(te2[RET], errors="coerce")
    print(f"\n===== {MARKET}  test 2022-06..2023-06 =====", flush=True)
    print(f"{'model':6s} {'meanExcess21d':>13s} {'ExcessSharpe':>13s} {'Excess>0%':>10s}  (excess=decile-benchmark)")
    for name in ("lgbm", "ridge", "ens"):
        te2["s"] = sc[name]
        exc = []
        for _, g in te2.groupby("date"):
            gg = g.dropna(subset=["s", "_y"])
            if len(gg) < 20: continue
            top = gg[gg["s"] >= gg["s"].quantile(0.9)]["_y"]
            exc.append(top.mean() - gg["_y"].mean())
        e = np.array([v for v in exc if pd.notna(v)])
        esh = e.mean()/e.std()*np.sqrt(252/21) if e.std() > 0 else 0
        print(f"{name:6s} {e.mean()*100:+11.3f}% {esh:+12.2f} {(e>0).mean():9.0%}", flush=True)
