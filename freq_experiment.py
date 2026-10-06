# 重训频率对比实验（第六步，独立文件：不依赖主管道的 Secret/配置函数，可跨 workspace 运行）
# 用法：modal run freq_experiment.py::freq_driver --freqs "60,20"
# 设计要点：
# - 直接读镜像内的官方 yaml + 手动打补丁（不 import 主文件，避免其 Secret 引用）
# - 训练/验证/执行边界全部由 qlib_audit_fixes 的成熟期数学 + purge_cfg_splits 硬断言
# - 时序与 cron 生产一致：重训日 T 收盘训练 → 信号用于 [T+1, T'] 交易
import modal
from bisect import bisect_left
from pathlib import Path

APP_NAME = "qlib-freq-experiment"
VOL_NAME = "qlib-cn-data"
VOL_ROOT = Path("/vol")
DATA_DIR = VOL_ROOT / "cn_data"
MLRUNS_DIR = VOL_ROOT / "mlruns"

vol = modal.Volume.from_name(VOL_NAME, create_if_missing=True)

# 与主管道相同的 image（numpy 锁定 / helper 模块拷贝 / qlib 源码编译）
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("build-essential")
    .env({"MLFLOW_ALLOW_FILE_STORE": "true"})
    .pip_install(
        "numpy==1.26.4", "cython", "pandas==2.2.3", "pyyaml", "ruamel.yaml", "fire", "lightgbm", "mlflow",
        "dill", "filelock", "tqdm", "loguru", "joblib", "pyarrow", "pydantic-settings", "redis",
        "python-redis-lock", "pymongo", "gym", "cvxpy", "matplotlib", "jupyter", "nbconvert",
        "setuptools-scm", "akshare",
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
            or str(path) in ("modal_qlib_cn_a10g.py", "freq_experiment.py")
        ),
    )
    .run_commands(
        "cd /root/qlib && pip install . --no-build-isolation --no-deps",
        "cp /root/qlib/qlib_audit_fixes.py /root/qlib/qlib_live_retrain.py /root/qlib/board_rules.py /root/qlib/board_execution.py /root/qlib/portfolio_performance.py /root/",
    )
)

app = modal.App(APP_NAME, image=image)

from board_execution import legacy_scalar_exchange, research_exchange, uniform_open_exchange

YAML_PATH = "/root/qlib/examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158_csi500.yaml"


def _ensure_custom_pool(market: str) -> None:
    """新池（star_chn/chinext/star）池文件不存在则从 all.txt 生成（幂等，fail-closed）。"""
    from board_rules import EW_BENCH, POOL_BOARDS, build_custom_instruments

    if market not in EW_BENCH:
        return
    all_txt = DATA_DIR / "instruments" / "all.txt"
    pool_txt = DATA_DIR / "instruments" / f"{market}.txt"
    build_custom_instruments(all_txt, pool_txt, boards=POOL_BOARDS[market])
    bench = EW_BENCH[market]
    if not (DATA_DIR / "features" / bench.lower() / "close.day.bin").is_file():
        raise FileNotFoundError(f"{market} 基准 {bench} 无数据；先跑主管道 ::build_star_chn_bench")


BENCH_BY_MARKET = {"csi1000": "SH000852", "csi500": "SH000905", "csi300": "SH000300"}

# Frozen pre-tuner audit configurations. These are baselines, not tuned optima.
PRE_TUNER_AUDIT_CONFIGS = {
    "csi1000": {"topk": 20, "nd": 2},
    "chinext": {"topk": 20, "nd": 3},
    "star": {"topk": 50, "nd": 2},
}


def pre_tuner_audit_config(market: str) -> dict:
    if market not in PRE_TUNER_AUDIT_CONFIGS:
        raise ValueError(
            f"pre-tuner audit market must be one of {sorted(PRE_TUNER_AUDIT_CONFIGS)}"
        )
    return dict(PRE_TUNER_AUDIT_CONFIGS[market])




def _bench_of(market: str) -> str:
    """新池用各自等权合成基准（board_rules.EW_BENCH，需先构造）；csi 池用中证系。"""
    if market in ("star_chn", "chinext", "star"):
        from board_rules import EW_BENCH

        return EW_BENCH[market]
    return BENCH_BY_MARKET[market]


def _load_task(market: str) -> dict:
    """读官方 yaml 并打实验补丁（独立于主管道的 _load_and_patch_cfg）。"""
    from ruamel.yaml import YAML

    _ensure_custom_pool(market)
    with open(YAML_PATH) as f:
        cfg = YAML(typ="safe", pure=True).load(f)
    dk = cfg["task"]["dataset"]["kwargs"]
    handler = dk["handler"]["kwargs"]
    handler["instruments"] = market
    handler["label"] = ["Ref($close, -20)/$close - 1"]
    cfg["qlib_init"] = {"provider_uri": str(DATA_DIR), "region": "cn"}
    cfg["qlib_init"]["exp_manager"] = {
        "class": "MLflowExpManager", "module_path": "qlib.workflow.expm",
        "kwargs": {"uri": f"file:{MLRUNS_DIR}", "default_exp_name": "qlib-freq"},
    }
    from qlib_live_retrain import apply_lgb_reproducibility
    apply_lgb_reproducibility(cfg)
    return cfg


@app.function(volumes={str(VOL_ROOT): vol}, cpu=4, memory=8192, timeout=4 * 3600)
def prepare(force: bool = True, market: str = ""):
    """确保 Volume 数据快照，并为 custom pool 重建派生等权基准。"""
    import shutil
    import tarfile

    import requests

    marker = DATA_DIR / ".chenditc"

    def _ensure_market_artifacts():
        if market not in ("star_chn", "chinext", "star"):
            return None
        from board_rules import POOL_BOARDS, build_custom_instruments, build_ew_bench_files
        pool_txt = DATA_DIR / "instruments" / f"{market}.txt"
        build_custom_instruments(
            DATA_DIR / "instruments" / "all.txt",
            pool_txt,
            boards=POOL_BOARDS[market],
        )
        return build_ew_bench_files(DATA_DIR, market)

    if not force and marker.exists():
        bench_report = _ensure_market_artifacts()
        if bench_report is not None:
            print(f"[data] ensured custom benchmark: {bench_report}")
            vol.commit()
        print("[data] reuse committed provider snapshot")
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    url = "https://github.com/chenditc/investment_data/releases/latest/download/qlib_bin.tar.gz"
    zip_path = Path("/tmp/chenditc.tar.gz")
    with requests.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        with zip_path.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    extract = Path("/tmp/chenditc_extract")
    if extract.exists():
        shutil.rmtree(extract)
    extract.mkdir(parents=True)
    with tarfile.open(zip_path, "r:gz") as tf:
        tf.extractall(extract)
    base = extract
    if not (base / "features").exists():
        for sub in base.iterdir():
            if sub.is_dir() and (sub / "features").exists():
                base = sub
                break
    for name in ["features", "calendars", "instruments"]:
        p = DATA_DIR / name
        if p.exists():
            shutil.rmtree(p)
        shutil.copytree(base / name, p)
    marker.write_text("latest")
    bench_report = _ensure_market_artifacts()
    if bench_report is not None:
        print(f"[data] rebuilt custom benchmark: {bench_report}")
    vol.commit()
    cal = (DATA_DIR / "calendars" / "day.txt").read_text().strip().splitlines()
    print(f"[data] 就绪，日历至 {cal[-1]}")



