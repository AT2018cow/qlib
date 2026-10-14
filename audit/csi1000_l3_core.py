"""CSI1000 L3 structural search core: retrain-frequency and label-horizon experiments.

Pure Python: no Modal, no Qlib provider, no model fitting. Defines the frozen
L3 protocol (contract 53, section 5), the frequency-parameterized retraining
calendar, PIT label specifications with maturity/purge rules, fit-budget math
and the advance decision. The executable runner lives in
:mod:`audit.csi1000_l3_modal`.

Status: NOT EXECUTED. Merging this code approves no spend, runs no search and
changes no production profile.

Phase convention: retrain calendars differ per frequency, so phase integers
are NOT comparable across frequencies. Within one evaluation, challenger and
baseline legs are compared perturbation-wise (first screen phase against
first, second against second) over the same shared 424-day execution window
and the same saved benchmark. Frequencies, phases and horizons below are the
pre-registered values; anything else is a new protocol version.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audit.csi1000_layered_search_core import (
    BASELINE_CANDIDATE,
    EXECUTION_SCHEMA,
    FEE_BUY_BP,
    FEE_MIN_CNY,
    FEE_SELL_BP,
    INITIAL_CASH_CNY,
    TOPK_BASELINE,
    DROPN_BASELINE,
    require,
)
from qlib_audit_fixes import last_matured_sample

PROTOCOL_VERSION = "csi1000_l3_structural_v1"

# Frozen anchor: original Baseline model, Top20/Drop2. Substituting an
# L1/L2-screened leader requires a separately versioned plan.
ANCHOR_CANDIDATE = BASELINE_CANDIDATE
ANCHOR_TOPK = TOPK_BASELINE
ANCHOR_DROPN = DROPN_BASELINE
ANCHOR_MODEL_PARAMS = {
    "learning_rate": 0.1,
    "colsample_bytree": 0.9,
    "lambda_l1": 205.6999,
    "lambda_l2": 580.9768,
    "max_depth": 8,
    "num_leaves": 250,
    "min_data_in_leaf": 20,
}

# Frozen training geometry (unchanged dimensions; only frequency/horizon vary).
TRAIN_START = "2016-01-01"
VALIDATION_SESSIONS = 252
EXECUTION_START = "2025-01-02"
EXECUTION_END = "2026-09-30"
SIGNAL_ANCHOR = "2024-12-31"

# Pre-registered screen variants: (label_horizon, retrain_frequency, phases).
STAGE1_VARIANTS = [
    {"label_horizon": 20, "retrain_frequency": 10, "screen_phases": [0, 5]},
    {"label_horizon": 20, "retrain_frequency": 40, "screen_phases": [0, 20]},
]
STAGE2_VARIANTS = [
    {"label_horizon": 10, "retrain_frequency": 20, "screen_phases": [0, 10]},
    {"label_horizon": 40, "retrain_frequency": 20, "screen_phases": [0, 10]},
]
BASELINE_CONTROL = {"label_horizon": 20, "retrain_frequency": 20, "screen_phases": [0, 10]}
ALLOWED_HORIZONS = (10, 20, 40)

# Five-phase finals calendars per frequency (contract 53 section 5). L4 scope;
# recorded here so phase sets cannot drift silently between stages.
FINALS_PHASES = {10: [0, 2, 3, 5, 7], 20: [0, 4, 6, 10, 15], 40: [0, 8, 12, 20, 30]}

# Budgets (contract 53 section 2, L3 sub-cap).
L3_FIT_CAP = 250
L3_USD_CAP = 6.0
L3_MAX_ADVANCE = 1
L3_WORKER_CAP = 4


def label_spec(horizon: int) -> Dict[str, Any]:
    """PIT label specification for one horizon (raw future return, no lookahead).

    The expression resolves ``horizon`` *trading* bars forward; maturity and
    purge for the same horizon are enforced separately at fit time. Anything
    outside the pre-registered horizons is a new protocol version.
    """
    require(horizon in ALLOWED_HORIZONS, f"label horizon {horizon} not pre-registered")
    return {"expression": f"Ref($close, -{horizon})/$close - 1", "horizon": horizon}


def build_l3_retrain_schedule(
    calendar: List[str],
    *,
    frequency: int,
    phase: int,
    anchor: str = SIGNAL_ANCHOR,
    execution_end: str = EXECUTION_END,
) -> List[Dict[str, Any]]:
    """Retraining lineage for one (frequency, phase) structural lineage.

    Mirrors the frozen freq20 geometry: start ``phase`` sessions before the
    anchor, then step ``frequency`` sessions, each slice covering
    ``[asof, signal_end]`` with ``signal_end`` clipped to the last execution
    session. ``phase`` must be a pre-registered offset strictly below the
    frequency so the two screen phases never coincide or exceed one period.
    """
    require(
        isinstance(calendar, list) and calendar == sorted(set(calendar)) and calendar,
        "trading calendar must be sorted, unique and non-empty",
    )
    require(isinstance(frequency, int) and frequency in (10, 20, 40), "retrain frequency not pre-registered")
    require(isinstance(phase, int) and 0 <= phase < frequency, "phase must satisfy 0 <= phase < frequency")
    require(
        anchor in calendar and execution_end in calendar and anchor <= execution_end,
        "anchor/execution window outside calendar",
    )
    anchor_i = calendar.index(anchor)
    last_signal_i = calendar.index(execution_end) - 1
    first_i = anchor_i - phase
    require(first_i >= 0, "insufficient history for requested phase")
    jobs = []
    i = first_i
    while i <= last_signal_i:
        signal_end_i = min(i + frequency - 1, last_signal_i)
        jobs.append(
            {"frequency": frequency, "phase": phase, "retrain_asof": calendar[i], "signal_end": calendar[signal_end_i]}
        )
        i += frequency
    require(jobs, "phase generated no retraining jobs")
    return jobs


def training_slice(
    calendar: List[str], *, asof: str, horizon: int, validation_sessions: int = VALIDATION_SESSIONS
) -> Dict[str, str]:
    """PIT train/valid split for one retraining as-of with label maturity.

    Validation ends ``horizon`` sessions before the as-of so every validation
    label is mature; training ends at the last matured sample before the
    validation start. Insufficient history is BLOCKED, never silently
    shortened (a short history would leak future information into training).
    """
    require(horizon in ALLOWED_HORIZONS, f"label horizon {horizon} not pre-registered")
    require(asof in calendar, "retrain as-of outside calendar")
    asof_i = calendar.index(asof)
    valid_end_i = asof_i - horizon - 1
    valid_start_i = valid_end_i - validation_sessions + 1
    require(valid_start_i > 0, f"insufficient matured history before {asof} for horizon {horizon}")
    train_end = last_matured_sample(calendar, calendar[valid_start_i], horizon)
    return {
        "train_start": TRAIN_START,
        "train_end": train_end,
        "valid_start": calendar[valid_start_i],
        "valid_end": calendar[valid_end_i],
        "asof": asof,
    }


def estimate_variant_fits(schedule: List[Dict[str, Any]]) -> int:
    """One genuine fit per retraining slice; no sharing across lineages."""
    require(len(schedule) > 0, "empty retraining schedule")
    return len(schedule)


def variant_key(variant: Dict[str, Any], phase: int) -> str:
    """Deterministic cell identity for one structural (variant, phase) cell."""
    require(phase in variant["screen_phases"], "phase not in variant screen set")
    return (
        f"l3_r{variant['label_horizon']}_f{variant['retrain_frequency']}"
        f"_p{phase:02d}_k{ANCHOR_TOPK}_d{ANCHOR_DROPN}"
    )


def make_l3_plan() -> Dict[str, Any]:
    """Freeze the L3 structural plan (no fitting, no spending)."""
    for variants, stage in ((STAGE1_VARIANTS, "stage1"), (STAGE2_VARIANTS, "stage2")):
        require(len(variants) == 2, f"L3 {stage} must pre-register exactly two variants")
        for variant in variants:
            require(variant["label_horizon"] in ALLOWED_HORIZONS, "variant horizon not pre-registered")
            require(variant["retrain_frequency"] in FINALS_PHASES, "variant frequency not pre-registered")
            require(len(variant["screen_phases"]) == 2, "each variant screens exactly two phases")
    return {
        "protocol": PROTOCOL_VERSION,
        "stage": "l3",
        "status": "PLAN_ONLY_NOT_EXECUTED",
        "anchor": {"candidate_id": ANCHOR_CANDIDATE, "topk": ANCHOR_TOPK, "dropn": ANCHOR_DROPN},
        "stage1_variants": STAGE1_VARIANTS,
        "stage2_variants": STAGE2_VARIANTS,
        "baseline_control": BASELINE_CONTROL,
        "finals_phases": FINALS_PHASES,
        "execution": EXECUTION_SCHEMA,
        "fees_bp": {"buy": FEE_BUY_BP, "sell": FEE_SELL_BP, "min_cny": FEE_MIN_CNY},
        "initial_cash_cny": INITIAL_CASH_CNY,
        "fit_cap_including_retries": L3_FIT_CAP,
        "cost_ceiling_usd": L3_USD_CAP,
        "max_advance": L3_MAX_ADVANCE,
        "historic_data": "HISTORICALLY_CONSUMED",
    }


def ordinal_remap_for_pairing(
    challenger_cells: List[Dict[str, Any]], baseline_cells: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Remap baseline cells onto challenger phases positionally for pairing.

    Challenger and baseline legs live on different retraining calendars, so
    phase integers are never matched directly. Both legs are sorted by phase
    and zipped by position (first perturbation against first, second against
    second) over the shared execution window and saved benchmark. Remapped
    records keep their original report bytes via ``_source_phase`` so loaders
    resolve real files; the remap itself is recorded for audit. Length
    mismatch is BLOCKED, never truncated or padded.
    """
    require(challenger_cells and baseline_cells, "pairing legs missing")
    require(len(challenger_cells) == len(baseline_cells), "phase count mismatch for ordinal pairing")
    chall_phases = sorted(c["phase"] for c in challenger_cells)
    require(len(set(chall_phases)) == len(chall_phases), "challenger phases not unique")
    ordered = sorted(baseline_cells, key=lambda r: r["phase"])
    remapped = [dict(row, phase=phase, _source_phase=row["phase"]) for row, phase in zip(ordered, chall_phases)]
    mapping = {"challenger_phases": chall_phases, "baseline_phases": [r["phase"] for r in ordered]}
    return remapped, mapping


