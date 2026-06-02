"""SEC Form 4 XML body parser → structured insider transaction data.

Form 4 filings have URL pointers in `disclosures.source_url`. The
primary document is an XML file with fields:

  ownershipDocument
    issuer (ticker)
    reportingOwner (insider name, role)
    nonDerivativeTable / derivativeTable
      nonDerivativeTransaction
        securityTitle, transactionDate, transactionAmounts (shares, price, A/D),
        postTransactionAmounts (shares owned after)

A (Acquisition) = buy, D (Disposition) = sell.

For ML features we want:
- Insider role (CEO/CFO/Director/Officer/Other)
- transactionCode (P=Open purchase, S=Open sale, A=Award etc.)
- shares × price = transaction value
- net direction by date (sum of A - sum of D in shares × price)

Stores parsed records in a NEW table `insider_transactions` (TODO:
need alembic migration to create) OR aggregates into a JSON blob in
disclosures.body_text (faster, no schema change).

For now we choose the faster path: aggregate per-filing summary into
disclosures.body_text as JSON and flip body_fetched=True. The feature
pipeline can JSON-parse for aggregations.

Usage:

    uv run python scripts/sec_form4_parser.py [--limit N]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from sqlalchemy import select, update

from core.config import get_settings
from core.db import session_scope
from core.models.disclosures import Disclosure


THROTTLE = 0.12  # 10 req/s SEC ceiling


def fetch_filing_index(filing_url: str, ua: str) -> Optional[list[str]]:
    """Given a primary doc URL, find the XML in the same filing folder."""
    base = filing_url.rsplit("/", 1)[0]
    idx_url = f"{base}/"
    try:
        r = httpx.get(idx_url, headers={"User-Agent": ua}, timeout=15,
                      follow_redirects=True)
        r.raise_for_status()
    except Exception:
        return None
    text = r.text.lower()
    # Look for primary_doc.xml or any .xml link
    xml_files = []
    for line in text.split("\n"):
        if ".xml" in line:
            # crude extraction of href
            i = line.find("href=")
            if i < 0:
                continue
            href = line[i + 6:].split('"')[0]
            if href.endswith(".xml"):
                xml_files.append(href)
    if not xml_files:
        return None
    # Return full URLs
    return [base + "/" + (f if not f.startswith("/") else f.lstrip("/")) for f in xml_files]


def fetch_form4_xml(url: str, ua: str) -> Optional[str]:
    try:
        r = httpx.get(url, headers={"User-Agent": ua}, timeout=15,
                      follow_redirects=True)
        r.raise_for_status()
        return r.text
    except Exception:
        return None


def parse_form4(xml_str: str) -> Optional[dict]:
    """Parse the OWNERSHIP DOCUMENT XML and return summary fields."""
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError:
        return None

    def _findtext(elem, path):
        node = elem.find(path)
        return node.text.strip() if node is not None and node.text else None

    summary = {
        "owner_name": _findtext(root, ".//reportingOwner/reportingOwnerId/rptOwnerName"),
        "is_director": _findtext(root, ".//reportingOwner/reportingOwnerRelationship/isDirector") == "1",
        "is_officer": _findtext(root, ".//reportingOwner/reportingOwnerRelationship/isOfficer") == "1",
        "is_ten_percent_owner": _findtext(root, ".//reportingOwner/reportingOwnerRelationship/isTenPercentOwner") == "1",
        "officer_title": _findtext(root, ".//reportingOwner/reportingOwnerRelationship/officerTitle"),
        "transactions": [],
    }

    for tx in root.findall(".//nonDerivativeTransaction"):
        try:
            code = _findtext(tx, "transactionCoding/transactionCode")
            ad = _findtext(tx, "transactionAmounts/transactionAcquiredDisposedCode/value")
            shares_raw = _findtext(tx, "transactionAmounts/transactionShares/value")
            price_raw = _findtext(tx, "transactionAmounts/transactionPricePerShare/value")
            date_str = _findtext(tx, "transactionDate/value")
            if not shares_raw:
                continue
            shares = float(shares_raw)
            price = float(price_raw) if price_raw else 0.0
            summary["transactions"].append({
                "code": code, "a_or_d": ad, "shares": shares, "price": price,
                "value": shares * price, "date": date_str,
            })
        except (ValueError, TypeError):
            continue

    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200,
                    help="how many Form 4 filings to process this run")
    ap.add_argument("--days-back", type=int, default=365)
    args = ap.parse_args()

    settings = get_settings()
    ua = settings.sec_user_agent
    if not ua:
        sys.exit("SEC_USER_AGENT missing")

    cutoff = date.today() - __import__("datetime").timedelta(days=args.days_back)
    print(f"SEC Form 4 parser :: filings_since={cutoff} limit={args.limit}")

    with session_scope() as s:
        candidates = list(s.execute(
            select(Disclosure.id, Disclosure.ticker, Disclosure.source_url,
                   Disclosure.filing_date)
            .where(
                Disclosure.filing_type_canonical == "INSIDER",
                Disclosure.body_fetched.is_(False),
                Disclosure.filing_date >= cutoff,
                Disclosure.source_url.isnot(None),
            )
            .order_by(Disclosure.filing_date.desc())
            .limit(args.limit)
        ).all())

    print(f"  {len(candidates)} Form 4 filings to process")

    total_ok, total_fail = 0, 0
    for i, (disc_id, ticker, url, fd) in enumerate(candidates, start=1):
        # The source_url points to the XSL-rendered HTML view
        # (.../xslF345X06/doc4.xml). The raw XML lives at the parent
        # path with the xslF345X06 segment removed.
        raw_xml_url = url.replace("/xslF345X06/", "/")
        xml_str = fetch_form4_xml(raw_xml_url, ua)
        time.sleep(THROTTLE)
        if not xml_str:
            total_fail += 1
            continue
        parsed = parse_form4(xml_str)
        if not parsed:
            total_fail += 1
            continue

        with session_scope() as s:
            s.execute(
                update(Disclosure)
                .where(Disclosure.id == disc_id)
                .values(
                    body_fetched=True,
                    body_text=json.dumps(parsed, ensure_ascii=False),
                )
            )
        total_ok += 1
        if i % 25 == 0 or i == len(candidates):
            print(f"  [{i:4d}/{len(candidates)}] ok={total_ok} fail={total_fail}",
                  flush=True)

    print(f"\nDone. ok={total_ok} fail={total_fail}")


if __name__ == "__main__":
    main()
