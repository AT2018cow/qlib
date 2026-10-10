"""Independent CSI1000 Stage-B fixed-execution accounting (NO Exchange/backtest/model).

Read SHA-pinned corrected report/decisions from commit a7e4f22 and load only
OHLCV/factor from the ORIGINAL provider.  This is an accounting verification,
NOT proof of order fills, historical ST, liquidity, PIT or matured labels.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from datetime import date
from pathlib import Path

# Direct-file Modal invocation has no audit package context. Unit tests do.
if __package__:
    from .stage_b_quote_inventory import scan_holding_quotes
else:
    from stage_b_quote_inventory import scan_holding_quotes

BASELINE = "23b92de05cf36c82998de684d0fbf64d81ee54490d755bd3cf96311c00286785"
RUNNER_UP = "c98856b460aba687640d422e85a82a545bf5b6ca932e906a180699e0f6cad19b"
CANDIDATES = (BASELINE, RUNNER_UP)
PHASES = (0, 4, 6, 10, 15)
START, END, DAYS = "2025-01-02", "2026-09-30", 424
CAPITAL = 100_000_000.0
SNAPSHOT = "51756897fc75230493194aef2e48815e4e9f3cc7426cc135f47bb8ba8e0d21e1"
FINGERPRINT = "cd2e68f571c6bd19fbb2b79089e891bb1c43b96ab1fcdb4d2b2a9b1fdd9ce5d5"
# Independently checked SHA256 of committed 55-phase batch_result.json.
BATCH_SHA = "ce0075f5d8c73325a12a4ecdf8fa4be856443a6fc1bac933f3b82d5c5035ff1c"
CNY_TOL = 0.01
RATE_TOL = 1e-10

# Explicitly documented, date-scoped exceptions. Never infer a suspension
# merely because a bar is missing from the research provider.
CHENMING_NOTICE = ("https://disc.static.szse.cn/download/disc/disk03/finalpage/"
                   "2025-02-19/b0f90573-61df-4bcc-953f-edf8738c84e1.PDF")
DALI_NOTICE = "https://static.cninfo.com.cn/finalpage/2025-04-26/1223329081.PDF"
VERIFIED_SUSPENSION_DATES = {
    ("2025-02-20", "SZ000488"): CHENMING_NOTICE,
    ("2025-04-28", "SZ002214"): DALI_NOTICE,
}
KNOWN_ST_EXECUTION_REVIEW = {
    ("2025-02-21", "SZ000488"): {
        "issue": "HISTORIC_ST_5PCT_LIMIT_SELL_NOT_EXTERNALLY_VALIDATED",
        "notice": CHENMING_NOTICE,
    },
    ("2025-04-29", "SZ002214"): {
        "issue": "HISTORIC_ST_5PCT_LIMIT_SELL_NOT_EXTERNALLY_VALIDATED",
        "notice": DALI_NOTICE,
    },
}


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise ValueError("independent-ledger BLOCKED/FAIL: " + reason)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for piece in iter(lambda: f.read(1024 * 1024), b""):
            h.update(piece)
    return h.hexdigest()


def checked(path: Path, expected: str, label: str) -> dict:
    require(path.is_file(), f"{label}: missing")
    actual = sha256(path)
    require(actual == expected, f"{label}: SHA256 mismatch")
    return {"sha256": actual, "bytes": path.stat().st_size}


def load_plan(repo: Path, candidate: str, phase: int):
    require(candidate in CANDIDATES and phase in PHASES, "unapproved candidate/phase")
    batch_file = repo / "audit/evidence/stage_b_corrected_rerank/batch_result.json"
    checked(batch_file, BATCH_SHA, "batch_result")
    batch = json.loads(batch_file.read_text())
    require(batch["original_snapshot_token"] == SNAPSHOT and
            batch["original_provider_fingerprint"] == FINGERPRINT and
            batch["completed_phase_replays"] == 55 and
            batch["training_performed"] is False, "batch provenance drift")
    rows = [x for x in batch["phase_comparisons"] if x["candidate_id"] == candidate and x["phase"] == phase]
    require(len(rows) == 1, "missing or repeated phase comparison")
    row = rows[0]
    directory = repo / "audit/evidence/stage_b_corrected_rerank" / f"{candidate[:16]}_p{phase:02d}"
    report = directory / "fixed_diagnostic_report.parquet"
    decisions = directory / "fixed_diagnostic_decisions.json"
    identities = {
        "report": checked(report, row["corrected_report_sha256"], "fixed_report"),
        "decisions": checked(decisions, row["corrected_decisions_sha256"], "fixed_decisions"),
    }
    return report, decisions, identities, row


def numeric(value, label: str, *, strictly_positive: bool = False) -> float:
    v = float(value)
    require(math.isfinite(v) and (not strictly_positive or v > 0), f"bad {label}")
    return v


def score(dates: list[date], nav: list[float], bench_daily: list[float]) -> dict:
    require(len(nav) == len(dates) == len(bench_daily) == DAYS, "metric calendar mismatch")
    ret = [nav[i] / (CAPITAL if i == 0 else nav[i - 1]) - 1 for i in range(len(nav))]
    vol = statistics.stdev(ret)
    benchmark = math.prod(1 + b for b in bench_daily)
    years = (dates[-1] - date.fromisoformat(START)).days / 365.2425
    high, dd = CAPITAL, 0.0
    for value in nav:
        high = max(high, value)
        dd = min(dd, value / high - 1)
    return {
        "sharpe_238_rf0": (statistics.mean(ret) / vol * math.sqrt(238)) if vol > 0 else None,
        "strategy_cagr": (nav[-1] / CAPITAL) ** (1 / years) - 1,
        "relative_excess_cagr": ((nav[-1] / CAPITAL) / benchmark) ** (1 / years) - 1,
        "strategy_max_drawdown": dd,
        "strategy_total_return": nav[-1] / CAPITAL - 1,
    }


def reconstruct(decisions: list[dict], report, quotes: dict, dates: list[date],
                *, cost_bps_extra=(0, 5, 10, 20)) -> dict:
    """Independent unit bookkeeping. Orders hold Qlib *adjusted* quantities.

    quotes[(date ISO, stock)] = (adjusted open, adjusted close, factor, volume).
    Unknown missing held close fails closed. Only explicitly documented
    suspension dates carry the last valid held mark, as in Qlib Account.
    This is accounting, NOT proof of a later executable sale.
    Qlib Position/Account/Exchange/strategy and their fee helpers NOT imported.
    """
    require(len(decisions) == len(report) == len(dates) == DAYS, "424 daily inputs required")
    assert cost_bps_extra[0] == 0
    held: dict[str, float] = {}
    last_valid_mark: dict[str, float] = {}
    last_valid_mark_date: dict[str, str] = {}
    suspension_marks = []
    st_execution_review = []
    cash = CAPITAL
    fees = turnover = 0.0
    max_diff = {k: 0.0 for k in ("account", "cash", "value", "total_cost", "total_turnover",
                                     "net_return", "cost_rate", "turnover_rate")}
    daily_nav, daily_bench, daily_turnover, daily_notional = [], [], [], []
    first_diff_date: dict[str, str] = {}
    violations: list[dict] = []
    previous_account = CAPITAL
    total_fills = 0
    for i, (decision, day) in enumerate(zip(decisions, dates)):
        today = day.isoformat()
        require(str(decision["start_time"])[:10] == today and
                decision["decision_index"] == i, f"decision clock/index {today}")
        orders = decision["orders"]
        daily_fee = daily_volume = 0.0
        sells_done = False
        for pos, order in enumerate(orders):
            symbol = order["stock_id"]
            direction = order["direction"]
            require(direction in (0, 1), f"direction {today}/{symbol}")
            require(order["order_index"] == pos and
                    str(order["start_time"])[:10] == today, f"order time/index {today}")
            require((today, symbol) not in VERIFIED_SUSPENSION_DATES,
                    f"order on verified suspension {today}/{symbol}")
            require(not sells_done or direction != 0, f"sell after buy {today}")
            if direction == 1:
                sells_done = True
            qty = numeric(order["deal_amount"], "deal amount", strictly_positive=True)
            requested = numeric(order["amount"], "order amount", strictly_positive=True)
            require(math.isclose(qty, requested, abs_tol=1e-6, rel_tol=1e-10),
                    f"partial fill {today}/{symbol}: not independently executable")
            quote = quotes.get((today, symbol))
            require(quote is not None, f"missing trade quote {today}/{symbol}")
            open_px, close_px, factor, volume = quote
            open_px = numeric(open_px, "open", strictly_positive=True)
            factor = numeric(factor, "factor", strictly_positive=True)
            require(close_px is not None and math.isfinite(close_px) and close_px > 0,
                    f"filled order despite missing close {today}/{symbol}")
            recorded_factor = numeric(order["factor"], "order factor", strictly_positive=True)
            require(math.isclose(factor, recorded_factor, abs_tol=1e-8, rel_tol=1e-6),
                    f"factor mismatch {today}/{symbol}")
            notional = qty * open_px
            require(math.isfinite(notional) and notional > 1e-5, "invalid notional")
            fee = max(5.0, notional * (0.0005 if direction == 1 else 0.0015))
            if direction == 0:
                before = held.get(symbol, 0.0)
                require(before > 0 and math.isclose(before, qty, abs_tol=1e-5, rel_tol=1e-9),
                        f"not a full sell of held adjusted shares {today}/{symbol}")
                del held[symbol]
                last_valid_mark.pop(symbol, None)
                last_valid_mark_date.pop(symbol, None)
                cash += notional - fee
            else:
                require(cash + 1e-5 >= notional + fee, f"negative cash buy {today}/{symbol}")
                held[symbol] = held.get(symbol, 0.0) + qty
                cash -= notional + fee
            if volume is None or not math.isfinite(float(volume)) or float(volume) <= 0:
                violations.append({"date": today, "code": symbol, "issue": "missing_or_zero_volume"})
            else:
                # No capacity claim: source $volume unit/auction participation unverified.
                pass
            flagged = KNOWN_ST_EXECUTION_REVIEW.get((today, symbol))
            if flagged:
                st_execution_review.append({
                    "date": today, "stock_id": symbol,
                    "direction": "SELL" if direction == 0 else "BUY",
                    "issue": flagged["issue"], "notice": flagged["notice"],
                    "status": "BLOCKED_MARKET_EXECUTION_NOT_CERTIFIED",
                })
            daily_fee += fee
            daily_volume += notional
            total_fills += 1
        stock_value = 0.0
        for symbol, shares in held.items():
            quote = quotes.get((today, symbol))
            close_px = quote[1] if quote is not None else None
            verified_notice = VERIFIED_SUSPENSION_DATES.get((today, symbol))
            if verified_notice is not None:
                require(close_px is None or not math.isfinite(float(close_px)),
                        f"verified suspension conflicts with quoted close {today}/{symbol}")
                require(symbol in last_valid_mark,
                        f"verified suspension has no prior held mark {today}/{symbol}")
                mark = last_valid_mark[symbol]
                suspension_marks.append({
                    "date": today, "stock_id": symbol,
                    "prior_mark_date": last_valid_mark_date[symbol],
                    "valuation": "CARRY_LAST_VALID_HELD_CLOSE",
                    "notice": verified_notice,
                })
            else:
                require(close_px is not None and math.isfinite(float(close_px)) and
                        float(close_px) > 0,
                        f"unknown missing held close {today}/{symbol}; no blanket forward fill")
                mark = float(close_px)
                last_valid_mark[symbol] = mark
                last_valid_mark_date[symbol] = today
            stock_value += shares * mark
        account = cash + stock_value
        require(cash >= -1e-4 and account > 0, f"negative cash or NAV {today}")
        fees += daily_fee
        turnover += daily_volume
        values = {
            "account": account, "cash": cash, "value": stock_value,
            "total_cost": fees, "total_turnover": turnover,
            "net_return": account / previous_account - 1,
            "cost_rate": daily_fee / previous_account,
            "turnover_rate": daily_volume / previous_account,
        }
        expected = report.iloc[i]
        required = {
            "account": "account", "cash": "cash", "value": "value",
            "total_cost": "total_cost", "total_turnover": "total_turnover",
            "cost_rate": "cost", "turnover_rate": "turnover",
        }
        for key, col in required.items():
            diff = abs(values[key] - numeric(expected[col], col))
            max_diff[key] = max(max_diff[key], diff)
            threshold = RATE_TOL if key in ("cost_rate", "turnover_rate") else CNY_TOL
            if diff > threshold and key not in first_diff_date:
                first_diff_date[key] = today
        daily_report_net = numeric(expected["return"], "return") - numeric(expected["cost"], "cost")
        net_error = abs(values["net_return"] - daily_report_net)
        max_diff["net_return"] = max(max_diff["net_return"], net_error)
        if net_error > RATE_TOL and "net_return" not in first_diff_date:
            first_diff_date["net_return"] = today
        daily_nav.append(account)
        daily_bench.append(numeric(expected["bench"], "bench"))
        daily_notional.append(daily_volume)
        daily_turnover.append(daily_volume / previous_account)
        previous_account = account
    errors = [x for x, v in max_diff.items() if v > (RATE_TOL if x in ("net_return", "cost_rate", "turnover_rate") else CNY_TOL)]
    # Cost-only sensitivity on *frozen* fills. NOT an executable re-simulation;
    # no re-sizing, portfolio re-ranking or market-impact/slippage forecast.
    stresses = {}
    for bps in cost_bps_extra:
        adjustment = 0.0
        stressed_nav = []
        for a, v in zip(daily_nav, daily_notional):
            adjustment += v * bps / 10000
            stressed_nav.append(a - adjustment)
        if min(stressed_nav) <= 0:
            stresses[str(bps)] = {"status": "BLOCKED_NEGATIVE_STRESSED_NAV"}
        else:
            stresses[str(bps)] = score(dates, stressed_nav, daily_bench)
    return {
        "ledger": "FAIL" if errors else "PASS_RESEARCH_ACCOUNTING_ONLY",
        "fail_fields": errors, "first_diff_date": first_diff_date, "max_abs_diff": max_diff,
        "matched_trading_days": len(dates), "filled_orders": total_fills,
        "daily_net_notional_summary": {"total": turnover, "avg_turnover_ratio": statistics.mean(daily_turnover)},
        "reported_price_volume_warnings": violations[:25],
        "reported_price_volume_warning_count": len(violations),
        "verified_suspension_marks": suspension_marks,
        "verified_suspension_marks_count": len(suspension_marks),
        "known_st_execution_review": st_execution_review,
        "known_st_execution_review_count": len(st_execution_review),
        "computed_metrics": score(dates, daily_nav, daily_bench),
        "extra_cost_bps_on_original_notional_not_new_fills": stresses,
        "external_fill_liquidity_st_ipo_pit_maturity": "BLOCKED_NOT_TESTED",
    }


def provider_prices(provider: Path, codes: list[str], expected_calendar: list[str]) -> dict:
    """Use Qlib solely as a field reader; no Qlib Exchange/backtest/Account.

    Passing a static code list avoids the dynamic-member quote-coverage F1 bug.
    disk_cache=False prevents writing provider disk-cache files.
    """
    import pandas as pd
    import qlib
    from qlib.data import D

    qlib.init(provider_uri=str(provider), region="cn")
    calendar = [str(x)[:10] for x in D.calendar(start_time=START, end_time=END)]
    require(calendar == expected_calendar, "provider calendar differs from 424 frozen days")
    frames = D.features(codes, ["$open", "$close", "$factor", "$volume"],
                        start_time=START, end_time=END, freq="day", disk_cache=False)
    require(isinstance(frames, pd.DataFrame) and not frames.empty, "empty provider features")
    require(not frames.index.has_duplicates, "duplicate provider instrument/date rows")
    rows = {}
    for (symbol, timestamp), q in frames.iterrows():
        dt = str(timestamp)[:10]
        def optional(v):
            return float(v) if pd.notna(v) else None
        rows[(dt, str(symbol))] = tuple(optional(q[k]) for k in ("$open", "$close", "$factor", "$volume"))
    return rows


def run(args):
    import pandas as pd

    repo = args.repo_root.resolve()
    provider = args.provider_uri.resolve()
    out = args.output_dir.resolve()
    original_snapshot = args.provider_snapshot.resolve()
    require(provider.is_dir() and original_snapshot.is_file(), "original read-only provider/snapshot missing")
    for forbidden in (repo, provider, original_snapshot.parent, Path("/vol")):
        require(not out.is_relative_to(forbidden), "output inside input/repo/Volume forbidden")
    require(not out.exists(), "output already exists; no overwrite")
    snapshot = json.loads(original_snapshot.read_text())
    require(snapshot.get("snapshot_token") == SNAPSHOT and
            snapshot.get("provider_fingerprint") == FINGERPRINT, "provider snapshot mismatch")
    # Source code does not train; no snapshot/Volume writes.
    candidate_set = CANDIDATES if args.candidate == "both" else (BASELINE if args.candidate == "baseline" else RUNNER_UP,)
    phases = PHASES if args.phases == "all" else (0,)
    tasks = []
    all_codes = set()
    for candidate in candidate_set:
        for phase in phases:
            report_path, decisions_path, identity, row = load_plan(repo, candidate, phase)
            report = pd.read_parquet(report_path)
            decisions = json.loads(decisions_path.read_text())
            dates = [x.date() for x in pd.DatetimeIndex(report.index)]
            require(len(dates) == DAYS and len(set(dates)) == DAYS and
                    dates[0].isoformat() == START and dates[-1].isoformat() == END and
                    list(report.index) == sorted(report.index), "report coverage/order")
            require(len(decisions) == DAYS, "decisions length drift")
            all_codes.update(o["stock_id"] for d in decisions for o in d["orders"])
            tasks.append((candidate, phase, dates, report, decisions, identity, row))
    # One provider read shared by all 10 cells; avoid repeated market scans.
    quotes = provider_prices(provider, sorted(all_codes), [x.isoformat() for x in tasks[0][2]])
    # Full sweep precedes all valuation, so a later unknown suspension cannot
    # hide additional missing holding quotes behind the first exception.
    inventory_by_cell = []
    for candidate, phase, dates, report, decisions, identity, row in tasks:
        require(dates == tasks[0][2], "cross-phase calendar disagreement")
        inventory_by_cell.append({
            "candidate_id": candidate, "phase": phase,
            "fixed_sha256": identity,
            "inventory": scan_holding_quotes(
                decisions, quotes, dates,
                VERIFIED_SUSPENSION_DATES, KNOWN_ST_EXECUTION_REVIEW),
        })
    blocked = [x for x in inventory_by_cell if x["inventory"]["blocking_count"]]
    if blocked:
        out.mkdir(parents=True)
        payload = {
            "status": "BLOCKED_OR_FAILED_NOT_PASS",
            "reason": "full-calendar quote inventory has unresolved blocking rows; no NAV PASS",
            "snapshot": SNAPSHOT, "provider_fingerprint": FINGERPRINT,
            "candidate": args.candidate, "phases": args.phases,
            "inventory_cells": inventory_by_cell,
            "blocking_cells": len(blocked),
            "market_execution": "NOT_CERTIFIED",
            "model_training": False, "production_promotion": False,
        }
        (out / "audit_failure.json").write_text(
            json.dumps(payload, indent=2, allow_nan=False) + "\n")
        raise ValueError("unresolved frozen holding quote inventory; see audit_failure.json")

    outputs = []
    for task, inv in zip(tasks, inventory_by_cell):
        candidate, phase, dates, report, decisions, identity, row = task
        result = reconstruct(decisions, report, quotes, dates)
        result["full_calendar_quote_inventory"] = inv["inventory"]
        report_metrics = row["fixed_metrics"]
        metric_aliases = {"sharpe_238_rf0": "sharpe", "strategy_cagr": "strategy_cagr",
                          "relative_excess_cagr": "relative_excess_cagr",
                          "strategy_max_drawdown": "strategy_max_drawdown",
                          "strategy_total_return": "strategy_total_return"}
        result["metrics_comparison"] = {
            name: {"independent": result["computed_metrics"][name], "saved": report_metrics[reference],
                   "difference": abs(result["computed_metrics"][name] - report_metrics[reference])}
            for name, reference in metric_aliases.items()
            if result["computed_metrics"][name] is not None
        }
        # Compare arithmetic-derived metrics with saved corrected metrics, not just daily NAV.
        drift = [k for k, v in result["metrics_comparison"].items()
                 if v["difference"] > 1e-6]
        if drift:
            result["ledger"] = "FAIL"
            result["fail_fields"].extend("saved_metric_" + k for k in drift)
        result.update({"candidate_id": candidate, "phase": phase, "fixed_sha256": identity})
        outputs.append(result)
    cohort_summaries = {}
    for candidate in candidate_set:
        group = [x for x in outputs if x["candidate_id"] == candidate]
        if len(group) == len(PHASES) and all(x["ledger"] == "PASS_RESEARCH_ACCOUNTING_ONLY" for x in group):
            cohort_summaries[candidate] = {
                "worst_relative_excess_cagr": min(x["computed_metrics"]["relative_excess_cagr"] for x in group),
                "median_sharpe": statistics.median(x["computed_metrics"]["sharpe_238_rf0"] for x in group),
                "phase_ids": [x["phase"] for x in group],
                "status": "DIAGNOSTIC_ON_CONSUMED_TAIL_NOT_BEST_STRATEGY_CERTIFICATION",
            }
    out.mkdir(parents=True)
    payload = {"audit": "independent_price_to_cash_nav_no_qlib_backtest",
               "snapshot": SNAPSHOT, "provider_fingerprint": FINGERPRINT,
               "cells": outputs, "phase_completeness": len(outputs),
               "quote_inventory_completed_before_ledger": True,
               "cohort_summaries": cohort_summaries,
               "all_research_accounting_pass": all(c["ledger"] == "PASS_RESEARCH_ACCOUNTING_ONLY" for c in outputs),
               "market_execution": "BLOCKED_EXTERNAL_HISTORICAL_RULES_AND_LIQUIDITY",
               "parameter_search": False, "model_training": False, "production_promotion": False,
               "selection_on_consumed_tail": "DIAGNOSTIC_ONLY",
               "independent_benchmark_price_audit": "BLOCKED_SAVED_BENCH_RETURNS_USED"}
    (out / "independent_ledger.json").write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n")
    if not payload["all_research_accounting_pass"]:
        raise ValueError("independent ledger has mismatches; see isolated JSON output")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--provider-uri", type=Path, required=True)
    parser.add_argument("--provider-snapshot", type=Path, required=True)
    parser.add_argument("--candidate", choices=("baseline", "runnerup", "both"), default="baseline")
    parser.add_argument("--phases", choices=("phase0", "all"), default="phase0")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run(args)
    except Exception as exc:
        # A failed preflight used to leave no artifact; preserve a minimal,
        # sanitized failure record only in an isolated, never-reused output dir.
        # Never write even a failure report to /vol, provider, snapshot or repo.
        out = args.output_dir.resolve()
        prohibited = (args.repo_root.resolve(), args.provider_uri.resolve(),
                      args.provider_snapshot.resolve().parent, Path("/vol"))
        if not out.exists() and not any(out.is_relative_to(p) for p in prohibited):
            out.mkdir(parents=True, exist_ok=False)
            (out / "audit_failure.json").write_text(
                json.dumps({"status": "BLOCKED_OR_FAILED_NOT_PASS",
                            "error_type": type(exc).__name__,
                            "reason": str(exc)[:1000],
                            "candidate": args.candidate,
                            "phases": args.phases,
                            "market_execution": "NOT_CERTIFIED"},
                           indent=2, allow_nan=False) + "\n"
            )
        raise
    print(json.dumps({"cells": len(result["cells"]), "ledger_match": result["all_research_accounting_pass"],
                      "market_execution": result["market_execution"], "output": "independent_ledger.json"}))


if __name__ == "__main__":
    main()
