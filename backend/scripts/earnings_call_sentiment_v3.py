"""Earnings call sentiment v3 — EX-99.1 본문 fetch + cap 해제 + 재계산.

**중대 수정** vs v2:
  - v2까지는 8-K primary document만 fetch했는데, 이건 3KB cover sheet 임.
    실제 earnings press release는 EX-99.1 첨부파일 (50-150KB)에 있음.
  - v3는 filing index.json을 파싱해서 ex99 패턴 파일을 찾아 fetch.

매칭 logic:
  1. submissions JSON에서 8-K + items=2.02 식별 (v2와 동일)
  2. filing index.json fetch (해당 accession 디렉토리)
  3. directory.item[] 중 이름이 'ex99' 또는 'ex-99' 또는 'ex_99' 패턴 매칭
  4. 가장 큰 ex99 파일 fetch (보통 .htm)
  5. HTML strip → FinBERT + LM 점수
  6. 기존 row UPDATE (재계산)

CAP: max-per-ticker=0 → 모든 매치 처리.

Usage:
    uv run python scripts/earnings_call_sentiment_v3.py --limit-tickers 50
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, text
from core.config import get_settings
from core.db import get_engine, session_scope
from core.models.universe import Security


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS earnings_call_sentiment (
    market                   VARCHAR(8) NOT NULL,
    ticker                   VARCHAR(16) NOT NULL,
    filing_date              DATE NOT NULL,
    transcript_sent_finbert  DOUBLE PRECISION,
    transcript_sent_lm       DOUBLE PRECISION,
    transcript_pos_words     INTEGER,
    transcript_neg_words     INTEGER,
    word_count               INTEGER,
    fetch_source             VARCHAR(32),   -- 'primary' (v2) or 'ex991' (v3)
    as_of_ts                 TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (market, ticker, filing_date)
);
ALTER TABLE earnings_call_sentiment
    ADD COLUMN IF NOT EXISTS fetch_source VARCHAR(32),
    ADD COLUMN IF NOT EXISTS sliding_sent_mean DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS sliding_sent_max DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS sliding_sent_min DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS sliding_sent_std DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS sliding_peak_pos_loc DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS sliding_peak_neg_loc DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS sliding_n_windows INTEGER;
"""


_EX99_RX = re.compile(r"^(?:.*[^a-z])?ex[-_]?99[._-]?(\d+)?\.html?$|.*ex99.*\.html?$",
                        re.IGNORECASE)


def fetch_submissions(cik: str, ua: str) -> dict | None:
    cik_padded = cik.zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
    try:
        r = httpx.get(url, headers={"User-Agent": ua,
                                       "Accept": "application/json"}, timeout=20)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def find_ex991_file(cik: str, accession: str, ua: str) -> str | None:
    """Return the EX-99.1 press-release file name.

    Heuristic (paranoid since filename patterns vary widely):
      1. Skip index pages, R-X.htm (XBRL viewer), <ticker>-<YYYYMMDD>.htm (cover)
      2. Among remaining .htm files, pick the largest by size.
      3. If filename contains "ex99" / "991" / "earnings" / "press" / "release"
         → boost priority, but size still tiebreaker
    """
    cik_int = str(int(cik))
    acc_clean = accession.replace("-", "")
    idx_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_clean}/index.json"
    try:
        r = httpx.get(idx_url, headers={"User-Agent": ua}, timeout=20)
        if r.status_code != 200:
            return None
        data = r.json()
    except Exception:
        return None
    items = data.get("directory", {}).get("item", [])
    candidates = []
    for item in items:
        name = item.get("name", "")
        size_raw = item.get("size") or ""
        try:
            size = int(size_raw) if size_raw else 0
        except (ValueError, TypeError):
            size = 0
        nl = name.lower()
        if not nl.endswith((".htm", ".html")):
            continue
        # Reject patterns
        if nl.startswith("0") and ("index" in nl):
            continue
        if re.match(r"^r\d+\.htm$", nl):    # XBRL R1.htm, R2.htm...
            continue
        # Cover doc: <ticker>-YYYYMMDD.htm (8 digit date)
        if re.match(r"^[a-z0-9]+-\d{8}\.htm$", nl):
            continue
        # Boost priority for ex99-like names
        boost = 0
        for kw in ("ex99", "ex-99", "ex_99", "991",
                     "earnings", "press", "release", "exhibit"):
            if kw in nl:
                boost = 1
                break
        candidates.append((boost, size, name))
    if not candidates:
        return None
    # Sort: priority first (boost), then size
    candidates.sort(reverse=True)
    return candidates[0][2]


