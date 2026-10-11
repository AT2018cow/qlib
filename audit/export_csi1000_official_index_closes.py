"""Fetch CSI's official SH000852 price-index closes into local, uncommitted CSV.

Source method: AKShare stock_zh_index_hist_csindex (CSI direct HTTP endpoint).
This script never edits existing audit inputs or grants price-source certification.
Requires permitted Internet access to csindex.com.cn; block rather than fallback
to another vendor or saved Qlib benchmark. Do not commit vendor market prices.

 python -m audit.export_csi1000_official_index_closes \\
   --out-dir /tmp/independent-csi1000-20261011

Then separately compare with the original corrected phase0 Parquet:
 python -m audit.csi1000_residual_evidence \\
   --frozen-report /readonly/23b92de0_p00/fixed_diagnostic_report.parquet \\
   --external-index /tmp/independent-csi1000-20261011/000852_prices.csv
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timezone, date
from pathlib import Path

INDEX = "000852"
START = "20241230"  # Capture previous close for 2025-01-02.
FIRST = date(2025, 1, 2)
END = date(2026, 9, 30)
EXPECTED_TRADING_SESSIONS = 424
END_STR = "20260930"
BASE_URL = "https://www.csindex.com.cn/csindex-home/perf/index-perf"


def parse_daily_records(raw_bytes):
    obj = json.loads(raw_bytes)
    data = obj.get("data")
    if not isinstance(data, list):
        raise ValueError("BLOCKED: CSI official response data is not a list")
    all_rows = []
    for row in data:
        if not isinstance(row, list) or len(row) < 10:
            raise ValueError("BLOCKED: unrecognized official CSI row schema")
        day = date.fromisoformat(str(row[0])[:10])
        if day > END or day < date(2024, 12, 1):
            continue  # Historical first-row boilerplate is not a session.
        code = str(row[1]).strip().zfill(6)
        if code != INDEX:
            raise ValueError("BLOCKED: unexpected index_code: " + code)
        close = float(row[9])
        if not math.isfinite(close) or close <= 0:
            raise ValueError("BLOCKED: invalid CSI index close")
        all_rows.append((day, close))
    all_rows.sort()
    days = [day for day, _ in all_rows]
    if days != sorted(set(days)):
        raise ValueError("BLOCKED: duplicate index sessions in CSI response")
    prior = [row for row in all_rows if row[0] < FIRST]
    current = [row for row in all_rows if FIRST <= row[0] <= END]
    if not prior or len(current) != EXPECTED_TRADING_SESSIONS:
        raise ValueError("BLOCKED: missing preceding close or 424 exact sessions")
    return [prior[-1]] + current


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out-dir", required=True, type=Path,
                   help="NEW directory outside repository and outside /vol")
    args = p.parse_args()
    root = Path(__file__).resolve().parents[1]
    out = args.out_dir.resolve()
    if out.exists() or out.is_relative_to(root) or out.is_relative_to(Path("/vol")):
        raise SystemExit("BLOCKED: output must be an entirely new external directory")
    params = urllib.parse.urlencode({
        "indexCode": INDEX, "startDate": START, "endDate": END_STR
    })
    url = BASE_URL + "?" + params
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                                "User-Agent": "csi1000-audit-readonly/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status != 200:
                raise RuntimeError("HTTP status " + str(resp.status))
            raw = resp.read(5_000_001)
        if len(raw) > 5_000_000:
            raise RuntimeError("official response exceeds size cap")
        series = parse_daily_records(raw)
    except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "BLOCKED_OFFICIAL_SOURCE", "reason": str(exc)}))
        return 2
    # Canonical, source-attributed local derivative. Not external source certification.
    out.mkdir(parents=True)
    csvpath = out / "000852_prices.csv"
    with csvpath.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=[
            "date", "close", "index_code", "return_type", "source_id", "source_url"
        ])
        writer.writeheader()
        for day, close in series:
            writer.writerow({
                "date": day.isoformat(), "close": repr(close),
                "index_code": INDEX, "return_type": "price_return",
                "source_id": "csindex_official_api", "source_url": url
            })
    meta = {
        "status": "EXTERNAL_SOURCE_BYTES_DOWNLOADED_NOT_RECONCILED",
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        "request_url": url,
        "http_response_sha256": hashlib.sha256(raw).hexdigest(),
        "normalized_csv_sha256": hashlib.sha256(csvpath.read_bytes()).hexdigest(),
        "rows": len(series), "first": series[0][0].isoformat(),
        "last": series[-1][0].isoformat(),
        "source": "China Securities Index Company official index-perf API",
        "methodology": "SH000852 price-level close-to-close",
        "human_provenance_review_required": True,
        "no_republication_of_full_vendor_data": True,
    }
    (out / "source_receipt.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(meta, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
