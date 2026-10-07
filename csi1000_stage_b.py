"""CSI1000 Stage-B reserved-tail confirmation runner.

Workflow:
  1) modal run --detach csi1000_stage_b.py --preflight-only
  2) after the pushed preflight is reviewed:
     modal run --detach csi1000_stage_b.py

The full run requires the passing baseline/phase-0 double-run preflight produced
under the exact same Stage-B manifest. Candidate selection is frozen in
csi1000_stage_b_core.py; this runner does not search or mutate candidates.
"""
from __future__ import annotations

from bisect import bisect_left
from pathlib import Path

import modal

from csi1000_stage_b_core import (
    ACCOUNT_ERROR_TOLERANCE,
    BENCHMARK,
    LABEL_HORIZON,
    MARKET,
    MODEL_NUM_THREADS,
    N_DROP,
    REFERENCE_PHASES,
    REPORT_REPRO_ATOL,
    REPORT_REPRO_RTOL,
    RESERVED_EXECUTION_END,
    RESERVED_EXECUTION_START,
    RESERVED_SIGNAL_ANCHOR,
    STAGE_A_EXPANDED_AUDIT,
    STAGE_A_EXPANDED_RESULT,
    STAGE_B_PROTOCOL_VERSION,
    STARTER_CONTAINER_LIMIT,
    TOPK,
    TRAIN_START,
    VALIDATION_SESSIONS,
    WORKER_CPU,
    WORKER_MAX_CONTAINERS,
    WORKER_MEMORY_MIB,
    WORKER_RETRIES,
    validate_worker_resources,
    build_phase_jobs,
    frozen_stage_b_protocol,
    rank_stage_b_candidates,
    stage_b_candidates,
    stage_b_fit_budget,
    validate_reserved_tail,
    validate_stage_a_evidence,
)

APP_NAME = "qlib-csi1000-stage-b"
VOL_NAME = "qlib-cn-data"
VOL_ROOT = Path("/vol")
DATA_DIR = VOL_ROOT / "cn_data"
MLRUNS_DIR = VOL_ROOT / "mlruns"
ARTIFACT_ROOT = VOL_ROOT / "csi1000_stage_b"
YAML_PATH = "/root/qlib/examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158_csi500.yaml"

vol = modal.Volume.from_name(VOL_NAME, create_if_missing=False)

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
            or str(path)
            in ("modal_qlib_cn_a10g.py", "freq_experiment.py", "csi1000_stage_b.py")
        ),
    )
    .run_commands(
        "cd /root/qlib && pip install . --no-build-isolation --no-deps",
        (
            "cp /root/qlib/qlib_audit_fixes.py /root/qlib/qlib_live_retrain.py "
            "/root/qlib/board_rules.py /root/qlib/board_execution.py "
            "/root/qlib/portfolio_performance.py /root/qlib/deterministic_strategy.py "
            "/root/qlib/csi1000_tuner_core.py /root/qlib/csi1000_stage_b_core.py /root/"
        ),
    )
)

app = modal.App(APP_NAME, image=image)


def _stable_json_sha256(obj) -> str:
    import hashlib
    import json

    return hashlib.sha256(
        json.dumps(
            obj,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=str,
        ).encode()
    ).hexdigest()


def _load_task(model_params: dict | None = None) -> dict:
    from ruamel.yaml import YAML

    with open(YAML_PATH) as handle:
        cfg = YAML(typ="safe", pure=True).load(handle)

    dataset_cfg = cfg["task"]["dataset"]["kwargs"]
    handler = dataset_cfg["handler"]["kwargs"]
    handler["instruments"] = MARKET
    handler["label"] = ["Ref($close, -20)/$close - 1"]
    cfg["qlib_init"] = {"provider_uri": str(DATA_DIR), "region": "cn"}
    cfg["qlib_init"]["exp_manager"] = {
        "class": "MLflowExpManager",
        "module_path": "qlib.workflow.expm",
        "kwargs": {
            "uri": f"file:{MLRUNS_DIR}",
            "default_exp_name": "qlib-csi1000-stage-b",
        },
    }

    from qlib_live_retrain import apply_lgb_reproducibility

    apply_lgb_reproducibility(cfg)
    # Match Stage-A _load_task exactly: the manifest base config is hashed
    # before any candidate overlay; actual candidate fits then overlay only
    # the frozen tunable params and re-assert deterministic controls.
    if model_params is not None:
        cfg["task"]["model"]["kwargs"].update(dict(model_params))
        apply_lgb_reproducibility(cfg)
    actual_threads = int(cfg["task"]["model"]["kwargs"].get("num_threads", -1))
    if actual_threads != MODEL_NUM_THREADS:
        raise RuntimeError(
            f"Stage-B LightGBM num_threads drift: {actual_threads} != {MODEL_NUM_THREADS}"
        )
    return cfg


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


def _decision_audit(decisions) -> dict:
    rows = []
    total_orders = 0
    for decision_index, decision in enumerate(decisions):
        orders = []
        for order_index, order in enumerate(decision.get_decision()):
            orders.append(
                {
                    "order_index": order_index,
                    "stock_id": str(order.stock_id),
                    "direction": int(order.direction),
                    "amount": float(order.amount),
                    "deal_amount": float(order.deal_amount),
                    "factor": None if order.factor is None else float(order.factor),
                    "start_time": str(order.start_time),
                    "end_time": str(order.end_time),
                }
            )
        total_orders += len(orders)
        rows.append(
            {
                "decision_index": decision_index,
                "start_time": str(decision.start_time),
                "end_time": str(decision.end_time),
                "orders": orders,
            }
        )
    return {
        "audit_version": "topk_decisions_v1",
        "decision_count": len(rows),
        "order_count": total_orders,
        "decisions": rows,
        "content_sha256": _stable_json_sha256(rows),
    }


