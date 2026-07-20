"""Survivorship gate for liquid KR: FDR KRX-DELISTING gives delisted tickers +
DelistingDate. Quantify the delisting rate in the 2018-2026 window (bias
magnitude) and check whether pykrx serves OHLCV for delisted codes (=> whether a
full point-in-time re-run carrying delisted names at terminal loss is feasible).

Usage: uv run python var/_analysis/kr_survivorship.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import pandas as pd, numpy as np
import FinanceDataReader as fdr
from pykrx import stock

dl = fdr.StockListing("KRX-DELISTING")
dl["DelistingDate"] = pd.to_datetime(dl["DelistingDate"], errors="coerce")
dl["ListingDate"] = pd.to_datetime(dl["ListingDate"], errors="coerce")
# common KOSPI/KOSDAQ equities delisted within the backtest window
win = dl[(dl["DelistingDate"] >= "2018-01-01") & (dl["DelistingDate"] <= "2026-07-01")].copy()
win = win[win["Market"].isin(["KOSPI", "KOSDAQ"])]
# rough common-stock filter (exclude SPAC/pref/ETF/REIT by name)
win = win[~win["Name"].astype(str).str.contains("스팩|우$|우B|ETF|리츠", regex=True, na=False)]
print(f"KR common delistings 2018-2026: {len(win)}  ({win['Market'].value_counts().to_dict()})")
by_yr = win.groupby(win["DelistingDate"].dt.year).size()
print("per-year:", by_yr.to_dict())
# a ~500-name small/mid universe over 8yr vs total listed ~2800: delisting exposure
n_listed = len(fdr.StockListing("KRX"))
print(f"currently listed KRX ~{n_listed}; {len(win)} delisted over 8yr "
      f"=> ~{len(win)/8/ n_listed*100:.1f}%/yr universe-wide delisting rate")

# can pykrx serve OHLCV for a delisted code? (=> full PIT re-run feasibility)
feasible = 0
for code in win["Symbol"].astype(str).str.zfill(6).head(8):
    try:
        d = stock.get_market_ohlcv("20180101", "20260101", code, adjusted=True)
        if d is not None and len(d) > 100:
            feasible += 1
    except Exception:
        pass
print(f"pykrx OHLCV available for {feasible}/8 sampled delisted codes "
      f"=> full PIT re-run {'FEASIBLE' if feasible >= 5 else 'partial/blocked'}")
print("bias direction: current-membership universe EXCLUDES these delisted names (mostly -90%~-100% terminal); "
      "the liquid mid/large edge is LESS exposed than micro (delistings concentrate in the junk tail), but "
      "the delisting rate above bounds the residual survivorship inflation.")
