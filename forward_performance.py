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
from csi1000_mvp_contract import REFERENCE_ST_POLICY

FORWARD_PERFORMANCE_START = "2026-10-12"
CANONICAL_LINEAGE = "stage_b_winner_canonical"


def build_forward_performance(
    artifacts: Iterable[Mapping[str, Any]], *, start_date: str = FORWARD_PERFORMANCE_START
) -> dict:
    date.fromisoformat(start_date)
    reports = {}
    scheduled = set()
    calendar_snapshots = {}
    calendar_dates = set()
    reported_st_policies = set()
    for artifact in artifacts:
        if artifact.get("market") != "csi1000":
            continue
        if artifact.get("paper_lineage") != CANONICAL_LINEAGE:
            continue
        usage_day = artifact.get("usage_date")
        if isinstance(usage_day, str) and usage_day >= start_date:
            date.fromisoformat(usage_day)
            snapshot = artifact.get("trading_calendar")
            if (not isinstance(snapshot, Mapping) or
                snapshot.get("source") != "akshare.tool_trade_date_hist_sina" or
                snapshot.get("through") != usage_day or
                not isinstance(snapshot.get("dates"), list)):
                raise ValueError(f"unverified publication calendar for {usage_day}")
            dates = snapshot["dates"]
            if (dates != sorted(set(dates)) or
                any(not isinstance(d, str) or d < start_date or d > usage_day
                    for d in dates)):
                raise ValueError(f"invalid trading calendar dates for {usage_day}")
            for d in dates:
                date.fromisoformat(d)
            if usage_day not in dates:
                raise ValueError(f"publication date not in trading calendar: {usage_day}")
            frozen = tuple(dates)
            if usage_day in calendar_snapshots and calendar_snapshots[usage_day] != frozen:
                raise ValueError(f"conflicting trading calendar for {usage_day}")
            calendar_snapshots[usage_day] = frozen
            calendar_dates.update(frozen)
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
        policy = (report.get("metadata") or {}).get("execution_st_policy")
        reported_st_policies.add(policy or "UNSPECIFIED")
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
            "opening_cash": float(report["opening_cash"]),
            "opening_shares": {
                sym: float(pos["shares"])
                for sym, pos in report["opening_positions"].items()
            },
            "position_shares": {
                sym: float(mark["shares"])
                for sym, mark in report["position_marks"].items()
            },
        }
        if day in reports and reports[day] != item:
            raise ValueError(f"conflicting performance reports for {day}")
        reports[day] = item

    # A later snapshot must extend, never rewrite, the calendar already
    # published for earlier usage dates.
    prev_calendar = ()
    for usage in sorted(calendar_snapshots):
        snapshot = calendar_snapshots[usage]
        if snapshot[:len(prev_calendar)] != prev_calendar:
            raise ValueError(f"calendar history drift at {usage}")
        prev_calendar = snapshot

    ordered = [reports[d] for d in sorted(reports)]
    if len(reported_st_policies) > 1:
        raise ValueError("mixed ST execution policies in a single forward reference NAV")
    execution_st_policy = next(iter(reported_st_policies), None)
    if execution_st_policy == "UNSPECIFIED":
        execution_st_policy = None
    if not ordered:
        return {
            "start_date": start_date,
            "status": "awaiting_first_valuation",
            "execution_st_policy": execution_st_policy,
            "latest_date": None,
            "cumulative_return": None,
            "points": [],
        }

    first = ordered[0]["date"]
    latest = ordered[-1]["date"]
    if not calendar_snapshots:
        raise ValueError("cannot publish forward NAV without dated trading calendar evidence")
    reported_outside = sorted(set(reports).difference(calendar_dates))
    if reported_outside:
        raise ValueError(f"report dates outside observed trading calendar: {reported_outside}")
    missing = sorted(
        (day for day in scheduled if day <= latest and day not in reports)
    )
    # A completely absent daily artifact leaves no pending-order record.
    # Checking all known exchange sessions closes that silent-gap loophole.
    missing += sorted(day for day in calendar_dates
                      if first <= day <= latest and day not in reports
                      and day not in missing)
    if missing:
        raise ValueError(f"missing scheduled/trading-calendar settlement(s): {missing}")
    baseline = ordered[0]["portfolio_value_pre_trade_open"]
    prev = None
    points = []
    for row in ordered:
        if prev is not None:
            if abs(row["opening_cash"] - prev["cash"]) > 0.011:
                raise ValueError(f"cash discontinuity before {row['date']}")
            if row["opening_shares"] != prev["position_shares"]:
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
        "execution_st_policy": execution_st_policy,
        "return_interpretation": (
            "REFERENCE_ONLY_ST_NOT_MODELED"
            if execution_st_policy == REFERENCE_ST_POLICY
            else "PAPER_ONLY"
        ),
        "latest_date": latest,
        "cumulative_return": points[-1]["cumulative_return"],
        "points": points,
    }