def _stable_json_sha256(obj) -> str:
    import hashlib
    import json
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _signal_sha256(signal) -> str:
    """Canonical content hash for a prediction Series, independent of pickle bytes."""
    import hashlib
    import struct

    s = signal.sort_index()
    h = hashlib.sha256()
    h.update(b"qlib-signal-v1\n")
    for idx, value in s.items():
        parts = idx if isinstance(idx, tuple) else (idx,)
        for part in parts:
            h.update(str(part).encode())
            h.update(b"\x1f")
        h.update(struct.pack("!d", float(value)))
        h.update(b"\n")
    return h.hexdigest()


def _frame_sha256(frame) -> str:
    """Canonical content hash for a numeric daily report."""
    import hashlib

    text = frame.sort_index().to_csv(
        index=True,
        float_format="%.17g",
        date_format="%Y-%m-%dT%H:%M:%S.%f",
    )
    return hashlib.sha256(text.encode()).hexdigest()


def _runtime_repro_manifest(market: str, cutoff: str) -> dict:
    """Fingerprint provider, model semantics, code, and key runtime versions."""
    import hashlib
    import importlib.metadata as metadata
    import platform

    from qlib_live_retrain import LGB_REPRO_PARAMS, provider_training_fingerprint

    cfg = _load_task(market)
    model_cfg = cfg["task"]["model"]

    def version(name):
        try:
            return metadata.version(name)
        except metadata.PackageNotFoundError:
            return "unknown"

    source_path = Path(__file__)
    source_sha = (
        hashlib.sha256(source_path.read_bytes()).hexdigest()
        if source_path.is_file() else "unavailable"
    )
    provider_fp = provider_training_fingerprint(DATA_DIR, market, cutoff)
    manifest = {
        "manifest_version": "freq_repro_v1",
        "market": market,
        "provider_cutoff": cutoff,
        "provider_fingerprint": provider_fp,
        "model_config_sha256": _stable_json_sha256(model_cfg),
        "lgb_repro_params": dict(LGB_REPRO_PARAMS),
        "runtime": {
            "python": platform.python_version(),
            "pyqlib": version("pyqlib"),
            "lightgbm": version("lightgbm"),
            "numpy": version("numpy"),
            "pandas": version("pandas"),
        },
        "freq_experiment_source_sha256": source_sha,
    }
    manifest["snapshot_token"] = _stable_json_sha256(manifest)
    return manifest


def _snapshot_manifest_path(market: str) -> Path:
    return VOL_ROOT / "freq_repro" / f"provider_snapshot_{market}.json"


def _publish_snapshot_manifest(manifest: dict) -> None:
    import json

    path = _snapshot_manifest_path(manifest["market"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    vol.commit()


def _worker_assert_snapshot(args: dict) -> None:
    """Warm Modal workers must reload the Volume and see the driver's snapshot token."""
    import json

    try:
        vol.reload()
    except Exception as exc:
        raise RuntimeError(f"worker Volume reload failed: {exc}") from exc
    expected = args.get("snapshot_token")
    if not expected:
        raise RuntimeError("missing snapshot_token in retrain job")
    path = _snapshot_manifest_path(args["market"])
    if not path.is_file():
        raise RuntimeError(f"provider snapshot manifest missing: {path}")
    actual = json.loads(path.read_text()).get("snapshot_token")
    if actual != expected:
        raise RuntimeError(
            f"worker provider snapshot mismatch: expected={expected} actual={actual}"
        )


def _assemble_signal(outs):
    import pickle
    import zlib

    import pandas as pd

    chunks = []
    chunk_meta = []
    for out in sorted(outs, key=lambda x: x["retrain_asof"]):
        pred = pickle.loads(zlib.decompress(out["pred_zlib_pickle"]))
        actual_hash = _signal_sha256(pred)
        if actual_hash != out.get("prediction_sha256"):
            raise RuntimeError(
                f"prediction payload hash mismatch at {out['retrain_asof']}"
            )
        chunks.append(pred)
        chunk_meta.append({
            "repeat": out.get("repeat"),
            "phase": out.get("phase"),
            "retrain_asof": out["retrain_asof"],
            "signal_end": out["signal_end"],
            "n_rows": out["n_rows"],
            "prediction_sha256": actual_hash,
        })
    if not chunks:
        raise RuntimeError("no prediction chunks")
    signal = pd.concat(chunks).sort_index()
    if signal.index.has_duplicates:
        raise RuntimeError("duplicate signal rows across retrain chunks")
    return signal, chunk_meta


def _write_signal_artifact(signal, path: Path) -> dict:
    import hashlib

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    signal.sort_index().rename("score").to_frame().to_parquet(path, index=True)
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "content_sha256": _signal_sha256(signal),
        "rows": int(len(signal)),
    }


def _load_signal_artifact(meta: dict):
    import hashlib

    import pandas as pd

    path = Path(meta["path"])
    if not path.is_file():
        raise RuntimeError(f"baseline signal artifact missing: {path}")
    if hashlib.sha256(path.read_bytes()).hexdigest() != meta["sha256"]:
        raise RuntimeError("baseline signal artifact byte hash mismatch")
    frame = pd.read_parquet(path)
    if "score" not in frame.columns:
        raise RuntimeError("baseline signal artifact missing score column")
    signal = frame["score"].sort_index()
    if _signal_sha256(signal) != meta["content_sha256"]:
        raise RuntimeError("baseline signal artifact content hash mismatch")
    return signal


def _run_signal_backtest(signal, *, execution_start: str, execution_end: str,
                         market: str, topk: int, nd: int):
    import qlib
    from qlib.backtest import backtest as normal_backtest

    qlib.init(**{**_load_task(market)["qlib_init"], "skip_if_reg": True})
    if market in ("star_chn", "chinext", "star"):
        from board_rules import star_chn_backtest_guard
        star_chn_backtest_guard(execution_start)
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
            "topk": int(topk),
            "n_drop": int(nd),
            "forbid_all_trade_at_limit": False,
        },
    }
    pm, _ = normal_backtest(
        strategy=strategy,
        executor=executor,
        start_time=execution_start,
        end_time=execution_end,
        account=100000000,
        benchmark=_bench_of(market),
        exchange_kwargs=research_exchange(execution_start, execution_end, codes=market),
    )
    return pm["1day"][0]


