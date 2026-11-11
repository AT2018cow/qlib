"""Validate a pre-registered research evidence bundle for independent review."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quant_bootstrap import ensure_quant_import_paths

ensure_quant_import_paths()

import artifact_io as json
from research_governance import digest, promotion_gate


def _safe_file(root, relative, label):
    if not isinstance(relative, str) or not relative:
        raise ValueError(f"{label} path is missing")
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label} path must stay inside the evidence root")
    root = Path(root).resolve()
    full = (root / path).resolve()
    if full != root and root not in full.parents:
        raise ValueError(f"{label} path escapes the evidence root")
    if not full.is_file():
        raise ValueError(f"{label} file is missing")
    return full


def _lower_sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def check_bundle(contract_path, evidence_path):
    contract_path = Path(contract_path).resolve()
    path = Path(evidence_path).resolve()
    contract = json.loads(contract_path.read_text())
    evidence = json.loads(path.read_text())
    result = promotion_gate(contract, evidence)
    contract_sha = digest(contract)
    if evidence.get("schema_version") != 1:
        result["failed_gates"].append("promotion evidence schema is missing or unsupported")
    try:
        registration_path = _safe_file(
            contract_path.parent, contract.get("registration_receipt_path"), "preregistration receipt"
        )
        if hashlib.sha256(registration_path.read_bytes()).hexdigest() != contract.get("registration_receipt_sha256"):
            raise ValueError("preregistration receipt hash")
        registration = json.loads(registration_path.read_text())
        if (
            registration.get("schema_version") != 1
            or registration.get("kind") != "preregistration"
            or registration.get("status") != "PASS"
            or registration.get("registered_at") != contract.get("registered_at")
            or registration.get("forward_start") != contract.get("forward_start")
            or registration.get("candidate_ids") != contract.get("candidate_ids")
        ):
            raise ValueError("preregistration receipt semantics")
    except (AttributeError, TypeError, ValueError):
        result["failed_gates"].append("missing or invalid preregistration receipt")
    for kind in ("frozen_predictions", "order_replay", "dated_st", "liquidity", "cost_stress", "multiple_testing"):
        receipt = evidence.get(kind, {})
        reference = receipt.get("path")
        try:
            receipt_path = _safe_file(path.parent, reference, f"{kind} receipt")
        except ValueError:
            receipt_path = None
        if (
            receipt_path is None
            or not _lower_sha(receipt.get("sha256"))
            or hashlib.sha256(receipt_path.read_bytes()).hexdigest() != receipt.get("sha256")
        ):
            result["failed_gates"].append(f"{kind}: missing or mismatched evidence artifact")
            continue
        try:
            payload = json.loads(receipt_path.read_text())
            identity = payload.get("identity") if isinstance(payload, dict) else None
            if (
                payload.get("schema_version") != 1
                or payload.get("kind") != kind
                or payload.get("status") != "PASS"
                or not isinstance(identity, dict)
                or identity.get("candidate_id") != evidence.get("candidate_id")
                or identity.get("contract_sha256") != contract_sha
                or identity.get("forward_start") != contract.get("forward_start")
                or identity.get("forward_end") != evidence.get("forward_dates", [None])[-1]
            ):
                raise ValueError("receipt identity")
            underlying = payload.get("artifact")
            if not isinstance(underlying, dict) or not _lower_sha(underlying.get("sha256")):
                raise ValueError("underlying artifact identity")
            underlying_path = _safe_file(path.parent, underlying.get("path"), f"{kind} underlying artifact")
            if hashlib.sha256(underlying_path.read_bytes()).hexdigest() != underlying["sha256"]:
                raise ValueError("underlying artifact hash")
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            result["failed_gates"].append(f"{kind}: invalid semantic receipt or underlying artifact")
    result.update(
        {
            "contract_sha256": contract_sha,
            "registration_receipt_sha256": contract.get("registration_receipt_sha256"),
            "evidence_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "candidate_id": evidence.get("candidate_id"),
            "market": evidence.get("market"),
            "profile": evidence.get("profile"),
            "source_sha256": evidence.get("source_sha256"),
            "environment_sha256": evidence.get("environment_sha256"),
            "provider_fingerprint": evidence.get("provider_fingerprint"),
            "window_start": evidence.get("forward_dates", [None])[0],
            "window_end": evidence.get("forward_dates", [None])[-1],
        }
    )
    result["promotion_eligible"] = not result["failed_gates"]
    result["status"] = "READY_FOR_INDEPENDENT_REVIEW" if result["promotion_eligible"] else "BLOCKED_EVIDENCE"
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = check_bundle(args.contract, args.evidence)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    raise SystemExit(0 if result["promotion_eligible"] else 2)
