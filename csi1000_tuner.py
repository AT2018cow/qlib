"""CSI1000 Stage-A LightGBM tuner.

The first stage is deliberately cheap: one deterministic fit per candidate per
purged temporal fold. It keeps the production-aligned target, execution,
portfolio, and canonical metrics frozen, and only changes the registered
LightGBM parameter subset in csi1000_tuner_core.py.

Do not use this fold screen as a final portfolio backtest. Top candidates must
later pass full freq20 rolling evaluation and the fixed calendar-phase screen.
"""
from __future__ import annotations

from bisect import bisect_left
from pathlib import Path

import modal

from csi1000_tuner_core import (
    LABEL_HORIZON,
    MARKET,
    N_DROP,
    PROTOCOL_VERSION,
    REPORT_REPRO_ATOL,
    REPORT_REPRO_RTOL,
    REPRO_GATE_VERSION,
    TOPK,
    build_stage_a_folds,
    expanded_candidate_specs,
    frozen_protocol,
    numeric_reproducibility_diagnostics,
    rank_candidates,
    ranking_contract,
    reserved_tail,
    select_stage_b_candidates,
    smoke_candidate_specs,
    summarize_candidate,
    validate_reproducibility_pairs,
)

APP_NAME = "qlib-csi1000-stage-a-tuner"
VOL_NAME = "qlib-cn-data"
VOL_ROOT = Path("/vol")
DATA_DIR = VOL_ROOT / "cn_data"
MLRUNS_DIR = VOL_ROOT / "mlruns"
ARTIFACT_ROOT = VOL_ROOT / "csi1000_tuner" / "stage_a"
YAML_PATH = "/root/qlib/examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158_csi500.yaml"

vol = modal.Volume.from_name(VOL_NAME, create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("build-essential")
    .env({"MLFLOW_ALLOW_FILE_STORE": "true"})
    .pip_install(
        "numpy==1.26.4",
        "cython",
        "pandas==2.2.3",
        "pyyaml",
        "ruamel.yaml",
        "fire",
        "lightgbm",
        "mlflow",
        "dill",
        "filelock",
        "tqdm",
        "loguru",
        "joblib",
        "pyarrow",
        "pydantic-settings",
        "redis",
        "python-redis-lock",
        "pymongo",
        "gym",
        "cvxpy",
        "matplotlib",
        "jupyter",
        "nbconvert",
        "setuptools-scm",
        "akshare",
    )
    .pip_install("torch")
    .add_local_dir(
        ".",
        remote_path="/root/qlib",
        copy=True,
        ignore=lambda path: (
            str(path).endswith((".cpp", ".so"))
            or any(part in str(path) for part in (".venv", "mlruns", "__pycache__"))
            or str(path).startswith(".git/")
            or "/.git/" in str(path)
            or str(path) in ("modal_qlib_cn_a10g.py", "freq_experiment.py", "csi1000_tuner.py")
        ),
    )
    .run_commands(
        "cd /root/qlib && pip install . --no-build-isolation --no-deps",
        (
            "cp /root/qlib/qlib_audit_fixes.py /root/qlib/qlib_live_retrain.py "
            "/root/qlib/board_rules.py /root/qlib/board_execution.py "
            "/root/qlib/portfolio_performance.py /root/qlib/csi1000_tuner_core.py /root/"
        ),
    )
)

app = modal.App(APP_NAME, image=image)


def _load_task(model_params: dict | None = None) -> dict:
    """Load Alpha158/raw-20d CSI1000 task and enforce deterministic LightGBM."""
    from ruamel.yaml import YAML

    with open(YAML_PATH) as handle:
        cfg = YAML(typ="safe", pure=True).load(handle)

    dk = cfg["task"]["dataset"]["kwargs"]
    handler = dk["handler"]["kwargs"]
    handler["instruments"] = MARKET
    handler["label"] = ["Ref($close, -20)/$close - 1"]
    cfg["qlib_init"] = {"provider_uri": str(DATA_DIR), "region": "cn"}
    cfg["qlib_init"]["exp_manager"] = {
        "class": "MLflowExpManager",
        "module_path": "qlib.workflow.expm",
        "kwargs": {"uri": f"file:{MLRUNS_DIR}", "default_exp_name": "qlib-csi1000-tuner"},
    }

    from qlib_live_retrain import apply_lgb_reproducibility

    apply_lgb_reproducibility(cfg)
    if model_params is not None:
        cfg["task"]["model"]["kwargs"].update(dict(model_params))
        # Candidate specs are not allowed to override deterministic controls.
        apply_lgb_reproducibility(cfg)
    return cfg


