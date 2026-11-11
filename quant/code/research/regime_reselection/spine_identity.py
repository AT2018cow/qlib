"""Canonical identities and receipts for G1 spine slices and assemblies."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

SCHEMA_VERSION = 2
ASSEMBLY_FIELDS = (
    "run_id",
    "run_end",
    "market",
    "spine",
    "profile",
    "label_horizon",
    "data_start",
    "fit_start",
    "seed",
    "num_threads",
    "kernels",
    "joblib_backend",
    "source_sha256",
    "environment_sha256",
    "provider_kind",
    "provider_fingerprint",
    "model_yaml_sha256",
    "strategy_contract_sha256",
)
IDENTITY_FIELDS = (
    "run_id",
    "run_end",
    "market",
    "spine",
    "profile",
    "label_horizon",
    "retrain_asof",
    "signal_end",
    "data_start",
    "fit_start",
    "seed",
    "num_threads",
    "kernels",
    "joblib_backend",
    "source_sha256",
    "environment_sha256",
    "provider_kind",
    "provider_fingerprint",
    "model_yaml_sha256",
    "strategy_contract_sha256",
)


def _content_sha(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def canonical_job_identity(
    job,
    *,
    source_sha256,
    environment_sha256,
    provider_kind="chenditc",
    provider_fingerprint,
    model_yaml_sha256,
    strategy_contract_sha256,
):
    body = {"schema_version": SCHEMA_VERSION}
    for field in IDENTITY_FIELDS:
        if field in (
            "source_sha256",
            "environment_sha256",
            "provider_kind",
            "provider_fingerprint",
            "model_yaml_sha256",
            "strategy_contract_sha256",
        ):
            value = {
                "source_sha256": source_sha256,
                "environment_sha256": environment_sha256,
                "provider_kind": provider_kind,
                "provider_fingerprint": provider_fingerprint,
                "model_yaml_sha256": model_yaml_sha256,
                "strategy_contract_sha256": strategy_contract_sha256,
            }[field]
        else:
            value = job.get(field)
        if value is None and field not in ("kernels", "joblib_backend"):
            raise ValueError(f"job identity field is missing: {field}")
        body[field] = value
    return {**body, "sha256": _content_sha(body)}


def _identity_body(identity):
    if not isinstance(identity, dict):
        raise ValueError("identity must be an object")
    return {key: value for key, value in identity.items() if key != "sha256"}


def _validate_canonical_identity(identity, fields, label):
    if not isinstance(identity, dict) or identity.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{label} schema is missing or unsupported")
    expected_keys = {"schema_version", "sha256", *fields}
    if set(identity) != expected_keys:
        raise ValueError(f"{label} fields differ from the canonical schema")
    for field in fields:
        if identity.get(field) is None and field not in ("kernels", "joblib_backend"):
            raise ValueError(f"{label} field is missing: {field}")
    if identity.get("sha256") != _content_sha(_identity_body(identity)):
        raise ValueError(f"{label} hash changed")
    return identity


def canonical_assembly_identity(job, **context):
    body = {"schema_version": SCHEMA_VERSION}
    for field in ASSEMBLY_FIELDS:
        if field in context:
            value = context[field]
        else:
            value = job.get(field)
        if value is None and field not in ("kernels", "joblib_backend"):
            raise ValueError(f"assembly identity field is missing: {field}")
        body[field] = value
    return {**body, "sha256": _content_sha(body)}


def validate_assembly_identity(identity):
    """Validate the exact coordinator-sealed assembly identity and self hash."""
    return _validate_canonical_identity(identity, ASSEMBLY_FIELDS, "assembly identity")


def attach_job_identity(job, context):
    identity = canonical_job_identity(job, **context)
    return {**job, "job_identity": identity, "job_sha256": identity["sha256"]}


def job_sha256(job):
    value = job.get("job_sha256")
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError("job has no canonical job_sha256")
    return value


def receipt_dir(root, job):
    return Path(root) / job["run_id"] / job["market"] / job["spine"] / f"job_{job_sha256(job)}"


def seal_receipt(receipt):
    body = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    return {**body, "receipt_sha256": _content_sha(body)}


def validate_slice_receipt(receipt, job, prediction_path):
    if not isinstance(receipt, dict) or receipt.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("slice receipt schema is missing or unsupported")
    if receipt.get("status") != "PASS" or receipt.get("kind") != "g1_spine_slice_v2":
        raise ValueError("slice receipt is not a PASS v2 slice")
    if receipt.get("job_sha256") != job_sha256(job) or receipt.get("job_identity") != job.get("job_identity"):
        raise ValueError("slice receipt job identity changed")
    job_identity = receipt.get("job_identity")
    _validate_canonical_identity(job_identity, IDENTITY_FIELDS, "slice receipt job identity")
    if receipt.get("job_sha256") != job_identity.get("sha256"):
        raise ValueError("slice receipt job sha differs from identity")
    if receipt.get("receipt_sha256") != seal_receipt(receipt)["receipt_sha256"]:
        raise ValueError("slice receipt self hash changed")
    prediction_path = Path(prediction_path)
    if not prediction_path.is_file():
        raise ValueError("slice prediction is missing")
    if receipt.get("prediction_sha256") != hashlib.sha256(prediction_path.read_bytes()).hexdigest():
        raise ValueError("slice prediction hash changed")
    days = receipt.get("days")
    if not isinstance(days, list) or not days:
        raise ValueError("slice receipt has no day list")
    if days != sorted(set(days)):
        raise ValueError("slice receipt day list is not sorted/unique")
    return receipt


def validate_slice_receipt_self(receipt, prediction_path):
    if not isinstance(receipt, dict) or receipt.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("slice receipt schema is missing or unsupported")
    if receipt.get("status") != "PASS" or receipt.get("kind") != "g1_spine_slice_v2":
        raise ValueError("slice receipt is not a PASS v2 slice")
    job_identity = receipt.get("job_identity")
    _validate_canonical_identity(job_identity, IDENTITY_FIELDS, "slice receipt job identity")
    if receipt.get("job_sha256") != job_identity.get("sha256"):
        raise ValueError("slice receipt job sha differs from identity")
    if receipt.get("receipt_sha256") != seal_receipt(receipt)["receipt_sha256"]:
        raise ValueError("slice receipt self hash changed")
    prediction_path = Path(prediction_path)
    if not prediction_path.is_file():
        raise ValueError("slice prediction is missing")
    if receipt.get("prediction_sha256") != hashlib.sha256(prediction_path.read_bytes()).hexdigest():
        raise ValueError("slice prediction hash changed")
    days = receipt.get("days")
    if not isinstance(days, list) or not days:
        raise ValueError("slice receipt has no day list")
    if days != sorted(set(days)):
        raise ValueError("slice receipt day list is not sorted/unique")
    return receipt


def validate_slice_set(receipts, *, identity_sha256=None, identity_common=None, assembly_identity=None, expected_days):
    if not isinstance(receipts, list) or not receipts:
        raise ValueError("slice receipts are missing")
    expected = list(expected_days)
    if assembly_identity is not None:
        validate_assembly_identity(assembly_identity)
    days = []
    seen = set()
    ordered = sorted(receipts, key=lambda item: item.get("retrain_asof", ""))
    for receipt in ordered:
        job_identity = receipt.get("job_identity") or {}
        if identity_sha256 is not None and job_identity.get("sha256") != identity_sha256:
            raise ValueError("slice belongs to a different job identity")
        if identity_common is not None:
            for key, value in identity_common.items():
                if job_identity.get(key) != value:
                    raise ValueError(f"slice identity field changed: {key}")
        if assembly_identity is not None:
            for key in ASSEMBLY_FIELDS:
                if job_identity.get(key) != assembly_identity.get(key):
                    raise ValueError(f"slice assembly identity field changed: {key}")
        block_days = receipt.get("days")
        if not isinstance(block_days, list) or not block_days:
            raise ValueError("slice has no days")
        for day in block_days:
            if day in seen:
                raise ValueError("duplicate slice day")
            seen.add(day)
            days.append(day)
    if days != expected:
        missing = sorted(set(expected) - set(days))[:5]
        extra = sorted(set(days) - set(expected))[:5]
        raise ValueError(f"slice coverage mismatch: missing={missing} extra={extra}")
    return ordered


def assembly_receipt(
    *,
    run_id,
    market,
    spine,
    start,
    end,
    calendar_sha256,
    identity_sha256,
    expected_days,
    slice_receipts,
    signal_sha256,
):
    ordered = sorted(slice_receipts, key=lambda item: item["retrain_asof"])
    body = {
        "schema_version": SCHEMA_VERSION,
        "kind": "g1_spine_v2",
        "status": "PASS",
        "run_id": run_id,
        "market": market,
        "spine": spine,
        "start": start,
        "end": end,
        "calendar_sha256": calendar_sha256,
        "expected_days_sha256": _content_sha(list(expected_days)),
        "identity_sha256": identity_sha256,
        "slice_receipt_sha256": [item["receipt_sha256"] for item in ordered],
        "slice_retrain_asof": [item["retrain_asof"] for item in ordered],
        "signal_sha256": signal_sha256,
    }
    return seal_receipt(body)


def validate_assembly_receipt(
    receipt,
    *,
    run_id,
    market,
    spine,
    start,
    end,
    calendar_sha256,
    identity_sha256,
    slice_receipts,
    signal_sha256,
    expected_days=None,
):
    if not isinstance(receipt, dict) or receipt.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("assembly receipt schema is missing or unsupported")
    if receipt.get("kind") != "g1_spine_v2" or receipt.get("status") != "PASS":
        raise ValueError("assembly receipt is not a PASS v2 receipt")
    expected = {
        "run_id": run_id,
        "market": market,
        "spine": spine,
        "start": start,
        "end": end,
        "calendar_sha256": calendar_sha256,
        "identity_sha256": identity_sha256,
        "signal_sha256": signal_sha256,
    }
    for key, value in expected.items():
        if receipt.get(key) != value:
            raise ValueError(f"assembly receipt {key} changed")
    if expected_days is not None and receipt.get("expected_days_sha256") != _content_sha(list(expected_days)):
        raise ValueError("assembly receipt expected day set changed")
    ordered = sorted(slice_receipts, key=lambda item: item["retrain_asof"])
    if receipt.get("slice_receipt_sha256") != [item["receipt_sha256"] for item in ordered]:
        raise ValueError("assembly receipt slice set changed")
    if receipt.get("receipt_sha256") != seal_receipt(receipt)["receipt_sha256"]:
        raise ValueError("assembly receipt self hash changed")
    return receipt
