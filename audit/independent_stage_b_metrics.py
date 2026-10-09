"""Independent, read-only CSI1000 Stage-B accounting audit.

Uses a frozen evidence manifest, raw Parquet files and the provider trading
calendar. Does NOT import portfolio_performance, Stage-B helpers or Qlib.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
from datetime import date
from pathlib import Path

REQUIRED = ("account", "return", "total_turnover", "turnover", "total_cost", "cost", "value", "cash", "bench")
STARTING_CASH = 100_000_000.0
ANNUAL_SESSIONS = 238
DAYS_PER_YEAR = 365.2425


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mean(xs):
    return math.fsum(xs) / len(xs)


def _ratio(xs, benchmark=None, sessions=238, annual_rf=0):
    if benchmark is None:
        risk_free = math.expm1(math.log1p(annual_rf) / sessions)
        x = [r - risk_free for r in xs]
    else:
        x = [r - b for r, b in zip(xs, benchmark)]
    sd = statistics.stdev(x) if len(x) > 1 else 0.0
    return _mean(x) / sd * math.sqrt(sessions) if sd else None


def _max_drawdown(navs):
    peak, worst = 1.0, 0.0
    for nav in navs:
        if nav <= 0 or not math.isfinite(nav):
            raise ValueError("invalid NAV")
        peak = max(peak, nav)
        worst = min(worst, nav / peak - 1)
    return worst


def metrics(dates, values):
    """Compute from primitive numeric arrays; no production metric functions."""
    n = len(dates)
    if n < 2:
        raise ValueError("at least two days required")
    r = [values["return"][i] - values["cost"][i] for i in range(n)]
    b = values["bench"]
    a = values["account"]
    prior = [STARTING_CASH] + a[:-1]
    implied = [a[i] / prior[i] - 1 for i in range(n)]
    delta = [implied[i] - r[i] for i in range(n)]
    strat = [x / STARTING_CASH for x in a]
    bench = []
    running = 1.0
    for x in b:
        running *= 1.0 + x
        bench.append(running)
    relative = [s / m for s, m in zip(strat, bench)]
    elapsed = max((dates[-1] - date.fromisoformat("2025-01-02")).days, 1)
    annual_power = DAYS_PER_YEAR / elapsed

    result = {
        "n_days": n,
        "start": dates[0].isoformat(),
        "end": dates[-1].isoformat(),
        "years": elapsed / DAYS_PER_YEAR,
        "strategy_total_return": strat[-1] - 1,
        "benchmark_total_return": bench[-1] - 1,
        "relative_total_return": relative[-1] - 1,
        "strategy_cagr": strat[-1] ** annual_power - 1,
        "benchmark_cagr": bench[-1] ** annual_power - 1,
        "relative_excess_cagr": relative[-1] ** annual_power - 1,
        "strategy_max_drawdown": _max_drawdown(strat),
        "benchmark_max_drawdown": _max_drawdown(bench),
        "relative_max_drawdown": _max_drawdown(relative),
        "sharpe": _ratio(r),
        "information_ratio": _ratio(r, b),
        "annual_volatility": statistics.stdev(r) * math.sqrt(ANNUAL_SESSIONS),
        "mean_daily_net_return": _mean(r),
        "mean_daily_active_return": _mean([x-y for x, y in zip(r, b)]),
        "account_return_max_error": max(abs(x) for x in delta),
        "mean_turnover": _mean(values["turnover"]),
        "total_cost_sum": math.fsum(values["cost"]),
    }
    sensitivity = {
        "sharpe_238_rf0_ddof1": _ratio(r),
        "sharpe_252_rf0_ddof1": _ratio(r, sessions=252),
        "sharpe_238_rf2pct_ddof1": _ratio(r, annual_rf=0.02),
        "sharpe_238_rf0_ddof0": _mean(r) / statistics.pstdev(r) * math.sqrt(238) if statistics.pstdev(r) else None,
    }
    diagnostics = {
        "max_account_return_delta": result["account_return_max_error"],
        "max_value_plus_cash_discrepancy": max(abs(a[i] - values["value"][i] - values["cash"][i]) for i in range(n)),
        "total_cost_absolute_sum": math.fsum(values["total_cost"]),
        "max_abs_net_return_date": dates[max(range(n), key=lambda i: abs(r[i]))].isoformat(),
        "max_net_return": max(r),
        "min_net_return": min(r),
    }
    daily = [
        {"date": dates[i].isoformat(), "account": a[i], "reported_net": r[i],
         "account_implied_net": implied[i], "difference": delta[i], "benchmark": b[i],
         "cost_rate": values["cost"][i], "total_cost": values["total_cost"][i],
         "cash": values["cash"][i], "value": values["value"][i], "turnover": values["turnover"][i]}
        for i in range(n)
    ]
    return result, sensitivity, diagnostics, daily


def load_calendar(path):
    days = [date.fromisoformat(x.strip()[:10]) for x in path.read_text().splitlines() if x.strip()]
    if days != sorted(set(days)):
        raise ValueError("reference calendar must be strictly increasing and unique")
    return days


def audit_one(item, snapshot, artifact_root, calendar, out_dir):
    label = f"{item['cohort']}_phase{item['phase']:02d}"
    source = Path(item["expected_report"]["path"])
    volume_prefix = Path("/vol/csi1000_stage_b") / snapshot
    rel = source.relative_to(volume_prefix)
    path = artifact_root / rel
    row = {"cohort": item["cohort"], "phase": item["phase"], "source_path": str(source),
           "local_path": str(path), "expected_sha256": item["expected_report"]["sha256"],
           "original": item["reported_metrics"], "recomputed": None, "comparison": None,
           "metric_formula": "BLOCKED", "daily_account": "BLOCKED", "execution_integrity": "BLOCKED"}
    if not path.is_file():
        row["reason"] = "raw report bytes unavailable"
        return row
    actual = file_sha256(path)
    row["actual_sha256"] = actual
    if actual != row["expected_sha256"]:
        row["metric_formula"] = "FAIL"
        row["reason"] = "raw Parquet SHA256 mismatch"
        return row
    try:
        import pandas as pd
        frame = pd.read_parquet(path)
        if list(frame.columns) != item["expected_report"]["columns"] or set(frame.columns) != set(REQUIRED):
            raise ValueError("schema does not match manifest")
        if len(frame) != item["expected_report"]["rows"]:
            raise ValueError("row count does not match manifest")
        if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
            raise ValueError("dates duplicate or not sorted")
        dates = [x.date() for x in pd.DatetimeIndex(frame.index)]
        if len(dates) != len(set(dates)):
            raise ValueError("duplicate dates in one session")
        values = {k: [float(v) for v in frame[k]] for k in REQUIRED}
        if any(not math.isfinite(x) for v in values.values() for x in v):
            raise ValueError("NaN or infinite column values")
        if any(x <= 0 for x in values["account"]):
            raise ValueError("nonpositive account")
        if any(x < 0 for x in values["cost"] + values["total_cost"] + values["turnover"]):
            raise ValueError("negative transaction costs or turnover")
        if any(values["return"][i] - values["cost"][i] <= -1 or values["bench"][i] <= -1 for i in range(len(dates))):
            raise ValueError("return at or below -100 percent")
        expected = None if calendar is None else [d for d in calendar if date(2025, 1, 2) <= d <= date(2026, 9, 30)]
        if expected is not None and dates != expected:
            raise ValueError("raw report index does not equal original trading calendar")
        numbers, sensitivity, diagnostics, daily = metrics(dates, values)
        row["recomputed"] = numbers
        row["sensitivity"] = sensitivity
        row["diagnostics"] = diagnostics
        errors = {}
        for k, old in item["reported_metrics"].items():
            if k not in numbers or old is None:
                continue
            new = numbers[k]
            tol = 1e-8 if k.startswith("mean_daily") else (1e-10 if k == "account_return_max_error" else 1e-6)
            if isinstance(old, (float, int)):
                errors[k] = {"saved": old, "recomputed": new, "absolute_error": abs(old - new), "tolerance": tol,
                             "within_tolerance": abs(old - new) <= tol}
        row["comparison"] = errors
        wrong = [k for k, v in errors.items() if not v["within_tolerance"]]
        if diagnostics["max_account_return_delta"] > 1e-10:
            row["daily_account"] = "FAIL"
            wrong.append("account_vs_daily_net")
        else:
            row["daily_account"] = "INCONCLUSIVE"  # Account/net agrees, fill provenance is missing.
        row["metric_formula"] = "FAIL" if wrong else ("PASS" if calendar is not None else "BLOCKED")
        row["reason"] = ("metric mismatches: " + ", ".join(wrong)) if wrong else (
            "calendar missing" if calendar is None else "metrics agree; execution not examined")
        with (out_dir / f"{label}_daily.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(daily[0]))
            writer.writeheader()
            writer.writerows(daily)
    except (ValueError, TypeError, KeyError, ImportError) as exc:
        row["metric_formula"] = "BLOCKED" if isinstance(exc, ImportError) else "FAIL"
        row["reason"] = f"{type(exc).__name__}: {exc}"
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path(__file__).parent / "evidence" / "csi1000_stage_b_frozen_manifest_20261009.json")
    parser.add_argument("--artifact-root", required=True, type=Path,
                        help="Read-only mirror of /vol/csi1000_stage_b/<snapshot>/")
    parser.add_argument("--calendar", type=Path, help="Original provider calendars/day.txt; required for calendar PASS")
    parser.add_argument("--all", action="store_true", help="Check all ten frozen phases; otherwise winner phase 0 only")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    calendar = load_calendar(args.calendar) if args.calendar else None
    args.output.mkdir(parents=True, exist_ok=True)
    items = manifest["phases"] if args.all else [next(x for x in manifest["phases"] if x["cohort"] == "winner" and x["phase"] == 0)]
    rows = [audit_one(i, manifest["snapshot_token"], args.artifact_root, calendar, args.output) for i in items]
    out = {"frozen_commit": manifest["frozen_commit"], "raw_data_scope": "all" if args.all else "winner_phase0",
           "audits": rows, "overall": "FAIL" if any(x["metric_formula"] == "FAIL" for x in rows) else "BLOCKED"}
    (args.output / "comparisons.json").write_text(json.dumps(out, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"overall": out["overall"], "phases": [
        {"cohort": x["cohort"], "phase": x["phase"], "metric_formula": x["metric_formula"], "reason": x["reason"]} for x in rows
    ]}, indent=2))
    return 1 if out["overall"] == "FAIL" else (2 if out["overall"] == "BLOCKED" else 0)


if __name__ == "__main__":
    raise SystemExit(main())
