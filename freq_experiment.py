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
        "cp /root/qlib/qlib_audit_fixes.py /root/qlib/qlib_live_retrain.py /root/",
    )
)

app = modal.App(APP_NAME, image=image)

YAML_PATH = "/root/qlib/examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158_csi500.yaml"
BENCH_BY_MARKET = {"csi1000": "SH000852", "csi500": "SH000905", "csi300": "SH000300"}


def _load_task(market: str) -> dict:
    """读官方 yaml 并打实验补丁（独立于主管道的 _load_and_patch_cfg）。"""
    from ruamel.yaml import YAML

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
    max_containers=8,
)
def freq_window(args: dict):
    """重训点 worker：训练（purge 边界）→ 预测并回测执行区间 [T+1, T']。"""
    import numpy as np
    import pandas as pd

    import qlib
    from qlib.backtest import backtest as normal_backtest
    from qlib.data.dataset import Dataset
    from qlib.model.base import Model
    from qlib.utils import init_instance_by_config

    from qlib_audit_fixes import last_matured_sample, purge_cfg_splits, read_trading_calendar

    qlib.init(**{**_load_task(args["market"])["qlib_init"], "skip_if_reg": True})  # 容器复用时防重复初始化
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
    seg["test"] = [args["eval_start"], args["eval_end"]]
    handler["start_time"] = "2015-01-01"
    handler["end_time"] = args["eval_end"]
    handler["fit_start_time"] = "2016-01-01"
    handler["fit_end_time"] = train_end
    purge_cfg_splits(cfg, cal, horizon=horizon)
    m = init_instance_by_config(cfg["task"]["model"], accept_types=Model)
    ds = init_instance_by_config(cfg["task"]["dataset"], accept_types=Dataset)
    m.fit(ds)
    pred = m.predict(ds, segment="test")
    if pred.empty:
        raise RuntimeError(f"empty predictions {args['eval_start']}~{args['eval_end']}")
    executor = {"class": "SimulatorExecutor", "module_path": "qlib.backtest.executor",
                "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True}}
    strategy = {"class": "TopkDropoutStrategy", "module_path": "qlib.contrib.strategy",
                "kwargs": {"signal": pred, "topk": args["topk"], "n_drop": args["nd"]}}
    pm, _ = normal_backtest(strategy=strategy, executor=executor,
                             start_time=args["eval_start"], end_time=args["eval_end"],
                             account=100000000, benchmark=BENCH_BY_MARKET[args["market"]],
                             exchange_kwargs={"limit_threshold": 0.095, "deal_price": "close",
                                              "open_cost": 0.0005, "close_cost": 0.0015, "min_cost": 5})
    rep = pm["1day"][0]
    excess = rep["return"] - rep["bench"] - rep["cost"]
    if excess.empty:
        raise RuntimeError("empty excess series")
    return {"freq": args["freq"], "eval_start": args["eval_start"],
            "daily": [round(float(v), 8) for v in excess.tolist()]}


@app.function(volumes={str(VOL_ROOT): vol}, cpu=4, memory=8192, timeout=8 * 3600)
def freq_driver(freqs="60,20", eval_from="2021-01-04", market="csi1000", topk=20, nd=2):
    """主函数：日历索引划分重训点（区间 [cal[i+1], cal[i+freq]] 无缝衔接）。"""
    import json as _json

    import numpy as np

    from qlib_audit_fixes import read_trading_calendar

    prepare.remote(force=True)
    cal = read_trading_calendar(DATA_DIR)
    start_i = bisect_left(cal, eval_from)
    results = {}
    for freq in [int(x) for x in freqs.split(",")]:
        jobs = []
        i = start_i
        while i < len(cal) - 1:
            jobs.append({"freq": freq, "retrain_asof": cal[i], "eval_start": cal[i + 1],
                         "eval_end": cal[min(i + freq, len(cal) - 1)], "market": market,
                         "topk": topk, "nd": nd})
            i += freq
        print(f"[freq] freq={freq}: {len(jobs)} 个重训点")
        outs = list(freq_window.map(jobs))
        daily, errs = [], 0
        for o in outs:
            if o.get("daily"):
                daily.extend(o["daily"])
            else:
                errs += 1
        x = np.array(daily)
        cum = np.cumsum(x)
        results[str(freq)] = {
            "n_retrains": len(jobs), "n_errors": errs, "n_days": len(x),
            "ann_excess": round(float(x.mean() * 238), 4),
            "ir": round(float(x.mean() / x.std(ddof=1) * np.sqrt(238)), 3),
            "max_drawdown": round(float((cum - np.maximum.accumulate(cum)).min()), 4),
            "positive_ratio": round(float((x > 0).mean()), 3),
        }
        print(f"[freq] freq={freq}: {results[str(freq)]}")
    out = VOL_ROOT / "freq_experiment"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "results.json").open("w") as f:
        _json.dump({"window": f"{eval_from}~{cal[-1]}", "market": market, "results": results}, f, indent=2)
    vol.commit()
    return results
