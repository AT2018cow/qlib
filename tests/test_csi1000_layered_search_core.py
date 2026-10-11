"""PR-S0 synthetic tests for the CSI1000 budgeted layered search.

No Modal, no private provider, no training. All fixtures are synthetic.
"""

import json
from pathlib import Path

import pytest

from audit import csi1000_layered_search_core as core
from audit import csi1000_layered_search_modal as runner
from audit import csi1000_layered_search_reconcile as rec


def test_layer_caps_sum_to_global():
    core.check_layer_totals()
    assert core.GLOBAL_CAP_USD == 30.0
    assert core.GLOBAL_FIT_CAP == 1200


def test_enumeration_counts():
    assert len(core.enumerate_cells("p0")) == 4
    assert len(core.enumerate_cells("l1-a")) == 32
    assert len(core.enumerate_cells("l1-b")) == 144
    assert len(core.enumerate_cells("l1-c")) == 264


def test_p0_cells_are_baseline_winner_phases_top20_drop2():
    cells = core.enumerate_cells("p0")
    assert {c["candidate_id"] for c in cells} == {core.BASELINE_CANDIDATE, core.WINNER_CANDIDATE}
    assert {c["phase"] for c in cells} == {0, 10}
    assert all((c["topk"], c["dropn"]) == (20, 2) for c in cells)


def test_portfolio_grid_pre_registered():
    assert len(core.L1_PORTFOLIO_GRID) == 12
    assert all(d <= k for k, d in core.L1_PORTFOLIO_GRID)
    assert len(core.L1A_8_COMBOS) == 8


def test_cache_strict_sha_resume():
    key = core.replay_cache_key("sig", 20, 2, "quote")
    cached = {"cache_key": key, "source_sha256": "s", "output_sha256": "o"}
    assert core.check_cache_hit(cached, {"cache_key": key, "source_sha256": "s"}) == "RESUME_VERIFIED"
    assert core.check_cache_hit(cached, {"cache_key": key, "source_sha256": "tampered"}) == "FAIL_CONFLICT"
    assert core.check_cache_hit(cached, {"cache_key": "other", "source_sha256": "s"}) == "FAIL_CONFLICT"


def test_budget_preflight_and_reserve():
    ledger = core.BudgetLedger()
    assert ledger.preflight("p0", 1.0) == "OK_CONTINUE"
    assert ledger.preflight("p0", 99.0) == "STOP_BUDGET"
    assert ledger.preflight("p0", -1.0) == "STOP_COST_UNKNOWN"
    assert ledger.preflight("p0", None) == "STOP_COST_UNKNOWN"
    assert ledger.record("p0", 1.0) == "OK_CONTINUE"
    assert ledger.record("p0", 5.0) == "STOP_BUDGET"


def test_negative_controls_all_fail_closed():
    with pytest.raises(ValueError):
        core.neg_config_hash_tampered("a", "b")
    with pytest.raises(ValueError):
        core.neg_future_universe_leak(["2026-10-13"], "2026-10-12", ["2026-10-10"])
    with pytest.raises(ValueError):
        core.neg_prediction_after_outcome("2026-10-13T00:00:00Z", "2026-10-12T00:00:00Z")
    with pytest.raises(ValueError):
        core.neg_missing_open_factor(False, True)
    with pytest.raises(ValueError):
        core.neg_delisted_missing_quote(True, False)
    with pytest.raises(ValueError):
        core.neg_halt_fake_sell(True, True)
    with pytest.raises(ValueError):
        core.neg_zero_fee(0.0)
    with pytest.raises(ValueError):
        core.neg_cross_account("same", "same")
    with pytest.raises(ValueError):
        core.neg_duplicate_key(["k", "k"])
    with pytest.raises(ValueError):
        core.neg_missing_baseline(False)
    with pytest.raises(ValueError):
        core.neg_over_budget(5.0, 2.0)


