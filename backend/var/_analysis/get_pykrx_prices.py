"""Kill-or-confirm integrity gate #2: re-source KR_MICRO OHLCV from pykrx
(direct KRX daily closes, adjusted) instead of yfinance, so the confirmed
IC 0.046 can be cross-validated on an INDEPENDENT price series. If the edge is
a yfinance micro-cap data artifact (stale/forward-filled closes fabricating 21d
forward returns), it will NOT replicate on pykrx.

Saves px_{}_PYKRX.parquet in the same schema as the yfinance caches, so
`wf_kr_adversarial.py KR_MICRO_PYKRX` runs the identical harness.

Usage: uv run python var/_analysis/get_pykrx_prices.py
"""
import sys, json, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import pandas as pd
from pykrx import stock

UNI = sys.argv[1] if len(sys.argv) > 1 else "KR_MICRO"
tickers = json.load(open("var/_analysis/universes.json"))[UNI]
codes = [t.split(".")[0] for t in tickers]
START, END = "20180101", "20260701"
rows, ok, fail = [], 0, 0
for i, code in enumerate(codes):
    try:
        d = stock.get_market_ohlcv(START, END, code, adjusted=True)
        if d is None or len(d) < 600:
            fail += 1; continue
        d = d.rename(columns={"시가": "open", "고가": "high", "저가": "low",
                              "종가": "close", "거래량": "volume"})
        d = d[["close", "high", "low", "volume"]].reset_index()
        d.columns = ["date", "close", "high", "low", "volume"]
        d["ticker"] = code
        d = d[d["close"] > 0]
        rows.append(d); ok += 1
    except Exception:
        fail += 1
    if (i + 1) % 50 == 0:
        print(f"  {i+1}/{len(codes)}  ok={ok} fail={fail}", flush=True)

px = pd.concat(rows, ignore_index=True)
px["date"] = pd.to_datetime(px["date"])
px.to_parquet(f"var/_analysis/px_{UNI}_PYKRX.parquet")
print(f"pykrx {UNI}: {ok} usable tickers, {len(px)} rows -> px_{UNI}_PYKRX.parquet", flush=True)