@app.function(volumes={str(VOL_ROOT): vol}, cpu=4, memory=8192, timeout=4 * 3600)
def prepare(force: bool = True):
    """Ensure a single committed provider snapshot for the whole Stage-A run."""
    import shutil
    import tarfile

    import requests

    marker = DATA_DIR / ".chenditc"
    if not force and marker.exists():
        print("[data] reuse committed provider snapshot")
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    url = "https://github.com/chenditc/investment_data/releases/latest/download/qlib_bin.tar.gz"
    archive = Path("/tmp/chenditc.tar.gz")
    with requests.get(url, stream=True, timeout=600) as response:
        response.raise_for_status()
        with archive.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1 << 20):
                handle.write(chunk)

    extract = Path("/tmp/chenditc_extract")
    if extract.exists():
        shutil.rmtree(extract)
    extract.mkdir(parents=True)
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(extract)

    base = extract
    if not (base / "features").exists():
        for sub in base.iterdir():
            if sub.is_dir() and (sub / "features").exists():
                base = sub
                break

    for name in ("features", "calendars", "instruments"):
        target = DATA_DIR / name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(base / name, target)
    marker.write_text("latest")
    vol.commit()

    calendar = (DATA_DIR / "calendars" / "day.txt").read_text().strip().splitlines()
    print(f"[data] provider snapshot ready through {calendar[-1][:10]}")


def _stable_json_sha256(obj) -> str:
    import hashlib
    import json

    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str).encode()
    ).hexdigest()


def _signal_sha256(signal) -> str:
    import hashlib
    import struct

    series = signal.sort_index()
    digest = hashlib.sha256()
    digest.update(b"qlib-signal-v1\n")
    for idx, value in series.items():
        parts = idx if isinstance(idx, tuple) else (idx,)
        for part in parts:
            digest.update(str(part).encode())
            digest.update(b"\x1f")
        digest.update(struct.pack("!d", float(value)))
        digest.update(b"\n")
    return digest.hexdigest()


def _frame_sha256(frame) -> str:
    import hashlib

    text = frame.sort_index().to_csv(
        index=True,
        float_format="%.17g",
        date_format="%Y-%m-%dT%H:%M:%S.%f",
    )
    return hashlib.sha256(text.encode()).hexdigest()


def _write_series_artifact(signal, path: Path) -> dict:
    import hashlib

    path.parent.mkdir(parents=True, exist_ok=True)
    signal.sort_index().rename("score").to_frame().to_parquet(path, index=True)
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "content_sha256": _signal_sha256(signal),
        "rows": int(len(signal)),
    }


def _write_report_artifact(report, path: Path) -> dict:
    import hashlib

    path.parent.mkdir(parents=True, exist_ok=True)
    report.sort_index().to_parquet(path, index=True)
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "content_sha256": _frame_sha256(report),
        "rows": int(len(report)),
        "columns": [str(column) for column in report.columns],
    }


def _runtime_manifest(cutoff: str) -> dict:
    import hashlib
    import importlib.metadata as metadata
    import platform

    import csi1000_tuner_core
    from qlib_live_retrain import LGB_REPRO_PARAMS, provider_training_fingerprint

    def version(name: str) -> str:
        try:
            return metadata.version(name)
        except metadata.PackageNotFoundError:
            return "unknown"

    cfg = _load_task()
    source_path = Path(__file__)
    core_path = Path(csi1000_tuner_core.__file__)
    manifest = {
        "manifest_version": "csi1000_tuner_repro_v2",
        "protocol": frozen_protocol(),
        "provider_cutoff": cutoff,
        "provider_fingerprint": provider_training_fingerprint(DATA_DIR, MARKET, cutoff),
        "base_model_config_sha256": _stable_json_sha256(cfg["task"]["model"]),
        "lgb_repro_params": dict(LGB_REPRO_PARAMS),
        "runtime": {
            "python": platform.python_version(),
            "pyqlib": version("pyqlib"),
            "lightgbm": version("lightgbm"),
            "numpy": version("numpy"),
            "pandas": version("pandas"),
        },
        "tuner_source_sha256": (
            hashlib.sha256(source_path.read_bytes()).hexdigest() if source_path.is_file() else "unavailable"
        ),
        "core_source_sha256": (
            hashlib.sha256(core_path.read_bytes()).hexdigest() if core_path.is_file() else "unavailable"
        ),
    }
    manifest["snapshot_token"] = _stable_json_sha256(manifest)
    return manifest