@app.function(
    volumes={str(VOL_ROOT): vol},
    cpu=8,
    memory=24576,
    timeout=2 * 3600,
    max_containers=25,
)
def freq_window(args: dict):
    """重训点 worker：只训练并返回该模型负责区间的信号，不在这里重置账户回测。

    retrain_asof 当日收盘完成训练并对当日打分；该分数供下一交易日执行。
    signal_end 是下一次重训日前一交易日，因此各 worker 信号区间不重叠。
    """
    import pickle
    import zlib

    import qlib
    from qlib.data.dataset import Dataset
    from qlib.model.base import Model
    from qlib.utils import init_instance_by_config

    from qlib_audit_fixes import last_matured_sample, purge_cfg_splits, read_trading_calendar

    _worker_assert_snapshot(args)
    qlib.init(**{**_load_task(args["market"])["qlib_init"], "skip_if_reg": True})
    cal = read_trading_calendar(DATA_DIR)
    horizon = args.get("horizon", 20)
    asof_i = bisect_left(cal, args["retrain_asof"])
    valid_end_i = asof_i - horizon - 1
    valid_start_i = valid_end_i - args.get("valid_sessions", 252) + 1
    if valid_start_i <= 0 or valid_end_i <= 0:
        raise RuntimeError(f"insufficient history at {args['retrain_asof']}")

    cfg = _load_task(args["market"])
    dk = cfg["task"]["dataset"]["kwargs"]
    seg = dk["segments"]
    handler = dk["handler"]["kwargs"]
    train_end = last_matured_sample(cal, cal[valid_start_i], horizon)
    seg["train"] = ["2016-01-01", train_end]
    seg["valid"] = [cal[valid_start_i], cal[valid_end_i]]
    seg["test"] = [args["retrain_asof"], args["signal_end"]]
    handler["start_time"] = "2015-01-01"
    handler["end_time"] = args["signal_end"]
    handler["fit_start_time"] = "2016-01-01"
    handler["fit_end_time"] = train_end
    purge_cfg_splits(cfg, cal, horizon=horizon)

    m = init_instance_by_config(cfg["task"]["model"], accept_types=Model)
    ds = init_instance_by_config(cfg["task"]["dataset"], accept_types=Dataset)
    m.fit(ds)
    pred = m.predict(ds, segment="test")
    if pred.empty:
        raise RuntimeError(f"empty predictions {args['retrain_asof']}~{args['signal_end']}")
    first_signal = str(pred.index.get_level_values(0).min())[:10]
    last_signal = str(pred.index.get_level_values(0).max())[:10]
    if first_signal != args["retrain_asof"] or last_signal != args["signal_end"]:
        raise RuntimeError(
            f"prediction coverage mismatch: got {first_signal}~{last_signal}, "
            f"expected {args['retrain_asof']}~{args['signal_end']}"
        )
    prediction_sha = _signal_sha256(pred)
    payload = zlib.compress(pickle.dumps(pred, protocol=pickle.HIGHEST_PROTOCOL), level=6)
    return {
        "freq": args["freq"],
        "phase": args.get("phase"),
        "repeat": args.get("repeat"),
        "retrain_asof": args["retrain_asof"],
        "signal_end": args["signal_end"],
        "n_rows": int(len(pred)),
        "prediction_sha256": prediction_sha,
        "snapshot_token": args["snapshot_token"],
        "pred_zlib_pickle": payload,
    }



def _write_report_artifact(report, path: Path) -> dict:
    """Persist the raw daily Qlib report and return reproducibility metadata."""
    import hashlib

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    report.sort_index().to_parquet(path, index=True)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "path": str(path),
        "sha256": digest,
        "content_sha256": _frame_sha256(report),
        "rows": int(len(report)),
        "columns": [str(x) for x in report.columns],
    }


def _phase_jobs(calendar: list[str], start_i: int, freq: int, phase: int,
                market: str, topk: int, nd: int) -> list[dict]:
    """Build one retraining lineage with a fixed phase and common eval window.

    phase=0 reproduces the historical freq_driver anchor (first retrain exactly
    on eval_from).  phase=p starts the already-active lineage p sessions before
    eval_from, while every phase is evaluated from the same eval_from+1 date.
    """
    if freq < 1:
        raise ValueError("freq must be positive")
    if phase < 0 or phase >= freq:
        raise ValueError(f"phase must be in [0, {freq - 1}]")
    first_i = start_i - phase
    if first_i < 0:
        raise ValueError("insufficient calendar history for requested phase")
    jobs = []
    i = first_i
    while i < len(calendar) - 1:
        signal_end_i = min(i + freq - 1, len(calendar) - 1)
        jobs.append({
            "freq": freq,
            "phase": phase,
            "retrain_asof": calendar[i],
            "signal_end": calendar[signal_end_i],
            "market": market,
            "topk": int(topk),
            "nd": nd,
        })
        i += freq
    return jobs


def _phase_metric_summary(results: dict, field: str) -> dict:
    import numpy as np

    vals = [float(v[field]) for v in results.values() if v.get(field) is not None]
    if not vals:
        return {"n": 0, "min": None, "q25": None, "median": None, "q75": None, "max": None}
    arr = np.asarray(vals, dtype=float)
    return {
        "n": int(len(arr)),
        "min": round(float(np.min(arr)), 6),
        "q25": round(float(np.quantile(arr, 0.25)), 6),
        "median": round(float(np.median(arr)), 6),
        "q75": round(float(np.quantile(arr, 0.75)), 6),
        "max": round(float(np.max(arr)), 6),
    }


