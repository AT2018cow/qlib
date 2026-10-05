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
        "cp /root/qlib/qlib_audit_fixes.py /root/qlib/qlib_live_retrain.py /root/qlib/board_rules.py /root/qlib/board_execution.py /root/",
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
        "retrain_asof": args["retrain_asof"],
        "signal_end": args["signal_end"],
        "n_rows": int(len(pred)),
        "pred_zlib_pickle": payload,
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

    import numpy as np
    import pandas as pd
    import qlib
    from qlib.backtest import backtest as normal_backtest

    from qlib_audit_fixes import read_trading_calendar

    prepare.remote(force=True)
    try:
        vol.reload()
    except Exception:
        pass
    cal = read_trading_calendar(DATA_DIR)
    start_i = bisect_left(cal, eval_from)
    if start_i >= len(cal) - 1 or cal[start_i] != eval_from:
        raise ValueError("eval_from must be a trading day with a following execution day")

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
        x = excess.to_numpy(dtype=float)
        cum = np.cumsum(x)
        results[str(freq)] = {
            "protocol": "continuous_account_board_aware_v3",
            "n_retrains": len(jobs),
            "n_errors": 0,
            "n_days": len(x),
            "signal_start": expected_signal_start,
            "execution_start": execution_start,
            "execution_end": execution_end,
            "ann_excess": round(float(x.mean() * 238), 4),
            "ir": round(float(x.mean() / x.std(ddof=1) * np.sqrt(238)), 3),
            "max_drawdown": round(float((cum - np.maximum.accumulate(cum)).min()), 4),
            "positive_ratio": round(float((x > 0).mean()), 3),
        }
        print(f"[freq] freq={freq}: {results[str(freq)]}")

    out = VOL_ROOT / "freq_experiment"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "results.json").open("w") as f:
        _json.dump({
            "protocol": "continuous_account_board_aware_v3",
            "window": f"{eval_from}~{cal[-1]}",
            "market": market,
            "results": results,
        }, f, indent=2)
    vol.commit()
    return results



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
        "n_days": int(len(excess)),
        "ann_excess": round(float(x.mean() * 238), 4),
        "ir": round(float(x.mean() / x.std(ddof=1) * np.sqrt(238)), 3)
        if len(x) > 1 and x.std(ddof=1) > 0 else None,
        "max_drawdown_excess": round(float((cum - np.maximum.accumulate(cum)).min()), 4),
        "strategy_total_return_sum": round(float(ret.sum()), 4),
        "total_cost_sum": round(float(cost.sum()), 4),
        "mean_turnover": (
            round(float(rep[turnover_col].dropna().mean()), 6)
            if turnover_col and not rep[turnover_col].dropna().empty else None
        ),
        "turnover_source": turnover_col,
        "annual_excess": yearly,
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

    def ann(name):
        return results[name]["ann_excess"]

    attribution = {
        "REF_to_A_direction_semantics_pp": round(
            (ann("A_LEGACY_ORACLE_CURRENT_DIRECTION") - ann("REF_LEGACY_EXACT")) * 100, 2
        ),
        "A_to_B_same_day_close_oracle_pp": round(
            (ann("B_UNIFORM_OPEN_095") - ann("A_LEGACY_ORACLE_CURRENT_DIRECTION")) * 100, 2
        ),
        "B_to_C_board_rule_pp": round(
            (ann("C_BOARD_AWARE") - ann("B_UNIFORM_OPEN_095")) * 100, 2
        ),
        "C_to_D_high_open_5_pp": round(
            (ann("D_BOARD_AWARE_HIGH_OPEN_5") - ann("C_BOARD_AWARE")) * 100, 2
        ),
        "C_to_E_exclude_growth_boards_pp": round(
            (ann("E_BOARD_AWARE_NO_CHINEXT_STAR") - ann("C_BOARD_AWARE")) * 100, 2
        ),
    }

    payload = {
        "protocol": "execution_attribution_v1",
        "market": market,
        "freq": freq,
        "topk": topk,
        "n_drop": nd,
        "window": f"{execution_start}~{execution_end}",
        "signal_rows": int(len(signal)),
        "n_retrains": len(jobs),
        "results": results,
        "attribution_pp": attribution,
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
