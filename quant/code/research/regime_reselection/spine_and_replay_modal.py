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
                    "quant/data",
                )
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
)


def _receipt_dir(job: dict) -> Path:
    return RUN_ROOT / job["run_id"] / job["market"] / job["spine"] / f"asof_{job['retrain_asof']}"


@app.function(
    volumes={str(VOL_ROOT): vol},
    cpu=8,
    memory=16384,
    timeout=3600,
    max_containers=24,
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

    receipt_dir = _receipt_dir(job)
    receipt_path = receipt_dir / "receipt.json"
    prediction_path = receipt_dir / "prediction.parquet"
    vol.reload()
    if receipt_path.is_file() and prediction_path.is_file():
        return json.loads(receipt_path.read_text())

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
    handler["start_time"] = "2015-01-01"
    handler["end_time"] = signal_end
    handler["fit_start_time"] = "2016-01-01"
    handler["fit_end_time"] = train_end
    dk["segments"] = {
        "train": ["2016-01-01", train_end],
        "valid": [calendar[valid_start_i], calendar[valid_end_i]],
        "test": [asof, signal_end],
    }
    manifest = profile_manifest(job["profile"])
    model_kwargs = cfg["task"]["model"]["kwargs"]
    model_kwargs.update(manifest["model_params"])
    model_kwargs["num_threads"] = int(job.get("num_threads", 8))
    model_kwargs.pop("n_jobs", None)
    apply_lgb_reproducibility(cfg)
    purge_cfg_splits(cfg, calendar, horizon=horizon)
    cfg["qlib_init"] = {"provider_uri": str(DATA_DIR), "region": "cn"}
    qlib.init(provider_uri=str(DATA_DIR), region="cn", skip_if_reg=True)

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
    receipt = {
        "status": "PASS",
        "kind": "g1_spine_slice",
        "run_id": job["run_id"],
        "code_sha": job["code_sha"],
        "market": job["market"],
        "spine": job["spine"],
        "profile": job["profile"],
        "label_horizon": horizon,
        "retrain_asof": asof,
        "signal_end": signal_end,
        "train_end": train_end,
        "valid": [calendar[valid_start_i], calendar[valid_end_i]],
        "n_rows": int(len(prediction)),
        "n_days": int(prediction.index.get_level_values(0).nunique()),
        "n_instruments": int(prediction.index.get_level_values(1).nunique()),
        "prediction_sha256": hashlib.sha256(prediction_path.read_bytes()).hexdigest(),
        "elapsed_s": round(time.time() - started, 1),
    }
    receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
    vol.commit()
    return receipt


@app.function(volumes={str(VOL_ROOT): vol}, cpu=4, memory=8192, timeout=900)
def read_calendar() -> list[str]:
    vol.reload()
    return [line.strip() for line in (DATA_DIR / "calendars" / "day.txt").read_text().splitlines() if line.strip()]


@app.function(volumes={str(VOL_ROOT): vol}, cpu=8, memory=16384, timeout=1800)
def assemble_spine(run_id: str, market: str, spine: str, start: str, end: str) -> dict:
    """Concatenate slice predictions, validate coverage, persist the spine."""
    import hashlib

    import pandas as pd

    from spine_jobs import expected_days

    vol.reload()
    root = RUN_ROOT / run_id / market / spine
    paths = sorted(root.glob("asof_*/prediction.parquet"))
    if not paths:
        raise RuntimeError(f"no slices for {market}/{spine}")
    frames = []
    for path in paths:
        frame = pd.read_parquet(path)
        if "score" not in frame.columns:
            raise RuntimeError(f"missing score column in {path}")
        frames.append(frame)
    signal = pd.concat(frames).sort_index()
    if signal.index.has_duplicates:
        raise RuntimeError(f"duplicate signal rows for {market}/{spine}")
    calendar = [line.strip() for line in (DATA_DIR / "calendars" / "day.txt").read_text().splitlines() if line.strip()]
    actual_days = sorted({str(day)[:10] for day in signal.index.get_level_values(0).unique()})
    expected = expected_days(calendar, start, end)
    if actual_days != expected:
        missing = sorted(set(expected) - set(actual_days))[:5]
        extra = sorted(set(actual_days) - set(expected))[:5]
        raise RuntimeError(f"spine coverage mismatch: missing={missing} extra={extra}")
    out = root / "signal.parquet"
    tmp = out.with_name(out.name + ".tmp")
    signal.to_parquet(tmp)
    os.replace(tmp, out)
    receipt = {
        "status": "PASS",
        "kind": "g1_spine",
        "run_id": run_id,
        "market": market,
        "spine": spine,
        "start": start,
        "end": end,
        "n_rows": int(len(signal)),
        "n_days": len(actual_days),
        "n_instruments": int(signal.index.get_level_values(1).nunique()),
        "slices": len(paths),
        "signal_sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
    }
    (root / "spine_receipt.json").write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
    vol.commit()
    return receipt


@app.local_entrypoint()
def main(
    start: str = "2021-02-18",
    end: str = "2024-02-05",
    run_id: str = "regime_g1_20261102_a1",
    markets: str = "csi300,csi500,csi1000",
    spines: str = "A,B",
    max_jobs: int = 0,
    smoke: bool = False,
):
    from spine_jobs import build_jobs

    import subprocess

    try:
        code_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:
        code_sha = os.environ.get("GIT_COMMIT", "working-tree")
    calendar = read_calendar.remote()
    jobs = build_jobs(
        calendar,
        start,
        end,
        run_id,
        code_sha,
        markets=tuple(x.strip() for x in markets.split(",") if x.strip()),
        spines=tuple(x.strip() for x in spines.split(",") if x.strip()),
        num_threads=8,
    )
    print(f"[g1] calendar {start}..{end} | jobs={len(jobs)} | run_id={run_id}")
    if smoke:
        jobs = jobs[: max(1, int(max_jobs or 2))]
        print(f"[g1] SMOKE mode: running {len(jobs)} job(s)")
    started = time.time()
    results = list(fit_slice.map(jobs))
    print(f"[g1] fits finished: {len(results)} in {round(time.time() - started, 1)}s")
    failed = [row for row in results if row.get("status") != "PASS"]
    if failed:
        raise RuntimeError(f"{len(failed)} slice(s) failed; first={failed[0]}")
    if smoke:
        print("[g1] SMOKE PASS", results[0])
        return
    summaries = []
    for market in {job["market"] for job in jobs}:
        for spine in {job["spine"] for job in jobs if job["market"] == market}:
            summary = assemble_spine.remote(run_id, market, spine, start, end)
            summaries.append(summary)
            print(
                f"[g1] assembled {market}/{spine}: days={summary['n_days']} rows={summary['n_rows']} sha={summary['signal_sha256'][:12]}"
            )
    print("[g1] DONE", json.dumps(summaries, indent=2, ensure_ascii=False))