@app.function(volumes={str(VOL_ROOT): vol}, cpu=4, memory=8192, timeout=8 * 3600)
def freq_driver(freqs="60,20", eval_from="2021-01-04", market="csi1000", topk=20, nd=2):
    """Single-pass continuous-account baseline under a fingerprinted provider snapshot.

    This driver is deterministic but is *not* the reproducibility gate because it
    trains each lineage only once.  Run reproducibility_gate_driver before an
    expensive phase audit.
    """
    import json as _json

    import numpy as np

    from qlib_audit_fixes import read_trading_calendar
    from portfolio_performance import portfolio_performance

    prepare.remote(force=True, market=market)
    vol.reload()
    cal = read_trading_calendar(DATA_DIR)
    start_i = bisect_left(cal, eval_from)
    if start_i >= len(cal) - 1 or cal[start_i] != eval_from:
        raise ValueError("eval_from must be a trading day with a following execution day")

    manifest = _runtime_repro_manifest(market, cal[-1])
    _publish_snapshot_manifest(manifest)

    out = VOL_ROOT / "freq_experiment"
    reports_dir = out / "reports"
    signals_dir = out / "signals"
    reports_dir.mkdir(parents=True, exist_ok=True)
    signals_dir.mkdir(parents=True, exist_ok=True)
    results = {}

    for freq in [int(x) for x in freqs.split(",")]:
        if freq < 1:
            raise ValueError("retraining frequency must be positive")
        jobs = _phase_jobs(cal, start_i, freq, 0, market, int(topk), int(nd))
        for job in jobs:
            job["snapshot_token"] = manifest["snapshot_token"]
        print(f"[freq] freq={freq}: {len(jobs)} deterministic retrain points")

        outs = list(freq_window.map(jobs))
        signal, chunk_meta = _assemble_signal(outs)
        expected_signal_start = cal[start_i]
        if str(signal.index.get_level_values(0).min())[:10] != expected_signal_start:
            raise RuntimeError("missing bootstrap signal on retrain/eval anchor")

        execution_start = cal[start_i + 1]
        execution_end = cal[-1]
        rep = _run_signal_backtest(
            signal,
            execution_start=execution_start,
            execution_end=execution_end,
            market=market,
            topk=int(topk),
            nd=int(nd),
        )
        excess = (rep["return"] - rep["bench"] - rep["cost"]).dropna()
        if excess.empty:
            raise RuntimeError(f"freq={freq}: empty continuous-account report")
        perf = portfolio_performance(
            rep, initial_cash=100000000, backtest_start=execution_start
        )
        x = excess.to_numpy(dtype=float)
        signal_artifact = _write_signal_artifact(
            signal,
            signals_dir / (
                f"signal_{market}_freq{freq}_phase00_{expected_signal_start}_{cal[-2]}.parquet"
            ),
        )
        report_artifact = _write_report_artifact(
            rep,
            reports_dir / (
                f"report_{market}_freq{freq}_{execution_start}_{execution_end}.parquet"
            ),
        )
        results[str(freq)] = {
            "protocol": "continuous_account_board_aware_v5_repro",
            "metric_version": perf["metric_version"],
            "n_retrains": len(jobs),
            "n_errors": 0,
            "n_days": len(x),
            "signal_start": expected_signal_start,
            "execution_start": execution_start,
            "execution_end": execution_end,
            "signal_sha256": signal_artifact["content_sha256"],
            "signal_artifact": signal_artifact,
            "chunk_predictions": chunk_meta,
            "strategy_cagr": perf["strategy_cagr"],
            "benchmark_cagr": perf["benchmark_cagr"],
            "relative_excess_cagr": perf["relative_excess_cagr"],
            "strategy_max_drawdown": perf["strategy_max_drawdown"],
            "benchmark_max_drawdown": perf["benchmark_max_drawdown"],
            "relative_max_drawdown": perf["relative_max_drawdown"],
            "sharpe": perf["sharpe"],
            "information_ratio": perf["information_ratio"],
            "annual_volatility": perf["annual_volatility"],
            "account_return_max_error": perf["account_return_max_error"],
            "report_artifact": report_artifact,
            "legacy_ann_excess_arithmetic": round(float(x.mean() * 238), 4),
            "legacy_ir_arithmetic": (
                round(float(x.mean() / x.std(ddof=1) * (238 ** 0.5)), 3)
                if len(x) > 1 and x.std(ddof=1) > 0 else None
            ),
            "positive_ratio": round(float((x > 0).mean()), 3),
            "performance": perf,
        }

    payload = {
        "protocol": "continuous_account_board_aware_v5_repro",
        "window": f"{eval_from}~{cal[-1]}",
        "market": market,
        "topk": int(topk),
        "n_drop": int(nd),
        "benchmark": _bench_of(market),
        "reproducibility": manifest,
        "reproducibility_gate": {
            "passed": False,
            "reason": "single-pass baseline; run reproducibility_gate_driver",
        },
        "results": results,
    }
    market_path = out / f"results_{market}.json"
    market_path.write_text(_json.dumps(payload, indent=2, ensure_ascii=False))
    if market == "csi1000":
        (out / "results.json").write_text(
            _json.dumps(payload, indent=2, ensure_ascii=False)
        )
    vol.commit()
    return payload


def pre_tuner_audit_matrix() -> dict:
    """Frozen three-pool audit matrix used before any nested tuning."""
    from board_rules import EW_BENCH

    out = {}
    for market, cfg in PRE_TUNER_AUDIT_CONFIGS.items():
        out[market] = {
            **cfg,
            "benchmark": _bench_of(market),
            "phase_count": 20,
            "screening_phases": [0, 5, 10, 15],
            "eval_from": "2021-01-04",
        }
    if out["chinext"]["benchmark"] != EW_BENCH["chinext"]:
        raise RuntimeError("chinext audit benchmark drift")
    if out["star"]["benchmark"] != EW_BENCH["star"]:
        raise RuntimeError("star audit benchmark drift")
    return out