def _write_parquet_bytes(frame, path: Path) -> str:
    """Serialize parquet off-Volume, then publish one closed file to the mount."""
    import hashlib
    import io
    import os
    import uuid

    path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    frame.to_parquet(buffer, index=True)
    data = buffer.getvalue()

    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    persisted = path.read_bytes()
    if persisted != data:
        raise RuntimeError(f"parquet write round-trip mismatch before commit: {path}")
    return hashlib.sha256(persisted).hexdigest()


def _write_series_artifact(signal, path: Path) -> dict:
    frame = signal.sort_index().rename("score").to_frame()
    return {
        "path": str(path),
        "sha256": _write_parquet_bytes(frame, path),
        "content_sha256": _signal_sha256(signal),
        "rows": int(len(signal)),
    }


def _write_report_artifact(report, path: Path) -> dict:
    ordered = report.sort_index()
    return {
        "path": str(path),
        "sha256": _write_parquet_bytes(ordered, path),
        "content_sha256": _frame_sha256(report),
        "rows": int(len(report)),
        "columns": [str(column) for column in report.columns],
    }


def _write_decision_artifact(audit: dict, path: Path) -> dict:
    import hashlib
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            audit["decisions"],
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            ensure_ascii=False,
        )
    )
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "content_sha256": audit["content_sha256"],
        "audit_version": audit["audit_version"],
        "decision_count": audit["decision_count"],
        "order_count": audit["order_count"],
    }


def _turnover_cost_summary(report) -> dict:
    turnover_col = next(
        (name for name in ("turnover", "total_turnover") if name in report.columns),
        None,
    )
    if turnover_col is None:
        raise RuntimeError("Stage-B report does not contain turnover")
    turnover = report[turnover_col].dropna()
    cost = report["cost"].dropna() if "cost" in report.columns else None
    if turnover.empty or cost is None or cost.empty:
        raise RuntimeError("Stage-B report has empty turnover/cost")
    return {
        "mean_turnover": round(float(turnover.mean()), 6),
        "turnover_source": turnover_col,
        "total_cost_sum": round(float(cost.sum()), 6),
    }


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
        return math.isclose(float(saved), float(recomputed), rel_tol=0.0, abs_tol=1e-12)
    return saved == recomputed


def _phase_metrics(report) -> dict:
    from portfolio_performance import portfolio_performance

    perf = portfolio_performance(
        report,
        initial_cash=100000000,
        backtest_start=RESERVED_EXECUTION_START,
    )
    error = perf.get("account_return_max_error")
    if error is None or abs(float(error)) > ACCOUNT_ERROR_TOLERANCE:
        raise RuntimeError("Stage-B report fails account consistency")
    return {**perf, **_turnover_cost_summary(report)}


def _report_compare_payload(report) -> dict:
    """Small in-memory semantic snapshot used by the preflight A/B gate."""
    import pandas as pd

    ordered = report.sort_index()
    if isinstance(ordered.index, pd.MultiIndex):
        index_values = [
            [str(part) for part in value]
            for value in ordered.index.tolist()
        ]
    else:
        index_values = [str(value) for value in ordered.index.tolist()]

    numeric = {}
    nonnumeric = {}
    for column in ordered.columns:
        key = str(column)
        series = ordered[column]
        if pd.api.types.is_numeric_dtype(series.dtype):
            numeric[key] = list(
                series.to_numpy(dtype=float, na_value=float("nan"))
            )
        else:
            nonnumeric[key] = [
                None if pd.isna(value) else str(value)
                for value in series.tolist()
            ]
    return {
        "index": index_values,
        "index_names": [str(name) for name in ordered.index.names],
        "columns": [str(column) for column in ordered.columns],
        "dtypes": [str(dtype) for dtype in ordered.dtypes],
        "numeric": numeric,
        "nonnumeric": nonnumeric,
    }


def _verify_phase_payload_artifacts(payload: dict) -> dict:
    """Verify the committed phase artifact set from the current Volume view."""
    signal = _load_verified_series_artifact(payload["signal_artifact"])
    _load_verified_decision_artifact(payload["decision_artifact"])
    report = _load_verified_report_artifact(payload["report_artifact"])

    metrics = _phase_metrics(report)
    expected_metrics = payload.get("phase_metrics") or {}
    mismatches = [
        key
        for key, value in metrics.items()
        if key not in expected_metrics
        or not _metric_values_match(expected_metrics.get(key), value)
    ]
    if mismatches:
        raise RuntimeError(
            f"Stage-B committed phase metric mismatch: {mismatches}"
        )

    result_path = Path(payload["result_path"])
    saved = _load_json(result_path)
    if not saved:
        raise RuntimeError(f"Stage-B committed phase result missing: {result_path}")
    if saved != {
        key: value
        for key, value in payload.items()
        if not key.startswith("_") and key != "durability_gate"
    }:
        raise RuntimeError(f"Stage-B committed phase result drift: {result_path}")

    return {
        "passed": True,
        "signal_rows": int(len(signal)),
        "report_rows": int(len(report)),
        "signal_content_sha256": payload["signal_artifact"]["content_sha256"],
        "report_content_sha256": payload["report_artifact"]["content_sha256"],
        "decision_content_sha256": payload["decision_artifact"]["content_sha256"],
    }


def _run_signal_backtest(signal):
    import qlib
    from qlib.backtest import collect_data

    from board_execution import research_exchange

    qlib.init(
        **{
            **_load_task(stage_b_candidates()[-1]["model_params"])["qlib_init"],
            "skip_if_reg": True,
        }
    )
    executor = {
        "class": "SimulatorExecutor",
        "module_path": "qlib.backtest.executor",
        "kwargs": {
            "time_per_step": "day",
            "generate_portfolio_metrics": True,
            "track_data": True,
        },
    }
    strategy = {
        "class": "DeterministicTopkDropoutStrategy",
        "module_path": "deterministic_strategy",
        "kwargs": {
            "signal": signal,
            "topk": TOPK,
            "n_drop": N_DROP,
            "forbid_all_trade_at_limit": False,
        },
    }
    return_value = {}
    decisions = list(
        collect_data(
            strategy=strategy,
            executor=executor,
            start_time=RESERVED_EXECUTION_START,
            end_time=RESERVED_EXECUTION_END,
            account=100000000,
            benchmark=BENCHMARK,
            exchange_kwargs=research_exchange(
                RESERVED_EXECUTION_START,
                RESERVED_EXECUTION_END,
                codes=MARKET,
            ),
            return_value=return_value,
        )
    )
    report = return_value["portfolio_dict"]["1day"][0]
    return report, _decision_audit(decisions)


