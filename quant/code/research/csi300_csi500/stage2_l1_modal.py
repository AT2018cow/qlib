"""Stage 2 (L1) Modal runner: 384 zero-training TopK/DropN replay cells.

This stage trains NOTHING: every ``l1cell`` reuses a frozen, SHA-verified
424-day prediction signal from Stage 0/1, so this file deliberately contains
no model-fitting call of any kind. The source Volume is mounted read-only and
artifacts go to research Volume v2. The coordinator stays attached in the
foreground: detaching, nohup or shell backgrounding are all forbidden.

Key contracts:
1. ``inventory_remote`` re-verifies the provider snapshot, calendar, both
   static quote unions, both benchmarks and all 48 source ``signal.parquet``
   files (2 markets x 12 models x 2 phases) against the SHA256 sealed in each
   source cell's ``receipt.json``.
2. ``l1_cell_replay_remote`` never copies the signal: the receipt pins
   ``source_signal_sha256`` / ``source_signal_relpath`` instead
   (``STAGE2_ARTIFACTS = {"l1cell": ("fixed_report.parquet", "decisions.json")}``).
3. ``export_summary_remote`` merges 384 new l1cells with the 96 verified prior
   cells into 480 cells = per market 120 ranked strategies + Top 4 diverse
   finalists.
4. ``--pull-root`` triggers the plan-mandated 16-thread concurrent artifact
   download (``ThreadPoolExecutor(max_workers=16)``); no serial download loop.

Trap compliance (carried over from Stage 0/1):
1. Image ignore list excludes .git/.worktrees/.venv/mlruns/__pycache__/
   quant/local_backup/quant/data plus .so/.cpp build artifacts.
2. Image PYTHONPATH covers /root/qlib + quant/code + execution + production +
   stage_ab + layered_search + csi300_csi500 (plan-exact string).
3. Replay uses stage0_p0_core.backtest_market_signal with an explicit
   benchmark (csi300 -> SH000300, csi500 -> SH000905).
4. No training config is ever constructed here (zero fits by construction).
5. Local entrypoint runs attached; operator tees stdout to a log file.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path


def _find_repo_root() -> Path:
    """Locate the repo root in both layouts (deep local vs flat /root copy)."""
    here = Path(__file__).resolve()
    for candidate in (here.parent, *here.parents):
        if (candidate / "qlib" / "__init__.py").is_file() and (candidate / "quant" / "code").is_dir():
            return candidate
    fallback = Path("/root/qlib")
    if (fallback / "qlib" / "__init__.py").is_file():
        return fallback
    raise ValueError("STAGE2_L1_NOREPO: cannot locate repository root")


_REPO = _find_repo_root()
for _entry in (
    str(_REPO),
    str(_REPO / "quant" / "code"),
    str(_REPO / "quant" / "code" / "execution"),
    str(_REPO / "quant" / "code" / "production"),
    str(_REPO / "quant" / "code" / "research" / "stage_ab"),
    str(_REPO / "quant" / "code" / "research" / "layered_search"),
    str(_REPO / "quant" / "code" / "research" / "csi300_csi500"),
    str(_REPO / "quant" / "code" / "audit"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from stage2_l1_core import (  # noqa: E402
    ALL_12_MODELS,
    EXPECTED_CALENDAR_DAYS,
    EXPECTED_CALENDAR_END,
    EXPECTED_PROVIDER_FINGERPRINT,
    EXPECTED_SNAPSHOT_TOKEN,
    MARKETS,
    N_EXECUTION_DAYS,
    PHASES,
    RESEARCH_VOLUME,
    RESERVED_EXECUTION_END,
    RESERVED_EXECUTION_START,
    RESERVED_SIGNAL_ANCHOR,
    SOURCE_VOLUME,
    STAGE0_CODE_SHA,
    STAGE0_PREFIX,
    STAGE0_RUN_ID,
    STAGE1_CODE_SHA,
    STAGE1_PREFIX,
    STAGE1_RUN_ID,
    bind_stage2_job,
    build_stage2_cell_jobs,
    checked_receipt,
    job_id,
    load_prior_96_cells,
    prefix_for,
    publish_receipt,
    quote_sha_of,
    rank_market_120_strategies,
    select_top4_diverse_finalists,
    source_signal_relpath,
)
from stage0_p0_core import (  # noqa: E402
    apply_cost_stress,
    backtest_market_signal,
)
from audit.csi1000_layered_search_core import require, sha256_file, stable_sha  # noqa: E402

try:
    import modal
except ImportError:
    modal = None

SOURCE = Path("/source")
RESULTS = Path("/results")
REPO = Path("/root/qlib")

if modal is not None:
    original = modal.Volume.from_name(SOURCE_VOLUME, create_if_missing=False)
    research = modal.Volume.from_name(RESEARCH_VOLUME, create_if_missing=False, version=2)
    image = (
        modal.Image.debian_slim(python_version="3.11")
        .apt_install("build-essential")
        .env(
            {
                "PYTHONPATH": (
                    "/root/qlib:"
                    "/root/qlib/quant/code:"
                    "/root/qlib/quant/code/execution:"
                    "/root/qlib/quant/code/production:"
                    "/root/qlib/quant/code/research/stage_ab:"
                    "/root/qlib/quant/code/research/layered_search:"
                    "/root/qlib/quant/code/research/csi300_csi500"
                ),
                "MLFLOW_ALLOW_FILE_STORE": "true",
            }
        )
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
                or any(
                    t in str(p)
                    for t in (
                        ".git",
                        ".worktrees",
                        ".venv",
                        "mlruns",
                        "__pycache__",
                        "quant/local_backup",
                        "quant/data",
                    )
                )
            ),
        )
        .run_commands(
            "printf 'version = \"0.0.0\"\\n' > /root/qlib/qlib/_version.py",
            "cd /tmp && python -c \"from setuptools import setup, Extension; from Cython.Build import cythonize; import numpy; root='/root/qlib'; extensions=[Extension('qlib.data._libs.rolling', [root+'/qlib/data/_libs/rolling.pyx'], language='c++', include_dirs=[numpy.get_include()]), Extension('qlib.data._libs.expanding', [root+'/qlib/data/_libs/expanding.pyx'], language='c++', include_dirs=[numpy.get_include()])]; setup(name='qlib-image-cython-only', version='0.0.0', ext_modules=cythonize(extensions, language_level=3), script_args=['build_ext', '--build-lib', root, '--build-temp', '/tmp/qlib-image-build'])\"",
            "cd /tmp && python -c \"import qlib; from qlib.data._libs import rolling, expanding; assert qlib.__version__ == '0.0.0'\"",
        )
    )
    app = modal.App("qlib-csi300-csi500-stage2-l1", image=image)
    mounts = {str(SOURCE): original.with_mount_options(read_only=True), str(RESULTS): research}

    def _root(job: dict) -> Path:
        return RESULTS / prefix_for(job["run_id"], job["code_sha"])

    def _identity(job: dict) -> dict:
        return {
            k: job[k]
            for k in (
                "run_id",
                "code_sha",
                "provider_fingerprint",
                "source_snapshot_sha256",
                "quote_sha256",
            )
        }

    def _source_checks() -> dict:
        # The image excludes quant/data: gate against frozen EXPECTED_* values.
        snap = SOURCE / "csi1000_stage_b" / "provider_snapshot.json"
        require(snap.is_file(), "STOP_STAGE2_SOURCE: provider snapshot missing")
        row = json.loads(snap.read_text(encoding="utf-8"))
        require(
            row.get("provider_fingerprint") == EXPECTED_PROVIDER_FINGERPRINT,
            "STOP_STAGE2_SOURCE: fingerprint differs from frozen Stage-B",
        )
        require(
            row.get("snapshot_token") == EXPECTED_SNAPSHOT_TOKEN,
            "STOP_STAGE2_SOURCE: snapshot token differs from frozen Stage-B",
        )
        return {
            "provider_fingerprint": row["provider_fingerprint"],
            "snapshot_token": row["snapshot_token"],
            "snapshot_sha256": sha256_file(snap),
        }

    def _scan_source_signals() -> dict:
        """Verify all 48 frozen source signals against their sealed receipt SHAs."""
        out: dict = {}
        for market in MARKETS:
            for model_key, spec in ALL_12_MODELS.items():
                for phase in PHASES:
                    relpath = source_signal_relpath(market, model_key, phase)
                    signal = RESULTS / relpath
                    require(signal.is_file(), f"STOP_STAGE2_SIGNAL_MISSING: {relpath}")
                    receipt_file = signal.parent / "receipt.json"
                    require(receipt_file.is_file(), f"STOP_STAGE2_SIGNAL_RECEIPT: {relpath}")
                    record = json.loads(receipt_file.read_text(encoding="utf-8"))
                    require(
                        record.get("status") == "PASS" and record.get("kind") == "cell",
                        f"STOP_STAGE2_SIGNAL_RECEIPT_STATUS: {relpath}",
                    )
                    expected_run = STAGE0_RUN_ID if spec["source_stage"] == "stage0_p0" else STAGE1_RUN_ID
                    expected_sha = STAGE0_CODE_SHA if spec["source_stage"] == "stage0_p0" else STAGE1_CODE_SHA
                    require(
                        record.get("run_id") == expected_run and record.get("code_sha") == expected_sha,
                        f"STOP_STAGE2_SIGNAL_RECEIPT_LINEAGE: {relpath}",
                    )
                    hashes = record.get("artifact_sha256") or {}
                    expected = hashes.get("signal.parquet")
                    require(
                        isinstance(expected, str) and re.fullmatch(r"[a-f0-9]{64}", expected) is not None,
                        f"STOP_STAGE2_SIGNAL_RECEIPT_HASH: {relpath}",
                    )
                    measured = sha256_file(signal)
                    require(measured == expected, f"STOP_STAGE2_SIGNAL_SHA: {relpath}")
                    out[f"{market}/{model_key}/phase{phase:02d}"] = {
                        "relpath": relpath,
                        "sha256": measured,
                        "source_stage": spec["source_stage"],
                        "receipt_sha256": sha256_file(receipt_file),
                    }
        require(len(out) == 48, "STOP_STAGE2_SIGNAL_COUNT")
        return out

    def _bind_all(meta: dict) -> list[dict]:
        jobs = build_stage2_cell_jobs(meta["run_id"], meta["code_sha"])
        bound = []
        for job in jobs:
            key = f"{job['market']}/{job['model_key']}/phase{job['phase']:02d}"
            entry = (meta.get("source_signals") or {}).get(key)
            require(
                isinstance(entry, dict) and entry.get("relpath") == job["source_signal_relpath"],
                f"STOP_STAGE2_SOURCE_INDEX: {key}",
            )
            bound.append(
                bind_stage2_job(
                    job,
                    provider_fingerprint=meta["provider_fingerprint"],
                    source_snapshot_sha256=meta["source_snapshot_sha256"],
                    quote_sha256=meta["quote_sha256_by_market"][job["market"]],
                    source_signal_sha256=entry["sha256"],
                )
            )
        require(len(bound) == 384, "STOP_STAGE2_ROSTER")
        return bound

    def _dispatch_l1_cells(jobs: list[dict], max_workers: int) -> None:
        width = min(max_workers, 80)
        for start in range(0, len(jobs), width):
            wave = jobs[start : start + width]
            already = receipts_remote.remote(wave)
            todo = [job for job, row in zip(wave, already) if row is None]
            if not todo:
                continue
            print(f"STAGE2_L1CELL_WAVE {start // width + 1}: new={len(todo)} workers={width}", flush=True)
            answers = list(l1_cell_replay_remote.with_options(max_containers=width).map(todo))
            require(
                len(answers) == len(todo) and all(x.get("status") == "PASS" for x in answers),
                "STOP_STAGE2_L1CELL_WAVE_FAILED",
            )
            verified = receipts_remote.remote(todo)
            require(all(x is not None for x in verified), "STOP_STAGE2_L1CELL_ARTIFACT_UNVERIFIED")

    def _pull_run_prefix(meta: dict, dest_root: str) -> dict:
        """Plan-mandated concurrent artifact pull (16 threads, never serial)."""
        import os as _os
        import threading
        from concurrent.futures import ThreadPoolExecutor

        from modal.volume import FileEntryType

        prefix = prefix_for(meta["run_id"], meta["code_sha"])
        volume = modal.Volume.from_name(RESEARCH_VOLUME, create_if_missing=False, version=2)
        entries = volume.listdir(prefix, recursive=True)
        files = [entry for entry in entries if entry.type == FileEntryType.FILE]
        require(bool(files), "STOP_STAGE2_PULL_EMPTY")
        dest = Path(dest_root) / "volume_snapshot"
        state = {"downloaded": 0, "skipped": 0, "failed": []}
        lock = threading.Lock()

        def _fetch(entry) -> None:
            target = dest / str(entry.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_file() and target.stat().st_size == int(entry.size):
                with lock:
                    state["skipped"] += 1
                return
            tmp = target.with_name(target.name + ".tmp")
            try:
                with tmp.open("wb") as handle:
                    volume.read_file_into_fileobj(str(entry.path), handle)
                _os.replace(tmp, target)
                with lock:
                    state["downloaded"] += 1
            except BaseException as exc:  # noqa: BLE001
                if tmp.exists():
                    tmp.unlink()
                with lock:
                    state["failed"].append((str(entry.path), repr(exc)))

        with ThreadPoolExecutor(max_workers=16) as pool:
            list(pool.map(_fetch, files))
        require(not state["failed"], f"STOP_STAGE2_PULL_FAILED: {state['failed'][:3]}")
        return {"dest": str(dest), "files": len(files), "downloaded": state["downloaded"], "skipped": state["skipped"]}

    @app.function(volumes=mounts, cpu=2, memory=4096, timeout=900, retries=0)
    def inventory_remote(meta: dict) -> dict:
        """Verify provider snapshot, calendar, quotes, benchmarks and 48 signals."""
        import qlib
        from qlib.data import D
        from qlib_audit_fixes import read_trading_calendar
        from audit.csi1000_layered_search_replay import static_quote_union

        src = _source_checks()
        require(
            src["provider_fingerprint"] == meta["provider_fingerprint"],
            "STOP_STAGE2_SOURCE: fingerprint identity drift",
        )
        qlib.init(provider_uri=str(SOURCE / "cn_data"), region="cn")
        calendar = read_trading_calendar(SOURCE / "cn_data")
        require(len(calendar) == EXPECTED_CALENDAR_DAYS, "STOP_STAGE2_CALENDAR: day count drift")
        require(calendar[-1] == EXPECTED_CALENDAR_END, "STOP_STAGE2_CALENDAR: cutoff drift")
        sessions = D.calendar(start_time=RESERVED_EXECUTION_START, end_time=RESERVED_EXECUTION_END)
        require(len(sessions) == N_EXECUTION_DAYS, "STOP_STAGE2_EXECUTION_WINDOW")
        quotes = {}
        for market, spec in MARKETS.items():
            codes, quote_sha = static_quote_union(market, RESERVED_EXECUTION_START, RESERVED_EXECUTION_END, D)
            require(len(codes) == spec["expected_quote_count"], f"STOP_STAGE2_QUOTE_COUNT: {market}")
            require(quote_sha == spec["expected_quote_sha256"], f"STOP_STAGE2_QUOTE_SHA: {market}")
            require(quote_sha == quote_sha_of(codes), f"STOP_STAGE2_QUOTE_RECOMPUTE: {market}")
            quotes[market] = {"count": len(codes), "quote_sha256": quote_sha}
            frame = D.features(
                [spec["benchmark"]],
                ["$close"],
                start_time=RESERVED_EXECUTION_START,
                end_time=RESERVED_EXECUTION_END,
                freq="day",
            )
            closes = frame["$close"] if "$close" in frame else frame.iloc[:, 0]
            import pandas as pd

            values = pd.Series(closes).dropna().to_numpy(dtype=float)
            require(len(values) == N_EXECUTION_DAYS, f"STOP_STAGE2_BENCH_COVERAGE: {spec['benchmark']}")
            require(bool((values > 0).all()), f"STOP_STAGE2_BENCH_POSITIVE: {spec['benchmark']}")
        return {
            "status": "PASS",
            "source": src,
            "quotes": quotes,
            "calendar_days": len(calendar),
            "calendar_end": calendar[-1],
            "source_signals": _scan_source_signals(),
        }

    @app.function(volumes=mounts, cpu=2, memory=4096, timeout=1800, retries=1)
    def l1_cell_replay_remote(job: dict) -> dict:
        """One zero-training F1 replay from a frozen, SHA-pinned signal."""
        import pandas as pd
        import qlib
        from qlib.data import D
        from qlib_audit_fixes import read_trading_calendar
        from audit.csi1000_layered_search_replay import report_metrics, static_quote_union

        research.reload()
        identity = _identity(job)
        root = _root(job)
        already = checked_receipt(root, job, identity)
        if already is not None:
            return {**already, "resumed": True}
        require(job.get("kind") == "l1cell", "STAGE2_L1 replay supports l1cell only")
        src = _source_checks()
        require(
            src["provider_fingerprint"] == identity["provider_fingerprint"]
            and src["snapshot_sha256"] == identity["source_snapshot_sha256"],
            "STOP_STAGE2_SOURCE_DRIFT",
        )
        require(job["benchmark"] == MARKETS[job["market"]]["benchmark"], "STAGE2_L1 market/benchmark mismatch")
        require(
            job["source_signal_relpath"] == source_signal_relpath(job["market"], job["model_key"], job["phase"]),
            "STOP_STAGE2_SOURCE_RELPATH_DRIFT",
        )
        source = RESULTS / job["source_signal_relpath"]
        require(source.is_file(), "STOP_STAGE2_SOURCE_SIGNAL_MISSING")
        require(sha256_file(source) == job["source_signal_sha256"], "STOP_STAGE2_SOURCE_SIGNAL_SHA")
        signal = pd.read_parquet(source)["score"]
        require(signal.index.is_unique, "STOP_STAGE2_SIGNAL_DUPLICATE")
        qlib.init(provider_uri=str(SOURCE / "cn_data"), region="cn")
        calendar = read_trading_calendar(SOURCE / "cn_data")
        actual_days = sorted({str(v)[:10] for v in signal.index.get_level_values(0).unique()})
        first = calendar.index(RESERVED_SIGNAL_ANCHOR)
        last = calendar.index(RESERVED_EXECUTION_END)
        require(actual_days == calendar[first:last], "STOP_STAGE2_SIGNAL_DAY_COVERAGE")
        codes, quote_sha = static_quote_union(job["market"], RESERVED_EXECUTION_START, RESERVED_EXECUTION_END, D)
        require(quote_sha == job["quote_sha256"], "STOP_STAGE2_QUOTE_SHA_DRIFT")
        report, decisions = backtest_market_signal(
            signal,
            job["topk"],
            job["n_drop"],
            codes,
            RESERVED_EXECUTION_START,
            RESERVED_EXECUTION_END,
            job["benchmark"],
        )
        metrics, diagnostics = report_metrics(report)
        require(len(report) == N_EXECUTION_DAYS and report.index.is_unique, "STOP_STAGE2_424_DAY_REPORT")
        require(len(decisions) == N_EXECUTION_DAYS, "STOP_STAGE2_424_DAY_DECISIONS")
        require(
            diagnostics["max_account_return_delta"] <= 1e-10 and diagnostics["max_cost_rate_delta"] <= 1e-10,
            "STOP_STAGE2_NAV_ACCOUNT_DRIFT",
        )
        stressed = {}
        for label, bps in (("10bp", 10.0), ("20bp", 20.0)):
            numbers, _ = report_metrics(apply_cost_stress(report, bps))
            stressed[label] = numbers
        details = {
            "market": job["market"],
            "benchmark": job["benchmark"],
            "model_key": job["model_key"],
            "candidate_id": job["candidate_id"],
            "label_horizon": int(job["label_horizon"]),
            "source_stage": job["source_stage"],
            "combo_key": job["combo_key"],
            "topk": job["topk"],
            "n_drop": job["n_drop"],
            "phase": job["phase"],
            "source_signal_sha256": job["source_signal_sha256"],
            "source_signal_relpath": job["source_signal_relpath"],
            "model_config_sha256": ALL_12_MODELS[job["model_key"]]["model_sha256"],
            "provider_snapshot_sha256": src["snapshot_sha256"],
            "metrics": metrics,
            "stressed": stressed,
            "mean_turnover": metrics["mean_turnover"],
            "n_days": metrics["n_days"],
            "coverage_ok": True,
            "ledger_ok": True,
            "data_ok": True,
            "fit_performed": False,
            "new_fits": 0,
        }
        row = publish_receipt(
            root,
            job,
            identity,
            {"fixed_report.parquet": report, "decisions.json": decisions},
            details,
        )
        research.commit()
        return row

    @app.function(volumes=mounts, cpu=2, memory=4096, timeout=900, retries=0)
    def export_summary_remote(meta: dict) -> dict:
        """Merge 384 new l1cells + 96 prior cells into 480 cells / 120 rankings."""
        research.reload()
        root = RESULTS / prefix_for(meta["run_id"], meta["code_sha"])
        jobs = _bind_all(meta)
        cells = []
        for job in jobs:
            rec = checked_receipt(root, job, _identity(job))
            require(rec is not None, f"STOP_STAGE2_MISSING_CELL: {job_id(job)}")
            cells.append(
                {
                    "market": job["market"],
                    "benchmark": job["benchmark"],
                    "model_key": job["model_key"],
                    "candidate_id": job["candidate_id"],
                    "label_horizon": int(job["label_horizon"]),
                    "anchor": False,
                    "source_stage": job["source_stage"],
                    "combo_key": job["combo_key"],
                    "topk": job["topk"],
                    "n_drop": job["n_drop"],
                    "phase": job["phase"],
                    "metrics": rec["metrics"],
                    "stressed": rec["stressed"],
                    "artifact_sha256": rec["artifact_sha256"],
                    "job_sha256": rec["job_sha256"],
                    "source_signal_sha256": rec["source_signal_sha256"],
                    "source_signal_relpath": rec["source_signal_relpath"],
                }
            )
        require(len(cells) == 384, "STOP_STAGE2_SUMMARY_COVERAGE")
        prior = load_prior_96_cells(
            RESULTS, meta.get("stage0_prefix", STAGE0_PREFIX), meta.get("stage1_prefix", STAGE1_PREFIX)
        )
        require(len(prior) == 96, "STOP_STAGE2_PRIOR_COVERAGE")
        for cell in prior:
            require(
                cell.get("provider_fingerprint") in ("", meta["provider_fingerprint"]),
                "STOP_STAGE2_PRIOR_PROVIDER_DRIFT",
            )
        all_cells = sorted(
            cells + prior, key=lambda row: (row["market"], row["model_key"], row["combo_key"], row["phase"])
        )
        require(len(all_cells) == 480, "STOP_STAGE2_480_COVERAGE")
        rankings = {
            market: rank_market_120_strategies([c for c in all_cells if c["market"] == market]) for market in MARKETS
        }
        require(all(len(rows) == 120 for rows in rankings.values()), "STOP_STAGE2_RANKING_COVERAGE")
        finalists = {market: select_top4_diverse_finalists(rankings[market]) for market in MARKETS}
        summary = {
            "status": "PASS",
            "stage": "stage2_l1",
            "run_id": meta["run_id"],
            "code_sha": meta["code_sha"],
            "provider_fingerprint": meta["provider_fingerprint"],
            "source_snapshot_sha256": meta["source_snapshot_sha256"],
            "quote_sha256_by_market": meta["quote_sha256_by_market"],
            "stage0_run_id": STAGE0_RUN_ID,
            "stage0_code_sha": STAGE0_CODE_SHA,
            "stage0_prefix": meta.get("stage0_prefix", STAGE0_PREFIX),
            "stage1_run_id": STAGE1_RUN_ID,
            "stage1_code_sha": STAGE1_CODE_SHA,
            "stage1_prefix": meta.get("stage1_prefix", STAGE1_PREFIX),
            "source_signals": meta["source_signals"],
            "expected_l1cells": 384,
            "completed_l1cells": len(cells),
            "prior_cells": len(prior),
            "total_cells": len(all_cells),
            "new_fits": 0,
            "cells": all_cells,
            "rankings": rankings,
            "finalists": finalists,
        }
        path = root / "stage2_l1_summary.json"
        tmp = path.with_suffix(".tmp")
        require(not tmp.exists(), "STOP_STAGE2_SUMMARY_TEMP")
        tmp.write_text(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
        tmp.replace(path)
        research.commit()
        return summary

    @app.function(volumes={str(RESULTS): research}, cpu=1, memory=1024, timeout=600, retries=0)
    def receipts_remote(jobs: list[dict]) -> list[dict | None]:
        research.reload()
        out = []
        for job in jobs:
            root = _root(job)
            out.append(checked_receipt(root, job, _identity(job)))
        return out

    @app.function(volumes={str(RESULTS): research}, cpu=1, memory=1024, timeout=600, retries=0)
    def freeze_remote(meta: dict, key: str, payload: dict) -> dict:
        """Immutable marker, no modification after verification."""
        require(key in ("smoke_pass", "stage2_l1_summary"), "STOP_STAGE2_MARKER_KIND")
        research.reload()
        require(bool(re.fullmatch(r"[a-f0-9]{40}", meta.get("code_sha") or "")), "STOP_STAGE2_MARKER_IDENTITY")
        root = RESULTS / prefix_for(meta["run_id"], meta["code_sha"])
        root.mkdir(parents=True, exist_ok=True)
        path = root / (key + ".json")
        data = {"run_id": meta["run_id"], "code_sha": meta["code_sha"], **payload}
        require("run_id" not in payload and "code_sha" not in payload, "STOP_STAGE2_MARKER_IDENTITY_OVERRIDE")
        if path.exists():
            require(json.loads(path.read_text(encoding="utf-8")) == data, "STOP_STAGE2_MARKER_CONFLICT")
        else:
            tmp = path.with_suffix(".tmp")
            require(not tmp.exists(), "STOP_STAGE2_PARTIAL_MARKER")
            tmp.write_text(json.dumps(data, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
            tmp.replace(path)
            research.commit()
        return data

    @app.function(volumes={str(RESULTS): research}, cpu=1, memory=1024, timeout=600, retries=0)
    def marker_remote(meta: dict, key: str) -> dict | None:
        require(key in ("smoke_pass", "stage2_l1_summary"), "STOP_STAGE2_MARKER_KIND")
        research.reload()
        path = RESULTS / prefix_for(meta["run_id"], meta["code_sha"]) / (key + ".json")
        if not path.is_file():
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
        require(
            record.get("run_id") == meta["run_id"] and record.get("code_sha") == meta["code_sha"],
            "STOP_STAGE2_MARKER_IDENTITY",
        )
        return record

    def _actual_sha() -> str:
        repo = _find_repo_root()
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, cwd=str(repo)).strip()
        require(
            not subprocess.check_output(["git", "status", "--porcelain"], text=True, cwd=str(repo)).strip(),
            "STOP_STAGE2_UNCLEAN_WORKTREE",
        )
        require(bool(re.fullmatch(r"[a-f0-9]{40}", sha or "")), "STOP_STAGE2_INVALID_HEAD")
        return sha

    @app.local_entrypoint()
    def main(
        mode: str = "smoke",
        run_id: str = "stage2_l1_20261016_a1",
        max_workers: int = 64,
        stage0_run_id: str = STAGE0_RUN_ID,
        stage1_run_id: str = STAGE1_RUN_ID,
        pull_root: str = "",
    ) -> None:
        """Smoke replays 2 cells (0 fits); complete replays 384 + 480-cell ranking."""
        require(mode in ("smoke", "complete"), "STOP_STAGE2_MODE")
        require(1 <= max_workers <= 80, "STOP_STAGE2_WORKERS")
        require((mode == "smoke" and max_workers == 2) or mode == "complete", "STOP_STAGE2_SMOKE_WORKERS")
        require(stage0_run_id == STAGE0_RUN_ID and stage1_run_id == STAGE1_RUN_ID, "STOP_STAGE2_LINEAGE")
        sha = _actual_sha()
        prefix_for(run_id, sha)
        meta = {"run_id": run_id, "code_sha": sha, "provider_fingerprint": EXPECTED_PROVIDER_FINGERPRINT}
        inventory = inventory_remote.remote(meta)
        require(inventory["status"] == "PASS", "STOP_STAGE2_INVENTORY")
        require(len(inventory["source_signals"]) == 48, "STOP_STAGE2_INVENTORY_SIGNALS")
        full_meta = {
            "run_id": run_id,
            "code_sha": sha,
            "provider_fingerprint": inventory["source"]["provider_fingerprint"],
            "source_snapshot_sha256": inventory["source"]["snapshot_sha256"],
            "quote_sha256_by_market": {m: inventory["quotes"][m]["quote_sha256"] for m in MARKETS},
            "source_signals": inventory["source_signals"],
            "stage0_prefix": STAGE0_PREFIX,
            "stage1_prefix": STAGE1_PREFIX,
        }
        jobs = _bind_all(full_meta)
        smoke_jobs = [
            next(
                j
                for j in jobs
                if j["market"] == "csi300"
                and j["model_key"] == "m01_59b93929"
                and j["combo_key"] == "top8_drop1"
                and j["phase"] == 0
            ),
            next(
                j
                for j in jobs
                if j["market"] == "csi500"
                and j["model_key"] == "m10_raw10d_c98856b4"
                and j["combo_key"] == "top15_drop2"
                and j["phase"] == 0
            ),
        ]
        smoke_shas = [stable_sha(job) for job in smoke_jobs]
        if mode == "smoke":
            require(marker_remote.remote(full_meta, "smoke_pass") is None, "STOP_STAGE2_SMOKE_ALREADY_DONE")
            _dispatch_l1_cells(smoke_jobs, max_workers)
            require(all(row is not None for row in receipts_remote.remote(smoke_jobs)), "STOP_STAGE2_SMOKE_RECEIPT")
            freeze_remote.remote(
                full_meta,
                "smoke_pass",
                {
                    "status": "PASS",
                    "smoke_job_shas": smoke_shas,
                    "provider_snapshot_sha256": full_meta["source_snapshot_sha256"],
                    "quote_sha256_by_market": full_meta["quote_sha256_by_market"],
                    "source_signal_sha256s": [job["source_signal_sha256"] for job in smoke_jobs],
                    "zero_fits": True,
                },
            )
            print("STAGE2_SMOKE_PASS 2/2 replay cells verified (0 fits)", flush=True)
            return
        marker = marker_remote.remote(full_meta, "smoke_pass")
        require(
            marker is not None
            and marker["status"] == "PASS"
            and marker["smoke_job_shas"] == smoke_shas
            and marker["provider_snapshot_sha256"] == full_meta["source_snapshot_sha256"]
            and marker.get("zero_fits") is True,
            "STOP_STAGE2_SMOKE_NOT_VERIFIED",
        )
        require(all(row is not None for row in receipts_remote.remote(smoke_jobs)), "STOP_STAGE2_SMOKE_RECEIPT_MISSING")
        _dispatch_l1_cells(jobs, max_workers)
        require(all(row is not None for row in receipts_remote.remote(jobs)), "STOP_STAGE2_CELLS_NOT_FULL")
        summary = export_summary_remote.remote(full_meta)
        require(
            summary["status"] == "PASS" and summary["completed_l1cells"] == 384 and summary["total_cells"] == 480,
            "STOP_STAGE2_SUMMARY_NOT_FULL",
        )
        require(
            all(len(summary["rankings"][m]) == 120 for m in MARKETS)
            and all(len(summary["finalists"][m]) == 4 for m in MARKETS),
            "STOP_STAGE2_RANKING_NOT_FULL",
        )
        if pull_root:
            pulled = _pull_run_prefix(full_meta, pull_root)
            print(
                f"STAGE2_PULL_OK {pulled['files']} files ({pulled['downloaded']} new, "
                f"{pulled['skipped']} existing) -> {pulled['dest']}",
                flush=True,
            )
        print("STAGE2_L1_COMPLETE 384/384 new cells 480/480 total ranking PASS (0 fits)", flush=True)

else:
    # Allows import/py_compile/--help on machines without Modal credentials.
    app = None