def stage2_qualified(stage1_screen: List[Dict[str, Any]]) -> bool:
    """Stage-2 label variants run only if a frequency variant passed coarse screening."""
    require(isinstance(stage1_screen, list) and stage1_screen, "stage-1 screen results missing")
    return any(bool(row.get("passed_coarse")) for row in stage1_screen)


def l3_decision(screened: List[Dict[str, Any]]) -> Tuple[str, Dict[str, Any]]:
    """Advance at most one structural configuration, else keep the Baseline.

    ``screened`` carries per-variant coarse verdicts with recomputed worst /
    median improvements. Embedded PASS claims are re-derived by the caller;
    this function only enforces the at-most-one rule and deterministic
    ordering (worst improvement, then median, then variant key).
    """
    passed = [v for v in screened if v.get("passed_coarse") is True]
    for variant in passed:
        require(
            {"label_horizon", "retrain_frequency", "worst_delta_pp", "median_delta_pp"} <= set(variant),
            "variant verdict fields missing",
        )
    if not passed:
        return "KEEP_BASELINE_FOR_PRODUCT_SELECTION", {"n_screened": len(screened), "n_pass": 0}
    ordered = sorted(
        passed, key=lambda v: (-v["worst_delta_pp"], -v["median_delta_pp"], v["label_horizon"], v["retrain_frequency"])
    )
    require(len(ordered) >= 1, "unreachable")
    return "PROPOSE_STRUCTURE", {"winner": ordered[0], "n_pass": len(passed), "dropped": [v for v in ordered[1:]]}


