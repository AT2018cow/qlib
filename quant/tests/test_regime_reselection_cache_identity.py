"""RR01: spine slice/assembly cache identity must bind the real experiment."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code/research/regime_reselection"))

import spine_identity as identity
from spine_jobs import build_jobs


def test_real_g1_entry_passes_full_assembly_identity_and_emits_v2_receipts():
    """AR01: the real coordinator/producer must use the shared v2 contract."""
    source = Path(__file__).resolve().parents[1] / "code/research/regime_reselection/spine_and_replay_modal.py"
    tree = ast.parse(source.read_text())
    assembly_arguments = []
    receipt_schemas = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "remote"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "assemble_spine"
        ):
            assembly_arguments.append(ast.unparse(node.args[5]))
        if isinstance(node, ast.Dict):
            values = {
                key.value: value
                for key, value in zip(node.keys, node.values)
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
            kind = values.get("kind")
            if isinstance(kind, ast.Constant) and kind.value == "g1_spine_slice_v2":
                receipt_schemas.append(values.get("schema_version"))
    assert assembly_arguments == ["assembly_identities[market, spine]"]
    assert len(receipt_schemas) == 1
    assert isinstance(receipt_schemas[0], ast.Attribute)
    assert ast.unparse(receipt_schemas[0]) == "spine_identity.SCHEMA_VERSION"


def _job(**overrides):
    job = {
        "run_id": "run-a",
        "run_end": "2024-02-05",
        "market": "csi300",
        "spine": "A",
        "profile": "59b93929_top10_drop1_10d",
        "label_horizon": 10,
        "retrain_asof": "2021-02-18",
        "signal_end": "2021-03-18",
        "data_start": "2015-01-01",
        "fit_start": "2016-01-01",
        "seed": 0,
        "num_threads": 8,
        "kernels": 8,
        "joblib_backend": "loky",
    }
    job.update(overrides)
    return job


def _context(**overrides):
    context = {
        "source_sha256": "a" * 64,
        "environment_sha256": "b" * 64,
        "provider_kind": "chenditc",
        "provider_fingerprint": "c" * 64,
        "model_yaml_sha256": "d" * 64,
        "strategy_contract_sha256": "e" * 64,
    }
    context.update(overrides)
    return context


def test_assembly_identity_self_hash_is_verified():
    validator = getattr(identity, "validate_assembly_identity", None)
    assert callable(validator), "the assembler needs a shared assembly-identity self-hash validator"
    assembly = identity.canonical_assembly_identity(_job(), **_context())
    assert validator(assembly) == assembly
    with pytest.raises(ValueError, match="hash"):
        validator({**assembly, "sha256": "f" * 64})


def test_any_identity_field_change_invalidates_the_job():
    base = identity.attach_job_identity(_job(), _context())
    assert identity.job_sha256(base) == identity.job_sha256(identity.attach_job_identity(_job(), _context()))
    changes = {
        "profile": "c98856b4_top10_drop1",
        "label_horizon": 20,
        "seed": 99,
        "fit_start": "2017-01-01",
        "data_start": "2014-01-01",
        "signal_end": "2021-03-19",
        "retrain_asof": "2021-02-19",
        "num_threads": 4,
        "kernels": 1,
        "joblib_backend": "threading",
        "run_end": "2024-02-06",
    }
    for field, value in changes.items():
        changed = identity.attach_job_identity(_job(**{field: value}), _context())
        assert changed["job_sha256"] != base["job_sha256"], field
    for field, value in {
        "source_sha256": "f" * 64,
        "environment_sha256": "f" * 64,
        "provider_fingerprint": "f" * 64,
        "model_yaml_sha256": "f" * 64,
        "strategy_contract_sha256": "f" * 64,
    }.items():
        changed = identity.attach_job_identity(_job(), _context(**{field: value}))
        assert changed["job_sha256"] != base["job_sha256"], field


def _slice(job, prediction, days, *, receipt_sha=None):
    receipt = {
        "schema_version": identity.SCHEMA_VERSION,
        "kind": "g1_spine_slice_v2",
        "status": "PASS",
        "job_identity": job["job_identity"],
        "job_sha256": job["job_sha256"],
        "retrain_asof": job["retrain_asof"],
        "signal_end": job["signal_end"],
        "days": days,
        "prediction_sha256": __import__("hashlib").sha256(prediction.read_bytes()).hexdigest(),
    }
    receipt = identity.seal_receipt(receipt)
    if receipt_sha is not None:
        receipt["receipt_sha256"] = receipt_sha
    return receipt


def test_slice_receipt_rejects_identity_or_prediction_changes(tmp_path):
    job = identity.attach_job_identity(_job(), _context())
    prediction = tmp_path / "prediction.parquet"
    prediction.write_bytes(b"fixed prediction")
    receipt = _slice(job, prediction, ["2021-02-18", "2021-02-19"])
    assert identity.validate_slice_receipt(receipt, job, prediction)["status"] == "PASS"
    old = {**receipt, "schema_version": 0}
    with pytest.raises(ValueError, match="schema"):
        identity.validate_slice_receipt(old, job, prediction)
    changed_job = identity.attach_job_identity(_job(profile="c98856b4_top10_drop1"), _context())
    with pytest.raises(ValueError, match="identity"):
        identity.validate_slice_receipt(receipt, changed_job, prediction)
    prediction.write_bytes(b"changed prediction")
    with pytest.raises(ValueError, match="prediction hash"):
        identity.validate_slice_receipt(receipt, job, prediction)


def test_slice_set_and_assembly_receipt_reject_missing_extra_duplicate_or_old_window(tmp_path):
    job = identity.attach_job_identity(_job(), _context())
    prediction = tmp_path / "prediction.parquet"
    prediction.write_bytes(b"fixed prediction")
    first = _slice(job, prediction, ["2021-02-18", "2021-02-19"])
    second = _slice(job, prediction, ["2021-02-22", "2021-02-23"])
    expected = ["2021-02-18", "2021-02-19", "2021-02-22", "2021-02-23"]
    ordered = identity.validate_slice_set([first, second], identity_sha256=job["job_sha256"], expected_days=expected)
    assert [item["retrain_asof"] for item in ordered] == ["2021-02-18", "2021-02-18"]
    with pytest.raises(ValueError, match="coverage"):
        identity.validate_slice_set([first], identity_sha256=job["job_sha256"], expected_days=expected)
    with pytest.raises(ValueError, match="duplicate"):
        identity.validate_slice_set([first, first], identity_sha256=job["job_sha256"], expected_days=expected)
    with pytest.raises(ValueError, match="coverage"):
        identity.validate_slice_set([first, second], identity_sha256=job["job_sha256"], expected_days=expected[:2])
    with pytest.raises(ValueError, match="different job identity"):
        identity.validate_slice_set([first, second], identity_sha256="f" * 64, expected_days=expected)
    assembly = identity.assembly_receipt(
        run_id="run-a",
        market="csi300",
        spine="A",
        start="2021-02-18",
        end="2021-02-23",
        calendar_sha256="9" * 64,
        identity_sha256=job["job_sha256"],
        expected_days=expected,
        slice_receipts=ordered,
        signal_sha256="8" * 64,
    )
    again = identity.assembly_receipt(
        run_id="run-a",
        market="csi300",
        spine="A",
        start="2021-02-18",
        end="2021-02-23",
        calendar_sha256="9" * 64,
        identity_sha256=job["job_sha256"],
        expected_days=expected,
        slice_receipts=ordered,
        signal_sha256="8" * 64,
    )
    assert again == assembly
    identity.validate_assembly_receipt(
        assembly,
        run_id="run-a",
        market="csi300",
        spine="A",
        start="2021-02-18",
        end="2021-02-23",
        calendar_sha256="9" * 64,
        identity_sha256=job["job_sha256"],
        slice_receipts=ordered,
        signal_sha256="8" * 64,
    )
    with pytest.raises(ValueError, match="start|end"):
        identity.validate_assembly_receipt(
            assembly,
            run_id="run-a",
            market="csi300",
            spine="A",
            start="2021-02-19",
            end="2021-02-23",
            calendar_sha256="9" * 64,
            identity_sha256=job["job_sha256"],
            slice_receipts=ordered,
            signal_sha256="8" * 64,
        )
    with pytest.raises(ValueError, match="slice set"):
        identity.validate_assembly_receipt(
            assembly,
            run_id="run-a",
            market="csi300",
            spine="A",
            start="2021-02-18",
            end="2021-02-23",
            calendar_sha256="9" * 64,
            identity_sha256=job["job_sha256"],
            slice_receipts=[first],
            signal_sha256="8" * 64,
        )


def test_build_jobs_attaches_identity_and_rejects_missing_context():
    calendar = [f"2021-01-{day:02d}" for day in range(1, 29)]
    contexts = {
        ("csi300", "A"): _context(),
        ("csi300", "B"): _context(strategy_contract_sha256="f" * 64),
    }
    jobs = build_jobs(
        calendar,
        calendar[0],
        calendar[10],
        "run-a",
        "sha",
        markets=("csi300",),
        spines=("A", "B"),
        identity_contexts=contexts,
    )
    assert len({job["job_sha256"] for job in jobs}) == 2
    assert all(job["job_identity"]["source_sha256"] == "a" * 64 for job in jobs)
    with pytest.raises(ValueError, match="missing identity context"):
        build_jobs(
            calendar,
            calendar[0],
            calendar[10],
            "run-a",
            "sha",
            markets=("csi300",),
            spines=("A",),
            identity_contexts={},
        )


def test_legacy_receipt_is_auditable_but_cannot_authorize_cache_hit(tmp_path):
    job = identity.attach_job_identity(_job(), _context())
    prediction = tmp_path / "prediction.parquet"
    prediction.write_bytes(b"legacy prediction")
    legacy = {
        "status": "PASS",
        "kind": "g1_spine_slice",
        "run_id": job["run_id"],
        "market": job["market"],
        "spine": job["spine"],
        "profile": job["profile"],
        "label_horizon": job["label_horizon"],
        "retrain_asof": job["retrain_asof"],
        "signal_end": job["signal_end"],
        "prediction_sha256": __import__("hashlib").sha256(prediction.read_bytes()).hexdigest(),
    }
    assert legacy["status"] == "PASS"
    with pytest.raises(ValueError, match="schema"):
        identity.validate_slice_receipt(legacy, job, prediction)


def test_assembly_identity_rejects_mixed_seed_slices(tmp_path):
    first_job = identity.attach_job_identity(_job(), _context())
    second_job = identity.attach_job_identity(_job(seed=1), _context())
    assembly = identity.canonical_assembly_identity(_job(), **_context())
    prediction = tmp_path / "prediction.parquet"
    prediction.write_bytes(b"fixed prediction")
    first = _slice(first_job, prediction, ["2021-02-18"])
    second = _slice(second_job, prediction, ["2021-02-22"])
    with pytest.raises(ValueError, match="assembly identity"):
        identity.validate_slice_set(
            [first, second],
            assembly_identity=assembly,
            expected_days=["2021-02-18", "2021-02-22"],
        )


def test_resealed_tampered_job_identity_is_rejected(tmp_path):
    job = identity.attach_job_identity(_job(), _context())
    prediction = tmp_path / "prediction.parquet"
    prediction.write_bytes(b"fixed prediction")
    receipt = _slice(job, prediction, ["2021-02-18"])
    receipt["job_identity"] = {**receipt["job_identity"], "seed": 99}
    receipt["job_sha256"] = receipt["job_identity"]["sha256"]
    receipt = identity.seal_receipt(receipt)
    with pytest.raises(ValueError, match="job identity hash"):
        identity.validate_slice_receipt_self(receipt, prediction)


def test_cli_overrides_are_sealed_into_job_identity():
    calendar = [f"2021-01-{day:02d}" for day in range(1, 29)]
    contexts = {("csi300", "A"): _context()}
    jobs = build_jobs(
        calendar,
        calendar[0],
        calendar[10],
        "run-a",
        "sha",
        markets=("csi300",),
        spines=("A",),
        kernels=1,
        joblib_backend="loky",
        identity_contexts=contexts,
    )
    assert jobs[0]["job_identity"]["kernels"] == 1
    assert jobs[0]["job_identity"]["joblib_backend"] == "loky"
