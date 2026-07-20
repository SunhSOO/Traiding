import sys
sys.path.insert(0,'.')
import pandas as pd, numpy as np, lightgbm as lgb
from core.db import session_scope
from core.types import Market
from decision.integrated_runner import ENTRY_MIN
from scripts.train_lgbm import ALL_FEATURE_COLS
from scripts.backtest_integrated import _tech_signals_by_trade_date

RET='ret_fwd_21d'; EMBARGO=21; step=42; dec=0.1; band=0.02; train_window=756
sig_names=["momentum_rsi_macd","trend_ema_alignment","mean_reversion_zscore","red_green","composite"]

for mkt in ['US']:
    market=Market(mkt)
    df=pd.read_parquet(f'var/_bt_period_{mkt}_2018-01-01_2024-01-01.parquet')
    df['date']=pd.to_datetime(df['date'])
    dates=np.sort(df['date'].unique())
    feats=[c for c in ALL_FEATURE_COLS if c in df.columns]
    # production label US=tb
    df['mn_fwd_21d']=df[RET]-df.groupby('date')[RET].transform('mean')
    from scripts.alpha_lab import _tb_label,_close_panel
    px=_close_panel(mkt,df['date'].min().date(),df['date'].max().date())
    df['tbmn']=_tb_label(df,px); target='tbmn'
    df=df.dropna(subset=[target]).copy()
    g=df.groupby('date'); df[feats]=(df[feats]-g[feats].transform('mean'))/(g[feats].transform('std')+1e-9)
    params=dict(n_estimators=300,num_leaves=31,learning_rate=0.03,min_child_samples=100,subsample=0.7,colsample_bytree=0.6,reg_lambda=5.0,verbose=-1,n_jobs=-1)
    reb=list(range(EMBARGO+252,len(dates),step))
    wins=[]
    for i in reb:
        t=dates[i]; tc=dates[i-EMBARGO]; lo=dates[max(0,i-EMBARGO-train_window)]
        tr=df[(df['date']<=tc)&(df['date']>lo)].dropna(subset=[target])
        at=df[df['date']==t].dropna(subset=[RET]).copy()
        if len(tr)<5000 or len(at)<30: continue
        sw=np.abs(tr[target].values)
        sel=lgb.LGBMRegressor(**params).fit(tr[feats].astype(float),tr[target].astype(float),sample_weight=sw)
        cols=pd.Series(sel.feature_importances_,index=feats).sort_values(ascending=False).head(50).index.tolist()
        model=lgb.LGBMRegressor(**params).fit(tr[cols].astype(float),tr[target].astype(float),sample_weight=sw)
        at['score']=model.predict(at[cols].astype(float)); at['rank_pct']=at['score'].rank(pct=True)
        bench_r=float(at[RET].mean()); basket=at[at['rank_pct']>=1-dec]; n_b=max(len(basket),1)
        td=pd.Timestamp(t).date()
        passed={s:[] for s in sig_names}; cnt={s:0 for s in sig_names}
        with session_scope() as s:
            for tkr,r in zip(basket['ticker'].astype(str),basket[RET].astype(float)):
                sc=_tech_signals_by_trade_date(s,market,tkr,td)
                if sc is None: continue
                for sig in sig_names:
                    if sc.get(sig,0.0)>=ENTRY_MIN:
                        passed[sig].append(float(r)); cnt[sig]+=1
        wins.append((bench_r,{sig:(sum(passed[sig])/n_b) for sig in sig_names},{sig:cnt[sig]/n_b for sig in sig_names}))
    dn=[w for w in wins if w[0]<-band]
    print(f'\n{mkt} down-windows n={len(dn)} (of {len(wins)}) bench={np.mean([w[0] for w in dn])*100:+.2f}%')
    print(f'{"signal":<18}{"cash/win":>10}{"inv_frac":>10}{"ret|invested":>14}')
    for sig in sig_names:
        cash=np.mean([w[1][sig] for w in dn]); inv=np.mean([w[2][sig] for w in dn])
        # per-invested-name mean return (undo the /n_b exposure effect)
        rii=np.mean([ (w[1][sig]/w[2][sig] if w[2][sig]>0 else 0.0) for w in dn])
        print(f'{sig:<18}{cash*100:>+9.2f}%{inv*100:>9.0f}%{rii*100:>+13.2f}%')
