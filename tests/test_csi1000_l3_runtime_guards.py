"""L3 offline regressions for formerly untested paid-run integration paths.

Every test is local and must not launch a Modal job.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pandas as pd
import pytest

from audit import csi1000_l3_core as core
from audit import csi1000_l3_modal as runner
from audit.csi1000_layered_search_core import stable_sha


def sample_job():
    return {
        "run_id": "l3_20261014_a1",
        "code_sha": "a" * 40,
        "plan_sha256": "b" * 64,
        "provider_fingerprint": "c" * 64,
        "source_snapshot_sha256": "d" * 64,
        "quote_sha256": "e" * 64,
        "p0_prefix": "csi1000_layered_search/csi1000_layered_search_v1/p0_20261013_c1/p0/cffb9cd56d52",
        "kind": "l3retrain",
        "candidate_id": "f" * 64,
        "model_params": {"learning_rate": 0.1},
        "model_sha256": "1" * 64,
        "label_horizon": 20,
        "retrain_frequency": 10,
        "phase": 0,
        "retrain_asof": "2024-12-31",
        "signal_end": "2025-01-10",
    }


def identity(job):
    return {
        k: job[k]
        for k in (
            "run_id",
            "code_sha",
            "plan_sha256",
            "provider_fingerprint",
            "source_snapshot_sha256",
            "quote_sha256",
            "p0_prefix",
        )
    }


def test_phase0_signal_preserves_anchor_and_drops_earlier_phase_predictions():
    calendar = list(pd.bdate_range("2024-10-01", "2026-11-01").strftime("%Y-%m-%d"))
    expected = calendar[calendar.index(core.SIGNAL_ANCHOR) : calendar.index(core.EXECUTION_END)]
    dates = list(pd.bdate_range("2024-12-01", "2026-09-30"))
    frame = pd.DataFrame(
        {"score": range(len(dates))},
        index=pd.MultiIndex.from_product([dates, ["000001.SZ"]], names=["datetime", "instrument"]),
    )
    cropped = core.trim_l3_signal(frame, calendar)
    assert list(pd.Index(cropped.index.get_level_values(0).unique()).strftime("%Y-%m-%d")) == expected
    assert str(cropped.index.get_level_values(0).min())[:10] == core.SIGNAL_ANCHOR
    assert str(cropped.index.get_level_values(0).max())[:10] < core.EXECUTION_END
    with pytest.raises(ValueError, match="STOP_L3_SIGNAL_DAY_COVERAGE"):
        core.trim_l3_signal(frame[frame.index.get_level_values(0) != pd.Timestamp("2026-09-29")], calendar)
    with pytest.raises(ValueError, match="STOP_L3_DUPLICATE_PREDICTION"):
        core.trim_l3_signal(pd.concat([frame, frame.iloc[:1]]), calendar)


def test_atomic_receipt_rejects_changed_job_and_corrupt_bytes(tmp_path):
    job = sample_job()
    payload = {
        "prediction.parquet": pd.DataFrame({"score": [0.42]}),
        "checkpoint.txt": "synthetic booster for offline test",
    }
    saved = runner.l3_publish_receipt(tmp_path, job, identity(job), payload, {"fit_performed": True})
    assert saved["job_sha256"] == stable_sha(job)
    assert runner.l3_checked_receipt(tmp_path, job, identity(job)) == saved
    with pytest.raises(ValueError, match="STOP_L3_JOB_SPEC_SHA_DRIFT"):
        runner.l3_checked_receipt(tmp_path, {**job, "signal_end": "2025-01-11"}, identity(job))
    with pytest.raises(ValueError, match="receipt identity drift"):
        runner.l3_checked_receipt(tmp_path, job, {**identity(job), "quote_sha256": "2" * 64})
    path = runner.l3_receipt_path(tmp_path, job)
    (path / "checkpoint.txt").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="L3 receipt bytes drift"):
        runner.l3_checked_receipt(tmp_path, job, identity(job))


def test_receipt_rejects_partial_manifest_overwrite_and_temp(tmp_path):
    job = sample_job()
    correct = {"prediction.parquet": pd.DataFrame({"score": [0.5]}), "checkpoint.txt": "booster"}
    with pytest.raises(ValueError, match="STOP_L3_ARTIFACTS_INCOMPLETE"):
        runner.l3_publish_receipt(tmp_path, job, identity(job), {"checkpoint.txt": "booster"}, {})
    with pytest.raises(ValueError, match="STOP_L3_SIGNED_FIELD_OVERRIDE"):
        runner.l3_publish_receipt(tmp_path, job, identity(job), correct, {"quote_sha256": job["quote_sha256"]})
    directory = runner.l3_receipt_path(tmp_path, job)
    directory.parent.mkdir(parents=True)
    temporary = directory.parent / (directory.name + ".tmp")
    temporary.mkdir()
    with pytest.raises(ValueError, match="STOP_L3_UNFINISHED_TEMP_RECEIPT"):
        runner.l3_publish_receipt(tmp_path, job, identity(job), correct, {})
    temporary.rmdir()
    runner.l3_publish_receipt(tmp_path, job, identity(job), correct, {})
    with pytest.raises(ValueError, match="already exists"):
        runner.l3_publish_receipt(tmp_path, job, identity(job), correct, {})


@pytest.mark.skipif(runner.modal is None, reason="Modal SDK optional on offline-only machines")
def test_screened_variant_passes_signed_fields_into_l3_final_decision():
    anchor = {"candidate_id": core.ANCHOR_CANDIDATE}
    variant = core.STAGE1_VARIANTS[0]
    detail = {"worst_delta_pp": 1.2, "median_delta_pp": 2.3, "mdd_degrade_pp": 0.4, "turnover_ratio": 1.1}
    result = {
        "passed_coarse": True,
        "detail": detail,
        "ordinal_mapping": {"challenger_phases": [0, 5], "baseline_phases": [0, 10]},
    }
    row = runner._screened_variant(variant, result, anchor)
    assert row["worst_delta_pp"] == 1.2
    assert len(row["config_sha256"]) == 64
    assert core.stage2_qualified([row]) is True
    verdict, out = core.l3_decision([row])
    assert verdict == "PROPOSE_STRUCTURE"
    assert out["winner"]["config_sha256"] == row["config_sha256"]
    result["detail"] = {}
    with pytest.raises(ValueError, match="STOP_L3_SCREEN_MISSING"):
        runner._screened_variant(variant, result, anchor)
    losing = runner._screened_variant(variant, {**result, "passed_coarse": False}, anchor)
    assert core.stage2_qualified([losing]) is False
    assert core.l3_decision([losing])[0] == "KEEP_BASELINE_FOR_PRODUCT_SELECTION"


@pytest.mark.skipif(runner.modal is None, reason="Modal SDK optional on offline-only machines")
def test_actual_dispatch_waves_enforce_limit_and_verify_resume(monkeypatch):
    completed = {}
    attempts = []
    waves = []

    class Receipts:
        def remote(self, jobs):
            return [completed.get(job["i"]) for job in jobs]

    class Attempts:
        def remote(self, meta, jobs):
            attempts.extend(job["i"] for job in jobs)
            return {"attempt_count": len(attempts)}

    class Fit:
        def with_options(self, *, max_containers):
            assert max_containers == 80
            return self

        def map(self, jobs):
            waves.append(len(jobs))
            for job in jobs:
                completed[job["i"]] = {"status": "PASS"}
                yield completed[job["i"]]

    monkeypatch.setattr(runner, "receipts_remote", Receipts())
    monkeypatch.setattr(runner, "attempts_remote", Attempts())
    monkeypatch.setattr(runner, "fit_remote", Fit())
    jobs = [{"i": i} for i in range(167)]
    with pytest.raises(runner.SafePause):
        runner._dispatch_fit(jobs, {}, 80, [1])
    assert len(completed) == 80 and waves == [80]
    runner._dispatch_fit(jobs, {}, 80, None)
    assert len(completed) == 167
    assert waves == [80, 80, 7]
    assert len(attempts) == 167
    runner._dispatch_fit(jobs, {}, 80, None)
    assert len(attempts) == 167


def test_stage1_and_stage2_markers_are_separate_and_pause_cannot_claim_complete():
    text = Path("audit/csi1000_l3_modal.py").read_text(encoding="utf-8")
    assert 'freeze_remote.remote(meta, "l3_screen_stage1"' in text
    assert 'freeze_remote.remote(meta, "l3_screen_final"' in text
    assert "STOP_L3_SIGNED_FIELD_OVERRIDE" in text
    assert "STOP_L3_PAIRED_DATE_ALIGNMENT" in text
    assert "STOP_L3_PAIRED_BENCH_DRIFT" in text
    assert 'return "INCOMPLETE"' in text
    assert 'if result != "COMPLETE":' in text
    assert "budget_gate(" not in text
    assert "usd_per_fit_reserve" not in text
    assert "smoke_actual_usd" not in text
    assert "max_workers <= 80" in text
    assert "max_containers=max_workers" in text
    tree = ast.parse(text)
    names = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
    assert names.count("run_l3") == 1


def test_l3_still_checks_scientific_fit_scope_250_without_usd_control():
    assert core.check_l3_fit_scope(250, 250) == "OK_CONTINUE"
    assert core.check_l3_fit_scope(251, 250) == "STOP_EXPERIMENT_SCOPE"
    assert core.check_l3_fit_scope(250, 251) == "STOP_EXPERIMENT_SCOPE"
    assert "cost_ceiling_usd" not in core.make_l3_plan()