@app.function(volumes={str(VOL_ROOT): vol}, cpu=4, memory=8192, timeout=12 * 3600)
def reproducibility_gate_driver(
    freq: int = 20,
    eval_from: str = "2021-01-04",
    market: str = "csi1000",
    topk: int = 20,
    nd: int = 2,
):
    """Train the identical phase-0 lineage twice and require byte-level identities.

    The two repeats are submitted together to exercise independent workers and
    scheduling.  The gate passes only if every prediction chunk hash, the
    concatenated signal hash, and two portfolio report content hashes match.
    A passing gate also writes the canonical phase-0 baseline and signal
    artifact consumed by retrain_phase_sensitivity_driver.
    """
    import json as _json

    import numpy as np

    from qlib_audit_fixes import read_trading_calendar
    from portfolio_performance import portfolio_performance

    if freq < 1:
        raise ValueError("freq must be positive")
    prepare.remote(force=True, market=market)
    vol.reload()
    cal = read_trading_calendar(DATA_DIR)
    start_i = bisect_left(cal, eval_from)
    if start_i >= len(cal) - 1 or cal[start_i] != eval_from:
        raise ValueError("eval_from must be a trading day with a following execution day")

    manifest = _runtime_repro_manifest(market, cal[-1])
    _publish_snapshot_manifest(manifest)
    base_jobs = _phase_jobs(cal, start_i, freq, 0, market, int(topk), int(nd))
    jobs = []
    for repeat in (0, 1):
        for original in base_jobs:
            job = dict(original)
            job["repeat"] = repeat
            job["snapshot_token"] = manifest["snapshot_token"]
            jobs.append(job)
    print(
        f"[repro] {market} freq={freq}: {len(base_jobs)} retrains x2 = {len(jobs)} fits"
    )

    outs = list(freq_window.map(jobs))
    by_repeat = {0: [], 1: []}
    for out in outs:
        repeat = out.get("repeat")
        if repeat not in by_repeat:
            raise RuntimeError(f"unexpected repeat id: {repeat}")
        by_repeat[repeat].append(out)

    signal_a, chunks_a = _assemble_signal(by_repeat[0])
    signal_b, chunks_b = _assemble_signal(by_repeat[1])
    hashes_a = [(x["retrain_asof"], x["prediction_sha256"]) for x in chunks_a]
    hashes_b = [(x["retrain_asof"], x["prediction_sha256"]) for x in chunks_b]
    if hashes_a != hashes_b:
        mismatches = [
            {"a": a, "b": b}
            for a, b in zip(hashes_a, hashes_b)
            if a != b
        ][:10]
        raise RuntimeError(f"repro gate failed: prediction chunk mismatch {mismatches}")

    signal_hash_a = _signal_sha256(signal_a)
    signal_hash_b = _signal_sha256(signal_b)
    if signal_hash_a != signal_hash_b:
        raise RuntimeError("repro gate failed: concatenated signal hash mismatch")

    execution_start = cal[start_i + 1]
    execution_end = cal[-1]
    rep_a = _run_signal_backtest(
        signal_a,
        execution_start=execution_start,
        execution_end=execution_end,
        market=market,
        topk=int(topk),
        nd=int(nd),
    )
    rep_b = _run_signal_backtest(
        signal_b,
        execution_start=execution_start,
        execution_end=execution_end,
        market=market,
        topk=int(topk),
        nd=int(nd),
    )
    report_hash_a = _frame_sha256(rep_a)
    report_hash_b = _frame_sha256(rep_b)
    if report_hash_a != report_hash_b:
        raise RuntimeError("repro gate failed: portfolio report content hash mismatch")

    perf = portfolio_performance(
        rep_a, initial_cash=100000000, backtest_start=execution_start
    )
    excess = (rep_a["return"] - rep_a["bench"] - rep_a["cost"]).dropna()
    x = excess.to_numpy(dtype=float)

    out = VOL_ROOT / "freq_experiment"
    signals_dir = out / "signals"
    reports_dir = out / "reports"
    signal_artifact = _write_signal_artifact(
        signal_a,
        signals_dir / (
            f"signal_{market}_freq{freq}_phase00_{eval_from}_{cal[-2]}.parquet"
        ),
    )
    report_artifact = _write_report_artifact(
        rep_a,
        reports_dir / (
            f"report_{market}_freq{freq}_{execution_start}_{execution_end}.parquet"
        ),
    )
    result = {
        "protocol": "continuous_account_board_aware_v5_repro",
        "metric_version": perf["metric_version"],
        "n_retrains": len(base_jobs),
        "n_errors": 0,
        "n_days": int(len(excess)),
        "signal_start": eval_from,
        "execution_start": execution_start,
        "execution_end": execution_end,
        "signal_sha256": signal_hash_a,
        "signal_artifact": signal_artifact,
        "chunk_predictions": chunks_a,
        "strategy_cagr": perf["strategy_cagr"],
        "benchmark_cagr": perf["benchmark_cagr"],
        "relative_excess_cagr": perf["relative_excess_cagr"],
        "strategy_max_drawdown": perf["strategy_max_drawdown"],
        "benchmark_max_drawdown": perf["benchmark_max_drawdown"],
        "relative_max_drawdown": perf["relative_max_drawdown"],
        "sharpe": perf["sharpe"],
        "information_ratio": perf["information_ratio"],
        "annual_volatility": perf["annual_volatility"],
        "account_return_max_error": perf["account_return_max_error"],
        "report_artifact": report_artifact,
        "legacy_ann_excess_arithmetic": round(float(x.mean() * 238), 4),
        "legacy_ir_arithmetic": (
            round(float(x.mean() / x.std(ddof=1) * (238 ** 0.5)), 3)
            if len(x) > 1 and x.std(ddof=1) > 0 else None
        ),
        "positive_ratio": round(float((x > 0).mean()), 3),
        "performance": perf,
    }
    gate = {
        "passed": True,
        "gate_version": "phase0_double_fit_v1",
        "prediction_chunks_match": True,
        "signal_hash_match": True,
        "report_hash_match": True,
        "repeat_a_signal_sha256": signal_hash_a,
        "repeat_b_signal_sha256": signal_hash_b,
        "repeat_a_report_content_sha256": report_hash_a,
        "repeat_b_report_content_sha256": report_hash_b,
        "n_retrains_per_repeat": len(base_jobs),
    }
    payload = {
        "protocol": "continuous_account_board_aware_v5_repro",
        "window": f"{eval_from}~{cal[-1]}",
        "market": market,
        "topk": int(topk),
        "n_drop": int(nd),
        "benchmark": _bench_of(market),
        "reproducibility": manifest,
        "reproducibility_gate": gate,
        "results": {str(freq): result},
    }

    out.mkdir(parents=True, exist_ok=True)
    (out / f"results_{market}.json").write_text(
        _json.dumps(payload, indent=2, ensure_ascii=False)
    )
    if market == "csi1000":
        (out / "results.json").write_text(
            _json.dumps(payload, indent=2, ensure_ascii=False)
        )
    gate_dir = VOL_ROOT / "freq_repro"
    gate_dir.mkdir(parents=True, exist_ok=True)
    (gate_dir / f"gate_{market}_freq{freq}.json").write_text(
        _json.dumps(payload, indent=2, ensure_ascii=False)
    )
    vol.commit()
    print(
        f"[repro] PASS signal={signal_hash_a[:12]} report={report_hash_a[:12]} "
        f"CAGR={perf['strategy_cagr']:.4f}"
    )
    return payload


