"""CSI1000 L3 actual-fit Modal runner: gated smoke -> attached auto-waves.

Executes the frozen structural plan from :mod:`audit.csi1000_l3_core`: real
LightGBM fits under new (label horizon, retrain frequency) lineages, F1
quote-union replay, ordinal paired screening against the Baseline control,
and at most one advancing structural configuration. No model fitting happens
on import or via --help. The source Volume mounts read-only; artifacts go to
an independent research Volume. The coordinator is always local and attached;
it never runs in the background, never passes --detach, nohup or shell &.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audit.csi1000_layered_search_core import (
    RESEARCH_VOLUME,
    SOURCE_VOLUME,
    require,
    sha256_file,
    stable_sha,
)
from audit.csi1000_layered_search_core import enumerate_cells, require_p0_pass, verify_export_cells

try:
    import modal
except ImportError:
    modal = None

SOURCE = Path("/source")
RESULTS = Path("/results")
REPO = Path("/root/qlib")

L3_ARTIFACTS = {
    "l3retrain": ("prediction.parquet", "checkpoint.txt"),
    "l3phase": ("report.parquet", "decisions.json", "signal.parquet"),
}


def l3_prefix_for(run_id: str, code_sha: str) -> str:
    """Isolated research prefix bound to the full immutable code SHA."""
    require(bool(re.fullmatch(r"l3_[a-zA-Z0-9_]{4,50}", run_id or "")), "L3 invalid isolated run ID")
    require(bool(re.fullmatch(r"[a-f0-9]{40}", code_sha or "")), "L3 needs full immutable code SHA")
    return f"csi1000_layered_search/csi1000_layered_search_v1/{run_id}/l3/{code_sha[:12]}"


def l3_job_id(job: dict) -> str:
    """Immutable job identity; structural lineage is part of the key."""
    require(job.get("kind") in ("l3retrain", "l3phase"), "L3 unknown job type")
    require(
        isinstance(job.get("label_horizon"), int) and isinstance(job.get("retrain_frequency"), int),
        "L3 structural lineage missing",
    )
    require(isinstance(job.get("phase"), int) and job["phase"] >= 0, "L3 invalid phase")
    suffix = f"r{job['label_horizon']}_f{job['retrain_frequency']}_p{job['phase']:02d}"
    if job["kind"] == "l3retrain":
        require(bool(re.fullmatch(r"\d{4}-\d\d-\d\d", job.get("retrain_asof", ""))), "L3 invalid as-of")
        suffix += f"/{job['retrain_asof']}"
    return f"{job['kind']}/{suffix}"


def l3_receipt_path(root: Path, job: dict) -> Path:
    return root / "jobs" / l3_job_id(job)


def l3_checked_receipt(root: Path, job: dict, identity: dict) -> dict | None:
    """Verify exact receipt job identity and every immutable output byte."""
    directory = l3_receipt_path(root, job)
    if not directory.exists():
        return None
    require((directory / "receipt.json").is_file(), "STOP_L3_INCOMPLETE_RECEIPT")
    record = json.loads((directory / "receipt.json").read_text(encoding="utf-8"))
    for key, value in identity.items():
        require(record.get(key) == value, f"L3 receipt identity drift: {key}")
    require(record.get("job_id") == l3_job_id(job), "L3 receipt job drift")
    require(record.get("job_sha256") == stable_sha(job), "STOP_L3_JOB_SPEC_SHA_DRIFT")
    require(record.get("status") == "PASS" and record.get("kind") == job["kind"], "L3 receipt kind/PASS drift")
    names = set(L3_ARTIFACTS[job["kind"]])
    hashes = record.get("artifact_sha256")
    require(isinstance(hashes, dict) and set(hashes) == names, "STOP_L3_ARTIFACT_MANIFEST")
    for name in sorted(names):
        require((directory / name).is_file() and sha256_file(directory / name) == hashes[name],
                f"L3 receipt bytes drift: {name}")
    return record


def l3_publish_receipt(root: Path, job: dict, identity: dict, artifacts: dict, details: dict) -> dict:
    """Publish to a unique temp directory and atomically rename; never overwrite."""
    import os
    import shutil

    import pandas as pd

    directory = l3_receipt_path(root, job)
    require(set(artifacts) == set(L3_ARTIFACTS[job["kind"]]), "STOP_L3_ARTIFACTS_INCOMPLETE")
    protected = set(identity) | {"status", "kind", "job_id", "job_sha256", "artifact_sha256"}
    require(not (set(details) & protected), "STOP_L3_SIGNED_FIELD_OVERRIDE")
    require(not directory.exists(), "L3 receipt already exists; refusing overwrite")
    temporary = directory.parent / (directory.name + ".tmp")
    require(not temporary.exists(), "STOP_L3_UNFINISHED_TEMP_RECEIPT")
    temporary.mkdir(parents=True, exist_ok=False)
    try:
        hashes = {}
        for name, payload in artifacts.items():
            target = temporary / name
            if isinstance(payload, str):
                target.write_text(payload, encoding="utf-8")
            elif isinstance(payload, list):
                target.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n", encoding="utf-8")
            elif isinstance(payload, pd.DataFrame):
                payload.to_parquet(target)
            else:
                require(False, f"L3 unserializable artifact {name}")
            hashes[name] = sha256_file(target)
        record = {"job_id": l3_job_id(job), "job_sha256": stable_sha(job),
                  "status": "PASS", "kind": job["kind"], **identity, **details,
                  "artifact_sha256": hashes}
        (temporary / "receipt.json").write_text(json.dumps(record, indent=2, sort_keys=True, allow_nan=False) + "\n",
                                                 encoding="utf-8")
        os.replace(temporary, directory)
        return record
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


if modal is not None:
    original = modal.Volume.from_name(SOURCE_VOLUME, create_if_missing=False)
    research = modal.Volume.from_name(RESEARCH_VOLUME, create_if_missing=False)
    image = (
        modal.Image.debian_slim(python_version="3.11")
        .apt_install("build-essential")
        .env({"PYTHONPATH": "/root/qlib:/root", "MLFLOW_ALLOW_FILE_STORE": "true"})
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
                str(p).endswith((".cpp", ".so")) or any(t in str(p) for t in (".venv", "mlruns", "__pycache__", ".git"))
            ),
        )
        .run_commands(
            "printf 'version = \"0.0.0\"\\n' > /root/qlib/qlib/_version.py",
            "cd /tmp && python -c \"from setuptools import setup, Extension; from Cython.Build import cythonize; import numpy; root='/root/qlib'; extensions=[Extension('qlib.data._libs.rolling', [root+'/qlib/data/_libs/rolling.pyx'], language='c++', include_dirs=[numpy.get_include()]), Extension('qlib.data._libs.expanding', [root+'/qlib/data/_libs/expanding.pyx'], language='c++', include_dirs=[numpy.get_include()])]; setup(name='qlib-image-cython-only', version='0.0.0', ext_modules=cythonize(extensions, language_level=3), script_args=['build_ext', '--build-lib', root, '--build-temp', '/tmp/qlib-image-build'])\"",
            "cd /tmp && python -c \"import qlib; from qlib.data._libs import rolling, expanding; assert qlib.__version__ == '0.0.0'\"",
        )
    )
    app = modal.App("qlib-csi1000-l3-structural", image=image)
    mounts = {str(SOURCE): original.with_mount_options(read_only=True), str(RESULTS): research}

    def _identity(job: dict) -> dict:
        return {
            k: job[k]
            for k in (
                "run_id",
                "code_sha",
                "plan_sha256",
                "provider_fingerprint",
                "source_snapshot_sha256",
                "quote_sha256",
                "p0_prefix",
            )
        }

    def _root(job: dict) -> Path:
        return RESULTS / l3_prefix_for(job["run_id"], job["code_sha"])

    def _source_checks() -> dict:
        from audit.csi1000_layered_search_replay import load_frozen_result

        frozen = load_frozen_result(REPO)["manifest"]
        snap = SOURCE / "csi1000_stage_b" / "provider_snapshot.json"
        require(snap.is_file(), "STOP_L3_SOURCE: provider snapshot missing")
        row = json.loads(snap.read_text(encoding="utf-8"))
        for key in ("snapshot_token", "provider_fingerprint"):
            require(row[key] == frozen[key], f"STOP_L3_SOURCE: {key} differs from Stage-B")
        return {
            "provider_fingerprint": frozen["provider_fingerprint"],
            "snapshot_sha256": sha256_file(snap),
            "snapshot_token": frozen["snapshot_token"],
        }

    def _task(model_params: dict, horizon: int) -> dict:
        from audit.csi1000_l3_core import label_spec
        from ruamel.yaml import YAML
        from qlib_live_retrain import apply_lgb_reproducibility
        from csi1000_tuner_core import build_candidate_spec

        spec = label_spec(horizon)
        built = build_candidate_spec(model_params)
        cfg_path = REPO / "examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158_csi500.yaml"
        cfg = YAML(typ="safe", pure=True).load(cfg_path.read_text(encoding="utf-8"))
        ds = cfg["task"]["dataset"]["kwargs"]
        ds["handler"]["kwargs"]["instruments"] = "csi1000"
        ds["handler"]["kwargs"]["label"] = [f"{spec['expression']}"]
        cfg["task"]["model"]["kwargs"].update(model_params)
        apply_lgb_reproducibility(cfg)
        require(int(cfg["task"]["model"]["kwargs"]["num_threads"]) == 20, "STOP_L3_MODEL_THREADS")
        cfg["qlib_init"] = {
            "provider_uri": str(SOURCE / "cn_data"),
            "region": "cn",
            "exp_manager": {
                "class": "MLflowExpManager",
                "module_path": "qlib.workflow.expm",
                "kwargs": {"uri": "file:/tmp/l3_mlflow", "default_exp_name": "qlib-l3-isolated"},
            },
        }
        return cfg, built

    @app.function(volumes=mounts, cpu=2, memory=4096, timeout=900, retries=0)
    def inventory_remote(meta: dict) -> dict:
        """Resolve live source identity and exact structural calendars."""
        import qlib
        from qlib_audit_fixes import read_trading_calendar
        from qlib.data import D
        from audit.csi1000_l3_core import build_l3_retrain_schedule, estimate_variant_fits, make_l3_plan
        from audit.csi1000_layered_search_replay import static_quote_union
        from csi1000_stage_b_core import MARKET, RESERVED_EXECUTION_END, RESERVED_EXECUTION_START

        src = _source_checks()
        require(
            src["provider_fingerprint"] == meta["provider_fingerprint"],
            "STOP_L3_SOURCE: frozen provider fingerprint drift",
        )
        qlib.init(provider_uri=str(SOURCE / "cn_data"), region="cn")
        calendar = read_trading_calendar(SOURCE / "cn_data")
        plan = make_l3_plan()
        total = 0
        for variant in plan["stage1_variants"] + plan["stage2_variants"]:
            for phase in variant["screen_phases"]:
                total += estimate_variant_fits(
                    build_l3_retrain_schedule(calendar, frequency=variant["retrain_frequency"], phase=phase)
                )
        require(total <= 250, "STOP_L3_FIT_CAP_PREPLAN")
        codes, quote_sha = static_quote_union(MARKET, RESERVED_EXECUTION_START, RESERVED_EXECUTION_END, D)
        require(bool(codes), "STOP_L3_QUOTE: empty static union")
        research.reload()
        from audit.csi1000_layered_search_core import BASELINE_CANDIDATE, enumerate_cells

        p0_cells = {r["phase"]: r for r in enumerate_cells("p0") if r["candidate_id"] == BASELINE_CANDIDATE}
        require(set(p0_cells) == {0, 10}, "STOP_L3_P0_SPECS")
        for phase in (0, 10):
            root = RESULTS / meta["p0_prefix"] / "cells" / p0_cells[phase]["cell_key"]
            require((root / "cell.json").is_file(), "STOP_L3_P0_REMOTE_SOURCE_MISSING")
            row = json.loads((root / "cell.json").read_text(encoding="utf-8"))
            require(
                row.get("status") == "PASS"
                and row.get("phase") == phase
                and row.get("quote_sha256") == quote_sha
                and row.get("provider_fingerprint") == src["provider_fingerprint"],
                "STOP_L3_P0_REMOTE_IDENTITY",
            )
            for name, hashed in row.get("artifact_sha256", {}).items():
                require(
                    (root / name).is_file() and sha256_file(root / name) == hashed,
                    f"STOP_L3_P0_REMOTE_BYTE_SHA: {name}",
                )
            require(row.get("artifact_sha256", {}).get("fixed_report.parquet"), "STOP_L3_P0_REMOTE_REPORT_MISSING")
        return {
            "source": src,
            "calendar": calendar,
            "quote_sha256": quote_sha,
            "plan": plan,
            "preplanned_fits": total,
        }

    @app.function(volumes=mounts, cpu=8, memory=16384, timeout=7200, retries=0)
    def fit_remote(job: dict) -> dict:
        """One genuine structural fit, exactly one as-of. No automatic retry."""
        import qlib
        import numpy as np
        from qlib.model.base import Model
        from qlib.data.dataset import Dataset
        from qlib.utils import init_instance_by_config
        from qlib_audit_fixes import purge_cfg_splits, read_trading_calendar
        from audit.csi1000_l3_core import build_l3_retrain_schedule, training_slice

        research.reload()
        identity = _identity(job)
        root = _root(job)
        cached = l3_checked_receipt(root, job, identity)
        if cached is not None:
            return {**cached, "resumed": True}
        source = _source_checks()
        require(
            source["provider_fingerprint"] == identity["provider_fingerprint"]
            and source["snapshot_sha256"] == identity["source_snapshot_sha256"],
            "STOP_L3_SOURCE_DRIFT",
        )
        horizon = int(job["label_horizon"])
        frequency = int(job["retrain_frequency"])
        config, built = _task(job["model_params"], horizon)
        require(built["candidate_id"] == job["candidate_id"], "L3 model ID mismatch")
        require(stable_sha(config["task"]["model"]) == job["model_sha256"], "L3 model config SHA mismatch")
        qlib.init(**{**config["qlib_init"], "skip_if_reg": True})
        calendar = read_trading_calendar(SOURCE / "cn_data")
        legal = {
            (p["retrain_asof"], p["signal_end"])
            for p in build_l3_retrain_schedule(calendar, frequency=frequency, phase=int(job["phase"]))
        }
        require((job["retrain_asof"], job["signal_end"]) in legal, "STOP_L3_RETRAIN_CALENDAR")
        window = training_slice(calendar, asof=job["retrain_asof"], horizon=horizon)
        ds = config["task"]["dataset"]["kwargs"]
        segments, handler = ds["segments"], ds["handler"]["kwargs"]
        segments.update(
            {
                "train": [window["train_start"], window["train_end"]],
                "valid": [window["valid_start"], window["valid_end"]],
                "test": [job["retrain_asof"], job["signal_end"]],
            }
        )
        handler["start_time"], handler["end_time"] = "2015-01-01", job["signal_end"]
        handler["fit_start_time"], handler["fit_end_time"] = window["train_start"], window["train_end"]
        purge_cfg_splits(config, calendar, horizon=horizon)
        model = init_instance_by_config(config["task"]["model"], accept_types=Model)
        dataset = init_instance_by_config(config["task"]["dataset"], accept_types=Dataset)
        model.fit(dataset)  # the ONLY new fit call for this job
        prediction = model.predict(dataset, segment="test")
        require(not prediction.empty, "STOP_L3_EMPTY_PREDICTION")
        observed = [
            str(prediction.index.get_level_values(0).min())[:10],
            str(prediction.index.get_level_values(0).max())[:10],
        ]
        require(observed == [job["retrain_asof"], job["signal_end"]], "STOP_L3_PREDICTION_COVERAGE")
        require(
            prediction.index.is_unique
            and prediction.notna().all()
            and np.isfinite(prediction.to_numpy(dtype=float)).all(),
            "STOP_L3_SIGNAL_SCHEMA",
        )
        booster = getattr(model, "model", None)
        require(booster is not None and hasattr(booster, "model_to_string"), "STOP_L3_NO_TRAINED_BOOSTER")
        checkpoint = booster.model_to_string()
        require(checkpoint and len(checkpoint) > 100, "STOP_L3_EMPTY_CHECKPOINT")
        artifacts = {"prediction.parquet": prediction.to_frame("score"), "checkpoint.txt": checkpoint}
        details = {
            "candidate_id": job["candidate_id"],
            "model_config_sha256": job["model_sha256"],
            "label_horizon": horizon,
            "retrain_frequency": frequency,
            "phase": int(job["phase"]),
            "retrain_asof": job["retrain_asof"],
            "signal_end": job["signal_end"],
            "train_window": [window["train_start"], window["train_end"]],
            "valid_window": [window["valid_start"], window["valid_end"]],
            "best_iteration": int(booster.best_iteration or 0),
            "fit_performed": True,
        }
        row = l3_publish_receipt(root, job, identity, artifacts, details)
        research.commit()
        return row

    @app.function(volumes=mounts, cpu=2, memory=4096, timeout=3600, retries=0)
    def phase_remote(job: dict) -> dict:
        """Assemble one structural-variant signal and F1 backtest, no fit."""
        import qlib
        import pandas as pd
        from qlib.data import D
        from audit.csi1000_layered_search_replay import backtest_signal, report_metrics, static_quote_union
        from audit.csi1000_l3_core import EXECUTION_END, EXECUTION_START, SIGNAL_ANCHOR, build_l3_retrain_schedule
        from csi1000_stage_b_core import MARKET
        from qlib_audit_fixes import read_trading_calendar

        research.reload()
        identity = _identity(job)
        root = _root(job)
        already = l3_checked_receipt(root, job, identity)
        if already is not None:
            return {**already, "resumed": True}
        src = _source_checks()
        require(
            src["provider_fingerprint"] == identity["provider_fingerprint"]
            and src["snapshot_sha256"] == identity["source_snapshot_sha256"],
            "STOP_L3_SOURCE_DRIFT",
        )
        qlib.init(provider_uri=str(SOURCE / "cn_data"), region="cn")
        calendar = read_trading_calendar(SOURCE / "cn_data")
        chunks = []
        source_shas = []
        for p in build_l3_retrain_schedule(calendar, frequency=int(job["retrain_frequency"]), phase=int(job["phase"])):
            sub = {
                **{
                    k: job[k]
                    for k in (
                        "run_id",
                        "code_sha",
                        "plan_sha256",
                        "provider_fingerprint",
                        "candidate_id",
                        "model_params",
                        "model_sha256",
                        "label_horizon",
                        "retrain_frequency",
                    )
                },
                "kind": "l3retrain",
                "source_snapshot_sha256": job["source_snapshot_sha256"],
                "quote_sha256": job["quote_sha256"],
                "p0_prefix": job["p0_prefix"],
                "phase": p["phase"],
                "retrain_asof": p["retrain_asof"],
                "signal_end": p["signal_end"],
            }
            rec = l3_checked_receipt(root, sub, identity)
            require(rec is not None, f"STOP_L3_MISSING_STRUCTURAL_FIT: {p['retrain_asof']}")
            chunks.append(pd.read_parquet(root / "jobs" / l3_job_id(sub) / "prediction.parquet")["score"])
            source_shas.append(rec["artifact_sha256"]["prediction.parquet"])
        signal = pd.concat(chunks).sort_index()
        require(signal.index.is_unique, "STOP_L3_DUPLICATE_PREDICTION")
        dates = signal.index.get_level_values(0).astype(str).str[:10]
        signal = signal[(dates >= SIGNAL_ANCHOR) & (dates < EXECUTION_END)]
        actual_days = sorted({str(v)[:10] for v in signal.index.get_level_values(0).unique()})
        first = calendar.index(SIGNAL_ANCHOR)
        last = calendar.index(EXECUTION_END)
        require(actual_days == calendar[first:last], "STOP_L3_SIGNAL_DAY_COVERAGE")
        codes, quote_sha = static_quote_union(MARKET, EXECUTION_START, EXECUTION_END, D)
        require(quote_sha == job["quote_sha256"], "STOP_L3_QUOTE_SHA_DRIFT")
        report, decisions = backtest_signal(signal, 20, 2, codes, EXECUTION_START, EXECUTION_END)
        metrics, diagnostics = report_metrics(report)
        require(len(decisions) == 424, "STOP_L3_PHASE_ORDER_COVERAGE")
        require(
            diagnostics["max_account_return_delta"] <= 1e-10 and diagnostics["max_cost_rate_delta"] <= 1e-10,
            "STOP_L3_NAV_ACCOUNT_DRIFT",
        )
        details = {
            "candidate_id": job["candidate_id"],
            "label_horizon": int(job["label_horizon"]),
            "retrain_frequency": int(job["retrain_frequency"]),
            "phase": int(job["phase"]),
            "source_prediction_shas": source_shas,
            "model_config_sha256": job["model_sha256"],
            "provider_snapshot_sha256": _source_checks()["snapshot_sha256"],
            "metrics": metrics,
            "mean_turnover": metrics["mean_turnover"],
            "n_days": metrics["n_days"],
            "coverage_ok": True,
            "ledger_ok": True,
            "data_ok": True,
            "fit_performed": False,
        }
        row = l3_publish_receipt(
            root,
            job,
            identity,
            {"report.parquet": report, "decisions.json": decisions, "signal.parquet": signal.to_frame("score")},
            details,
        )
        research.commit()
        return row

    @app.function(volumes={str(RESULTS): research}, cpu=1, memory=1024, timeout=600, retries=0)
    def receipts_remote(jobs: list[dict]) -> list[dict | None]:
        research.reload()
        return [l3_checked_receipt(_root(j), j, _identity(j)) for j in jobs]

    @app.function(volumes={str(RESULTS): research}, cpu=1, memory=1024, timeout=600, retries=0)
    def attempts_remote(meta: dict, new_jobs: list[dict]) -> dict:
        """Record all actual attempted fits before dispatch; scope maximum is 250.

        This is immutable experiment provenance, NOT a spend or budget ledger.
        """
        research.reload()
        root = _root(meta)
        root.mkdir(parents=True, exist_ok=True)
        path = root / "attempts.json"
        if path.exists():
            book = json.loads(path.read_text(encoding="utf-8"))
            require(book["run_id"] == meta["run_id"] and
                    book["code_sha"] == meta["code_sha"] and
                    book["plan_sha256"] == meta["plan_sha256"], "STOP_L3_ATTEMPT_IDENTITY")
        else:
            book = {"run_id": meta["run_id"], "code_sha": meta["code_sha"],
                    "plan_sha256": meta["plan_sha256"], "attempts": []}
        previous = set(book["attempts"])
        require(len(previous) == len(book["attempts"]), "STOP_L3_DUPLICATE_ATTEMPT_LEDGER")
        keys = [stable_sha(job) for job in new_jobs]
        require(len(set(keys)) == len(keys), "STOP_L3_DUPLICATE_DISPATCH")
        require(not (set(keys) & previous), "STOP_L3_REPEAT_FIT: attempted fit without durable receipt")
        require(len(previous) + len(keys) <= 250, "STOP_L3_FIT_SCOPE_250")
        book["attempts"].extend(keys)
        tmp = path.with_suffix(".tmp")
        require(not tmp.exists(), "STOP_L3_UNFINISHED_ATTEMPT_LEDGER")
        tmp.write_text(json.dumps(book, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)
        research.commit()
        return {"attempt_count": len(book["attempts"])}

    @app.function(volumes={str(RESULTS): research}, cpu=1, memory=1024, timeout=600, retries=0)
    def freeze_remote(meta: dict, kind: str, obj: dict) -> dict:
        """Non-overwriting, plan-SHA-bound smoke/screen/terminal record."""
        research.reload()
        require(kind in ("smoke_pass", "l3_screen_stage1", "l3_screen_final", "l3_decision"), "STOP_L3_ILLEGAL_MARKER")
        root = _root(meta)
        root.mkdir(parents=True, exist_ok=True)
        path = root / (kind + ".json")
        data = {"run_id": meta["run_id"], "code_sha": meta["code_sha"], "plan_sha256": meta["plan_sha256"], **obj}
        if path.exists():
            require(json.loads(path.read_text(encoding="utf-8")) == data, "STOP_L3_FROZEN_MARKER_CONFLICT")
        else:
            path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            research.commit()
        return data

    @app.function(volumes={str(RESULTS): research}, cpu=1, memory=1024, timeout=600, retries=0)
    def get_marker_remote(meta: dict, kind: str) -> dict | None:
        research.reload()
        path = _root(meta) / (kind + ".json")
        if not path.exists():
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
        require(record.get("run_id") == meta["run_id"] and
                record.get("code_sha") == meta["code_sha"] and
                record.get("plan_sha256") == meta["plan_sha256"], "STOP_L3_MARKER_IDENTITY")
        return record

    @app.function(volumes={str(RESULTS): research}, cpu=2, memory=4096, timeout=1800, retries=0)
    def screen_remote(meta: dict, variant: dict, p0_prefix: str, expected_quote_sha: str) -> dict:
        """Ordinal paired screen of one structural variant against Baseline P0."""
        import pandas as pd
        from audit.csi1000_l3_core import ordinal_remap_for_pairing
        from audit.csi1000_layered_search_core import BASELINE_CANDIDATE, coarse_pass, enumerate_cells
        from audit.csi1000_layered_search_replay import paired_rows

        research.reload()
        root = _root(meta)
        specs = {s["phase"]: s for s in enumerate_cells("p0") if s["candidate_id"] == BASELINE_CANDIDATE}
        require(set(specs) == {0, 10}, "STOP_L3_P0_BASELINE")
        base_root = RESULTS / p0_prefix / "cells"
        baseline = []
        for phase in (0, 10):
            dest = base_root / specs[phase]["cell_key"]
            row = json.loads((dest / "cell.json").read_text(encoding="utf-8"))
            require(
                row["status"] == "PASS"
                and row["phase"] == phase
                and row["quote_sha256"] == expected_quote_sha
                and row["provider_fingerprint"] == meta["provider_fingerprint"],
                "STOP_L3_P0_LINEAGE",
            )
            require(
                sha256_file(dest / "fixed_report.parquet") == row["artifact_sha256"]["fixed_report.parquet"],
                "STOP_L3_P0_BYTES",
            )
            baseline.append(row)
        trials = []
        for phase in variant["screen_phases"]:
            path = root / "jobs" / f"l3phase/r{variant['label_horizon']}_f{variant['retrain_frequency']}_p{phase:02d}"
            record = json.loads((path / "receipt.json").read_text(encoding="utf-8"))
            require(
                record.get("status") == "PASS"
                and record.get("phase") == phase
                and record.get("quote_sha256") == expected_quote_sha
                and record.get("label_horizon") == variant["label_horizon"]
                and record.get("retrain_frequency") == variant["retrain_frequency"],
                "STOP_L3_TRIAL_LINEAGE",
            )
            trials.append(record)
        remapped, mapping = ordinal_remap_for_pairing([{"phase": p} for p in variant["screen_phases"]], baseline)
        require(len(remapped) == len(variant["screen_phases"]), "STOP_L3_ORDINAL_REMAP")

        def loader(rec):
            return pd.read_parquet(
                root
                / "jobs"
                / f"l3phase/r{variant['label_horizon']}_f{variant['retrain_frequency']}_p{rec['phase']:02d}/report.parquet"
            )

        base_cells = []
        for position, phase in enumerate(sorted(variant["screen_phases"])):
            source = sorted(baseline, key=lambda r: r["phase"])[position]
            base_cells.append({**source, "phase": phase, "_source_phase": source["phase"]})
        chall_rows, base_rows, _turnover_median = paired_rows(trials, base_cells, loader)
        passed, detail = coarse_pass(chall_rows, base_rows, set(variant["screen_phases"]))
        return {
            "status": "PASS",
            "variant": {k: variant[k] for k in ("label_horizon", "retrain_frequency", "screen_phases")},
            "ordinal_mapping": mapping,
            "passed_coarse": passed,
            "detail": detail,
            "historic_data": "HISTORICALLY_CONSUMED",
        }

    class SafePause(Exception):
        """Requested graceful foreground pause at a completed wave boundary."""

    def _actual_sha() -> str:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        require(
            not subprocess.check_output(["git", "status", "--porcelain"], text=True).strip(), "STOP_L3_UNCLEAN_WORKTREE"
        )
        return sha

    def _model_sha(params: dict) -> str:
        from ruamel.yaml import YAML
        from qlib_live_retrain import apply_lgb_reproducibility

        path = Path("examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158_csi500.yaml")
        cfg = YAML(typ="safe", pure=True).load(path.read_text(encoding="utf-8"))
        cfg["task"]["model"]["kwargs"].update(params)
        apply_lgb_reproducibility(cfg)
        return stable_sha(cfg["task"]["model"])

    def _meta(run_id: str, sha: str, plan: dict, provider: str) -> dict:
        return {"run_id": run_id, "code_sha": sha, "plan_sha256": plan["plan_sha256"], "provider_fingerprint": provider}

    def _fit_job(meta: dict, item: dict, asof: str, signal_end: str, horizon: int, frequency: int, phase: int) -> dict:
        # Explicit fields only: reserve/spend bookkeeping must never leak
        # into job identity, or resume would double-count fits on rebooking.
        return {
            "run_id": meta["run_id"],
            "code_sha": meta["code_sha"],
            "plan_sha256": meta["plan_sha256"],
            "provider_fingerprint": meta["provider_fingerprint"],
            "source_snapshot_sha256": meta["source_snapshot_sha256"],
            "quote_sha256": meta["quote_sha256"],
            "p0_prefix": meta["p0_prefix"],
            "kind": "l3retrain",
            "candidate_id": item["candidate_id"],
            "model_params": item["model_params"],
            "model_sha256": _model_sha(item["model_params"]),
            "label_horizon": horizon,
            "retrain_frequency": frequency,
            "phase": phase,
            "retrain_asof": asof,
            "signal_end": signal_end,
        }

    def _phase_job(meta: dict, item: dict, variant: dict, phase: int, quote_sha: str) -> dict:
        return {
            "run_id": meta["run_id"],
            "code_sha": meta["code_sha"],
            "plan_sha256": meta["plan_sha256"],
            "provider_fingerprint": meta["provider_fingerprint"],
            "source_snapshot_sha256": meta["source_snapshot_sha256"],
            "quote_sha256": quote_sha,
            "p0_prefix": meta["p0_prefix"],
            "kind": "l3phase",
            "candidate_id": item["candidate_id"],
            "model_params": item["model_params"],
            "model_sha256": _model_sha(item["model_params"]),
            "label_horizon": variant["label_horizon"],
            "retrain_frequency": variant["retrain_frequency"],
            "phase": phase,
        }

    def _dispatch_fit(
        jobs: list[dict],
        meta: dict,
        max_workers: int,
        reserve: float,
        actual: float,
        remaining_waves: list[int] | None = None,
    ) -> None:
        # Every fit attempt is DURABLY recorded before the first container starts.
        n = 4  # small internal checkpoint wave, not a manual approval gate
        for start in range(0, len(jobs), n):
            wave = jobs[start : start + n]
            already = receipts_remote.remote(wave)
            todo = [job for job, row in zip(wave, already) if row is None]
            if not todo:
                continue
            if remaining_waves is not None and remaining_waves[0] == 0:
                raise SafePause("verified fit-wave limit reached")
            book = attempts_remote.remote(meta, todo, 250, reserve, actual)
            print(f"L3_FIT_WAVE {start // n + 1}: new={len(todo)} attempted={book['attempt_count']}", flush=True)
            result = list(fit_remote.with_options(max_containers=max_workers).map(todo))
            require(
                len(result) == len(todo) and all(r.get("status") == "PASS" for r in result), "STOP_L3_FIT_WAVE_FAILED"
            )
            verified = receipts_remote.remote(todo)
            require(all(r is not None for r in verified), "STOP_L3_FIT_ARTIFACT_UNVERIFIED")
            if remaining_waves is not None:
                remaining_waves[0] -= 1

    def _wave(
        shorts: list[dict],
        meta: dict,
        max_workers: int,
        remaining_waves: list[int] | None,
    ) -> list[dict]:
        if remaining_waves is not None:
            if not remaining_waves[0]:
                raise SafePause("L3 wave budget exhausted; resume with the same command")
            remaining_waves[0] -= 1
        return _dispatch_fit(shorts, meta, max_workers, meta["reserve_per_fit"], meta["spent_usd"])

    def _run_l3_smoke(meta: dict, max_workers: int, anchor: dict) -> None:
        from audit.csi1000_l3_core import STAGE1_VARIANTS, build_l3_retrain_schedule

        require(len(STAGE1_VARIANTS) == 2, "STOP_L3_SMOKE_VARIANTS")
        inventory = inventory_remote.remote(meta)
        calendar = inventory["calendar"]
        meta = {
            **meta,
            "source_snapshot_sha256": inventory["source"]["snapshot_sha256"],
            "quote_sha256": inventory["quote_sha256"],
        }
        jobs = []
        for variant in STAGE1_VARIANTS:
            phase = variant["screen_phases"][0]
            first = build_l3_retrain_schedule(calendar, frequency=variant["retrain_frequency"], phase=phase)[0]
            jobs.append(
                _fit_job(
                    meta,
                    anchor,
                    first["retrain_asof"],
                    first["signal_end"],
                    variant["label_horizon"],
                    variant["retrain_frequency"],
                    phase,
                )
            )
        from audit.csi1000_l2_execution import budget_gate

        budget_gate(0, len(jobs), 0.0, meta["reserve_per_fit"], meta["spent_usd"], cap=6.0)
        _dispatch_fit(jobs, meta, max_workers, meta["reserve_per_fit"], meta["spent_usd"])
        rows = receipts_remote.remote(jobs)
        require(len(rows) == 2 and all(r is not None and r.get("fit_performed") for r in rows), "STOP_L3_SMOKE_RECEIPT")
        freeze_remote.remote(
            meta,
            "smoke_pass",
            {
                "status": "PASS",
                "smoke_job_shas": [stable_sha(j) for j in jobs],
                "provider_snapshot_sha256": inventory["source"]["snapshot_sha256"],
                "quote_sha256": inventory["quote_sha256"],
                "two_real_fits": True,
            },
        )
        print(f"L3_SMOKE_PASS 2/2 actual new fits run={meta['run_id']} code={meta['code_sha']}", flush=True)

    def _run_l3_complete(meta: dict, max_workers: int, anchor: dict, remaining_waves: list[int] | None) -> None:
        from audit.csi1000_l3_core import (
            STAGE1_VARIANTS,
            STAGE2_VARIANTS,
            build_l3_retrain_schedule,
            l3_decision,
            stage2_qualified,
        )

        inventory = inventory_remote.remote(meta)
        calendar = inventory["calendar"]
        meta = {
            **meta,
            "source_snapshot_sha256": inventory["source"]["snapshot_sha256"],
            "quote_sha256": inventory["quote_sha256"],
        }
        marker = get_marker_remote.remote(meta, "smoke_pass")
        require(marker is not None and marker.get("status") == "PASS", "STOP_L3_SMOKE_NOT_VERIFIED")
        require(
            marker.get("provider_snapshot_sha256") == inventory["source"]["snapshot_sha256"]
            and marker.get("quote_sha256") == inventory["quote_sha256"],
            "STOP_L3_SMOKE_IDENTITY",
        )
        stage1_jobs = []
        for variant in STAGE1_VARIANTS:
            for phase in variant["screen_phases"]:
                for p in build_l3_retrain_schedule(calendar, frequency=variant["retrain_frequency"], phase=phase):
                    stage1_jobs.append(
                        _fit_job(
                            meta,
                            anchor,
                            p["retrain_asof"],
                            p["signal_end"],
                            variant["label_horizon"],
                            variant["retrain_frequency"],
                            phase,
                        )
                    )
        try:
            _wave(stage1_jobs, meta, max_workers, remaining_waves)
        except SafePause:
            print("L3_INCOMPLETE_SAFE_PAUSE: verified partial wave receipts; rerun --mode complete", flush=True)
            return
        screened = []
        for variant in STAGE1_VARIANTS:
            for phase in variant["screen_phases"]:
                _phase_job_check(meta, anchor, variant, phase, inventory["quote_sha256"])
            result = screen_remote.remote(meta, variant, meta["p0_prefix"], inventory["quote_sha256"])
            screened.append({**variant, "passed_coarse": result["passed_coarse"], "detail": result["detail"]})
        freeze_remote.remote(meta, "l3_screen", {"stage": "stage1", "variants": screened})
        if stage2_qualified(screened):
            stage2_jobs = []
            for variant in STAGE2_VARIANTS:
                for phase in variant["screen_phases"]:
                    for p in build_l3_retrain_schedule(calendar, frequency=variant["retrain_frequency"], phase=phase):
                        stage2_jobs.append(
                            _fit_job(
                                meta,
                                anchor,
                                p["retrain_asof"],
                                p["signal_end"],
                                variant["label_horizon"],
                                variant["retrain_frequency"],
                                phase,
                            )
                        )
            try:
                _wave(stage2_jobs, meta, max_workers, remaining_waves)
            except SafePause:
                print("L3_INCOMPLETE_SAFE_PAUSE: verified partial wave receipts; rerun --mode complete", flush=True)
                return
            for variant in STAGE2_VARIANTS:
                for phase in variant["screen_phases"]:
                    _phase_job_check(meta, anchor, variant, phase, inventory["quote_sha256"])
                result = screen_remote.remote(meta, variant, meta["p0_prefix"], inventory["quote_sha256"])
                screened.append({**variant, "passed_coarse": result["passed_coarse"], "detail": result["detail"]})
            freeze_remote.remote(meta, "l3_screen", {"stage": "stage1+stage2", "variants": screened})
        decision, info = l3_decision(screened)
        freeze_remote.remote(meta, "l3_decision", {"decision": decision, **info})
        print(
            f"L3_COMPLETE screened={len(screened)} decision={decision} " f"note=ACTUAL_BILL_RECONCILIATION_REQUIRED",
            flush=True,
        )

    def _phase_job_check(meta: dict, anchor: dict, variant: dict, phase: int, quote_sha: str) -> dict:
        job = _phase_job(meta, anchor, variant, phase, quote_sha)
        row = phase_remote.remote(job)
        require(row.get("status") == "PASS", "STOP_L3_PHASE_FAILED")
        return row

    @app.local_entrypoint()
    def run_l3(
        mode: str = "smoke",
        run_id: str = "",
        p0_summary: str = "",
        p0_prefix: str = "",
        max_workers: int = 1,
        usd_per_fit_reserve: float = 0.0,
        smoke_actual_usd: float = 0.0,
        nonfit_reserve_usd: float = 0.5,
        max_fit_waves: int = 0,
    ) -> None:
        """Smoke once (two first-slice fits, one per stage-1 frequency); complete runs stage-gated waves."""
        import re

        require(mode in ("smoke", "complete"), "STOP_L3_MODE")
        require(isinstance(max_fit_waves, int) and 0 <= max_fit_waves <= 10000, "STOP_L3_INVALID_FOREGROUND_WAVE_LIMIT")
        require(mode != "smoke" or max_fit_waves == 0, "STOP_L3_SMOKE_MUST_FINISH")
        require(1 <= max_workers <= 4, "STOP_L3_WORKER_CAP")
        require((mode == "smoke" and max_workers == 1) or mode == "complete", "STOP_L3_SMOKE_MUST_BE_ONE_WORKER")
        require(usd_per_fit_reserve > 0 and math.isfinite(usd_per_fit_reserve), "STOP_L3_COST_RESERVE_REQUIRED")
        require(0 <= smoke_actual_usd < 6 and math.isfinite(smoke_actual_usd), "STOP_L3_ACTUAL_BILL")
        require(math.isfinite(nonfit_reserve_usd) and nonfit_reserve_usd >= 0.5, "STOP_L3_NONFIT_COST_RESERVE_REQUIRED")
        require(
            p0_prefix.startswith("csi1000_layered_search/") and ".." not in p0_prefix and p0_prefix.count("/") >= 4,
            "STOP_L3_P0_PREFIX",
        )
        require(bool(re.fullmatch(r"l3_[a-zA-Z0-9_]{4,50}", run_id or "")), "STOP_L3_RUN_ID")
        from audit.csi1000_l3_core import ANCHOR_CANDIDATE, ANCHOR_MODEL_PARAMS, make_l3_plan

        sha = _actual_sha()
        plan = make_l3_plan()
        plan["plan_sha256"] = stable_sha({k: plan[k] for k in sorted(plan) if k != "plan_sha256"})
        from audit.csi1000_layered_search_reconcile import verify_sha256sums

        p0_path = Path(p0_summary).resolve()
        p0 = json.loads(p0_path.read_text(encoding="utf-8"))
        require_p0_pass(p0, p0.get("verified_cells"))
        require(
            p0_prefix
            == (f"csi1000_layered_search/csi1000_layered_search_v1/" f"{p0['run_id']}/p0/{p0['code_sha'][:12]}"),
            "STOP_L3_P0_PREFIX_AND_SUMMARY_MISMATCH",
        )
        export_root = p0_path.parents[2]
        if (export_root / "SHA256SUMS.txt").exists():
            verify_sha256sums(export_root)
        verify_export_cells(
            export_root,
            {c["cell_key"] for c in enumerate_cells("p0")},
            "P0",
        )
        provider = _provider_from_p0(p0)
        anchor = {"candidate_id": ANCHOR_CANDIDATE, "model_params": dict(ANCHOR_MODEL_PARAMS)}
        meta = {
            "run_id": run_id,
            "code_sha": sha,
            "plan_sha256": plan["plan_sha256"],
            "provider_fingerprint": provider,
            "source_snapshot_sha256": "",
            "quote_sha256": "",
            "p0_prefix": p0_prefix,
            "reserve_per_fit": usd_per_fit_reserve,
            "spent_usd": smoke_actual_usd + nonfit_reserve_usd,
        }
        if mode == "smoke":
            require(get_marker_remote.remote(meta, "smoke_pass") is None, "STOP_L3_SMOKE_ALREADY_DONE")
            # The 125% calibration rule applies to the full run, not the smoke itself.
            _run_l3_smoke(meta, max_workers, anchor)
        else:
            require(smoke_actual_usd > 0, "STOP_L3_SMOKE_ACTUAL_BILL_REQUIRED")
            require(
                2 * usd_per_fit_reserve >= 1.25 * smoke_actual_usd,
                "STOP_L3_UNDERQUOTED_FIT_RESERVE: require >=125% of average billed smoke fit",
            )
            _run_l3_complete(meta, max_workers, anchor, [max_fit_waves] if max_fit_waves else None)
        print("L3_DONE", flush=True)

    def _provider_from_p0(p0: dict) -> str:
        rows = p0.get("verified_cells", [])
        require(rows, "STOP_L3_P0_CELLS_MISSING")
        prints = {r.get("provider_fingerprint") for r in rows}
        require(len(prints) == 1 and all(prints), "STOP_L3_P0_PROVIDER_AMBIGUOUS")
        return prints.pop()

else:
    # Allows import/py_compile/--help in a machine without Modal credentials.
    app = None
