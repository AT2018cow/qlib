"""Deterministic L2 fast-lane job plan, with zero Modal activity.

This module freezes Stage-A proposal identities and prechecks the entire
long-training calendar before an operator launches a separately reviewed
L2 fit/prediction/replay runner. A plan is NOT a signed result or a training
authorization. Do not use this module to accept unverified remote receipts.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from audit.csi1000_layered_search_core import RESEARCH_SEED_L2, require, stable_sha
from audit.csi1000_layered_search_selection import stage_a_proposals

SHORT_FOLDS = (0, 1, 2, 3)
LONG_PHASES = (0, 10)
L2_FIT_CAP = 320  # Includes short smoke, long smoke, failures and retries.
L2_NEW_SHORT_FIT_CAP = 16
L2_LONG_CANDIDATE_CAP = 6
L2_NEW_LONG_CANDIDATE_CAP = 2
L2_COST_CAP_USD = 12.0
L2_FIXED_PORTFOLIO = {"topk": 20, "dropn": 2}


def _candidate_ids(plan: dict[str, Any], group: str) -> list[str]:
    rows = plan[group]
    require(len(rows) == 4, f"L2 requires exactly four {group} candidates")
    ids = [r["candidate_id"] for r in rows]
    require(len(set(ids)) == 4, f"L2 duplicate candidate in {group}")
    require(all(re.fullmatch(r"[a-f0-9]{64}", k) for k in ids), f"L2 invalid {group} candidate ID")
    return ids


def make_fastlane_plan(repo_root: str | Path = ".") -> dict[str, Any]:
    """Freeze the existing 4 + proposed 4, with full short-fold roster.

    This is intentionally isolated from model fitting: the runner must verify
    source/provider/PIT identities, persist true fit receipts, and enforce caps.
    """
    selection = stage_a_proposals(repo_root, seed=RESEARCH_SEED_L2)
    require(selection["status"] == "PLAN_ONLY_NOT_EXECUTED", "L2 proposal is not pre-execution")
    require(selection["seed"] == RESEARCH_SEED_L2, "L2 proposal seed mismatch")
    existing = _candidate_ids(selection, "four_existing")
    new = _candidate_ids(selection, "four_new")
    require(not set(existing) & set(new), "L2 old/new candidates overlap")
    require(selection["stage_a_short_fold_fit_cap"] == 16, "L2 short fold cap drift")
    require(selection["l2_long_fit_cap"] == 320, "L2 long fit cap drift")
    require(selection["allowed_long_validation_challengers"] == 6, "L2 long roster cap drift")
    short_jobs = [{"candidate_id": cid, "fold_index": fold} for cid in new for fold in SHORT_FOLDS]
    require(len(short_jobs) == L2_NEW_SHORT_FIT_CAP, "L2 needs exactly sixteen short jobs")
    frozen: dict[str, Any] = {
        "protocol": "csi1000_l2_fastlane_plan_v1",
        "stage": "l2",
        "status": "PLAN_ONLY_NOT_EXECUTED",
        "seed": RESEARCH_SEED_L2,
        "historic_data": "HISTORICALLY_CONSUMED",
        "stage_a_source_sha256": selection["original_stage_a_source_sha256"],
        "stage_b_source_sha256": selection["original_stage_b_source_sha256"],
        "existing_candidates": selection["four_existing"],
        "new_candidates": selection["four_new"],
        "short_fold_jobs": short_jobs,
        "short_fit_cap": L2_NEW_SHORT_FIT_CAP,
        "long_candidate_cap": L2_LONG_CANDIDATE_CAP,
        "new_long_candidate_cap": L2_NEW_LONG_CANDIDATE_CAP,
        "long_phases": list(LONG_PHASES),
        "portfolio": dict(L2_FIXED_PORTFOLIO),
        "total_fit_cap_including_retries": L2_FIT_CAP,
        "cost_ceiling_usd": L2_COST_CAP_USD,
        "mandatory_smoke": {
            "short_fold": short_jobs[0],
            "long_retrain": {
                "candidate_id": existing[0],
                "phase": LONG_PHASES[0],
                "asof": "first_frozen_retrain_asof_from_verified_calendar",
            },
            "note": "Both must be verified, durable and counted in the full run; neither is an extra fit.",
        },
        "run_policy": {
            "single_attached_coordinator": True,
            "resume_only_after_sha_verification": True,
            "auto_continue_after_smoke": True,
            "new_fit_retries": 0,
            "stop_on_deterministic_error": True,
            "stop_if_timeout_exceeds_attached_session": True,
            "production_change": False,
        },
    }
    return {**frozen, "plan_sha256": stable_sha(frozen)}


def make_long_job_roster(
    plan: dict[str, Any],
    calendar: list[str],
    qualified_new_ids: list[str],
) -> dict[str, Any]:
    """Expand only 4 old + <=2 independently qualified new candidates.

    The calling L2 runner must recompute qualification from complete 4-fold
    artifacts; this pure function checks membership, geometry and planned cap,
    not the truth of the supplied qualification claim.
    """
    from csi1000_stage_b_core import build_phase_jobs

    old = [r["candidate_id"] for r in plan["existing_candidates"]]
    new = {r["candidate_id"] for r in plan["new_candidates"]}
    require(len(qualified_new_ids) == len(set(qualified_new_ids)), "L2 repeated qualified new ID")
    require(len(qualified_new_ids) <= L2_NEW_LONG_CANDIDATE_CAP, "L2 exceeds two new long candidates")
    require(set(qualified_new_ids) <= new, "L2 long candidate was not a frozen new proposal")
    candidates = old + sorted(qualified_new_ids)
    require(len(candidates) <= L2_LONG_CANDIDATE_CAP, "L2 exceeds long candidate cap")
    jobs = []
    for candidate_id in candidates:
        for phase in LONG_PHASES:
            for period in build_phase_jobs(calendar, phase):
                jobs.append({"candidate_id": candidate_id, **period})
    planned = len(jobs)
    # Short-fold smoke is among the 16, long smoke must be one of these jobs.
    require(planned + L2_NEW_SHORT_FIT_CAP <= L2_FIT_CAP, "STOP_L2_FIT_BUDGET: planned fits exceed 320")
    require(
        len({(r["candidate_id"], r["phase"], r["retrain_asof"]) for r in jobs}) == planned,
        "L2 duplicate long retrain key",
    )
    return {
        "stage": "l2",
        "plan_sha256": plan["plan_sha256"],
        "status": "JOB_ROSTER_ONLY_NOT_EXECUTED",
        "portfolio": dict(L2_FIXED_PORTFOLIO),
        "selected_new_candidate_ids": sorted(qualified_new_ids),
        "long_candidate_count": len(candidates),
        "long_lineage_count": 2 * len(candidates),
        "short_fits_including_smoke": L2_NEW_SHORT_FIT_CAP,
        "long_fits_including_smoke": planned,
        "fits_before_failures": planned + L2_NEW_SHORT_FIT_CAP,
        "fit_ceiling_including_failures": L2_FIT_CAP,
        "jobs": jobs,
        "job_roster_sha256": stable_sha(jobs),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="L2 offline plan and cap check ONLY; never fit or launch Modal.")
    p.add_argument("--repo-root", default=".")
    p.add_argument("--output", required=True, help="New JSON path; refuses overwrite.")
    args = p.parse_args()
    result = make_fastlane_plan(args.repo_root)
    output = Path(args.output)
    require(not output.exists(), "L2 plan output already exists; refusing overwrite")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(f"L2_PLAN_ONLY plan_sha256={result['plan_sha256']} short_fits=16 long_candidates<=6")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
