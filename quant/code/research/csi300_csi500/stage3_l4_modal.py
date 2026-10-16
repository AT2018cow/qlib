"""Stage 3 (L4) Modal runner: 660 new-phase fits + 54 replays + five-phase ranking.

Trains exactly the 660 rollout fits of the three remaining reference phases
(4 / 6 / 15) for the 10 (market, model_key) pairs, replays the 54 new
finalist-strategy units from reassembled 424-day signals, merges the 36
verified Stage 0/1/2 phase-(0, 10) units and exports the 90-unit five-phase
scoreboard. The coordinator stays attached in the foreground: detaching,
nohup or shell backgrounding are all forbidden.

Key contracts:
1. ``inventory_remote`` re-verifies the provider snapshot, calendar, both
   quote unions, both benchmarks and the 36 prior units' receipts (via
   ``load_stage2_prior_units`` on the research Volume v2).
2. ``fit_remote`` performs the only genuine model fitting calls; every
   receipt records ``fit_performed=True``.
3. ``l4_cell_replay_remote`` never copies a signal: it reassembles the
   424-day signal from the 22 verified slice predictions and only the first
   ``combo_key`` per (market, model_key) writes the canonical shared
   ``signals/<market>/<model>/phase<pp>/signal.parquet``
   (``STAGE3_ARTIFACTS = {"l4retrain": ("prediction.parquet", "checkpoint.txt"),
   "l4cell": ("fixed_report.parquet", "decisions.json")}``).
4. ``export_summary_remote`` merges 54 new l4cells with the 36 prior units
   into 90 five-phase cells = per market 9 ranked strategies, after checking
   every shared-signal group is byte-consistent with its reassembly.
5. ``--pull-root`` triggers the plan-mandated concurrent artifact download
   (``ThreadPoolExecutor(max_workers=16)``, ``fits/`` deliberately skipped):
   194 selected files + at least 1980 ignored fit files.

Trap compliance (carried over from Stage 0/1/2):
1. Image ignore list excludes .git/.worktrees/.venv/mlruns/__pycache__/
   quant/local_backup/quant/data plus .so/.cpp build artifacts.
2. Image PYTHONPATH covers /root/qlib + quant/code + execution + production +
   stage_ab + layered_search + csi300_csi500 (plan-exact string).
3. Replay uses stage0_p0_core.backtest_market_signal with an explicit
   benchmark (csi300 -> SH000300, csi500 -> SH000905).
4. Training always goes through stage1_m1_core.retrain_splits and
   qlib_audit_fixes.purge_cfg_splits at the job's own label horizon.
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
    raise ValueError("STAGE3_L4_NOREPO: cannot locate repository root")


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

from stage3_l4_core import (  # noqa: E402
    ALL_L4_MODELS,
    EXPECTED_CALENDAR_DAYS,
    EXPECTED_CALENDAR_END,
    EXPECTED_FITS,
    EXPECTED_FIVE_PHASE_UNITS,
    EXPECTED_L4_CELLS,
    EXPECTED_PRIOR_UNITS,
    EXPECTED_PROVIDER_FINGERPRINT,
    EXPECTED_SNAPSHOT_TOKEN,
    MARKETS,
    N_EXECUTION_DAYS,
    NEW_PHASES,
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
    STAGE2_CODE_SHA,
    STAGE2_PREFIX,
    STAGE2_RUN_ID,
    assemble_phase_signal,
    bind_stage3_job,
    build_stage3_cell_jobs,
    build_stage3_fit_jobs,
    checked_receipt,
    fit_job_for_slice,
    is_canonical_signal_writer,
    job_id,
    label_expr,
    load_stage2_prior_units,
    prefix_for,
    publish_receipt,
    quote_sha_of,
    rank_five_phase_strategies,
    receipt_path,
    shared_signal_path,
    signal_relpath,
)
from stage0_p0_core import (  # noqa: E402
    apply_cost_stress,
    backtest_market_signal,
)
from csi1000_stage_b_core import build_phase_jobs  # noqa: E402
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
    app = modal.App("qlib-csi300-csi500-stage3-l4", image=image)
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
        require(snap.is_file(), "STOP_STAGE3_SOURCE: provider snapshot missing")
        row = json.loads(snap.read_text(encoding="utf-8"))
        require(
            row.get("provider_fingerprint") == EXPECTED_PROVIDER_FINGERPRINT,
            "STOP_STAGE3_SOURCE: fingerprint differs from frozen Stage-B",
        )
        require(
            row.get("snapshot_token") == EXPECTED_SNAPSHOT_TOKEN,
            "STOP_STAGE3_SOURCE: snapshot token differs from frozen Stage-B",
        )
        return {
            "provider_fingerprint": row["provider_fingerprint"],
            "snapshot_token": row["snapshot_token"],
            "snapshot_sha256": sha256_file(snap),
        }

    def _task(model_params: dict, market: str, label_horizon: int) -> dict:
        from ruamel.yaml import YAML

        from qlib_live_retrain import apply_lgb_reproducibility

        require(market in MARKETS, "STAGE3_L4 unknown market")
        horizon = int(label_horizon)
        require(horizon in (10, 20), "STAGE3_L4 invalid label_horizon")
        cfg_path = REPO / BASE_YAML
        cfg = YAML(typ="safe", pure=True).load(cfg_path.read_text(encoding="utf-8"))
        ds = cfg["task"]["dataset"]["kwargs"]
        ds["handler"]["kwargs"]["instruments"] = market
        ds["handler"]["kwargs"]["label"] = [label_expr(horizon)]
        cfg["task"]["model"]["kwargs"].update(model_params)
        apply_lgb_reproducibility(cfg)
        require(int(cfg["task"]["model"]["kwargs"]["num_threads"]) == 20, "STOP_STAGE3_MODEL_THREADS")
        cfg["qlib_init"] = {
            "provider_uri": str(SOURCE / "cn_data"),
            "region": "cn",
            "exp_manager": {
                "class": "MLflowExpManager",
                "module_path": "qlib.workflow.expm",
                "kwargs": {"uri": "file:/tmp/stage3_l4_mlflow", "default_exp_name": "qlib-stage3-l4-isolated"},
            },
        }
        return cfg

    def _model_sha(model_params: dict) -> str:
        return stable_sha(_task(model_params, "csi300", 20)["task"]["model"])

    def _prior_digest(units: list[dict]) -> str:
        return stable_sha(
            [
                {
                    "market": unit["market"],
                    "model_key": unit["model_key"],
                    "combo_key": unit["combo_key"],
                    "phase": unit["phase"],
                    "unit_source": unit["unit_source"],
                    "job_sha256": unit["job_sha256"],
                    "receipt_sha256": unit["receipt_sha256"],
                    "source_signal_sha256": unit["source_signal_sha256"],
                }
                for unit in units
            ]
        )

    def _bind_all(calendar: list[str], run_id: str, sha: str, inventory: dict) -> tuple[list[dict], list[dict], dict]:
        fits = build_stage3_fit_jobs(calendar, run_id, sha)
        cells = build_stage3_cell_jobs(run_id, sha)
        meta = {
            "run_id": run_id,
            "code_sha": sha,
            "provider_fingerprint": inventory["source"]["provider_fingerprint"],
            "source_snapshot_sha256": inventory["source"]["snapshot_sha256"],
            "quote_sha256_by_market": {m: inventory["quotes"][m]["quote_sha256"] for m in MARKETS},
            "stage0_prefix": STAGE0_PREFIX,
            "stage1_prefix": STAGE1_PREFIX,
            "stage2_prefix": STAGE2_PREFIX,
            "prior_units_digest": inventory["prior_units_digest"],
        }
        bound_fits = [
            bind_stage3_job(
                job,
                provider_fingerprint=meta["provider_fingerprint"],
                source_snapshot_sha256=meta["source_snapshot_sha256"],
                quote_sha256=meta["quote_sha256_by_market"][job["market"]],
            )
            for job in fits
        ]
        bound_cells = [
            bind_stage3_job(
                job,
                provider_fingerprint=meta["provider_fingerprint"],
                source_snapshot_sha256=meta["source_snapshot_sha256"],
                quote_sha256=meta["quote_sha256_by_market"][job["market"]],
            )
            for job in cells
        ]
        return bound_fits, bound_cells, meta

    def _dispatch_fits(jobs: list[dict], max_workers: int, remaining: list[int] | None) -> bool:
        width = min(max_workers, 66)
        for start in range(0, len(jobs), width):
            wave = jobs[start : start + width]
            already = receipts_remote.remote(wave)
            todo = [job for job, row in zip(wave, already) if row is None]
            if not todo:
                continue
            if remaining is not None and remaining[0] == 0:
                print("STAGE3_SAFE_PAUSE: verified fits preserved; rerun the same complete command", flush=True)
                return False
            print(f"STAGE3_L4RETRAIN_WAVE {start // width + 1}: new={len(todo)} workers={width}", flush=True)
            answers = list(fit_remote.with_options(max_containers=width).map(todo))
            require(
                len(answers) == len(todo) and all(x.get("status") == "PASS" for x in answers),
                "STOP_STAGE3_FIT_WAVE_FAILED",
            )
            verified = receipts_remote.remote(todo)
            require(all(x is not None for x in verified), "STOP_STAGE3_FIT_ARTIFACT_UNVERIFIED")
            if remaining is not None:
                remaining[0] -= 1
        return True

    def _dispatch_cells(jobs: list[dict], max_workers: int) -> None:
        width = min(max_workers, 66)
        for start in range(0, len(jobs), width):
            wave = jobs[start : start + width]
            already = receipts_remote.remote(wave)
            todo = [job for job, row in zip(wave, already) if row is None]
            if not todo:
                continue
            print(f"STAGE3_L4CELL_WAVE {start // width + 1}: new={len(todo)} workers={width}", flush=True)
            answers = list(l4_cell_replay_remote.with_options(max_containers=width).map(todo))
            require(
                len(answers) == len(todo) and all(x.get("status") == "PASS" for x in answers),
                "STOP_STAGE3_CELL_WAVE_FAILED",
            )
            verified = receipts_remote.remote(todo)
            require(all(x is not None for x in verified), "STOP_STAGE3_CELL_ARTIFACT_UNVERIFIED")

    def _pull_run_prefix(meta: dict, dest_root: str) -> dict:
        """Plan-mandated concurrent artifact pull (16 threads); fits/ skipped."""
        import os as _os
        import threading
        from concurrent.futures import ThreadPoolExecutor

        from modal.volume import FileEntryType

        prefix = prefix_for(meta["run_id"], meta["code_sha"])
        volume = modal.Volume.from_name(RESEARCH_VOLUME, create_if_missing=False, version=2)
        entries = volume.listdir(prefix, recursive=True)
        files = [entry for entry in entries if entry.type == FileEntryType.FILE]
        require(bool(files), "STOP_STAGE3_PULL_EMPTY")
        selected = [entry for entry in files if not str(entry.path)[len(prefix) + 1 :].startswith("fits/")]
        ignored_fits = len(files) - len(selected)
        require(len(selected) == 194, f"STOP_STAGE3_PULL_COUNT: {len(selected)} selected, need 194")
        require(ignored_fits >= 1980, f"STOP_STAGE3_PULL_FITS: only {ignored_fits} ignored fit files")
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
            list(pool.map(_fetch, selected))
        require(not state["failed"], f"STOP_STAGE3_PULL_FAILED: {state['failed'][:3]}")
        return {
            "dest": str(dest),
            "files": len(selected),
            "ignored_fits": ignored_fits,
            "downloaded": state["downloaded"],
            "skipped": state["skipped"],
        }

    @app.function(volumes=mounts, cpu=2, memory=4096, timeout=1200, retries=0)
    def inventory_remote(meta: dict) -> dict:
        """Verify provider, calendar, quotes, benchmarks and the 36 prior units."""
        import qlib
        from qlib.data import D
        from qlib_audit_fixes import read_trading_calendar
        from audit.csi1000_layered_search_replay import static_quote_union

        src = _source_checks()
        require(
            src["provider_fingerprint"] == meta["provider_fingerprint"],
            "STOP_STAGE3_SOURCE: fingerprint identity drift",
        )
        qlib.init(provider_uri=str(SOURCE / "cn_data"), region="cn")
        calendar = read_trading_calendar(SOURCE / "cn_data")
        require(len(calendar) == EXPECTED_CALENDAR_DAYS, "STOP_STAGE3_CALENDAR: day count drift")
        require(calendar[-1] == EXPECTED_CALENDAR_END, "STOP_STAGE3_CALENDAR: cutoff drift")
        sessions = D.calendar(start_time=RESERVED_EXECUTION_START, end_time=RESERVED_EXECUTION_END)
        require(len(sessions) == N_EXECUTION_DAYS, "STOP_STAGE3_EXECUTION_WINDOW")
        quotes = {}
        for market, spec in MARKETS.items():
            codes, quote_sha = static_quote_union(market, RESERVED_EXECUTION_START, RESERVED_EXECUTION_END, D)
            require(len(codes) == spec["expected_quote_count"], f"STOP_STAGE3_QUOTE_COUNT: {market}")
            require(quote_sha == spec["expected_quote_sha256"], f"STOP_STAGE3_QUOTE_SHA: {market}")
            require(quote_sha == quote_sha_of(codes), f"STOP_STAGE3_QUOTE_RECOMPUTE: {market}")
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
            require(len(values) == N_EXECUTION_DAYS, f"STOP_STAGE3_BENCH_COVERAGE: {spec['benchmark']}")
            require(bool((values > 0).all()), f"STOP_STAGE3_BENCH_POSITIVE: {spec['benchmark']}")
        prior_units = load_stage2_prior_units(RESULTS)
        require(len(prior_units) == EXPECTED_PRIOR_UNITS, "STOP_STAGE3_PRIOR_COUNT")
        return {
            "status": "PASS",
            "source": src,
            "calendar": calendar,
            "quotes": quotes,
            "calendar_days": len(calendar),
            "calendar_end": calendar[-1],
            "prior_units": len(prior_units),
            "prior_units_digest": _prior_digest(prior_units),
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
        require(job.get("kind") == "l4retrain", "STAGE3_L4 fit supports l4retrain only")
        source = _source_checks()
        require(
            source["provider_fingerprint"] == identity["provider_fingerprint"]
            and source["snapshot_sha256"] == identity["source_snapshot_sha256"],
            "STOP_STAGE3_SOURCE_DRIFT",
        )
        spec = ALL_L4_MODELS[job["model_key"]]
        require(job["candidate_id"] == spec["candidate_id"], "STAGE3_L4 model ID mismatch")
        require(job["source_stage"] == spec["source_stage"], "STAGE3_L4 source stage mismatch")
        require(int(job["label_horizon"]) == int(spec["label_horizon"]), "STAGE3_L4 horizon mismatch")
        require(job["benchmark"] == MARKETS[job["market"]]["benchmark"], "STAGE3_L4 market/benchmark mismatch")
        config = _task(spec["model_params"], job["market"], spec["label_horizon"])
        require(stable_sha(config["task"]["model"]) == spec["model_sha256"], "STAGE3_L4 model config SHA mismatch")
        require(_model_sha(spec["model_params"]) == spec["model_sha256"], "STAGE3_L4 model SHA recompute mismatch")
        qlib.init(**{**config["qlib_init"], "skip_if_reg": True})
        calendar = read_trading_calendar(SOURCE / "cn_data")
        from csi1000_stage_b_core import build_phase_jobs

        horizon = int(job["label_horizon"])
        require(horizon in (10, 20), "STOP_STAGE3_HORIZON")
        legal = build_phase_jobs(calendar, job["phase"])
        require(
            any(p["retrain_asof"] == job["retrain_asof"] and p["signal_end"] == job["signal_end"] for p in legal),
            "STOP_STAGE3_LONG_CALENDAR",
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
        require(not prediction.empty, "STOP_STAGE3_EMPTY_PREDICTION")
        observed = [
            str(prediction.index.get_level_values(0).min())[:10],
            str(prediction.index.get_level_values(0).max())[:10],
        ]
        require(observed == [job["retrain_asof"], job["signal_end"]], "STOP_STAGE3_PREDICTION_COVERAGE")
        import numpy as np

        require(
            prediction.index.is_unique
            and prediction.notna().all().all()
            and np.isfinite(prediction.to_numpy(dtype=float)).all(),
            "STOP_STAGE3_SIGNAL_SCHEMA",
        )
        booster = getattr(model, "model", None)
        require(booster is not None and hasattr(booster, "model_to_string"), "STOP_STAGE3_NO_TRAINED_BOOSTER")
        checkpoint = booster.model_to_string()
        require(checkpoint and len(checkpoint) > 100, "STOP_STAGE3_EMPTY_CHECKPOINT")
        artifacts = {"prediction.parquet": prediction.to_frame("score"), "checkpoint.txt": checkpoint}
        details = {
            "market": job["market"],
            "benchmark": job["benchmark"],
            "model_key": job["model_key"],
            "candidate_id": job["candidate_id"],
            "model_config_sha256": spec["model_sha256"],
            "label_horizon": horizon,
            "source_stage": spec["source_stage"],
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
    def l4_cell_replay_remote(job: dict) -> dict:
        """Assemble one 424-day new-phase signal from 22 verified fits and replay it."""
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
        require(job.get("kind") == "l4cell", "STAGE3_L4 replay supports l4cell only")
        src = _source_checks()
        require(
            src["provider_fingerprint"] == identity["provider_fingerprint"]
            and src["snapshot_sha256"] == identity["source_snapshot_sha256"],
            "STOP_STAGE3_SOURCE_DRIFT",
        )
        require(job["benchmark"] == MARKETS[job["market"]]["benchmark"], "STAGE3_L4 market/benchmark mismatch")
        require(
            bool(job.get("canonical_signal_writer"))
            == is_canonical_signal_writer(job["market"], job["model_key"], job["combo_key"]),
            "STOP_STAGE3_WRITER_FLAG_DRIFT",
        )
        qlib.init(provider_uri=str(SOURCE / "cn_data"), region="cn")
        calendar = read_trading_calendar(SOURCE / "cn_data")
        fits = [
            bind_stage3_job(
                raw,
                provider_fingerprint=identity["provider_fingerprint"],
                source_snapshot_sha256=identity["source_snapshot_sha256"],
                quote_sha256=identity["quote_sha256"],
            )
            for raw in build_stage3_fit_jobs(calendar, job["run_id"], job["code_sha"])
        ]
        assembled = assemble_phase_signal(root, calendar, fits, job, identity)
        signal = assembled["signal"]["score"]
        actual_days = sorted({str(v)[:10] for v in signal.index.get_level_values(0).unique()})
        first = calendar.index(RESERVED_SIGNAL_ANCHOR)
        last = calendar.index(RESERVED_EXECUTION_END)
        require(actual_days == calendar[first:last], "STOP_STAGE3_SIGNAL_DAY_COVERAGE")
        relpath = signal_relpath(job["market"], job["model_key"], int(job["phase"]))
        signal_written = False
        if job["canonical_signal_writer"]:
            shared = root / relpath
            shared.parent.mkdir(parents=True, exist_ok=True)
            tmp_shared = shared.parent / (shared.name + ".tmp")
            require(not tmp_shared.exists(), "STOP_STAGE3_SHARED_SIGNAL_TEMP")
            frame = signal.to_frame("score")
            if shared.is_file():
                require(pd.read_parquet(shared)["score"].equals(frame["score"]), "STOP_STAGE3_SHARED_SIGNAL_DRIFT")
            else:
                frame.to_parquet(tmp_shared)
                import os as _os

                _os.replace(tmp_shared, shared)
            signal_written = True
        codes, quote_sha = static_quote_union(job["market"], RESERVED_EXECUTION_START, RESERVED_EXECUTION_END, D)
        require(quote_sha == job["quote_sha256"], "STOP_STAGE3_QUOTE_SHA_DRIFT")
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
        require(len(report) == N_EXECUTION_DAYS and report.index.is_unique, "STOP_STAGE3_424_DAY_REPORT")
        require(len(decisions) == N_EXECUTION_DAYS, "STOP_STAGE3_424_DAY_DECISIONS")
        require(
            diagnostics["max_account_return_delta"] <= 1e-10 and diagnostics["max_cost_rate_delta"] <= 1e-10,
            "STOP_STAGE3_NAV_ACCOUNT_DRIFT",
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
            "role": job["role"],
            "canonical_signal_writer": bool(job["canonical_signal_writer"]),
            "signal_relpath": relpath,
            "signal_written": signal_written,
            "source_prediction_shas": assembled["source_prediction_shas"],
            "fit_slice_job_sha256s": assembled["slice_job_shas"],
            "model_config_sha256": ALL_L4_MODELS[job["model_key"]]["model_sha256"],
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

    @app.function(volumes=mounts, cpu=2, memory=4096, timeout=1800, retries=0)
    def export_summary_remote(meta: dict) -> dict:
        """Merge 54 new l4cells + 36 prior units into 90 five-phase cells."""
        import pandas as pd
        import qlib
        from qlib_audit_fixes import read_trading_calendar

        research.reload()
        root = RESULTS / prefix_for(meta["run_id"], meta["code_sha"])
        qlib.init(provider_uri=str(SOURCE / "cn_data"), region="cn")
        calendar = read_trading_calendar(SOURCE / "cn_data")
        fit_jobs = [
            bind_stage3_job(
                raw,
                provider_fingerprint=meta["provider_fingerprint"],
                source_snapshot_sha256=meta["source_snapshot_sha256"],
                quote_sha256=meta["quote_sha256_by_market"][raw["market"]],
            )
            for raw in build_stage3_fit_jobs(calendar, meta["run_id"], meta["code_sha"])
        ]
        cell_jobs = [
            bind_stage3_job(
                raw,
                provider_fingerprint=meta["provider_fingerprint"],
                source_snapshot_sha256=meta["source_snapshot_sha256"],
                quote_sha256=meta["quote_sha256_by_market"][raw["market"]],
            )
            for raw in build_stage3_cell_jobs(meta["run_id"], meta["code_sha"])
        ]
        for job in fit_jobs:
            rec = checked_receipt(root, job, _identity(job))
            require(rec is not None, f"STOP_STAGE3_MISSING_FIT: {job_id(job)}")
            require(rec.get("fit_performed") is True, f"STOP_STAGE3_FIT_NOT_PERFORMED: {job_id(job)}")
        cells = []
        for job in cell_jobs:
            rec = checked_receipt(root, job, _identity(job))
            require(rec is not None, f"STOP_STAGE3_MISSING_CELL: {job_id(job)}")
            cells.append(
                {
                    "market": job["market"],
                    "benchmark": job["benchmark"],
                    "model_key": job["model_key"],
                    "candidate_id": job["candidate_id"],
                    "label_horizon": int(job["label_horizon"]),
                    "source_stage": job["source_stage"],
                    "unit_source": "stage3_l4_new",
                    "combo_key": job["combo_key"],
                    "topk": job["topk"],
                    "n_drop": job["n_drop"],
                    "role": job["role"],
                    "canonical_signal_writer": bool(job["canonical_signal_writer"]),
                    "phase": job["phase"],
                    "metrics": rec["metrics"],
                    "stressed": rec["stressed"],
                    "artifact_sha256": rec["artifact_sha256"],
                    "job_sha256": rec["job_sha256"],
                    "receipt_sha256": sha256_file(receipt_path(root, job) / "receipt.json"),
                    "source_prediction_shas": rec["source_prediction_shas"],
                    "signal_relpath": rec["signal_relpath"],
                    "signal_written": rec["signal_written"],
                    "source_signal_sha256": "",
                }
            )
        require(len(cells) == EXPECTED_L4_CELLS, "STOP_STAGE3_SUMMARY_COVERAGE")
        prior = load_stage2_prior_units(RESULTS)
        require(len(prior) == EXPECTED_PRIOR_UNITS, "STOP_STAGE3_PRIOR_COVERAGE")
        require(
            _prior_digest(sorted(prior, key=lambda r: (r["market"], r["model_key"], r["combo_key"], r["phase"])))
            == meta["prior_units_digest"],
            "STOP_STAGE3_PRIOR_DIGEST_DRIFT",
        )
        all_cells = sorted(
            cells + prior, key=lambda row: (row["market"], row["model_key"], row["combo_key"], row["phase"])
        )
        require(len(all_cells) == EXPECTED_FIVE_PHASE_UNITS, "STOP_STAGE3_90_COVERAGE")
        cell_job_by_key = {(j["market"], j["model_key"], j["combo_key"], j["phase"]): j for j in cell_jobs}
        signals = {}
        for market in MARKETS:
            for model_key in ALL_L4_MODELS:
                for phase in NEW_PHASES:
                    group = [
                        c for c in cells if (c["market"], c["model_key"], c["phase"]) == (market, model_key, phase)
                    ]
                    if not group:
                        continue
                    sha_sets = {tuple(c["source_prediction_shas"]) for c in group}
                    require(len(sha_sets) == 1, f"STOP_STAGE3_GROUP_SHA_DRIFT: {market}/{model_key}/phase{phase}")
                    writers = [c for c in group if c["canonical_signal_writer"]]
                    require(len(writers) == 1, f"STOP_STAGE3_GROUP_WRITER_DRIFT: {market}/{model_key}/phase{phase}")
                    require(writers[0]["signal_written"] is True, "STOP_STAGE3_SIGNAL_NOT_WRITTEN")
                    shared = root / writers[0]["signal_relpath"]
                    require(shared.is_file(), f"STOP_STAGE3_SHARED_SIGNAL_MISSING: {market}/{model_key}")
                    writer_job = cell_job_by_key[(market, model_key, writers[0]["combo_key"], phase)]
                    assembled = assemble_phase_signal(root, calendar, fit_jobs, writer_job, _identity(writer_job))
                    require(
                        tuple(assembled["source_prediction_shas"]) == tuple(writers[0]["source_prediction_shas"]),
                        "STOP_STAGE3_GROUP_ASSEMBLY_SHA_DRIFT",
                    )
                    frame = pd.read_parquet(shared)["score"]
                    require(
                        frame.equals(assembled["signal"]["score"]),
                        f"STOP_STAGE3_SHARED_SIGNAL_DRIFT: {market}/{model_key}/phase{phase}",
                    )
                    signals[f"{market}/{model_key}/phase{phase:02d}"] = {
                        "relpath": writers[0]["signal_relpath"],
                        "sha256": sha256_file(shared),
                        "source_prediction_shas": list(assembled["source_prediction_shas"]),
                        "fit_slice_job_sha256s": list(assembled["slice_job_shas"]),
                    }
        require(len(signals) == 30, f"STOP_STAGE3_SIGNAL_GROUPS: {len(signals)} != 30")
        rankings = {
            market: rank_five_phase_strategies([c for c in all_cells if c["market"] == market]) for market in MARKETS
        }
        require(all(len(rows) == 9 for rows in rankings.values()), "STOP_STAGE3_RANKING_COVERAGE")
        summary = {
            "status": "PASS",
            "stage": "stage3_l4",
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
            "stage2_run_id": STAGE2_RUN_ID,
            "stage2_code_sha": STAGE2_CODE_SHA,
            "stage2_prefix": meta.get("stage2_prefix", STAGE2_PREFIX),
            "prior_units_digest": meta["prior_units_digest"],
            "expected_fits": EXPECTED_FITS,
            "completed_fits": len(fit_jobs),
            "expected_l4_cells": EXPECTED_L4_CELLS,
            "completed_l4_cells": len(cells),
            "prior_units": len(prior),
            "total_cells": len(all_cells),
            "new_fits": EXPECTED_FITS,
            "cells": all_cells,
            "signals": signals,
            "rankings": rankings,
        }
        path = root / "stage3_l4_summary.json"
        tmp = path.with_suffix(".tmp")
        require(not tmp.exists(), "STOP_STAGE3_SUMMARY_TEMP")
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
        require(key in ("smoke_pass", "stage3_l4_summary"), "STOP_STAGE3_MARKER_KIND")
        research.reload()
        require(bool(re.fullmatch(r"[a-f0-9]{40}", meta.get("code_sha") or "")), "STOP_STAGE3_MARKER_IDENTITY")
        root = RESULTS / prefix_for(meta["run_id"], meta["code_sha"])
        root.mkdir(parents=True, exist_ok=True)
        path = root / (key + ".json")
        data = {"run_id": meta["run_id"], "code_sha": meta["code_sha"], **payload}
        require("run_id" not in payload and "code_sha" not in payload, "STOP_STAGE3_MARKER_IDENTITY_OVERRIDE")
        if path.exists():
            require(json.loads(path.read_text(encoding="utf-8")) == data, "STOP_STAGE3_MARKER_CONFLICT")
        else:
            tmp = path.with_suffix(".tmp")
            require(not tmp.exists(), "STOP_STAGE3_PARTIAL_MARKER")
            tmp.write_text(json.dumps(data, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
            tmp.replace(path)
            research.commit()
        return data

    @app.function(volumes={str(RESULTS): research}, cpu=1, memory=1024, timeout=600, retries=0)
    def marker_remote(meta: dict, key: str) -> dict | None:
        require(key in ("smoke_pass", "stage3_l4_summary"), "STOP_STAGE3_MARKER_KIND")
        research.reload()
        path = RESULTS / prefix_for(meta["run_id"], meta["code_sha"]) / (key + ".json")
        if not path.is_file():
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
        require(
            record.get("run_id") == meta["run_id"] and record.get("code_sha") == meta["code_sha"],
            "STOP_STAGE3_MARKER_IDENTITY",
        )
        return record

    def _actual_sha() -> str:
        repo = _find_repo_root()
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, cwd=str(repo)).strip()
        require(
            not subprocess.check_output(["git", "status", "--porcelain"], text=True, cwd=str(repo)).strip(),
            "STOP_STAGE3_UNCLEAN_WORKTREE",
        )
        require(bool(re.fullmatch(r"[a-f0-9]{40}", sha or "")), "STOP_STAGE3_INVALID_HEAD")
        return sha

    @app.local_entrypoint()
    def main(
        mode: str = "smoke",
        run_id: str = "stage3_l4_20261016_a1",
        max_workers: int = 66,
        max_fit_waves: int = 0,
        pull_root: str = "",
    ) -> None:
        """Smoke fits 2 phase-4 slices; complete runs 660 fits + 54 cells + 90 units."""
        require(mode in ("smoke", "complete"), "STOP_STAGE3_MODE")
        require(1 <= max_workers <= 66, "STOP_STAGE3_WORKERS")
        require((mode == "smoke" and max_workers == 2) or mode == "complete", "STOP_STAGE3_SMOKE_WORKERS")
        require(isinstance(max_fit_waves, int) and 0 <= max_fit_waves <= 10000, "STOP_STAGE3_WAVE_LIMIT")
        require(mode != "smoke" or max_fit_waves == 0, "STOP_STAGE3_SMOKE_MUST_COMPLETE")
        sha = _actual_sha()
        prefix_for(run_id, sha)
        meta = {"run_id": run_id, "code_sha": sha, "provider_fingerprint": EXPECTED_PROVIDER_FINGERPRINT}
        inventory = inventory_remote.remote(meta)
        require(inventory["status"] == "PASS", "STOP_STAGE3_INVENTORY")
        fits, cells, full_meta = _bind_all(inventory["calendar"], run_id, sha, inventory)
        require(len(fits) == EXPECTED_FITS and len(cells) == EXPECTED_L4_CELLS, "STOP_STAGE3_ROSTER")
        first_asof = build_phase_jobs(inventory["calendar"], 4)[0]["retrain_asof"]
        smoke_jobs = [
            fit_job_for_slice(fits, "csi300", "m03_4e908173", 4, first_asof),
            fit_job_for_slice(fits, "csi500", "m10_raw10d_c98856b4", 4, first_asof),
        ]
        require(
            smoke_jobs[0]["label_horizon"] == 20 and smoke_jobs[1]["label_horizon"] == 10,
            "STOP_STAGE3_SMOKE_HORIZONS",
        )
        smoke_shas = [stable_sha(job) for job in smoke_jobs]
        if mode == "smoke":
            require(marker_remote.remote(full_meta, "smoke_pass") is None, "STOP_STAGE3_SMOKE_ALREADY_DONE")
            _dispatch_fits(smoke_jobs, max_workers, None)
            require(all(row is not None for row in receipts_remote.remote(smoke_jobs)), "STOP_STAGE3_SMOKE_RECEIPT")
            freeze_remote.remote(
                full_meta,
                "smoke_pass",
                {
                    "status": "PASS",
                    "smoke_fit_job_shas": smoke_shas,
                    "provider_snapshot_sha256": full_meta["source_snapshot_sha256"],
                    "quote_sha256_by_market": full_meta["quote_sha256_by_market"],
                    "prior_units_digest": full_meta["prior_units_digest"],
                    "fit_performed": True,
                    "zero_cells": True,
                    "horizons": [20, 10],
                },
            )
            print("STAGE3_SMOKE_PASS 2/2 new-phase fits verified (phase4 first slices)", flush=True)
            return
        marker = marker_remote.remote(full_meta, "smoke_pass")
        require(
            marker is not None
            and marker["status"] == "PASS"
            and marker["smoke_fit_job_shas"] == smoke_shas
            and marker["provider_snapshot_sha256"] == full_meta["source_snapshot_sha256"]
            and marker.get("prior_units_digest") == full_meta["prior_units_digest"]
            and marker.get("fit_performed") is True
            and marker.get("zero_cells") is True,
            "STOP_STAGE3_SMOKE_NOT_VERIFIED",
        )
        require(all(row is not None for row in receipts_remote.remote(smoke_jobs)), "STOP_STAGE3_SMOKE_RECEIPT_MISSING")
        remaining = [max_fit_waves] if max_fit_waves else None
        if not _dispatch_fits(fits, max_workers, remaining):
            print("STAGE3_SAFE_PAUSE", flush=True)
            return
        require(all(row is not None for row in receipts_remote.remote(fits)), "STOP_STAGE3_FITS_NOT_FULL")
        _dispatch_cells(cells, min(max_workers, 66))
        require(all(row is not None for row in receipts_remote.remote(cells)), "STOP_STAGE3_CELLS_NOT_FULL")
        summary = export_summary_remote.remote(full_meta)
        require(
            summary["status"] == "PASS"
            and summary["completed_fits"] == EXPECTED_FITS
            and summary["completed_l4_cells"] == EXPECTED_L4_CELLS
            and summary["prior_units"] == EXPECTED_PRIOR_UNITS
            and summary["total_cells"] == EXPECTED_FIVE_PHASE_UNITS,
            "STOP_STAGE3_SUMMARY_NOT_FULL",
        )
        require(all(len(summary["rankings"][m]) == 9 for m in MARKETS), "STOP_STAGE3_RANKING_NOT_FULL")
        if pull_root:
            pulled = _pull_run_prefix(full_meta, pull_root)
            print(
                f"STAGE3_PULL_OK {pulled['files']} files ({pulled['ignored_fits']} fits ignored, "
                f"{pulled['downloaded']} new, {pulled['skipped']} existing) -> {pulled['dest']}",
                flush=True,
            )
        print("STAGE3_L4_COMPLETE 660/660 new-phase fits 54/54 new cells 90/90 five-phase units PASS", flush=True)

else:
    # Allows import/py_compile/--help on machines without Modal credentials.
    app = None