def _load_json(path: Path):
    import json

    if not path.is_file():
        return None
    return json.loads(path.read_text())


def _runtime_manifest(calendar: list[str]) -> dict:
    import hashlib
    import importlib.metadata as metadata
    import platform

    import csi1000_stage_b_core
    import deterministic_strategy
    from qlib_live_retrain import LGB_REPRO_PARAMS, provider_training_fingerprint

    validate_reserved_tail(calendar)

    def version(name):
        try:
            return metadata.version(name)
        except metadata.PackageNotFoundError:
            return "unknown"

    expanded_path = Path("/root/qlib") / STAGE_A_EXPANDED_RESULT
    audit_path = Path("/root/qlib") / STAGE_A_EXPANDED_AUDIT
    if not expanded_path.is_file() or not audit_path.is_file():
        raise RuntimeError("committed Stage-A evidence files are missing from image")
    expanded = _load_json(expanded_path)
    audit = _load_json(audit_path)
    selection_validation = validate_stage_a_evidence(expanded, audit)

    source_path = Path(__file__)
    core_path = Path(csi1000_stage_b_core.__file__)
    strategy_path = Path(deterministic_strategy.__file__)

    # Stage-A manifest base_model_config_sha256 was computed from _load_task()
    # with no candidate overlay.  Compare the same layer here.  Candidate
    # configs are intentionally different from the base and are fingerprinted
    # separately below.
    base_model = _load_task(None)["task"]["model"]
    frozen_candidates = stage_b_candidates()
    candidate_model_config_sha256 = {
        candidate["candidate_id"]: _stable_json_sha256(
            _load_task(candidate["model_params"])["task"]["model"]
        )
        for candidate in frozen_candidates
    }

    current_runtime = {
        "python": platform.python_version(),
        "pyqlib": version("pyqlib"),
        "lightgbm": version("lightgbm"),
        "numpy": version("numpy"),
        "pandas": version("pandas"),
        "modal": version("modal"),
    }
    current_provider_fingerprint = provider_training_fingerprint(
        DATA_DIR, MARKET, RESERVED_EXECUTION_END
    )
    current_base_model_sha = _stable_json_sha256(base_model)
    current_strategy_sha = hashlib.sha256(strategy_path.read_bytes()).hexdigest()
    stage_a_manifest = expanded.get("manifest") or {}

    if current_provider_fingerprint != stage_a_manifest.get("provider_fingerprint"):
        raise RuntimeError("Stage-B provider fingerprint differs from frozen Stage-A snapshot")
    if current_base_model_sha != stage_a_manifest.get("base_model_config_sha256"):
        raise RuntimeError(
            "Stage-B unoverlaid base model config differs from frozen Stage-A base config"
        )
    if dict(LGB_REPRO_PARAMS) != (stage_a_manifest.get("lgb_repro_params") or {}):
        raise RuntimeError("Stage-B LightGBM reproducibility controls drifted from Stage A")
    if current_strategy_sha != stage_a_manifest.get(
        "deterministic_strategy_source_sha256"
    ):
        raise RuntimeError("Stage-B deterministic strategy source drifted from Stage A")
    for package in ("python", "pyqlib", "lightgbm", "numpy", "pandas"):
        if current_runtime[package] != (stage_a_manifest.get("runtime") or {}).get(package):
            raise RuntimeError(
                f"Stage-B runtime drift for {package}: "
                f"{current_runtime[package]} != "
                f"{(stage_a_manifest.get('runtime') or {}).get(package)}"
            )

    manifest = {
        "manifest_version": "csi1000_stage_b_repro_v1",
        "protocol": frozen_stage_b_protocol(),
        "provider_cutoff": RESERVED_EXECUTION_END,
        "provider_fingerprint": current_provider_fingerprint,
        "stage_a_expanded_sha256": hashlib.sha256(expanded_path.read_bytes()).hexdigest(),
        "stage_a_audit_sha256": hashlib.sha256(audit_path.read_bytes()).hexdigest(),
        "stage_a_selection_validation": selection_validation,
        "cross_stage_continuity": {
            "provider_fingerprint_match": True,
            "base_model_config_match": True,
            "lgb_repro_params_match": True,
            "deterministic_strategy_source_match": True,
            "runtime_match": True,
        },
        "base_model_config_sha256": current_base_model_sha,
        "candidate_model_config_sha256": candidate_model_config_sha256,
        "lgb_repro_params": dict(LGB_REPRO_PARAMS),
        "worker_resource_defaults": {
            "cpu_physical_cores": WORKER_CPU,
            "memory_mib": WORKER_MEMORY_MIB,
            "max_containers": WORKER_MAX_CONTAINERS,
            "starter_container_limit": STARTER_CONTAINER_LIMIT,
            "retries": WORKER_RETRIES,
            "lightgbm_num_threads": MODEL_NUM_THREADS,
        },
        "runtime": current_runtime,
        "runner_source_sha256": (
            hashlib.sha256(source_path.read_bytes()).hexdigest()
            if source_path.is_file()
            else "unavailable"
        ),
        "core_source_sha256": hashlib.sha256(core_path.read_bytes()).hexdigest(),
        "deterministic_strategy_source_sha256": current_strategy_sha,
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
    try:
        vol.reload()
    except Exception as exc:
        print(f"worker Volume reload skipped: {exc}")
    manifest = _load_json(_snapshot_manifest_path())
    if not manifest:
        raise RuntimeError("Stage-B provider snapshot manifest missing")
    if manifest.get("snapshot_token") != expected:
        raise RuntimeError(
            f"Stage-B snapshot mismatch: expected={expected} "
            f"actual={manifest.get('snapshot_token')}"
        )
    return manifest


def _safe_namespace(namespace: str | None) -> Path | None:
    if namespace is None:
        return None
    path = Path(str(namespace))
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("artifact namespace must be a safe relative path")
    return path


def _chunk_dir(snapshot: str, candidate_id: str, phase: int, retrain_asof: str,
               namespace: str | None = None) -> Path:
    root = ARTIFACT_ROOT / snapshot
    safe = _safe_namespace(namespace)
    if safe is not None:
        root = root / safe
    return root / "chunks" / candidate_id / f"phase{phase:02d}" / retrain_asof


def _phase_dir(snapshot: str, candidate_id: str, phase: int,
               namespace: str | None = None) -> Path:
    root = ARTIFACT_ROOT / snapshot
    safe = _safe_namespace(namespace)
    if safe is not None:
        root = root / safe
    return root / "phases" / candidate_id / f"phase{phase:02d}"


def _load_verified_series_artifact(meta: dict):
    import hashlib
    import pandas as pd

    path = Path((meta or {}).get("path", ""))
    if not path.is_file():
        raise RuntimeError(f"signal artifact missing: {path}")
    if hashlib.sha256(path.read_bytes()).hexdigest() != meta.get("sha256"):
        raise RuntimeError(f"signal artifact byte hash mismatch: {path}")
    frame = pd.read_parquet(path)
    if list(frame.columns) != ["score"]:
        raise RuntimeError(f"unexpected signal artifact columns: {path}")
    signal = frame["score"].sort_index()
    if _signal_sha256(signal) != meta.get("content_sha256"):
        raise RuntimeError(f"signal artifact content hash mismatch: {path}")
    return signal


def _load_verified_report_artifact(meta: dict):
    import hashlib
    import pandas as pd

    path = Path((meta or {}).get("path", ""))
    if not path.is_file():
        raise RuntimeError(f"report artifact missing: {path}")
    if hashlib.sha256(path.read_bytes()).hexdigest() != meta.get("sha256"):
        raise RuntimeError(f"report artifact byte hash mismatch: {path}")
    report = pd.read_parquet(path)
    if _frame_sha256(report) != meta.get("content_sha256"):
        raise RuntimeError(f"report artifact content hash mismatch: {path}")
    return report


def _load_verified_decision_artifact(meta: dict):
    import hashlib
    import json

    path = Path((meta or {}).get("path", ""))
    if not path.is_file():
        raise RuntimeError(f"decision artifact missing: {path}")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != meta.get("sha256"):
        raise RuntimeError(f"decision artifact byte hash mismatch: {path}")
    rows = json.loads(raw)
    if _stable_json_sha256(rows) != meta.get("content_sha256"):
        raise RuntimeError(f"decision artifact content hash mismatch: {path}")
    return rows


@app.function(volumes={str(VOL_ROOT): vol}, cpu=2, memory=4096, timeout=30 * 60)
def prepare_stage_b():
    from qlib_audit_fixes import read_trading_calendar

    vol.reload()
    marker = DATA_DIR / ".chenditc"
    if not marker.is_file():
        raise RuntimeError(
            "provider snapshot is missing; Stage B must not download/refresh data"
        )
    calendar = read_trading_calendar(DATA_DIR)
    tail = validate_reserved_tail(calendar)
    manifest = _runtime_manifest(calendar)
    _publish_snapshot_manifest(manifest)
    return {
        "manifest": manifest,
        "reserved_tail": tail,
        "fit_budget": stage_b_fit_budget(calendar),
    }


def _valid_chunk_result(payload: dict, args: dict) -> bool:
    return bool(
        payload
        and payload.get("protocol") == STAGE_B_PROTOCOL_VERSION
        and payload.get("snapshot_token") == args["snapshot_token"]
        and payload.get("candidate_id") == args["candidate"]["candidate_id"]
        and int(payload.get("phase", -1)) == int(args["phase"])
        and payload.get("retrain_asof") == args["retrain_asof"]
        and payload.get("signal_end") == args["signal_end"]
        and payload.get("candidate") == args["candidate"]
        and payload.get("prediction_artifact")
    )


def _load_reusable_chunk(args: dict, manifest: dict):
    result_path = _chunk_dir(
        args["snapshot_token"],
        args["candidate"]["candidate_id"],
        args["phase"],
        args["retrain_asof"],
        args.get("artifact_namespace"),
    ) / "result.json"
    payload = _load_json(result_path)
    if not _valid_chunk_result(payload, args):
        return None
    expected_model_sha = (manifest.get("candidate_model_config_sha256") or {}).get(
        args["candidate"]["candidate_id"]
    )
    if not expected_model_sha or payload.get("model_config_sha256") != expected_model_sha:
        return None
    _load_verified_series_artifact(payload["prediction_artifact"])
    payload["source"] = "artifact_reuse_verified"
    return payload


@app.function(
    volumes={str(VOL_ROOT): vol},
    cpu=WORKER_CPU,
    memory=WORKER_MEMORY_MIB,
    timeout=2 * 3600,
    retries=WORKER_RETRIES,
    max_containers=WORKER_MAX_CONTAINERS,
)
def stage_b_retrain_worker(args: dict):
    import json

    import qlib
    from qlib.data.dataset import Dataset
    from qlib.model.base import Model
    from qlib.utils import init_instance_by_config

    from qlib_audit_fixes import last_matured_sample, purge_cfg_splits, read_trading_calendar

    manifest = _worker_assert_snapshot(args["snapshot_token"])
    if args.get("resume", True):
        reusable = _load_reusable_chunk(args, manifest)
        if reusable is not None:
            return reusable

    candidate = args["candidate"]
    cfg = _load_task(candidate["model_params"])
    qlib.init(**{**cfg["qlib_init"], "skip_if_reg": True})
    calendar = read_trading_calendar(DATA_DIR)

    asof_i = bisect_left(calendar, args["retrain_asof"])
    if asof_i >= len(calendar) or calendar[asof_i] != args["retrain_asof"]:
        raise RuntimeError("retrain_asof is not in provider calendar")
    valid_end_i = asof_i - LABEL_HORIZON - 1
    valid_start_i = valid_end_i - VALIDATION_SESSIONS + 1
    if valid_start_i <= 0 or valid_end_i <= 0:
        raise RuntimeError(f"insufficient history at {args['retrain_asof']}")

    dataset_cfg = cfg["task"]["dataset"]["kwargs"]
    segments = dataset_cfg["segments"]
    handler = dataset_cfg["handler"]["kwargs"]
    train_end = last_matured_sample(
        calendar, calendar[valid_start_i], LABEL_HORIZON
    )
    segments["train"] = [TRAIN_START, train_end]
    segments["valid"] = [calendar[valid_start_i], calendar[valid_end_i]]
    segments["test"] = [args["retrain_asof"], args["signal_end"]]
    handler["start_time"] = "2015-01-01"
    handler["end_time"] = args["signal_end"]
    handler["fit_start_time"] = TRAIN_START
    handler["fit_end_time"] = train_end
    purge_cfg_splits(cfg, calendar, horizon=LABEL_HORIZON)

    model_config_sha256 = _stable_json_sha256(cfg["task"]["model"])
    expected_model_sha256 = (
        manifest.get("candidate_model_config_sha256") or {}
    ).get(candidate["candidate_id"])
    if not expected_model_sha256:
        raise RuntimeError(
            f"Stage-B manifest is missing candidate model hash: {candidate['candidate_id']}"
        )
    if model_config_sha256 != expected_model_sha256:
        raise RuntimeError(
            f"Stage-B candidate model config drift: {candidate['candidate_id']}"
        )
    model = init_instance_by_config(cfg["task"]["model"], accept_types=Model)
    dataset = init_instance_by_config(cfg["task"]["dataset"], accept_types=Dataset)
    model.fit(dataset)
    prediction = model.predict(dataset, segment="test")
    if prediction.empty:
        raise RuntimeError("Stage-B retrain produced empty prediction")
    first_signal = str(prediction.index.get_level_values(0).min())[:10]
    last_signal = str(prediction.index.get_level_values(0).max())[:10]
    if [first_signal, last_signal] != [args["retrain_asof"], args["signal_end"]]:
        raise RuntimeError(
            f"prediction coverage mismatch: {first_signal}~{last_signal} != "
            f"{args['retrain_asof']}~{args['signal_end']}"
        )

    root = _chunk_dir(
        args["snapshot_token"],
        candidate["candidate_id"],
        args["phase"],
        args["retrain_asof"],
        args.get("artifact_namespace"),
    )
    prediction_artifact = _write_series_artifact(
        prediction, root / "prediction.parquet"
    )
    best_iteration = None
    if getattr(model, "model", None) is not None:
        value = getattr(model.model, "best_iteration", None)
        best_iteration = int(value) if value else None

    payload = {
        "protocol": STAGE_B_PROTOCOL_VERSION,
        "snapshot_token": args["snapshot_token"],
        "candidate_id": candidate["candidate_id"],
        "candidate": candidate,
        "phase": int(args["phase"]),
        "retrain_asof": args["retrain_asof"],
        "signal_end": args["signal_end"],
        "artifact_namespace": args.get("artifact_namespace"),
        "source": "deterministic_fit",
        "model_config_sha256": model_config_sha256,
        "best_iteration": best_iteration,
        "prediction_artifact": prediction_artifact,
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "result.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False)
    )
    vol.commit()
    return payload


def _assemble_phase_signal(chunk_results: list[dict], calendar: list[str]):
    import pandas as pd

    chunks = []
    metadata = []
    for row in sorted(chunk_results, key=lambda item: item["retrain_asof"]):
        signal = _load_verified_series_artifact(row["prediction_artifact"])
        chunks.append(signal)
        metadata.append(
            {
                "retrain_asof": row["retrain_asof"],
                "signal_end": row["signal_end"],
                "best_iteration": row.get("best_iteration"),
                "model_config_sha256": row.get("model_config_sha256"),
                "prediction_content_sha256": row["prediction_artifact"][
                    "content_sha256"
                ],
                "source": row.get("source"),
            }
        )
    if not chunks:
        raise RuntimeError("Stage-B phase has no prediction chunks")
    signal = pd.concat(chunks).sort_index()
    if signal.index.has_duplicates:
        raise RuntimeError("duplicate signal rows across Stage-B retrain chunks")

    dates = signal.index.get_level_values(0)
    date_text = dates.astype(str).str[:10]
    keep = (date_text >= RESERVED_SIGNAL_ANCHOR) & (
        date_text < RESERVED_EXECUTION_END
    )
    signal = signal[keep].sort_index()
    expected_start = bisect_left(calendar, RESERVED_SIGNAL_ANCHOR)
    expected_end = bisect_left(calendar, RESERVED_EXECUTION_END)
    expected_days = calendar[expected_start:expected_end]
    actual_days = sorted(
        {str(value)[:10] for value in signal.index.get_level_values(0).unique()}
    )
    if actual_days != expected_days:
        raise RuntimeError(
            f"Stage-B signal-day coverage mismatch: "
            f"{len(actual_days)} != {len(expected_days)}"
        )
    return signal, metadata


def _valid_phase_result(payload: dict, args: dict) -> bool:
    return bool(
        payload
        and payload.get("protocol") == STAGE_B_PROTOCOL_VERSION
        and payload.get("snapshot_token") == args["snapshot_token"]
        and payload.get("candidate_id") == args["candidate"]["candidate_id"]
        and int(payload.get("phase", -1)) == int(args["phase"])
        and payload.get("candidate") == args["candidate"]
        and payload.get("signal_artifact")
        and payload.get("decision_artifact")
        and payload.get("report_artifact")
    )


def _load_reusable_phase(args: dict):
    result_path = _phase_dir(
        args["snapshot_token"],
        args["candidate"]["candidate_id"],
        args["phase"],
        args.get("artifact_namespace"),
    ) / "result.json"
    payload = _load_json(result_path)
    if not _valid_phase_result(payload, args):
        return None
    _load_verified_series_artifact(payload["signal_artifact"])
    _load_verified_decision_artifact(payload["decision_artifact"])
    report = _load_verified_report_artifact(payload["report_artifact"])
    metrics = _phase_metrics(report)
    mismatches = [
        key
        for key, value in metrics.items()
        if key not in payload or not _metric_values_match(payload.get(key), value)
    ]
    if mismatches:
        raise RuntimeError(f"reusable Stage-B phase metric mismatch: {mismatches}")
    payload.update(metrics)
    payload["source"] = "artifact_reuse_verified"
    return payload


@app.function(
    volumes={str(VOL_ROOT): vol},
    cpu=4,
    memory=8192,
    timeout=2 * 3600,
    retries=1,
    max_containers=55,
)
def stage_b_phase_worker(args: dict):
    import json

    from qlib_audit_fixes import read_trading_calendar

    _worker_assert_snapshot(args["snapshot_token"])
    if args.get("resume", True):
        reusable = _load_reusable_phase(args)
        if reusable is not None:
            return reusable

    vol.reload()
    calendar = read_trading_calendar(DATA_DIR)
    signal, chunk_meta = _assemble_phase_signal(args["chunk_results"], calendar)
    report, decision_audit = _run_signal_backtest(signal)
    metrics = _phase_metrics(report)

    root = _phase_dir(
        args["snapshot_token"],
        args["candidate"]["candidate_id"],
        args["phase"],
        args.get("artifact_namespace"),
    )
    signal_artifact = _write_series_artifact(signal, root / "signal.parquet")
    report_artifact = _write_report_artifact(report, root / "report.parquet")
    decision_artifact = _write_decision_artifact(
        decision_audit, root / "decisions.json"
    )
    payload = {
        "protocol": STAGE_B_PROTOCOL_VERSION,
        "snapshot_token": args["snapshot_token"],
        "candidate_id": args["candidate"]["candidate_id"],
        "candidate": args["candidate"],
        "stage_a_rank": args["candidate"]["stage_a_rank"],
        "phase": int(args["phase"]),
        "artifact_namespace": args.get("artifact_namespace"),
        "source": "deterministic_full_rolling",
        "n_retrains": len(chunk_meta),
        "chunk_predictions": chunk_meta,
        "signal_artifact": signal_artifact,
        "decision_artifact": decision_artifact,
        "report_artifact": report_artifact,
        **metrics,
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "result.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False)
    )
    vol.commit()
    return payload


def _numeric_report_diagnostics(left, right):
    import math

    if len(left) != len(right):
        return {"passed": False, "reason": "length_mismatch"}
    mismatch_count = 0
    max_abs_diff = 0.0
    max_rel_diff = 0.0
    exact_match = True
    for a_raw, b_raw in zip(left, right):
        a = float(a_raw)
        b = float(b_raw)
        if math.isnan(a) or math.isnan(b):
            if not (math.isnan(a) and math.isnan(b)):
                mismatch_count += 1
                exact_match = False
            continue
        if math.isinf(a) or math.isinf(b):
            if a != b:
                mismatch_count += 1
                exact_match = False
            continue
        diff = abs(a - b)
        scale = max(abs(a), abs(b))
        max_abs_diff = max(max_abs_diff, diff)
        max_rel_diff = max(max_rel_diff, diff / max(scale, 1e-300))
        if a != b:
            exact_match = False
        if diff > REPORT_REPRO_ATOL + REPORT_REPRO_RTOL * scale:
            mismatch_count += 1
    return {
        "passed": mismatch_count == 0,
        "mismatch_count": mismatch_count,
        "exact_match": exact_match,
        "rtol": REPORT_REPRO_RTOL,
        "atol": REPORT_REPRO_ATOL,
        "max_abs_diff": max_abs_diff,
        "max_rel_diff": max_rel_diff,
    }


def _compare_preflight_phase_results(a: dict, b: dict) -> dict:
    import pandas as pd

    chunk_a = [
        (
            row["retrain_asof"],
            row["signal_end"],
            row["best_iteration"],
            row["model_config_sha256"],
            row["prediction_content_sha256"],
        )
        for row in a["chunk_predictions"]
    ]
    chunk_b = [
        (
            row["retrain_asof"],
            row["signal_end"],
            row["best_iteration"],
            row["model_config_sha256"],
            row["prediction_content_sha256"],
        )
        for row in b["chunk_predictions"]
    ]
    if chunk_a != chunk_b:
        raise RuntimeError("Stage-B preflight prediction-chunk lineage mismatch")
    if a["signal_artifact"]["content_sha256"] != b["signal_artifact"]["content_sha256"]:
        raise RuntimeError("Stage-B preflight assembled signal mismatch")
    if (
        a["decision_artifact"]["content_sha256"]
        != b["decision_artifact"]["content_sha256"]
    ):
        raise RuntimeError("Stage-B preflight decision lineage mismatch")

    report_a = _load_verified_report_artifact(a["report_artifact"])
    report_b = _load_verified_report_artifact(b["report_artifact"])
    detail = {
        "passed": True,
        "prediction_chunks_exact": True,
        "signal_exact": True,
        "decision_exact": True,
        "report_content_exact": (
            a["report_artifact"]["content_sha256"]
            == b["report_artifact"]["content_sha256"]
        ),
        "structure_match": True,
        "canonical_metrics_match": True,
        "columns": {},
        "errors": [],
    }
    if (
        not report_a.index.equals(report_b.index)
        or list(report_a.index.names) != list(report_b.index.names)
        or list(report_a.columns) != list(report_b.columns)
        or [str(x) for x in report_a.dtypes] != [str(x) for x in report_b.dtypes]
    ):
        detail["passed"] = False
        detail["structure_match"] = False
        detail["errors"].append("report_structure_mismatch")
        return detail

    for column in report_a.columns:
        left = report_a[column]
        right = report_b[column]
        if pd.api.types.is_numeric_dtype(left.dtype):
            scale = 100000000.0 if str(column) == "account" else 1.0
            diag = _numeric_report_diagnostics(
                left.to_numpy(dtype=float, na_value=float("nan")) / scale,
                right.to_numpy(dtype=float, na_value=float("nan")) / scale,
            )
            diag["normalization_scale"] = scale
        else:
            exact = left.equals(right)
            diag = {"passed": bool(exact), "exact_match": bool(exact)}
        detail["columns"][str(column)] = diag
        if not diag["passed"]:
            detail["passed"] = False
            detail["errors"].append(f"report_column_mismatch:{column}")

    metrics_a = _phase_metrics(report_a)
    metrics_b = _phase_metrics(report_b)
    mismatches = [
        key
        for key in metrics_a
        if key not in metrics_b
        or not _metric_values_match(metrics_a[key], metrics_b[key])
    ]
    if mismatches:
        detail["passed"] = False
        detail["canonical_metrics_match"] = False
        detail["errors"].append("canonical_metric_mismatch:" + ",".join(mismatches))
    detail["canonical_metrics"] = metrics_b
    return detail


def _preflight_path(snapshot_token: str) -> Path:
    return ARTIFACT_ROOT / snapshot_token / "preflight.json"


def _load_passing_preflight(snapshot_token: str) -> dict:
    payload = _load_json(_preflight_path(snapshot_token))
    if not payload or not payload.get("passed"):
        raise RuntimeError(
            "Stage-B full run requires a passing preflight under the same manifest"
        )
    if payload.get("snapshot_token") != snapshot_token:
        raise RuntimeError("Stage-B preflight snapshot mismatch")
    return payload


def _jobs_for(candidate: dict, phase: int, calendar: list[str], snapshot: str,
              *, namespace: str | None = None, resume: bool = True) -> list[dict]:
    jobs = []
    for geometry in build_phase_jobs(calendar, phase):
        jobs.append(
            {
                **geometry,
                "candidate": candidate,
                "snapshot_token": snapshot,
                "artifact_namespace": namespace,
                "resume": bool(resume),
            }
        )
    return jobs


@app.function(volumes={str(VOL_ROOT): vol}, cpu=4, memory=8192, timeout=24 * 3600)
def stage_b_driver(
    preflight_only: bool = False,
    resume: bool = True,
    worker_cpu: float = WORKER_CPU,
    worker_memory_mib: int = WORKER_MEMORY_MIB,
    worker_max_containers: int = WORKER_MAX_CONTAINERS,
):
    import json

    from qlib_audit_fixes import read_trading_calendar

    execution_resources = validate_worker_resources(
        cpu=float(worker_cpu),
        memory_mib=int(worker_memory_mib),
        max_containers=int(worker_max_containers),
    )
    worker_cpu_value = execution_resources["retrain_worker_cpu_physical_cores"]
    worker_memory_value = execution_resources["retrain_worker_memory_mib"]
    worker_cap = execution_resources["retrain_worker_max_containers"]

    setup = prepare_stage_b.remote()
    vol.reload()
    manifest = setup["manifest"]
    snapshot = manifest["snapshot_token"]
    calendar = read_trading_calendar(DATA_DIR)
    baseline = next(row for row in stage_b_candidates() if row["is_baseline"])

    if preflight_only:
        repeat_phase_results = []
        for repeat in ("repeat_a", "repeat_b"):
            namespace = f"_preflight/{repeat}"
            jobs = _jobs_for(
                baseline,
                0,
                calendar,
                snapshot,
                namespace=namespace,
                resume=False,
            )
            print(
                f"[stage-b preflight] {repeat}: {len(jobs)} retrain fits; "
                f"cpu={worker_cpu_value} memory={worker_memory_value}MiB "
                f"worker cap={worker_cap}"
            )
            chunks = list(stage_b_retrain_worker.with_options(
                cpu=worker_cpu_value,
                memory=worker_memory_value,
                max_containers=worker_cap,
            ).map(jobs))
            vol.reload()
            phase = stage_b_phase_worker.remote(
                {
                    "candidate": baseline,
                    "phase": 0,
                    "snapshot_token": snapshot,
                    "artifact_namespace": namespace,
                    "resume": False,
                    "chunk_results": chunks,
                }
            )
            repeat_phase_results.append(phase)

            # stage_b_phase_worker writes/commits phase-level artifacts from a
            # different container. Refresh the driver's Volume view before the
            # next repeat or the final cross-repeat comparison reads those
            # artifacts. Without this reload, repeat_b's report can be absent
            # from the driver's mounted snapshot even though the worker
            # successfully committed it.
            vol.reload()

        comparison = _compare_preflight_phase_results(
            repeat_phase_results[0], repeat_phase_results[1]
        )
        if not comparison["passed"]:
            raise RuntimeError(
                f"Stage-B preflight semantic report gate failed: {comparison['errors']}"
            )
        gate = {
            "passed": True,
            "gate_version": "stage_b_baseline_phase0_double_run_v1",
            "snapshot_token": snapshot,
            "candidate_id": baseline["candidate_id"],
            "phase": 0,
            "n_retrains_per_repeat": repeat_phase_results[0]["n_retrains"],
            "total_model_fits": 2 * repeat_phase_results[0]["n_retrains"],
            "execution_resources": execution_resources,
            "repeat_a": repeat_phase_results[0],
            "repeat_b": repeat_phase_results[1],
            "comparison": comparison,
        }
        path = _preflight_path(snapshot)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(gate, indent=2, ensure_ascii=False))
        vol.commit()
        return {
            "protocol": STAGE_B_PROTOCOL_VERSION,
            "run_kind": "preflight_only",
            "manifest": manifest,
            "reserved_tail": setup["reserved_tail"],
            "fit_budget": setup["fit_budget"],
            "preflight": gate,
            "execution_resources": execution_resources,
        }

    preflight = _load_passing_preflight(snapshot)
    candidates = stage_b_candidates()
    all_retrain_jobs = []
    jobs_by_lineage = {}
    for candidate in candidates:
        for phase in REFERENCE_PHASES:
            key = (candidate["candidate_id"], phase)
            if candidate["is_baseline"] and phase == 0:
                jobs_by_lineage[key] = []
                continue
            jobs = _jobs_for(
                candidate,
                phase,
                calendar,
                snapshot,
                resume=resume,
            )
            jobs_by_lineage[key] = jobs
            all_retrain_jobs.extend(jobs)

    print(
        f"[stage-b full] candidates={len(candidates)} phases={len(REFERENCE_PHASES)} "
        f"new/reusable retrain jobs={len(all_retrain_jobs)} "
        f"cpu={worker_cpu_value} memory={worker_memory_value}MiB "
        f"worker cap={worker_cap}"
    )
    chunk_results = list(
        stage_b_retrain_worker.with_options(
            cpu=worker_cpu_value,
            memory=worker_memory_value,
            max_containers=worker_cap,
        ).map(all_retrain_jobs)
    )
    vol.reload()
    by_key = {}
    for row in chunk_results:
        by_key.setdefault((row["candidate_id"], int(row["phase"])), []).append(row)

    phase_jobs = []
    for candidate in candidates:
        for phase in REFERENCE_PHASES:
            if candidate["is_baseline"] and phase == 0:
                continue
            key = (candidate["candidate_id"], phase)
            expected = len(jobs_by_lineage[key])
            chunks = by_key.get(key) or []
            if len(chunks) != expected:
                raise RuntimeError(
                    f"Stage-B lineage incomplete {key}: {len(chunks)} != {expected}"
                )
            phase_jobs.append(
                {
                    "candidate": candidate,
                    "phase": phase,
                    "snapshot_token": snapshot,
                    "artifact_namespace": None,
                    "resume": bool(resume),
                    "chunk_results": chunks,
                }
            )

    phase_results = list(stage_b_phase_worker.map(phase_jobs))
    vol.reload()

    # The passing repeat_b is the frozen baseline phase-0 confirmation result.
    baseline_phase0 = dict(preflight["repeat_b"])
    baseline_phase0["source"] = "stage_b_preflight_reuse"
    phase_results.append(baseline_phase0)

    results_by_candidate = []
    for candidate in candidates:
        rows = sorted(
            [
                row
                for row in phase_results
                if row["candidate_id"] == candidate["candidate_id"]
            ],
            key=lambda row: int(row["phase"]),
        )
        if [int(row["phase"]) for row in rows] != list(REFERENCE_PHASES):
            raise RuntimeError(
                f"Stage-B candidate phase set incomplete: {candidate['candidate_id']}"
            )
        results_by_candidate.append(
            {
                "candidate_id": candidate["candidate_id"],
                "stage_a_rank": candidate["stage_a_rank"],
                "is_baseline": candidate["is_baseline"],
                "model_params": candidate["model_params"],
                "phase_results": rows,
            }
        )

    ranked = rank_stage_b_candidates(results_by_candidate)
    payload = {
        "protocol": STAGE_B_PROTOCOL_VERSION,
        "run_kind": "full",
        "manifest": manifest,
        "reserved_tail": setup["reserved_tail"],
        "fit_budget": setup["fit_budget"],
        "preflight": {
            "passed": preflight["passed"],
            "gate_version": preflight["gate_version"],
            "snapshot_token": preflight["snapshot_token"],
            "candidate_id": preflight["candidate_id"],
            "phase": preflight["phase"],
            "n_retrains_per_repeat": preflight["n_retrains_per_repeat"],
            "total_model_fits": preflight["total_model_fits"],
            "execution_resources": preflight.get("execution_resources"),
            "comparison": preflight["comparison"],
        },
        "candidate_count": len(candidates),
        "phase_count": len(REFERENCE_PHASES),
        "ranking_contract": __import__(
            "csi1000_stage_b_core"
        ).phase_ranking_contract(),
        "ranked_candidates": ranked,
        "reserved_tail_policy": (
            "final_confirmation_only_do_not_retune_on_stage_b_results"
        ),
        "execution_resources": execution_resources,
    }
    root = ARTIFACT_ROOT / snapshot
    root.mkdir(parents=True, exist_ok=True)
    (root / "summary.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False)
    )
    vol.commit()
    return payload


@app.local_entrypoint()
def main(
    preflight_only: bool = False,
    resume: bool = True,
    worker_cpu: float = WORKER_CPU,
    worker_memory_mib: int = WORKER_MEMORY_MIB,
    worker_max_containers: int = WORKER_MAX_CONTAINERS,
):
    import json

    payload = stage_b_driver.remote(
        preflight_only=bool(preflight_only),
        resume=bool(resume),
        worker_cpu=float(worker_cpu),
        worker_memory_mib=int(worker_memory_mib),
        worker_max_containers=int(worker_max_containers),
    )
    out_dir = Path("results") / "csi1000_stage_b"
    out_dir.mkdir(parents=True, exist_ok=True)
    token = payload["manifest"]["snapshot_token"]
    kind = payload["run_kind"]
    out_path = out_dir / f"stage_b_{kind}_{token[:16]}.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"[stage-b] exported {out_path}")
