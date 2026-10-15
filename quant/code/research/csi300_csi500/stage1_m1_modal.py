"""Stage 1 (M1) Modal runner: 10 new models x 2 phases rolling fits + 80 cells.

NO model fitting happens on import or via --help. The source Volume is
mounted read-only and artifacts go to the independent v2 research Volume
(shared with Stage 0 anchors). The coordinator stays attached in the
foreground: detaching, nohup or shell backgrounding are all forbidden.

Horizon handling (plan section 3.1): ``_task`` sets the label expression
from ``label_horizon`` (10 -> ``Ref($close, -10)/$close - 1``,
20 -> ``Ref($close, -20)/$close - 1``); ``fit_remote`` derives validation
splits and purge from ``horizon = int(job["label_horizon"])`` (never 20).

Trap compliance (plan section 3.2 of Stage 0, carried over):
1. Image ignore list excludes .git/.worktrees/.venv/mlruns/__pycache__/
   quant/local_backup/quant/data plus .so/.cpp build artifacts.
2. Image PYTHONPATH covers /root/qlib + quant/code + execution + production +
   stage_ab + layered_search + csi300_csi500 (plan-exact string).
3. Replay uses stage0_p0_core.backtest_market_signal with an explicit
   benchmark (csi300 -> SH000300, csi500 -> SH000905).
4. Training config sets handler instruments to the job market, never csi1000.
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
    raise ValueError("STAGE1_M1_NOREPO: cannot locate repository root")


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

from stage1_m1_core import (  # noqa: E402
    COMBOS,
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
    STAGE1_NEW_MODELS,
    bind_job_identity,
    build_stage1_cell_jobs,
    build_stage1_fit_jobs,
    checked_receipt,
    job_id,
    label_expr,
    load_stage0_anchor_cells,
    prefix_for,
    publish_receipt,
    quote_sha_of,
    rank_market_cells,
    receipt_path,
    retrain_splits,
    shared_signal_path,
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

BASE_YAML = "examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158_csi500.yaml"

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
    app = modal.App("qlib-csi300-csi500-stage1-m1", image=image)
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
        require(snap.is_file(), "STOP_STAGE1_SOURCE: provider snapshot missing")
        row = json.loads(snap.read_text(encoding="utf-8"))
        require(
            row.get("provider_fingerprint") == EXPECTED_PROVIDER_FINGERPRINT,
            "STOP_STAGE1_SOURCE: fingerprint differs from frozen Stage-B",
        )
        require(
            row.get("snapshot_token") == EXPECTED_SNAPSHOT_TOKEN,
            "STOP_STAGE1_SOURCE: snapshot token differs from frozen Stage-B",
        )
        return {
            "provider_fingerprint": row["provider_fingerprint"],
            "snapshot_token": row["snapshot_token"],
            "snapshot_sha256": sha256_file(snap),
        }

    def _task(model_params: dict, market: str, label_horizon: int) -> dict:
        from ruamel.yaml import YAML

        from qlib_live_retrain import apply_lgb_reproducibility

        require(market in MARKETS, "STAGE1_M1 unknown market")
        horizon = int(label_horizon)
        require(horizon in (10, 20), "STAGE1_M1 invalid label_horizon")
        cfg_path = REPO / BASE_YAML
        cfg = YAML(typ="safe", pure=True).load(cfg_path.read_text(encoding="utf-8"))
        ds = cfg["task"]["dataset"]["kwargs"]
        ds["handler"]["kwargs"]["instruments"] = market
        ds["handler"]["kwargs"]["label"] = [label_expr(horizon)]
        cfg["task"]["model"]["kwargs"].update(model_params)
        apply_lgb_reproducibility(cfg)
        require(int(cfg["task"]["model"]["kwargs"]["num_threads"]) == 20, "STOP_STAGE1_MODEL_THREADS")
        cfg["qlib_init"] = {
            "provider_uri": str(SOURCE / "cn_data"),
            "region": "cn",
            "exp_manager": {
                "class": "MLflowExpManager",
                "module_path": "qlib.workflow.expm",
                "kwargs": {"uri": "file:/tmp/stage1_m1_mlflow", "default_exp_name": "qlib-stage1-m1-isolated"},
            },
        }
        return cfg

    def _model_sha(model_params: dict) -> str:
        return stable_sha(_task(model_params, "csi300", 20)["task"]["model"])

    @app.function(volumes=mounts, cpu=2, memory=4096, timeout=900, retries=0)
    def inventory_remote(meta: dict) -> dict:
        """Verify provider snapshot, calendar, both static quote unions, benchmarks."""
        import qlib
        from qlib.data import D
        from qlib_audit_fixes import read_trading_calendar
        from audit.csi1000_layered_search_replay import static_quote_union

        src = _source_checks()
        require(
            src["provider_fingerprint"] == meta["provider_fingerprint"], "STOP_STAGE1_SOURCE: fingerprint identity drift"
        )
        qlib.init(provider_uri=str(SOURCE / "cn_data"), region="cn")
        calendar = read_trading_calendar(SOURCE / "cn_data")
        require(len(calendar) == EXPECTED_CALENDAR_DAYS, "STOP_STAGE1_CALENDAR: day count drift")
        require(calendar[-1] == EXPECTED_CALENDAR_END, "STOP_STAGE1_CALENDAR: cutoff drift")
        sessions = D.calendar(start_time=RESERVED_EXECUTION_START, end_time=RESERVED_EXECUTION_END)
        require(len(sessions) == N_EXECUTION_DAYS, "STOP_STAGE1_EXECUTION_WINDOW")
        quotes = {}
        for market, spec in MARKETS.items():
            codes, quote_sha = static_quote_union(market, RESERVED_EXECUTION_START, RESERVED_EXECUTION_END, D)
            require(len(codes) == spec["expected_quote_count"], f"STOP_STAGE1_QUOTE_COUNT: {market}")
            require(quote_sha == spec["expected_quote_sha256"], f"STOP_STAGE1_QUOTE_SHA: {market}")
            require(quote_sha == quote_sha_of(codes), f"STOP_STAGE1_QUOTE_RECOMPUTE: {market}")
            quotes[market] = {"count": len(codes), "quote_sha256": quote_sha}
            frame = D.features([spec["benchmark"]], ["$close"], start_time=RESERVED_EXECUTION_START,
                               end_time=RESERVED_EXECUTION_END, freq="day")
            closes = frame["$close"] if "$close" in frame else frame.iloc[:, 0]
            import pandas as pd

            values = pd.Series(closes).dropna().to_numpy(dtype=float)
            require(len(values) == N_EXECUTION_DAYS, f"STOP_STAGE1_BENCH_COVERAGE: {spec['benchmark']}")
            require(bool((values > 0).all()), f"STOP_STAGE1_BENCH_POSITIVE: {spec['benchmark']}")
        return {
            "status": "PASS",
            "source": src,
            "calendar": calendar,
            "quotes": quotes,
        }

    @app.function(volumes=mounts, cpu=8, memory=16384, timeout=3600, retries=1)
    def fit_remote(job: dict) -> dict:
        """One genuine LightGBM fit for exactly one (market, model, phase, as-of)."""
        import qlib
        from qlib.data.dataset import Dataset
        from qlib.model.base import Model
        from qlib.utils import init_instance_by_config
        from qlib_audit_fixes import purge_cfg_splits, read_trading_calendar

        research.reload()
        identity = _identity(job)
        root = _root(job)
        cached = checked_receipt(root, job, identity)
        if cached is not None:
            return {**cached, "resumed": True}
        require(job.get("kind") == "retrain", "STAGE1_M1 fit supports retrain only")
        source = _source_checks()
        require(
            source["provider_fingerprint"] == identity["provider_fingerprint"]
            and source["snapshot_sha256"] == identity["source_snapshot_sha256"],
            "STOP_STAGE1_SOURCE_DRIFT",
        )
        spec = STAGE1_NEW_MODELS[job["model_key"]]
        require(job["candidate_id"] == spec["candidate_id"], "STAGE1_M1 model ID mismatch")
        require(job["model_params"] == spec["model_params"], "STAGE1_M1 model params mismatch")
        require(int(job["label_horizon"]) == spec["label_horizon"], "STAGE1_M1 horizon mismatch")
        require(job["benchmark"] == MARKETS[job["market"]]["benchmark"], "STAGE1_M1 market/benchmark mismatch")
        config = _task(job["model_params"], job["market"], job["label_horizon"])
        require(stable_sha(config["task"]["model"]) == job["model_sha256"], "STAGE1_M1 model config SHA mismatch")
        require(_model_sha(job["model_params"]) == job["model_sha256"], "STAGE1_M1 model SHA recompute mismatch")
        qlib.init(**{**config["qlib_init"], "skip_if_reg": True})
        calendar = read_trading_calendar(SOURCE / "cn_data")
        from csi1000_stage_b_core import build_phase_jobs

        horizon = int(job["label_horizon"])
        require(horizon in (10, 20), "STOP_STAGE1_HORIZON")
        legal = build_phase_jobs(calendar, job["phase"])
        require(
            any(p["retrain_asof"] == job["retrain_asof"] and p["signal_end"] == job["signal_end"] for p in legal),
            "STOP_STAGE1_LONG_CALENDAR",
        )
        splits = retrain_splits(calendar, job["retrain_asof"], horizon)
        ds = config["task"]["dataset"]["kwargs"]
        segments, handler = ds["segments"], ds["handler"]["kwargs"]
        segments.update(
            {
                "train": splits["train"],
                "valid": splits["valid"],
                "test": [job["retrain_asof"], job["signal_end"]],
            }
        )
        handler["start_time"], handler["end_time"] = "2015-01-01", job["signal_end"]
        handler["fit_start_time"], handler["fit_end_time"] = splits["train"][0], splits["train"][1]
        purge_cfg_splits(config, calendar, horizon=horizon)
        model = init_instance_by_config(config["task"]["model"], accept_types=Model)
        dataset = init_instance_by_config(config["task"]["dataset"], accept_types=Dataset)
        model.fit(dataset)  # the ONLY new fit call for this job
        prediction = model.predict(dataset, segment="test")
        require(not prediction.empty, "STOP_STAGE1_EMPTY_PREDICTION")
        observed = [
            str(prediction.index.get_level_values(0).min())[:10],
            str(prediction.index.get_level_values(0).max())[:10],
        ]
        require(observed == [job["retrain_asof"], job["signal_end"]], "STOP_STAGE1_PREDICTION_COVERAGE")
        import numpy as np

        require(
            prediction.index.is_unique
            and prediction.notna().all().all()
            and np.isfinite(prediction.to_numpy(dtype=float)).all(),
            "STOP_STAGE1_SIGNAL_SCHEMA",
        )
        booster = getattr(model, "model", None)
        require(booster is not None and hasattr(booster, "model_to_string"), "STOP_STAGE1_NO_TRAINED_BOOSTER")
        checkpoint = booster.model_to_string()
        require(checkpoint and len(checkpoint) > 100, "STOP_STAGE1_EMPTY_CHECKPOINT")
        artifacts = {"prediction.parquet": prediction.to_frame("score"), "checkpoint.txt": checkpoint}
        details = {
            "market": job["market"],
            "benchmark": job["benchmark"],
            "model_key": job["model_key"],
            "candidate_id": job["candidate_id"],
            "model_config_sha256": job["model_sha256"],
            "label_horizon": horizon,
            "phase": job["phase"],
            "retrain_asof": job["retrain_asof"],
            "signal_end": job["signal_end"],
            "best_iteration": int(booster.best_iteration or 0),
            "fit_performed": True,
        }
        row = publish_receipt(root, job, identity, artifacts, details)
        research.commit()
        return row

    @app.function(volumes=mounts, cpu=2, memory=4096, timeout=1800, retries=0)
    def cell_replay_remote(job: dict) -> dict:
        """Assemble one 424-day signal from 22 verified fits and run one F1 replay."""
        import pandas as pd
        import qlib
        from qlib.data import D
        from qlib_audit_fixes import read_trading_calendar
        from audit.csi1000_layered_search_replay import report_metrics, static_quote_union
        from csi1000_stage_b_core import (
            RESERVED_EXECUTION_END,
            RESERVED_EXECUTION_START,
            RESERVED_SIGNAL_ANCHOR,
            build_phase_jobs,
        )

        research.reload()
        identity = _identity(job)
        root = _root(job)
        already = checked_receipt(root, job, identity)
        if already is not None:
            return {**already, "resumed": True}
        require(job.get("kind") == "cell", "STAGE1_M1 replay supports cell only")
        src = _source_checks()
        require(
            src["provider_fingerprint"] == identity["provider_fingerprint"]
            and src["snapshot_sha256"] == identity["source_snapshot_sha256"],
            "STOP_STAGE1_SOURCE_DRIFT",
        )
        require(job["benchmark"] == MARKETS[job["market"]]["benchmark"], "STAGE1_M1 market/benchmark mismatch")
        qlib.init(provider_uri=str(SOURCE / "cn_data"), region="cn")
        calendar = read_trading_calendar(SOURCE / "cn_data")
        chunks = []
        source_shas = []
        for part in build_phase_jobs(calendar, job["phase"]):
            fit_job = {
                **{k: job[k] for k in ("run_id", "code_sha", "provider_fingerprint", "source_snapshot_sha256",
                                       "quote_sha256")},
                "kind": "retrain",
                "market": job["market"],
                "benchmark": job["benchmark"],
                "model_key": job["model_key"],
                "candidate_id": job["candidate_id"],
                "model_params": STAGE1_NEW_MODELS[job["model_key"]]["model_params"],
                "model_sha256": STAGE1_NEW_MODELS[job["model_key"]]["model_sha256"],
                "label_horizon": job["label_horizon"],
                "phase": job["phase"],
                "retrain_asof": part["retrain_asof"],
                "signal_end": part["signal_end"],
            }
            rec = checked_receipt(root, fit_job, identity)
            require(rec is not None, f"STOP_STAGE1_MISSING_FIT: {part['retrain_asof']}")
            chunks.append(pd.read_parquet(root / "fits" / job_id(fit_job) / "prediction.parquet")["score"])
            source_shas.append(rec["artifact_sha256"]["prediction.parquet"])
        signal = pd.concat(chunks).sort_index()
        require(signal.index.is_unique, "STOP_STAGE1_DUPLICATE_PREDICTION")
        dates = signal.index.get_level_values(0).astype(str).str[:10]
        signal = signal[(dates >= RESERVED_SIGNAL_ANCHOR) & (dates < RESERVED_EXECUTION_END)]
        actual_days = sorted({str(v)[:10] for v in signal.index.get_level_values(0).unique()})
        first = calendar.index(RESERVED_SIGNAL_ANCHOR)
        last = calendar.index(RESERVED_EXECUTION_END)
        require(actual_days == calendar[first:last], "STOP_STAGE1_SIGNAL_DAY_COVERAGE")
        codes, quote_sha = static_quote_union(job["market"], RESERVED_EXECUTION_START, RESERVED_EXECUTION_END, D)
        require(quote_sha == job["quote_sha256"], "STOP_STAGE1_QUOTE_SHA_DRIFT")
        shared = shared_signal_path(root, job["market"], job["model_key"], job["phase"])
        if shared.is_file():
            require(pd.read_parquet(shared)["score"].equals(signal.to_frame("score")["score"]),
                    "STOP_STAGE1_SHARED_SIGNAL_DRIFT")
        else:
            shared.parent.mkdir(parents=True, exist_ok=True)
            tmp_shared = shared.parent / (shared.name + ".tmp")
            require(not tmp_shared.exists(), "STOP_STAGE1_SHARED_SIGNAL_TEMP")
            signal.to_frame("score").to_parquet(tmp_shared)
            import os as _os

            _os.replace(tmp_shared, shared)
        report, decisions = backtest_market_signal(
            signal, job["topk"], job["n_drop"], codes,
            RESERVED_EXECUTION_START, RESERVED_EXECUTION_END, job["benchmark"],
        )
        metrics, diagnostics = report_metrics(report)
        require(len(report) == N_EXECUTION_DAYS and report.index.is_unique, "STOP_STAGE1_424_DAY_REPORT")
        require(len(decisions) == N_EXECUTION_DAYS, "STOP_STAGE1_424_DAY_DECISIONS")
        require(
            diagnostics["max_account_return_delta"] <= 1e-10
            and diagnostics["max_cost_rate_delta"] <= 1e-10,
            "STOP_STAGE1_NAV_ACCOUNT_DRIFT",
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
            "combo_key": job["combo_key"],
            "topk": job["topk"],
            "n_drop": job["n_drop"],
            "phase": job["phase"],
            "source_prediction_shas": source_shas,
            "model_config_sha256": STAGE1_NEW_MODELS[job["model_key"]]["model_sha256"],
            "provider_snapshot_sha256": src["snapshot_sha256"],
            "metrics": metrics,
            "stressed": stressed,
            "mean_turnover": metrics["mean_turnover"],
            "n_days": metrics["n_days"],
            "coverage_ok": True,
            "ledger_ok": True,
            "data_ok": True,
            "fit_performed": False,
        }
        row = publish_receipt(
            root,
            job,
            identity,
            {
                "fixed_report.parquet": report,
                "decisions.json": decisions,
                "signal.parquet": signal.to_frame("score"),
            },
            details,
        )
        research.commit()
        return row

    @app.function(volumes=mounts, cpu=2, memory=4096, timeout=600, retries=0)
    def export_summary_remote(meta: dict) -> dict:
        """Aggregate 80 new cells + 16 Stage 0 anchors into the 96-cell ranking."""
        research.reload()
        root = RESULTS / prefix_for(meta["run_id"], meta["code_sha"])
        jobs = build_stage1_cell_jobs(meta["run_id"], meta["code_sha"])
        bound = [
            bind_job_identity(
                job,
                provider_fingerprint=meta["provider_fingerprint"],
                source_snapshot_sha256=meta["source_snapshot_sha256"],
                quote_sha256=meta["quote_sha256_by_market"][job["market"]],
            )
            for job in jobs
        ]
        cells = []
        for job in bound:
            rec = checked_receipt(root, job, _identity(job))
            require(rec is not None, f"STOP_STAGE1_MISSING_CELL: {job_id(job)}")
            cells.append(
                {
                    "market": job["market"],
                    "benchmark": job["benchmark"],
                    "model_key": job["model_key"],
                    "candidate_id": job["candidate_id"],
                    "label_horizon": int(job["label_horizon"]),
                    "anchor": False,
                    "combo_key": job["combo_key"],
                    "topk": job["topk"],
                    "n_drop": job["n_drop"],
                    "phase": job["phase"],
                    "metrics": rec["metrics"],
                    "stressed": rec["stressed"],
                    "artifact_sha256": rec["artifact_sha256"],
                    "job_sha256": rec["job_sha256"],
                }
            )
        require(len(cells) == 80, "STOP_STAGE1_SUMMARY_COVERAGE")
        anchors = load_stage0_anchor_cells(RESULTS, meta.get("stage0_prefix", STAGE0_PREFIX))
        require(len(anchors) == 16, "STOP_STAGE1_ANCHOR_COVERAGE")
        for anchor in anchors:
            require(
                anchor.get("provider_fingerprint", meta["provider_fingerprint"]) == meta["provider_fingerprint"],
                "STOP_STAGE1_ANCHOR_PROVIDER_DRIFT",
            )
        all_cells = sorted(cells + anchors, key=lambda row: (row["market"], row["model_key"], row["combo_key"], row["phase"]))
        require(len(all_cells) == 96, "STOP_STAGE1_96_COVERAGE")
        rankings = {
            market: rank_market_cells([c for c in all_cells if c["market"] == market])
            for market in ("csi300", "csi500")
        }
        require(all(len(rows) == 24 for rows in rankings.values()), "STOP_STAGE1_RANKING_COVERAGE")
        summary = {
            "status": "PASS",
            "stage": "stage1_m1",
            "run_id": meta["run_id"],
            "code_sha": meta["code_sha"],
            "provider_fingerprint": meta["provider_fingerprint"],
            "source_snapshot_sha256": meta["source_snapshot_sha256"],
            "quote_sha256_by_market": meta["quote_sha256_by_market"],
            "stage0_run_id": STAGE0_RUN_ID,
            "stage0_code_sha": STAGE0_CODE_SHA,
            "stage0_prefix": meta.get("stage0_prefix", STAGE0_PREFIX),
            "expected_cells": 80,
            "completed_cells": len(cells),
            "anchor_cells": len(anchors),
            "total_cells": len(all_cells),
            "cells": all_cells,
            "rankings": rankings,
        }
        path = root / "stage1_m1_summary.json"
        tmp = path.with_suffix(".tmp")
        require(not tmp.exists(), "STOP_STAGE1_SUMMARY_TEMP")
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
        require(key in ("smoke_pass", "stage1_m1_summary"), "STOP_STAGE1_MARKER_KIND")
        research.reload()
        require(
            bool(re.fullmatch(r"[a-f0-9]{40}", meta.get("code_sha") or "")), "STOP_STAGE1_MARKER_IDENTITY"
        )
        root = RESULTS / prefix_for(meta["run_id"], meta["code_sha"])
        root.mkdir(parents=True, exist_ok=True)
        path = root / (key + ".json")
        data = {"run_id": meta["run_id"], "code_sha": meta["code_sha"], **payload}
        require("run_id" not in payload and "code_sha" not in payload, "STOP_STAGE1_MARKER_IDENTITY_OVERRIDE")
        if path.exists():
            require(json.loads(path.read_text(encoding="utf-8")) == data, "STOP_STAGE1_MARKER_CONFLICT")
        else:
            tmp = path.with_suffix(".tmp")
            require(not tmp.exists(), "STOP_STAGE1_PARTIAL_MARKER")
            tmp.write_text(json.dumps(data, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
            tmp.replace(path)
            research.commit()
        return data

    @app.function(volumes={str(RESULTS): research}, cpu=1, memory=1024, timeout=600, retries=0)
    def marker_remote(meta: dict, key: str) -> dict | None:
        require(key in ("smoke_pass", "stage1_m1_summary"), "STOP_STAGE1_MARKER_KIND")
        research.reload()
        path = RESULTS / prefix_for(meta["run_id"], meta["code_sha"]) / (key + ".json")
        if not path.is_file():
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
        require(
            record.get("run_id") == meta["run_id"] and record.get("code_sha") == meta["code_sha"],
            "STOP_STAGE1_MARKER_IDENTITY",
        )
        return record

    class SafePause(Exception):
        """Requested graceful foreground pause at a completed wave boundary."""

    def _actual_sha() -> str:
        repo = _find_repo_root()
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, cwd=str(repo)).strip()
        require(
            not subprocess.check_output(["git", "status", "--porcelain"], text=True, cwd=str(repo)).strip(),
            "STOP_STAGE1_UNCLEAN_WORKTREE",
        )
        require(bool(re.fullmatch(r"[a-f0-9]{40}", sha or "")), "STOP_STAGE1_INVALID_HEAD")
        return sha

    def _bind_all(calendar: list[str], run_id: str, sha: str, inventory: dict) -> tuple[list[dict], list[dict], dict]:
        fits = build_stage1_fit_jobs(calendar, run_id, sha)
        cells = build_stage1_cell_jobs(run_id, sha)
        bound_fits = [
            bind_job_identity(
                job,
                provider_fingerprint=inventory["source"]["provider_fingerprint"],
                source_snapshot_sha256=inventory["source"]["snapshot_sha256"],
                quote_sha256=inventory["quotes"][job["market"]]["quote_sha256"],
            )
            for job in fits
        ]
        meta = {
            "run_id": run_id,
            "code_sha": sha,
            "provider_fingerprint": inventory["source"]["provider_fingerprint"],
            "source_snapshot_sha256": inventory["source"]["snapshot_sha256"],
            "quote_sha256_by_market": {m: inventory["quotes"][m]["quote_sha256"] for m in MARKETS},
            "stage0_prefix": STAGE0_PREFIX,
        }
        bound_cells = [
            bind_job_identity(
                job,
                provider_fingerprint=meta["provider_fingerprint"],
                source_snapshot_sha256=meta["source_snapshot_sha256"],
                quote_sha256=meta["quote_sha256_by_market"][job["market"]],
            )
            for job in cells
        ]
        return bound_fits, bound_cells, meta

    def _dispatch_fits(jobs: list[dict], max_workers: int, remaining: list[int] | None) -> bool:
        width = min(max_workers, 80)
        for start in range(0, len(jobs), width):
            wave = jobs[start : start + width]
            already = receipts_remote.remote(wave)
            todo = [job for job, row in zip(wave, already) if row is None]
            if not todo:
                continue
            if remaining is not None and remaining[0] == 0:
                print("STAGE1_INCOMPLETE_SAFE_PAUSE: verified fits preserved; rerun same complete command", flush=True)
                return False
            print(f"STAGE1_FIT_WAVE {start // width + 1}: new={len(todo)} workers={width}", flush=True)
            answers = list(fit_remote.with_options(max_containers=width).map(todo))
            require(
                len(answers) == len(todo) and all(x.get("status") == "PASS" for x in answers),
                "STOP_STAGE1_FIT_WAVE_FAILED",
            )
            verified = receipts_remote.remote(todo)
            require(all(x is not None for x in verified), "STOP_STAGE1_FIT_ARTIFACT_UNVERIFIED")
            if remaining is not None:
                remaining[0] -= 1
        return True

    def _dispatch_cells(jobs: list[dict], max_workers: int) -> None:
        width = min(max_workers, 80)
        for start in range(0, len(jobs), width):
            wave = jobs[start : start + width]
            already = receipts_remote.remote(wave)
            todo = [job for job, row in zip(wave, already) if row is None]
            if not todo:
                continue
            print(f"STAGE1_CELL_WAVE {start // width + 1}: new={len(todo)} workers={width}", flush=True)
            answers = list(cell_replay_remote.with_options(max_containers=width).map(todo))
            require(
                len(answers) == len(todo) and all(x.get("status") == "PASS" for x in answers),
                "STOP_STAGE1_CELL_WAVE_FAILED",
            )
            verified = receipts_remote.remote(todo)
            require(all(x is not None for x in verified), "STOP_STAGE1_CELL_ARTIFACT_UNVERIFIED")

    @app.local_entrypoint()
    def main(
        mode: str = "smoke",
        run_id: str = "stage1_m1_20261015_a1",
        max_workers: int = 64,
        max_fit_waves: int = 0,
        stage0_run_id: str = STAGE0_RUN_ID,
        stage0_code_sha: str = STAGE0_CODE_SHA,
    ) -> None:
        """Smoke covers raw20+raw10; complete runs 880 fits + 80 cells + 96 ranking."""
        require(mode in ("smoke", "complete"), "STOP_STAGE1_MODE")
        require(1 <= max_workers <= 80, "STOP_STAGE1_WORKERS")
        require((mode == "smoke" and max_workers == 2) or mode == "complete", "STOP_STAGE1_SMOKE_WORKERS")
        require(isinstance(max_fit_waves, int) and 0 <= max_fit_waves <= 10000, "STOP_STAGE1_WAVE_LIMIT")
        require(mode != "smoke" or max_fit_waves == 0, "STOP_STAGE1_SMOKE_MUST_COMPLETE")
        require(stage0_run_id == STAGE0_RUN_ID and stage0_code_sha == STAGE0_CODE_SHA, "STOP_STAGE1_ANCHOR_LINEAGE")
        sha = _actual_sha()
        prefix_for(run_id, sha)
        meta = {
            "run_id": run_id,
            "code_sha": sha,
            "provider_fingerprint": EXPECTED_PROVIDER_FINGERPRINT,
        }
        inventory = inventory_remote.remote(meta)
        require(inventory["status"] == "PASS", "STOP_STAGE1_INVENTORY")
        fits, cells, full_meta = _bind_all(inventory["calendar"], run_id, sha, inventory)
        require(len(fits) == 880 and len(cells) == 80, "STOP_STAGE1_ROSTER")
        smoke_jobs = [
            next(j for j in fits if j["market"] == "csi300" and j["model_key"] == "m01_59b93929" and j["phase"] == 0),
            next(j for j in fits if j["market"] == "csi500" and j["model_key"] == "m09_raw10d_baseline" and j["phase"] == 0),
        ]
        require(smoke_jobs[0]["label_horizon"] == 20 and smoke_jobs[1]["label_horizon"] == 10, "STOP_STAGE1_SMOKE_HORIZONS")
        if mode == "smoke":
            require(marker_remote.remote(full_meta, "smoke_pass") is None, "STOP_STAGE1_SMOKE_ALREADY_DONE")
            _dispatch_fits(smoke_jobs, max_workers, None)
            require(
                all(row is not None for row in receipts_remote.remote(smoke_jobs)), "STOP_STAGE1_SMOKE_RECEIPT"
            )
            freeze_remote.remote(
                full_meta,
                "smoke_pass",
                {
                    "status": "PASS",
                    "smoke_job_shas": [stable_sha(smoke_jobs[0]), stable_sha(smoke_jobs[1])],
                    "provider_snapshot_sha256": inventory["source"]["snapshot_sha256"],
                    "quote_sha256_by_market": full_meta["quote_sha256_by_market"],
                    "two_real_fits": True,
                    "horizons": [20, 10],
                },
            )
            print("STAGE1_SMOKE_PASS 2/2 fits verified (raw20 + raw10)", flush=True)
            return
        marker = marker_remote.remote(full_meta, "smoke_pass")
        require(
            marker is not None
            and marker["status"] == "PASS"
            and marker["smoke_job_shas"]
            == [stable_sha(smoke_jobs[0]), stable_sha(smoke_jobs[1])]
            and marker["provider_snapshot_sha256"] == inventory["source"]["snapshot_sha256"],
            "STOP_STAGE1_SMOKE_NOT_VERIFIED",
        )
        require(
            all(row is not None for row in receipts_remote.remote(smoke_jobs)), "STOP_STAGE1_SMOKE_RECEIPT_MISSING"
        )
        remaining = [max_fit_waves] if max_fit_waves else None
        if not _dispatch_fits(fits, max_workers, remaining):
            print("STAGE1_SAFE_PAUSE", flush=True)
            return
        require(all(row is not None for row in receipts_remote.remote(fits)), "STOP_STAGE1_FITS_NOT_FULL")
        _dispatch_cells(cells, min(max_workers, 80))
        require(all(row is not None for row in receipts_remote.remote(cells)), "STOP_STAGE1_CELLS_NOT_FULL")
        summary = export_summary_remote.remote(full_meta)
        require(
            summary["status"] == "PASS" and summary["completed_cells"] == 80 and summary["total_cells"] == 96,
            "STOP_STAGE1_SUMMARY_NOT_FULL",
        )
        freeze_remote.remote(full_meta, "stage1_m1_summary", {k: v for k, v in summary.items() if k not in ("run_id", "code_sha")})
        print("STAGE1_M1_COMPLETE 880/880 fits 80/80 cells 96/96 ranking PASS", flush=True)

else:
    # Allows import/py_compile/--help on machines without Modal credentials.
    app = None
