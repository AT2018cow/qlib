"""Synthetic fail-closed checks for the L2 zero-Modal fast-lane planning gate."""

from __future__ import annotations

import copy

import pytest

from audit import csi1000_l2_fastlane as l2
from audit.csi1000_layered_search_core import stable_sha


def _dummy_plan():
    frozen = {
        "status": "PLAN_ONLY_NOT_EXECUTED",
        "stage": "l2",
        "seed": l2.RESEARCH_SEED_L2,
        "original_stage_a_source_sha256": "a" * 64,
        "original_stage_b_source_sha256": "b" * 64,
        "four_existing": [{"candidate_id": f"{i:064x}", "model_params": {}} for i in range(1, 5)],
        "four_new": [{"candidate_id": f"{i:064x}", "model_params": {}} for i in range(5, 9)],
        "stage_a_short_fold_fit_cap": 16,
        "l2_long_fit_cap": 320,
        "allowed_long_validation_challengers": 6,
    }
    return frozen


def test_l2_plan_is_deterministic_and_never_claims_execution(monkeypatch):
    monkeypatch.setattr(l2, "stage_a_proposals", lambda root, seed: _dummy_plan())
    a = l2.make_fastlane_plan(".")
    assert a == l2.make_fastlane_plan(".")
    assert a["plan_sha256"] == stable_sha({k: v for k, v in a.items() if k != "plan_sha256"})
    assert a["status"] == "PLAN_ONLY_NOT_EXECUTED"
    assert len(a["short_fold_jobs"]) == 16
    assert len({(r["candidate_id"], r["fold_index"]) for r in a["short_fold_jobs"]}) == 16
    assert {r["fold_index"] for r in a["short_fold_jobs"]} == {0, 1, 2, 3}
    assert a["total_fit_cap_including_retries"] == 320
    assert a["cost_ceiling_usd"] == 12.0
    assert a["mandatory_smoke"]["short_fold"] in a["short_fold_jobs"]
    assert a["run_policy"]["new_fit_retries"] == 0
    assert a["run_policy"]["single_attached_coordinator"] is True
    assert a["portfolio"] == {"topk": 20, "dropn": 2}


@pytest.mark.parametrize("which", ["repeat_old", "overlap", "invalid_id", "wrong_cap", "wrong_seed"])
def test_l2_frozen_proposal_integrity_fail_closed(monkeypatch, which):
    proposal = _dummy_plan()
    if which == "repeat_old":
        proposal["four_existing"][1]["candidate_id"] = proposal["four_existing"][0]["candidate_id"]
    elif which == "overlap":
        proposal["four_new"][1]["candidate_id"] = proposal["four_existing"][0]["candidate_id"]
    elif which == "invalid_id":
        proposal["four_new"][0]["candidate_id"] = "not_sha256"
    elif which == "wrong_cap":
        proposal["l2_long_fit_cap"] = 321
    else:
        proposal["seed"] = 9
    monkeypatch.setattr(l2, "stage_a_proposals", lambda root, seed: copy.deepcopy(proposal))
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        l2.make_fastlane_plan(".")


def test_l2_long_roster_includes_smoke_and_total_cap(monkeypatch):
    monkeypatch.setattr(l2, "stage_a_proposals", lambda root, seed: _dummy_plan())
    plan = l2.make_fastlane_plan(".")
    import csi1000_stage_b_core

    def periods(calendar, phase):
        return [
            {"phase": phase, "retrain_asof": f"2025-01-{i+1:02d}", "signal_end": f"2025-01-{i+1:02d}"}
            for i in range(25)
        ]

    monkeypatch.setattr(csi1000_stage_b_core, "build_phase_jobs", periods)
    new = [r["candidate_id"] for r in plan["new_candidates"]]
    all_roster = l2.make_long_job_roster(plan, [], new[:2])
    assert all_roster["long_lineage_count"] == 12
    assert all_roster["long_fits_including_smoke"] == 300
    assert all_roster["fits_before_failures"] == 316
    assert len(all_roster["jobs"]) == 300
    assert len({(r["candidate_id"], r["phase"], r["retrain_asof"]) for r in all_roster["jobs"]}) == 300
    assert all_roster["job_roster_sha256"] == stable_sha(all_roster["jobs"])

    def too_many(calendar, phase):
        return periods(calendar, phase) + [{"phase": phase, "retrain_asof": "2025-02-01", "signal_end": "2025-02-01"}]

    monkeypatch.setattr(csi1000_stage_b_core, "build_phase_jobs", too_many)
    with pytest.raises(ValueError, match="STOP_L2_FIT_BUDGET"):
        l2.make_long_job_roster(plan, [], new[:2])


def test_l2_long_roster_rejects_invalid_new_promotions(monkeypatch):
    monkeypatch.setattr(l2, "stage_a_proposals", lambda root, seed: _dummy_plan())
    plan = l2.make_fastlane_plan(".")
    ids = [r["candidate_id"] for r in plan["new_candidates"]]
    with pytest.raises(ValueError, match="two new"):
        l2.make_long_job_roster(plan, [], ids[:3])
    with pytest.raises(ValueError, match="repeated"):
        l2.make_long_job_roster(plan, [], [ids[0], ids[0]])
    with pytest.raises(ValueError, match="not a frozen"):
        l2.make_long_job_roster(plan, [], ["f" * 64])


def test_l2_plan_frozen_main_evidence():
    # This is a historical proposal-only test. No Modal or actual training.
    result = l2.make_fastlane_plan(".")
    assert len(result["existing_candidates"]) == 4
    assert len(result["new_candidates"]) == 4
    assert result["status"] == "PLAN_ONLY_NOT_EXECUTED"
