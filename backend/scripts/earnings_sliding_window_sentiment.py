"""Sliding-window FinBERT scoring on earnings call full body — Wave 3.

Re-processes existing earnings_call_sentiment rows by:
  1. Re-downloading the SEC 8-K filing body
  2. Splitting into 480-token windows (with 64-token overlap)
  3. Running FinBERT on each window via GPU batched inference
  4. Aggregating per-filing:
     - sentiment_mean (avg of net sentiment across windows)
     - sentiment_max (most positive window)
     - sentiment_min (most negative window)
     - sentiment_std (variability across the document)
     - peak_pos_window_pos (position of most positive window 0..1)
     - peak_neg_window_pos
     - n_windows

Updates `earnings_call_sentiment` with new columns sliding_* if absent.

Usage:
    uv run python scripts/earnings_sliding_window_sentiment.py --max 500
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

from sqlalchemy import text
from core.config import get_settings
from core.db import get_engine, session_scope


MIGRATION_SQL = """
ALTER TABLE earnings_call_sentiment
    ADD COLUMN IF NOT EXISTS sliding_sent_mean DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS sliding_sent_max DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS sliding_sent_min DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS sliding_sent_std DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS sliding_peak_pos_loc DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS sliding_peak_neg_loc DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS sliding_n_windows INTEGER;
"""


def fetch_full_filing_text(ticker: str, fd, ua: str) -> str:
    """Re-fetch original 8-K filing from SEC for full body."""
    # We need to lookup CIK then filing URL from disclosures or submissions JSON
    from sqlalchemy import select
    from core.db import session_scope
    from core.models.universe import Security
    with session_scope() as s:
        cik = s.execute(select(Security.cik).where(
            Security.ticker == ticker, Security.market == "US")).scalar()
    if not cik:
        return ""
    # Fetch submissions and find matching filing_date with item 2.02
    cik_padded = cik.zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
    try:
        r = httpx.get(url, headers={"User-Agent": ua}, timeout=20)
        if r.status_code != 200:
            return ""
        data = r.json()
    except Exception:
        return ""
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accs = recent.get("accessionNumber", [])
    items = recent.get("items", [])
    docs = recent.get("primaryDocument", [])
    for f, d, a, it, doc in zip(forms, dates, accs, items, docs):
        if d != str(fd) or not f.startswith("8-K") or "2.02" not in (it or ""):
            continue
        cik_int = str(int(cik))
        acc_clean = a.replace("-", "")
        doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_clean}/{doc}"
        try:
            r2 = httpx.get(doc_url, headers={"User-Agent": ua}, timeout=60)
            r2.raise_for_status()
            raw = r2.text
        except Exception:
            return ""
        clean = re.sub(r"<[^>]+>", " ", raw)
        clean = re.sub(r"&[a-z]+;", " ", clean)
        return re.sub(r"\s+", " ", clean).lower()
    return ""


def sliding_finbert_score(body: str, tokenizer, model, device, dtype,
                            window_size: int = 480, stride: int = 416,
                            batch_size: int = 32) -> dict:
    """Tokenize body, slice into overlapping windows, batch FinBERT."""
    import torch
    encoded = tokenizer(body, return_tensors=None, truncation=False,
                          add_special_tokens=False)["input_ids"]
    if len(encoded) <= window_size:
        windows = [encoded]
    else:
        windows = []
        for start in range(0, len(encoded), stride):
            window = encoded[start: start + window_size]
            if len(window) < 50:
                break
            windows.append(window)
    if not windows:
        return {}

    # Convert each window back to text via tokenizer for batched inference
    texts = [tokenizer.decode(w, skip_special_tokens=True) for w in windows]
    nets = []
    for i in range(0, len(texts), batch_size):
        chunk = texts[i:i + batch_size]
        inputs = tokenizer(chunk, padding=True, truncation=True,
                            max_length=512, return_tensors="pt").to(device)
        with torch.no_grad():
            logits = model(**inputs).logits
        probs = torch.softmax(logits.float(), dim=-1).cpu().numpy()
        # FinBERT: 0=positive, 1=negative, 2=neutral
        net = probs[:, 0] - probs[:, 1]
        nets.extend(net.tolist())
    nets_arr = list(nets)
    if not nets_arr:
        return {}
    n = len(nets_arr)
    mx_idx = max(range(n), key=lambda i: nets_arr[i])
    mn_idx = min(range(n), key=lambda i: nets_arr[i])
    return {
        "sliding_sent_mean": sum(nets_arr) / n,
        "sliding_sent_max": max(nets_arr),
        "sliding_sent_min": min(nets_arr),
        "sliding_sent_std": (sum((x - sum(nets_arr) / n) ** 2 for x in nets_arr) / n) ** 0.5,
        "sliding_peak_pos_loc": mx_idx / max(1, n - 1),
        "sliding_peak_neg_loc": mn_idx / max(1, n - 1),
        "sliding_n_windows": n,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=500)
    args = ap.parse_args()

    settings = get_settings()
    ua = str(getattr(settings, "sec_user_agent", None) or "research-bot research@example.com")

    eng = get_engine()
    with eng.begin() as conn:
        conn.execute(text(MIGRATION_SQL))
    print("[sliding] table migrated")

    import torch
    from transformers import AutoTokenizer, AutoModelForSequenceClassification
    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32
    print(f"[sliding] device={device} dtype={dtype}")
    tokenizer = AutoTokenizer.from_pretrained("ProsusAI/finbert")
    model = AutoModelForSequenceClassification.from_pretrained("ProsusAI/finbert").to(device=device, dtype=dtype)
    model.eval()

    with session_scope() as s:
        rows = list(s.execute(text("""
            SELECT ticker, filing_date FROM earnings_call_sentiment
             WHERE sliding_sent_mean IS NULL
             ORDER BY filing_date DESC LIMIT :max
        """), {"max": args.max}).all())
    print(f"[sliding] {len(rows)} earnings calls to process")

    total_ok = 0
    for ticker, fd in rows:
        time.sleep(0.15)
        body = fetch_full_filing_text(ticker, fd, ua)
        if len(body) < 1000:
            continue
        feats = sliding_finbert_score(body, tokenizer, model, device, dtype)
        if not feats:
            continue
        feats["ticker"] = ticker
        feats["fd"] = fd
        with eng.begin() as conn:
            conn.execute(text("""
                UPDATE earnings_call_sentiment SET
                  sliding_sent_mean = :sliding_sent_mean,
                  sliding_sent_max = :sliding_sent_max,
                  sliding_sent_min = :sliding_sent_min,
                  sliding_sent_std = :sliding_sent_std,
                  sliding_peak_pos_loc = :sliding_peak_pos_loc,
                  sliding_peak_neg_loc = :sliding_peak_neg_loc,
                  sliding_n_windows = :sliding_n_windows
                WHERE market='US' AND ticker = :ticker AND filing_date = :fd
            """), feats)
        total_ok += 1
        if total_ok % 25 == 0:
            print(f"  ok={total_ok}/{len(rows)}", flush=True)

    print(f"\n[sliding] Done. {total_ok}/{len(rows)} processed.")


if __name__ == "__main__":
    main()
