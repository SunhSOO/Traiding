"""Fetch free membership lists for a cap-spectrum of testable universes, to test
the thesis that selection alpha lives in LESS-efficient (smaller-cap) universes.
Saves ticker lists to var/_analysis/universes.json.

US: S&P 500 / 400(mid) / 600(small) from Wikipedia (UA header fixes 403).
KR: KOSPI+KOSDAQ full listing (FDR, has Marcap) sliced by market-cap rank into
    large(1-200) / mid(200-500) / small(500-1000). yfinance suffix .KS/.KQ.
"""
import sys, json, io, urllib.request
sys.path.insert(0, ".")
import pandas as pd
import FinanceDataReader as fdr

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) research"}
out = {}


def wiki_tickers(url, sym_col_candidates):
    req = urllib.request.Request(url, headers=UA)
    html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
    tables = pd.read_html(io.StringIO(html))
    for t in tables:
        for c in t.columns:
            if any(k.lower() in str(c).lower() for k in sym_col_candidates):
                syms = [str(x).strip().replace(".", "-") for x in t[c].dropna()]
                syms = [s for s in syms if 1 <= len(s) <= 6 and s.isupper() and s.isalpha()]
                if len(syms) > 100:
                    return sorted(set(syms))
    return []


# ── US ──────────────────────────────────────────────────────────────────
try:
    out["US_LARGE"] = wiki_tickers("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", ["symbol", "ticker"])
except Exception as e:
    print("US500 err", str(e)[:80]); out["US_LARGE"] = []
try:
    out["US_MID"] = wiki_tickers("https://en.wikipedia.org/wiki/List_of_S%26P_400_companies", ["symbol", "ticker"])
except Exception as e:
    print("US400 err", str(e)[:80]); out["US_MID"] = []
try:
    out["US_SMALL"] = wiki_tickers("https://en.wikipedia.org/wiki/List_of_S%26P_600_companies", ["symbol", "ticker"])
except Exception as e:
    print("US600 err", str(e)[:80]); out["US_SMALL"] = []

# ── KR: FDR marcap slices ─────────────────────────────────────────────────
try:
    kp = fdr.StockListing("KOSPI"); kq = fdr.StockListing("KOSDAQ")
    allkr = pd.concat([kp.assign(_mkt="KS"), kq.assign(_mkt="KQ")], ignore_index=True)
    allkr = allkr.dropna(subset=["Marcap"]).copy()
    # exclude non-common (SPAC/ETF/pref) heuristically by Name suffix where possible
    allkr = allkr[~allkr["Name"].astype(str).str.contains("스팩|ETF|리츠|우$|우B$", regex=True, na=False)]
    allkr = allkr.sort_values("Marcap", ascending=False).reset_index(drop=True)
    allkr["yf"] = allkr["Code"].astype(str).str.zfill(6) + "." + allkr["_mkt"]
    def sl(a, b): return allkr.iloc[a:b]["yf"].tolist()
    out["KR_LARGE"] = sl(0, 200)      # ~KOSPI200/대형
    out["KR_MID"] = sl(200, 500)
    out["KR_SMALL"] = sl(500, 1000)
except Exception as e:
    print("KR err", str(e)[:120]); out["KR_LARGE"] = out["KR_MID"] = out["KR_SMALL"] = []

with open("var/_analysis/universes.json", "w") as f:
    json.dump(out, f)
print("\n=== universe membership counts ===")
for k, v in out.items():
    print(f"  {k:10s} {len(v):5d}  e.g. {v[:5]}")