@app.function(volumes={str(VOL_ROOT): vol}, cpu=4, memory=8192, timeout=24 * 3600)
def retrain_phase_sensitivity_driver(
    freq: int = 20,
    eval_from: str = "2021-01-04",
    market: str = "csi1000",
    topk: int = 20,
    nd: int = 2,
    phases: str = "all",
    require_repro_gate: bool = True,
):
    """Evaluate retraining-calendar phase sensitivity after a reproducibility gate.

    Phase 0 is reused from the passing double-fit gate instead of being trained
    a third time.  All other phases use the exact same provider snapshot and
    deterministic LightGBM policy.  The driver fails before launching expensive
    model fits when the baseline manifest, runtime, provider fingerprint, or
    portfolio configuration differs.
    """
    import json as _json

    from qlib_audit_fixes import read_trading_calendar
    from portfolio_performance import portfolio_performance

    if freq < 2:
        raise ValueError("phase sensitivity requires freq >= 2")
    if phases == "all":
        phase_list = list(range(freq))
    else:
        phase_list = sorted({int(x.strip()) for x in phases.split(",") if x.strip()})
        if not phase_list:
            raise ValueError("phases is empty")
        if phase_list[0] < 0 or phase_list[-1] >= freq:
            raise ValueError(f"phases must be within 0..{freq - 1}")

    # Reuse the committed provider snapshot from the reproducibility gate.
    # For custom pools, prepare(force=False) only ensures derived pool/benchmark
    # files and never downloads a newer provider package.
    prepare.remote(force=False, market=market)
    vol.reload()
    cal = read_trading_calendar(DATA_DIR)
    start_i = bisect_left(cal, eval_from)
    if start_i >= len(cal) - 1 or cal[start_i] != eval_from:
        raise ValueError("eval_from must be a trading day with a following execution day")
    if start_i - max(phase_list) < 0:
        raise ValueError("insufficient pre-evaluation calendar for requested phases")

    current_manifest = _runtime_repro_manifest(market, cal[-1])
    baseline_path = VOL_ROOT / "freq_experiment" / f"results_{market}.json"
    if not baseline_path.is_file() and market == "csi1000":
        baseline_path = VOL_ROOT / "freq_experiment" / "results.json"
    if not baseline_path.is_file():
        raise RuntimeError(
            f"missing baseline result {baseline_path}; run reproducibility_gate_driver first"
        )
    baseline = _json.loads(baseline_path.read_text())
    gate = baseline.get("reproducibility_gate") or {}
    if require_repro_gate and not gate.get("passed"):
        raise RuntimeError(
            "phase audit requires a passing reproducibility_gate_driver baseline"
        )
    if baseline.get("protocol") != "continuous_account_board_aware_v5_repro":
        raise RuntimeError("baseline protocol is not v5 reproducible protocol")
    if baseline.get("market") != market:
        raise RuntimeError("baseline market mismatch")
    if int(baseline.get("topk", -1)) != int(topk) or int(baseline.get("n_drop", -1)) != int(nd):
        raise RuntimeError("baseline topk/n_drop mismatch")
    if baseline.get("reproducibility") != current_manifest:
        raise RuntimeError(
            "provider/runtime/source manifest changed since reproducibility gate; "
            "rerun the gate before phase audit"
        )
    baseline_result = (baseline.get("results") or {}).get(str(freq))
    if baseline_result is None:
        raise RuntimeError(f"baseline missing freq={freq}")

    snapshot_token = current_manifest["snapshot_token"]
    _publish_snapshot_manifest(current_manifest)

    jobs_by_phase = {}
    all_jobs = []
    for phase in phase_list:
        if phase == 0:
            jobs_by_phase[phase] = []
            continue
        jobs = _phase_jobs(cal, start_i, freq, phase, market, int(topk), int(nd))
        for job in jobs:
            job["snapshot_token"] = snapshot_token
        jobs_by_phase[phase] = jobs
        all_jobs.extend(jobs)
    print(
        f"[phase] freq={freq} phases={phase_list}: "
        f"{len(all_jobs)} model fits; phase0 reused from repro gate"
    )

    outs_by_phase = {phase: [] for phase in phase_list}
    if all_jobs:
        outs = list(freq_window.map(all_jobs))
        for out in outs:
            phase = out.get("phase")
            if phase not in outs_by_phase:
                raise RuntimeError(f"unexpected phase output: {phase}")
            outs_by_phase[phase].append(out)

    execution_start = cal[start_i + 1]
    execution_end = cal[-1]
    report_root = VOL_ROOT / "freq_phase_sensitivity" / "reports"
    report_root.mkdir(parents=True, exist_ok=True)
    results = {}

    for phase in phase_list:
        if phase == 0:
            signal = _load_signal_artifact(baseline_result["signal_artifact"])
            if _signal_sha256(signal) != baseline_result["signal_sha256"]:
                raise RuntimeError("baseline phase0 signal hash mismatch")
            results["0"] = {
                "phase": 0,
                "source": "repro_gate_baseline_reuse",
                "first_retrain_asof": eval_from,
                "n_retrains": int(baseline_result["n_retrains"]),
                "signal_sha256": baseline_result["signal_sha256"],
                "signal_artifact": baseline_result["signal_artifact"],
                "strategy_cagr": baseline_result["strategy_cagr"],
                "benchmark_cagr": baseline_result["benchmark_cagr"],
                "relative_excess_cagr": baseline_result["relative_excess_cagr"],
                "strategy_max_drawdown": baseline_result["strategy_max_drawdown"],
                "relative_max_drawdown": baseline_result["relative_max_drawdown"],
                "sharpe": baseline_result["sharpe"],
                "information_ratio": baseline_result["information_ratio"],
                "account_return_max_error": baseline_result["account_return_max_error"],
                "report_artifact": baseline_result["report_artifact"],
                "performance": baseline_result["performance"],
            }
            continue

        signal, chunk_meta = _assemble_signal(outs_by_phase[phase])
        signal_days = {str(x)[:10] for x in signal.index.get_level_values(0).unique()}
        if eval_from not in signal_days:
            raise RuntimeError(f"phase={phase}: bootstrap signal missing for {eval_from}")
        if cal[-2] not in signal_days:
            raise RuntimeError(f"phase={phase}: final executable signal day missing")

        rep = _run_signal_backtest(
            signal,
            execution_start=execution_start,
            execution_end=execution_end,
            market=market,
            topk=int(topk),
            nd=int(nd),
        )
        perf = portfolio_performance(
            rep, initial_cash=100000000, backtest_start=execution_start
        )
        report_artifact = _write_report_artifact(
            rep,
            report_root / (
                f"report_{market}_freq{freq}_phase{phase:02d}_"
                f"{execution_start}_{execution_end}.parquet"
            ),
        )
        results[str(phase)] = {
            "phase": phase,
            "source": "deterministic_refit",
            "first_retrain_asof": jobs_by_phase[phase][0]["retrain_asof"],
            "n_retrains": len(jobs_by_phase[phase]),
            "signal_sha256": _signal_sha256(signal),
            "chunk_predictions": chunk_meta,
            "strategy_cagr": perf["strategy_cagr"],
            "benchmark_cagr": perf["benchmark_cagr"],
            "relative_excess_cagr": perf["relative_excess_cagr"],
            "strategy_max_drawdown": perf["strategy_max_drawdown"],
            "relative_max_drawdown": perf["relative_max_drawdown"],
            "sharpe": perf["sharpe"],
            "information_ratio": perf["information_ratio"],
            "account_return_max_error": perf["account_return_max_error"],
            "report_artifact": report_artifact,
            "performance": perf,
        }
        print(
            f"[phase] {phase:02d}: CAGR={perf['strategy_cagr']:.4f} "
            f"rel={perf['relative_excess_cagr']:.4f} "
            f"MDD={perf['strategy_max_drawdown']:.4f} "
            f"Sharpe={perf['sharpe']}"
        )

    complete_grid = phase_list == list(range(freq))
    metric_fields = [
        "strategy_cagr",
        "relative_excess_cagr",
        "strategy_max_drawdown",
        "relative_max_drawdown",
        "sharpe",
        "information_ratio",
    ]
    summary = {
        field: _phase_metric_summary(results, field) for field in metric_fields
    }
    summary["strategy_cagr_range_pp"] = round(
        (summary["strategy_cagr"]["max"] - summary["strategy_cagr"]["min"]) * 100,
        3,
    )
    summary["relative_excess_cagr_range_pp"] = round(
        (
            summary["relative_excess_cagr"]["max"]
            - summary["relative_excess_cagr"]["min"]
        ) * 100,
        3,
    )
    payload = {
        "protocol": "retrain_phase_sensitivity_v2_repro",
        "purpose": "audit_only_do_not_select_best_phase",
        "market": market,
        "freq": freq,
        "topk": int(topk),
        "n_drop": int(nd),
        "eval_from": eval_from,
        "execution_start": execution_start,
        "execution_end": execution_end,
        "phases": phase_list,
        "complete_phase_grid": complete_grid,
        "phase0_reused_from_repro_gate": 0 in phase_list,
        "reproducibility": current_manifest,
        "reproducibility_gate": gate,
        "results": results,
        "summary": summary,
    }
    out = VOL_ROOT / "freq_phase_sensitivity"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"phase_{market}_freq{freq}.json").write_text(
        _json.dumps(payload, indent=2, ensure_ascii=False)
    )
    vol.commit()
    return payload


