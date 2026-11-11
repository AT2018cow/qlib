"""G1 stress-first spine generation on Modal (infi research workspace)."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import modal

_SCRIPT_DIR = Path(__file__).resolve().parent
for _candidate in (_SCRIPT_DIR, Path("/root/qlib/quant/code/research/regime_reselection")):
    if str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

APP_NAME = "qlib-regime-reselection"
VOL_NAME = "qlib-cn-data"
VOL_ROOT = Path("/vol")
DATA_DIR = VOL_ROOT / "cn_data"
RUN_ROOT = VOL_ROOT / "regime_reselection" / "spines"

# Same dependency set as the validated freq experiment image (cached in infi).
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
    .add_local_dir(
        ".",
        remote_path="/root/qlib",
        copy=True,
        ignore=lambda path: (
            str(path).endswith((".cpp", ".so"))
            or any(
                part in str(path)
                for part in (
                    ".venv",
                    ".worktrees",
                    "mlruns",
                    "__pycache__",
                    "quant/local_backup",
                )
            )
            or (
                "quant/data" in str(path)
                and "quant/data/strategy_releases" not in str(path)
                and not str(path).endswith("quant/data")
            )
            or str(path).startswith(".git/")
            or "/.git/" in str(path)
            or str(path) in ("modal_qlib_cn_a10g.py", "freq_experiment.py")
        ),
    )
    .run_commands(
        "cd /root/qlib && pip install . --no-build-isolation --no-deps",
        "cp /root/qlib/quant/code/execution/qlib_audit_fixes.py "
        "/root/qlib/quant/code/execution/qlib_live_retrain.py "
        "/root/qlib/quant/code/execution/board_rules.py "
        "/root/qlib/quant/code/execution/board_execution.py "
        "/root/qlib/quant/code/execution/execution_limits.py "
        "/root/qlib/quant/code/execution/order_quantity.py "
        "/root/qlib/quant/code/execution/st_history.py "
        "/root/qlib/quant/code/execution/portfolio_performance.py /root/",
        "cp /root/qlib/quant/code/production/csi1000_production_config.py "
        "/root/qlib/quant/code/research/stage_ab/csi1000_tuner_core.py /root/",
    )
)

app = modal.App(APP_NAME, image=image)
vol = modal.Volume.from_name(VOL_NAME, create_if_missing=True)

REPO_PATHS = (
    "/root/qlib/quant/code",
    "/root/qlib/quant/code/production",
    "/root/qlib/quant/code/execution",
    "/root/qlib/quant/code/research/stage_ab",
    "/root/qlib/quant/code/research/layered_search",
    "/root/qlib/quant/code/research/regime_reselection",
    "/root/qlib/quant/code/ops",
)


def _receipt_dir(job: dict) -> Path:
    return RUN_ROOT / job["run_id"] / job["market"] / job["spine"] / f"asof_{job['retrain_asof']}"


def _strategy_contract_sha(job: dict, manifest: dict) -> str:
    from csi1000_production_config import PAPER_INITIAL_CASH
    from strategy_release import strategy_contract_sha256

    contract = {
        "contract_version": "cn_equity_strategy_contract_v1",
        "market": job["market"],
        "profile": job["profile"],
        "candidate_id": manifest.get("candidate_id"),
        "model_config_sha256": manifest.get("model_config_sha256"),
        "label_expression": f"Ref($close, -{int(job['label_horizon'])})/$close - 1",
        "label_horizon": int(job["label_horizon"]),
        "pit_universe": job["market"],
        "topk": manifest.get("topk"),
        "dropn": manifest.get("nd"),
        "retrain_every_sessions": 20,
        "execution_timing": "T_CLOSE_TO_T1_OPEN",
        "board_limits": "cn_equity_board_date_aware_v2",
        "listing_exemptions": "board_listing_date_rules",
        "lot_rule": "cn_equity_board_lot_v1",
        "liquidity_policy": "missing_quote_fail_closed",
        "fees": {"open_cost": 0.0005, "close_cost": 0.0015, "min_cost": 5.0},
        "initial_cash": PAPER_INITIAL_CASH,
        "account_start": "2026-09-30",
    }
    return strategy_contract_sha256(contract)


def _runtime_identity_context(job: dict, data_dir: Path) -> dict:
    """Recompute source/environment/provider/config identity from actual files."""
    import hashlib

    from csi1000_production_config import profile_manifest
    from qlib_live_retrain import provider_training_fingerprint
    from research_identity import environment_manifest, source_manifest

    yaml_path = Path("/root/qlib/examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158_csi500.yaml")
    if not yaml_path.is_file():
        yaml_path = (
            Path(__file__).resolve().parents[3]
            / "examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158_csi500.yaml"
        )
    if not yaml_path.is_file():
        raise FileNotFoundError(f"production model YAML missing: {yaml_path}")
    manifest = profile_manifest(job["profile"])
    strategy_contract = _strategy_contract_sha(job, manifest)
    return {
        "source_sha256": source_manifest("training")["sha256"],
        "environment_sha256": environment_manifest()["sha256"],
        "provider_kind": "chenditc",
        "provider_fingerprint": provider_training_fingerprint(data_dir, job["market"], job["run_end"]),
        "model_yaml_sha256": hashlib.sha256(yaml_path.read_bytes()).hexdigest(),
        "strategy_contract_sha256": strategy_contract,
    }


def _read_peak_memory_mb() -> float | None:
    """Best-effort container peak RSS from cgroup v2/v1, then getrusage."""
    candidates = (
        ("/sys/fs/cgroup/memory.peak", 1024 * 1024),
        ("/sys/fs/cgroup/memory/memory.max_usage_in_bytes", 1024 * 1024),
    )
    for path, divisor in candidates:
        try:
            return round(int(Path(path).read_text().strip()) / divisor, 1)
        except (OSError, ValueError):
            continue
    try:
        import resource

        # Linux reports ru_maxrss in KiB.
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    except Exception:  # noqa: BLE001
        return None


@app.function(
    volumes={str(VOL_ROOT): vol},
    cpu=8,
    memory=16384,
    timeout=3600,
    # 12-way concurrency is the level validated by the all-combination smoke.
    max_containers=12,
    retries=modal.Retries(max_retries=3, initial_delay=10, backoff_coefficient=1.5, max_delay=60),
)
def fit_slice(job: dict) -> dict:
    """Train one 20-session slice and persist the prediction + receipt."""
    import hashlib
    import os
    import time

    import numpy as np
    import pandas as pd
    from ruamel.yaml import YAML

    for path in REPO_PATHS:
        if path not in sys.path:
            sys.path.insert(0, path)
    import qlib
    from qlib.data.dataset import Dataset
    from qlib.model.base import Model
    from qlib.utils import init_instance_by_config

    from csi1000_production_config import profile_manifest
    from qlib_audit_fixes import last_matured_sample, purge_cfg_splits, read_trading_calendar
    from qlib_live_retrain import apply_lgb_reproducibility
    import spine_identity
    from spine_identity import (
        attach_job_identity,
        canonical_job_identity,
        receipt_dir as identity_receipt_dir,
        seal_receipt,
        validate_slice_receipt,
    )
    from training_window import training_segments

    context = _runtime_identity_context(job, DATA_DIR)
    expected_identity = canonical_job_identity(job, **context)
    if job.get("job_identity") is not None and job["job_identity"] != expected_identity:
        raise RuntimeError("job identity differs from actual source/environment/provider/config")
    job = attach_job_identity(job, context)
    receipt_dir = identity_receipt_dir(RUN_ROOT, job)
    receipt_path = receipt_dir / "receipt.json"
    prediction_path = receipt_dir / "prediction.parquet"
    vol.reload()
    if receipt_path.is_file() and prediction_path.is_file():
        try:
            existing = json.loads(receipt_path.read_text())
            validate_slice_receipt(existing, job, prediction_path)
            return existing
        except (ValueError, OSError):
            pass

    calendar = read_trading_calendar(DATA_DIR)
    asof = job["retrain_asof"]
    signal_end = job["signal_end"]
    horizon = int(job["label_horizon"])
    asof_i = calendar.index(asof)
    valid_end_i = asof_i - horizon - 1
    valid_start_i = valid_end_i - 252 + 1
    if valid_start_i <= 0:
        raise RuntimeError(f"insufficient history at {asof}")
    train_end = last_matured_sample(calendar, calendar[valid_start_i], horizon)

    yaml_path = "/root/qlib/examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158_csi500.yaml"
    with open(yaml_path) as stream:
        cfg = YAML(typ="safe", pure=True).load(stream)
    dk = cfg["task"]["dataset"]["kwargs"]
    handler = dk["handler"]["kwargs"]
    handler["instruments"] = job["market"]
    handler["label"] = [f"Ref($close, -{horizon})/$close - 1"]
    fit_start = job.get("fit_start", "2016-01-01")
    segments = training_segments(
        calendar,
        fit_start=fit_start,
        train_end=train_end,
        valid_start=calendar[valid_start_i],
        valid_end=calendar[valid_end_i],
        asof=asof,
        signal_end=signal_end,
    )
    handler["start_time"] = job.get("data_start", "2015-01-01")
    handler["end_time"] = signal_end
    handler["fit_start_time"] = fit_start
    handler["fit_end_time"] = train_end
    dk["segments"] = {key: segments[key] for key in ("train", "valid", "test")}
    manifest = profile_manifest(job["profile"])
    model_kwargs = cfg["task"]["model"]["kwargs"]
    model_kwargs.update(manifest["model_params"])
    model_kwargs["num_threads"] = int(job.get("num_threads", 8))
    model_kwargs.pop("n_jobs", None)
    apply_lgb_reproducibility(cfg)
    seed = int(job.get("seed", 0))
    model_kwargs["seed"] = seed
    for offset, key in enumerate(
        ("data_random_seed", "feature_fraction_seed", "bagging_seed", "drop_seed", "objective_seed", "extra_seed"),
        start=1,
    ):
        model_kwargs[key] = seed + offset
    purge_cfg_splits(cfg, calendar, horizon=horizon)
    cfg["qlib_init"] = {"provider_uri": str(DATA_DIR), "region": "cn"}
    init_kwargs = {"provider_uri": str(DATA_DIR), "region": "cn", "skip_if_reg": True}
    if job.get("kernels") is not None:
        init_kwargs["kernels"] = int(job["kernels"])
    if job.get("joblib_backend"):
        init_kwargs["joblib_backend"] = job["joblib_backend"]
    qlib.init(**init_kwargs)

    started = time.time()
    model = init_instance_by_config(cfg["task"]["model"], accept_types=Model)
    dataset = init_instance_by_config(cfg["task"]["dataset"], accept_types=Dataset)
    model.fit(dataset)
    prediction = model.predict(dataset, segment="test")
    if isinstance(prediction, pd.DataFrame):
        prediction = prediction.iloc[:, 0]
    if prediction.empty or prediction.index.get_level_values(0).nunique() == 0:
        raise RuntimeError(f"empty predictions {asof}~{signal_end}")
    first = str(prediction.index.get_level_values(0).min())[:10]
    last = str(prediction.index.get_level_values(0).max())[:10]
    if first != asof or last != signal_end:
        raise RuntimeError(f"prediction coverage mismatch: {first}~{last}, expected {asof}~{signal_end}")
    values = prediction.to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise RuntimeError("non-finite predictions")

    receipt_dir.mkdir(parents=True, exist_ok=True)
    tmp = prediction_path.with_name(prediction_path.name + ".tmp")
    prediction.to_frame("score").to_parquet(tmp)
    os.replace(tmp, prediction_path)
    days = sorted({str(day)[:10] for day in prediction.index.get_level_values(0).unique()})
    receipt = {
        "schema_version": spine_identity.SCHEMA_VERSION,
        "status": "PASS",
        "kind": "g1_spine_slice_v2",
        "job_identity": job["job_identity"],
        "job_sha256": job["job_sha256"],
        "run_id": job["run_id"],
        "code_sha": job["code_sha"],
        "market": job["market"],
        "spine": job["spine"],
        "profile": job["profile"],
        "label_horizon": horizon,
        "seed": seed,
        "retrain_asof": asof,
        "signal_end": signal_end,
        "train_end": train_end,
        "valid": [calendar[valid_start_i], calendar[valid_end_i]],
        "days": days,
        "n_rows": int(len(prediction)),
        "n_days": int(len(days)),
        "n_instruments": int(prediction.index.get_level_values(1).nunique()),
        "peak_memory_mb": _read_peak_memory_mb(),
        "prediction_sha256": hashlib.sha256(prediction_path.read_bytes()).hexdigest(),
        "elapsed_s": round(time.time() - started, 1),
    }
    receipt = seal_receipt(receipt)
    receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    vol.commit()
    return receipt


@app.function(volumes={str(VOL_ROOT): vol}, cpu=4, memory=8192, timeout=900)
def read_calendar() -> list[str]:
    vol.reload()
    return [line.strip() for line in (DATA_DIR / "calendars" / "day.txt").read_text().splitlines() if line.strip()]


@app.function(volumes={str(VOL_ROOT): vol}, cpu=1, memory=2048, timeout=900)
def research_source_identity() -> str:
    from research_identity import source_manifest

    return source_manifest("training")["sha256"]


@app.function(volumes={str(VOL_ROOT): vol}, cpu=1, memory=2048, timeout=900)
def research_environment_identity() -> str:
    from research_identity import environment_manifest

    return environment_manifest()["sha256"]


@app.function(volumes={str(VOL_ROOT): vol}, cpu=4, memory=8192, timeout=900)
def provider_fingerprint(market: str, cutoff: str) -> str:
    """Read-only provider identity for one market through the run end."""
    from qlib_live_retrain import provider_training_fingerprint

    vol.reload()
    return provider_training_fingerprint(DATA_DIR, market, cutoff)


@app.function(volumes={str(VOL_ROOT): vol}, cpu=1, memory=2048, timeout=900)
def completed_jobs(jobs: list[dict]) -> list[str]:
    """Return keys only for v2 receipts matching the full job identity."""
    from spine_identity import receipt_dir as identity_receipt_dir, validate_slice_receipt

    vol.reload()
    done = []
    for job in jobs:
        if not job.get("job_sha256"):
            continue
        directory = identity_receipt_dir(RUN_ROOT, job)
        receipt_path = directory / "receipt.json"
        prediction_path = directory / "prediction.parquet"
        if not (receipt_path.is_file() and prediction_path.is_file()):
            continue
        try:
            receipt = json.loads(receipt_path.read_text())
            validate_slice_receipt(receipt, job, prediction_path)
        except (ValueError, OSError):
            continue
        done.append(f"{job['market']}/{job['spine']}/{job['retrain_asof']}")
    return done


@app.function(volumes={str(VOL_ROOT): vol}, cpu=8, memory=16384, timeout=1800)
def assemble_spine(
    run_id: str,
    market: str,
    spine: str,
    start: str,
    end: str,
    assembly_identity: dict | None = None,
) -> dict:
    """Concatenate v2 slices and bind the request, calendar and ordered receipts."""
    import hashlib

    import pandas as pd

    from spine_identity import (
        assembly_receipt,
        validate_assembly_receipt,
        validate_assembly_identity,
        validate_slice_receipt_self,
        validate_slice_set,
    )
    from spine_jobs import expected_days

    vol.reload()
    root = RUN_ROOT / run_id / market / spine
    calendar = [line.strip() for line in (DATA_DIR / "calendars" / "day.txt").read_text().splitlines() if line.strip()]
    expected = expected_days(calendar, start, end)
    paths = sorted(root.glob("job_*/receipt.json"))
    if not paths:
        raise RuntimeError(f"no v2 slices for {market}/{spine}")
    if not isinstance(assembly_identity, dict):
        raise RuntimeError("assembly identity is required from the coordinator")
    validate_assembly_identity(assembly_identity)
    receipts = []
    for receipt_path in paths:
        receipt = json.loads(receipt_path.read_text())
        validate_slice_receipt_self(receipt, receipt_path.with_name("prediction.parquet"))
        receipts.append(receipt)
    ordered = validate_slice_set(receipts, assembly_identity=assembly_identity, expected_days=expected)
    identity_sha = assembly_identity["sha256"]
    calendar_sha = hashlib.sha256("\n".join(calendar).encode()).hexdigest()
    out = root / "signal.parquet"
    existing_receipt = root / "spine_receipt.json"
    if existing_receipt.is_file() and out.is_file():
        try:
            prior = json.loads(existing_receipt.read_text())
            validate_assembly_receipt(
                prior,
                run_id=run_id,
                market=market,
                spine=spine,
                start=start,
                end=end,
                calendar_sha256=calendar_sha,
                identity_sha256=identity_sha,
                slice_receipts=ordered,
                signal_sha256=hashlib.sha256(out.read_bytes()).hexdigest(),
                expected_days=expected,
            )
            return prior
        except (ValueError, OSError):
            pass
    frames = [pd.read_parquet(path.with_name("prediction.parquet")) for path in paths]
    signal = pd.concat(frames).sort_index()
    if signal.index.has_duplicates:
        raise RuntimeError(f"duplicate signal rows for {market}/{spine}")
    actual_days = sorted({str(day)[:10] for day in signal.index.get_level_values(0).unique()})
    if actual_days != expected:
        missing = sorted(set(expected) - set(actual_days))[:5]
        extra = sorted(set(actual_days) - set(expected))[:5]
        raise RuntimeError(f"spine coverage mismatch: missing={missing} extra={extra}")
    tmp = out.with_name(out.name + ".tmp")
    signal.to_parquet(tmp)
    os.replace(tmp, out)
    receipt = assembly_receipt(
        run_id=run_id,
        market=market,
        spine=spine,
        start=start,
        end=end,
        calendar_sha256=calendar_sha,
        identity_sha256=identity_sha,
        expected_days=expected,
        slice_receipts=ordered,
        signal_sha256=hashlib.sha256(out.read_bytes()).hexdigest(),
    )
    receipt.update(
        {
            "n_rows": int(len(signal)),
            "n_days": len(actual_days),
            "n_instruments": int(signal.index.get_level_values(1).nunique()),
            "slices": len(ordered),
        }
    )
    receipt = __import__("spine_identity").seal_receipt(receipt)
    existing_receipt.write_text(json.dumps(receipt, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    from research_bundle import build_bundle, G1_SIGNAL_ROLES, ROLE_KIND, SCHEMA_VERSION as BUNDLE_SCHEMA
    from csi1000_production_config import profile_manifest

    profile = profile_manifest(assembly_identity["profile"])
    identity = {
        "bundle_id": f"{run_id}:{market}:{spine}",
        "market": market,
        "profile": assembly_identity["profile"],
        "candidate_id": profile["candidate_id"],
        "window_start": start,
        "window_end": end,
        "source_sha256": assembly_identity["source_sha256"],
        "environment_sha256": assembly_identity["environment_sha256"],
        "provider_fingerprint": assembly_identity["provider_fingerprint"],
        "model_sha256": assembly_identity["model_yaml_sha256"],
        "strategy_contract_sha256": assembly_identity["strategy_contract_sha256"],
        "calendar_sha256": calendar_sha,
        "signal_sha256": receipt["signal_sha256"],
        "assembly_receipt_sha256": receipt["receipt_sha256"],
    }
    role_payloads = {
        "research_contract": {
            "contract_version": "g1_spine_v2",
            "assembly_receipt_sha256": receipt["receipt_sha256"],
        },
        "source_environment": {
            "source_sha256": identity["source_sha256"],
            "environment_sha256": identity["environment_sha256"],
        },
        "provider": {
            "provider_kind": assembly_identity.get("provider_kind"),
            "fingerprint": identity["provider_fingerprint"],
        },
        "calendar": {"calendar_sha256": calendar_sha, "dates": expected},
        "model": {"model_sha256": identity["model_sha256"]},
        "strategy": {
            "strategy_contract_sha256": identity["strategy_contract_sha256"],
            "candidate_id": identity["candidate_id"],
        },
        "slice_receipts": {"receipt_sha256": receipt["slice_receipt_sha256"]},
        "assembly_receipt": {"receipt_sha256": receipt["receipt_sha256"]},
        "signal": {"signal_sha256": receipt["signal_sha256"]},
    }
    roles_dir = root / "evidence_bundle" / "roles"
    roles_dir.mkdir(parents=True, exist_ok=True)
    role_paths = {}
    for role in G1_SIGNAL_ROLES:
        role_path = roles_dir / f"{role}.json"
        role_path.write_text(
            json.dumps(
                {
                    "schema_version": BUNDLE_SCHEMA,
                    "kind": ROLE_KIND[role],
                    "status": "PASS",
                    "identity": identity,
                    "payload": role_payloads[role],
                },
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        )
        role_paths[role] = role_path
    build_bundle(
        root / "evidence_bundle",
        bundle_kind="G1_SIGNAL",
        bundle_id=identity["bundle_id"],
        artifacts=role_paths,
        identity=identity,
        attachments={"signal": out},
    )
    vol.commit()
    return receipt


@app.local_entrypoint()
def debug_modal(
    start: str = "2021-02-18",
    end: str = "2024-02-05",
    run_id: str = "regime_g1_debug_20261102",
    mode: str = "default",
):
    """Run one first and one last slice per (market, spine) sequentially."""
    import traceback

    from spine_jobs import build_jobs

    if mode == "kernels1":
        overrides = {"kernels": 1}
    elif mode == "loky6":
        overrides = {"kernels": 6, "joblib_backend": "loky"}
    else:
        overrides = {}
    calendar = read_calendar.remote()
    jobs = build_jobs(calendar, start, end, run_id, "debug", num_threads=8)
    grouped = {}
    for job in jobs:
        grouped.setdefault((job["market"], job["spine"]), []).append(job)
    probes = []
    for key in sorted(grouped):
        probes.append(grouped[key][0])
        if len(grouped[key]) > 1:
            probes.append(grouped[key][-1])
    failures = 0
    for job in probes:
        job.update(overrides)
        label = f"{job['market']}/{job['spine']}/{job['retrain_asof']}"
        print(f"[debug] PROBE {label} kernels={job.get('kernels')} backend={job.get('joblib_backend')}", flush=True)
        try:
            out = fit_slice.remote(job)
            print(
                f"[debug] OK {label} rows={out.get('n_rows')} days={out.get('n_days')} elapsed={out.get('elapsed_s')}",
                flush=True,
            )
        except Exception:
            failures += 1
            print(f"[debug] FAIL {label}", flush=True)
            traceback.print_exc()
    print(f"[debug] DONE mode={mode} failures={failures}/{len(probes)}", flush=True)
    if failures:
        raise SystemExit(1)


@app.local_entrypoint()
def main(
    start: str = "2021-02-18",
    end: str = "2024-02-05",
    run_id: str = "regime_g1_20261102_a1",
    markets: str = "csi300,csi500,csi1000",
    spines: str = "A,B",
    max_jobs: int = 0,
    smoke: bool = False,
    smoke_all: bool = False,
    max_attempts: int = 3,
    kernels: int = -1,
    joblib_backend: str = "",
    data_start: str = "2015-01-01",
    fit_start: str = "2016-01-01",
    max_containers: int = 12,
    seed: int = 0,
):
    from spine_jobs import build_jobs, failed_jobs

    import subprocess

    try:
        code_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:
        code_sha = os.environ.get("GIT_COMMIT", "working-tree")
    calendar = read_calendar.remote()
    import hashlib

    from csi1000_production_config import profile_manifest
    from spine_identity import canonical_assembly_identity
    from spine_jobs import MARKETS, swapped_horizon

    market_list = tuple(x.strip() for x in markets.split(",") if x.strip())
    spine_list = tuple(x.strip() for x in spines.split(",") if x.strip())
    yaml_path = Path("/root/qlib/examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158_csi500.yaml")
    if not yaml_path.is_file():
        yaml_path = (
            Path(__file__).resolve().parents[3]
            / "examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158_csi500.yaml"
        )
    model_yaml_sha = hashlib.sha256(yaml_path.read_bytes()).hexdigest()
    source_sha = research_source_identity.remote()
    environment_sha = research_environment_identity.remote()
    overrides = {}
    if kernels and kernels > 0:
        overrides["kernels"] = int(kernels)
    if joblib_backend:
        overrides["joblib_backend"] = joblib_backend
    identity_contexts = {}
    assembly_identities = {}
    for market in market_list:
        provider_sha = provider_fingerprint.remote(market, end)
        manifest = profile_manifest(MARKETS[market]["profile"])
        for spine in spine_list:
            horizon = MARKETS[market]["horizon"] if spine == "A" else swapped_horizon(MARKETS[market]["horizon"])
            context = {
                "source_sha256": source_sha,
                "environment_sha256": environment_sha,
                "provider_kind": "chenditc",
                "provider_fingerprint": provider_sha,
                "model_yaml_sha256": model_yaml_sha,
                "strategy_contract_sha256": _strategy_contract_sha(
                    {"market": market, "profile": MARKETS[market]["profile"], "label_horizon": horizon},
                    manifest,
                ),
            }
            identity_contexts[(market, spine)] = context
            assembly_identities[(market, spine)] = canonical_assembly_identity(
                {
                    "run_id": run_id,
                    "run_end": end,
                    "market": market,
                    "spine": spine,
                    "profile": MARKETS[market]["profile"],
                    "label_horizon": horizon,
                    "data_start": data_start,
                    "fit_start": fit_start,
                    "seed": seed,
                    "num_threads": 8,
                    "kernels": overrides.get("kernels"),
                    "joblib_backend": overrides.get("joblib_backend"),
                },
                **context,
            )
    jobs = build_jobs(
        calendar,
        start,
        end,
        run_id,
        code_sha,
        markets=market_list,
        spines=spine_list,
        num_threads=8,
        data_start=data_start,
        fit_start=fit_start,
        seed=seed,
        kernels=overrides.get("kernels"),
        joblib_backend=overrides.get("joblib_backend"),
        identity_contexts=identity_contexts,
    )
    print(
        f"[g1] calendar {start}..{end} | jobs={len(jobs)} | run_id={run_id} | "
        f"data_start={data_start} fit_start={fit_start} max_containers={max_containers} seed={seed}"
    )
    if overrides:
        print(f"[g1] overrides: {overrides}")
    if smoke_all:
        grouped = {}
        for job in jobs:
            grouped.setdefault((job["market"], job["spine"]), []).append(job)
        probes = []
        for key in sorted(grouped):
            probes.append(grouped[key][0])
            if len(grouped[key]) > 1:
                probes.append(grouped[key][-1])
        jobs = probes
        print(f"[g1] SMOKE-ALL mode: running {len(jobs)} probes (first+last per market/spine)")
    elif smoke:
        jobs = jobs[: max(1, int(max_jobs or 2))]
        print(f"[g1] SMOKE mode: running {len(jobs)} job(s)")
    started = time.time()
    pending = list(jobs)
    last_error = None
    for attempt in range(1, max(1, int(max_attempts)) + 1):
        done = set(completed_jobs.remote(pending))
        pending = [job for job in pending if f"{job['market']}/{job['spine']}/{job['retrain_asof']}" not in done]
        print(f"[g1] attempt {attempt}: pending={len(pending)} (already valid={len(done)})", flush=True)
        if not pending:
            break
        try:
            runner = fit_slice
            if max_containers > 0 and int(max_containers) != 12:
                runner = fit_slice.with_options(max_containers=int(max_containers))
            results = list(runner.map(pending, return_exceptions=True))
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            print(f"[g1] attempt {attempt} aborted: {type(exc).__name__}: {exc}", flush=True)
            time.sleep(5)
            continue
        if len(results) != len(pending):
            raise RuntimeError(
                f"map returned partial results: jobs={len(pending)} results={len(results)}; "
                "refusing to declare completion or assemble"
            )
        for job, result in zip(pending, results):
            if isinstance(result, BaseException):
                print(
                    f"[g1] slice failed: {job['market']}/{job['spine']}/{job['retrain_asof']}: "
                    f"{type(result).__name__}: {result}",
                    flush=True,
                )
            elif not isinstance(result, dict) or result.get("status") != "PASS":
                print(
                    f"[g1] slice incomplete: {job['market']}/{job['spine']}/{job['retrain_asof']}: {result}",
                    flush=True,
                )
        pending = failed_jobs(pending, results)
        if not pending:
            break
        time.sleep(5)
    if pending:
        done = set(completed_jobs.remote(pending))
        pending = [job for job in pending if f"{job['market']}/{job['spine']}/{job['retrain_asof']}" not in done]
        if pending:
            raise RuntimeError(f"{len(pending)} slice(s) still missing after retries; last_error={last_error}")
    print(f"[g1] fits finished: all {len(jobs)} valid in {round(time.time() - started, 1)}s")
    if smoke:
        print("[g1] SMOKE PASS")
        return
    if smoke_all:
        print(f"[g1] SMOKE-ALL PASS ({len(jobs)} probes)")
        return
    summaries = []
    for market in {job["market"] for job in jobs}:
        for spine in {job["spine"] for job in jobs if job["market"] == market}:
            summary = assemble_spine.remote(run_id, market, spine, start, end, assembly_identities[(market, spine)])
            summaries.append(summary)
            print(
                f"[g1] assembled {market}/{spine}: days={summary['n_days']} rows={summary['n_rows']} sha={summary['signal_sha256'][:12]}"
            )
    print("[g1] DONE", json.dumps(summaries, indent=2, ensure_ascii=False))
