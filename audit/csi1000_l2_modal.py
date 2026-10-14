"""CSI1000 L2 actual-fit Modal runner: gated smoke -> attached auto-waves.

NO model fitting is performed by importing this module or by --help. Source
Volume is mounted read-only and artifacts go to an independent research Volume.
The coordinator is always local/attached; there is no --detach path.
"""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
from pathlib import Path
from typing import Any

from audit.csi1000_l2_execution import (
    budget_gate, checked_receipt, choose_new_for_long, job_id, prefix_for,
    publish_receipt,
)
from audit.csi1000_l2_fastlane import make_fastlane_plan, make_long_job_roster
from audit.csi1000_layered_search_core import (
    RESEARCH_VOLUME, SOURCE_VOLUME, require, sha256_file, stable_sha,
)
from audit.csi1000_layered_search_core import enumerate_cells, require_p0_pass, verify_export_cells
from audit.csi1000_layered_search_selection import FROZEN_STAGE_A, load_json

try:
    import modal
except ImportError:
    modal = None

SOURCE = Path("/source")
RESULTS = Path("/results")
REPO = Path("/root/qlib")

if modal is not None:
    original = modal.Volume.from_name(SOURCE_VOLUME, create_if_missing=False)
    research = modal.Volume.from_name(RESEARCH_VOLUME, create_if_missing=False)
    image = (
        modal.Image.debian_slim(python_version="3.11")
        .apt_install("build-essential")
        .env({"PYTHONPATH": "/root/qlib:/root", "MLFLOW_ALLOW_FILE_STORE": "true"})
        .pip_install(
            "numpy==1.26.4", "pandas==2.2.3", "pyarrow==17.0.0", "lightgbm==4.7.0",
            "cython", "setuptools-scm", "pyyaml", "ruamel.yaml", "fire", "mlflow",
            "dill", "filelock", "tqdm", "loguru", "joblib", "pydantic-settings",
            "redis", "python-redis-lock", "pymongo", "gym", "cvxpy", "matplotlib", "torch",
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
    app = modal.App("qlib-csi1000-l2-fastlane", image=image)
    mounts = {str(SOURCE): original.with_mount_options(read_only=True), str(RESULTS): research}

    def _identity(job: dict) -> dict:
        return {k: job[k] for k in (
            "run_id", "code_sha", "plan_sha256", "provider_fingerprint",
            "source_snapshot_sha256", "quote_sha256", "p0_prefix",
        )}

    def _root(job: dict) -> Path:
        return RESULTS / prefix_for(job["run_id"], job["code_sha"])

    def _source_checks() -> dict:
        from audit.csi1000_layered_search_replay import load_frozen_result
        frozen = load_frozen_result(REPO)["manifest"]
        snap = SOURCE / "csi1000_stage_b" / "provider_snapshot.json"
        require(snap.is_file(), "STOP_L2_SOURCE: provider snapshot missing")
        row = json.loads(snap.read_text(encoding="utf-8"))
        for key in ("snapshot_token", "provider_fingerprint"):
            require(row[key] == frozen[key], f"STOP_L2_SOURCE: {key} differs from Stage-B")
        return {"provider_fingerprint": frozen["provider_fingerprint"],
                "snapshot_sha256": sha256_file(snap), "snapshot_token": frozen["snapshot_token"]}

    def _task(params: dict) -> dict:
        from ruamel.yaml import YAML
        from qlib_live_retrain import apply_lgb_reproducibility
        from csi1000_tuner_core import build_candidate_spec

        built = build_candidate_spec(params)
        cfg_path = REPO / "examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158_csi500.yaml"
        cfg = YAML(typ="safe", pure=True).load(cfg_path.read_text(encoding="utf-8"))
        ds = cfg["task"]["dataset"]["kwargs"]
        ds["handler"]["kwargs"]["instruments"] = "csi1000"
        ds["handler"]["kwargs"]["label"] = ["Ref($close, -20)/$close - 1"]
        cfg["task"]["model"]["kwargs"].update(params)
        apply_lgb_reproducibility(cfg)
        require(int(cfg["task"]["model"]["kwargs"]["num_threads"]) == 20, "STOP_L2_MODEL_THREADS")
        cfg["qlib_init"] = {
            "provider_uri": str(SOURCE / "cn_data"), "region": "cn",
            "exp_manager": {
                "class": "MLflowExpManager", "module_path": "qlib.workflow.expm",
                "kwargs": {"uri": "file:/tmp/l2_mlflow", "default_exp_name": "qlib-l2-isolated"},
            },
        }
        return cfg, built

    @app.function(volumes=mounts, cpu=2, memory=4096, timeout=900, retries=0)
    def inventory_remote(meta: dict) -> dict:
        """Resolve the *live* source identities and exact old training calendar."""
        import qlib
        from qlib_audit_fixes import read_trading_calendar
        from csi1000_tuner_core import build_stage_a_folds
        from csi1000_stage_b_core import build_phase_jobs, MARKET, RESERVED_EXECUTION_END, RESERVED_EXECUTION_START
        from audit.csi1000_layered_search_replay import static_quote_union
        from qlib.data import D

        src = _source_checks()
        require(src["provider_fingerprint"] == meta["provider_fingerprint"],
                "STOP_L2_SOURCE: frozen provider fingerprint drift")
        qlib.init(provider_uri=str(SOURCE / "cn_data"), region="cn")
        calendar = read_trading_calendar(SOURCE / "cn_data")
        folds = build_stage_a_folds(calendar)
        periods = {str(p): build_phase_jobs(calendar, p) for p in (0, 10)}
        codes, quote_sha = static_quote_union(MARKET, RESERVED_EXECUTION_START, RESERVED_EXECUTION_END, D)
        require(bool(codes), "STOP_L2_QUOTE: empty static union")
        return {"source": src, "folds": folds, "calendar": calendar,
                "long_periods": periods, "quote_sha256": quote_sha}

    @app.function(volumes=mounts, cpu=8, memory=16384, timeout=7200, retries=0)
    def fit_remote(job: dict) -> dict:
        """One genuine LightGBM fit, exactly one as-of. No automatic retry."""
        import bisect
        import qlib
        import pandas as pd
        from qlib.model.base import Model
        from qlib.data.dataset import Dataset
        from qlib.utils import init_instance_by_config
        from qlib_audit_fixes import last_matured_sample, purge_cfg_splits, read_trading_calendar

        research.reload()
        identity = _identity(job)
        root = _root(job)
        cached = checked_receipt(root, job, identity)
        if cached is not None:
            return {**cached, "resumed": True}
        source = _source_checks()
        require(source["provider_fingerprint"] == identity["provider_fingerprint"] and
                source["snapshot_sha256"] == identity["source_snapshot_sha256"], "STOP_L2_SOURCE_DRIFT")
        require(job["kind"] in ("short", "long"), "L2 fit supports short/long only")
        config, built = _task(job["model_params"])
        require(built["candidate_id"] == job["candidate_id"], "L2 model ID mismatch")
        require(stable_sha(config["task"]["model"]) == job["model_sha256"], "L2 model config SHA mismatch")
        qlib.init(**{**config["qlib_init"], "skip_if_reg": True})
        calendar = read_trading_calendar(SOURCE / "cn_data")
        ds = config["task"]["dataset"]["kwargs"]
        segments, handler = ds["segments"], ds["handler"]["kwargs"]
        if job["kind"] == "short":
            from csi1000_tuner_core import build_stage_a_folds
            fold = next(x for x in build_stage_a_folds(calendar) if x["fold_id"] == job["fold_id"])
            require(fold == job["fold"], "STOP_L2_FOLD_GEOMETRY")
            segments.update({"train": list(fold["train"]), "valid": list(fold["valid"]),
                             "test": list(fold["signal"])})
            handler["start_time"] = "2015-01-01"
            handler["end_time"] = fold["signal"][1]
            handler["fit_start_time"], handler["fit_end_time"] = fold["train"]
        else:
            from csi1000_stage_b_core import (
                LABEL_HORIZON, VALIDATION_SESSIONS, TRAIN_START, build_phase_jobs,
            )
            legal = build_phase_jobs(calendar, job["phase"])
            require(any(p["retrain_asof"] == job["retrain_asof"] and
                        p["signal_end"] == job["signal_end"] for p in legal),
                    "STOP_L2_LONG_CALENDAR")
            asof = bisect.bisect_left(calendar, job["retrain_asof"])
            require(asof < len(calendar) and calendar[asof] == job["retrain_asof"], "L2 as-of missing")
            valid_end = asof - LABEL_HORIZON - 1
            valid_start = valid_end - VALIDATION_SESSIONS + 1
            require(valid_start > 0, "STOP_L2_LABEL_MATURITY")
            train_end = last_matured_sample(calendar, calendar[valid_start], LABEL_HORIZON)
            segments.update({"train": [TRAIN_START, train_end],
                             "valid": [calendar[valid_start], calendar[valid_end]],
                             "test": [job["retrain_asof"], job["signal_end"]]})
            handler["start_time"], handler["end_time"] = "2015-01-01", job["signal_end"]
            handler["fit_start_time"], handler["fit_end_time"] = TRAIN_START, train_end
        purge_cfg_splits(config, calendar, horizon=20)
        model = init_instance_by_config(config["task"]["model"], accept_types=Model)
        dataset = init_instance_by_config(config["task"]["dataset"], accept_types=Dataset)
        model.fit(dataset)  # the ONLY new fit call for this job
        prediction = model.predict(dataset, segment="test")
        require(not prediction.empty, "STOP_L2_EMPTY_PREDICTION")
        observed = [str(prediction.index.get_level_values(0).min())[:10],
                    str(prediction.index.get_level_values(0).max())[:10]]
        expected = job["fold"]["signal"] if job["kind"] == "short" else [job["retrain_asof"], job["signal_end"]]
        require(observed == expected, "STOP_L2_PREDICTION_COVERAGE")
        require(prediction.index.is_unique and prediction.notna().all(), "STOP_L2_SIGNAL_SCHEMA")
        booster = getattr(model, "model", None)
        require(booster is not None and hasattr(booster, "model_to_string"),
                "STOP_L2_NO_TRAINED_BOOSTER")
        checkpoint = booster.model_to_string()
        require(checkpoint and len(checkpoint) > 100, "STOP_L2_EMPTY_CHECKPOINT")
        artifacts = {"prediction.parquet": prediction.to_frame("score"),
                     "checkpoint.txt": checkpoint}
        details = {"candidate_id": job["candidate_id"],
                   "model_config_sha256": job["model_sha256"],
                   "best_iteration": int(booster.best_iteration or 0),
                   "fit_performed": True}
        if job["kind"] == "short":
            from audit.csi1000_layered_search_replay import backtest_signal
            from portfolio_performance import portfolio_performance
            from csi1000_tuner_core import METRIC_VERSION

            f = job["fold"]
            report, decisions = backtest_signal(
                prediction, 20, 2, "csi1000", f["execution"][0], f["execution"][1]
            )
            metrics = portfolio_performance(
                report, initial_cash=100_000_000, backtest_start=f["execution"][0]
            )
            col = "turnover" if "turnover" in report.columns else "total_turnover"
            require(col in report and "cost" in report, "STOP_L2_SHORT_METRICS_MISSING")
            metrics["mean_turnover"] = round(float(report[col].dropna().mean()), 6)
            metrics["total_cost_sum"] = round(float(report["cost"].dropna().sum()), 6)
            require(metrics["metric_version"] == METRIC_VERSION, "STOP_L2_SHORT_METRIC_VERSION")
            require(abs(metrics["account_return_max_error"]) <= 1e-12, "STOP_L2_ACCOUNT_DRIFT")
            artifacts["report.parquet"] = report
            artifacts["decisions.json"] = decisions
            details.update({"fold_id": job["fold_id"], "metrics": metrics})
        row = publish_receipt(root, job, identity, artifacts, details)
        research.commit()
        return row

    @app.function(volumes=mounts, cpu=2, memory=4096, timeout=3600, retries=0)
    def phase_remote(job: dict) -> dict:
        """Assemble one full 424-day new-model signal and F1 backtest, no fit."""
        import qlib
        import pandas as pd
        from qlib.data import D
        from audit.csi1000_layered_search_replay import backtest_signal, report_metrics, static_quote_union
        from csi1000_stage_b_core import (
            RESERVED_SIGNAL_ANCHOR, RESERVED_EXECUTION_END, RESERVED_EXECUTION_START, MARKET,
        )
        from qlib_audit_fixes import read_trading_calendar

        research.reload()
        identity = _identity(job)
        root = _root(job)
        already = checked_receipt(root, job, identity)
        if already is not None:
            return {**already, "resumed": True}
        src = _source_checks()
        require(src["provider_fingerprint"] == identity["provider_fingerprint"] and
                src["snapshot_sha256"] == identity["source_snapshot_sha256"], "STOP_L2_SOURCE_DRIFT")
        qlib.init(provider_uri=str(SOURCE / "cn_data"), region="cn")
        from csi1000_stage_b_core import build_phase_jobs

        calendar = read_trading_calendar(SOURCE / "cn_data")
        chunks = []
        source_shas = []
        for p in build_phase_jobs(calendar, job["phase"]):
            sub = {
                **{k: job[k] for k in ("run_id", "code_sha", "plan_sha256", "provider_fingerprint",
                                     "candidate_id", "model_params", "model_sha256")},
                "kind": "long", "source_snapshot_sha256": job["source_snapshot_sha256"],
                "quote_sha256": job["quote_sha256"], "p0_prefix": job["p0_prefix"], **p,
            }
            rec = checked_receipt(root, sub, identity)
            require(rec is not None, f"STOP_L2_MISSING_LONG_FIT: {p['retrain_asof']}")
            chunks.append(pd.read_parquet(root / "jobs" / job_id(sub) / "prediction.parquet")["score"])
            source_shas.append(rec["artifact_sha256"]["prediction.parquet"])
        signal = pd.concat(chunks).sort_index()
        require(signal.index.is_unique, "STOP_L2_DUPLICATE_PREDICTION")
        dates = signal.index.get_level_values(0).astype(str).str[:10]
        signal = signal[(dates >= RESERVED_SIGNAL_ANCHOR) & (dates < RESERVED_EXECUTION_END)]
        actual_days = sorted({str(v)[:10] for v in signal.index.get_level_values(0).unique()})
        first = calendar.index(RESERVED_SIGNAL_ANCHOR)
        last = calendar.index(RESERVED_EXECUTION_END)
        require(actual_days == calendar[first:last], "STOP_L2_SIGNAL_DAY_COVERAGE")
        codes, quote_sha = static_quote_union(MARKET, RESERVED_EXECUTION_START, RESERVED_EXECUTION_END, D)
        require(quote_sha == job["quote_sha256"], "STOP_L2_QUOTE_SHA_DRIFT")
        report, decisions = backtest_signal(
            signal, 20, 2, codes, RESERVED_EXECUTION_START, RESERVED_EXECUTION_END
        )
        metrics, diagnostics = report_metrics(report)
        require(diagnostics["max_account_return_delta"] <= 1e-10 and
                diagnostics["max_cost_rate_delta"] <= 1e-10, "STOP_L2_NAV_ACCOUNT_DRIFT")
        details = {
            "candidate_id": job["candidate_id"], "phase": job["phase"],
            "source_prediction_shas": source_shas, "quote_sha256": quote_sha,
            "model_config_sha256": job["model_sha256"],
            "provider_snapshot_sha256": _source_checks()["snapshot_sha256"],
            "metrics": metrics, "mean_turnover": metrics["mean_turnover"],
            "n_days": metrics["n_days"], "coverage_ok": True, "ledger_ok": True, "data_ok": True,
            "fit_performed": False,
        }
        row = publish_receipt(root, job, identity, {
            "report.parquet": report, "decisions.json": decisions,
            "signal.parquet": signal.to_frame("score"),
        }, details)
        research.commit()
        return row

    @app.function(volumes={str(RESULTS): research}, cpu=1, memory=1024, timeout=600, retries=0)
    def receipts_remote(jobs: list[dict]) -> list[dict | None]:
        research.reload()
        return [checked_receipt(_root(j), j, _identity(j)) for j in jobs]

    @app.function(volumes={str(RESULTS): research}, cpu=1, memory=1024, timeout=600, retries=0)
    def attempts_remote(meta: dict, new_jobs: list[dict], max_fits: int, reserve: float, spent: float) -> dict:
        """Precommit attempted-fit claims BEFORE dispatch, including failed attempts."""
        research.reload()
        root = _root(meta)
        root.mkdir(parents=True, exist_ok=True)
        path = root / "attempts.json"
        if path.exists():
            book = json.loads(path.read_text(encoding="utf-8"))
            require(book["code_sha"] == meta["code_sha"] and
                    book["plan_sha256"] == meta["plan_sha256"], "STOP_L2_ATTEMPT_IDENTITY")
        else:
            book = {"run_id": meta["run_id"], "code_sha": meta["code_sha"],
                    "plan_sha256": meta["plan_sha256"], "attempts": [], "reserved_usd": 0.0}
        previous = set(book["attempts"])
        keys = [stable_sha(j) for j in new_jobs]
        require(len(keys) == len(set(keys)), "STOP_L2_DUPLICATE_DISPATCH")
        require(not any(k in previous for k in keys), "STOP_L2_REPEAT_FIT: previous attempt already charged")
        require(max_fits <= 320, "STOP_L2_FIT_CAP")
        budget_gate(len(previous), len(keys), float(book["reserved_usd"]), reserve, spent)
        book["attempts"].extend(keys)
        book["reserved_usd"] += reserve * len(keys)
        tmp = path.with_suffix(".tmp")
        require(not tmp.exists(), "STOP_L2_UNFINISHED_ATTEMPT_LEDGER")
        tmp.write_text(json.dumps(book, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)
        research.commit()
        return {"attempt_count": len(book["attempts"]), "reserved_usd": book["reserved_usd"]}

    @app.function(volumes={str(RESULTS): research}, cpu=1, memory=1024, timeout=600, retries=0)
    def freeze_remote(meta: dict, kind: str, obj: dict) -> dict:
        """Non-overwriting, plan-SHA-bound smoke/screen/terminal record."""
        research.reload()
        require(kind in ("smoke_pass", "short_screen", "stage_l2_summary"), "STOP_L2_ILLEGAL_MARKER")
        root = _root(meta)
        root.mkdir(parents=True, exist_ok=True)
        path = root / (kind + ".json")
        data = {"run_id": meta["run_id"], "code_sha": meta["code_sha"],
                "plan_sha256": meta["plan_sha256"], **obj}
        if path.exists():
            require(json.loads(path.read_text(encoding="utf-8")) == data,
                    f"STOP_L2_MARKER_CONFLICT: {kind}")
        else:
            path.write_text(json.dumps(data, sort_keys=True, indent=2, allow_nan=False) + "\n",
                            encoding="utf-8")
            research.commit()
        return data

    @app.function(volumes={str(RESULTS): research}, cpu=1, memory=1024, timeout=120, retries=0)
    def get_marker_remote(meta: dict, kind: str) -> dict | None:
        research.reload()
        path = _root(meta) / (kind + ".json")
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        require(data["run_id"] == meta["run_id"] and data["code_sha"] == meta["code_sha"] and
                data["plan_sha256"] == meta["plan_sha256"], "STOP_L2_MARKER_IDENTITY")
        return data

    @app.function(volumes={str(RESULTS): research}, cpu=2, memory=4096, timeout=1800, retries=0)
    def screen_remote(meta: dict, phases: list[dict], p0_prefix: str, expected_quote_sha: str) -> dict:
        """Read verified phase/P0 NAV bytes; recalculate two-phase coarse PASS."""
        import pandas as pd
        from audit.csi1000_layered_search_core import coarse_pass, enumerate_cells
        from audit.csi1000_layered_search_replay import paired_rows

        research.reload()
        root = _root(meta)
        specs = {s["phase"]: s for s in enumerate_cells("p0")
                 if s["candidate_id"] == "23b92de05cf36c82998de684d0fbf64d81ee54490d755bd3cf96311c00286785"}
        require(set(specs) == {0, 10}, "STOP_L2_P0_BASELINE")
        base_root = RESULTS / p0_prefix / "cells"
        baseline = []
        for phase in (0, 10):
            dest = base_root / specs[phase]["cell_key"]
            row = json.loads((dest / "cell.json").read_text(encoding="utf-8"))
            require(row["status"] == "PASS" and row["phase"] == phase and
                    row["quote_sha256"] == expected_quote_sha and
                    row["provider_fingerprint"] == meta["provider_fingerprint"], "STOP_L2_P0_LINEAGE")
            require(sha256_file(dest / "fixed_report.parquet") == row["artifact_sha256"]["fixed_report.parquet"],
                    "STOP_L2_P0_BYTES")
            baseline.append(row)
        ranked = []
        for cid in sorted({j["candidate_id"] for j in phases}):
            trials = []
            for phase in (0, 10):
                candidates = [j for j in phases if j["candidate_id"] == cid and j["phase"] == phase]
                require(len(candidates) == 1, "STOP_L2_MISSING_PHASE")
                j = candidates[0]
                row = checked_receipt(root, j, _identity(j))
                require(row is not None and row["quote_sha256"] == expected_quote_sha,
                        "STOP_L2_PHASE_BYTES")
                trials.append(row)
            def loader(rec):
                phase = next(p for p in phases if p["candidate_id"] == cid and
                             p["phase"] == rec["phase"])
                return pd.read_parquet(root / "jobs" / job_id(phase) / "report.parquet")
            chal, base, ratio = paired_rows(trials, baseline, loader)
            passed, detail = coarse_pass(chal, base)
            ranked.append({"candidate_id": cid, "topk": 20, "dropn": 2,
                           "passed_coarse": passed, **detail, "turnover_ratio": ratio})
        ranked.sort(key=lambda r: (
            not r["passed_coarse"], -float(r.get("worst_delta_pp") or -1e6),
            -float(r.get("median_delta_pp") or -1e6), r["candidate_id"],
        ))
        return {"status": "PASS", "qualified_configs": sum(r["passed_coarse"] for r in ranked),
                "top_two_for_final_pool": [r for r in ranked if r["passed_coarse"]][:2],
                "all_ranked_trials": ranked, "historic_data": "HISTORICALLY_CONSUMED"}

    def _actual_sha() -> str:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        require(not subprocess.check_output(["git", "status", "--porcelain"], text=True).strip(),
                "STOP_L2_UNCLEAN_WORKTREE")
        return sha

    def _model_sha(params: dict) -> str:
        from ruamel.yaml import YAML
        from qlib_live_retrain import apply_lgb_reproducibility
        path = Path("examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158_csi500.yaml")
        cfg = YAML(typ="safe", pure=True).load(path.read_text(encoding="utf-8"))
        cfg["task"]["model"]["kwargs"].update(params)
        apply_lgb_reproducibility(cfg)
        return stable_sha(cfg["task"]["model"])

    def _dispatch_fit(jobs: list[dict], meta: dict, max_workers: int, reserve: float, actual: float) -> None:
        # Every fit attempt is DURABLY recorded before the first container starts.
        n = 4  # small internal checkpoint wave, not a manual approval gate
        for start in range(0, len(jobs), n):
            wave = jobs[start:start + n]
            already = receipts_remote.remote(wave)
            todo = [job for job, row in zip(wave, already) if row is None]
            if not todo:
                continue
            book = attempts_remote.remote(meta, todo, 320, reserve, actual)
            print(f"L2_FIT_WAVE {start // n + 1}: new={len(todo)} attempted={book['attempt_count']}", flush=True)
            result = list(fit_remote.with_options(max_containers=max_workers).map(todo))
            require(len(result) == len(todo) and all(r.get("status") == "PASS" for r in result),
                    "STOP_L2_FIT_WAVE_FAILED")
            verified = receipts_remote.remote(todo)
            require(all(r is not None for r in verified), "STOP_L2_FIT_ARTIFACT_UNVERIFIED")

    def _meta(run_id: str, sha: str, plan: dict, provider: str) -> dict:
        return {"run_id": run_id, "code_sha": sha, "plan_sha256": plan["plan_sha256"],
                "provider_fingerprint": provider}

    def _short_job(meta: dict, item: dict, fold: dict) -> dict:
        return {**meta, "kind": "short", "candidate_id": item["candidate_id"],
                "model_params": item["model_params"], "model_sha256": _model_sha(item["model_params"]),
                "fold_id": fold["fold_id"], "fold": fold}

    def _long_job(meta: dict, item: dict, geometry: dict) -> dict:
        return {**meta, "kind": "long", "candidate_id": item["candidate_id"],
                "model_params": item["model_params"], "model_sha256": _model_sha(item["model_params"]),
                **geometry}

    def _phase_job(meta: dict, item: dict, phase: int, quote_sha: str) -> dict:
        return {**meta, "kind": "phase", "candidate_id": item["candidate_id"],
                "model_params": item["model_params"], "model_sha256": _model_sha(item["model_params"]),
                "phase": phase, "quote_sha256": quote_sha}

    @app.local_entrypoint()
    def run_l2(
        mode: str = "smoke",
        run_id: str = "",
        p0_summary: str = "",
        p0_prefix: str = "",
        max_workers: int = 1,
        usd_per_fit_reserve: float = 0.01,
        smoke_actual_usd: float = 0.0,
    ) -> None:
        """Smoke once; use mode=complete to automatically continue all safe waves."""
        require(mode in ("smoke", "complete"), "STOP_L2_MODE")
        require(1 <= max_workers <= 4, "STOP_L2_WORKER_CAP")
        require((mode == "smoke" and max_workers == 1) or mode == "complete",
                "STOP_L2_SMOKE_MUST_BE_ONE_WORKER")
        require(usd_per_fit_reserve > 0 and math.isfinite(usd_per_fit_reserve),
                "STOP_L2_COST_RESERVE_REQUIRED")
        require(0 <= smoke_actual_usd < 12 and math.isfinite(smoke_actual_usd), "STOP_L2_ACTUAL_BILL")
        require(p0_prefix.startswith("csi1000_layered_search/") and ".." not in p0_prefix and
                p0_prefix.count("/") >= 4, "STOP_L2_P0_PREFIX")
        sha = _actual_sha()
        prefix_for(run_id, sha)
        plan = make_fastlane_plan(".")
        from audit.csi1000_layered_search_reconcile import verify_sha256sums
        p0_path = Path(p0_summary).resolve()
        p0 = json.loads(p0_path.read_text(encoding="utf-8"))
        require_p0_pass(p0, p0.get("verified_cells"))
        export_root = p0_path.parents[2]
        if (export_root / "SHA256SUMS.txt").exists():
            verify_sha256sums(export_root)
        verify_export_cells(export_root, {c["cell_key"] for c in enumerate_cells("p0")},
                            "P0", {r["cell_key"]: r for r in p0["verified_cells"]})
        require(p0["status"] == "PASS", "STOP_L2_P0")
        require(p0_prefix == (
            f"csi1000_layered_search/csi1000_layered_search_v1/"
            f"{p0['run_id']}/p0/{p0['code_sha'][:12]}"
        ), "STOP_L2_P0_PREFIX_AND_SUMMARY_MISMATCH")
        if mode == "complete":
            require(smoke_actual_usd > 0, "STOP_L2_SMOKE_ACTUAL_BILL_REQUIRED")
        stage_a = load_json(FROZEN_STAGE_A)
        baseline = json.loads(Path("results/csi1000_stage_b/stage_b_full_51756897fc752304.json").read_text())
        provider = baseline["manifest"]["provider_fingerprint"]
        meta = _meta(run_id, sha, plan, provider)
        inventory = inventory_remote.remote(meta)
        meta.update({"source_snapshot_sha256": inventory["source"]["snapshot_sha256"],
                     "quote_sha256": inventory["quote_sha256"], "p0_prefix": p0_prefix})
        folds, calendar = inventory["folds"], inventory["calendar"]
        short = [_short_job(meta, item, fold) for item in plan["new_candidates"] for fold in folds]
        require(len(short) == 16, "STOP_L2_SHORT_ROSTER")
        long_first = _long_job(meta, plan["existing_candidates"][0], inventory["long_periods"]["0"][0])
        if mode == "smoke":
            require(get_marker_remote.remote(meta, "smoke_pass") is None, "STOP_L2_SMOKE_ALREADY_DONE")
            _dispatch_fit([short[0], long_first], meta, 1, usd_per_fit_reserve, 0)
            freeze_remote.remote(meta, "smoke_pass", {
                "status": "PASS", "smoke_short_job_sha": stable_sha(short[0]),
                "smoke_long_job_sha": stable_sha(long_first),
                "provider_snapshot_sha256": inventory["source"]["snapshot_sha256"],
                "quote_sha256": inventory["quote_sha256"], "two_real_fits": True,
            })
            print(f"L2_SMOKE_PASS 2/2 actual new fits run={run_id} code={sha}", flush=True)
            return
        marker = get_marker_remote.remote(meta, "smoke_pass")
        require(marker is not None and marker["status"] == "PASS" and
                marker["provider_snapshot_sha256"] == inventory["source"]["snapshot_sha256"] and
                marker["quote_sha256"] == inventory["quote_sha256"], "STOP_L2_SMOKE_NOT_VERIFIED")
        require(all(r is not None for r in receipts_remote.remote([short[0], long_first])),
                "STOP_L2_SMOKE_RECEIPT")
        # Reserve the maximum 16 short + 6 models x phases0/10 in advance;
        # the actual shortlisted plan can only shrink, not grow.
        from csi1000_stage_b_core import build_phase_jobs
        max_planned = 16 + 6 * sum(len(build_phase_jobs(calendar, p)) for p in (0, 10))
        require(max_planned <= 320, "STOP_L2_FIT_CAP_PREPLAN")
        budget_gate(0, max_planned, 0, usd_per_fit_reserve, smoke_actual_usd)
        _dispatch_fit(short, meta, max_workers, usd_per_fit_reserve, smoke_actual_usd)
        short_rows = receipts_remote.remote(short)
        require(all(r is not None for r in short_rows), "STOP_L2_SHORT_NOT_FULL")
        shortlist = choose_new_for_long(plan, short_rows, stage_a)
        freeze_remote.remote(meta, "short_screen", shortlist)
        roster = make_long_job_roster(plan, calendar, shortlist["selected_new_candidate_ids"])
        candidates = plan["existing_candidates"] + [
            x for x in plan["new_candidates"] if x["candidate_id"] in set(shortlist["selected_new_candidate_ids"])
        ]
        by_id = {x["candidate_id"]: x for x in candidates}
        long = [_long_job(meta, by_id[j["candidate_id"]], j) for j in roster["jobs"]]
        _dispatch_fit(long, meta, max_workers, usd_per_fit_reserve, smoke_actual_usd)
        phase_jobs = [_phase_job(meta, candidate, phase, inventory["quote_sha256"])
                      for candidate in candidates for phase in (0, 10)]
        for i in range(0, len(phase_jobs), max_workers):
            wave = phase_jobs[i:i + max_workers]
            list(phase_remote.with_options(max_containers=max_workers).map(wave))
            require(all(r is not None for r in receipts_remote.remote(wave)), "STOP_L2_PHASE_RESULT_MISSING")
        ranking = screen_remote.remote(meta, phase_jobs, p0_prefix, inventory["quote_sha256"])
        result = freeze_remote.remote(meta, "stage_l2_summary", {
            **ranking, "fit_roster": roster, "short_screen": shortlist,
            "expected_short": 16, "expected_long_fits": len(long),
            "complete_phase_count": len(phase_jobs),
            "bill_is_operator_verified": False,
        })
        print(f"L2_COMPLETE submitted={len(long) + 16} long_phase_results={len(phase_jobs)} "
              f"qualified={result['qualified_configs']} "
              f"note=ACTUAL_BILL_RECONCILIATION_REQUIRED", flush=True)
else:
    # Allows import/py_compile/--help in a machine without Modal credentials.
    app = None