def fetch_doc_text(cik: str, accession: str, doc_name: str, ua: str) -> str:
    cik_int = str(int(cik))
    acc_clean = accession.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_clean}/{doc_name}"
    try:
        r = httpx.get(url, headers={"User-Agent": ua}, timeout=60)
        r.raise_for_status()
    except Exception:
        return ""
    raw = r.text
    clean = re.sub(r"<[^>]+>", " ", raw)
    clean = re.sub(r"&[a-z]+;", " ", clean)
    return re.sub(r"\s+", " ", clean).lower()


def compute_lm_sentiment(body: str, lm: dict) -> tuple[float, int, int, int]:
    words = re.findall(r"[a-z]+", body)
    if not words:
        return float("nan"), 0, 0, 0
    pos = sum(1 for w in words if w in lm["positive"])
    neg = sum(1 for w in words if w in lm["negative"])
    return (pos - neg) / len(words), pos, neg, len(words)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit-tickers", type=int, default=0,
                    help="0 = all tickers (no cap)")
    ap.add_argument("--max-per-ticker", type=int, default=0,
                    help="0 = no cap on filings per ticker")
    ap.add_argument("--tickers", default="")
    ap.add_argument("--force-redo", action="store_true",
                    help="Recompute even if fetch_source='ex991' already")
    args = ap.parse_args()

    settings = get_settings()
    ua = str(getattr(settings, "sec_user_agent", None) or "research-bot research@example.com")

    eng = get_engine()
    with eng.begin() as conn:
        for stmt in CREATE_TABLE_SQL.strip().split(";"):
            if stmt.strip():
                conn.execute(text(stmt))

    # LM dict
    import pysentiment2, pandas as pd, os
    csv_path = os.path.join(os.path.dirname(pysentiment2.__file__), "static", "LM.csv")
    lm_df = pd.read_csv(csv_path)
    lm = {
        "positive": set(lm_df.loc[lm_df["Positive"] > 0, "Word"].str.lower().tolist()),
        "negative": set(lm_df.loc[lm_df["Negative"] > 0, "Word"].str.lower().tolist()),
    }

    # FinBERT GPU
    import torch
    from transformers import AutoTokenizer, AutoModelForSequenceClassification
    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32
    tokenizer = AutoTokenizer.from_pretrained("ProsusAI/finbert")
    model = AutoModelForSequenceClassification.from_pretrained("ProsusAI/finbert").to(device=device, dtype=dtype)
    model.eval()
    print(f"[v3] device={device} dtype={dtype} | LM: +{len(lm['positive'])} -{len(lm['negative'])}")

    # Tickers + CIK
    with session_scope() as s:
        q = select(Security.ticker, Security.cik).where(
            Security.market == "US", Security.cik.isnot(None)
        )
        if args.tickers:
            tickers_list = [t.strip().upper() for t in args.tickers.split(",")]
            q = q.where(Security.ticker.in_(tickers_list))
        rows = list(s.execute(q).all())
    if args.limit_tickers > 0:
        rows = rows[:args.limit_tickers]
    print(f"[v3] Processing {len(rows)} tickers...")

    # Existing rows
    with session_scope() as s:
        existing_rows = {(r[0], r[1]): (r[2] or "") for r in s.execute(text(
            "SELECT ticker, filing_date, fetch_source FROM earnings_call_sentiment"
        )).all()}

    n_scanned = 0
    n_items_202 = 0
    n_ex991_found = 0
    n_ex991_missing = 0
    n_scored_new = 0
    n_skip_existing = 0
    n_fetch_fail = 0

    def score_text(body: str) -> dict:
        if len(body) < 500:
            return None
        lm_sent, pos, neg, wc = compute_lm_sentiment(body, lm)

        # Single-window (first 3000 chars) FinBERT
        finbert_sent = float("nan")
        try:
            inputs = tokenizer([body[:3000]], padding=True, truncation=True,
                                max_length=512, return_tensors="pt").to(device)
            with torch.no_grad():
                logits = model(**inputs).logits
            probs = torch.softmax(logits.float(), dim=-1).cpu().numpy()[0]
            finbert_sent = float(probs[0] - probs[1])
        except Exception:
            pass

        # Sliding-window FinBERT on full body
        sliding = {"mean": None, "max": None, "min": None, "std": None,
                    "peak_pos_loc": None, "peak_neg_loc": None, "n_windows": None}
        try:
            encoded = tokenizer(body, return_tensors=None, truncation=False,
                                  add_special_tokens=False)["input_ids"]
            window_size, stride = 480, 416
            windows = []
            if len(encoded) <= window_size:
                windows = [encoded]
            else:
                for start in range(0, len(encoded), stride):
                    w = encoded[start: start + window_size]
                    if len(w) < 50:
                        break
                    windows.append(w)
            if windows:
                texts = [tokenizer.decode(w, skip_special_tokens=True) for w in windows]
                nets = []
                BATCH = 32
                for i in range(0, len(texts), BATCH):
                    chunk = texts[i:i + BATCH]
                    inp = tokenizer(chunk, padding=True, truncation=True,
                                      max_length=512, return_tensors="pt").to(device)
                    with torch.no_grad():
                        lg = model(**inp).logits
                    pr = torch.softmax(lg.float(), dim=-1).cpu().numpy()
                    nets.extend((pr[:, 0] - pr[:, 1]).tolist())
                if nets:
                    n = len(nets)
                    mn = sum(nets) / n
                    var = sum((x - mn) ** 2 for x in nets) / n
                    mx_idx = max(range(n), key=lambda i: nets[i])
                    mn_idx = min(range(n), key=lambda i: nets[i])
                    sliding = {
                        "mean": mn, "max": max(nets), "min": min(nets),
                        "std": var ** 0.5,
                        "peak_pos_loc": mx_idx / max(1, n - 1),
                        "peak_neg_loc": mn_idx / max(1, n - 1),
                        "n_windows": n,
                    }
        except Exception:
            pass

        return {"finbert": finbert_sent, "lm": lm_sent, "pos": pos, "neg": neg,
                "wc": wc, **{f"sliding_{k}": v for k, v in sliding.items()}}

    for ticker, cik in rows:
        subs = fetch_submissions(cik, ua)
        time.sleep(0.15)
        if not subs:
            continue
        recent = subs.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accs = recent.get("accessionNumber", [])
        items = recent.get("items", [])

        matches = []
        for f, d, a, it in zip(forms, dates, accs, items):
            n_scanned += 1
            if f.startswith("8-K") and ("2.02" in (it or "")):
                n_items_202 += 1
                matches.append((d, a))
        if args.max_per_ticker > 0:
            matches = matches[:args.max_per_ticker]

        for filing_date, acc in matches:
            try:
                fd = datetime.strptime(filing_date, "%Y-%m-%d").date()
            except ValueError:
                continue
            existing_src = existing_rows.get((ticker, fd))
            if existing_src == "ex991" and not args.force_redo:
                n_skip_existing += 1
                continue

            ex_doc = find_ex991_file(cik, acc, ua)
            time.sleep(0.15)
            if not ex_doc:
                n_ex991_missing += 1
                continue
            n_ex991_found += 1
            body = fetch_doc_text(cik, acc, ex_doc, ua)
            time.sleep(0.15)
            if len(body) < 500:
                n_fetch_fail += 1
                continue
            scored = score_text(body)
            if not scored:
                continue

            with eng.begin() as conn:
                conn.execute(text("""
                    INSERT INTO earnings_call_sentiment
                    (market, ticker, filing_date, transcript_sent_finbert,
                     transcript_sent_lm, transcript_pos_words, transcript_neg_words,
                     word_count, fetch_source,
                     sliding_sent_mean, sliding_sent_max, sliding_sent_min,
                     sliding_sent_std, sliding_peak_pos_loc, sliding_peak_neg_loc,
                     sliding_n_windows, as_of_ts)
                    VALUES ('US', :ticker, :fd, :fb, :lm, :pos, :neg, :wc, 'ex991',
                            :sm, :smx, :smn, :ssd, :spp, :spn, :snw, :ats)
                    ON CONFLICT (market, ticker, filing_date) DO UPDATE SET
                        transcript_sent_finbert = EXCLUDED.transcript_sent_finbert,
                        transcript_sent_lm = EXCLUDED.transcript_sent_lm,
                        transcript_pos_words = EXCLUDED.transcript_pos_words,
                        transcript_neg_words = EXCLUDED.transcript_neg_words,
                        word_count = EXCLUDED.word_count,
                        fetch_source = 'ex991',
                        sliding_sent_mean = EXCLUDED.sliding_sent_mean,
                        sliding_sent_max = EXCLUDED.sliding_sent_max,
                        sliding_sent_min = EXCLUDED.sliding_sent_min,
                        sliding_sent_std = EXCLUDED.sliding_sent_std,
                        sliding_peak_pos_loc = EXCLUDED.sliding_peak_pos_loc,
                        sliding_peak_neg_loc = EXCLUDED.sliding_peak_neg_loc,
                        sliding_n_windows = EXCLUDED.sliding_n_windows,
                        as_of_ts = EXCLUDED.as_of_ts
                """), {
                    "ticker": ticker, "fd": fd,
                    "fb": scored["finbert"], "lm": scored["lm"],
                    "pos": scored["pos"], "neg": scored["neg"], "wc": scored["wc"],
                    "sm": scored["sliding_mean"], "smx": scored["sliding_max"],
                    "smn": scored["sliding_min"], "ssd": scored["sliding_std"],
                    "spp": scored["sliding_peak_pos_loc"],
                    "spn": scored["sliding_peak_neg_loc"],
                    "snw": scored["sliding_n_windows"],
                    "ats": datetime.now(timezone.utc),
                })
            n_scored_new += 1

        if n_scored_new > 0 and n_scored_new % 25 == 0:
            print(f"  scan={n_scanned:,} items={n_items_202:,} "
                  f"ex991_ok={n_ex991_found:,} missing={n_ex991_missing:,} "
                  f"scored={n_scored_new:,} skip={n_skip_existing:,}", flush=True)

    print(f"\n=== v3 Summary ===")
    print(f"  Tickers processed         : {len(rows)}")
    print(f"  Total 8-K-ish scanned     : {n_scanned:,}")
    print(f"  Item 2.02 matches         : {n_items_202:,}")
    print(f"  EX-99.1 file found        : {n_ex991_found:,}")
    print(f"  EX-99.1 missing in index  : {n_ex991_missing:,}")
    print(f"  Fetched + scored          : {n_scored_new:,}")
    print(f"  Fetch failures            : {n_fetch_fail:,}")
    print(f"  Skipped (already ex991)   : {n_skip_existing:,}")
    if n_items_202 > 0:
        rate = 100 * n_ex991_found / n_items_202
        print(f"  EX-99.1 found rate        : {rate:.1f}%")


if __name__ == "__main__":
    main()
