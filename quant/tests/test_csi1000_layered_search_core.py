"""PR-S0 core/moderation tests: budgets, paired decisions, gates, CLI parity.

No Modal, no private provider, no training. All fixtures are synthetic.
"""

import ast
from pathlib import Path

import pytest

from audit import csi1000_layered_search_core as core
from audit import csi1000_layered_search_modal as runner


def _rows(phases, base_rel=0.02, chall_rel=0.05, base_mdd=9.0, chall_mdd=7.0, turnover=0.022, beat=True):
    chall, base = [], []
    for p in phases:
        chall.append(
            {
                "phase": p,
                "net_relative_cagr_pp": (chall_rel - base_rel) * 100.0,
                "net_total_return": 0.10,
                "mdd_pp": chall_mdd,
                "n_days": 424,
                "turnover": turnover,
                "coverage_ok": True,
                "ledger_ok": True,
                "data_ok": True,
                "plus10bp_advantage_pp": 0.5,
            }
        )
        base.append(
            {
                "phase": p,
                "net_relative_cagr_pp": 0.0,
                "net_total_return": 0.05 if beat else 0.20,
                "mdd_pp": base_mdd,
                "n_days": 424,
                "turnover": 0.02,
                "coverage_ok": True,
                "ledger_ok": True,
                "data_ok": True,
                "plus10bp_advantage_pp": 0.0,
            }
        )
    return chall, base


def _record(**kw):
    rec = {
        "status": "PASS",
        "cell_key": "k",
        "source_sha256": "a" * 64,
        "output_sha256": "b" * 64,
        "quote_sha256": "c" * 64,
        "signal_sha256": "d" * 64,
        "cache_key": "e" * 64,
        "final_nav_cny": 101_000_000.0,
        "total_cost_cny": 5000.0,
        "ledger_ok": True,
        "coverage_ok": True,
        "data_ok": True,
        "training_performed": False,
        "promotion_authorized": False,
        "n_days": 424,
        "topk": 20,
        "dropn": 2,
        "legacy_reproduction": "PASS",
        "corrected_reference": "PASS",
    }
    rec.update(kw)
    return rec


def test_layer_caps_sum_to_global():
    core.check_layer_totals()
    assert core.GLOBAL_CAP_USD == 30.0
    assert core.GLOBAL_FIT_CAP == 1200


def test_enumeration_counts_and_p0_identity():
    assert len(core.enumerate_cells("p0")) == 4
    assert len(core.enumerate_cells("l1-a")) == 32
    assert len(core.enumerate_cells("l1-b")) == 144
    assert len(core.enumerate_cells("l1-c")) == 264
    cells = core.enumerate_cells("p0")
    assert {c["candidate_id"] for c in cells} == {core.BASELINE_CANDIDATE, core.WINNER_CANDIDATE}
    assert all((c["topk"], c["dropn"]) == (20, 2) for c in cells)


def test_replay_cache_key_rejects_placeholders():
    with pytest.raises(ValueError):
        core.replay_cache_key("pending_signal_sha", 20, 2, "f" * 64)
    with pytest.raises(ValueError):
        core.replay_cache_key("a" * 64, 20, 2, "pending_quote_sha")
    key = core.replay_cache_key("a" * 64, 20, 2, "f" * 64)
    assert len(key) == 64


def test_cache_hit_requires_output_sha_match():
    cached = {"cache_key": "1" * 64, "source_sha256": "a" * 64, "output_sha256": "b" * 64}
    assert (
        core.check_cache_hit(cached, {"cache_key": "1" * 64, "source_sha256": "a" * 64, "output_sha256": "b" * 64})
        == "RESUME_VERIFIED"
    )
    assert (
        core.check_cache_hit(cached, {"cache_key": "1" * 64, "source_sha256": "a" * 64, "output_sha256": "c" * 64})
        == "FAIL_CONFLICT"
    )
    assert (
        core.check_cache_hit(cached, {"cache_key": "1" * 64, "source_sha256": "d" * 64, "output_sha256": "b" * 64})
        == "FAIL_CONFLICT"
    )
    assert (
        core.check_cache_hit(cached, {"cache_key": "e" * 64, "source_sha256": "a" * 64, "output_sha256": "b" * 64})
        == "FAIL_CONFLICT"
    )


