"""Validate a pre-registered research evidence bundle for independent review."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quant_bootstrap import ensure_quant_import_paths

ensure_quant_import_paths()

import artifact_io as json
from research_governance import promotion_gate


def check_bundle(contract_path, evidence_path):
    contract = json.loads(Path(contract_path).read_text())
    path = Path(evidence_path)
    evidence = json.loads(path.read_text())
    result = promotion_gate(contract, evidence)
    for kind in ("frozen_predictions", "order_replay", "dated_st", "liquidity", "cost_stress", "multiple_testing"):
        receipt = evidence.get(kind, {})
        reference = receipt.get("path")
        artifact = path.parent / reference if isinstance(reference, str) else None
        if (
            artifact is None
            or not artifact.is_file()
            or hashlib.sha256(artifact.read_bytes()).hexdigest() != receipt.get("sha256")
        ):
            result["failed_gates"].append(f"{kind}: missing or mismatched evidence artifact")
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