def _snapshot_manifest_path() -> Path:
    return ARTIFACT_ROOT / "provider_snapshot.json"


def _publish_snapshot_manifest(manifest: dict) -> None:
    import json

    path = _snapshot_manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    vol.commit()


def _worker_assert_snapshot(expected: str) -> dict:
    import json

    try:
        vol.reload()
    except Exception as exc:
        print(f"worker Volume reload skipped: {exc}")
    path = _snapshot_manifest_path()
    if not path.is_file():
        raise RuntimeError("Stage-A provider snapshot manifest is missing")
    manifest = json.loads(path.read_text())
    if manifest.get("snapshot_token") != expected:
        raise RuntimeError(
            f"Stage-A provider snapshot mismatch: expected={expected} actual={manifest.get('snapshot_token')}"
        )
    return manifest


def _run_signal_backtest(signal, *, execution_start: str, execution_end: str):
    import qlib
    from qlib.backtest import backtest as normal_backtest

    from board_execution import research_exchange

    qlib.init(**{**_load_task()["qlib_init"], "skip_if_reg": True})
    executor = {
        "class": "SimulatorExecutor",
        "module_path": "qlib.backtest.executor",
        "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True},
    }
    strategy = {
        "class": "TopkDropoutStrategy",
        "module_path": "qlib.contrib.strategy",
        "kwargs": {
            "signal": signal,
            "topk": TOPK,
            "n_drop": N_DROP,
            "forbid_all_trade_at_limit": False,
        },
    }
    portfolio_metrics, _ = normal_backtest(
        strategy=strategy,
        executor=executor,
        start_time=execution_start,
        end_time=execution_end,
        account=100000000,
        benchmark="SH000852",
        exchange_kwargs=research_exchange(execution_start, execution_end, codes=MARKET),
    )
    return portfolio_metrics["1day"][0]


def _turnover_cost_summary(report) -> dict:
    turnover_col = next(
        (name for name in ("turnover", "total_turnover") if name in report.columns),
        None,
    )
    if turnover_col is None:
        raise RuntimeError("fold report does not contain turnover")
    turnover = report[turnover_col].dropna()
    cost = report["cost"].dropna() if "cost" in report.columns else None
    if turnover.empty or cost is None or cost.empty:
        raise RuntimeError("fold report has empty turnover/cost")
    return {
        "mean_turnover": round(float(turnover.mean()), 6),
        "turnover_source": turnover_col,
        "total_cost_sum": round(float(cost.sum()), 6),
    }


def _recompute_result_metrics(report, fold: dict) -> dict:
    """Recompute every reusable metric from the verified raw report."""
    from portfolio_performance import portfolio_performance

    perf = portfolio_performance(
        report,
        initial_cash=100000000,
        backtest_start=fold["execution"][0],
    )
    if (
        perf["account_return_max_error"] is None
        or abs(float(perf["account_return_max_error"])) > 1e-12
    ):
        raise RuntimeError("reusable report fails account consistency")
    return {**perf, **_turnover_cost_summary(report)}


def _metric_values_match(saved, recomputed) -> bool:
    import math

    if saved is None or recomputed is None:
        return saved is None and recomputed is None
    if (
        isinstance(saved, (int, float))
        and not isinstance(saved, bool)
        and isinstance(recomputed, (int, float))
        and not isinstance(recomputed, bool)
    ):
        return math.isclose(
            float(saved),
            float(recomputed),
            rel_tol=0.0,
            abs_tol=1e-12,
        )
    return saved == recomputed


