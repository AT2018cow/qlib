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
import sys
import math
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quant_bootstrap import ensure_quant_import_paths

ensure_quant_import_paths()
import artifact_io as json
from account_boundary import account_identity, reconcile_states
from datetime import date


def attach_backfill_boundary(prefix, artifact):
    """Bind an immutable prefix to its same-date paper closing state."""
    market = prefix.get("market")
    identity = account_identity(market)
    end = prefix["points"][-1]["date"]
    if (
        artifact.get("market") != market
        or artifact.get("usage_date") != end
        or artifact.get("paper_lineage") != identity["paper_lineage"]
        or prefix.get("profile") != identity["profile"]
        or prefix.get("initial_cash") != identity["initial_cash"]
        or any(prefix.get(key) != identity[key] for key in ("horizon", "topk", "nd"))
    ):
        raise ValueError("backfill closing artifact identity mismatch")
    manifest = artifact.get("production_lineage") or {}
    if (
        manifest.get("profile") != identity["profile"]
        or manifest.get("label_horizon") != identity["horizon"]
        or any(manifest.get(key) != identity[key] for key in ("topk", "nd"))
    ):
        raise ValueError("backfill closing model/profile mismatch")
    positions = artifact["positions"]
    cash = _number(artifact["cash"], "boundary cash")
    close = cash + sum(
        _number(p["shares"], "boundary shares") * _number(p["last_price"], "boundary mark") for p in positions.values()
    )
    expected = _number(prefix["points"][-1]["nav"], "prefix NAV") * identity["initial_cash"]
    if abs(close - expected) > 0.025:
        raise ValueError("backfill close NAV disagrees with closing cash/inventory marks")
    return {
        **prefix,
        "account_identity": identity,
        "closing_state": {"cash": cash, "shares": {s: p["shares"] for s, p in positions.items()}},
    }


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
        date.fromisoformat(day)
        nav = _number(point.get("nav"), f"backfill nav {day}")
        if nav <= 0:
            raise ValueError(f"backfill nav must be positive: {day}")
        daily = _number(point.get("daily_return"), f"backfill daily return {day}")
        cumulative = _number(point.get("cumulative_return"), f"backfill cumulative return {day}")
        prior = cleaned[-1]["nav"] if cleaned else 1.0
        if abs(cumulative - (nav - 1)) > 1e-6 or abs(daily - (nav / prior - 1)) > 1e-6:
            raise ValueError("inconsistent backfill NAV/return")
        cleaned.append(
            {
                "date": day,
                "nav": nav,
                "daily_return": daily,
                "cumulative_return": cumulative,
                "data_mode": "BACKFILL_RESEARCH",
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
    result = {
        "start_date": str(prefix.get("start") or prefix_points[0]["date"]),
        "inception_date": prefix_points[0]["date"],
        "status": "active",
        "data_mode": "BACKFILL_RESEARCH",
        "backfill_end_date": prefix_last_date,
        "latest_date": prefix_last_date,
        "cumulative_return": prefix_points[-1]["cumulative_return"],
        "return_interpretation": "BACKFILL_RESEARCH_ONLY",
        "points": [dict(point) for point in prefix_points],
    }
    if live.get("status") != "active" or not live_points:
        return result

    identity = prefix.get("account_identity")
    if not identity or identity != live.get("account_identity") or identity["initial_cash"] != initial_cash:
        raise ValueError("missing/mismatched account identity at segment boundary")
    if identity != account_identity(identity.get("market")):
        raise ValueError("unexpected canonical account identity")
    reconcile_states(prefix.get("closing_state"), live_points[0].get("opening_state"))
    dates = live.get("trading_dates")
    if not isinstance(dates, list) or dates != sorted(set(dates)) or prefix_last_date not in dates:
        raise ValueError("missing verified trading calendar at segment boundary")
    for day in dates:
        date.fromisoformat(day)
    required = [d for d in dates if prefix_last_date < d <= live_points[-1]["date"]]
    if required != [p["date"] for p in live_points]:
        raise ValueError("missing trading session at segment boundary")

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
        date.fromisoformat(day)
        if index:
            reconcile_states(live_points[index - 1].get("closing_state"), point.get("opening_state"))
        policy = point.get("execution_st_policy")
        if policy not in ("ST_VERIFIED_PREOPEN", "ST_IGNORED_REFERENCE_ONLY"):
            raise ValueError("live point requires explicit ST execution evidence")
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
                "data_mode": "LIVE_PAPER",
                "execution_st_policy": policy,
                "execution_rule_version": point.get("execution_rule_version", "LEGACY_UNVERSIONED"),
                "opening_state": point["opening_state"],
                "closing_state": point["closing_state"],
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
            "schema_version": 2,
            "account_identity": identity,
            "return_interpretation": "BACKFILL_PLUS_DISCLOSED_LIVE_PAPER",
            "st_fallback_dates": [
                p["date"] for p in merged if p.get("execution_st_policy") == "ST_IGNORED_REFERENCE_ONLY"
            ],
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
    if prefix is not None and prefix.get("market"):
        market = prefix["market"]
        suffix = "" if market == "csi1000" else f"_{market}"
        boundary = args.prefix.parent / f"{prefix['points'][-1]['date']}_paper_portfolio{suffix}.json"
        prefix = attach_backfill_boundary(prefix, json.loads(boundary.read_text()))
    live = json.loads(args.live.read_text())
    merged = merge_forward_performance(prefix, live)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
