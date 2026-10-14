"""L2 offline control plane. No Modal import, remote calls, or model fitting.

All receipts bind their immutable job specification, concrete model config,
source identity, actual output bytes and code SHA. Cross-run reuse fails closed.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
from pathlib import Path
from typing import Any

from audit.csi1000_layered_search_core import require, sha256_file, stable_sha

L2_MAX_FITS = 320
L2_MAX_USD = 12.0
SHORT_KINDS = ("short", "long")
ARTIFACTS = {
    "short": ("prediction.parquet", "checkpoint.txt", "report.parquet", "decisions.json"),
    "long": ("prediction.parquet", "checkpoint.txt"),
    "phase": ("report.parquet", "decisions.json", "signal.parquet"),
}


def prefix_for(run_id: str, code_sha: str) -> str:
    require(bool(re.fullmatch(r"l2_[a-zA-Z0-9_]{4,50}", run_id)), "L2 invalid isolated run ID")
    require(bool(re.fullmatch(r"[a-f0-9]{40}", code_sha)), "L2 needs full immutable code SHA")
    return f"csi1000_layered_search/csi1000_layered_search_v1/{run_id}/l2/{code_sha[:12]}"


def job_id(job: dict[str, Any]) -> str:
    require(job.get("kind") in ("short", "long", "phase"), "L2 unknown job type")
    require(bool(re.fullmatch(r"[a-f0-9]{64}", job.get("candidate_id", ""))), "L2 invalid candidate ID")
    if job["kind"] == "short":
        require(job.get("fold_id") in ("fold1", "fold2", "fold3", "fold4"), "L2 invalid fold")
        suffix = job["fold_id"]
    else:
        require(job.get("phase") in (0, 10), "L2 invalid long phase")
        suffix = f"phase{job['phase']}"
        if job["kind"] == "long":
            require(bool(re.fullmatch(r"\d{4}-\d\d-\d\d", job.get("retrain_asof", ""))), "L2 invalid as-of")
            suffix += f"/{job['retrain_asof']}"
    return f"{job['kind']}/{job['candidate_id']}/{suffix}"


def receipt_path(root: Path, job: dict[str, Any]) -> Path:
    return root / "jobs" / job_id(job)


def checked_receipt(root: Path, job: dict[str, Any], identity: dict[str, str]) -> dict | None:
    """Return verified immutable receipt; incomplete or mismatched files block.

    Never interpret existence or a timestamp as a cache hit.
    """
    directory = receipt_path(root, job)
    if not directory.exists():
        return None
    path = directory / "receipt.json"
    require(path.is_file(), "L2 incomplete published receipt")
    record = json.loads(path.read_text(encoding="utf-8"))
    for key, expected in identity.items():
        require(record.get(key) == expected, f"L2 receipt identity drift: {key}")
    require(record.get("job_sha256") == stable_sha(job), "L2 receipt job spec mismatch")
    require(record.get("status") == "PASS" and record.get("kind") == job["kind"],
            "L2 receipt is not a PASS of the requested kind")
    names = ARTIFACTS[job["kind"]]
    hashes = record.get("artifact_sha256")
    require(isinstance(hashes, dict) and set(hashes) == set(names), "L2 receipt artifact manifest incomplete")
    for name in names:
        target = directory / name
        require(target.is_file() and sha256_file(target) == hashes[name], f"L2 artifact SHA drift: {name}")
    return record


def publish_receipt(
    root: Path, job: dict[str, Any], identity: dict[str, str], artifacts: dict[str, Any], details: dict
) -> dict:
    """Write into a unique temp directory; existing artifacts are never overwritten."""
    dest = receipt_path(root, job)
    require(set(artifacts) == set(ARTIFACTS[job["kind"]]), "L2 wrong artifact list")
    protected = set(identity) | {"status", "kind", "job_sha256", "artifact_sha256"}
    require(not protected.intersection(details), "L2 receipt cannot override signed identity fields")
    if dest.exists():
        existing = checked_receipt(root, job, identity)
        require(existing is not None, "L2 existing incomplete receipt")
        return existing
    tmp = dest.parent / (dest.name + ".tmp")
    require(not tmp.exists(), "L2 stale temp artifact; manual evidence check required")
    tmp.mkdir(parents=True, exist_ok=False)
    try:
        for name, obj in artifacts.items():
            path = tmp / name
            if name.endswith(".parquet"):
                obj.to_parquet(path)
            elif isinstance(obj, bytes):
                path.write_bytes(obj)
            elif isinstance(obj, str):
                path.write_text(obj, encoding="utf-8")
            else:
                path.write_text(json.dumps(obj, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
        hashes = {name: sha256_file(tmp / name) for name in sorted(artifacts)}
        record = {
            "status": "PASS",
            **identity,
            "kind": job["kind"],
            "job_sha256": stable_sha(job),
            "artifact_sha256": hashes,
            **details,
        }
        (tmp / "receipt.json").write_text(
            json.dumps(record, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        os.replace(tmp, dest)
        return record
    except BaseException:
        # Keep failed outputs only if they could be needed for manual diagnosis;
        # never mistake them for PASS or overwrite any completed receipt.
        if tmp.exists():
            shutil.rmtree(tmp)
        raise


def choose_new_for_long(plan: dict, short_receipts: list[dict], stage_a: dict) -> dict:
    """Pre-registered four-fold rule; do not choose by the Stage-B consumed tail.

    Compare four complete genuine short-fold replays to historical Stage-A
    baseline summary under the SAME original Stage-A execution contract.
    """
    wanted = {r["candidate_id"] for r in plan["new_candidates"]}
    by_id: dict[str, dict[str, dict]] = {}
    for item in short_receipts:
        cid, fold = item["candidate_id"], item["fold_id"]
        require(cid in wanted and fold in ("fold1", "fold2", "fold3", "fold4"), "L2 rogue short result")
        require(item["status"] == "PASS" and item["kind"] == "short", "L2 short job failed")
        require(fold not in by_id.setdefault(cid, {}), "L2 duplicate short fit")
        by_id[cid][fold] = item
    require(set(by_id) == wanted and all(len(v) == 4 for v in by_id.values()), "L2 short 16/16 not verified")
    from csi1000_tuner_core import summarize_candidate

    baseline = [r for r in stage_a["ranked_candidates"] if r["candidate"]["is_baseline"]]
    require(len(baseline) == 1, "L2 Stage-A baseline must be unique")
    b = baseline[0]["fold_summary"]
    qualified = []
    scores = []
    for item in plan["new_candidates"]:
        cid = item["candidate_id"]
        spec = {"candidate_id": cid, "model_params": item["model_params"], "is_baseline": False}
        folds = []
        for fold in ("fold1", "fold2", "fold3", "fold4"):
            rec = by_id[cid][fold]
            folds.append({"fold_id": fold, **rec["metrics"]})
        summary = summarize_candidate(spec, folds)["fold_summary"]
        worst = float(summary["relative_excess_cagr_worst"]) - float(b["relative_excess_cagr_worst"])
        median = float(summary["relative_excess_cagr_median"]) - float(b["relative_excess_cagr_median"])
        mdd = abs(float(summary["strategy_max_drawdown_worst"])) - abs(float(b["strategy_max_drawdown_worst"]))
        passed = worst >= 0.005 and median >= 0.005 and mdd <= 0.05
        rank = {
            "candidate_id": cid,
            "worst_delta": worst,
            "median_delta": median,
            "mdd_degradation": mdd,
            "passed": bool(passed),
        }
        scores.append(rank)
        if passed:
            qualified.append(rank)
    qualified.sort(key=lambda x: (-x["worst_delta"], -x["median_delta"], x["mdd_degradation"], x["candidate_id"]))
    selection = [r["candidate_id"] for r in qualified[:2]]
    return {
        "status": "SHORT_SCREEN_PASS",
        "short_count": 16,
        "eligible": scores,
        "selected_new_candidate_ids": selection,
        "selection_sha256": stable_sha(selection),
    }


def budget_gate(
    fits_attempted: int,
    wave_new_fits: int,
    spend_estimate: float,
    reserve_per_fit: float,
    spent_usd: float,
    cap: float = L2_MAX_USD,
) -> None:
    """Conservative reservation check; NEVER calls projected spend 'actual billing'."""
    require(isinstance(fits_attempted, int) and fits_attempted >= 0, "L2 invalid attempted fit count")
    require(isinstance(wave_new_fits, int) and wave_new_fits >= 0, "L2 invalid fit wave size")
    require(fits_attempted + wave_new_fits <= L2_MAX_FITS, "STOP_L2_FIT_CAP")
    nums = (spend_estimate, reserve_per_fit, spent_usd, cap)
    require(all(isinstance(v, (int, float)) and math.isfinite(v) and v >= 0 for v in nums), "STOP_L2_COST_UNKNOWN")
    require(reserve_per_fit > 0 and cap <= L2_MAX_USD, "STOP_L2_COST_UNKNOWN")
    require(
        spent_usd + spend_estimate + wave_new_fits * reserve_per_fit <= cap,
        "STOP_L2_BUDGET: estimate incl unsettled reserve exceeds cap",
    )


def summarise_long(chall_rows: list[dict], baseline_rows: list[dict], reporter) -> dict:
    """Use audited L1 two-phase arithmetic; reject 1-phase cherry picks."""
    from audit.csi1000_layered_search_core import coarse_pass
    from audit.csi1000_layered_search_replay import paired_rows

    chal, base, ratio = paired_rows(chall_rows, baseline_rows, reporter)
    passed, detail = coarse_pass(chal, base)
    return {"passed_coarse": passed, **detail, "turnover_ratio": ratio}