def test_check_cell_record_rejects_fake_pass():
    core.check_cell_record(_record())
    with pytest.raises(ValueError):
        core.check_cell_record(_record(status="PASS_SKELETON"))
    with pytest.raises(ValueError):  # missing NAV
        core.check_cell_record(_record(final_nav_cny=None))
    with pytest.raises(ValueError):  # NaN fee total
        core.check_cell_record(_record(total_cost_cny=float("nan")))
    with pytest.raises(ValueError):  # placeholder SHA
        core.check_cell_record(_record(output_sha256="pending_output_sha"))
    with pytest.raises(ValueError):
        core.check_cell_record(_record(training_performed=True))
    with pytest.raises(ValueError):
        core.check_cell_record(_record(promotion_authorized=True))
    with pytest.raises(ValueError):
        core.check_cell_record(_record(ledger_ok=False))


def test_require_p0_pass():
    rows = [
        _record(**{k: v for k, v in cell.items() if k in ("cell_key", "candidate_id", "phase", "topk", "dropn")})
        for cell in core.enumerate_cells("p0")
    ]
    summary = {"stage": "p0", "expected": 4, "completed": 4, "blocked": 0}
    assert core.require_p0_pass(summary, rows)["status"] == "P0_VERIFIED"
    with pytest.raises(ValueError, match="identit"):
        core.require_p0_pass(summary, rows[:3] + [rows[0]])
    with pytest.raises(ValueError):
        core.require_p0_pass({**summary, "completed": 3, "blocked": 1}, rows[:3])
    with pytest.raises(ValueError):  # skeleton is not a verified PASS
        core.require_p0_pass(summary, [_record(cell_key="c0", status="PASS_SKELETON")] + rows[1:])


def test_ledger_nan_and_pending_and_cross_batch(tmp_path):
    ledger = core.BudgetLedger()
    assert ledger.preflight("p0", 1.0) == "OK_CONTINUE"
    assert ledger.preflight("p0", float("nan")) == "STOP_COST_UNKNOWN"
    assert ledger.preflight("p0", float("inf")) == "STOP_COST_UNKNOWN"
    assert ledger.preflight("p0", 99.0) == "STOP_BUDGET"
    assert ledger.record_estimate("p0", 1.0, run_id="r1") == "OK_CONTINUE"
    assert ledger.preflight("p0", 0.5) == "STOP_COST_UNKNOWN"  # actuals not booked yet
    assert ledger.record_actual("p0", 0.8, app_id="app-1", run_id="r1") == "OK_CONTINUE"
    assert ledger.preflight("p0", 0.5) == "OK_CONTINUE"
    path = tmp_path / "ledger.json"
    ledger.save(path)
    reloaded = core.BudgetLedger.load(path)
    assert reloaded.spent_by_layer["p0"] == 0.8
    assert reloaded.preflight("p0", 1.5) == "STOP_BUDGET"  # 0.8 + 1.5 > 2.0 layer cap
    with pytest.raises(ValueError):
        core.BudgetLedger.estimate_p90([1.0], float("nan"), 0.001, 10.0, 10.0)
    with pytest.raises(ValueError):
        ledger.record_actual("p0", float("nan"), app_id="app-2", run_id="r2")
    with pytest.raises(ValueError):
        ledger.record_actual("p0", 0.1, app_id="pending", run_id="r2")
    corrupt = tmp_path / "bad.json"
    corrupt.write_text("{nope", encoding="utf-8")
    with pytest.raises(ValueError):
        core.BudgetLedger.load(corrupt)