def _filter_signal_excluding_growth_boards(signal):
    """Remove ChiNext/STAR instruments from an already-trained signal.

    This deliberately does not retrain the model.  E therefore answers only
    the execution/universe question: "what if the same model scores were not
    allowed to enter those boards?"
    """
    from board_rules import board_of

    idx = signal.index
    if "instrument" in idx.names:
        inst = idx.get_level_values("instrument")
    else:
        inst = idx.get_level_values(-1)
    keep = [board_of(str(x)) not in ("chinext", "star") for x in inst]
    return signal[keep]


def _run_execution_protocol(signal, *, protocol: str, execution_start: str,
                            execution_end: str, market: str, topk: int, nd: int):
    """Backtest one frozen signal under exactly one execution protocol."""
    import numpy as np
    from qlib.backtest import backtest as normal_backtest
    from portfolio_performance import portfolio_performance

    if protocol == "REF_LEGACY_EXACT":
        # Historical v2 reference: same-day $change oracle + symmetric limit
        # blocking (TopkDropoutStrategy default).  Diagnostic only.
        exchange_kwargs = legacy_scalar_exchange()
        forbid_all = True
        sig = signal
    elif protocol == "A_LEGACY_ORACLE_CURRENT_DIRECTION":
        # Keep the old same-day-close oracle, but use today's direction-aware
        # strategy semantics so A->B isolates information timing only.
        exchange_kwargs = legacy_scalar_exchange()
        forbid_all = False
        sig = signal
    elif protocol == "B_UNIFORM_OPEN_095":
        # Same 9.5% scalar policy, but computed only from open/previous close.
        exchange_kwargs = uniform_open_exchange(
            execution_start, execution_end, codes=market, ratio=0.095
        )
        forbid_all = False
        sig = signal
    elif protocol == "C_BOARD_AWARE":
        exchange_kwargs = research_exchange(
            execution_start, execution_end, codes=market
        )
        forbid_all = False
        sig = signal
    elif protocol == "D_BOARD_AWARE_HIGH_OPEN_5":
        # Diagnostic overlay: uses the realised opening print to decide whether
        # the buy is allowed, then fills at that same open.  This measures the
        # value of the rule but is not automatically an implementable auction
        # protocol without a post-open execution convention.
        exchange_kwargs = research_exchange(
            execution_start, execution_end, codes=market, high_open_block=0.05
        )
        forbid_all = False
        sig = signal
    elif protocol == "E_BOARD_AWARE_NO_CHINEXT_STAR":
        exchange_kwargs = research_exchange(
            execution_start, execution_end, codes=market
        )
        forbid_all = False
        sig = _filter_signal_excluding_growth_boards(signal)
    else:
        raise ValueError(f"unknown execution protocol: {protocol}")

    executor = {
        "class": "SimulatorExecutor",
        "module_path": "qlib.backtest.executor",
        "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True},
    }
    strategy = {
        "class": "TopkDropoutStrategy",
        "module_path": "qlib.contrib.strategy",
        "kwargs": {
            "signal": sig,
            "topk": int(topk),
            "n_drop": int(nd),
            "forbid_all_trade_at_limit": forbid_all,
        },
    }
    pm, _ = normal_backtest(
        strategy=strategy,
        executor=executor,
        start_time=execution_start,
        end_time=execution_end,
        account=100000000,
        benchmark=_bench_of(market),
        exchange_kwargs=exchange_kwargs,
    )
    rep = pm["1day"][0]
    excess = (rep["return"] - rep["bench"] - rep["cost"]).dropna()
    ret = rep["return"].dropna()
    cost = rep["cost"].dropna()
    if excess.empty:
        raise RuntimeError(f"{protocol}: empty report")
    perf = portfolio_performance(
        rep,
        initial_cash=100000000,
        backtest_start=execution_start,
    )
    x = excess.to_numpy(dtype=float)
    cum = np.cumsum(x)
    yearly = {}
    for year, vals in excess.groupby(excess.index.year):
        arr = vals.to_numpy(dtype=float)
        yearly[str(int(year))] = {
            "excess_sum": round(float(arr.sum()), 4),
            "ann_excess_from_daily_mean": round(float(arr.mean() * 238), 4),
            "n_days": int(len(arr)),
        }

    turnover_col = None
    for candidate in ("turnover", "total_turnover"):
        if candidate in rep.columns:
            turnover_col = candidate
            break

    result = {
        "protocol": protocol,
        "metric_version": perf["metric_version"],
        "n_days": int(len(excess)),
        "strategy_cagr": perf["strategy_cagr"],
        "benchmark_cagr": perf["benchmark_cagr"],
        "relative_excess_cagr": perf["relative_excess_cagr"],
        "strategy_max_drawdown": perf["strategy_max_drawdown"],
        "benchmark_max_drawdown": perf["benchmark_max_drawdown"],
        "relative_max_drawdown": perf["relative_max_drawdown"],
        "sharpe": perf["sharpe"],
        "information_ratio": perf["information_ratio"],
        "annual_volatility": perf["annual_volatility"],
        "account_return_max_error": perf["account_return_max_error"],
        "legacy_ann_excess_arithmetic": round(float(x.mean() * 238), 4),
        "legacy_ir_arithmetic": (
            round(float(x.mean() / x.std(ddof=1) * np.sqrt(238)), 3)
            if len(x) > 1 and x.std(ddof=1) > 0 else None
        ),
        "legacy_max_drawdown_excess_arithmetic": round(
            float((cum - np.maximum.accumulate(cum)).min()), 4
        ),
        "strategy_total_return_sum_legacy": round(float(ret.sum()), 4),
        "total_cost_sum": round(float(cost.sum()), 4),
        "mean_turnover": (
            round(float(rep[turnover_col].dropna().mean()), 6)
            if turnover_col and not rep[turnover_col].dropna().empty else None
        ),
        "turnover_source": turnover_col,
        "annual_excess_arithmetic_legacy": yearly,
        "performance": perf,
    }
    return result


