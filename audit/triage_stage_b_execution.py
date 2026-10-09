"""Urgent Stage-B decision triage from TEN immutable original execution reports.

Measures actual zero-trading periods in every frozen Parquet and recomputes
historical metrics. Detects selection invalidity without pretending to repair
nine missing replay paths. Does not train, tune, choose new parameters, access
Modal, or modify production / frozen evidence.

Run: python -m audit.triage_stage_b_execution --repo-root .
Dependencies: pandas, pyarrow (for original frozen Parquet only).
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import date
from pathlib import Path

import pandas as pd

from audit.independent_stage_b_metrics import REQUIRED, metrics
from audit.stage_b_f6_preflight import BASELINE, FROZEN, WINNER, preflight

START = "2025-07-02"
END = "2026-06-29"
CHECK_METRICS = ("sharpe", "strategy_cagr", "strategy_max_drawdown")
TOLERANCE = 1e-5
CNY_TURNOVER_TOL = 0.01


def require(ok, reason):
    if not ok:
        raise ValueError("Stage-B triage FAIL: " + reason)


def longest_true_run(flags: list[bool]) -> int:
    longest = current = 0
    for flag in flags:
        current = current + 1 if flag else 0
        longest = max(current, longest)
    return longest


def periods_of_no_trading(report: pd.DataFrame) -> tuple[list[bool], list[float]]:
    totals = report["total_turnover"].astype(float).tolist()
    ratios = report["turnover"].astype(float).tolist()
    require(all(math.isfinite(x) and x >= 0 for x in totals + ratios),
            "nonfinite or negative frozen turnover")
    increments = [totals[0]] + [b - a for a, b in zip(totals, totals[1:])]
    require(all(x >= -CNY_TURNOVER_TOL for x in increments),
            "frozen cumulative turnover decreases")
    zero = [abs(x) <= CNY_TURNOVER_TOL for x in increments]
    rate_zero = [x <= 1e-12 for x in ratios]
    require(zero == rate_zero, "cumulative-turnover and daily-turnover rates disagree")
    return zero, increments


def analyze(root: Path):
    root = Path(root).resolve()
    inventory = preflight(root)
    require(inventory["report_hashes_verified"] == 10, "report SHA gate incomplete")
    frozen = json.loads((root / FROZEN).read_text())
    candidates = {c["candidate_id"]: c for c in frozen["ranked_candidates"]}
    fixed = json.loads((root / "audit/evidence/winner_phase0_fixed/comparison.json").read_text())
    rows = []
    shared_calendar = None
    for item in inventory["rows"]:
        candidate = candidates[item["candidate_id"]]
        phase = next(p for p in candidate["phase_results"] if p["phase"] == item["phase"])
        report = pd.read_parquet(root / "results/csi1000_stage_b/audit_reports" / item["report"]["export"])
        require(set(REQUIRED).issubset(report.columns), "missing frozen report columns")
        dates = pd.DatetimeIndex(report.index)
        require(len(dates) == 424 and dates.is_monotonic_increasing and
                dates.is_unique and len(report) == phase["n_days"],
                "frozen report date range or coverage drift")
        day_labels = [x.strftime("%Y-%m-%d") for x in dates]
        if shared_calendar is None:
            shared_calendar = day_labels
        require(day_labels == shared_calendar, "candidate calendars differ")
        values = {column: report[column].astype(float).tolist() for column in REQUIRED}
        calculated, _, _, _ = metrics([x.date() for x in dates], values)
        for key in CHECK_METRICS:
            require(abs(calculated[key] - float(phase[key])) <= TOLERANCE,
                    f"{item['candidate']} phase{item['phase']:02d}: frozen {key} arithmetic mismatch")
        zero, increments = periods_of_no_trading(report)
        in_stall = [START <= d <= END for d in day_labels]
        require(sum(in_stall) == 240, "F1 candidate comparison date span changed")
        stalled = [z for z, selected in zip(zero, in_stall) if selected]
        frozen_interval_zero = sum(stalled)
        run = longest_true_run(zero)
        anomaly = (run >= 20 or frozen_interval_zero >= 60)
        rows.append({
            "cohort": item["candidate"],
            "phase": item["phase"],
            "original_order_count": phase["decision_artifact"]["order_count"],
            "original_sharpe": calculated["sharpe"],
            "original_cagr": calculated["strategy_cagr"],
            "original_max_drawdown": calculated["strategy_max_drawdown"],
            "original_mean_turnover": calculated["mean_turnover"],
            "original_cost_fraction": calculated["total_cost_sum"],
            "zero_turnover_days_total": sum(zero),
            "longest_consecutive_zero_turnover_sessions": run,
            "zero_turnover_days_within_f1_240_session_window": frozen_interval_zero,
            "nonzero_turnover_days_within_f1_window": 240 - frozen_interval_zero,
            "material_legacy_execution_anomaly": anomaly,
            "post_f1_fixed_replay": (
                "ALREADY_VERIFIED_DIAGNOSTIC" if item["candidate"] == "winner"
                and item["phase"] == 0 else "BLOCKED_ORIGINAL_SIGNAL_DECISIONS"
            ),
            "frozen_report_sha_verified": True,
        })
    require(len(rows) == 10, "ten-phase coverage")
    winner0 = next(x for x in rows if x["cohort"] == "winner" and x["phase"] == 0)
    require(winner0["zero_turnover_days_within_f1_240_session_window"] == 240,
            "known F1 240-session original trade halt not reproduced")
    require(winner0["original_order_count"] == 482, "known original winner phase0 order count drift")
    require(abs(winner0["original_sharpe"] - fixed["legacy_metrics"]["sharpe"]) <= 1e-11,
            "legacy F1 reference Sharpe drift")
    corrected_sharpe = fixed["fixed_metrics"]["sharpe"]
    corrected_cagr = fixed["fixed_metrics"]["strategy_cagr"]
    invalid_old_ranking = True  # Known confirmed F1 survivor-bias execution defect.
    report = {
        "schema": "csi1000_stage_b_rapid_execution_decision_v1",
        "scope": "read_only_frozen_reports_not_nine_corrected_replays",
        "rows_checked": 10,
        "original_report_sha_verified": inventory["report_hashes_verified"],
        "original_signal_decision_pairs_sha_ready": inventory["source_pairs_ready"],
        "material_zero_turnover_anomaly_rows": sum(x["material_legacy_execution_anomaly"] for x in rows),
        "original_execution_assumption_confirmed_invalid": True,
        "winner_phase0_fixed_sharpe": corrected_sharpe,
        "winner_phase0_fixed_cagr": corrected_cagr,
        "winner_phase0_sharpe_delta_fixed_minus_legacy":
            corrected_sharpe - winner0["original_sharpe"],
        "winner_phase0_cagr_delta_fixed_minus_legacy":
            corrected_cagr - winner0["original_cagr"],
        "original_winner_ranking_valid_for_promotion": not invalid_old_ranking,
        "same_frozen_tail_reused_for_parameter_search_allowed": False,
        "next_decision": "CORRECTED_SAME_PARAMS_RERANK_NEEDED_BEFORE_ANY_RETUNING",
        "retune_now": "NO",
        "retune_trigger": (
            "Only after original candidates are fairly evaluated with repaired execution, "
            "external execution/PIT evidence assessed and a NEW untouched validation window "
            "or new independent split is preregistered; never optimize on the consumed tail"
        ),
        "remaining_nine_corrected_sharpes": "UNKNOWN_NOT_ESTIMATED",
        "real_market_execution": "BLOCKED",
        "rows": rows,
    }
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--out", type=Path, default=None,
                        help="optional new JSON path outside repo and /vol (no overwrite)")
    args = parser.parse_args()
    result = analyze(args.repo_root)
    content = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if args.out is None:
        print(content, end="")
    else:
        target = args.out.resolve()
        require(not target.exists(), "cannot overwrite prior decision evidence")
        require(not target.is_relative_to(Path("/vol")), "no write under /vol")
        require(not target.is_relative_to(args.repo_root.resolve()),
                "no write inside source checkout")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


if __name__ == "__main__":
    main()