def test_negative_controls_cover_nan_and_nav():
    with pytest.raises(ValueError):
        core.neg_nonfinite_spend(float("nan"))
    with pytest.raises(ValueError):
        core.neg_missing_nav(None)
    with pytest.raises(ValueError):
        core.neg_zero_fee(0.0)
    with pytest.raises(ValueError):
        core.neg_cross_account("same", "same")
    with pytest.raises(ValueError):
        core.neg_duplicate_key(["k", "k"])
    with pytest.raises(ValueError):
        core.neg_over_budget(5.0, 2.0)
    with pytest.raises(ValueError):
        core.neg_over_budget(float("nan"), 2.0)


def test_coarse_pass_uses_improvements_not_absolutes():
    chall, base = _rows([0, 10])
    passed, _ = core.coarse_pass(chall, base)
    assert passed is True
    # Median of two observations is their arithmetic mean, not their max.
    asymmetric = [dict(r) for r in chall]
    asymmetric[0]["net_relative_cagr_pp"] = 1.0
    passed, details = core.coarse_pass(asymmetric, base)
    assert passed is True
    assert details["median_delta_pp"] == pytest.approx(2.0)
    # Challenger looks strong in absolute terms but loses to baseline: must fail.
    losing, _ = _rows([0, 10], base_rel=0.10, chall_rel=0.05)
    passed, info = core.coarse_pass(losing, base)
    assert passed is False and info["failed"] == "improvement"
    missing = [dict(r) for r, _ in zip(*_rows([0, 10]))]
    del missing[0]["net_relative_cagr_pp"]
    passed, info = core.coarse_pass(missing, base)
    assert passed is False
    bad_base = [dict(r) for r in base]
    bad_base[0]["ledger_ok"] = False
    passed, info = core.coarse_pass(chall, bad_base)
    assert passed is False and info["gate"] == "BASELINE_INELIGIBLE"


def test_final_verdict_paired_gates():
    chall, base = _rows([0, 4, 6, 10, 15])
    verdict, info = core.final_verdict(chall, base, 1.1)
    assert verdict == "PASS", info
    weak, _ = _rows([0, 4, 6, 10, 15], base_rel=0.05, chall_rel=0.055)
    assert core.final_verdict(weak, base, 1.1)[0] == "NOT_ELIGIBLE"
    narrow_chall, narrow_base = _rows([0, 4, 6, 10, 15], beat=False)
    assert core.final_verdict(narrow_chall, narrow_base, 1.1)[1]["failed"] == "breadth"
    assert core.final_verdict(chall, base, 2.0)[1]["failed"] == "turnover"
    no_stress = [dict(r, plus10bp_advantage_pp=None) for r in chall]
    assert core.final_verdict(no_stress, base, 1.1)[0] == "NOT_ELIGIBLE"
    neg_stress = [dict(r, plus10bp_advantage_pp=-0.2) for r in chall]
    assert core.final_verdict(neg_stress, base, 1.1)[1]["failed"] == "cost_stress_10bp"
    assert core.final_verdict(chall[:4], base[:4], 1.1)[0] == "INCOMPLETE"
    duplicate = [dict(r) for r in chall]
    duplicate[1]["phase"] = duplicate[0]["phase"]
    assert core.final_verdict(duplicate, base, 1.1)[0] == "NOT_ELIGIBLE"
    strong, safe_base = _rows([0, 4, 6, 10, 15], base_mdd=12.0, chall_mdd=14.0)
    safe_base[0]["mdd_pp"] = 8.0
    verdict, risk = core.final_verdict(strong, safe_base, 1.1)
    assert verdict == "PASS", risk
    assert risk["mdd_degrade_pp"] == pytest.approx(2.0)
    severe = [dict(r, mdd_pp=15.01) for r in strong]
    assert core.final_verdict(severe, safe_base, 1.1)[1]["failed"] == "mdd"
    # Single turnover outlier does not change median of five phases.
    skewed = [dict(r) for r in chall]
    skewed[0]["turnover"] = 0.10
    still_pass, diag = core.final_verdict(skewed, base, 1.1)
    assert still_pass == "PASS", diag
    assert diag["turnover_ratio"] == pytest.approx(1.1)
    # A caller-supplied fake low ratio cannot hide poor turnover evidence.
    expensive = [dict(r, turnover=0.04) for r in chall]
    cheated, info = core.final_verdict(expensive, base, 1.1)
    assert cheated == "NOT_ELIGIBLE" and info["failed"] == "turnover"
    assert info["turnover_ratio"] == pytest.approx(2.0)
    # If the claimed ratio is corrected, the risk gate still blocks it.
    assert core.final_verdict(expensive, base, 2.0)[1]["failed"] == "turnover"
    bad_base = [dict(r) for r in base]
    bad_base[0]["data_ok"] = False
    assert core.final_verdict(chall, bad_base, 1.1)[0] == "BASELINE_INELIGIBLE"


