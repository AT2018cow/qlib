"""Zero-Modal L4 immutable manifest, frozen-source, receipt and hard-gate tests."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from audit import csi1000_l4_core as core
from audit import csi1000_l4_modal as runner
from audit.csi1000_layered_search_core import stable_sha


def test_frozen_manifest_exact_four_configs_and_five_phases():
    manifest = core.frozen_l4_manifest(".")
    assert manifest["finalist_manifest_sha256"] == core.FROZEN_MANIFEST_SHA
    specs = core.l4_cell_specs(manifest)
    assert len(specs) == 20
    assert len({(s["config_sha256"], s["phase"]) for s in specs}) == 20
    assert len({s["candidate_id"] for s in specs}) == 3
    assert set(s["phase"] for s in specs) == {0, 4, 6, 10, 15}
    first, third = manifest["configurations"][0], manifest["configurations"][2]
    assert first["candidate_id"] == third["candidate_id"]
    assert (first["topk"], first["dropn"]) == (20, 2)
    assert (third["topk"], third["dropn"]) == (10, 1)
    assert first["config_sha256"] != third["config_sha256"]
    assert [c["layer"] for c in manifest["configurations"]] == ["p0", "l1", "l1", "l1"]


@pytest.mark.parametrize("tamper", ["drop_config", "reorder", "alter_combo", "bad_hash", "extra_l3"])
def test_frozen_manifest_drift_fail_closed(monkeypatch, tmp_path, tamper):
    manifest = core.frozen_l4_manifest(".")
    manifest = copy.deepcopy(manifest)
    cs = manifest["configurations"]
    if tamper == "drop_config":
        cs.pop()
    elif tamper == "reorder":
        cs[1], cs[2] = cs[2], cs[1]
    elif tamper == "alter_combo":
        cs[1]["topk"] = 20
    elif tamper == "bad_hash":
        manifest["finalist_manifest_sha256"] = "f" * 64
    else:
        cs[3]["layer"] = "l3"
    relative = Path(core.FROZEN_MANIFEST_PATH)
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.frozen_l4_manifest(tmp_path)


def test_actual_stage_b_five_phases_source_metadata_and_model_shas():
    from audit.csi1000_layered_search_replay import load_frozen_result

    frozen = core.frozen_l4_manifest(".")
    info = core.check_stage_b_source_coverage(load_frozen_result("."), frozen)
    assert info["model_count"] == 3
    assert info["source_model_phases"] == 15
    assert len({(x["candidate_id"], x["phase"]) for x in info["sources"]}) == 15
    assert len(info["source_digest"]) == 64
    assert all(len(x["frozen_signal_sha256"]) == 64 for x in info["sources"])


def test_stage_b_chunk_model_config_drift_rejected():
    from audit.csi1000_layered_search_replay import load_frozen_result

    real = load_frozen_result(".")
    changed = copy.deepcopy(real)
    cid = core.frozen_l4_manifest(".")["configurations"][1]["candidate_id"]
    entry = next(x for x in changed["ranked_candidates"] if x["candidate_id"] == cid)
    entry["phase_results"][2]["chunk_predictions"][0]["model_config_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="STOP_L4_FROZEN_PHASE_MODEL_MISMATCH"):
        core.check_stage_b_source_coverage(changed, core.frozen_l4_manifest("."))


def _job():
    m = core.frozen_l4_manifest(".")
    spec = core.l4_cell_specs(m)[0]
    return {
        **spec, "run_id": "l4_20261014_a1", "code_sha": "a" * 40,
        "manifest_sha256": core.FROZEN_MANIFEST_SHA,
        "source_digest": "b" * 64,
        "provider_fingerprint": "c" * 64,
        "source_snapshot_sha256": "d" * 64,
        "quote_sha256": "e" * 64,
        "p0_prefix": runner.EXPECTED_P0_PREFIX,
        "expected_signal_sha256": "f" * 64,
    }


def _identity(job):
    return {
        k: job[k] for k in (
            "run_id", "code_sha", "manifest_sha256", "source_digest", "provider_fingerprint",
            "source_snapshot_sha256", "quote_sha256", "p0_prefix",
        )
    }


def _frame():
    return pd.DataFrame({"account": [1e8, 1e8 + 1.0], "bench": [0.1, 0.2],
                         "return": [0., 1e-8], "cost": [0., 0.0001],
                         "total_cost": [0., 2.]})


def test_l4_actual_atomic_output_sha_receipts(tmp_path):
    job = _job()
    report = _frame()
    artifacts = {"fixed_report.parquet": report, "signal.parquet": report[["return"]],
                 "decisions.json": [{"orders": []}]}
    rec = runner.publish_l4_cell(tmp_path, job, _identity(job), artifacts, {
        "metrics": {"strategy_total_return": 0.01}, "n_days": 424,
    })
    assert rec["status"] == "PASS"
    assert rec["job_sha256"] == stable_sha(job)
    assert runner.checked_l4_cell(tmp_path, job, _identity(job)) == rec
    with pytest.raises(ValueError, match="STOP_L4_JOB_SHA_OR_PASS"):
        runner.checked_l4_cell(tmp_path, {**job, "expected_signal_sha256": "0" * 64}, _identity(job))
    with pytest.raises(ValueError, match="STOP_L4_CELL_IDENTITY"):
        runner.checked_l4_cell(tmp_path, job, {**_identity(job), "quote_sha256": "0" * 64})
    directory = runner.l4_cell_dir(tmp_path, job)
    (directory / "decisions.json").write_text("corrupt", encoding="utf-8")
    with pytest.raises(ValueError, match="STOP_L4_ARTIFACT_BYTES"):
        runner.checked_l4_cell(tmp_path, job, _identity(job))


def test_l4_partial_and_identity_overrides_blocked(tmp_path):
    job = _job()
    artifacts = {"fixed_report.parquet": _frame(), "signal.parquet": _frame()[["return"]],
                 "decisions.json": [{"orders": []}]}
    with pytest.raises(ValueError, match="STOP_L4_SIGNED_FIELD_OVERRIDE"):
        runner.publish_l4_cell(tmp_path, job, _identity(job), artifacts, {"quote_sha256": "e" * 64})
    with pytest.raises(ValueError, match="STOP_L4_ARTIFACT_SET"):
        runner.publish_l4_cell(tmp_path, job, _identity(job), {"decisions.json": []}, {})
    directory = runner.l4_cell_dir(tmp_path, job)
    directory.parent.mkdir(parents=True)
    temporary = directory.parent / (directory.name + ".tmp")
    temporary.mkdir()
    with pytest.raises(ValueError, match="STOP_L4_PARTIAL_TEMP_RECEIPT"):
        runner.publish_l4_cell(tmp_path, job, _identity(job), artifacts, {})


def test_p0_daily_ledger_tolerances_and_date_integrity():
    f = _frame()
    runner.verify_reference_report(f, f.copy())
    g = f.copy()
    g.loc[1, "return"] += 1e-5
    with pytest.raises(ValueError, match="STOP_L4_P0"):
        runner.verify_reference_report(g, f)
    # Valid two rows aren't the real 424-day P0 reference.
    with pytest.raises(ValueError, match="STOP_L4_P0_DAILY_INDEX"):
        runner.verify_reference_report(f, g)


def _receipts():
    manifest = core.frozen_l4_manifest(".")
    return [{
        **x, "status": "PASS", "n_days": 424,
        "coverage_ok": True, "ledger_ok": True, "data_ok": True,
        "metrics": {"relative_excess_cagr": 0.1, "strategy_total_return": 0.12,
                    "strategy_max_drawdown": -0.1}, "mean_turnover": 0.5,
    } for x in core.l4_cell_specs(manifest)]


def test_l4_scoreboard_five_phases_or_no_terminal_verdict(monkeypatch):
    import audit.csi1000_layered_search_replay as replay

    def paired(chall, base, report_loader):
        rows_c, rows_b = [], []
        for c, b in zip(chall, base):
            report_loader(c)
            for arr, row in ((rows_c, c), (rows_b, b)):
                arr.append({
                    "phase": row["phase"],
                    "net_relative_cagr_pp": 2.0 if arr is rows_c else 0.,
                    "net_total_return": 0.2 if arr is rows_c else 0.1,
                    "mdd_pp": 8. if arr is rows_c else 10.,
                    "turnover": 0.5,
                    "n_days": 424,
                    "coverage_ok": True, "ledger_ok": True, "data_ok": True,
                    "plus10bp_advantage_pp": 1.0,
                })
        return rows_c, rows_b, 1.0

    monkeypatch.setattr(replay, "paired_rows", paired)
    index = pd.bdate_range("2024-12-01", periods=424)
    reports = {}
    for c in core.l4_cell_specs(core.frozen_l4_manifest(".")):
        reports[c["config_sha256"], c["phase"]] = pd.DataFrame({"bench": np.zeros(424)}, index=index)
    def load(row):
        return reports[row["config_sha256"], row["phase"]]
    rows = _receipts()
    result = core.l4_decision_from_receipts(core.frozen_l4_manifest("."), rows, load)
    assert result["completed_cells"] == 20
    assert result["decision"] == "PROPOSE_PRODUCT_PROFILE_VERSION"
    assert result["promotion_authorized"] is False
    assert len(result["challenger_scoreboard"]) == 3
    with pytest.raises(ValueError, match="STOP_L4_INCOMPLETE_FIVE_PHASE_FINAL"):
        core.l4_decision_from_receipts(core.frozen_l4_manifest("."), rows[:-1], load)
    reports[rows[6]["config_sha256"], rows[6]["phase"]] = pd.DataFrame(
        {"bench": np.ones(424)}, index=index
    )
    with pytest.raises(ValueError, match="STOP_L4_BENCHMARK_MISMATCH"):
        core.l4_decision_from_receipts(core.frozen_l4_manifest("."), rows, load)


def test_l4_runner_contract_static_no_fit_and_no_usd_gates():
    source = Path("audit/csi1000_l4_modal.py").read_text(encoding="utf-8")
    assert "model.fit(" not in source
    assert "SOURCE_VOLUME, create_if_missing=False" in source
    assert "version=2" in source
    assert "read_only=True" in source
    assert "max_workers <= 80" in source
    assert "L4_INCOMPLETE_SAFE_PAUSE" in source
    assert "l4_final_decision" in source
    assert "budget_gate(" not in source
    assert "usd_per_fit_reserve" not in source
    assert "require_p0_pass" in source
    assert "verify_reference_report" in source
    assert "STOP_L4_15_SOURCE_PREFLIGHT" in source
    assert "0 new LightGBM fits" in source
