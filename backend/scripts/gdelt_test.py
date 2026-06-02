"""Quick GDELT BigQuery connectivity test."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from google.cloud import bigquery

PROJECT = os.environ.get("GCP_PROJECT_ID") or "project-39b6b2ad-4644-4993-aeb"


def main() -> None:
    client = bigquery.Client(project=PROJECT)
    print(f"BigQuery client OK, project={client.project}")

    # Test 1: count rows for one day using SQLDATE field
    q = client.query("""
        SELECT COUNT(*) AS n
        FROM `gdelt-bq.gdeltv2.events`
        WHERE SQLDATE = 20260515
    """)
    n = list(q.result())[0].n
    print(f"GDELT events on 2026-05-15: {n:,}")
    print(f"Bytes processed: {q.total_bytes_processed:,} ({q.total_bytes_processed / 1e9:.2f} GB)")

    # Test 2: ticker-relevant query for "Apple" on one day
    q2 = client.query("""
        SELECT COUNT(*) AS n_apple_mentions, AVG(AvgTone) AS avg_tone
        FROM `gdelt-bq.gdeltv2.gkg_partitioned`
        WHERE _PARTITIONTIME BETWEEN TIMESTAMP('2026-05-15') AND TIMESTAMP('2026-05-16')
          AND (V2Organizations LIKE '%apple inc%' OR V2Organizations LIKE '%Apple Inc%')
    """)
    rows = list(q2.result())
    print(f"GKG 'Apple Inc' mentions 2026-05-15: {rows[0].n_apple_mentions}, "
          f"avg_tone={rows[0].avg_tone}")
    print(f"  Bytes processed: {q2.total_bytes_processed:,}")


if __name__ == "__main__":
    main()
