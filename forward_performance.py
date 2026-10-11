"""Forward-only, verified CSI1000 paper NAV publication.

Historical and forward sessions both settle via PaperPortfolio.execute_pending.
This builder merely verifies and displays the resulting audited ledger; it
never synthesizes orders, missing closes or backfilled forward returns.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Any, Iterable, Mapping

from paper_accounting import verify_ledger

FORWARD_PERFORMANCE_START = "2026-10-12"
CANONICAL_LINEAGE = "stage_b_winner_canonical"


def build_forward_performance(
    artifacts: Iterable[Mapping[str, Any]], *, start_date: str = FORWARD_PERFORMANCE_START
) -> dict:
    date.fromisoformat(start_date)
    reports = {}
    scheduled = set()
    for artifact in artifacts:
        if artifact.get("market") != "csi1000":
            continue
        if artifact.get("paper_lineage") != CANONICAL_LINEAGE:
            continue
        pending = artifact.get("pending_orders")
        if isinstance(pending, Mapping):
            due = pending.get("execution_date")
            if isinstance(due, str) and due >= start_date:
                date.fromisoformat(due)
                scheduled.add(due)
        report = artifact.get("execution_report")
        if not isinstance(report, Mapping):
            continue
        day = str(report.get("date") or "")
        if not day or day < start_date:
            continue
        date.fromisoformat(day)
        if report.get("valuation_verified") is not True:
            raise ValueError(f"unverified forward valuation for {day}")
        # Cash flow, inventory and close NAV are recomputed from the primitive
        # filled orders and marked positions, independently of paper state.
        verify_ledger(report)
        try:
            opening = float(report["portfolio_value_pre_trade_open"])
            closing = float(report["portfolio_value_close"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"incomplete NAV report {day}") from exc
        if not all(math.isfinite(x) and x > 0 for x in (opening, closing)):
            raise ValueError(f"invalid NAV report {day}")
        item = {
            "date": day,
            "portfolio_value_pre_trade_open": opening,
            "portfolio_value_close": closing,
            "cash": float(report["cash"]),
            "position_shares": {
                sym: float(mark["shares"])
                for sym, mark in report["position_marks"].items()
            },
        }
        if day in reports and reports[day] != item:
            raise ValueError(f"conflicting performance reports for {day}")
        reports[day] = item

    ordered = [reports[d] for d in sorted(reports)]
    if not ordered:
        return {
            "start_date": start_date,
            "status": "awaiting_first_valuation",
            "latest_date": None,
            "cumulative_return": None,
            "points": [],
        }

    first = ordered[0]["date"]
    latest = ordered[-1]["date"]
    missing = sorted(day for day in scheduled if day <= latest and day not in reports)
    if missing:
        raise ValueError(f"missing scheduled execution settlement(s): {missing}")
    baseline = ordered[0]["portfolio_value_pre_trade_open"]
    prev = None
    points = []
    for row in ordered:
        if prev is not None:
            if abs(row["cash"] - prev["cash"]) > 0.011:
                raise ValueError(f"cash discontinuity before {row['date']}")
            if row["position_shares"] != prev["position_shares"]:
                raise ValueError(f"position discontinuity before {row['date']}")
        close = row["portfolio_value_close"]
        base = baseline if prev is None else prev["portfolio_value_close"]
        points.append({
            "date": row["date"],
            "nav": close / baseline,
            "daily_return": close / base - 1,
            "cumulative_return": close / baseline - 1,
        })
        prev = row
    return {
        "start_date": start_date,
        "inception_date": first,
        "status": "active",
        "latest_date": latest,
        "cumulative_return": points[-1]["cumulative_return"],
        "points": points,
    }