@app.function(volumes={str(VOL_ROOT): vol}, cpu=4, memory=8192, timeout=8 * 3600)
def execution_attribution_driver(freq: int = 20, eval_from: str = "2021-01-04",
                                 market: str = "csi1000", topk: int = 20, nd: int = 2):
    """Freeze one trained signal path and compare execution protocols only.

    Attribution map:
      REF -> A : old symmetric-limit strategy semantics
      A   -> B : same-day-close ($change) oracle / information-timing effect
      B   -> C : uniform 9.5% -> board/date-aware statutory execution
      C   -> D : 5% high-open overlay (diagnostic)
      C   -> E : excluding ChiNext/STAR from the same frozen signal

    The model is trained only once per retraining point.  All protocols consume
    the exact same concatenated signal path; E only filters instruments after
    scoring and never retrains.
    """
    import json as _json
    import pickle
    import zlib

    import pandas as pd
    import qlib

    from qlib_audit_fixes import read_trading_calendar

    if freq < 1:
        raise ValueError("freq must be positive")

    prepare.remote(force=True, market=market)
    try:
        vol.reload()
    except Exception:
        pass

    cal = read_trading_calendar(DATA_DIR)
    start_i = bisect_left(cal, eval_from)
    if start_i >= len(cal) - 1 or cal[start_i] != eval_from:
        raise ValueError("eval_from must be a trading day with a following execution day")

    jobs = []
    i = start_i
    while i < len(cal) - 1:
        signal_end_i = min(i + freq - 1, len(cal) - 1)
        jobs.append({
            "freq": freq,
            "retrain_asof": cal[i],
            "signal_end": cal[signal_end_i],
            "market": market,
            "topk": int(topk),
            "nd": nd,
        })
        i += freq

    print(f"[attrib] freq={freq}: train {len(jobs)} model windows once")
    outs = list(freq_window.map(jobs))
    chunks = []
    for o in sorted(outs, key=lambda x: x["retrain_asof"]):
        chunks.append(pickle.loads(zlib.decompress(o["pred_zlib_pickle"])))
    if not chunks:
        raise RuntimeError("no signal chunks")

    signal = pd.concat(chunks).sort_index()
    if signal.index.has_duplicates:
        raise RuntimeError("duplicate signal rows across retrain chunks")
    if str(signal.index.get_level_values(0).min())[:10] != cal[start_i]:
        raise RuntimeError("missing attribution bootstrap signal")

    execution_start = cal[start_i + 1]
    execution_end = cal[-1]
    qlib.init(**{**_load_task(market)["qlib_init"], "skip_if_reg": True})

    protocols = [
        "REF_LEGACY_EXACT",
        "A_LEGACY_ORACLE_CURRENT_DIRECTION",
        "B_UNIFORM_OPEN_095",
        "C_BOARD_AWARE",
        "D_BOARD_AWARE_HIGH_OPEN_5",
        "E_BOARD_AWARE_NO_CHINEXT_STAR",
    ]
    results = {}
    for name in protocols:
        print(f"[attrib] running {name}")
        results[name] = _run_execution_protocol(
            signal,
            protocol=name,
            execution_start=execution_start,
            execution_end=execution_end,
            market=market,
            topk=topk,
            nd=nd,
        )

    def metric(name, field):
        return results[name][field]

    attribution = {
        "metric": "relative_excess_cagr",
        "REF_to_A_direction_semantics_pp": round(
            (metric("A_LEGACY_ORACLE_CURRENT_DIRECTION", "relative_excess_cagr")
             - metric("REF_LEGACY_EXACT", "relative_excess_cagr")) * 100, 2
        ),
        "A_to_B_same_day_close_oracle_pp": round(
            (metric("B_UNIFORM_OPEN_095", "relative_excess_cagr")
             - metric("A_LEGACY_ORACLE_CURRENT_DIRECTION", "relative_excess_cagr")) * 100, 2
        ),
        "B_to_C_board_rule_pp": round(
            (metric("C_BOARD_AWARE", "relative_excess_cagr")
             - metric("B_UNIFORM_OPEN_095", "relative_excess_cagr")) * 100, 2
        ),
        "C_to_D_high_open_5_pp": round(
            (metric("D_BOARD_AWARE_HIGH_OPEN_5", "relative_excess_cagr")
             - metric("C_BOARD_AWARE", "relative_excess_cagr")) * 100, 2
        ),
        "C_to_E_exclude_growth_boards_pp": round(
            (metric("E_BOARD_AWARE_NO_CHINEXT_STAR", "relative_excess_cagr")
             - metric("C_BOARD_AWARE", "relative_excess_cagr")) * 100, 2
        ),
    }
    legacy_attribution = {
        "metric": "legacy_ann_excess_arithmetic",
        "REF_to_A_direction_semantics_pp": round(
            (metric("A_LEGACY_ORACLE_CURRENT_DIRECTION", "legacy_ann_excess_arithmetic")
             - metric("REF_LEGACY_EXACT", "legacy_ann_excess_arithmetic")) * 100, 2
        ),
        "A_to_B_same_day_close_oracle_pp": round(
            (metric("B_UNIFORM_OPEN_095", "legacy_ann_excess_arithmetic")
             - metric("A_LEGACY_ORACLE_CURRENT_DIRECTION", "legacy_ann_excess_arithmetic")) * 100, 2
        ),
        "B_to_C_board_rule_pp": round(
            (metric("C_BOARD_AWARE", "legacy_ann_excess_arithmetic")
             - metric("B_UNIFORM_OPEN_095", "legacy_ann_excess_arithmetic")) * 100, 2
        ),
        "C_to_D_high_open_5_pp": round(
            (metric("D_BOARD_AWARE_HIGH_OPEN_5", "legacy_ann_excess_arithmetic")
             - metric("C_BOARD_AWARE", "legacy_ann_excess_arithmetic")) * 100, 2
        ),
        "C_to_E_exclude_growth_boards_pp": round(
            (metric("E_BOARD_AWARE_NO_CHINEXT_STAR", "legacy_ann_excess_arithmetic")
             - metric("C_BOARD_AWARE", "legacy_ann_excess_arithmetic")) * 100, 2
        ),
    }

    payload = {
        "protocol": "execution_attribution_v3_repro",
        "market": market,
        "freq": freq,
        "topk": int(topk),
        "n_drop": int(nd),
        "window": f"{execution_start}~{execution_end}",
        "signal_rows": int(len(signal)),
        "n_retrains": len(jobs),
        "results": results,
        "attribution_pp": attribution,
        "legacy_attribution_arithmetic_pp": legacy_attribution,
        "notes": {
            "REF_LEGACY_EXACT": "legacy same-day $change oracle; diagnostic only",
            "D_BOARD_AWARE_HIGH_OPEN_5": (
                "diagnostic conditional on observed open; not automatically an implementable "
                "opening-auction execution protocol"
            ),
            "E_BOARD_AWARE_NO_CHINEXT_STAR": (
                "same trained signal; filters ChiNext/STAR after scoring, no retraining"
            ),
        },
    }

    out = VOL_ROOT / "execution_attribution"
    out.mkdir(parents=True, exist_ok=True)
    with (out / f"attrib_{market}_freq{freq}.json").open("w") as fh:
        _json.dump(payload, fh, indent=2, ensure_ascii=False)
    vol.commit()
    print(f"[attrib] attribution(pp): {attribution}")
    return payload