def _assert_reusable_metrics(payload: dict, report, fold: dict) -> dict:
    """Fail closed if result.json disagrees with independently recomputed metrics."""
    recomputed = _recompute_result_metrics(report, fold)
    mismatches = []
    for key, value in recomputed.items():
        if key not in payload or not _metric_values_match(payload.get(key), value):
            mismatches.append(key)
    if mismatches:
        raise RuntimeError(
            "reusable result metadata disagrees with raw report for "
            f"{fold['fold_id']}: {mismatches}"
        )
    return recomputed


def _result_dir(
    snapshot_token: str,
    candidate_id: str,
    fold_id: str,
    *,
    artifact_namespace: str | None = None,
) -> Path:
    root = ARTIFACT_ROOT / snapshot_token
    if artifact_namespace:
        namespace = Path(str(artifact_namespace))
        if namespace.is_absolute() or ".." in namespace.parts:
            raise ValueError("artifact_namespace must be a relative safe path")
        root = root / namespace
    return root / candidate_id / fold_id


def _validate_reusable_result(payload: dict, args: dict) -> bool:
    if not payload:
        return False
    if payload.get("protocol") != PROTOCOL_VERSION:
        return False
    if payload.get("snapshot_token") != args["snapshot_token"]:
        return False
    if payload.get("candidate_id") != args["candidate"]["candidate_id"]:
        return False
    if payload.get("fold_id") != args["fold"]["fold_id"]:
        return False
    if payload.get("fold") != args["fold"]:
        return False
    if payload.get("candidate") != args["candidate"]:
        return False
    return bool(payload.get("signal_artifact") and payload.get("report_artifact"))


def _load_verified_report_artifact(meta: dict):
    import hashlib

    import pandas as pd

    path = Path(meta["path"])
    if not path.is_file():
        raise RuntimeError(f"repro report artifact missing: {path}")
    if hashlib.sha256(path.read_bytes()).hexdigest() != meta["sha256"]:
        raise RuntimeError(f"repro report byte hash mismatch: {path}")
    report = pd.read_parquet(path)
    if _frame_sha256(report) != meta["content_sha256"]:
        raise RuntimeError(f"repro report content hash mismatch: {path}")
    return report


def _compare_repro_reports(repeat_a: list[dict], repeat_b: list[dict]) -> dict:
    """Compare baseline backtest reports semantically, preserving float diagnostics."""
    import pandas as pd

    left = {row["fold_id"]: row for row in repeat_a}
    right = {row["fold_id"]: row for row in repeat_b}
    if set(left) != set(right):
        raise RuntimeError("repro report fold sets do not match")

    fold_details = {}
    all_passed = True
    all_exact = True
    for fold_id in sorted(left):
        a = left[fold_id]
        b = right[fold_id]
        report_a = _load_verified_report_artifact(a["report_artifact"])
        report_b = _load_verified_report_artifact(b["report_artifact"])
        fold = a["fold"]

        detail = {
            "passed": True,
            "report_content_sha256_a": a["report_artifact"]["content_sha256"],
            "report_content_sha256_b": b["report_artifact"]["content_sha256"],
            "exact_content_match": (
                a["report_artifact"]["content_sha256"]
                == b["report_artifact"]["content_sha256"]
            ),
            "structure_match": True,
            "canonical_metrics_match": True,
            "columns": {},
            "errors": [],
        }
        all_exact = all_exact and detail["exact_content_match"]

        if (
            not report_a.index.equals(report_b.index)
            or list(report_a.index.names) != list(report_b.index.names)
        ):
            detail["passed"] = False
            detail["structure_match"] = False
            detail["errors"].append("index_mismatch")

        if list(report_a.columns) != list(report_b.columns):
            detail["passed"] = False
            detail["structure_match"] = False
            detail["errors"].append("column_mismatch")
        elif [str(dtype) for dtype in report_a.dtypes] != [
            str(dtype) for dtype in report_b.dtypes
        ]:
            detail["passed"] = False
            detail["structure_match"] = False
            detail["errors"].append("dtype_mismatch")

        if detail["structure_match"]:
            for column in report_a.columns:
                series_a = report_a[column]
                series_b = report_b[column]
                if pd.api.types.is_numeric_dtype(series_a.dtype):
                    scale = 100000000.0 if str(column) == "account" else 1.0
                    diag = numeric_reproducibility_diagnostics(
                        (float(value) / scale for value in series_a.tolist()),
                        (float(value) / scale for value in series_b.tolist()),
                        rtol=REPORT_REPRO_RTOL,
                        atol=REPORT_REPRO_ATOL,
                    )
                    diag["normalization_scale"] = scale
                    detail["columns"][str(column)] = diag
                    if not diag["passed"]:
                        detail["passed"] = False
                        detail["errors"].append(f"numeric_tolerance:{column}")
                else:
                    exact = series_a.equals(series_b)
                    detail["columns"][str(column)] = {
                        "passed": bool(exact),
                        "exact_match": bool(exact),
                        "kind": "non_numeric",
                    }
                    if not exact:
                        detail["passed"] = False
                        detail["errors"].append(f"non_numeric_mismatch:{column}")

        metrics_a = _recompute_result_metrics(report_a, fold)
        metrics_b = _recompute_result_metrics(report_b, fold)
        metric_mismatches = [
            key
            for key in metrics_a
            if key not in metrics_b
            or not _metric_values_match(metrics_a[key], metrics_b[key])
        ]
        if metric_mismatches:
            detail["passed"] = False
            detail["canonical_metrics_match"] = False
            detail["errors"].append(
                "canonical_metric_mismatch:" + ",".join(metric_mismatches)
            )
        detail["canonical_metrics"] = metrics_b

        if not detail["passed"]:
            all_passed = False
        fold_details[fold_id] = detail

    return {
        "passed": all_passed,
        "gate_version": REPRO_GATE_VERSION,
        "rtol": REPORT_REPRO_RTOL,
        "atol": REPORT_REPRO_ATOL,
        "account_normalization": 100000000.0,
        "all_report_content_exact": all_exact,
        "folds": fold_details,
    }


