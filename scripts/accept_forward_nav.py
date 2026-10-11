#!/usr/bin/env python3
"""Read-only production acceptance of publicly published CSI1000 paper NAV.

NOT_STARTED is an expected verdict before the first genuinely verified market
settlement.  PASS requires both the complete public artifact series and an
operator-supplied, separately sourced exchange calendar and adjusted OHLC/factor
snapshot; neither can be manufactured by this script.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forward_performance import FORWARD_PERFORMANCE_START, CANONICAL_LINEAGE, build_forward_performance


def _read_csv(path, required):
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not set(required).issubset(reader.fieldnames or []):
            raise ValueError(f"CSV missing columns {sorted(set(required) - set(reader.fieldnames or []))}: {path}")
        return list(reader)


def _equal_quote(observed, expected, label):
    try:
        actual = float(observed)
        want = float(expected)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"invalid quote {label}") from exc
    if not all(math.isfinite(x) and x > 0 for x in (actual, want)):
        raise ValueError(f"invalid quote {label}")
    if not math.isclose(actual, want, rel_tol=1e-7, abs_tol=1e-7):
        raise ValueError(f"independent quote mismatch {label}: {actual} != {want}")


def acceptance(signals, forward_path, *, calendar_path=None, quotes_path=None,
               calendar_source=None, quote_source=None):
    artifacts = []
    for path in sorted(Path(signals).glob("*_paper_portfolio.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            if path.name[:10] >= FORWARD_PERFORMANCE_START:
                raise ValueError(f"unreadable post-start paper artifact: {path}") from exc
            continue
        artifacts.append(raw)
    generated = build_forward_performance(artifacts)
    published = json.loads(Path(forward_path).read_text(encoding="utf-8"))
    if published != generated:
        raise ValueError("published forward JSON disagrees with verified raw paper artifacts")
    if generated["status"] != "active":
        return {"status": "NOT_STARTED", "audited_days": 0, "latest_date": None,
                "reason": "No completed post-start verified paper settlement"}
    start = generated["inception_date"]
    last = generated["latest_date"]
    if not all((calendar_path, quotes_path, calendar_source, quote_source)):
        return {"status": "BLOCKED_INDEPENDENT_SOURCES",
                "audited_days": len(generated["points"]), "latest_date": last,
                "reason": "Provide external calendar CSV, independently adjusted daily quotes CSV and provenance identifiers"}
    calendar_rows = _read_csv(calendar_path, {"date"})
    external_sessions = [r["date"] for r in calendar_rows if start <= r["date"] <= last]
    if external_sessions != sorted(set(external_sessions)):
        raise ValueError("independent exchange calendar has duplicate or out-of-order sessions")
    public_dates = [p["date"] for p in generated["points"]]
    if public_dates != external_sessions:
        raise ValueError(f"independent exchange calendar mismatch: published {public_dates}, external {external_sessions}")
    quotes = {}
    for row in _read_csv(quotes_path, {"date", "instrument", "open", "close", "factor"}):
        key = (row["date"], row["instrument"])
        if key in quotes:
            raise ValueError(f"duplicate independent adjusted quote {key}")
        quotes[key] = row
    verified_quotes = 0
    halt_marks = 0
    for artifact in artifacts:
        if artifact.get("market") != "csi1000" or artifact.get("paper_lineage") != CANONICAL_LINEAGE:
            continue
        report = artifact.get("execution_report")
        if not isinstance(report, dict):
            continue
        day = report.get("date")
        if day not in public_dates:
            continue
        for fill in report["executed_buy"] + report["executed_sell"]:
            sym = fill["instrument"]
            quote = quotes.get((day, sym))
            if not quote:
                raise ValueError(f"independent open missing for executed {day}/{sym}")
            _equal_quote(quote["open"], fill["price"], f"{day}/{sym} fill open")
            verified_quotes += 1
            if "factor" in fill:
                _equal_quote(quote["factor"], fill["factor"], f"{day}/{sym} fill factor")
                verified_quotes += 1
        for field, quote_col, reason in (("opening_marks", "open", "EXCHANGE_OPEN"),
                                         ("position_marks", "close", "EXCHANGE_CLOSE")):
            for sym, mark in report[field].items():
                if mark["reason"] == "CERTIFIED_HALT_CARRY":
                    halt_marks += 1
                    continue
                if mark["reason"] != reason:
                    raise ValueError(f"unverified mark source {day}/{sym}/{field}")
                quote = quotes.get((day, sym))
                if not quote:
                    raise ValueError(f"independent {quote_col} missing for marked {day}/{sym}")
                _equal_quote(quote[quote_col], mark["price"], f"{day}/{sym} {quote_col}")
                verified_quotes += 1
    return {
        "status": "HALT_SOURCE_REVIEW_REQUIRED" if halt_marks else "PASS_ACCOUNTING_CALENDAR_AND_EXTERNAL_QUOTES",
        "audited_days": len(public_dates), "latest_date": last,
        "external_calendar_source": calendar_source,
        "external_quote_source": quote_source,
        "compared_quote_fields": verified_quotes,
        "certified_halt_marks_needing_human_source_review": halt_marks,
        "qualification": "Declared external data provenance must be human-checked; paper fills do not represent broker fills",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signals", type=Path, required=True)
    parser.add_argument("--forward", type=Path, required=True)
    parser.add_argument("--calendar-csv", type=Path)
    parser.add_argument("--quotes-csv", type=Path)
    parser.add_argument("--calendar-source")
    parser.add_argument("--quote-source")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-pass", action="store_true")
    args = parser.parse_args()
    try:
        result = acceptance(args.signals, args.forward,
                            calendar_path=args.calendar_csv, quotes_path=args.quotes_csv,
                            calendar_source=args.calendar_source, quote_source=args.quote_source)
        exit_code = 0 if not args.require_pass or result["status"] == "PASS_ACCOUNTING_CALENDAR_AND_EXTERNAL_QUOTES" else 2
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result = {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}
        exit_code = 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