def _passing_phases():
    return [
        {
            "net_relative_cagr_pp": 1.5 + i * 0.1,
            "net_total_return": 0.10 + i * 0.01,
            "baseline_net_total_return": 0.05,
            "mdd_pp": 8.0,
            "baseline_mdd_pp": 9.0,
            "plus10bp_advantage_pp": 0.5,
            "coverage_ok": True,
            "ledger_ok": True,
            "data_ok": True,
        }
        for i in range(5)
    ]


def test_final_verdict_pass_and_fail_modes():
    verdict, _ = core.final_verdict(_passing_phases(), 1.1)
    assert verdict == "PASS"
    bad = _passing_phases()
    bad[0]["net_relative_cagr_pp"] = -5.0
    verdict, detail = core.final_verdict(bad, 1.1)
    assert verdict == "NOT_ELIGIBLE"
    assert detail["failed"] == "worst_phase"
    verdict, _ = core.final_verdict(_passing_phases()[:4], 1.1)
    assert verdict == "INCOMPLETE"


def test_no_pass_means_keep_baseline():
    code, _ = core.decide_product([{"verdict": "NOT_ELIGIBLE"}])
    assert code == "KEEP_BASELINE_FOR_PRODUCT_SELECTION"


def test_rank_tiebreak_is_deterministic_sha():
    rows = [
        {"worst_pp": 1.0, "median_pp": 1.0, "mdd_degrade_pp": 0.0, "turnover_ratio": 1.0, "config_sha": "ff"},
        {"worst_pp": 1.0, "median_pp": 1.0, "mdd_degrade_pp": 0.0, "turnover_ratio": 1.0, "config_sha": "00"},
    ]
    assert core.rank_challengers(rows)[0]["config_sha"] == "00"


def test_output_never_inside_source(tmp_path):
    with pytest.raises(ValueError):
        core.check_output_not_in_source(tmp_path, tmp_path / "out")


def test_runner_cli_guards():
    with pytest.raises(ValueError):
        runner.validate_args(
            runner.build_cli().parse_args(["--stage", "p0", "--run-id", "x", "--estimated-total-usd", "1"])
        )
    with pytest.raises(ValueError):
        runner.validate_args(
            runner.build_cli().parse_args(
                ["--stage", "p0", "--run-id", "valid-run-01", "--max-workers", "9", "--estimated-total-usd", "1"]
            )
        )
    with pytest.raises(ValueError):
        runner.validate_args(
            runner.build_cli().parse_args(["--stage", "p0", "--run-id", "valid-run-01", "--estimated-total-usd", "99"])
        )
    with pytest.raises(ValueError):
        runner.validate_args(
            runner.build_cli().parse_args(["--stage", "l2", "--run-id", "valid-run-01", "--estimated-total-usd", "1"])
        )
    cells = runner.validate_args(
        runner.build_cli().parse_args(["--stage", "p0", "--run-id", "valid-run-01", "--estimated-total-usd", "1"])
    )
    assert len(cells) == 4


def test_modal_file_read_only_isolation():
    text = Path("audit/csi1000_layered_search_modal.py").read_text(encoding="utf-8")
    assert "create_if_missing=False" in text
    assert "with_mount_options(read_only=True)" in text
    assert "original.commit()" not in text and "source.commit()" not in text
    assert "research.commit()" in text
    assert "--detach" not in text or "NEVER pass" in text and "detach" in text.lower()
    assert ".fit(" not in text


def test_reconcile_sha_and_decision(tmp_path):
    d = tmp_path / "ev"
    d.mkdir()
    (d / "a.json").write_text(json.dumps({"x": 1}), encoding="utf-8")
    import hashlib

    digest = hashlib.sha256((d / "a.json").read_bytes()).hexdigest()
    (d / "SHA256SUMS.txt").write_text(f"{digest}  a.json\n", encoding="utf-8")
    assert rec.verify_sha256sums(d)["status"] == "SHA_VERIFIED"
    decision = rec.build_decision([], 2.0, 0)
    assert decision["decision"] == "KEEP_BASELINE_FOR_PRODUCT_SELECTION"


def test_frozen_profiles_match_production_config():
    profiles = core.load_frozen_profiles(".")
    assert profiles["baseline_control"]["candidate_id"] == core.BASELINE_CANDIDATE
