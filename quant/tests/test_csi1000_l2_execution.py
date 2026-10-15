"""Offline L2 receipt, budget and four-fold promotion negative controls."""

from __future__ import annotations

from pathlib import Path

import pytest

from audit import csi1000_l2_execution as l2
from audit.csi1000_layered_search_core import stable_sha


def _meta():
    return {
        "run_id": "l2_20261014_a1",
        "code_sha": "a" * 40,
        "plan_sha256": "b" * 64,
        "provider_fingerprint": "f" * 64,
        "source_snapshot_sha256": "e" * 64,
        "quote_sha256": "d" * 64,
        "p0_prefix": "csi1000_layered_search/contract/old/p0/123456789abc",
    }


def _short_job(fold="fold1"):
    return {
        **_meta(),
        "kind": "short",
        "candidate_id": "c" * 64,
        "fold_id": fold,
        "fold": {
            "train": ["2016-01-01", "2020-01-01"],
            "valid": ["2020-01-31", "2020-11-01"],
            "signal": ["2021-01-01", "2021-01-29"],
            "execution": ["2021-01-04", "2021-02-01"],
        },
        "model_params": {"learning_rate": 0.1},
        "model_sha256": "1" * 64,
    }


def test_l2_run_id_and_prefix_fail_closed():
    assert l2.prefix_for("l2_20261014_a1", "a" * 40).endswith("l2/aaaaaaaaaaaa")
    for run_id in ("l1_20261014_a1", "../l2_xxx", "l2_a/../", "l2_"):
        with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
            l2.prefix_for(run_id, "a" * 40)
    with pytest.raises(ValueError, match="full immutable code"):
        l2.prefix_for("l2_20261014_a1", "abc")


def test_l2_job_id_is_unique_and_phase_bounds():
    a = _short_job()
    b = _short_job("fold2")
    assert l2.job_id(a) != l2.job_id(b)
    long = {**a, "kind": "long", "phase": 0, "retrain_asof": "2025-01-02", "signal_end": "2025-01-29"}
    phase = {**long, "kind": "phase"}
    assert l2.job_id(long) != l2.job_id(phase)
    with pytest.raises(ValueError, match="long phase"):
        l2.job_id({**phase, "phase": 4})
    with pytest.raises(ValueError, match="as-of"):
        l2.job_id({**long, "retrain_asof": "../old"})


