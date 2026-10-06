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
    return cfg


@app.function(volumes={str(VOL_ROOT): vol}, cpu=4, memory=8192, timeout=4 * 3600)
def prepare(force: bool = True):
    """确保 Volume 数据最新（infi Volume 可能是旧版本）。"""
    import shutil
    import tarfile

    import requests

    marker = DATA_DIR / ".chenditc"
    if not force and marker.exists():
        print("[data] skip")
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
    vol.commit()
    cal = (DATA_DIR / "calendars" / "day.txt").read_text().strip().splitlines()
    print(f"[data] 就绪，日历至 {cal[-1]}")


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
    payload = zlib.compress(pickle.dumps(pred, protocol=pickle.HIGHEST_PROTOCOL), level=6)
    return {
        "freq": args["freq"],
        "phase": args.get("phase"),
        "retrain_asof": args["retrain_asof"],
        "signal_end": args["signal_end"],
        "n_rows": int(len(pred)),
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
            "topk": topk,
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
    """连续账户重训频率实验。

    每个 worker 只负责一个模型谱系区间的预测；driver 拼接全部预测后只运行一次
    TopkDropoutStrategy 回测。这样重训只替换模型，不清空持仓，也保证首个执行日
    使用 retrain_asof 当日收盘生成的上一交易步信号。
    """
    import json as _json
    import pickle
    import zlib

    import pandas as pd
    import qlib
    from qlib.backtest import backtest as normal_backtest

    from qlib_audit_fixes import read_trading_calendar
    from portfolio_performance import portfolio_performance

    prepare.remote(force=True)
    try:
        vol.reload()
    except Exception:
        pass
    cal = read_trading_calendar(DATA_DIR)
    start_i = bisect_left(cal, eval_from)
    if start_i >= len(cal) - 1 or cal[start_i] != eval_from:
        raise ValueError("eval_from must be a trading day with a following execution day")

    out = VOL_ROOT / "freq_experiment"
    reports_dir = out / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    results = {}
    for freq in [int(x) for x in freqs.split(",")]:
        if freq < 1:
            raise ValueError("retraining frequency must be positive")
        jobs = []
        i = start_i
        while i < len(cal) - 1:
            # Model trained after bar i scores bar i itself; those scores execute on i+1.
            # Stop its signal responsibility the day before the next retrain to avoid overlap.
            signal_end_i = min(i + freq - 1, len(cal) - 1)
            jobs.append({
                "freq": freq,
                "retrain_asof": cal[i],
                "signal_end": cal[signal_end_i],
                "market": market,
                "topk": topk,
                "nd": nd,
            })
            i += freq
        print(f"[freq] freq={freq}: {len(jobs)} 个重训点")

        outs = list(freq_window.map(jobs))
        chunks = []
        for o in sorted(outs, key=lambda x: x["retrain_asof"]):
            raw = zlib.decompress(o["pred_zlib_pickle"])
            pred = pickle.loads(raw)
            chunks.append(pred)
        if not chunks:
            raise RuntimeError(f"freq={freq}: no prediction chunks")

        signal = pd.concat(chunks).sort_index()
        if signal.index.has_duplicates:
            raise RuntimeError(f"freq={freq}: duplicate signal rows across retrain chunks")
        signal_days = signal.index.get_level_values(0)
        expected_signal_start = cal[start_i]
        if str(signal_days.min())[:10] != expected_signal_start:
            raise RuntimeError("missing bootstrap signal on retrain/eval anchor")

        execution_start = cal[start_i + 1]
        execution_end = cal[-1]
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
            "kwargs": {"signal": signal, "topk": topk, "n_drop": nd, "forbid_all_trade_at_limit": False},
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
        rep = pm["1day"][0]
        excess = (rep["return"] - rep["bench"] - rep["cost"]).dropna()
        if excess.empty:
            raise RuntimeError(f"freq={freq}: empty continuous-account report")
        perf = portfolio_performance(
            rep,
            initial_cash=100000000,
            backtest_start=execution_start,
        )
        x = excess.to_numpy(dtype=float)
        report_artifact = _write_report_artifact(
            rep,
            reports_dir / (
                f"report_{market}_freq{freq}_{execution_start}_{execution_end}.parquet"
            ),
        )
        results[str(freq)] = {
            "protocol": "continuous_account_board_aware_v4_cny_tick",
            "metric_version": perf["metric_version"],
            "n_retrains": len(jobs),
            "n_errors": 0,
            "n_days": len(x),
            "signal_start": expected_signal_start,
            "execution_start": execution_start,
            "execution_end": execution_end,
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
        print(f"[freq] freq={freq}: {results[str(freq)]}")

    with (out / "results.json").open("w") as f:
        _json.dump({
            "protocol": "continuous_account_board_aware_v4_cny_tick",
            "window": f"{eval_from}~{cal[-1]}",
            "market": market,
            "results": results,
        }, f, indent=2)
    vol.commit()
    return results



@app.function(volumes={str(VOL_ROOT): vol}, cpu=4, memory=8192, timeout=24 * 3600)
def retrain_phase_sensitivity_driver(
    freq: int = 20,
    eval_from: str = "2021-01-04",
    market: str = "csi1000",
    topk: int = 20,
    nd: int = 2,
    phases: str = "all",
):
    """Evaluate retraining-calendar phase sensitivity under one fixed protocol.

    All phases share the exact same execution window.  phase=0 reproduces the
    historical freq-driver schedule.  phase=1..freq-1 means the model lineage
    was already active that many sessions before eval_from.

    This is an audit, not a tuner: no phase is selected or deployed.
    """
    import json as _json
    import pickle
    import zlib

    import pandas as pd
    import qlib
    from qlib.backtest import backtest as normal_backtest

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

    prepare.remote(force=True)
    try:
        vol.reload()
    except Exception:
        pass

    cal = read_trading_calendar(DATA_DIR)
    start_i = bisect_left(cal, eval_from)
    if start_i >= len(cal) - 1 or cal[start_i] != eval_from:
        raise ValueError("eval_from must be a trading day with a following execution day")
    if start_i - max(phase_list) < 0:
        raise ValueError("insufficient pre-evaluation calendar for requested phases")

    all_jobs = []
    jobs_by_phase = {}
    for phase in phase_list:
        jobs = _phase_jobs(cal, start_i, freq, phase, market, topk, nd)
        jobs_by_phase[phase] = jobs
        all_jobs.extend(jobs)
    print(
        f"[phase] freq={freq} phases={phase_list}: "
        f"{len(all_jobs)} total model fits"
    )

    outs = list(freq_window.map(all_jobs))
    outs_by_phase = {phase: [] for phase in phase_list}
    for out in outs:
        phase = out.get("phase")
        if phase not in outs_by_phase:
            raise RuntimeError(f"unexpected phase output: {phase}")
        outs_by_phase[phase].append(out)

    execution_start = cal[start_i + 1]
    execution_end = cal[-1]
    qlib.init(**{**_load_task(market)["qlib_init"], "skip_if_reg": True})
    if market in ("star_chn", "chinext", "star"):
        from board_rules import star_chn_backtest_guard
        star_chn_backtest_guard(execution_start)

    executor = {
        "class": "SimulatorExecutor",
        "module_path": "qlib.backtest.executor",
        "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True},
    }
    report_root = VOL_ROOT / "freq_phase_sensitivity" / "reports"
    report_root.mkdir(parents=True, exist_ok=True)
    results = {}

    for phase in phase_list:
        chunks = []
        phase_outs = sorted(outs_by_phase[phase], key=lambda x: x["retrain_asof"])
        for out in phase_outs:
            chunks.append(pickle.loads(zlib.decompress(out["pred_zlib_pickle"])))
        if not chunks:
            raise RuntimeError(f"phase={phase}: no prediction chunks")
        signal = pd.concat(chunks).sort_index()
        if signal.index.has_duplicates:
            raise RuntimeError(f"phase={phase}: duplicate signal rows")
        signal_days = {str(x)[:10] for x in signal.index.get_level_values(0).unique()}
        if eval_from not in signal_days:
            raise RuntimeError(f"phase={phase}: bootstrap signal missing for {eval_from}")
        if cal[-2] not in signal_days:
            raise RuntimeError(f"phase={phase}: final executable signal day missing")

        strategy = {
            "class": "TopkDropoutStrategy",
            "module_path": "qlib.contrib.strategy",
            "kwargs": {
                "signal": signal,
                "topk": topk,
                "n_drop": nd,
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
            exchange_kwargs=research_exchange(
                execution_start, execution_end, codes=market
            ),
        )
        rep = pm["1day"][0]
        perf = portfolio_performance(
            rep,
            initial_cash=100000000,
            backtest_start=execution_start,
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
            "first_retrain_asof": jobs_by_phase[phase][0]["retrain_asof"],
            "n_retrains": len(jobs_by_phase[phase]),
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
        "protocol": "retrain_phase_sensitivity_v1_cny_tick",
        "purpose": "audit_only_do_not_select_best_phase",
        "market": market,
        "freq": freq,
        "topk": topk,
        "n_drop": nd,
        "eval_from": eval_from,
        "execution_start": execution_start,
        "execution_end": execution_end,
        "phases": phase_list,
        "complete_phase_grid": complete_grid,
        "results": results,
        "summary": summary,
    }
    out = VOL_ROOT / "freq_phase_sensitivity"
    out.mkdir(parents=True, exist_ok=True)
    with (out / f"phase_{market}_freq{freq}.json").open("w") as fh:
        _json.dump(payload, fh, indent=2, ensure_ascii=False)
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
            "topk": topk,
            "n_drop": nd,
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

    prepare.remote(force=True)
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
            "topk": topk,
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
        "protocol": "execution_attribution_v2_cny_tick",
        "market": market,
        "freq": freq,
        "topk": topk,
        "n_drop": nd,
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
