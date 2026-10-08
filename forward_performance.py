"""Forward-only public performance series for the CSI1000 production model.

The public series deliberately starts on 2026-10-12.  It is built from the
stateful paper account's actual execution reports, not from backfilled research
returns.  The first day's pre-trade open account value is normalized to 1.0;
subsequent points use end-of-day marked portfolio value.
"""
from __future__ import annotations

from typing import Iterable, Mapping, Any

FORWARD_PERFORMANCE_START = "2026-10-12"


def build_forward_performance(
    artifacts: Iterable[Mapping[str, Any]],
    *,
    start_date: str = FORWARD_PERFORMANCE_START,
) -> dict:
    """Build a deterministic forward performance series from paper artifacts.

    Each usable artifact must contain an execution report with:
      - date
      - portfolio_value_pre_trade_open
      - portfolio_value_close

    The first included pre-trade open value is the inception baseline.  This
    makes the first trading day's open->close P&L and trading costs part of the
    public forward result.
    """
    reports = {}
    for artifact in artifacts:
        if artifact.get("market") != "csi1000":
            continue
        if artifact.get("paper_lineage") != "stage_b_winner_canonical":
            continue
        report = artifact.get("execution_report")
        if not isinstance(report, Mapping):
            continue
        date = str(report.get("date") or "")
        if not date or date < start_date:
            continue
        try:
            open_value = float(report["portfolio_value_pre_trade_open"])
            close_value = float(report["portfolio_value_close"])
        except (KeyError, TypeError, ValueError):
            continue
        if open_value <= 0 or close_value <= 0:
            continue
        item = {
            "date": date,
            "portfolio_value_pre_trade_open": open_value,
            "portfolio_value_close": close_value,
        }
        # Same execution day must be byte-equivalent in normal production, but
        # if duplicate artifacts are supplied, require equivalent accounting.
        if date in reports and reports[date] != item:
            raise ValueError(f"conflicting performance reports for {date}")
        reports[date] = item

    ordered = [reports[d] for d in sorted(reports)]
    if not ordered:
        return {
            "start_date": start_date,
            "status": "awaiting_first_valuation",
            "latest_date": None,
            "cumulative_return": None,
            "points": [],
        }

    baseline = ordered[0]["portfolio_value_pre_trade_open"]
    prev_close = None
    points = []
    for row in ordered:
        close_value = row["portfolio_value_close"]
        daily_base = row["portfolio_value_pre_trade_open"] if prev_close is None else prev_close
        daily_return = close_value / daily_base - 1.0
        cumulative_return = close_value / baseline - 1.0
        points.append(
            {
                "date": row["date"],
                "nav": close_value / baseline,
                "daily_return": daily_return,
                "cumulative_return": cumulative_return,
            }
        )
        prev_close = close_value

    return {
        "start_date": start_date,
        "status": "active",
        "latest_date": points[-1]["date"],
        "cumulative_return": points[-1]["cumulative_return"],
        "points": points,
    }