def check_l3_budgets(fits_planned: int, fits_used: int, spend_usd: float) -> str:
    """Fail-closed L3 budget gate: 250 fits and USD 6.0 are hard stops."""
    for value, name in ((fits_planned, "planned fits"), (fits_used, "used fits")):
        require(isinstance(value, int) and value >= 0, f"{name} invalid")
    if not isinstance(spend_usd, (int, float)) or not math.isfinite(spend_usd):
        return "STOP_COST_UNKNOWN"
    require(spend_usd >= 0, "spend invalid")
    if fits_used > L3_FIT_CAP or fits_planned > L3_FIT_CAP:
        return "STOP_BUDGET"
    if spend_usd > L3_USD_CAP + 1e-9:
        return "STOP_BUDGET"
    return "OK_CONTINUE"


def build_cli() -> argparse.ArgumentParser:
    """CLI for local inventory/dry-run. Real execution uses the modal runner."""
    parser = argparse.ArgumentParser(
        description="L3 structural-search core: frozen plan, calendars and budget gates (no Modal, no training)."
    )
    parser.add_argument("--inventory", action="store_true", help="Print the frozen plan and budget caps.")
    parser.add_argument("--dry-run", action="store_true", help="Validate calendars and gates on synthetic inputs.")
    return parser


def main(argv: List[str] | None = None) -> int:
    """CLI entry point.

    Parameters
    ----------
    argv : list of str or None
        Argument list; None uses sys.argv.

    Returns
    -------
    int
        Process exit code.
    """
    args = build_cli().parse_args(argv)
    if args.inventory:
        plan = make_l3_plan()
        print(
            json.dumps(
                {
                    "protocol": plan["protocol"],
                    "variants": plan["stage1_variants"] + plan["stage2_variants"],
                    "fit_cap": plan["fit_cap_including_retries"],
                    "cost_cap": plan["cost_ceiling_usd"],
                },
                indent=2,
            )
        )
        return 0
    if args.dry_run:
        plan = make_l3_plan()
        assert plan["status"] == "PLAN_ONLY_NOT_EXECUTED"
        assert check_l3_budgets(196, 0, 0.0) == "OK_CONTINUE"
        assert check_l3_budgets(251, 0, 0.0) == "STOP_BUDGET"
        print("DRY_RUN_OK")
        return 0
    build_cli().print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
