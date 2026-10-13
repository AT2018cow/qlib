"""Modal P0/L1 runner for the CSI1000 budgeted layered search (PR-S0).

Real frozen-signal replay via :mod:`audit.csi1000_layered_search_replay`:
read-only source volume, separate research volume, per-cell SHA resume with
atomic publish, persistent cross-batch cost ledger and fail-closed STOP gates.
Zero training for p0/l1-a: frozen Stage-B signals are replayed under different
(TopK, DropN) combos only; L1-A additionally requires a verified P0 summary.

Foreground rule: when launched through OpenCode, NEVER pass ``--detach`` to
``modal run``. Keep the launcher attached with a tool timeout longer than the
worst-case queue + build + run + collect time. If the timeout cannot cover the
batch, shrink ``--batch-limit`` first or STOP. ``git checkout --detach`` is
unrelated and remains allowed. No ``nohup``, shell ``&`` or untracked
background mechanism is used anywhere in this file.

Status: code only, NOT EXECUTED. Real spend needs explicit USD30 approval,
and no billed run is claimed by merging or reviewing this file.
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
    load_frozen_profiles,
    replay_cache_key,
    require_p0_pass,
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
    keys = ("cpu_rate_per_second", "memory_rate_per_gib_second",
            "p90_cpu_seconds_per_cell", "p90_gib_seconds_per_cell", "overhead_usd")
    for key in keys:
        value = quote.get(key)
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError(f"STOP_COST_UNKNOWN: missing/nonfinite billing field {key}")
    if not quote["cpu_rate_per_second"] or not quote["memory_rate_per_gib_second"]:
        raise ValueError("STOP_COST_UNKNOWN: current Modal billing rates must be positive")
    if not isinstance(quote.get("covered_cells"), int) or quote["covered_cells"] < covered_cells:
        raise ValueError("STOP_COST_UNKNOWN: quote covers fewer cells than dispatched batch")
    if not isinstance(quote.get("rate_report_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", quote["rate_report_sha256"]):
        raise ValueError("STOP_COST_UNKNOWN: signed rate evidence SHA missing")
    estimated = (
        quote["covered_cells"] *
        (quote["cpu_rate_per_second"] * quote["p90_cpu_seconds_per_cell"]
         + quote["memory_rate_per_gib_second"] * quote["p90_gib_seconds_per_cell"])
        + quote["overhead_usd"]
    )
    if not math.isfinite(estimated) or estimated <= 0:
        raise ValueError("STOP_COST_UNKNOWN: estimated batch price is not positive")
    if estimated > estimated_usd + 1e-9:
        raise ValueError(f"STOP_BUDGET: current-rate P90 USD {estimated:.6f} > authorized batch ceiling {estimated_usd:.6f}")
    return {"source": path, "predicted_p90_usd": round(estimated, 6),
            "covered_cells": quote["covered_cells"], "captured_utc": quote["captured_utc"]}


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
            "numpy==1.26.4", "pandas==2.2.3", "pyarrow==17.0.0",
            "lightgbm==4.7.0", "cython", "setuptools-scm", "pyyaml",
            "ruamel.yaml", "fire", "mlflow", "dill", "filelock", "tqdm",
            "loguru", "joblib", "pydantic-settings", "redis",
            "python-redis-lock", "pymongo", "gym", "cvxpy", "matplotlib",
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
            if provider_meta.get("snapshot_token") != frozen_manifest.get("snapshot_token") or provider_meta.get("provider_fingerprint") != frozen_manifest.get("provider_fingerprint"):
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
            record["provider_snapshot_sha256"] = __import__("audit.csi1000_layered_search_core", fromlist=["sha256_file"]).sha256_file(provider_snapshot)
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
        cpu=1, memory=2048, timeout=900,
    )
    def inventory_frozen_sources_remote(prefix: str) -> Dict[str, Any]:
        """SHA-verify all 11x5 frozen signals before P0 portfolio selection."""
        from audit.csi1000_layered_search_replay import (
            load_frozen_result, resolve_phase_sources, sha_bytes,
        )
        from audit.csi1000_layered_search_core import sha256_file
        research.reload()
        frozen = load_frozen_result(REPO)
        info = frozen["manifest"]
        snapshot = SOURCE / "csi1000_stage_b" / "provider_snapshot.json"
        if not snapshot.is_file():
            raise ValueError("STOP_SOURCE_UNAVAILABLE: provider snapshot manifest absent")
        actual = json.loads(snapshot.read_text(encoding="utf-8"))
        if actual.get("snapshot_token") != info.get("snapshot_token") or actual.get("provider_fingerprint") != info.get("provider_fingerprint"):
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
                entries.append({
                    "candidate_id": cid,
                    "phase": phase,
                    "model_config_sha256": expected_model,
                    "signal_sha256": sources["signal_sha256"],
                    "report_sha256": sources["report_sha256"],
                    "decision_sha256": sources["decision_sha256"],
                    "n_retrain_chunks": len(chunks),
                    "chunk_prediction_sha256": [
                        chunk.get("prediction_content_sha256") for chunk in chunks
                    ],
                })
        if len(entries) != 55 or len({(e["candidate_id"],e["phase"]) for e in entries}) != 55:
            raise ValueError("STOP_P0: original frozen 11x5 source inventory incomplete")
        payload = {
            "status": "SOURCE_11x5_SHA_VERIFIED", "protocol": PROTOCOL_VERSION,
            "provider_fingerprint": actual["provider_fingerprint"],
            "provider_snapshot_sha256": sha256_file(snapshot),
            "snapshot_token": actual["snapshot_token"],
            "sources": entries,
        }
        target = RESULTS / prefix / "frozen_source_11x5.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
        if target.exists():
            if target.read_text(encoding="utf-8") != encoded:
                raise ValueError("STOP_P0: frozen source inventory conflict")
        else:
            target.write_text(encoded, encoding="utf-8")
            research.commit()
        return {"status": payload["status"], "count": len(entries), "manifest_sha256": sha256_file(target)}

    @app.function(volumes={str(RESULTS): research}, cpu=1, memory=1024, timeout=120)
    def inventory_completed_remote(prefix: str, stage: str) -> List[str]:
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
        return sorted(completed)

    @app.function(
        volumes={str(SOURCE): original.with_mount_options(read_only=True), str(RESULTS): research},
        cpu=2,
        memory=4096,
        timeout=900,
    )
    def finalize_remote(
        prefix: str, stage: str, outcomes: List[Dict[str, Any]], code_sha: str, run_id: str
    ) -> Dict[str, Any]:
        """Re-validate every outcome (never trust incoming PASS), then sign the summary."""
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
            if o.get("cell_key") not in expected_keys:
                blocked.append({"cell_key": o.get("cell_key"), "reason": "unexpected cell identity"})
                continue
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
        summary = {
            "protocol": PROTOCOL_VERSION,
            "stage": stage,
            "run_id": run_id,
            "code_sha": code_sha,
            "expected": len(expected_keys),
            "completed": len(verified),
            "blocked": len(blocked),
            "remaining": len(expected_keys) - len(verified) - len(blocked),
            "status": "PASS" if len(verified) == len(expected_keys) and not blocked
                      else ("BLOCKED" if blocked else "INCOMPLETE_BATCH"),
            "blocked_cells": blocked,
            "verified_cells": verified,
            "training_performed": False,
            "promotion_authorized": False,
            "source_volume_read_only": True,
        }
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
        protocol_bytes = json.dumps(build_protocol(run_id), indent=2, sort_keys=True) + "\n"
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
            "cells": [{
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
            } for item in verified],
        }
        manifest_path = (RESULTS / prefix / "source_manifest_final.json"
                         if summary["status"] == "PASS"
                         else RESULTS / prefix / f"source_manifest_partial_{__import__('uuid').uuid4().hex}.json")
        if manifest_path.exists():
            raise RuntimeError("STOP_P0: signed source manifest already exists; refusing overwrite")
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
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
    ) -> None:
        """Foreground Modal entrypoint: P0 controls, then gated L1-A screen.

        Execute attached, e.g. ``modal run audit/csi1000_layered_search_modal.py
        --stage p0 --run-id <id> --max-workers 2 --estimated-total-usd 2.0``.
        Never append ``--detach`` when launched through OpenCode. L1-A also
        needs ``--p0-summary <downloaded stage_p0_summary.json>`` proving 4/4
        verified P0 cells.
        """
        validate_for_dispatch(
            stage, run_id, max_workers, batch_limit, estimated_total_usd, resume_from_manifest, ledger, p0_summary
        )
        if stage == "p0" and max_workers > 2:
            raise ValueError("STOP_BUDGET: P0 must start with no more than two workers")
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        if subprocess.check_output(["git", "status", "--porcelain"], text=True).strip():
            raise ValueError("STOP: worktree must be clean to bind code SHA to the Modal image")
        prefix = result_prefix(run_id, stage, sha)
        estimate_n = batch_limit if batch_limit else len(enumerate_cells(stage))
        check_operator_cost_quote(cost_quote, float(estimated_total_usd), estimate_n)
        ledger_obj = BudgetLedger.load(ledger)
        gate = ledger_obj.preflight("p0" if stage == "p0" else "l1", float(estimated_total_usd))
        if gate != "OK_CONTINUE":
            raise ValueError(f"STOP: ledger preflight gate {gate}")
        ledger_obj.record_estimate(
            "p0" if stage == "p0" else "l1",
            float(estimated_total_usd), run_id=run_id,
            note="reserve before any billable remote inventory or replay",
        )
        ledger_obj.save(ledger)
        verified_roster = inventory_frozen_sources_remote.remote(prefix)
        if verified_roster.get("count") != 55:
            raise RuntimeError("STOP_P0: missing 11x5 source inventory")
        completed = set(inventory_completed_remote.remote(prefix, stage))
        planned = sorted(enumerate_cells(stage), key=lambda c: c["cell_key"])
        pending = [c for c in planned if c["cell_key"] not in completed]
        cells = pending[:batch_limit] if batch_limit else pending
        if not cells:
            raise ValueError("STOP: no unexecuted cells in this batch; request the existing verified summary")
        print(
            json.dumps(
                {
                    "stage": stage,
                    "cells": len(cells),
                    "max_workers": max_workers,
                    "estimated_total_usd": estimated_total_usd,
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
        outcomes = list(replay_cell_remote.with_options(max_containers=max_workers).map(build_jobs(cells, prefix)))
        summary = finalize_remote.remote(prefix, stage, outcomes, sha, run_id)
        _write_inflight_ledger(
            Path(ledger).expanduser().parent / TRIAL_LEDGER_NAME,
            stage,
            run_id,
            outcomes,
            note="in-flight: App ID + billed spend must be filled from the Modal run header/billing report before audit",
        )
        print(json.dumps(summary, indent=2), flush=True)
        if summary["blocked"]:
            raise RuntimeError("STOP_P0: one or more P0/L1 controls failed; not eligible for continuation")
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
        description="PR-S0 Modal runner: P0 4-cell controls + L1-A small portfolio screen (zero-fit, read-only source).",
        epilog="OpenCode foreground rule: run attached without --detach; set the tool timeout beyond worst-case wall time.",
    )
    p.add_argument("--stage", default="p0", help="p0 | l1-a | inventory | dry-run (l1-b/l1-c/l2/l3/l4 need later PRs).")
    p.add_argument("--run-id", default="", help="Pre-registered run id [A-Za-z0-9][A-Za-z0-9_-]{3,63}.")
    p.add_argument("--max-workers", type=int, default=2, help="Parallel workers, 1..8 (PR-S0 cap).")
    p.add_argument("--batch-limit", type=int, default=0, help="Max cells this batch; 0 means all cells of the stage.")
    p.add_argument("--estimated-total-usd", type=float, default=-1.0, help="REQUIRED provisional P90 ceiling in USD.")
    p.add_argument("--resume-from-manifest", default="", help="Existing source_manifest.json for hash-checked resume.")
    p.add_argument("--ledger", default="~/.local/state/qlib-csi1000-layered/cost_ledger.json", help="Persisted cross-batch ledger outside the checkout.")
    p.add_argument("--p0-summary", default="", help="Downloaded stage_p0_summary.json; required for --stage l1-a.")
    p.add_argument("--cost-quote", default="", help="Required current-rate P90 operator quote JSON for remote execution.")
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


def validate_for_dispatch(
    stage: str,
    run_id: str,
    max_workers: int,
    batch_limit: int,
    estimated_total_usd: float,
    resume_from_manifest: str,
    ledger: str,
    p0_summary: str = "",
) -> List[Dict[str, Any]]:
    """Fail-closed validation shared by the local CLI and the Modal entrypoint."""
    if stage not in ("p0", "l1-a"):
        raise ValueError(f"STOP: stage {stage} needs a later reviewed PR (PR-S0 covers p0/l1-a only)")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{3,63}", run_id or ""):
        raise ValueError("STOP: --run-id must match [A-Za-z0-9][A-Za-z0-9_-]{3,63}")
    if not isinstance(max_workers, int) or not 1 <= max_workers <= 8:
        raise ValueError("STOP: --max-workers must be 1..8 in PR-S0")
    import math as _math

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
    if stage == "l1-a":
        if not p0_summary:
            raise ValueError("STOP_P0: l1-a requires --p0-summary with a verified 4/4 P0 summary")
        try:
            summary = json.loads(Path(p0_summary).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"STOP_P0: cannot read P0 summary: {exc}") from exc
        try:
            require_p0_pass(summary, summary.get("verified_cells"))
            # A JSON-only PASS is not evidence. Require the complete exported
            # research prefix with its original relative paths and checksum index.
            from audit.csi1000_layered_search_core import sha256_file
            from audit.csi1000_layered_search_reconcile import verify_sha256sums
            evidence = Path(p0_summary).resolve().parents[2]
            verify_sha256sums(evidence)
            manifest = json.loads((evidence / "source_manifest_final.json").read_text(encoding="utf-8"))
            expected_cells = {c["cell_key"] for c in enumerate_cells("p0")}
            if {x.get("cell_key") for x in manifest.get("cells", [])} != expected_cells:
                raise ValueError("source manifest P0 roster differs from expected four")
            for o in summary["verified_cells"]:
                directory = evidence / "cells" / o["cell_key"]
                persisted = json.loads((directory / "cell.json").read_text(encoding="utf-8"))
                for key in ("cache_key", "source_sha256", "output_sha256", "provider_snapshot_sha256"):
                    if persisted.get(key) != o.get(key):
                        raise ValueError(f"P0 exported cell differs from summary: {key}")
                names = ("daily_nav.csv", "fixed_report.parquet", "fixed_decisions.json", "metrics.json")
                recorded_hashes = persisted.get("artifact_sha256")
                if not isinstance(recorded_hashes, dict) or set(recorded_hashes) != set(names):
                    raise ValueError("P0 exported artifact manifest incomplete")
                for name in names:
                    if sha256_file(directory / name) != recorded_hashes[name]:
                        raise ValueError(f"P0 exported {name} hash differs")
        except (ValueError, FileNotFoundError, KeyError, OSError) as exc:
            raise ValueError(f"STOP_P0: complete signed P0 research export required: {exc}") from exc
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