def test_l2_receipt_requires_exact_bytes_job_and_identity(tmp_path):
    job = _short_job()
    identity = _meta()

    class DummyParquet:
        def __init__(self, data):
            self.data = data

        def to_parquet(self, path):
            Path(path).write_bytes(self.data)

    content = {
        "prediction.parquet": DummyParquet(b"prediction"),
        "checkpoint.txt": "lightgbm-booster",
        "report.parquet": DummyParquet(b"report"),
        "decisions.json": [{"orders": []}],
    }
    saved = l2.publish_receipt(
        tmp_path,
        job,
        identity,
        content,
        {
            "candidate_id": job["candidate_id"],
            "fold_id": "fold1",
            "metrics": {"x": 1.0},
        },
    )
    assert saved["status"] == "PASS"
    assert saved["job_sha256"] == stable_sha(job)
    assert l2.checked_receipt(tmp_path, job, identity) == saved
    assert l2.publish_receipt(tmp_path, job, identity, content, {}) == saved
    with pytest.raises(ValueError, match="identity drift"):
        l2.checked_receipt(tmp_path, job, {**identity, "provider_fingerprint": "x"})
    with pytest.raises(ValueError, match="job spec mismatch"):
        l2.checked_receipt(tmp_path, {**job, "model_sha256": "f" * 64}, identity)
    d = l2.receipt_path(tmp_path, job)
    (d / "checkpoint.txt").write_text("corrupted", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA drift"):
        l2.checked_receipt(tmp_path, job, identity)


def test_l2_receipt_wrong_artifact_schema_fails(tmp_path):
    with pytest.raises(ValueError, match="artifact list"):
        l2.publish_receipt(tmp_path, _short_job(), _meta(), {"x": b"bad"}, {})


@pytest.mark.parametrize(
    "attempts,wave,reserved,unit,actual,should_block",
    [
        (0, 1, 0.0, 0.01, 0.0, False),
        (318, 2, 0.0, 0.01, 0.0, False),
        (319, 2, 0.0, 0.01, 0.0, True),
        (0, 1, 11.9, 0.2, 0.0, True),
        (0, 1, 0.0, -1, 0.0, True),
        (0, 1, 0.0, float("nan"), 0.0, True),
        (0, 1, 0.0, 0.01, float("nan"), True),
    ],
)
def test_l2_fit_and_budget_fail_closed(attempts, wave, reserved, unit, actual, should_block):
    if should_block:
        with pytest.raises(ValueError, match="STOP_L2"):
            l2.budget_gate(attempts, wave, reserved, unit, actual)
    else:
        l2.budget_gate(attempts, wave, reserved, unit, actual)


def test_l2_four_fold_selection_complete_and_pre_registered(monkeypatch):
    from csi1000_tuner_core import METRIC_VERSION

    base_metrics = {
        "metric_version": METRIC_VERSION,
        "strategy_total_return": 0.1,
        "relative_excess_cagr": 0.1,
        "strategy_max_drawdown": -0.1,
        "information_ratio": 1.0,
        "sharpe": 1.0,
        "account_return_max_error": 0.0,
        "mean_turnover": 0.5,
        "total_cost_sum": 0.001,
    }
    import csi1000_tuner_core

    def summarized(candidate, rows):
        values = [r["relative_excess_cagr"] for r in rows]
        return {
            "fold_summary": {
                "relative_excess_cagr_worst": min(values),
                "relative_excess_cagr_median": sum(values) / 4,
                "strategy_max_drawdown_worst": -0.1,
            }
        }

    monkeypatch.setattr(csi1000_tuner_core, "summarize_candidate", summarized)
    ids = [f"{i:064x}" for i in range(1, 5)]
    plan = {"new_candidates": [{"candidate_id": cid, "model_params": {}} for cid in ids]}
    source = {
        "ranked_candidates": [
            {
                "candidate": {"is_baseline": True},
                "fold_summary": {
                    "relative_excess_cagr_worst": 0.1,
                    "relative_excess_cagr_median": 0.1,
                    "strategy_max_drawdown_worst": -0.1,
                },
            }
        ]
    }
    rows = [
        {
            "candidate_id": cid,
            "fold_id": f"fold{j}",
            "status": "PASS",
            "kind": "short",
            "metrics": {**base_metrics, "relative_excess_cagr": score},
        }
        for cid, score in zip(ids, (0.12, 0.11, 0.10, 0.09))
        for j in range(1, 5)
    ]
    answer = l2.choose_new_for_long(plan, rows, source)
    assert answer["selected_new_candidate_ids"] == ids[:2]
    assert answer["short_count"] == 16
    with pytest.raises(ValueError, match="short 16/16"):
        l2.choose_new_for_long(plan, rows[:-1], source)
    with pytest.raises(ValueError, match="duplicate short"):
        l2.choose_new_for_long(plan, rows + rows[:1], source)


def test_runner_has_no_unreviewed_second_app_or_fit_retries():
    source = Path("quant/code/research/layered_search/csi1000_l2_modal.py").read_text(encoding="utf-8")
    assert "with_mount_options(read_only=True)" in source
    assert "retries=0" in source
    assert "@app.local_entrypoint()" in source
    assert "fit_remote.with_options(max_containers=max_workers).map(todo)" in source
    assert "STOP_L2_SMOKE_NOT_VERIFIED" in source
    assert "attempts_remote.remote(meta, todo" in source
    assert "bill_is_operator_verified" in source
    assert "model.fit(dataset)" in source
    assert "modal deploy" not in source
    assert "--detach" not in source


def test_l2_phase_metadata_cannot_override_signed_quote_identity(tmp_path):
    """Regression: phase Remote used to re-add quote_sha256 in details.

    Smoke only runs fit_remote, so the conflict appeared after many long fits.
    Compare the actual AST payload against the signer and exercise the writer.
    """
    import ast

    source = Path("quant/code/research/layered_search/csi1000_l2_modal.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    phase_fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "phase_remote")
    details = next(
        n.value
        for n in ast.walk(phase_fn)
        if isinstance(n, ast.Assign)
        and len(n.targets) == 1
        and isinstance(n.targets[0], ast.Name)
        and n.targets[0].id == "details"
    )
    assert isinstance(details, ast.Dict)
    keys = {key.value for key in details.keys if isinstance(key, ast.Constant)}
    protected = set(_meta()) | {"status", "kind", "job_sha256", "artifact_sha256"}
    assert not (keys & protected), f"phase details must not re-sign identity fields: {keys & protected}"

    class MockFrame:
        def to_parquet(self, path):
            Path(path).write_bytes(b"valid-test-parquet-sha-proxy")

    job = {**_short_job(), "kind": "phase", "phase": 0}
    artifacts = {
        "report.parquet": MockFrame(),
        "decisions.json": [{"orders": []}],
        "signal.parquet": MockFrame(),
    }
    good = l2.publish_receipt(tmp_path, job, _meta(), artifacts, {"candidate_id": job["candidate_id"]})
    assert good["quote_sha256"] == _meta()["quote_sha256"]
    assert l2.checked_receipt(tmp_path, job, _meta()) == good
    with pytest.raises(ValueError, match="cannot override signed identity"):
        l2.publish_receipt(tmp_path / "bad", job, _meta(), artifacts, {"quote_sha256": _meta()["quote_sha256"]})
