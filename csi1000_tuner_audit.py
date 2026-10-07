"""Read-only audit tooling for CSI1000 Stage-A smoke/expanded screens.

This module intentionally does NOT modify csi1000_tuner.py, csi1000_tuner_core.py,
or deterministic_strategy.py. Those files participate in the Stage-A runtime
snapshot token. Keeping them unchanged preserves the validated v3 snapshot and
allows the 11 completed non-baseline smoke candidates (44 candidate-fold
artifacts) to be reused by the 80-candidate expansion.

Two modes are supported:

1. plan
   Inventory candidate-fold artifacts that already exist before expansion and
   record mtimes so later reuse can be demonstrated without changing the tuner.

2. export
   Export compact per-fold metrics for audit/leave-one-fold-out analysis and,
   when given the plan file, verify that planned reusable artifacts were not
   rewritten during the expanded run.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from statistics import median
from typing import Any

import modal


APP_NAME = "qlib-csi1000-stage-a-audit"
VOL_NAME = "qlib-cn-data"
VOL_ROOT = Path("/vol")
ARTIFACT_ROOT = VOL_ROOT / "csi1000_tuner" / "stage_a"

# Keep this tool read-only and lightweight. It only reads JSON/parquet bytes;
# it does not import Qlib or execute models/backtests.
vol = modal.Volume.from_name(VOL_NAME, create_if_missing=False)
image = modal.Image.debian_slim(python_version="3.11")
app = modal.App(APP_NAME, image=image)

COMPACT_METRIC_FIELDS = (
    "strategy_total_return",
    "strategy_cagr",
    "benchmark_total_return",
    "benchmark_cagr",
    "relative_total_return",
    "relative_excess_cagr",
    "strategy_max_drawdown",
    "benchmark_max_drawdown",
    "relative_max_drawdown",
    "sharpe",
    "information_ratio",
    "annual_volatility",
    "mean_daily_net_return",
    "mean_daily_active_return",
    "account_return_max_error",
    "mean_turnover",
    "total_cost_sum",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_artifact_path(path_text: str, expected_dir: Path) -> Path:
    path = Path(path_text)
    try:
        path.relative_to(expected_dir)
    except ValueError as exc:
        raise ValueError(
            f"artifact path escapes expected candidate-fold directory: {path}"
        ) from exc
    return path


def _validate_artifact(meta: dict, expected_dir: Path) -> dict:
    if not isinstance(meta, dict):
        return {"passed": False, "reason": "missing_metadata"}
    if not meta.get("path") or not meta.get("sha256"):
        return {"passed": False, "reason": "incomplete_metadata"}
    try:
        path = _safe_artifact_path(meta["path"], expected_dir)
    except ValueError as exc:
        return {"passed": False, "reason": str(exc)}
    if not path.is_file():
        return {"passed": False, "reason": f"missing_file:{path.name}"}
    actual_sha = _sha256_file(path)
    if actual_sha != meta["sha256"]:
        return {
            "passed": False,
            "reason": f"byte_sha256_mismatch:{path.name}",
            "expected_sha256": meta["sha256"],
            "actual_sha256": actual_sha,
        }
    return {
        "passed": True,
        "path": str(path),
        "sha256": actual_sha,
        "content_sha256": meta.get("content_sha256"),
        "mtime_ns": path.stat().st_mtime_ns,
    }


def _finite_metric(payload: dict, key: str) -> float:
    value = payload.get(key)
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} is missing or non-numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{key} is not finite")
    return number


def _scan_result(
    *,
    root: Path,
    protocol: str,
    snapshot_token: str,
    candidate: dict,
    fold: dict,
) -> dict:
    candidate_id = candidate["candidate_id"]
    fold_id = fold["fold_id"]
    expected_dir = root / candidate_id / fold_id
    result_path = expected_dir / "result.json"
    base = {
        "candidate_id": candidate_id,
        "is_baseline": bool(candidate.get("is_baseline")),
        "fold_id": fold_id,
        "result_path": str(result_path),
        "exists": result_path.is_file(),
        "precheck_reusable": False,
    }
    if not result_path.is_file():
        return {**base, "reason": "missing_result"}

    try:
        raw_bytes = result_path.read_bytes()
        payload = json.loads(raw_bytes)
    except Exception as exc:
        return {**base, "reason": f"invalid_result_json:{type(exc).__name__}:{exc}"}

    checks = {
        "protocol": payload.get("protocol") == protocol,
        "snapshot_token": payload.get("snapshot_token") == snapshot_token,
        "candidate_id": payload.get("candidate_id") == candidate_id,
        "candidate": payload.get("candidate") == candidate,
        "fold_id": payload.get("fold_id") == fold_id,
        "fold": payload.get("fold") == fold,
        "canonical_namespace": payload.get("artifact_namespace") is None,
    }
    failed_identity = [name for name, passed in checks.items() if not passed]
    if failed_identity:
        return {
            **base,
            "reason": "identity_mismatch:" + ",".join(failed_identity),
            "identity_checks": checks,
            "result_json_mtime_ns": result_path.stat().st_mtime_ns,
            "result_json_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        }

    try:
        metrics = {key: _finite_metric(payload, key) for key in COMPACT_METRIC_FIELDS}
    except ValueError as exc:
        return {
            **base,
            "reason": f"metric_validation:{exc}",
            "result_json_mtime_ns": result_path.stat().st_mtime_ns,
            "result_json_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        }

    artifact_checks = {}
    for key in ("signal_artifact", "decision_artifact", "report_artifact"):
        artifact_checks[key] = _validate_artifact(payload.get(key), expected_dir)
    failed_artifacts = [
        name for name, detail in artifact_checks.items() if not detail["passed"]
    ]
    if failed_artifacts:
        return {
            **base,
            "reason": "artifact_precheck:" + ",".join(failed_artifacts),
            "artifact_checks": artifact_checks,
            "result_json_mtime_ns": result_path.stat().st_mtime_ns,
            "result_json_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        }

    return {
        **base,
        "precheck_reusable": True,
        "reason": None,
        "result_json_mtime_ns": result_path.stat().st_mtime_ns,
        "result_json_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "best_iteration": payload.get("best_iteration"),
        "stored_source": payload.get("source"),
        "metrics": metrics,
        "signal_content_sha256": artifact_checks["signal_artifact"].get("content_sha256"),
        "decision_content_sha256": artifact_checks["decision_artifact"].get("content_sha256"),
        "report_content_sha256": artifact_checks["report_artifact"].get("content_sha256"),
    }


@app.function(volumes={str(VOL_ROOT): vol}, cpu=1, memory=1024, timeout=30 * 60)
def scan_snapshot(
    snapshot_token: str,
    protocol: str,
    candidates: list[dict],
    planned_entries: dict[str, dict] | None = None,
):
    """Read canonical Stage-A result artifacts without mutating the Volume."""
    root = ARTIFACT_ROOT / snapshot_token
    summary_path = root / "summary.json"
    if not summary_path.is_file():
        raise RuntimeError(f"Stage-A summary not found: {summary_path}")
    summary = json.loads(summary_path.read_text())

    if summary.get("protocol") != protocol:
        raise RuntimeError(
            f"protocol mismatch: expected={protocol} actual={summary.get('protocol')}"
        )
    if (summary.get("manifest") or {}).get("snapshot_token") != snapshot_token:
        raise RuntimeError("summary snapshot token does not match requested snapshot")

    folds = summary.get("folds") or []
    if len(folds) != 4:
        raise RuntimeError(f"expected 4 Stage-A folds, found {len(folds)}")

    rows = []
    for candidate in candidates:
        for fold in folds:
            row = _scan_result(
                root=root,
                protocol=protocol,
                snapshot_token=snapshot_token,
                candidate=candidate,
                fold=fold,
            )
            key = f"{candidate['candidate_id']}/{fold['fold_id']}"
            planned = (planned_entries or {}).get(key)
            if planned and planned.get("precheck_reusable"):
                row["planned_reusable"] = True
                row["planned_result_json_mtime_ns"] = planned.get(
                    "result_json_mtime_ns"
                )
                row["planned_reuse_unchanged"] = bool(
                    row.get("precheck_reusable")
                    and row.get("result_json_mtime_ns")
                    == planned.get("result_json_mtime_ns")
                )
            else:
                row["planned_reusable"] = False
                row["planned_reuse_unchanged"] = None
            rows.append(row)

    return {
        "snapshot_token": snapshot_token,
        "protocol": protocol,
        "source_summary_kind": summary.get("screen_kind"),
        "folds": folds,
        "rows": rows,
    }


def _linear_quantile(values: list[float], q: float) -> float:
    xs = sorted(float(value) for value in values)
    if not xs:
        raise ValueError("empty quantile input")
    if len(xs) == 1:
        return xs[0]
    position = (len(xs) - 1) * q
    lo = math.floor(position)
    hi = math.ceil(position)
    if lo == hi:
        return xs[lo]
    weight = position - lo
    return xs[lo] * (1.0 - weight) + xs[hi] * weight


def _metric_summary(rows: list[dict]) -> dict:
    relative = [row["metrics"]["relative_excess_cagr"] for row in rows]
    return {
        "n": len(rows),
        "ids": [row["fold_id"] for row in rows],
        "relative_excess_cagr_q25": _linear_quantile(relative, 0.25),
        "relative_excess_cagr_median": float(median(relative)),
        "relative_excess_cagr_worst": min(relative),
        "positive_ratio": sum(value > 0 for value in relative) / len(relative),
        "information_ratio_median": float(
            median(row["metrics"]["information_ratio"] for row in rows)
        ),
        "sharpe_median": float(median(row["metrics"]["sharpe"] for row in rows)),
        "strategy_max_drawdown_worst": min(
            row["metrics"]["strategy_max_drawdown"] for row in rows
        ),
        "mean_turnover_median": float(
            median(row["metrics"]["mean_turnover"] for row in rows)
        ),
        "total_cost_sum_median": float(
            median(row["metrics"]["total_cost_sum"] for row in rows)
        ),
    }


def _rank_from_rows(
    candidates: list[dict],
    rows: list[dict],
    *,
    rank_specs: list[tuple[str, int]],
    omit_fold: str | None = None,
) -> list[dict]:
    by_candidate: dict[str, list[dict]] = {c["candidate_id"]: [] for c in candidates}
    for row in rows:
        if not row.get("precheck_reusable"):
            continue
        if omit_fold is not None and row["fold_id"] == omit_fold:
            continue
        by_candidate[row["candidate_id"]].append(row)

    expected_n = 3 if omit_fold else 4
    ranked = []
    for candidate in candidates:
        candidate_rows = by_candidate[candidate["candidate_id"]]
        if len(candidate_rows) != expected_n:
            return []
        summary = _metric_summary(candidate_rows)
        vector = tuple(
            float(summary[field]) * int(direction)
            for field, direction in rank_specs
        )
        ranked.append(
            {
                "candidate_id": candidate["candidate_id"],
                "is_baseline": bool(candidate.get("is_baseline")),
                "summary": summary,
                "ranking_vector": list(vector),
            }
        )

    ranked.sort(
        key=lambda item: (
            tuple(-value for value in item["ranking_vector"]),
            item["candidate_id"],
        )
    )
    for rank, item in enumerate(ranked, start=1):
        item["rank"] = rank
    return ranked


def _compact_candidate_results(candidates: list[dict], rows: list[dict]) -> list[dict]:
    by_candidate: dict[str, list[dict]] = {c["candidate_id"]: [] for c in candidates}
    for row in rows:
        if row.get("precheck_reusable"):
            by_candidate[row["candidate_id"]].append(row)

    out = []
    for candidate in candidates:
        fold_rows = sorted(
            by_candidate[candidate["candidate_id"]],
            key=lambda row: row["fold_id"],
        )
        out.append(
            {
                "candidate_id": candidate["candidate_id"],
                "is_baseline": bool(candidate.get("is_baseline")),
                "model_params": candidate["model_params"],
                "folds": [
                    {
                        "fold_id": row["fold_id"],
                        "best_iteration": row.get("best_iteration"),
                        "metrics": row["metrics"],
                        "signal_content_sha256": row.get("signal_content_sha256"),
                        "decision_content_sha256": row.get("decision_content_sha256"),
                        "report_content_sha256": row.get("report_content_sha256"),
                    }
                    for row in fold_rows
                ],
            }
        )
    return out


def _plan_entry_map(payload: dict) -> dict[str, dict]:
    return {
        f"{row['candidate_id']}/{row['fold_id']}": row
        for row in payload.get("artifact_inventory", [])
    }


@app.local_entrypoint()
def main(
    mode: str = "plan",
    snapshot_token: str = "",
    candidate_count: int = 80,
    plan_path: str = "",
    plan_label: str = "",
):
    """Plan reuse before expansion or export compact fold audit after expansion."""
    from csi1000_tuner_core import (
        PROTOCOL_VERSION,
        SCREEN_RANK_SPECS,
        expanded_candidate_specs,
    )

    mode = str(mode).strip().lower()
    if mode not in {"plan", "export"}:
        raise ValueError("mode must be 'plan' or 'export'")
    if mode == "plan":
        if not plan_label:
            raise ValueError(
                "plan_label is required in plan mode so reuse plans are immutable"
            )
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", plan_label):
            raise ValueError(
                "plan_label must be 1-64 characters: letters, digits, dot, underscore, hyphen"
            )
    if not snapshot_token:
        raise ValueError("snapshot_token is required")
    if int(candidate_count) != 80:
        raise ValueError("current audit contract expects the registered 80-candidate expansion")

    candidates = expanded_candidate_specs(count=int(candidate_count))
    planned_entries = {}
    plan_payload = None
    if plan_path:
        plan_payload = json.loads(Path(plan_path).read_text())
        if plan_payload.get("snapshot_token") != snapshot_token:
            raise ValueError("plan snapshot token does not match requested snapshot")
        if plan_payload.get("mode") != "plan":
            raise ValueError("plan_path must reference a plan-mode audit file")
        if int((plan_payload.get("accounting") or {}).get("candidate_count", -1)) != int(
            candidate_count
        ):
            raise ValueError("plan candidate_count does not match requested expansion")
        planned_entries = _plan_entry_map(plan_payload)

    scan = scan_snapshot.remote(
        snapshot_token,
        PROTOCOL_VERSION,
        candidates,
        planned_entries,
    )
    source_kind = scan["source_summary_kind"]
    if mode == "plan" and source_kind != "smoke":
        raise RuntimeError(
            f"reuse plan must be created from the accepted smoke summary, got {source_kind!r}"
        )
    if mode == "export" and source_kind != "expanded":
        raise RuntimeError(
            f"expanded audit requires a completed expanded summary, got {source_kind!r}"
        )

    rows = scan["rows"]
    nonbaseline_rows = [row for row in rows if not row["is_baseline"]]
    prechecked_reusable = sum(
        bool(row.get("precheck_reusable")) for row in nonbaseline_rows
    )
    candidate_fold_jobs = (len(candidates) - 1) * len(scan["folds"])
    gate_new_fits = 2 * len(scan["folds"])

    accounting = {
        "candidate_count": len(candidates),
        "fold_count": len(scan["folds"]),
        "candidate_fold_jobs_excluding_baseline": candidate_fold_jobs,
        "baseline_gate_new_fits": gate_new_fits,
        "prechecked_reusable_nonbaseline_candidate_folds": prechecked_reusable,
        "expected_new_candidate_fits_if_resume_accepts_precheck": (
            candidate_fold_jobs - prechecked_reusable
        ),
        "expected_total_new_fits_if_resume_accepts_precheck": (
            gate_new_fits + candidate_fold_jobs - prechecked_reusable
        ),
        "worst_case_new_fits_without_any_reuse": gate_new_fits + candidate_fold_jobs,
    }

    payload = {
        "audit_version": "csi1000_stage_a_expanded_audit_v1",
        "mode": mode,
        "snapshot_token": snapshot_token,
        "protocol": PROTOCOL_VERSION,
        "source_summary_kind": scan["source_summary_kind"],
        "accounting": accounting,
        "artifact_inventory": rows,
    }

    if mode == "export":
        valid_rows = [row for row in rows if row.get("precheck_reusable")]
        payload["all_candidate_fold_results_present"] = (
            len(valid_rows) == len(candidates) * len(scan["folds"])
        )
        payload["candidate_fold_results"] = _compact_candidate_results(
            candidates,
            rows,
        )

        full_rank = _rank_from_rows(
            candidates,
            rows,
            rank_specs=list(SCREEN_RANK_SPECS),
        )
        payload["recomputed_full_rank"] = full_rank
        payload["leave_one_fold_out_rankings"] = {
            fold["fold_id"]: _rank_from_rows(
                candidates,
                rows,
                rank_specs=list(SCREEN_RANK_SPECS),
                omit_fold=fold["fold_id"],
            )
            for fold in scan["folds"]
        }

        if plan_payload is not None:
            planned_rows = [
                row
                for row in rows
                if row.get("planned_reusable")
                and not row.get("is_baseline")
            ]
            unchanged = sum(
                bool(row.get("planned_reuse_unchanged")) for row in planned_rows
            )
            payload["reuse_verification"] = {
                "planned_reusable_nonbaseline_candidate_folds": len(planned_rows),
                "unchanged_result_artifacts": unchanged,
                "rewritten_or_missing_result_artifacts": len(planned_rows) - unchanged,
                "all_planned_reuse_unchanged": unchanged == len(planned_rows),
                "method": "result.json mtime unchanged from pre-expansion plan",
            }

    out_dir = Path("results") / "csi1000_tuner"
    out_dir.mkdir(parents=True, exist_ok=True)
    if mode == "plan":
        payload["plan_label"] = plan_label
        stem = "stage_a_expanded_reuse_plan"
        out_path = out_dir / (
            f"{stem}_{snapshot_token[:16]}_{plan_label}.json"
        )
        serialized = json.dumps(payload, indent=2, ensure_ascii=False)
        if out_path.exists():
            if out_path.read_text() != serialized:
                raise RuntimeError(
                    f"immutable reuse plan already exists with different content: {out_path}"
                )
            print(f"[stage-a-audit] immutable plan already exists unchanged: {out_path}")
        else:
            out_path.write_text(serialized)
    else:
        stem = "stage_a_expanded_audit"
        out_path = out_dir / f"{stem}_{snapshot_token[:16]}.json"
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"[stage-a-audit] exported {out_path}")
    print(json.dumps(accounting, indent=2))
