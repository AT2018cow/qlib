"""Modal P0/L1 runner for the CSI1000 budgeted layered search (PR-S0).

Read-only source, separate research volume, per-cell SHA resume, real-cost
ledger and fail-closed STOP gates. Zero training for p0/l1-a: frozen Stage-B
signals are replayed under different (TopK, DropN) combos only.

Foreground rule: when launched through OpenCode, NEVER pass ``--detach`` to
``modal run``. Keep the launcher attached with a tool timeout longer than the
worst-case queue + build + run + collect time. If the timeout cannot cover the
batch, shrink ``--batch-limit`` first or STOP. ``git checkout --detach`` is
unrelated and remains allowed.

Status: code only, NOT EXECUTED. Real spend needs explicit USD30 approval.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audit.csi1000_layered_search_core import (
    LAYER_CAPS_USD,
    PHASES_SCREEN_TWO,
    PROTOCOL_VERSION,
    RESEARCH_VOLUME,
    SOURCE_VOLUME,
    BudgetLedger,
    build_protocol,
    check_cache_hit,
    check_output_not_in_source,
    enumerate_cells,
    load_frozen_profiles,
    replay_cache_key,
)

try:
    import modal  # type: ignore[import-not-found]

    _HAS_MODAL = True
except Exception:  # local CI without modal must still support --help
    modal = None  # type: ignore[assignment]
    _HAS_MODAL = False

SOURCE = Path("/source")
RESULTS = Path("/results")
REPO = Path("/root/qlib")

if _HAS_MODAL:
    original = modal.Volume.from_name(SOURCE_VOLUME, create_if_missing=False)
    research = modal.Volume.from_name(RESEARCH_VOLUME, create_if_missing=False)
    image = (
        modal.Image.debian_slim(python_version="3.11")
        .apt_install("build-essential")
        .env({"PYTHONPATH": "/root/qlib:/root", "MLFLOW_ALLOW_FILE_STORE": "true"})
        .pip_install("numpy==1.26.4", "pandas==2.2.3", "pyarrow", "lightgbm==4.7.0", "cython", "pyyaml", "fire")
        .add_local_dir(
            ".",
            remote_path=str(REPO),
            copy=True,
            ignore=lambda p: (
                str(p).endswith((".cpp", ".so"))
                or any(part in str(p) for part in (".venv", "mlruns", "__pycache__", ".git"))
            ),
        )
    )
    app = modal.App("qlib-csi1000-layered-search-pr-s0", image=image)

    @app.function(
        volumes={str(SOURCE): original.with_mount_options(read_only=True), str(RESULTS): research},
        cpu=2,
        memory=4096,
        timeout=1800,
        retries=0,
    )
    def replay_cell(job: Dict[str, Any]) -> Dict[str, Any]:
        """Replay one frozen-signal cell. Never trains, never writes the source volume."""
        from datetime import datetime, timezone

        started = datetime.now(timezone.utc).isoformat()
        try:
            root = RESULTS / job["prefix"] / job["cell"]["cell_key"]
            if (root / "cell.json").exists():
                cached = json.loads((root / "cell.json").read_text(encoding="utf-8"))
                verdict = check_cache_hit(
                    cached, {"cache_key": job["cache_key"], "source_sha256": job["source_sha256"]}
                )
                if verdict == "RESUME_VERIFIED":
                    return {**cached, "resumed_hash_verified": True, "training_performed": False}
                return {
                    "status": "BLOCKED",
                    "reason": "FAIL_CONFLICT",
                    "cell_key": job["cell"]["cell_key"],
                    "training_performed": False,
                }
            # NOTE: full Qlib replay body lands in PR-S1; PR-S0 records the verified
            # cell skeleton (identity + cache key + ledger) so P0 wiring is reviewable
            # without claiming any real backtest has run.
            out = {
                "status": "PASS_SKELETON",
                "cell_key": job["cell"]["cell_key"],
                "cell": job["cell"],
                "cache_key": job["cache_key"],
                "source_sha256": job["source_sha256"],
                "output_sha256": job["cache_key"],
                "training_performed": False,
                "promotion_authorized": False,
                "started_utc": started,
                "finished_utc": datetime.now(timezone.utc).isoformat(),
            }
            root.mkdir(parents=True, exist_ok=True)
            (root / "cell.json").write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            # Only the independent research volume is ever committed; the source
            # volume object has no commit call anywhere in this file.
            research.commit()
            return out
        except Exception as exc:  # fail closed per cell, never partial-write as PASS
            return {
                "status": "BLOCKED",
                "cell_key": job["cell"]["cell_key"],
                "reason": f"{type(exc).__name__}: {exc}",
                "started_utc": started,
                "training_performed": False,
            }

    @app.function(
        volumes={str(SOURCE): original.with_mount_options(read_only=True), str(RESULTS): research},
        cpu=2,
        memory=4096,
        timeout=900,
    )
    def finalize(prefix: str, stage: str, outcomes: List[Dict[str, Any]], code_sha: str, run_id: str) -> Dict[str, Any]:
        ledger = BudgetLedger()
        summary = {
            "protocol": PROTOCOL_VERSION,
            "stage": stage,
            "run_id": run_id,
            "code_sha": code_sha,
            "expected": len(outcomes),
            "completed": sum(1 for o in outcomes if o.get("status", "").startswith("PASS")),
            "blocked": sum(1 for o in outcomes if o.get("status") == "BLOCKED"),
            "training_performed": False,
            "promotion_authorized": False,
            "source_volume_read_only": True,
            "outcomes": outcomes,
        }
        root = RESULTS / prefix / "summaries" / stage
        root.mkdir(parents=True, exist_ok=True)
        if (root / f"stage_{stage}_summary.json").exists():
            raise RuntimeError("summary already exists; refusing to overwrite")
        (root / f"stage_{stage}_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
        (root / "protocol.json").write_text(json.dumps(build_protocol(run_id), indent=2, sort_keys=True) + "\n")
        research.commit()
        return summary


def build_cli() -> argparse.ArgumentParser:
    """CLI contract (real --help; actual flags govern, not the contract prose)."""
    p = argparse.ArgumentParser(
        description="PR-S0 Modal runner: P0 4-cell controls + L1-A small portfolio screen (zero-fit, read-only source).",
        epilog="OpenCode foreground rule: run attached without --detach; set the tool timeout beyond worst-case wall time.",
    )
    p.add_argument("--stage", default="p0", help="p0 | l1-a | inventory | dry-run (l1-b/l1-c/l2/l3/l4 need later PRs).")
    p.add_argument("--run-id", default="", help="Pre-registered run id [A-Za-z0-9][A-Za-z0-9_-]{3,63}.")
    p.add_argument("--max-workers", type=int, default=2, help="Parallel workers, 1..8 (PR-S0 cap).")
    p.add_argument("--batch-limit", type=int, default=0, help="Max cells this batch; 0 means all cells of the stage.")
    p.add_argument("--estimated-total-usd", type=float, default=-1.0, help="REQUIRED P90 batch estimate in USD.")
    p.add_argument(
        "--resume-from-manifest", default="", help="Optional existing source_manifest.json for hash-checked resume."
    )
    p.add_argument("--dry-run", action="store_true", help="Local validation only; no Modal dispatch.")
    p.add_argument("--inventory", action="store_true", help="Print frozen enumeration and exit without dispatch.")
    return p


def validate_args(args: argparse.Namespace) -> List[Dict[str, Any]]:
    """Fail-closed CLI validation shared by local and Modal paths."""
    if args.inventory or args.stage == "inventory":
        return []
    if args.stage not in ("p0", "l1-a", "dry-run"):
        raise ValueError(f"STOP: stage {args.stage} needs a later reviewed PR (PR-S0 covers p0/l1-a only)")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{3,63}", args.run_id):
        raise ValueError("STOP: --run-id must match [A-Za-z0-9][A-Za-z0-9_-]{3,63}")
    if not 1 <= args.max_workers <= 8:
        raise ValueError("STOP: --max-workers must be 1..8 in PR-S0")
    if args.estimated_total_usd is None or args.estimated_total_usd <= 0:
        raise ValueError("STOP_COST_UNKNOWN: --estimated-total-usd is required and must be > 0")
    layer = "p0" if args.stage == "p0" else "l1"
    if args.estimated_total_usd > LAYER_CAPS_USD[layer]:
        raise ValueError(
            f"STOP_BUDGET: estimate {args.estimated_total_usd} exceeds layer {layer} cap {LAYER_CAPS_USD[layer]}"
        )
    ledger = BudgetLedger()
    gate = ledger.preflight(layer, args.estimated_total_usd)
    if gate != "OK_CONTINUE":
        raise ValueError(f"STOP: preflight gate {gate}")
    if args.stage == "dry-run":
        return []
    cells = enumerate_cells(args.stage)
    if args.batch_limit and not 1 <= args.batch_limit <= len(cells):
        raise ValueError("STOP: --batch-limit out of range")
    return cells[: args.batch_limit] if args.batch_limit else cells


def main(argv: List[str] | None = None) -> int:
    """CLI entry point.

    Parameters
    ----------
    argv : list of str or None
        Argument list; None uses sys.argv.

    Returns
    -------
    int
        Process exit code.
    """
    args = build_cli().parse_args(argv)
    if args.inventory or args.stage == "inventory":
        print(
            json.dumps(
                {
                    "protocol": PROTOCOL_VERSION,
                    "p0": len(enumerate_cells("p0")),
                    "l1-a": len(enumerate_cells("l1-a")),
                    "layer_caps": LAYER_CAPS_USD,
                },
                indent=2,
            )
        )
        return 0
    if args.dry_run or args.stage == "dry-run":
        load_frozen_profiles(".")
        validate_args(
            build_cli().parse_args(
                ["--stage", "p0", "--run-id", args.run_id or "dry-run-0000", "--estimated-total-usd", "1.0"]
            )
        )
        print("DRY_RUN_OK: frozen profiles + P0 gate valid; no Modal dispatch, no spend")
        return 0
    cells = validate_args(args)
    if not _HAS_MODAL:
        raise ValueError("STOP: modal package unavailable; refusing to claim a remote run (use --dry-run/--inventory)")
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    if subprocess.check_output(["git", "status", "--porcelain"], text=True).strip():
        raise ValueError("STOP: worktree must be clean to bind code SHA to the Modal image")
    prefix = f"csi1000_layered_search/{PROTOCOL_VERSION}/{args.run_id}/{args.stage}/{sha[:12]}"
    check_output_not_in_source("/tmp", f"/tmp/{args.run_id}")
    jobs = [
        {
            "cell": c,
            "prefix": prefix,
            "cache_key": replay_cache_key("stage_b_frozen_signal", c["topk"], c["dropn"], "pending_quote_sha"),
            "source_sha256": "pending_source_sha",
        }
        for c in cells
    ]
    print(
        json.dumps(
            {
                "stage": args.stage,
                "cells": len(jobs),
                "max_workers": args.max_workers,
                "estimated_total_usd": args.estimated_total_usd,
                "code_sha": sha,
                "source_volume_read_only": True,
                "output_prefix": prefix,
                "opencode_foreground_modal": True,
                "modal_detach_used": False,
            },
            indent=2,
        )
    )
    print("READY_FOR_FOREGROUND_MODAL_RUN: execute attached `modal run audit/csi1000_layered_search_modal.py ...`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
