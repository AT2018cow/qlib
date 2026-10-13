"""Modal P0/L1 runner for the CSI1000 budgeted layered search (PR-S0/PR-S1).

Real frozen-signal replay via :mod:`audit.csi1000_layered_search_replay`:
read-only source volume, separate research volume, per-cell SHA resume with
atomic publish and fail-closed source/evidence gates. L1-B/L1-C use a smoke-first
execution policy without mandatory cost quotes or per-batch ledger booking.
Zero training for p0/l1-a/l1-b/l1-c: frozen Stage-B signals are replayed under
different (TopK, DropN) combos only. L1-A requires a verified P0 summary;
L1-B reuses 32 verified L1-A cells; L1-C reuses all 144 verified L1-B
cells only after their two-phase screen independently passes. Both require
reviewed cross-code replay equivalence and byte-level SHA verification.

Foreground rule: when launched through OpenCode, NEVER pass ``--detach`` to
``modal run``. Keep the launcher attached with a tool timeout longer than the
worst-case queue + build + run + collect time. If the timeout cannot cover the
batch, shrink ``--batch-limit`` first or STOP. ``git checkout --detach`` is
unrelated and remains allowed. No ``nohup``, shell ``&`` or untracked
background mechanism is used anywhere in this file.

Status: code only, NOT EXECUTED. L1-B runtime is smoke-first; this code
does not itself launch any work merely by being merged or reviewed.
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
    PROTOCOL_VERSION,
    RESEARCH_VOLUME,
    SOURCE_VOLUME,
    BudgetLedger,
    build_protocol,
    check_cell_record,
    enumerate_cells,
    frozen_source_digest,
    load_frozen_profiles,
    manifest_source_index,
    plan_l1b_reuse,
    plan_l1c_reuse,
    replay_cache_key,
    require,
    require_p0_pass,
    require_signed_l1a_export,
    require_signed_l1b_export,
    select_pending_batch,
    verify_export_cells,
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

LEDGER_NAME = "cost_ledger.json"
TRIAL_LEDGER_NAME = "trial_events_pending.jsonl"


def check_operator_cost_quote(path: str, estimated_usd: float, covered_cells: int) -> Dict[str, Any]:
    """Validate a current billing-rate quote and conservative P90 batch ceiling.

    Billing data must be collected by the authorized operator, not guessed from
    historical illustrative USD rates or Starter credits.
    """
    import math
    from datetime import datetime, timezone, timedelta

    if not path or not Path(path).is_file():
        raise ValueError("STOP_COST_UNKNOWN: operator billing-rate quote JSON missing")
    quote = json.loads(Path(path).read_text(encoding="utf-8"))
    if quote.get("schema") != "modal_cost_p90_quote_v1":
        raise ValueError("STOP_COST_UNKNOWN: unsupported billing quote schema")
    try:
        captured = datetime.fromisoformat(quote["captured_utc"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("STOP_COST_UNKNOWN: cost quote timestamp missing") from exc
    now = datetime.now(timezone.utc)
    if captured.tzinfo is None or captured > now + timedelta(minutes=5) or now - captured > timedelta(hours=24):
        raise ValueError("STOP_COST_UNKNOWN: billing-rate snapshot is stale or in the future")
    keys = (
        "cpu_rate_per_second",
        "memory_rate_per_gib_second",
        "p90_cpu_seconds_per_cell",
        "p90_gib_seconds_per_cell",
        "overhead_usd",
    )
    for key in keys:
        value = quote.get(key)
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError(f"STOP_COST_UNKNOWN: missing/nonfinite billing field {key}")
    if not quote["cpu_rate_per_second"] or not quote["memory_rate_per_gib_second"]:
        raise ValueError("STOP_COST_UNKNOWN: current Modal billing rates must be positive")
    if not isinstance(quote.get("covered_cells"), int) or quote["covered_cells"] < covered_cells:
        raise ValueError("STOP_COST_UNKNOWN: quote covers fewer cells than dispatched batch")
    if not isinstance(quote.get("rate_report_sha256"), str) or not re.fullmatch(
        r"[0-9a-f]{64}", quote["rate_report_sha256"]
    ):
        raise ValueError("STOP_COST_UNKNOWN: signed rate evidence SHA missing")
    estimated = (
        quote["covered_cells"]
        * (
            quote["cpu_rate_per_second"] * quote["p90_cpu_seconds_per_cell"]
            + quote["memory_rate_per_gib_second"] * quote["p90_gib_seconds_per_cell"]
        )
        + quote["overhead_usd"]
    )
    if not math.isfinite(estimated) or estimated <= 0:
        raise ValueError("STOP_COST_UNKNOWN: estimated batch price is not positive")
    if estimated > estimated_usd + 1e-9:
        raise ValueError(
            f"STOP_BUDGET: current-rate P90 USD {estimated:.6f} > authorized batch ceiling {estimated_usd:.6f}"
        )
    return {
        "source": path,
        "predicted_p90_usd": round(estimated, 6),
        "covered_cells": quote["covered_cells"],
        "captured_utc": quote["captured_utc"],
    }


def check_l1b_smoke_gate(
    *, smoke: bool, max_workers: int, batch_limit: int, completed_new: int, smoke_passed: bool
) -> None:
    """Require one finalized replay before dispatching a longer L1-B/C batch."""
    if smoke:
        require(max_workers == 1 and batch_limit == 1, "STOP_SMOKE: use one worker and one new cell")
        require(
            completed_new in (0, 1) and not smoke_passed,
            "STOP_SMOKE: smoke already completed or more than one new cell exists",
        )
    else:
        require(smoke_passed and completed_new >= 1, "STOP_SMOKE_FIRST: run --smoke with 1 worker/1 cell first")
        require(1 <= batch_limit <= 32, "STOP_BATCH_BOUND: choose 1..32 new cells per L1-B/C batch")


def result_prefix(run_id: str, stage: str, code_sha: str) -> str:
    """Versioned, non-overwriting research prefix for one run."""
    if not re.fullmatch(r"[0-9a-f]{40}", code_sha or ""):
        raise ValueError("STOP: code_sha must be a full git SHA")
    return f"csi1000_layered_search/{PROTOCOL_VERSION}/{run_id}/{stage}/{code_sha[:12]}"


def build_jobs(cells: List[Dict[str, Any]], prefix: str) -> List[Dict[str, Any]]:
    """Local job descriptors (no placeholder SHAs; resolved remotely per cell)."""
    return [{"cell": c, "prefix": prefix} for c in cells]


if _HAS_MODAL:
    original = modal.Volume.from_name(SOURCE_VOLUME, create_if_missing=False)
    research = modal.Volume.from_name(RESEARCH_VOLUME, create_if_missing=False)
    image = (
        modal.Image.debian_slim(python_version="3.11")
        .apt_install("build-essential")
        .env({"PYTHONPATH": "/root/qlib:/root", "MLFLOW_ALLOW_FILE_STORE": "true"})
        # P0 replays frozen signals directly from the copied source tree.
        # Do NOT pip-install this dynamic-SCM package: Modal omits .git, and
        # modern setuptools-scm/vcs-versioning cannot infer its version.
        # An image-only _version.py avoids SCM metadata processing entirely.
        .pip_install(
            "numpy==1.26.4",
            "pandas==2.2.3",
            "pyarrow==17.0.0",
            "lightgbm==4.7.0",
            "cython",
            "setuptools-scm",
            "pyyaml",
            "ruamel.yaml",
            "fire",
            "mlflow",
            "dill",
            "filelock",
            "tqdm",
            "loguru",
            "joblib",
            "pydantic-settings",
            "redis",
            "python-redis-lock",
            "pymongo",
            "gym",
            "cvxpy",
            "matplotlib",
            "torch",
        )
        .add_local_dir(
            ".",
            remote_path=str(REPO),
            copy=True,
            ignore=lambda p: (
                str(p).endswith((".cpp", ".so"))
                or any(part in str(p) for part in (".venv", "mlruns", "__pycache__", ".git"))
            ),
        )
        .run_commands(
            # Image-only metadata; not a production package version.
            "printf 'version = \"0.0.0\"\\n' > /root/qlib/qlib/_version.py",
            # Build from /tmp, *outside* pyqlib's pyproject.toml/SCM root.
            # Extension outputs still land in the PYTHONPATH source tree.
            "cd /tmp && python -c \"from setuptools import setup, Extension; from Cython.Build import cythonize; import numpy; root='/root/qlib'; extensions=[Extension('qlib.data._libs.rolling', [root+'/qlib/data/_libs/rolling.pyx'], language='c++', include_dirs=[numpy.get_include()]), Extension('qlib.data._libs.expanding', [root+'/qlib/data/_libs/expanding.pyx'], language='c++', include_dirs=[numpy.get_include()])]; setup(name='qlib-image-cython-only', version='0.0.0', ext_modules=cythonize(extensions, language_level=3), script_args=['build_ext', '--build-lib', root, '--build-temp', '/tmp/qlib-image-build'])\"",
            "cd /tmp && python -c \"import qlib; from qlib.data._libs import rolling, expanding; assert qlib.__version__ == '0.0.0'\"",
        )
    )
    app = modal.App("qlib-csi1000-layered-search-pr-s0", image=image)

    @app.function(
        volumes={str(SOURCE): original.with_mount_options(read_only=True), str(RESULTS): research},
        cpu=2,
        memory=4096,
        timeout=3600,
        retries=0,
    )
    def replay_cell_remote(job: Dict[str, Any]) -> Dict[str, Any]:
        """Execute one frozen-signal cell. Never trains, never writes the source volume."""
        import pandas as pd

        from audit.csi1000_layered_search_replay import (
            execute_cell,
            load_frozen_result,
            lookup_phase,
            publish_cell,
            resolve_phase_sources,
            static_quote_union,
        )
        from csi1000_stage_b_core import MARKET, RESERVED_EXECUTION_END, RESERVED_EXECUTION_START
        from qlib.data import D

        cell = job["cell"]
        try:
            import qlib

            qlib.init(provider_uri=str(SOURCE / "cn_data"), region="cn")
            result = load_frozen_result(REPO)
            provider_snapshot = SOURCE / "csi1000_stage_b" / "provider_snapshot.json"
            if not provider_snapshot.is_file():
                raise ValueError("STOP_SOURCE_UNAVAILABLE: Stage-B provider snapshot missing")
            provider_meta = json.loads(provider_snapshot.read_text(encoding="utf-8"))
            frozen_manifest = result.get("manifest", {})
            if provider_meta.get("snapshot_token") != frozen_manifest.get("snapshot_token") or provider_meta.get(
                "provider_fingerprint"
            ) != frozen_manifest.get("provider_fingerprint"):
                raise ValueError("STOP_P0: source provider identity differs from frozen Stage-B")
            phase_row = lookup_phase(result, cell["candidate_id"], int(cell["phase"]))
            snapshot_root = SOURCE / "csi1000_stage_b" / result["manifest"]["snapshot_token"]
            sources = resolve_phase_sources(snapshot_root, phase_row, cell["candidate_id"], int(cell["phase"]))
            frame = pd.read_parquet(sources["signal"])
            if list(frame.columns) != ["score"]:
                raise ValueError("STOP_SOURCE_UNAVAILABLE: frozen signal schema drift")
            signal = frame["score"]
            frozen_report = pd.read_parquet(sources["report"])
            frozen_decisions = json.loads(sources["decision"].read_text(encoding="utf-8"))
            quote_codes, quote_sha = static_quote_union(MARKET, RESERVED_EXECUTION_START, RESERVED_EXECUTION_END, D)
            record, artifacts = execute_cell(
                cell=cell,
                sources=sources,
                signal=signal,
                frozen_report=frozen_report,
                frozen_decisions=frozen_decisions,
                quote_codes=quote_codes,
                quote_sha=quote_sha,
                market_codes=MARKET,
            )
            cache_key = replay_cache_key(
                record["signal_sha256"], record["topk"], record["dropn"], record["quote_sha256"]
            )
            record["provider_fingerprint"] = frozen_manifest["provider_fingerprint"]
            record["provider_snapshot_sha256"] = __import__(
                "audit.csi1000_layered_search_core", fromlist=["sha256_file"]
            ).sha256_file(provider_snapshot)
            final = publish_cell(RESULTS / job["prefix"] / "cells", record, artifacts, cache_key)
            # Only the independent research volume is ever committed; the source
            # volume object has no commit call anywhere in this file.
            research.commit()
            return {**final, "resumed": final.get("resumed", False)}
        except Exception as exc:  # fail closed per cell, never partial-write as PASS
            return {
                "status": "BLOCKED",
                "cell_key": cell["cell_key"],
                "reason": f"{type(exc).__name__}: {exc}",
                "training_performed": False,
            }

    @app.function(
        volumes={str(SOURCE): original.with_mount_options(read_only=True), str(RESULTS): research},
        cpu=1,
        memory=2048,
        timeout=900,
    )
    def inventory_frozen_sources_remote(prefix: str) -> Dict[str, Any]:
        """SHA-verify all 11x5 frozen signals before P0 portfolio selection."""
        from audit.csi1000_layered_search_replay import (
            load_frozen_result,
            resolve_phase_sources,
        )
        from audit.csi1000_layered_search_core import sha256_file

        research.reload()
        frozen = load_frozen_result(REPO)
        info = frozen["manifest"]
        snapshot = SOURCE / "csi1000_stage_b" / "provider_snapshot.json"
        if not snapshot.is_file():
            raise ValueError("STOP_SOURCE_UNAVAILABLE: provider snapshot manifest absent")
        actual = json.loads(snapshot.read_text(encoding="utf-8"))
        if actual.get("snapshot_token") != info.get("snapshot_token") or actual.get("provider_fingerprint") != info.get(
            "provider_fingerprint"
        ):
            raise ValueError("STOP_P0: provider token/fingerprint mismatch")
        entries = []
        source_root = SOURCE / "csi1000_stage_b" / info["snapshot_token"]
        for candidate in frozen.get("ranked_candidates", []):
            cid = candidate["candidate_id"]
            for row in candidate["phase_results"]:
                phase = int(row["phase"])
                sources = resolve_phase_sources(source_root, row, cid, phase)
                expected_model = info["candidate_model_config_sha256"].get(cid)
                if not expected_model:
                    raise ValueError("STOP_SOURCE_UNAVAILABLE: model config identity missing")
                chunks = row.get("chunk_predictions")
                if not isinstance(chunks, list) or not chunks:
                    raise ValueError("STOP_SOURCE_UNAVAILABLE: retrain chunk lineage missing")
                if any(chunk.get("model_config_sha256") != expected_model for chunk in chunks):
                    raise ValueError("STOP_P0: retrain model config drift")
                entries.append(
                    {
                        "candidate_id": cid,
                        "phase": phase,
                        "model_config_sha256": expected_model,
                        "signal_sha256": sources["signal_sha256"],
                        "report_sha256": sources["report_sha256"],
                        "decision_sha256": sources["decision_sha256"],
                        "n_retrain_chunks": len(chunks),
                        "chunk_prediction_sha256": [chunk.get("prediction_content_sha256") for chunk in chunks],
                    }
                )
        if len(entries) != 55 or len({(e["candidate_id"], e["phase"]) for e in entries}) != 55:
            raise ValueError("STOP_P0: original frozen 11x5 source inventory incomplete")
        import qlib
        from audit.csi1000_layered_search_replay import static_quote_union
        from csi1000_stage_b_core import MARKET, RESERVED_EXECUTION_END, RESERVED_EXECUTION_START
        from qlib.data import D

        qlib.init(provider_uri=str(SOURCE / "cn_data"), region="cn")
        quote_codes, quote_sha = static_quote_union(MARKET, RESERVED_EXECUTION_START, RESERVED_EXECUTION_END, D)
        payload = {
            "status": "SOURCE_11x5_SHA_VERIFIED",
            "protocol": PROTOCOL_VERSION,
            "provider_fingerprint": actual["provider_fingerprint"],
            "provider_snapshot_sha256": sha256_file(snapshot),
            "snapshot_token": actual["snapshot_token"],
            "quote_union_size": len(quote_codes),
            "quote_sha256": quote_sha,
            "sources": entries,
        }
        payload["source_55_sha256"] = frozen_source_digest(entries)
        target = RESULTS / prefix / "frozen_source_11x5.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
        if target.exists():
            if target.read_text(encoding="utf-8") != encoded:
                raise ValueError("STOP_P0: frozen source inventory conflict")
        else:
            target.write_text(encoded, encoding="utf-8")
            research.commit()
        return {
            "status": payload["status"],
            "count": len(entries),
            "manifest_sha256": sha256_file(target),
            "provider_snapshot_sha256": payload["provider_snapshot_sha256"],
            "provider_fingerprint": payload["provider_fingerprint"],
            "quote_sha256": payload["quote_sha256"],
            "quote_union_size": payload["quote_union_size"],
            "source_55_sha256": payload["source_55_sha256"],
        }

    @app.function(volumes={str(RESULTS): research}, cpu=1, memory=1024, timeout=120)
    def inventory_completed_remote(prefix: str, stage: str, run_id: str = "", code_sha: str = "") -> Dict[str, Any]:
        """Read existing research artifacts and accept only verified output hashes."""
        from audit.csi1000_layered_search_core import sha256_file

        research.reload()
        output = RESULTS / prefix / "cells"
        expected = {c["cell_key"] for c in enumerate_cells(stage)}
        completed = []
        if output.exists():
            for root in output.iterdir():
                if not root.is_dir() or root.name.endswith(".tmp"):
                    continue
                if root.name not in expected:
                    raise RuntimeError(f"STOP_P0: unexpected existing cell identity {root.name}")
                record = json.loads((root / "cell.json").read_text(encoding="utf-8"))
                check_cell_record(record)
                required = ("daily_nav.csv", "fixed_report.parquet", "fixed_decisions.json", "metrics.json")
                hashes = record.get("artifact_sha256")
                if not isinstance(hashes, dict) or set(hashes) != set(required):
                    raise RuntimeError("STOP_P0: full persisted artifact manifest absent")
                for filename in required:
                    if not (root / filename).is_file() or sha256_file(root / filename) != hashes[filename]:
                        raise RuntimeError("STOP_P0: persisted artifact SHA drift")
                if hashes["daily_nav.csv"] != record["output_sha256"]:
                    raise RuntimeError("STOP_P0: persisted NAV SHA drift")
                completed.append(root.name)
        smoke_passed = False
        marker = RESULTS / prefix / "smoke_pass.json"
        if stage in ("l1-b", "l1-c") and marker.is_file():
            proof = json.loads(marker.read_text(encoding="utf-8"))
            prior_stage = "l1-a" if stage == "l1-b" else "l1-b"
            new_keys = {c["cell_key"] for c in enumerate_cells(stage)} - {
                c["cell_key"] for c in enumerate_cells(prior_stage)
            }
            smoke_key = proof.get("cell_key")
            if (
                proof.get("stage") != stage
                or proof.get("run_id") != run_id
                or proof.get("code_sha") != code_sha
                or proof.get("status") != "SMOKE_PASS"
                or smoke_key not in new_keys
                or smoke_key not in completed
                or sha256_file(output / smoke_key / "cell.json") != proof.get("cell_record_sha256")
            ):
                raise RuntimeError("STOP_SMOKE: stored smoke proof is invalid or artifact changed")
            smoke_passed = True
        return {"completed": sorted(completed), "smoke_passed": smoke_passed}

    @app.function(
        volumes={str(SOURCE): original.with_mount_options(read_only=True), str(RESULTS): research},
        cpu=2,
        memory=4096,
        timeout=900,
    )
    def verify_reuse_remote(new_prefix: str, old_prefix: str, specs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Materialize prior-run cells under the new prefix (all or nothing).

        Byte-verifies every spec against the old prefix FIRST, then copies
        each verified cell tree into the new prefix. Runs BEFORE any new
        compute: a reuse failure aborts the batch with zero replay spend.
        """
        from audit.csi1000_layered_search_core import materialize_reuse_cells

        research.reload()
        try:
            records = materialize_reuse_cells(RESULTS, new_prefix, old_prefix, specs)
        except ValueError as exc:
            raise RuntimeError(f"STOP_L1: reuse materialization failed: {exc}") from exc
        research.commit()
        return records

    @app.function(
        volumes={str(SOURCE): original.with_mount_options(read_only=True), str(RESULTS): research},
        cpu=2,
        memory=4096,
        timeout=900,
    )
    def finalize_remote(
        prefix: str,
        stage: str,
        outcomes: List[Dict[str, Any]],
        code_sha: str,
        run_id: str,
        attestations: List[Dict[str, Any]] | None = None,
        smoke: bool = False,
    ) -> Dict[str, Any]:
        """Revalidate persisted bytes of new and materialized prior-run cells.

        L1-B old cells have already been copied to this run's own prefix and
        are checked through the same artifact and metric gates as new cells.
        """
        from audit.csi1000_layered_search_core import sha256_file, write_sha256sums
        from audit.csi1000_layered_search_replay import report_metrics
        import pandas as pd

        research.reload()
        previous: List[Dict[str, Any]] = []
        expected_keys = {c["cell_key"] for c in enumerate_cells(stage)}
        previously = RESULTS / prefix / "cells"
        if previously.exists():
            for folder in previously.iterdir():
                if not folder.is_dir() or folder.name.endswith(".tmp"):
                    continue
                previous.append(json.loads((folder / "cell.json").read_text(encoding="utf-8")))
        verified, blocked = [], []
        seen = set()
        for o in previous + outcomes:
            if o.get("cell_key") in seen:
                # A re-returned cell from the same batch must match exactly.
                if o.get("cell_key") in expected_keys:
                    persisted = next(p for p in previous if p["cell_key"] == o["cell_key"])
                    for k in ("source_sha256", "output_sha256", "cache_key"):
                        if o.get(k) != persisted.get(k):
                            raise RuntimeError("STOP_P0: duplicated cell returned conflicting evidence")
                    continue
                raise RuntimeError("STOP_P0: conflicting duplicate cell result")
            seen.add(o.get("cell_key"))
            try:
                check_cell_record(o)
                data_dir = RESULTS / prefix / "cells" / o["cell_key"]
                recorded = json.loads((data_dir / "cell.json").read_text(encoding="utf-8"))
                for field in ("cache_key", "source_sha256", "output_sha256"):
                    if recorded.get(field) != o.get(field):
                        raise ValueError(f"remote return differs from persisted {field}")
                nav_file = data_dir / "daily_nav.csv"
                if sha256_file(nav_file) != o["output_sha256"]:
                    raise ValueError("daily NAV bytes hash drift")
                artifacts = o.get("artifact_sha256")
                expected_files = ("daily_nav.csv", "fixed_report.parquet", "fixed_decisions.json", "metrics.json")
                if not isinstance(artifacts, dict) or set(artifacts) != set(expected_files):
                    raise ValueError("full artifact SHA manifest absent")
                for name in expected_files:
                    if not (data_dir / name).is_file() or sha256_file(data_dir / name) != artifacts[name]:
                        raise ValueError(f"output artifact hash mismatch: {name}")
                frame = pd.read_parquet(data_dir / "fixed_report.parquet")
                recalculated, checks = report_metrics(frame)
                if abs(frame["account"].iloc[-1] - o["final_nav_cny"]) > 0.01:
                    raise ValueError("final NAV differs from persisted source")
                if abs(frame["total_cost"].iloc[-1] - o["total_cost_cny"]) > 0.01:
                    raise ValueError("total CNY cost differs from persisted source")
                for metric in ("strategy_total_return", "relative_excess_cagr", "mean_turnover"):
                    if abs(float(recalculated[metric]) - float(o["metrics"][metric])) > 1e-10:
                        raise ValueError(f"independently recomputed {metric} drift")
                verified.append(o)
            except Exception as exc:
                blocked.append({"cell_key": o.get("cell_key"), "reason": f"{type(exc).__name__}: {exc}"})
        attestations = attestations or []
        attested = {a.get("cell_key") for a in attestations}
        if attested - seen:
            raise RuntimeError("STOP_L1: attestation without verified cell")
        summary = {
            "protocol": PROTOCOL_VERSION,
            "stage": stage,
            "run_id": run_id,
            "code_sha": code_sha,
            "expected": len(expected_keys),
            "completed": len(verified),
            "blocked": len(blocked),
            "remaining": len(expected_keys) - len(verified) - len(blocked),
            "status": "PASS"
            if len(verified) == len(expected_keys) and not blocked
            else ("BLOCKED" if blocked else "INCOMPLETE_BATCH"),
            "blocked_cells": blocked,
            "verified_cells": verified,
            "reuse_attestations": attestations,
            "computed_new": len(verified) - len(attested),
            "reused": len(attested),
            "training_performed": False,
            "promotion_authorized": False,
            "source_volume_read_only": True,
        }
        # Validate the full stage roster, reuse SHA attestations and batch
        # accounting BEFORE publishing any PASS summary to the research Volume.
        from audit.csi1000_layered_search_reconcile import check_stage_summary

        check_stage_summary(summary)
        root = RESULTS / prefix / "summaries" / stage
        root.mkdir(parents=True, exist_ok=True)
        from uuid import uuid4

        batch_path = root / f"batch_{uuid4().hex}.json"
        batch_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if summary["status"] == "PASS":
            target = root / f"stage_{stage}_summary.json"
            if target.exists():
                raise RuntimeError("summary already finalized: refusing to overwrite")
            target.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        protocol_path = root / "protocol.json"
        protocol_bytes = (
            json.dumps(
                build_protocol(
                    run_id,
                    approved_by=(
                        "L1B_SMOKE_FIRST_NO_COST_APPROVAL"
                        if stage == "l1-b"
                        else ("L1C_SMOKE_FIRST_NO_COST_APPROVAL" if stage == "l1-c" else "PENDING_BUDGET_APPROVAL")
                    ),
                ),
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        if protocol_path.exists() and protocol_path.read_text(encoding="utf-8") != protocol_bytes:
            raise RuntimeError("STOP_P0: frozen protocol already exists with different content")
        if not protocol_path.exists():
            protocol_path.write_text(protocol_bytes, encoding="utf-8")
        source_55_file = RESULTS / prefix / "frozen_source_11x5.json"
        if not source_55_file.is_file():
            raise RuntimeError("STOP_P0: 11x5 source SHA inventory absent")
        source_55 = json.loads(source_55_file.read_text(encoding="utf-8"))
        if len(source_55.get("sources", [])) != 55:
            raise RuntimeError("STOP_P0: 11x5 source inventory incomplete")
        manifest = {
            "frozen_11x5_manifest_sha256": sha256_file(source_55_file),
            "frozen_11x5_sources": len(source_55["sources"]),
            "protocol": PROTOCOL_VERSION,
            "run_id": run_id,
            "stage": stage,
            "code_sha": code_sha,
            "cells": [
                {
                    "cell_key": item["cell_key"],
                    "candidate_id": item["candidate_id"],
                    "phase": item["phase"],
                    "signal_sha256": item["signal_sha256"],
                    "quote_sha256": item["quote_sha256"],
                    "provider_fingerprint": item["provider_fingerprint"],
                    "provider_snapshot_sha256": item["provider_snapshot_sha256"],
                    "source_sha256": item["source_sha256"],
                    "output_sha256": item["output_sha256"],
                    "cache_key": item["cache_key"],
                }
                for item in verified
            ],
        }
        manifest_path = (
            RESULTS / prefix / "source_manifest_final.json"
            if summary["status"] == "PASS"
            else RESULTS / prefix / f"source_manifest_partial_{__import__('uuid').uuid4().hex}.json"
        )
        if manifest_path.exists():
            raise RuntimeError("STOP_P0: signed source manifest already exists; refusing overwrite")
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if smoke:
            prior_stage = {"l1-b": "l1-a", "l1-c": "l1-b"}.get(stage)
            if prior_stage is None:
                raise RuntimeError("STOP_SMOKE: only L1-B/L1-C have first-cell smoke")
            reused_count = len(enumerate_cells(prior_stage))
            if summary["status"] != "INCOMPLETE_BATCH" or summary["completed"] != reused_count + 1:
                raise RuntimeError(f"STOP_SMOKE: expected {reused_count} reused and exactly one verified new cell")
            new_keys = {c["cell_key"] for c in enumerate_cells(stage)} - {
                c["cell_key"] for c in enumerate_cells(prior_stage)
            }
            done = [c for c in verified if c["cell_key"] in new_keys]
            if len(done) != 1 or summary["blocked"] or len(attestations) != reused_count:
                raise RuntimeError("STOP_SMOKE: incomplete first-cell evidence")
            smoke_file = RESULTS / prefix / "smoke_pass.json"
            if smoke_file.exists():
                raise RuntimeError("STOP_SMOKE: smoke proof exists; never overwrite")
            smoke_file.write_text(
                json.dumps(
                    {
                        "stage": stage,
                        "run_id": run_id,
                        "code_sha": code_sha,
                        "cell_key": done[0]["cell_key"],
                        "cell_record_sha256": sha256_file(
                            RESULTS / prefix / "cells" / done[0]["cell_key"] / "cell.json"
                        ),
                        "status": "SMOKE_PASS",
                    },
                    sort_keys=True,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        write_sha256sums(RESULTS / prefix)
        research.commit()
        return summary

    @app.local_entrypoint()
    def run_search(
        stage: str = "p0",
        run_id: str = "",
        max_workers: int = 2,
        batch_limit: int = 0,
        estimated_total_usd: float = -1.0,
        resume_from_manifest: str = "",
        ledger: str = "~/.local/state/qlib-csi1000-layered/cost_ledger.json",
        p0_summary: str = "",
        cost_quote: str = "",
        reuse_summary: str = "",
        reuse_prefix: str = "",
        equivalence_ref: str = "",
        smoke: bool = False,
    ) -> None:
        """Foreground Modal entrypoint: P0 controls, then gated L1-A/L1-B screens.

        Execute attached, e.g. ``modal run audit/csi1000_layered_search_modal.py
        --stage p0 --run-id <id> --max-workers 2 --estimated-total-usd 2.0``.
        Never append ``--detach`` when launched through OpenCode. L1-A also
        needs ``--p0-summary <downloaded stage_p0_summary.json>`` proving 4/4
        verified P0 cells. L1-B additionally needs ``--reuse-summary`` (a
        verified 32/32 L1-A export), ``--reuse-prefix`` (its research prefix)
        and ``--equivalence-ref`` (the reviewer-approved equivalence decision).
        For L1-B/C run ``--smoke --max-workers 1 --batch-limit 1`` first;
        after verified smoke start with 2 workers and 8 cells; successful
        batches may grow to 4 workers and 16–32 cells. Both stages skip
        legacy quote/ledger paperwork while retaining strict evidence gates.
        """
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        if subprocess.check_output(["git", "status", "--porcelain"], text=True).strip():
            raise ValueError("STOP: worktree must be clean to bind code SHA to the Modal image")
        cells = validate_for_dispatch(
            stage,
            run_id,
            max_workers,
            batch_limit,
            estimated_total_usd,
            resume_from_manifest,
            ledger,
            p0_summary,
            reuse_summary,
            equivalence_ref,
            reuse_prefix,
            sha,
            smoke,
        )
        if stage == "p0" and max_workers > 2:
            raise ValueError("STOP_BUDGET: P0 must start with no more than two workers")
        prefix = result_prefix(run_id, stage, sha)
        if stage not in ("l1-b", "l1-c"):
            # Historical P0/L1-A accounting is unchanged. L1-B intentionally
            # does not require a bill quote, ledger write, or batch approval.
            estimate_n = len(cells)
            check_operator_cost_quote(cost_quote, float(estimated_total_usd), estimate_n)
            ledger_obj = BudgetLedger.load(ledger)
            gate = ledger_obj.preflight("p0" if stage == "p0" else "l1", float(estimated_total_usd))
            if gate != "OK_CONTINUE":
                raise ValueError(f"STOP: ledger preflight gate {gate}")
            ledger_obj.record_estimate(
                "p0" if stage == "p0" else "l1",
                float(estimated_total_usd),
                run_id=run_id,
                note="reserve before remote inventory or replay",
            )
            ledger_obj.save(ledger)
        verified_roster = inventory_frozen_sources_remote.remote(prefix)
        if verified_roster.get("count") != 55:
            raise RuntimeError("STOP_P0: missing 11x5 source inventory")
        attestations: List[Dict[str, Any]] = []
        if stage in ("l1-b", "l1-c"):
            from audit.csi1000_layered_search_core import check_live_provider_match

            plan = (
                prepare_l1b_plan(reuse_summary, equivalence_ref, sha)
                if stage == "l1-b"
                else prepare_l1c_plan(reuse_summary, p0_summary, equivalence_ref, sha)
            )
            check_live_provider_match(
                verified_roster.get("provider_snapshot_sha256", ""),
                verified_roster.get("quote_sha256", ""),
                plan["provider_snapshot_sha256"],
                plan["quote_sha"],
            )
            require(
                verified_roster.get("source_55_sha256") == plan["source_55_sha256"],
                "STOP_L1: live frozen 11x5 source inventory differs from audited L1-A export",
            )
            # Materialize BEFORE the empty-batch check and BEFORE any new
            # compute: reuse must be proven with zero replay spend at risk.
            # Idempotent: identical bytes resume, conflicts refuse.
            specs = [
                {
                    "cell_key": key,
                    "source_sha256": next(r["source_sha256"] for r in plan["reuse_records"] if r["cell_key"] == key),
                    "output_sha256": next(r["output_sha256"] for r in plan["reuse_records"] if r["cell_key"] == key),
                    "cache_key": next(r["cache_key"] for r in plan["reuse_records"] if r["cell_key"] == key),
                }
                for key in plan["overlap_keys"]
            ]
            verify_reuse_remote.remote(prefix, reuse_prefix, specs)
            attestations = plan["attestations"]
            planned = list(cells)
        else:
            planned = sorted(enumerate_cells(stage), key=lambda c: c["cell_key"])
        completed_state = inventory_completed_remote.remote(prefix, stage, run_id, sha)
        completed = set(completed_state["completed"])
        if stage in ("l1-b", "l1-c"):
            eligible_new = {c["cell_key"] for c in planned}
            completed_new = len(completed & eligible_new)
            check_l1b_smoke_gate(
                smoke=smoke,
                max_workers=max_workers,
                batch_limit=batch_limit,
                completed_new=completed_new,
                smoke_passed=completed_state["smoke_passed"],
            )
        # An interrupted smoke can leave one verified new cell without its
        # final smoke_pass.json. Never select a second new cell on retry.
        if stage in ("l1-b", "l1-c") and smoke and completed_new == 1:
            cells = []
        else:
            cells = select_pending_batch(planned, completed, batch_limit)
        # Recovery after a completed cell but interrupted finalizer: verify
        # and sign the existing smoke result WITHOUT paying to recompute it.
        smoke_recover = stage in ("l1-b", "l1-c") and smoke and not cells and completed_new == 1
        # Recovery: all new cells verified but a previous finalization crashed.
        finalize_only = stage in ("l1-b", "l1-c") and not cells and len(completed) == len(enumerate_cells(stage))
        if not cells and not (finalize_only or smoke_recover):
            raise ValueError("STOP: no unexecuted cells in this batch; request the existing verified summary")
        print(
            json.dumps(
                {
                    "stage": stage,
                    "cells": len(cells),
                    "max_workers": max_workers,
                    "execution_mode": "smoke" if smoke else "bounded_batch",
                    "replay_cpu": 2,
                    "replay_memory_mib": 4096,
                    "code_sha": sha,
                    "source_volume_read_only": True,
                    "output_prefix": prefix,
                    "opencode_foreground_modal": True,
                    "modal_detach_used": False,
                },
                indent=2,
            ),
            flush=True,
        )
        outcomes = (
            []
            if finalize_only or smoke_recover
            else list(replay_cell_remote.with_options(max_containers=max_workers).map(build_jobs(cells, prefix)))
        )
        summary = finalize_remote.remote(prefix, stage, outcomes, sha, run_id, attestations=attestations, smoke=smoke)
        if stage not in ("l1-b", "l1-c"):
            _write_inflight_ledger(
                Path(ledger).expanduser().parent / TRIAL_LEDGER_NAME,
                stage,
                run_id,
                outcomes,
                note="historical stage: billing can be booked from the operator report",
            )
        print(json.dumps(summary, indent=2), flush=True)
        if summary["blocked"]:
            raise RuntimeError("STOP_P0: one or more P0/L1 controls failed; not eligible for continuation")
        if stage in ("l1-b", "l1-c"):
            if smoke:
                print("SMOKE_PASS: evidence recorded; proceed with bounded 2-worker batches.", flush=True)
            elif summary["status"] == "INCOMPLETE_BATCH":
                print("INCOMPLETE_BATCH: resume same run_id/code SHA with another bounded batch.", flush=True)
            print(
                "OBSERVE: keep App ID, runtime and failure logs; stop repeated failures or resource pressure.",
                flush=True,
            )
        else:
            if summary["status"] == "INCOMPLETE_BATCH":
                print("INCOMPLETE_BATCH: book the real bill before launching the next bounded batch.", flush=True)
            print("NEXT: fill the real App ID/billed spend, then book it with BudgetLedger.record_actual.", flush=True)


def _write_inflight_ledger(path: Path, stage: str, run_id: str, outcomes: List[Dict[str, Any]], note: str) -> Path:
    """Append pending trial events without presenting unknown billing as final.

    Operator booking uses these events to create separately audited, immutable
    final trial_ledger.jsonl records. Never call pending events PASS billing.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for o in outcomes:
            f.write(
                json.dumps(
                    {
                        "cell_key": o.get("cell_key", "?"),
                        "stage": stage,
                        "run_id": run_id,
                        "app_id": "",
                        "app_id_source": "operator_modal_run_header_and_billing_report",
                        "status": o.get("status", "BLOCKED"),
                        "spend_usd": None,
                        "reason": o.get("reason", ""),
                        "note": note,
                    },
                    sort_keys=True,
                )
                + "\n"
            )
    return path


def build_cli() -> argparse.ArgumentParser:
    """CLI contract (real --help; mirrors the Modal entrypoint parameters)."""
    p = argparse.ArgumentParser(
        description="P0 + L1-A/B/C portfolio replay runner (zero-fit, read-only source).",
        epilog="OpenCode foreground rule: run attached without --detach; set the tool timeout beyond worst-case wall time.",
    )
    p.add_argument(
        "--stage", default="p0", help="p0 | l1-a | l1-b | l1-c | inventory | dry-run (l2/l3/l4 need later PRs)."
    )
    p.add_argument("--run-id", default="", help="Pre-registered run id [A-Za-z0-9][A-Za-z0-9_-]{3,63}.")
    p.add_argument("--max-workers", type=int, default=2, help="Parallel workers, 1..8 (PR-S0 cap).")
    p.add_argument(
        "--batch-limit", type=int, default=0, help="Max NEW compute cells this batch; 0 means all remaining."
    )
    p.add_argument("--estimated-total-usd", type=float, default=-1.0, help="Legacy P0/L1-A only; ignored for L1-B/C.")
    p.add_argument("--resume-from-manifest", default="", help="Existing source_manifest.json for hash-checked resume.")
    p.add_argument(
        "--ledger",
        default="~/.local/state/qlib-csi1000-layered/cost_ledger.json",
        help="Legacy P0/L1-A only; not read or written by L1-B/C.",
    )
    p.add_argument(
        "--p0-summary", default="", help="Downloaded stage_p0_summary.json; required for --stage l1-a/l1-b/l1-c."
    )
    p.add_argument("--reuse-summary", default="", help="Full signed L1-A summary for L1-B or L1-B summary for L1-C.")
    p.add_argument("--reuse-prefix", default="", help="Source research prefix of L1-A (L1-B) or L1-B (L1-C) export.")
    p.add_argument(
        "--equivalence-ref",
        default="",
        help="Reviewed equivalence decision for cross-code L1-B/C reuse.",
    )
    p.add_argument("--cost-quote", default="", help="Legacy P0/L1-A only; not required or read for L1-B/C.")
    p.add_argument("--smoke", action="store_true", help="L1-B/C first-run gate: 1 new replay cell with 1 worker.")
    p.add_argument("--dry-run", action="store_true", help="Local validation only; no Modal dispatch.")
    p.add_argument("--inventory", action="store_true", help="Print frozen enumeration and exit without dispatch.")
    return p


def _check_manifest(manifest_path: str) -> Dict[str, Any]:
    """Load a resume manifest; corrupt or placeholder content blocks the batch."""
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    require_manifest_ok = isinstance(manifest, dict) and isinstance(manifest.get("cells"), list)
    if not require_manifest_ok:
        raise ValueError("STOP: resume manifest is not a cell manifest")
    for row in manifest["cells"]:
        for k in ("cell_key", "source_sha256", "output_sha256", "cache_key"):
            if not row.get(k) or "pending" in str(row.get(k, "")):
                raise ValueError(f"STOP: resume manifest has placeholder {k} for {row.get('cell_key')}")
    return manifest


def _require_signed_export(summary_path: str, stage: str, gate: str) -> Dict[str, Any]:
    """Deep-check a downloaded stage export: summary record plus full file tree.

    ``gate`` selects messages (``STOP_P0`` for the P0 export, ``STOP_L1`` for
    the L1-A reuse export). Returns the parsed summary and its evidence root.
    """
    from audit.csi1000_layered_search_reconcile import verify_sha256sums

    try:
        summary = json.loads(Path(summary_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{gate}: cannot read {stage} summary: {exc}") from exc
    try:
        if stage == "p0":
            require_p0_pass(summary, summary.get("verified_cells"))
            expected_cells = {c["cell_key"] for c in enumerate_cells("p0")}
            signed = {r.get("cell_key"): r for r in summary.get("verified_cells", [])}
            verify_export_cells(Path(summary_path).resolve().parents[2], expected_cells, "P0", signed)
        elif stage in ("l1-a", "l1-b"):
            if stage == "l1-a":
                require_signed_l1a_export(summary, summary.get("verified_cells"))
            else:
                require_signed_l1b_export(summary, summary.get("verified_cells"))
            expected_cells = {c["cell_key"] for c in enumerate_cells(stage)}
            signed = {r.get("cell_key"): r for r in summary.get("verified_cells", [])}
            # JSON-only PASS claims are insufficient: verify full exported tree.
            verify_sha256sums(Path(summary_path).resolve().parents[2])
            verify_export_cells(
                Path(summary_path).resolve().parents[2],
                expected_cells,
                "L1-A" if stage == "l1-a" else "L1-B",
                signed,
            )
        else:
            raise ValueError(f"unsupported export stage {stage}")
    except (ValueError, FileNotFoundError, KeyError, OSError) as exc:
        raise ValueError(f"{gate}: complete signed {stage} research export required: {exc}") from exc
    return summary


def _check_reuse_prefix(reuse_prefix: str) -> str:
    """Fail fast on unsafe or out-of-tree reuse prefixes (mirrors the remote check)."""
    require(bool(reuse_prefix), "STOP_L1: L1-B/C require --reuse-prefix with the prior research prefix")
    require(not reuse_prefix.startswith("/") and ".." not in reuse_prefix.split("/"), "STOP_L1: unsafe reuse prefix")
    require(reuse_prefix.startswith("csi1000_layered_search/"), "STOP_L1: reuse prefix outside layered-search tree")
    return reuse_prefix


def prepare_l1b_plan(
    reuse_summary_path: str,
    equivalence_ref: str,
    current_code_sha: str,
) -> Dict[str, Any]:
    """Build the L1-B reuse plan from a verified L1-A export (local, no cloud).

    Deep-checks the export, derives the unanimous quote/provider identity,
    re-measures prior output bytes, and splits the 144-cell roster into 32
    verified reuses plus 112 new-compute cells with attestations.
    """
    from audit.csi1000_layered_search_core import sha256_file

    summary = _require_signed_export(reuse_summary_path, "l1-a", "STOP_L1")
    records = summary.get("verified_cells", [])
    quotes = {r.get("quote_sha256") for r in records}
    require(len(quotes) == 1 and all(quotes), "STOP_L1: reuse export has no unanimous quote identity")
    providers = {r.get("provider_snapshot_sha256") for r in records}
    require(len(providers) == 1 and all(providers), "STOP_L1: reuse export has no unanimous provider identity")
    evidence = Path(reuse_summary_path).resolve().parents[2]
    verified_output_sha = {}
    for record in records:
        nav_file = evidence / "cells" / record["cell_key"] / "daily_nav.csv"
        require(nav_file.is_file(), f"STOP_L1: reuse output file missing: {record['cell_key']}")
        measured = sha256_file(nav_file)
        require(measured == record.get("output_sha256"), f"STOP_L1: reuse output bytes drift: {record['cell_key']}")
        verified_output_sha[record["cell_key"]] = measured
    manifest_path = evidence / "frozen_source_11x5.json"
    require(manifest_path.is_file(), "STOP_L1: frozen 11x5 manifest missing from reuse export")
    original_sources = json.loads(manifest_path.read_text(encoding="utf-8"))["sources"]
    manifest_sources = manifest_source_index({"sources": original_sources})
    current = {
        "quote_sha": quotes.pop(),
        "provider_snapshot_sha256": providers.pop(),
        "code_sha": current_code_sha,
        "prior_run_id": summary.get("run_id", ""),
        "prior_code_sha": summary.get("code_sha", ""),
        "verified_output_sha": verified_output_sha,
        "version_equivalence_ref": equivalence_ref,
    }
    require(current["prior_run_id"] and current["prior_code_sha"], "STOP_L1: reuse summary lacks run identity")
    plan = plan_l1b_reuse(records, manifest_sources, current)
    return {
        **plan,
        "prior_run_id": current["prior_run_id"],
        "prior_code_sha": current["prior_code_sha"],
        "quote_sha": current["quote_sha"],
        "source_55_sha256": frozen_source_digest(original_sources),
    }


def prepare_l1c_plan(
    reuse_summary_path: str,
    p0_summary_path: str,
    equivalence_ref: str,
    current_code_sha: str,
) -> Dict[str, Any]:
    """Verify signed L1-B evidence and independently recalculate L1-C eligibility.

    Only genuine two-phase qualified L1-B exports may enter L1-C. Source,
    provider, quote and all 144 cell bytes must be identical to their
    signed records; every overlap is reattested under the new code SHA.
    """
    from audit.csi1000_layered_search_core import sha256_file
    from audit.csi1000_layered_search_selection import screen_l1

    summary = _require_signed_export(reuse_summary_path, "l1-b", "STOP_L1")
    _require_signed_export(p0_summary_path, "p0", "STOP_P0")
    evidence = Path(reuse_summary_path).resolve().parents[2]
    p0_root = Path(p0_summary_path).resolve().parents[2]
    screen = screen_l1("l1-b", evidence / "cells", p0_root / "cells")
    require(
        screen.get("can_expand_to_l1_c") is True and screen.get("qualified_configs", 0) >= 2,
        "STOP_L1_C_GATE: fewer than two independently qualified two-phase configurations",
    )
    records = summary["verified_cells"]
    quotes = {r.get("quote_sha256") for r in records}
    providers = {r.get("provider_snapshot_sha256") for r in records}
    require(len(quotes) == 1 and all(quotes), "STOP_L1: L1-B quote identity not unanimous")
    require(len(providers) == 1 and all(providers), "STOP_L1: L1-B provider identity not unanimous")
    verified_output_sha = {}
    for record in records:
        key = record["cell_key"]
        nav_file = evidence / "cells" / key / "daily_nav.csv"
        measured = sha256_file(nav_file)
        require(measured == record["output_sha256"], f"STOP_L1: L1-B NAV bytes drift: {key}")
        verified_output_sha[key] = measured
    manifest_path = evidence / "frozen_source_11x5.json"
    require(manifest_path.is_file(), "STOP_L1: L1-B frozen 11x5 manifest missing")
    original_sources = json.loads(manifest_path.read_text(encoding="utf-8"))["sources"]
    sources = manifest_source_index({"sources": original_sources})
    current = {
        "quote_sha": quotes.pop(),
        "provider_snapshot_sha256": providers.pop(),
        "code_sha": current_code_sha,
        "prior_run_id": summary.get("run_id", ""),
        "prior_code_sha": summary.get("code_sha", ""),
        "verified_output_sha": verified_output_sha,
        "version_equivalence_ref": equivalence_ref,
    }
    plan = plan_l1c_reuse(records, sources, current)
    return {
        **plan,
        "prior_run_id": current["prior_run_id"],
        "prior_code_sha": current["prior_code_sha"],
        "quote_sha": current["quote_sha"],
        "source_55_sha256": frozen_source_digest(original_sources),
        "l1b_screen": {
            "qualified_configs": screen["qualified_configs"],
            "can_expand_to_l1_c": screen["can_expand_to_l1_c"],
        },
    }


def validate_for_dispatch(
    stage: str,
    run_id: str,
    max_workers: int,
    batch_limit: int,
    estimated_total_usd: float,
    resume_from_manifest: str,
    ledger: str,
    p0_summary: str = "",
    reuse_summary: str = "",
    equivalence_ref: str = "",
    reuse_prefix: str = "",
    current_code_sha: str = "",
    smoke: bool = False,
) -> List[Dict[str, Any]]:
    """Fail-closed validation shared by the local CLI and the Modal entrypoint.

    Returns the cells to really compute. For L1-B that is only the 112 new
    cells; the 32 overlap cells travel via attestations (see
    :func:`prepare_l1b_plan`), never by silent cache hits.
    """
    if stage not in ("p0", "l1-a", "l1-b", "l1-c"):
        raise ValueError(f"STOP: stage {stage} needs a later reviewed PR (covers p0/l1-a/l1-b/l1-c only)")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{3,63}", run_id or ""):
        raise ValueError("STOP: --run-id must match [A-Za-z0-9][A-Za-z0-9_-]{3,63}")
    if not isinstance(max_workers, int) or not 1 <= max_workers <= 8:
        raise ValueError("STOP: --max-workers must be 1..8 in PR-S0")
    import math as _math

    if stage not in ("l1-b", "l1-c"):
        if (
            not isinstance(estimated_total_usd, (int, float))
            or not _math.isfinite(estimated_total_usd)
            or estimated_total_usd <= 0
        ):
            raise ValueError("STOP_COST_UNKNOWN: --estimated-total-usd is required and must be finite > 0")
        layer = "p0" if stage == "p0" else "l1"
        if estimated_total_usd > LAYER_CAPS_USD[layer]:
            raise ValueError(
                f"STOP_BUDGET: estimate {estimated_total_usd} exceeds layer {layer} cap {LAYER_CAPS_USD[layer]}"
            )
        ledger_obj = BudgetLedger.load(ledger)
        gate = ledger_obj.preflight(layer, float(estimated_total_usd))
        if gate != "OK_CONTINUE":
            raise ValueError(f"STOP: ledger preflight gate {gate} (spend unknown until record_actual)")
    if smoke and stage not in ("l1-b", "l1-c"):
        raise ValueError("STOP_SMOKE: --smoke applies only to L1-B/C")
    if smoke and (max_workers != 1 or batch_limit != 1):
        raise ValueError("STOP_SMOKE: use --max-workers 1 --batch-limit 1")
    if stage in ("l1-a", "l1-b", "l1-c"):
        if not p0_summary:
            raise ValueError(f"STOP_P0: {stage} requires --p0-summary with a verified 4/4 P0 summary")
        _require_signed_export(p0_summary, "p0", "STOP_P0")
    if stage in ("l1-b", "l1-c"):
        if not reuse_summary:
            raise ValueError(f"STOP_L1: {stage} requires a complete signed prior L1 export")
        if resume_from_manifest:
            raise ValueError(
                f"STOP_L1: {stage} resumes inside its own research prefix; --resume-from-manifest is not accepted"
            )
        _check_reuse_prefix(reuse_prefix)
        plan = (
            prepare_l1b_plan(reuse_summary, equivalence_ref, current_code_sha)
            if stage == "l1-b"
            else prepare_l1c_plan(reuse_summary, p0_summary, equivalence_ref, current_code_sha)
        )
        prior_stage = "l1-a" if stage == "l1-b" else "l1-b"
        required_prefix = result_prefix(plan["prior_run_id"], prior_stage, plan["prior_code_sha"])
        require(
            reuse_prefix == required_prefix,
            f"STOP_L1: --reuse-prefix does not match the audited {prior_stage} run/stage/code SHA",
        )
        new_cells = sorted(plan["new_cells"], key=lambda c: c["cell_key"])
        if batch_limit and (not isinstance(batch_limit, int) or not 1 <= batch_limit <= len(new_cells)):
            raise ValueError(f"STOP: --batch-limit out of range for the {len(new_cells)} new {stage} cells")
        return new_cells
    if resume_from_manifest:
        _check_manifest(resume_from_manifest)
    cells = enumerate_cells(stage)
    if batch_limit and (not isinstance(batch_limit, int) or not 1 <= batch_limit <= len(cells)):
        raise ValueError("STOP: --batch-limit out of range")
    ordered = sorted(cells, key=lambda c: c["cell_key"])
    return ordered[:batch_limit] if batch_limit else ordered


def validate_args(args: argparse.Namespace) -> List[Dict[str, Any]]:
    """Argparse wrapper around :func:`validate_for_dispatch`."""
    if args.inventory or args.stage == "inventory":
        return []
    if args.stage == "dry-run":
        return []
    return validate_for_dispatch(
        args.stage,
        args.run_id,
        args.max_workers,
        args.batch_limit,
        args.estimated_total_usd,
        args.resume_from_manifest,
        args.ledger,
        args.p0_summary,
        args.reuse_summary,
        args.equivalence_ref,
        args.reuse_prefix,
        smoke=args.smoke,
    )


def main(argv: List[str] | None = None) -> int:
    """Local CLI entry point (validation/inventory/dry-run; never dispatches Modal).

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
                    "l1-b": len(enumerate_cells("l1-b")),
                    "l1-b-new": 112,
                    "l1-c": len(enumerate_cells("l1-c")),
                    "l1-c-reused-from-l1-b": 144,
                    "l1-c-new": 120,
                    "layer_caps": LAYER_CAPS_USD,
                },
                indent=2,
            )
        )
        return 0
    if args.dry_run or args.stage == "dry-run":
        load_frozen_profiles(".")
        validate_for_dispatch("p0", args.run_id or "dry-run-0000", 2, 0, 1.0, "", args.ledger)
        print("DRY_RUN_OK: frozen profiles + P0 gate valid; no Modal dispatch, no spend")
        return 0
    cells = validate_args(args)
    if not _HAS_MODAL:
        print(
            json.dumps(
                {
                    "stage": args.stage,
                    "cells": [c["cell_key"] for c in cells],
                    "validated": True,
                    "dispatched": False,
                    "reason": "local environment has no modal package; dispatch needs the authorized workspace",
                },
                indent=2,
            )
        )
        return 0
    print(
        json.dumps(
            {
                "stage": args.stage,
                "cells": len(cells),
                "note": "dispatch via attached `modal run audit/csi1000_layered_search_modal.py --stage ...`",
                "opencode_foreground_modal": True,
                "modal_detach_used": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