def _load_reusable_result(args: dict):
    import hashlib
    import json

    import pandas as pd

    result_path = _result_dir(
        args["snapshot_token"],
        args["candidate"]["candidate_id"],
        args["fold"]["fold_id"],
    ) / "result.json"
    if not result_path.is_file():
        return None
    payload = json.loads(result_path.read_text())
    if not _validate_reusable_result(payload, args):
        return None

    signal_meta = payload["signal_artifact"]
    report_meta = payload["report_artifact"]
    signal_path = Path(signal_meta["path"])
    report_path = Path(report_meta["path"])
    if not signal_path.is_file() or not report_path.is_file():
        return None
    if hashlib.sha256(signal_path.read_bytes()).hexdigest() != signal_meta["sha256"]:
        return None
    if hashlib.sha256(report_path.read_bytes()).hexdigest() != report_meta["sha256"]:
        return None

    signal = pd.read_parquet(signal_path)["score"].sort_index()
    report = pd.read_parquet(report_path)
    if _signal_sha256(signal) != signal_meta["content_sha256"]:
        return None
    if _frame_sha256(report) != report_meta["content_sha256"]:
        return None

    recomputed = _assert_reusable_metrics(payload, report, args["fold"])
    payload.update(recomputed)
    payload["source"] = "artifact_reuse_verified"
    return payload