def test_decide_product_recomputes_and_blocks():
    chall, base = _rows([0, 4, 6, 10, 15])
    good = {
        "config_sha": "a" * 64,
        "challenger_phases": chall,
        "baseline_phases": base,
        "turnover_ratio": 1.1,
        "verdict": "PASS",
    }
    code, detail = core.decide_product([good])
    assert code == "PROPOSE_PRODUCT_PROFILE_VERSION" and detail["n_pass"] == 1
    # An embedded PASS claim with losing numbers must NOT promote.
    weak, _ = _rows([0, 4, 6, 10, 15], base_rel=0.05, chall_rel=0.04)
    liar = {
        "config_sha": "b" * 64,
        "challenger_phases": weak,
        "baseline_phases": base,
        "turnover_ratio": 1.1,
        "verdict": "PASS",
    }
    assert core.decide_product([liar])[0] == "KEEP_BASELINE_FOR_PRODUCT_SELECTION"
    assert core.decide_product([liar], baseline_eligible=False)[0] == "STOP_PRODUCT_BLOCKED"
    assert core.decide_product([])[0] == "KEEP_BASELINE_FOR_PRODUCT_SELECTION"


def test_rank_tiebreak_deterministic():
    rows = [
        {
            "worst_delta_pp": 1.0,
            "median_delta_pp": 1.0,
            "mdd_degrade_pp": 0.0,
            "turnover_ratio": 1.0,
            "config_sha": "ff",
        },
        {
            "worst_delta_pp": 1.0,
            "median_delta_pp": 1.0,
            "mdd_degrade_pp": 0.0,
            "turnover_ratio": 1.0,
            "config_sha": "00",
        },
    ]
    assert core.rank_challengers(rows)[0]["config_sha"] == "00"


def test_output_isolation_and_trial_ledger(tmp_path):
    with pytest.raises(ValueError):
        core.check_output_not_in_source([tmp_path, tmp_path / "prov"], tmp_path / "prov" / "out")
    with pytest.raises(ValueError):
        core.check_output_not_in_source([tmp_path], "/vol/evil-out")
    ledger = tmp_path / "trial.jsonl"
    core.append_trial_ledger(
        ledger, [{"cell_key": "a", "run_id": "r1", "app_id": "app-1", "status": "PASS", "spend_usd": 0.5}]
    )
    with pytest.raises(ValueError):  # duplicate (run_id, cell_key)
        core.append_trial_ledger(
            ledger, [{"cell_key": "a", "run_id": "r1", "app_id": "app-2", "status": "PASS", "spend_usd": 0.1}]
        )
    # Same cell under a new run is a distinct audited row (reuse travels by attestation).
    core.append_trial_ledger(
        ledger, [{"cell_key": "a", "run_id": "r2", "app_id": "app-2", "status": "PASS", "spend_usd": 0.1}]
    )
    with pytest.raises(ValueError):  # run-id required
        core.append_trial_ledger(tmp_path / "t0.jsonl", [{"cell_key": "z", "app_id": "app-1", "status": "PASS"}])
    with pytest.raises(ValueError):  # placeholder App ID
        core.append_trial_ledger(tmp_path / "t2.jsonl", [{"cell_key": "b", "app_id": "pending", "status": "PASS"}])
    with pytest.raises(ValueError):  # skeleton status never lands in the audited ledger
        core.append_trial_ledger(
            tmp_path / "t3.jsonl", [{"cell_key": "c", "app_id": "app-1", "status": "PASS_SKELETON"}]
        )


