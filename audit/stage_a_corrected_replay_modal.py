"""Isolated Modal entrypoint for Stage-A immutable-source, zero-fit quote replay.

ONLY mount qlib-cn-data read-only; writes go to precreated research Volume.
Execute controls first; all requires an explicit <=$20 operator cost estimate.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import modal

from audit.replay_stage_a_corrected import (
    SNAPSHOT, SUMMARY, VERSION, aggregate, checked_resume, read_json, load_summary,
    roster, run_cell, save_json, sha256,
)

SOURCE = Path("/source")
RESULTS = Path("/results")
REPO = Path("/root/qlib")
original = modal.Volume.from_name("qlib-cn-data", create_if_missing=False)
research = modal.Volume.from_name("qlib-csi1000-research-audit", create_if_missing=False)
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("build-essential")
    .pip_install("numpy==1.26.4", "pandas==2.2.3", "pyarrow", "lightgbm==4.7.0",
                 "cython", "pyyaml", "ruamel.yaml", "fire", "mlflow", "dill",
                 "filelock", "tqdm", "loguru", "joblib", "pydantic-settings",
                 "redis", "python-redis-lock", "pymongo", "gym", "cvxpy",
                 "matplotlib", "setuptools-scm", "torch")
    .add_local_dir(".", remote_path=str(REPO), copy=True, ignore=lambda p: (
        str(p).endswith((".cpp", ".so")) or
        any(part in str(p) for part in (".venv", "mlruns", "__pycache__", ".git"))
    ))
    .run_commands("cd /root/qlib && pip install . --no-build-isolation --no-deps",
                  "cp /root/qlib/deterministic_strategy.py /root/qlib/board_execution.py /root/qlib/board_rules.py /root/")
)
app = modal.App("qlib-csi1000-stage-a-quote-replay", image=image)


def result_subdir(code_sha, provider_fingerprint):
    if not (len(code_sha) == 40 and all(c in "0123456789abcdef" for c in code_sha)):
        raise ValueError("expected full git SHA")
    return Path("stage_a_corrected") / SNAPSHOT / VERSION / (code_sha + "_" + provider_fingerprint)


@app.function(volumes={str(SOURCE): original.with_mount_options(read_only=True), str(RESULTS): research},
              cpu=2, memory=4096, timeout=3600, retries=0)
def replay_cell(job: dict) -> dict:
    import qlib
    from datetime import datetime, timezone
    summary = load_summary(REPO / SUMMARY)
    root = RESULTS / job["prefix"] / job["candidate"]["candidate_id"] / job["fold"]["fold_id"]
    started = datetime.now(timezone.utc).isoformat()
    try:
        if (root / "comparison.json").exists():
            outcome = checked_resume(SOURCE, SOURCE / "csi1000_tuner/stage_a/provider_snapshot.json",
                                     summary, job["candidate"], job["fold"], root)
            outcome = {**outcome, "resumed_hash_verified": True}
        else:
            qlib.init(provider_uri=str(SOURCE / "cn_data"), region="cn")
            outcome = run_cell(SOURCE, SOURCE / "cn_data",
                               SOURCE / "csi1000_tuner/stage_a/provider_snapshot.json", summary,
                               job["candidate"], job["fold"], root)
            research.commit()
    except Exception as exc:
        outcome = {"status": "BLOCKED", "candidate_id": job["candidate"]["candidate_id"],
                   "fold_id": job["fold"]["fold_id"], "reason": f"{type(exc).__name__}: {exc}",
                   "training_performed": False}
    outcome["started_utc"] = started
    outcome["finished_utc"] = datetime.now(timezone.utc).isoformat()
    print(json.dumps({k: outcome.get(k) for k in ("candidate_id", "fold_id", "status", "reason", "started_utc", "finished_utc")}), flush=True)
    return outcome


@app.function(volumes={str(SOURCE): original.with_mount_options(read_only=True), str(RESULTS): research},
              cpu=2, memory=4096, timeout=1800)
def finalize(prefix: str, scope: str, outcomes: list[dict], code_sha: str):
    from audit.replay_stage_a_corrected import check_provider, checked_sources, require
    summary = load_summary(REPO / SUMMARY)
    snapshot = SOURCE / "csi1000_tuner/stage_a/provider_snapshot.json"
    check_provider(snapshot, summary["manifest"])
    # Full input SHA revalidation before accepting remote worker claims.
    for row in outcomes:
        if row["status"] == "PASS":
            c = next(c for c in summary["ranked_candidates"] if c["candidate_id"] == row["candidate_id"])
            f = next(f for f in summary["folds"] if f["fold_id"] == row["fold_id"])
            checked_resume(SOURCE, snapshot, summary, c, f,
                           RESULTS / prefix / c["candidate_id"] / f["fold_id"])
    batch, ranking = aggregate(summary, outcomes, scope)
    root = RESULTS / prefix / "summaries" / scope
    root.mkdir(parents=True, exist_ok=True)
    # Forbid replacing previously signed-off summaries; records are idempotent per cell.
    for name in ("stage_a_320_corrected_batch.json", "stage_a_80_corrected_ranking.json", "source_manifest.json"):
        require(not (root / name).exists(), "batch summary already exists: " + name)
    save_json(root / "stage_a_320_corrected_batch.json", batch)
    save_json(root / "stage_a_80_corrected_ranking.json", ranking)
    save_json(root / "source_manifest.json", {
        "code_sha": code_sha, "snapshot": SNAPSHOT, "provider_snapshot_sha256": sha256(snapshot),
        "provider_fingerprint": summary["manifest"]["provider_fingerprint"],
        "cells": [{"candidate_id": r["candidate_id"], "fold_id": r["fold_id"], "status": r["status"],
                   "source_sha256": r.get("source_sha256"), "reason": r.get("reason")}
                  for r in outcomes], "training_performed": False, "promotion_authorized": False,
    })
    research.commit()
    return {"scope": scope, "complete": batch["completed"], "blocked": batch["blocked"],
            "ranking_status": ranking["status"], "result_prefix": str(prefix)}


@app.local_entrypoint()
def main(scope: str = "controls", max_workers: int = 2, estimated_total_usd: float = -1):
    if scope not in ("controls", "all") or not 1 <= max_workers <= 24:
        raise ValueError("scope controls/all, max_workers 1..24")
    if scope == "controls" and max_workers > 2:
        raise ValueError("controls must start at <=2 workers")
    if scope == "all" and not (0 <= estimated_total_usd <= 20):
        raise ValueError("full replay requires operator-estimated compute <=$20 based on smoke; request approval otherwise")
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    status = subprocess.check_output(["git", "status", "--porcelain"], text=True)
    if status:
        raise ValueError("worktree must be clean to bind code SHA to Modal image")
    summary = load_summary(Path(SUMMARY))
    prefix = result_subdir(sha, summary["manifest"]["provider_fingerprint"])
    tasks = [{"candidate": c, "fold": f, "prefix": str(prefix)} for c, f in roster(summary, scope)]
    print(json.dumps({"scope": scope, "cells": len(tasks), "max_workers": max_workers,
                      "estimated_total_usd": estimated_total_usd, "code_sha": sha,
                      "source_volume_read_only": True, "output_prefix": str(prefix)}))
    outcomes = list(replay_cell.with_options(max_containers=max_workers).map(tasks))
    print(json.dumps(finalize.remote(str(prefix), scope, outcomes, sha), indent=2))
