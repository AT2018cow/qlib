"""CSI1000 L4 signed five-phase research replay, zero new LightGBM fit.

This app reuses the *actual* frozen Stage-B 3 models × 5 freq20 phases.
Every Stage-B report/signal/decision source is verified by byte SHA before
any new F1 replay. 4 strategies × 5 phases require 20 new strategy reports
but ZERO new training fits. Production Winner is never touched.

The local coordinator must remain attached (no --detach or background);
a separate v2 Volume stores L4 results, existing source/P0 are read-only.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from audit.csi1000_l4_core import (
    FROZEN_MANIFEST_SHA, FINAL_PHASES, BASELINE_CONFIG_SHA,
    check_stage_b_source_coverage, frozen_l4_manifest, l4_cell_specs,
    l4_decision_from_receipts,
)
from audit.csi1000_layered_search_core import (
    BASELINE_CANDIDATE, RESEARCH_VOLUME, SOURCE_VOLUME, enumerate_cells,
    require, require_p0_pass, sha256_file, stable_sha, verify_export_cells,
)
from audit.csi1000_layered_search_replay import (
    load_frozen_result, lookup_phase, resolve_phase_sources,
)

try:
    import modal
except ImportError:
    modal = None

SOURCE = Path("/source")
P0_RESULTS = Path("/p0-evidence")
RESULTS = Path("/results")
REPO = Path("/root/qlib")
L4_VOLUME_V2 = "qlib-csi1000-l4-v2"
L4_OUTPUT_FILES = ("fixed_report.parquet", "decisions.json", "signal.parquet")
EXPECTED_P0_PREFIX = (
    "csi1000_layered_search/csi1000_layered_search_v1/"
    "p0_20261013_c1/p0/cffb9cd56d52"
)


def l4_prefix_for(run_id: str, code_sha: str) -> str:
    require(bool(re.fullmatch(r"l4_[A-Za-z0-9_]{4,50}", run_id)), "STOP_L4_INVALID_RUN_ID")
    require(bool(re.fullmatch(r"[a-f0-9]{40}", code_sha)), "STOP_L4_FULL_GIT_SHA_REQUIRED")
    return f"csi1000_layered_search/csi1000_layered_search_v1/{run_id}/l4/{code_sha[:12]}"


def l4_cell_dir(root: Path, job: dict) -> Path:
    require(job["manifest_sha256"] == FROZEN_MANIFEST_SHA, "STOP_L4_MANIFEST_DRIFT")
    require(job["config_sha256"] in (
        BASELINE_CONFIG_SHA, *(
            "1ca6fae56e8c4e40c73b1a45866825930aecba36ec529544a50da5408c93d317",
            "95ae81ba885dad4e6eb31838e0bbea55d9281807289efe9ce150cbc12edc74b6",
            "3ddff2cd6138f0adf003ff6d899a496ed2393ba26dddccc6ce7af274532535e0",
        )
    ), "STOP_L4_FOREIGN_CONFIGURATION")
    require(job["phase"] in FINAL_PHASES, "STOP_L4_FOREIGN_PHASE")
    return root / "cells" / job["config_sha256"] / f"phase{job['phase']:02d}"


def checked_l4_cell(root: Path, job: dict, identity: dict) -> dict | None:
    directory = l4_cell_dir(root, job)
    if not directory.exists():
        return None
    record_path = directory / "receipt.json"
    require(record_path.is_file(), "STOP_L4_PARTIAL_CELL")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    for name, expected in identity.items():
        require(record.get(name) == expected, f"STOP_L4_CELL_IDENTITY_{name}")
    require(record.get("job_sha256") == stable_sha(job)
            and record.get("status") == "PASS", "STOP_L4_JOB_SHA_OR_PASS")
    require(record.get("config_sha256") == job["config_sha256"]
            and record.get("phase") == job["phase"]
            and record.get("candidate_id") == job["candidate_id"]
            and record.get("topk") == job["topk"] and record.get("dropn") == job["dropn"],
            "STOP_L4_CELL_SPEC_DRIFT")
    hashes = record.get("artifact_sha256")
    require(isinstance(hashes, dict) and set(hashes) == set(L4_OUTPUT_FILES), "STOP_L4_ARTIFACT_SET")
    for name in L4_OUTPUT_FILES:
        target = directory / name
        require(target.is_file() and sha256_file(target) == hashes[name],
                f"STOP_L4_ARTIFACT_BYTES_{name}")
    return record


def publish_l4_cell(root: Path, job: dict, identity: dict, artifacts: dict, details: dict) -> dict:
    """Publish only fully written and signed artifacts; do not replace cache entries."""
    import pandas as pd

    directory = l4_cell_dir(root, job)
    require(set(artifacts) == set(L4_OUTPUT_FILES), "STOP_L4_ARTIFACT_SET")
    protected = set(identity) | {
        "job_sha256", "status", "artifact_sha256", "config_sha256",
        "candidate_id", "topk", "dropn", "phase",
    }
    require(not protected.intersection(details), "STOP_L4_SIGNED_FIELD_OVERRIDE")
    require(not directory.exists(), "STOP_L4_EXISTING_CELL_NO_OVERWRITE")
    temporary = directory.parent / (directory.name + ".tmp")
    require(not temporary.exists(), "STOP_L4_PARTIAL_TEMP_RECEIPT")
    temporary.mkdir(parents=True, exist_ok=False)
    try:
        for name, value in artifacts.items():
            path = temporary / name
            if isinstance(value, pd.DataFrame):
                value.to_parquet(path)
            elif isinstance(value, list):
                path.write_text(json.dumps(value, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
            else:
                require(False, f"STOP_L4_UNKNOWN_ARTIFACT_{name}")
        hashes = {name: sha256_file(temporary / name) for name in sorted(artifacts)}
        record = {
            **identity, "status": "PASS", "job_sha256": stable_sha(job),
            **{k: job[k] for k in ("candidate_id", "topk", "dropn", "config_sha256", "phase")},
            **details, "artifact_sha256": hashes,
        }
        (temporary / "receipt.json").write_text(
            json.dumps(record, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        os.replace(temporary, directory)
        return record
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def verify_reference_report(current, expected) -> None:
    """Daily P0 baseline equality on actual accounting fields, not headline CAGR."""
    import numpy as np

    require(current.index.equals(expected.index) and len(current) == 424,
            "STOP_L4_P0_DAILY_INDEX")
    for name in ("bench", "return", "cost", "account", "total_cost"):
        require(name in current and name in expected, f"STOP_L4_P0_MISSING_{name}")
        require(np.isfinite(current[name].to_numpy(dtype=float)).all()
                and np.isfinite(expected[name].to_numpy(dtype=float)).all(),
                "STOP_L4_P0_NONFINITE")
        atol = 0.01 if name in ("account", "total_cost") else 1e-10
        require(np.allclose(current[name].to_numpy(dtype=float),
                            expected[name].to_numpy(dtype=float), rtol=0.0, atol=atol),
                f"STOP_L4_P0_REPLAY_DIFF_{name}")


if modal is not None:
    original = modal.Volume.from_name(SOURCE_VOLUME, create_if_missing=False)
    p0_archive = modal.Volume.from_name(RESEARCH_VOLUME, create_if_missing=False)
    research = modal.Volume.from_name(L4_VOLUME_V2, create_if_missing=False, version=2)
    image = (
        modal.Image.debian_slim(python_version="3.11")
        .apt_install("build-essential")
        .env({"PYTHONPATH": "/root/qlib:/root", "MLFLOW_ALLOW_FILE_STORE": "true"})
        .pip_install(
            "numpy==1.26.4", "pandas==2.2.3", "pyarrow==17.0.0",
            "lightgbm==4.7.0", "cython", "setuptools-scm", "pyyaml",
            "ruamel.yaml", "fire", "mlflow", "dill", "filelock", "tqdm",
            "loguru", "joblib", "pydantic-settings", "redis",
            "python-redis-lock", "pymongo", "gym", "cvxpy", "matplotlib", "torch",
        )
        .add_local_dir(
            ".", remote_path=str(REPO), copy=True,
            ignore=lambda p: (
                str(p).endswith((".cpp", ".so"))
                or any(t in str(p) for t in (".venv", "mlruns", "__pycache__", ".git"))
            ),
        )
        .run_commands(
            "printf 'version = \"0.0.0\"\\n' > /root/qlib/qlib/_version.py",
            "cd /tmp && python -c \"from setuptools import setup, Extension; from Cython.Build import cythonize; import numpy; root='/root/qlib'; extensions=[Extension('qlib.data._libs.rolling', [root+'/qlib/data/_libs/rolling.pyx'], language='c++', include_dirs=[numpy.get_include()]), Extension('qlib.data._libs.expanding', [root+'/qlib/data/_libs/expanding.pyx'], language='c++', include_dirs=[numpy.get_include()])]; setup(name='qlib-image-cython-only', version='0.0.0', ext_modules=cythonize(extensions, language_level=3), script_args=['build_ext', '--build-lib', root, '--build-temp', '/tmp/qlib-image-build'])\"",
            "cd /tmp && python -c \"import qlib; from qlib.data._libs import rolling, expanding; assert qlib.__version__ == '0.0.0'\"",
        )
    )
    app = modal.App("qlib-csi1000-l4-five-phase", image=image)
    mounts = {
        str(SOURCE): original.with_mount_options(read_only=True),
        str(P0_RESULTS): p0_archive.with_mount_options(read_only=True),
        str(RESULTS): research,
    }

    def _root(job: dict) -> Path:
        return RESULTS / l4_prefix_for(job["run_id"], job["code_sha"])

    def _identity(job: dict) -> dict:
        return {k: job[k] for k in (
            "run_id", "code_sha", "manifest_sha256", "source_digest",
            "provider_fingerprint", "source_snapshot_sha256",
            "quote_sha256", "p0_prefix",
        )}

    def _source_checks() -> dict:
        frozen = load_frozen_result(REPO)["manifest"]
        path = SOURCE / "csi1000_stage_b" / "provider_snapshot.json"
        require(path.is_file(), "STOP_L4_STAGE_B_PROVIDER_MANIFEST_MISSING")
        data = json.loads(path.read_text(encoding="utf-8"))
        require(data.get("snapshot_token") == frozen["snapshot_token"]
                and data.get("provider_fingerprint") == frozen["provider_fingerprint"],
                "STOP_L4_FROZEN_PROVIDER_DRIFT")
        return {"snapshot_token": data["snapshot_token"],
                "provider_fingerprint": data["provider_fingerprint"],
                "source_snapshot_sha256": sha256_file(path)}

    def _verified_original_p0(phase: int, meta: dict) -> tuple[dict, Path]:
        require(phase in (0, 10), "STOP_L4_P0_REFERENCE_PHASE")
        specs = {
            cell["phase"]: cell for cell in enumerate_cells("p0")
            if cell["candidate_id"] == BASELINE_CANDIDATE
        }
        require(set(specs) == {0, 10}, "STOP_L4_P0_CONTROL_PHASES")
        directory = P0_RESULTS / meta["p0_prefix"] / "cells" / specs[phase]["cell_key"]
        path = directory / "cell.json"
        require(path.is_file(), "STOP_L4_ORIGINAL_P0_NOT_FOUND")
        row = json.loads(path.read_text(encoding="utf-8"))
        require(row.get("status") == "PASS" and row.get("candidate_id") == BASELINE_CANDIDATE
                and row.get("phase") == phase
                and row.get("topk") == 20 and row.get("dropn") == 2
                and row.get("provider_fingerprint") == meta["provider_fingerprint"]
                and row.get("quote_sha256") == meta["quote_sha256"],
                "STOP_L4_ORIGINAL_P0_IDENTITY")
        hashes = row.get("artifact_sha256") or {}
        require("fixed_report.parquet" in hashes, "STOP_L4_P0_REPORT_HASH_MISSING")
        for name, expected in hashes.items():
            require((directory / name).is_file()
                    and sha256_file(directory / name) == expected,
                    f"STOP_L4_P0_ARTIFACT_DRIFT_{name}")
        return row, directory

    @app.function(volumes=mounts, cpu=2, memory=4096, timeout=1800, retries=0)
    def inventory_remote(meta: dict) -> dict:
        """Preflight ALL 15 full five-phase Stage-B sources, not only Smoke's 2."""
        import qlib
        from qlib.data import D
        from audit.csi1000_layered_search_replay import static_quote_union
        from csi1000_stage_b_core import MARKET, RESERVED_EXECUTION_START, RESERVED_EXECUTION_END

        manifest = frozen_l4_manifest(REPO)
        frozen = load_frozen_result(REPO)
        src = _source_checks()
        require(src["provider_fingerprint"] == meta["provider_fingerprint"],
                "STOP_L4_SOURCE_PROVIDER")
        coverage = check_stage_b_source_coverage(frozen, manifest)
        require(coverage["source_digest"] == meta["source_digest"], "STOP_L4_SOURCE_MANIFEST_SHA")
        qlib.init(provider_uri=str(SOURCE / "cn_data"), region="cn")
        codes, quote_sha = static_quote_union(MARKET, RESERVED_EXECUTION_START, RESERVED_EXECUTION_END, D)
        require(quote_sha == meta["quote_sha256"] and codes, "STOP_L4_QUOTE_SHA")
        root = SOURCE / "csi1000_stage_b" / src["snapshot_token"]
        found = []
        for pair in coverage["sources"]:
            row = lookup_phase(frozen, pair["candidate_id"], pair["phase"])
            checked = resolve_phase_sources(root, row, pair["candidate_id"], pair["phase"])
            require(checked["signal_sha256"] == pair["frozen_signal_sha256"],
                    "STOP_L4_SOURCE_SIGNAL_SHA")
            found.append([pair["candidate_id"], pair["phase"], checked["signal_sha256"]])
        p0_archive.reload()
        for phase in (0, 10):
            ref, _ = _verified_original_p0(phase, meta)
            baseline_source = lookup_phase(frozen, BASELINE_CANDIDATE, phase)["signal_artifact"]["sha256"]
            require(ref["signal_sha256"] == baseline_source,
                    "STOP_L4_P0_FROZEN_SIGNAL_DIFFERENT")
        return {"status": "PASS", "source_model_phases": len(found),
                "source_verified_digest": stable_sha(found),
                "quote_sha256": quote_sha,
                "no_new_model_fit": True}

    @app.function(volumes=mounts, cpu=2, memory=4096, timeout=3600, retries=0)
    def replay_remote(job: dict) -> dict:
        """One F1 TopK/DropN report using SHA-verified existing Stage-B signal."""
        import pandas as pd
        import qlib
        from qlib.data import D
        from csi1000_stage_b_core import MARKET, RESERVED_EXECUTION_START, RESERVED_EXECUTION_END
        from audit.csi1000_layered_search_replay import static_quote_union, backtest_signal, report_metrics

        research.reload()
        identity = _identity(job)
        root = _root(job)
        already = checked_l4_cell(root, job, identity)
        if already is not None:
            return {**already, "resumed": True}
        require(frozen_l4_manifest(REPO)["finalist_manifest_sha256"] == job["manifest_sha256"],
                "STOP_L4_FROZEN_PLAN_MISMATCH")
        source = _source_checks()
        require(source["provider_fingerprint"] == identity["provider_fingerprint"]
                and source["source_snapshot_sha256"] == identity["source_snapshot_sha256"],
                "STOP_L4_PROVIDER_OR_SNAPSHOT")
        frozen = load_frozen_result(REPO)
        row = lookup_phase(frozen, job["candidate_id"], job["phase"])
        stage_b_root = SOURCE / "csi1000_stage_b" / source["snapshot_token"]
        refs = resolve_phase_sources(stage_b_root, row, job["candidate_id"], job["phase"])
        require(row["signal_artifact"]["sha256"] == job["expected_signal_sha256"],
                "STOP_L4_SOURCE_SIGNAL_DRIFT")
        assert refs["signal_sha256"] == job["expected_signal_sha256"]
        signal_frame = pd.read_parquet(refs["signal"])
        require(list(signal_frame.columns) == ["score"] and signal_frame.index.is_unique,
                "STOP_L4_FROZEN_SIGNAL_SCHEMA")
        qlib.init(provider_uri=str(SOURCE / "cn_data"), region="cn")
        codes, quote_sha = static_quote_union(MARKET, RESERVED_EXECUTION_START, RESERVED_EXECUTION_END, D)
        require(codes and quote_sha == identity["quote_sha256"], "STOP_L4_F1_QUOTE_CHANGED")
        report, decisions = backtest_signal(
            signal_frame["score"], job["topk"], job["dropn"], codes,
            RESERVED_EXECUTION_START, RESERVED_EXECUTION_END,
        )
        metrics, diag = report_metrics(report)
        require(len(report) == 424 and report.index.is_unique and len(decisions) == 424,
                "STOP_L4_424_DAY_REPORT")
        require(diag["max_account_return_delta"] <= 1e-10
                and diag["max_cost_rate_delta"] <= 1e-10
                and diag["max_value_plus_cash_discrepancy"] <= 0.01,
                "STOP_L4_F1_NAV_ACCOUNT_DRIFT")
        if job["config_sha256"] == BASELINE_CONFIG_SHA and job["phase"] in (0, 10):
            p0_archive.reload()
            original_row, directory = _verified_original_p0(job["phase"], job)
            require(original_row["signal_sha256"] == refs["signal_sha256"],
                    "STOP_L4_P0_SIGNAL_NOT_IDENTICAL")
            verify_reference_report(report, pd.read_parquet(directory / "fixed_report.parquet"))
        details = {
            "metrics": metrics, "n_days": metrics["n_days"],
            "mean_turnover": metrics["mean_turnover"],
            "coverage_ok": True, "ledger_ok": True, "data_ok": True,
            "training_performed": False,
            "source_signal_sha256": refs["signal_sha256"],
            "source_report_sha256": refs["report_sha256"],
            "source_decision_sha256": refs["decision_sha256"],
            "source_model_config_sha256": frozen["manifest"]["candidate_model_config_sha256"][job["candidate_id"]],
            "quote_path": "static_quote_union_f1",
            "historic_data": "HISTORICALLY_CONSUMED",
            "promotion_authorized": False,
        }
        receipt = publish_l4_cell(
            root, job, identity,
            {"fixed_report.parquet": report, "decisions.json": decisions,
             "signal.parquet": signal_frame},
            details,
        )
        research.commit()
        return receipt

    @app.function(volumes={str(RESULTS): research}, cpu=1, memory=1024, timeout=600, retries=0)
    def inspect_remote(jobs: list[dict]) -> list[dict | None]:
        research.reload()
        return [checked_l4_cell(_root(j), j, _identity(j)) for j in jobs]

    @app.function(volumes={str(RESULTS): research}, cpu=1, memory=1024, timeout=600, retries=0)
    def freeze_remote(meta: dict, key: str, payload: dict) -> dict:
        """Immutable marker, no modification after verification."""
        require(key in ("smoke_pass", "l4_final_decision"), "STOP_L4_MARKER_KIND")
        research.reload()
        root = _root(meta)
        root.mkdir(parents=True, exist_ok=True)
        path = root / (key + ".json")
        data = {**_identity(meta), **payload}
        protected = set(_identity(meta))
        require(not (protected & set(payload)), "STOP_L4_MARKER_IDENTITY_OVERRIDE")
        if path.exists():
            require(json.loads(path.read_text(encoding="utf-8")) == data, "STOP_L4_MARKER_CONFLICT")
        else:
            tmp = path.with_suffix(".tmp")
            require(not tmp.exists(), "STOP_L4_PARTIAL_MARKER")
            tmp.write_text(json.dumps(data, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
            tmp.replace(path)
            research.commit()
        return data

    @app.function(volumes={str(RESULTS): research}, cpu=1, memory=1024, timeout=600, retries=0)
    def marker_remote(meta: dict, key: str) -> dict | None:
        require(key in ("smoke_pass", "l4_final_decision"), "STOP_L4_MARKER_KIND")
        research.reload()
        path = _root(meta) / (key + ".json")
        if not path.is_file():
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
        for name, value in _identity(meta).items():
            require(record.get(name) == value, f"STOP_L4_MARKER_{name}")
        return record

    @app.function(
        volumes={
            str(RESULTS): research,
            str(P0_RESULTS): p0_archive.with_mount_options(read_only=True),
        }, cpu=2, memory=4096, timeout=3600, retries=0,
    )
    def final_remote(meta: dict, jobs: list[dict]) -> dict:
        """Independently reload 20 exact bytes, benchmark and all product gates."""
        import pandas as pd

        research.reload()
        p0_archive.reload()
        manifest = frozen_l4_manifest(REPO)
        require(set(stable_sha(j) for j in jobs) ==
                set(stable_sha(j) for j in _jobs_for_run(meta, manifest)) and len(jobs) == 20,
                "STOP_L4_JOB_LIST_NOT_FROZEN")
        rows = []
        for job in jobs:
            rec = checked_l4_cell(_root(job), job, _identity(job))
            require(rec is not None, "STOP_L4_MISSING_FINAL_REPORT")
            rows.append(rec)
        for phase in (0, 10):
            baseline_job = next(j for j in jobs if j["config_sha256"] == BASELINE_CONFIG_SHA
                                and j["phase"] == phase)
            record, directory = _verified_original_p0(phase, meta)
            require(record["signal_sha256"] ==
                    next(r for r in rows if r["config_sha256"] == BASELINE_CONFIG_SHA
                         and r["phase"] == phase)["source_signal_sha256"],
                    "STOP_L4_P0_SOURCE_DRIFT")
            current = pd.read_parquet(l4_cell_dir(_root(baseline_job), baseline_job)
                                      / "fixed_report.parquet")
            verify_reference_report(current, pd.read_parquet(directory / "fixed_report.parquet"))

        def loader(record):
            job = next(j for j in jobs if j["config_sha256"] == record["config_sha256"]
                       and j["phase"] == record["phase"])
            return pd.read_parquet(l4_cell_dir(_root(job), job) / "fixed_report.parquet")

        result = l4_decision_from_receipts(manifest, rows, loader)
        result["receipt_shas"] = {
            f"{r['config_sha256']}:phase{r['phase']}": stable_sha(r)
            for r in rows
        }
        result["source_model_phases"] = 15
        result["real_new_model_fits"] = 0
        result["verified_original_p0_phases"] = [0, 10]
        return freeze_remote.local(meta, "l4_final_decision", result)

    def _actual_sha() -> str:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        require(not subprocess.check_output(["git", "status", "--porcelain"], text=True).strip(),
                "STOP_L4_UNCLEAN_TREE")
        require(re.fullmatch(r"[a-f0-9]{40}", sha) is not None, "STOP_L4_INVALID_HEAD")
        return sha

    def _jobs_for_run(meta: dict, manifest: dict) -> list[dict]:
        frozen = load_frozen_result(REPO)
        result = []
        for cell in l4_cell_specs(manifest):
            stage_b = lookup_phase(frozen, cell["candidate_id"], cell["phase"])
            result.append({
                **meta, **cell,
                "expected_signal_sha256": stage_b["signal_artifact"]["sha256"],
            })
        return result

    def _dispatch(jobs: list[dict], workers: int, remaining: list[int] | None) -> bool:
        """True one-coordinator wave; no repeat of previously signed cells."""
        width = min(workers, 80)
        for start in range(0, len(jobs), width):
            wave = jobs[start:start + width]
            already = inspect_remote.remote(wave)
            todo = [j for j, row in zip(wave, already) if row is None]
            if not todo:
                continue
            if remaining is not None and remaining[0] == 0:
                print("L4_INCOMPLETE_SAFE_PAUSE: verified reports preserved; rerun same complete command", flush=True)
                return False
            print(f"L4_F1_WAVE {start // width+1}: new_cells={len(todo)} "
                  f"workers={workers} no_new_model_fit=True", flush=True)
            answers = list(replay_remote.with_options(max_containers=workers).map(todo))
            require(len(answers) == len(todo) and all(x["status"] == "PASS" for x in answers),
                    "STOP_L4_REPLAY_CELL_FAILURE")
            verified = inspect_remote.remote(todo)
            require(all(x is not None for x in verified), "STOP_L4_REPLAY_SHA_NOT_DURABLE")
            if remaining is not None:
                remaining[0] -= 1
        return True

    @app.local_entrypoint()
    def run_l4(
        mode: str = "smoke",
        run_id: str = "",
        p0_summary: str = "",
        p0_prefix: str = "",
        max_workers: int = 1,
        max_cell_waves: int = 0,
    ) -> None:
        """Two real F1 replays Smoke; one foreground auto-complete, zero fit."""
        require(mode in ("smoke", "complete"), "STOP_L4_MODE")
        require(1 <= max_workers <= 80, "STOP_L4_MAX_80_WORKERS")
        require((mode == "smoke" and max_workers == 1) or mode == "complete",
                "STOP_L4_SMOKE_ONE_WORKER_ONLY")
        require(isinstance(max_cell_waves, int) and 0 <= max_cell_waves <= 100,
                "STOP_L4_WAVE_LIMIT")
        require(mode != "smoke" or max_cell_waves == 0, "STOP_L4_SMOKE_MUST_COMPLETE")
        require(p0_prefix == EXPECTED_P0_PREFIX, "STOP_L4_P0_PREFIX_NOT_FROZEN")
        sha = _actual_sha()
        l4_prefix_for(run_id, sha)
        from audit.csi1000_layered_search_reconcile import verify_sha256sums
        path = Path(p0_summary).resolve()
        p0 = json.loads(path.read_text(encoding="utf-8"))
        require_p0_pass(p0, p0.get("verified_cells"))
        require(p0_prefix == f"csi1000_layered_search/csi1000_layered_search_v1/"
                f"{p0['run_id']}/p0/{p0['code_sha'][:12]}",
                "STOP_L4_P0_PREFIX_VS_EXPORT")
        export = path.parents[2]
        require((export / "SHA256SUMS.txt").is_file(), "STOP_L4_SIGNED_P0_SHA256SUMS_REQUIRED")
        verify_sha256sums(export)
        verify_export_cells(export, {c["cell_key"] for c in enumerate_cells("p0")}, "P0")
        fingerprint = {r["provider_fingerprint"] for r in p0["verified_cells"]}
        require(len(fingerprint) == 1, "STOP_L4_P0_PROVIDER_AMBIGUOUS")
        manifest = frozen_l4_manifest(".")
        stage_b = load_frozen_result(".")
        coverage = check_stage_b_source_coverage(stage_b, manifest)
        meta = {
            "run_id": run_id, "code_sha": sha,
            "manifest_sha256": FROZEN_MANIFEST_SHA,
            "source_digest": coverage["source_digest"],
            "provider_fingerprint": next(iter(fingerprint)),
            "source_snapshot_sha256": sha256_file(SOURCE / "csi1000_stage_b" / "provider_snapshot.json")
                if SOURCE.exists() else "",  # resolved remotely below, not trusted locally
            "quote_sha256": "",
            "p0_prefix": p0_prefix,
        }
        # Remote inventory independently derives live provider snapshot and quotes.
        live = inventory_remote.remote({**meta, "quote_sha256": "", "source_snapshot_sha256": ""})
        require(live["status"] == "PASS" and live["source_model_phases"] == 15,
                "STOP_L4_15_SOURCE_PREFLIGHT")
        meta["source_snapshot_sha256"] = live["source_snapshot_sha256"]
        meta["quote_sha256"] = live["quote_sha256"]
        jobs = _jobs_for_run(meta, manifest)
        baseline0 = next(j for j in jobs if j["config_sha256"] == BASELINE_CONFIG_SHA and j["phase"] == 0)
        champion0 = next(j for j in jobs if j["config_sha256"] != BASELINE_CONFIG_SHA and j["phase"] == 0)
        if mode == "smoke":
            require(marker_remote.remote(meta, "smoke_pass") is None, "STOP_L4_SMOKE_ALREADY_PASSED")
            require(_dispatch([baseline0, champion0], 1, None), "STOP_L4_SMOKE_REPLAY")
            require(all(r is not None for r in inspect_remote.remote([baseline0, champion0])),
                    "STOP_L4_SMOKE_BYTE_SHA")
            freeze_remote.remote(meta, "smoke_pass", {
                "status": "PASS", "replay_cell_shas": [stable_sha(baseline0), stable_sha(champion0)],
                "new_fit_count": 0, "f1_replayed_cells": 2,
            })
            print("L4_SMOKE_PASS 2/2 real F1 replays / 0 new LightGBM fits", flush=True)
            return
        smoke = marker_remote.remote(meta, "smoke_pass")
        require(smoke is not None and smoke["status"] == "PASS"
                and smoke["replay_cell_shas"] == [stable_sha(baseline0), stable_sha(champion0)],
                "STOP_L4_SMOKE_IDENTITY")
        require(all(r is not None for r in inspect_remote.remote([baseline0, champion0])),
                "STOP_L4_SMOKE_RESULTS_MISSING")
        if not _dispatch(jobs, max_workers, [max_cell_waves] if max_cell_waves else None):
            return
        result = final_remote.remote(meta, jobs)
        require(result["status"] == "L4_RESEARCH_COMPLETE_NOT_RELEASED"
                and result["completed_cells"] == 20 and result["real_new_model_fits"] == 0,
                "STOP_L4_TERMINAL_20_CELL_REVIEW")
        print(f"L4_COMPLETE 20/20 verified replay cells 0 new fits "
              f"decision={result['decision']} promotion_authorized=False", flush=True)
        print("L4_DONE", flush=True)
else:
    app = None