def test_runner_guards_and_p0_gate(tmp_path):
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
    with pytest.raises(ValueError):  # L1-A without P0 proof
        runner.validate_for_dispatch("l1-a", "run-01", 2, 0, 1.0, "", str(tmp_path / "l.json"), "")
    cells = runner.validate_args(
        runner.build_cli().parse_args(
            [
                "--stage",
                "p0",
                "--run-id",
                "valid-run-01",
                "--estimated-total-usd",
                "1",
                "--ledger",
                str(tmp_path / "l.json"),
            ]
        )
    )
    assert [c["cell_key"] for c in cells] == sorted(c["cell_key"] for c in cells) and len(cells) == 4
    limited = runner.validate_for_dispatch("p0", "run-01", 2, 2, 1.0, "", str(tmp_path / "l.json"), "")
    assert len(limited) == 2
    with pytest.raises(ValueError):
        runner.validate_for_dispatch("p0", "run-01", 2, 99, 1.0, "", str(tmp_path / "l.json"), "")


def test_p0_image_avoids_git_metadata_build():
    """A single guard for the actual two-failure root cause."""
    text = Path("quant/code/research/layered_search/csi1000_layered_search_modal.py").read_text(encoding="utf-8")
    assert "make prerequisite" not in text
    assert "pip install . --no-build-isolation" not in text
    assert "pip install -e ." not in text
    assert "cd /tmp && python -c" in text
    assert "from Cython.Build import cythonize" in text
    assert "'qlib-image-cython-only'" in text
    assert "/root/qlib/qlib/_version.py" in text
    assert "from qlib.data._libs import rolling, expanding" in text


def test_modal_file_static_contract():
    text = Path("quant/code/research/layered_search/csi1000_layered_search_modal.py").read_text(encoding="utf-8")
    assert "create_if_missing=False" in text
    assert "with_mount_options(read_only=True)" in text
    assert "original.commit()" not in text and "source.commit()" not in text
    assert "research.commit()" in text
    assert ".fit(" not in text
    assert "@app.local_entrypoint()" in text
    assert "finalize_remote" in text and "replay_cell_remote" in text
    assert "require_p0_pass" in text
    for line in text.splitlines():
        if "--detach" in line and "checkout" not in line and "modal_detach_used" not in line:
            assert any(w in line.lower() for w in ("never", "without", "forbidden", "not ")), f"detach allowed: {line}"
    assert "modal_detach_used" in text
    tree = ast.parse(text)
    entries = [
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.args.args and n.args.args[0].arg == "stage"
    ]
    assert entries, "no local entrypoint with --stage found"
    entry = entries[0]
    params = {a.arg for a in entry.args.args} | {a.arg for a in entry.args.kwonlyargs}
    for flag in (
        "stage",
        "run_id",
        "max_workers",
        "batch_limit",
        "estimated_total_usd",
        "resume_from_manifest",
        "ledger",
        "p0_summary",
        "reuse_summary",
        "reuse_prefix",
        "equivalence_ref",
    ):
        assert flag in params, f"entrypoint missing --{flag.replace('_', '-')}"


def test_no_production_promotion_path():
    for name in (
        "quant/code/research/layered_search/csi1000_layered_search_core.py",
        "quant/code/research/layered_search/csi1000_layered_search_replay.py",
        "quant/code/research/layered_search/csi1000_layered_search_modal.py",
        "quant/code/research/layered_search/csi1000_layered_search_reconcile.py",
    ):
        text = Path(name).read_text(encoding="utf-8")
        assert "CANONICAL_PROFILE" not in text
        assert "stage_b_winner_canonical" not in text
        assert ".fit(" not in text


def test_frozen_profiles_match_production_config():
    profiles = core.load_frozen_profiles(".")
    assert profiles["baseline_control"]["candidate_id"] == core.BASELINE_CANDIDATE
