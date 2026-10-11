"""Offline reconcile for the CSI1000 layered search (PR-S0).

Recomputes ranking/fees/account checks from committed summaries, verifies
SHA256SUMS, evaluates STOP gates and emits decision.json. Pure Python; never
contacts Modal, never trains, never modifies production.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audit.csi1000_layered_search_core import (
    GLOBAL_CAP_USD,
    L1_CELL_CAP,
    L4_CELL_CAP,
    L4_CONFIG_CAP,
    STOP_CODES,
    BudgetLedger,
    decide_product,
    final_verdict,
)


def require(ok: bool, why: str) -> None:
    if not ok:
        raise ValueError("RECONCILE_BLOCKED: " + why)


def verify_sha256sums(root: str | Path) -> Dict[str, Any]:
    """Verify SHA256SUMS.txt against actual bytes (excluding itself)."""
    root = Path(root)
    sums = root / "SHA256SUMS.txt"
    require(sums.is_file(), f"SHA256SUMS.txt missing under {root}")
    bad: List[str] = []
    n = 0
    for line in sums.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, _, rel = line.partition("  ")
        target = root / rel.strip()
        require(target.is_file(), f"listed file missing: {rel}")
        h = hashlib.sha256()
        with target.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        n += 1
        if h.hexdigest() != digest.strip():
            bad.append(rel.strip())
    require(not bad, f"SHA mismatch: {bad}")
    return {"files": n, "status": "SHA_VERIFIED"}


def check_stage_summary(summary: Dict[str, Any]) -> Dict[str, Any]:
    """Validate one stage summary's completeness and no-overwrite invariants."""
    require(summary.get("training_performed") in (False,), "training flag must be false for PR-S0 stages")
    require(summary.get("promotion_authorized") is False, "promotion must never be pre-authorized")
    require(summary.get("source_volume_read_only") is True, "source must stay read-only")
    expected = int(summary.get("expected", -1))
    completed = int(summary.get("completed", -1))
    blocked = int(summary.get("blocked", -1))
    require(expected >= 0 and completed >= 0 and blocked >= 0, "summary counters missing")
    require(completed + blocked == expected, "summary counters inconsistent")
    if summary.get("stage") == "l1-c":
        require(expected <= L1_CELL_CAP, "L1 exceeds 264-cell cap")
    return {"stage": summary.get("stage"), "expected": expected, "completed": completed, "blocked": blocked}


def check_trial_ledger(path: str | Path) -> Dict[str, Any]:
    """Trial ledger must be append-only JSONL with unique cell keys and real App IDs."""
    rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    keys = [r.get("cell_key") for r in rows]
    require(len(set(keys)) == len(keys), "duplicate cell keys in trial ledger")
    for r in rows:
        require(r.get("app_id"), f"trial {r.get('cell_key')} missing real App ID")
        require(r.get("status") in ("PASS", "PASS_SKELETON", "BLOCKED", "RESUME_VERIFIED"), "unknown trial status")
    return {"trials": len(rows)}


def build_decision(finalists: List[Dict[str, Any]], spent_usd: float, fits_used: int) -> Dict[str, Any]:
    """Emit the product decision; defaults to KEEP_BASELINE without full-threshold winner."""
    require(spent_usd <= GLOBAL_CAP_USD + 1e-9, "spend exceeds USD30 global cap")
    code, detail = decide_product(finalists)
    require(code in STOP_CODES, "unknown decision code")
    return {
        "decision": code,
        "detail": detail,
        "spent_usd": spent_usd,
        "fits_used": fits_used,
        "l4_configs": len(finalists),
        "l4_cap": L4_CONFIG_CAP,
        "l4_cell_cap": L4_CELL_CAP,
        "historic_tail": "HISTORICALLY_CONSUMED",
        "oos_claim": False,
    }


def build_cli() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="PR-S0 offline reconcile: verify evidence, evaluate STOP/decision gates.")
    p.add_argument("--summary", default="", help="Stage summary JSON to validate.")
    p.add_argument("--ledger", default="", help="trial_ledger.jsonl to validate.")
    p.add_argument("--evidence-dir", default="", help="Directory containing SHA256SUMS.txt to verify.")
    p.add_argument("--finalists", default="", help="Finalists JSON for decision.json.")
    p.add_argument("--spent-usd", type=float, default=0.0, help="Total real spend in USD.")
    p.add_argument("--fits-used", type=int, default=0, help="Total real fit() calls.")
    p.add_argument("--out", default="", help="Write decision.json here.")
    return p


def main(argv: List[str] | None = None) -> int:
    args = build_cli().parse_args(argv)
    report: Dict[str, Any] = {"protocol": "csi1000_layered_search_v1"}
    if args.summary:
        report["stage"] = check_stage_summary(json.loads(Path(args.summary).read_text(encoding="utf-8")))
    if args.ledger:
        report["ledger"] = check_trial_ledger(args.ledger)
    if args.evidence_dir:
        report["sha"] = verify_sha256sums(args.evidence_dir)
    if args.finalists or args.out:
        finalists = json.loads(Path(args.finalists).read_text(encoding="utf-8")) if args.finalists else []
        decision = build_decision(finalists, args.spent_usd, args.fits_used)
        report["decision"] = decision
        if args.out:
            Path(args.out).write_text(json.dumps(decision, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    if not (args.summary or args.ledger or args.evidence_dir or args.finalists or args.out):
        build_cli().print_help()
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
