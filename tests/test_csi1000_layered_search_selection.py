"""Deterministic, offline-only tests for the CSI1000 layered selection planner."""
import json
from pathlib import Path

import pytest

from audit import csi1000_layered_search_selection as selection
from audit import csi1000_layered_search_core as core


def test_l2_proposals_are_frozen_and_repeatable():
    a = selection.stage_a_proposals(".")
    b = selection.stage_a_proposals(".")
    assert a == b
    assert a["status"] == "PLAN_ONLY_NOT_EXECUTED"
    assert len(a["four_existing"]) == 4
    assert len(a["four_new"]) == 4
    assert len({r["candidate_id"] for r in a["four_existing"] + a["four_new"]}) == 8
    assert not ({r["candidate_id"] for r in a["four_existing"]} & set(core.STAGE_B_11_MODELS))
    assert a["l2_long_fit_cap"] == 320


def test_l3_variants_do_not_claim_any_fit():
    anchor = {"candidate_id": core.BASELINE_CANDIDATE, "topk": 20, "dropn": 2}
    preliminary = selection.l3_structure_plan(anchor)
    assert {(v["label_horizon"], v["retrain_frequency"]) for v in preliminary["variants"]} == {(20, 10), (20, 40)}
    assert all(v["status"].startswith("BLOCKED") for v in preliminary["variants"])
    qualified = selection.l3_structure_plan(anchor, freq_screen_qualified=True)
    assert {(v["label_horizon"], v["retrain_frequency"]) for v in qualified["variants"]} == {
        (20, 10),
        (20, 40),
        (10, 20),
        (40, 20),
    }
    assert qualified["max_new_fit_calls"] == 250


def test_finalists_are_capped_and_deterministically_sorted():
    baseline = {
        "candidate_id": core.BASELINE_CANDIDATE,
        "topk": 20,
        "dropn": 2,
        "config_sha256": "0" * 64,
    }
    rows = [
        {
            "candidate_id": f"candidate_{i}",
            "layer": "l1" if i < 5 else "l2",
            "coarse_pass": True,
            "worst_delta_pp": 0.7 + i * 0.1,
            "median_delta_pp": 1.0 + i * 0.1,
            "mdd_degrade_pp": 1.0,
            "turnover_ratio": 1.1,
            "config_sha256": f"{i+1:064x}",
        }
        for i in range(8)
    ]
    result = selection.freeze_finalists(rows, baseline)
    assert result == selection.freeze_finalists(list(reversed(rows)), baseline)
    assert len(result["configurations"]) == 6
    assert result["configurations"][0] == baseline
    assert len([r for r in result["configurations"][1:] if r["layer"] == "l1"]) <= 3
    assert len([r for r in result["configurations"][1:] if r["layer"] == "l2"]) <= 2
    assert result["status"] == "FROZEN_FINALIST_PLAN_NOT_EXECUTED"


def test_final_decision_does_not_promote_embedded_pass(tmp_path):
    path = tmp_path / "paired.json"
    path.write_text(
        json.dumps({"stage": "l4", "baseline_eligible": True, "finalists": [{"verdict": "PASS"}]}), encoding="utf-8"
    )
    result = selection.decide_from_paired_file(path)
    assert result["decision"] == "KEEP_BASELINE_FOR_PRODUCT_SELECTION"
    assert result["promotion_authorized"] is False


def test_l1_screen_requires_real_cell_artifacts(tmp_path):
    with pytest.raises((ValueError, FileNotFoundError)):
        selection.screen_l1("l1-a", tmp_path / "empty", tmp_path / "baseline")


def test_l3_anchor_incomplete_is_blocked():
    with pytest.raises(ValueError):
        selection.l3_structure_plan({"candidate_id": core.BASELINE_CANDIDATE})
