#!/usr/bin/env python3
"""Splice the disclosed October backfill prefix onto the live paper ledger.

October 2026 is a point-in-time research backfill: its NAV points are real
simulated-account marks but the paper artifacts were not published live.  From
2026-11-03 the scheduled production job resumes the *same* 1M accounts.  The
dashboard must show one continuous curve, not reset to "awaiting first
valuation" when the live ledger starts.

The live builder emits absolute close values, so the splice is just
``live_close / backfill_close_value``; no return is inferred from missing data.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Mapping


def _number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {label}: {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"non-finite {label}: {value!r}")
    return number


def _prefix_series(prefix: Mapping[str, Any]) -> tuple[list[dict], float]:
    points = prefix.get("points")
    if not isinstance(points, list) or not points:
        raise ValueError("backfill prefix has no points")
    initial_cash = _number(prefix.get("initial_cash"), "backfill initial cash")
    if initial_cash <= 0:
        raise ValueError("backfill initial cash must be positive")
    cleaned = []
    previous_date = ""
    for point in points:
        if not isinstance(point, Mapping):
            raise ValueError("invalid backfill point")
        day = point.get("date")
        if not isinstance(day, str) or len(day) != 10 or day <= previous_date:
            raise ValueError(f"backfill dates must increase: {day!r}")
        nav = _number(point.get("nav"), f"backfill nav {day}")
        if nav <= 0:
            raise ValueError(f"backfill nav must be positive: {day}")
        cleaned.append(
            {
                "date": day,
                "nav": nav,
                "daily_return": _number(point.get("daily_return"), f"backfill daily return {day}"),
                "cumulative_return": _number(point.get("cumulative_return"), f"backfill cumulative return {day}"),
            }
        )
        previous_date = day
    return cleaned, initial_cash


def merge_forward_performance(
    prefix: Mapping[str, Any] | None,
    live: Mapping[str, Any],
) -> dict:
    """Return one continuous series, or the prefix while live has no marks."""
    if not isinstance(live, Mapping):
        raise ValueError("live forward series must be an object")
    live_points = live.get("points")
    if not isinstance(live_points, list):
        raise ValueError("live forward series has no points array")

    if prefix is None:
        if live.get("status") != "active" or not live_points:
            return dict(live)
        return dict(live)

    prefix_points, initial_cash = _prefix_series(prefix)
    prefix_last_date = prefix_points[-1]["date"]
    prefix_last_value = prefix_points[-1]["nav"] * initial_cash
    result = {
        "start_date": str(prefix.get("start") or prefix_points[0]["date"]),
        "inception_date": prefix_points[0]["date"],
        "status": "active",
        "data_mode": "BACKFILL_RESEARCH",
        "backfill_end_date": prefix_last_date,
        "latest_date": prefix_last_date,
        "cumulative_return": prefix_points[-1]["cumulative_return"],
        "return_interpretation": "CONTINUOUS_SIMULATED_ACCOUNT",
        "points": [dict(point) for point in prefix_points],
    }
    if live.get("status") != "active" or not live_points:
        return result

    previous_nav = prefix_points[-1]["nav"]
    previous_date = prefix_last_date
    merged = result["points"]
    for index, point in enumerate(live_points):
        if not isinstance(point, Mapping):
            raise ValueError("invalid live point")
        day = point.get("date")
        if not isinstance(day, str) or len(day) != 10 or day <= previous_date:
            raise ValueError(f"live dates must follow the backfill: {day!r}")
        close = _number(point.get("portfolio_value_close"), f"live close value {day}")
        if close <= 0:
            raise ValueError(f"live close value must be positive: {day}")
        if index == 0:
            # The 1M account is continuous: the first live close must be in the
            # same value range as the 10/30 backfill close.  Allow a generous
            # overnight gap, but fail closed on a reset or a unit mix-up.
            gap = abs(close - prefix_last_value) / prefix_last_value
            if gap >= 0.25:
                raise ValueError(f"live/backfill account value discontinuity at {day}: gap={gap:.1%}")
        nav = close / initial_cash
        if not 0.05 < nav < 20:
            raise ValueError(f"live/backfill account value discontinuity at {day}: nav={nav}")
        merged.append(
            {
                "date": day,
                "nav": nav,
                "daily_return": nav / previous_nav - 1,
                "cumulative_return": nav - 1,
                "portfolio_value_close": close,
            }
        )
        previous_date = day
        previous_nav = nav

    result.update(
        {
            "data_mode": "BACKFILL_PLUS_LIVE",
            "live_start_date": merged[len(prefix_points)]["date"],
            "latest_date": merged[-1]["date"],
            "cumulative_return": merged[-1]["cumulative_return"],
        }
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefix", type=Path)
    parser.add_argument("--live", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    prefix = json.loads(args.prefix.read_text()) if args.prefix and args.prefix.is_file() else None
    live = json.loads(args.live.read_text())
    merged = merge_forward_performance(prefix, live)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