@app.function(
    volumes={str(VOL_ROOT): vol},
    cpu=8,
    memory=24576,
    timeout=2 * 3600,
    max_containers=24,
)
def stage_a_fold_worker(args: dict):
    """Train one candidate once on one purged fold and retain all audit artifacts."""
    import json

    import qlib
    from qlib.data.dataset import Dataset
    from qlib.model.base import Model
    from qlib.utils import init_instance_by_config

    from portfolio_performance import portfolio_performance
    from qlib_audit_fixes import purge_cfg_splits, read_trading_calendar

    manifest = _worker_assert_snapshot(args["snapshot_token"])
    if args.get("resume", True):
        reusable = _load_reusable_result(args)
        if reusable is not None:
            return reusable

    candidate = args["candidate"]
    fold = args["fold"]
    qlib.init(**{**_load_task(candidate["model_params"])["qlib_init"], "skip_if_reg": True})
    calendar = read_trading_calendar(DATA_DIR)

    cfg = _load_task(candidate["model_params"])
    dataset_cfg = cfg["task"]["dataset"]["kwargs"]
    segments = dataset_cfg["segments"]
    handler = dataset_cfg["handler"]["kwargs"]
    segments["train"] = list(fold["train"])
    segments["valid"] = list(fold["valid"])
    segments["test"] = list(fold["signal"])
    handler["start_time"] = "2015-01-01"
    handler["end_time"] = fold["signal"][1]
    handler["fit_start_time"] = fold["train"][0]
    handler["fit_end_time"] = fold["train"][1]
    purge_cfg_splits(cfg, calendar, horizon=LABEL_HORIZON)

    model_config_sha256 = _stable_json_sha256(cfg["task"]["model"])
    model = init_instance_by_config(cfg["task"]["model"], accept_types=Model)
    dataset = init_instance_by_config(cfg["task"]["dataset"], accept_types=Dataset)
    model.fit(dataset)
    prediction = model.predict(dataset, segment="test")
    if prediction.empty:
        raise RuntimeError(f"{candidate['candidate_id']} {fold['fold_id']}: empty prediction")
    first_signal = str(prediction.index.get_level_values(0).min())[:10]
    last_signal = str(prediction.index.get_level_values(0).max())[:10]
    if [first_signal, last_signal] != fold["signal"]:
        raise RuntimeError(
            f"{candidate['candidate_id']} {fold['fold_id']}: prediction coverage "
            f"{first_signal}~{last_signal} != {fold['signal']}"
        )

    root = _result_dir(
        args["snapshot_token"],
        candidate["candidate_id"],
        fold["fold_id"],
        artifact_namespace=args.get("artifact_namespace"),
    )
    signal_artifact = _write_series_artifact(prediction, root / "signal.parquet")
    report = _run_signal_backtest(
        prediction,
        execution_start=fold["execution"][0],
        execution_end=fold["execution"][1],
    )
    perf = portfolio_performance(
        report,
        initial_cash=100000000,
        backtest_start=fold["execution"][0],
    )
    if perf["account_return_max_error"] is None or abs(float(perf["account_return_max_error"])) > 1e-12:
        raise RuntimeError(
            f"{candidate['candidate_id']} {fold['fold_id']}: account consistency failed"
        )
    turnover_cost = _turnover_cost_summary(report)
    report_artifact = _write_report_artifact(report, root / "report.parquet")

    best_iteration = None
    if getattr(model, "model", None) is not None:
        value = getattr(model.model, "best_iteration", None)
        best_iteration = int(value) if value else None

    payload = {
        "protocol": PROTOCOL_VERSION,
        "snapshot_token": manifest["snapshot_token"],
        "candidate_id": candidate["candidate_id"],
        "candidate": candidate,
        "fold_id": fold["fold_id"],
        "fold": fold,
        "source": "deterministic_fit",
        "artifact_namespace": args.get("artifact_namespace"),
        "model_config_sha256": model_config_sha256,
        "best_iteration": best_iteration,
        "signal_sha256": signal_artifact["content_sha256"],
        "signal_artifact": signal_artifact,
        "report_artifact": report_artifact,
        **perf,
        **turnover_cost,
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "result.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    vol.commit()
    return payload


@app.function(volumes={str(VOL_ROOT): vol}, cpu=4, memory=8192, timeout=12 * 3600)
def stage_a_screen_driver(
    expanded: bool = False,
    candidate_count: int = 12,
    force_data: bool = True,
    resume: bool = True,
):
    """Run the 12-candidate smoke screen or a capped deterministic expansion."""
    import json

    from qlib_audit_fixes import read_trading_calendar

    prepare.remote(force=force_data)
    vol.reload()
    calendar = read_trading_calendar(DATA_DIR)
    folds = build_stage_a_folds(calendar)
    tail = reserved_tail(calendar, folds)
    manifest = _runtime_manifest(calendar[-1])
    _publish_snapshot_manifest(manifest)

    if expanded:
        candidates = expanded_candidate_specs(count=int(candidate_count))
    else:
        if int(candidate_count) != 12:
            raise ValueError("smoke screen is frozen at exactly 12 candidates")
        candidates = smoke_candidate_specs()

    baseline = next((candidate for candidate in candidates if candidate["is_baseline"]), None)
    if baseline is None:
        raise RuntimeError("Stage-A candidate set is missing the frozen baseline")

    gate_jobs_a = [
        {
            "candidate": baseline,
            "fold": fold,
            "snapshot_token": manifest["snapshot_token"],
            "resume": False,
            "artifact_namespace": f"_repro/{REPRO_GATE_VERSION}/repeat_a",
        }
        for fold in folds
    ]
    gate_jobs_b = [
        {
            "candidate": baseline,
            "fold": fold,
            "snapshot_token": manifest["snapshot_token"],
            "resume": False,
            # Repeat B remains the canonical retained baseline artifact.
            "artifact_namespace": None,
        }
        for fold in folds
    ]
    print(
        f"[stage-a] reproducibility preflight: baseline x {len(folds)} folds x2"
    )
    repeat_a = list(stage_a_fold_worker.map(gate_jobs_a))
    repeat_b = list(stage_a_fold_worker.map(gate_jobs_b))

    reproducibility_gate = validate_reproducibility_pairs(repeat_a, repeat_b)
    report_validation = _compare_repro_reports(repeat_a, repeat_b)
    reproducibility_gate["report_validation"] = report_validation
    reproducibility_gate["passed"] = bool(
        reproducibility_gate["passed"] and report_validation["passed"]
    )

    gate_root = ARTIFACT_ROOT / manifest["snapshot_token"]
    gate_root.mkdir(parents=True, exist_ok=True)
    gate_path = gate_root / "reproducibility_gate.json"
    gate_path.write_text(json.dumps(reproducibility_gate, indent=2, ensure_ascii=False))
    vol.commit()
    if not reproducibility_gate["passed"]:
        failed = [
            fold_id
            for fold_id, detail in report_validation["folds"].items()
            if not detail["passed"]
        ]
        raise RuntimeError(
            "Stage-A baseline report semantic reproducibility failed for "
            f"{failed}; diagnostics={gate_path}"
        )

    # The second baseline repeat is now the canonical retained baseline result.
    # Avoid a third baseline fit; all remaining candidates follow normal resume policy.
    jobs = [
        {
            "candidate": candidate,
            "fold": fold,
            "snapshot_token": manifest["snapshot_token"],
            "resume": bool(resume),
        }
        for candidate in candidates
        if candidate["candidate_id"] != baseline["candidate_id"]
        for fold in folds
    ]
    max_new_fits = reproducibility_gate["total_gate_fits"] + len(jobs)
    print(
        f"[stage-a] candidates={len(candidates)} folds={len(folds)} "
        f"fits<= {max_new_fits} snapshot={manifest['snapshot_token'][:12]}"
    )

    outputs = list(repeat_b) + list(stage_a_fold_worker.map(jobs))
    by_candidate = {candidate["candidate_id"]: [] for candidate in candidates}
    for result in outputs:
        candidate_id = result["candidate_id"]
        if candidate_id not in by_candidate:
            raise RuntimeError(f"unexpected candidate result: {candidate_id}")
        by_candidate[candidate_id].append(result)

    summaries = [
        summarize_candidate(
            candidate,
            by_candidate[candidate["candidate_id"]],
        )
        for candidate in candidates
    ]
    ranked = rank_candidates(summaries, stage="screen")
    promote_n = min(8, len(ranked))
    stage_b = select_stage_b_candidates(ranked, top_n=promote_n)

    payload = {
        "protocol": PROTOCOL_VERSION,
        "screen_kind": "expanded" if expanded else "smoke",
        "manifest": manifest,
        "folds": folds,
        "reserved_tail": tail,
        "candidate_count": len(candidates),
        "fold_count": len(folds),
        "max_new_fits": max_new_fits,
        "reproducibility_gate": reproducibility_gate,
        "ranking_contract": ranking_contract("screen"),
        "ranked_candidates": ranked,
        "stage_b_preview": stage_b,
    }
    root = ARTIFACT_ROOT / manifest["snapshot_token"]
    root.mkdir(parents=True, exist_ok=True)
    (root / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    vol.commit()
    return payload


@app.local_entrypoint()
def main(
    expanded: bool = False,
    candidate_count: int = 12,
    force_data: bool = True,
    resume: bool = True,
):
    """Launch Stage-A through normal Modal app boundaries and export review JSON."""
    import json

    payload = stage_a_screen_driver.remote(
        expanded=expanded,
        candidate_count=candidate_count,
        force_data=force_data,
        resume=resume,
    )
    token = payload["manifest"]["snapshot_token"]
    out = Path("results") / "csi1000_tuner"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"stage_a_{payload['screen_kind']}_{token[:16]}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"[stage-a] exported {path}")
