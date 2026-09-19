# Modal A10G 验证脚本：csi300 / Alpha158 日频 + ALSTM/GRU
#
# 用法（在仓库根目录执行，避免 `qlib/` 包被遮蔽）：
#   pip install modal
#   modal setup                                  # 登录
#   modal volume create qlib-cn-data             # 一次性
#   modal run modal_qlib_cn_a10g.py --model gru --smoke        # GPU冒烟，n_epochs=2
#   modal run modal_qlib_cn_a10g.py --model alstm --smoke
#   modal run modal_qlib_cn_a10g.py --model gru                # 全量 n_epochs=200/early_stop=10
#   modal run modal_qlib_cn_a10g.py --data-only                # 只下载/校验数据
#
# 设计要点：
# - qlib 的 pytorch 模型逻辑是 cuda:0 if available else cpu（见 qlib/contrib/model/pytorch_gru_ts.py），
#   同一份 yaml 本地 CPU 可跑，Modal 加 gpu="A10G" 自动加速，yaml 里 GPU:0 不用改。
# - 数据与 mlruns 都落到同一个 Volume 的不同子目录，避免容器销毁丢失。
# - 烟雾模式只缩 n_epochs/early_stop，不动数据区间，用于验证 CUDA + 数据链路。

import modal
from pathlib import Path
from qlib_audit_fixes import read_trading_calendar, purge_cfg_splits
from qlib_live_retrain import (configure_asof, should_retrain, cache_signature,
                               RETRAIN_EVERY_SESSIONS)

APP_NAME = "qlib-cn-daily-a10g"
VOL_NAME = "qlib-cn-data"
VOL_ROOT = Path("/vol")
DATA_DIR = VOL_ROOT / "cn_data"  # 对应 yaml 的 provider_uri
MLRUNS_DIR = VOL_ROOT / "mlruns"

vol = modal.Volume.from_name(VOL_NAME, create_if_missing=True)

# GPU 容器自带 CUDA driver，默认 PyPI 的 torch wheel 即含 CUDA，无需手动指定 index-url
# （官方示例：modal.Image.debian_slim().pip_install("torch") + gpu="A10G" 即可）。
# numpy 必须锁 1.26.x：qlib 的 Cython 扩展（qlib/data/_libs/*.pyx）与 numpy 2.x
# 头文件不兼容（PyDataType_TYPEOBJ 等宏在 numpy 2.3+ 被移除，构建期报错）。
# 这与官方 Dockerfile 用的 numpy 1.23.5 组合一致；pandas 锁 2.2.x 避免 pandas 3.0 破坏 qlib 的 groupby/fillna 兼容。
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("build-essential")
    # mlflow>=3.16 默认禁用 file:// 存储后端（maintenance mode），qlib 的
    # MLflowExpManager 用的就是 file:// uri，必须显式放行。
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
    # 把当前仓库（含本文件同目录的 qlib/ + examples/）打进镜像，并在构建期编译
    # qlib/data/_libs/{rolling,expanding}.pyx（否则 ImportError: qlib.data._libs）。
    # copy=True：后续还有 run_commands 步骤，必须直接拷入镜像而非运行时挂载。
    # ignore：排除本地构建产物（.cpp/.so 是本地 pip install 生成的，随 numpy 版本绑定，
    #         带过去会编译失败）、.venv、.git、mlruns。
    .add_local_dir(
        ".",
        remote_path="/root/qlib",
        copy=True,
        ignore=lambda path: (
            str(path).endswith((".cpp", ".so"))
            or any(part in str(path) for part in (".venv", "mlruns", "__pycache__"))
            or str(path).startswith(".git/")
            or "/.git/" in str(path)
            or str(path) == "modal_qlib_cn_a10g.py"
        ),
    )
    # --no-deps：上面已预装全部运行时依赖。若不带它，pip 会对 pyqlib 的未锁版本依赖
    # （如 numpy）重新解析并升级到 2.x，导致按 1.26 头文件编译的 Cython 扩展在运行时
    # 报 "numpy.core.multiarray failed to import"。非 editable 安装避免 namespace 歧义。
    .run_commands("cd /root/qlib && pip install . --no-build-isolation --no-deps")
    # 审计 PR 的 helper 模块随 add_local_dir 进了 /root/qlib/，但 Modal 入口脚本挂在
    # /root/ 运行（sys.path 首位是 /root）——必须复制到 /root/ 否则 ModuleNotFoundError。
    .run_commands("cp /root/qlib/qlib_audit_fixes.py /root/qlib/qlib_live_retrain.py /root/")
)

app = modal.App(APP_NAME, image=image)

CPU_COUNT = 8
MODEL_CFG = {
    "gru": "/root/qlib/examples/benchmarks/GRU/workflow_config_gru_Alpha158.yaml",
    "alstm": "/root/qlib/examples/benchmarks/ALSTM/workflow_config_alstm_Alpha158.yaml",
    "lgb360": "/root/qlib/examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha360_csi500.yaml",
    "lgb158": "/root/qlib/examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158_csi500.yaml",
}


def _ensure_data(force: bool = False):
    """确保 Volume 里有最新 A 股数据（chenditc/investment_data 每日 release，qlib 格式）。

    - 官方 qlib 数据源已停服且只到 2020 年；chenditc 每日更新到最近交易日。
    - marker 文件 .chenditc 存在且非 force 时跳过（避免每次重下 1-2GB）。
    - 部署前清空旧数据（2020 版），防止 features 格式混用。
    """
    import shutil
    import tarfile

    import requests

    marker = DATA_DIR / ".chenditc"
    if not force and marker.exists() and (DATA_DIR / "features").exists():
        print(f"[data] 已有 chenditc 数据（{marker.read_text().strip()}），跳过")
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    url = "https://github.com/chenditc/investment_data/releases/latest/download/qlib_bin.tar.gz"
    zip_path = Path("/tmp/chenditc_qlib_bin.tar.gz")
    extract_dir = Path("/tmp/chenditc_extract")

    print(f"[data] 下载 {url} ...")
    with requests.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        downloaded = 0
        with zip_path.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    print(f"\r[data] {downloaded / total * 100:.1f}% ({downloaded / 1e6:.0f}/{total / 1e6:.0f} MB)", end="")
    print(f"\n[data] 下载完成: {zip_path.stat().st_size / 1e6:.0f} MB")

    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True)
    with tarfile.open(zip_path, "r:gz") as tf:
        tf.extractall(extract_dir)
    # 兼容压缩包顶层是否套一层目录
    root = extract_dir
    if not (root / "features").exists():
        for sub in root.iterdir():
            if sub.is_dir() and (sub / "features").exists():
                root = sub
                break
    print(f"[data] 解压完成，内容: {sorted(p.name for p in root.iterdir() if p.is_dir())}")

    # 清空旧数据（features/calendars/instruments/缓存），避免两种格式混用
    # ⚠️ 这会连同 build_fund_factors 写入的基本面因子 bin 一起删除——
    #    force 更新后若需 --fund 模式，必须重跑 --build-fund
    if (DATA_DIR / "features").exists() and any(
        (p / "roe.day.bin").exists() for p in (DATA_DIR / "features").iterdir() if p.is_dir()
    ):
        print("[data] 警告：检测到基本面因子 bin，force 更新将一并清除，--fund 模式需重跑 --build-fund")
    for name in ["features", "calendars", "instruments", "features_cache", "dataset_cache"]:
        p = DATA_DIR / name
        if p.exists():
            shutil.rmtree(p)
            print(f"[data] 已删除旧 {name}")
    for name in ["features", "calendars", "instruments", "features_cache", "dataset_cache"]:
        src = root / name
        if src.exists():
            shutil.copytree(src, DATA_DIR / name)

    marker.write_text("chenditc daily release")
    cal = DATA_DIR / "calendars" / "day.txt"
    if cal.exists():
        lines = cal.read_text().strip().splitlines()
        print(f"[data] 日历覆盖: {lines[0]} ~ {lines[-1]}，共 {len(lines)} 个交易日")
    inst = DATA_DIR / "instruments"
    if inst.exists():
        print(f"[data] instruments: {sorted(p.name for p in inst.iterdir())}")


def _latest_trading_day(data_dir=None) -> str:
    """读取数据日历的最新交易日（YYYY-MM-DD）。data_dir=None 时用 Volume 路径；
    standalone 模式必须传容器本地解压目录（否则读到不存在的 Volume → 兜底旧日期）。"""
    cal_file = (data_dir or DATA_DIR) / "calendars" / "day.txt"
    if cal_file.exists():
        lines = cal_file.read_text().strip().splitlines()
        if lines:
            return lines[-1]
    return "2026-09-11"  # 兜底（仅当数据目录不可读时）


def _load_and_patch_cfg(yaml_path: str, smoke: bool, recent: bool = False, enhanced: bool = False, long_train: bool = False, fund: bool = False, label20: bool = False, label40: bool = False, label60: bool = False, rolling: bool = False, verify: bool = False, topk: int = None, market: str = None, nd: int = None, provider_dir: str = None) -> dict:
    """读 bundled yaml，打上 Modal 路径补丁。不改仓库原文件。

    recent=True 时把整套数据区间前移到 2026 年（训练 2021-2024 / 验证 2025 /
    回测 2026-01~今），并降换手（n_drop 5→2）+ 固定 seed，用于"预测未来"场景。
    enhanced=True 时：股票池 csi300→csi500（样本更多、QLib 评测 IC 普遍更高）、
    早停放宽 early_stop 10→30（让模型真正训完），benchmark 换 SH000905。
    long_train=True（需配合 recent）：训练区间延长到 2016-2024，覆盖完整牛熊周期。
    fund=True：handler 换成 Alpha158Fund（Alpha158 + 基本面因子 $roe 等，
    由 build_fund_factors 写入 bin），移除 FilterCol 以免过滤掉基本面字段。
    label20=True：标签换成 20 日收益。
    verify=True（需配合 recent）：walk-forward 验证模式——训练 2016-2023 /
    验证 2024 / 回测 2025-01~最新交易日（约 1.7 年），验证信号稳健性。
    topk：自定义策略持仓数（默认取 yaml 的 50）。
    """
    if sum(bool(x) for x in (label20, label40, label60)) > 1:
        raise ValueError("Choose exactly one prediction horizon")
    if (long_train or verify) and not recent:
        raise ValueError("long_train and verify require recent=True")
    from ruamel.yaml import YAML

    yaml = YAML(typ="safe", pure=True)
    with open(yaml_path) as f:
        cfg = yaml.load(f)

    # 1) 数据路径：默认 Volume；standalone 模式可传本地解压目录
    cfg.setdefault("qlib_init", {})["provider_uri"] = str(provider_dir) if provider_dir else str(DATA_DIR)
    cfg["qlib_init"]["region"] = "cn"
    # 2) mlflow 落到 Volume，否则容器退出即丢
    cfg["qlib_init"]["exp_manager"] = {
        "class": "MLflowExpManager",
        "module_path": "qlib.workflow.expm",
        "kwargs": {"uri": f"file:{MLRUNS_DIR}", "default_exp_name": "qlib-cn-daily"},
    }
    # 3) DataLoader 并发对齐 Modal cpu，避免 n_jobs=20 超配
    try:
        cfg["task"]["model"]["kwargs"]["n_jobs"] = CPU_COUNT
    except KeyError:
        pass
    # 4) 烟雾：只跑 2 个 epoch 验证 CUDA+数据链路
    if smoke:
        try:
            cfg["task"]["model"]["kwargs"]["n_epochs"] = 2
            cfg["task"]["model"]["kwargs"]["early_stop"] = 2
        except KeyError:
            pass
    # 5) 近期模式：训练/验证/回测全部前移到最新交易日（配合 chenditc 每日更新数据）
    if recent:
        END = _latest_trading_day(Path(provider_dir) if provider_dir else None)
        dh = cfg["task"]["dataset"]["kwargs"]["handler"]["kwargs"]
        train_start, fit_start = ("2016-01-01", "2016-01-01") if long_train else ("2021-01-01", "2021-01-01")
        dh["start_time"] = "2015-01-01" if long_train else "2020-01-01"  # 提前一年保证 Alpha158 60日窗口
        dh["end_time"] = END
        dh["fit_start_time"] = fit_start
        dh["fit_end_time"] = "2024-12-31"
        segments = cfg["task"]["dataset"]["kwargs"]["segments"]
        segments["train"] = [train_start, "2024-12-31"]
        segments["valid"] = ["2025-01-01", "2025-12-31"]
        segments["test"] = ["2026-01-01", END]
        bt = cfg["port_analysis_config"]["backtest"]
        bt["start_time"] = "2026-01-01"
        bt["end_time"] = END
        # n_drop=3：P0 网格与 vcheck(Alpha158) 双重确认最优（2/1 均显著更差）
        cfg["port_analysis_config"]["strategy"]["kwargs"]["n_drop"] = 3
        try:
            cfg["task"]["model"]["kwargs"]["seed"] = 0
        except KeyError:
            pass
        # 5b) walk-forward 验证模式：训练 2016-2023 / 验证 2024 / 回测 2025~最新（约 1.7 年）
        if verify:
            dh["start_time"] = "2015-01-01"
            dh["end_time"] = END
            dh["fit_start_time"] = "2016-01-01"
            dh["fit_end_time"] = "2023-12-31"
            segments["train"] = ["2016-01-01", "2023-12-31"]
            segments["valid"] = ["2024-01-01", "2024-12-31"]
            segments["test"] = ["2025-01-01", END]
            bt["start_time"] = "2025-01-01"
            bt["end_time"] = END
    # 自定义持仓数 / 换手 / 股票池（终审候选 csi1000 需要覆盖 yaml 默认的 csi500）
    if topk is not None:
        cfg["port_analysis_config"]["strategy"]["kwargs"]["topk"] = topk
    if nd is not None:
        cfg["port_analysis_config"]["strategy"]["kwargs"]["n_drop"] = nd
    if market is not None:
        dh = cfg["task"]["dataset"]["kwargs"]["handler"]["kwargs"]
        dh["instruments"] = market
        cfg["port_analysis_config"]["backtest"]["benchmark"] = "SH000852" if market == "csi1000" else "SH000905"
    # 6) 增强模式：csi500 + 早停放宽
    if enhanced:
        dh = cfg["task"]["dataset"]["kwargs"]["handler"]["kwargs"]
        dh["instruments"] = "csi500"
        cfg["market"] = "csi500"
        cfg["benchmark"] = "SH000905"
        cfg["port_analysis_config"]["backtest"]["benchmark"] = "SH000905"
        try:
            cfg["task"]["model"]["kwargs"]["early_stop"] = 30
        except KeyError:
            pass
    # Explicit market selection must win over --enhanced and update both benchmarks.
    if market is not None:
        _indices = {"csi300": "SH000300", "csi500": "SH000905", "csi1000": "SH000852"}
        if market not in _indices:
            raise ValueError(f"Unsupported market: {market}")
        cfg["task"]["dataset"]["kwargs"]["handler"]["kwargs"]["instruments"] = market
        cfg["market"] = market
        cfg["benchmark"] = _indices[market]
        cfg["port_analysis_config"]["backtest"]["benchmark"] = _indices[market]
    # 7) 基本面因子模式：Alpha158 + $roe 等 6 字段
    if fund:
        handler_cfg = cfg["task"]["dataset"]["kwargs"]["handler"]
        handler_cfg["class"] = "Alpha158Fund"
        handler_cfg["module_path"] = "fund_handler"
        import sys as _sys

        _sys.path.append("/root/qlib")  # fund_handler.py 位于仓库根
        dh = handler_cfg["kwargs"]
        dh["infer_processors"] = [p for p in dh.get("infer_processors", []) if p.get("class") != "FilterCol"]
    # 8) 20 日收益标签（与基本面因子的季度信息周期匹配）
    if label20:
        dh = cfg["task"]["dataset"]["kwargs"]["handler"]["kwargs"]
        dh["label"] = ["Ref($close, -20)/$close - 1"]
    # 8b) 40 日收益标签（P0-5 实测 IC 峰值在 40 日：0.137 vs 20 日 0.108）
    if label40:
        dh = cfg["task"]["dataset"]["kwargs"]["handler"]["kwargs"]
        dh["label"] = ["Ref($close, -40)/$close - 1"]
    # 8c) 60 日收益标签（更长期动量，用于多周期融合）
    if label60:
        dh = cfg["task"]["dataset"]["kwargs"]["handler"]["kwargs"]
        dh["label"] = ["Ref($close, -60)/$close - 1"]
    # 9) 低频调仓：PeriodicTopkStrategy（每 rebalance_days 个交易日调仓），匹配 20 日标签
    if rolling:
        import sys as _sys

        _sys.path.append("/root/qlib")  # rebalance_strategy.py 位于仓库根
        cfg["port_analysis_config"]["strategy"] = {
            "class": "PeriodicTopkStrategy",
            "module_path": "rebalance_strategy",
            "kwargs": {"signal": "<PRED>", "topk": topk if topk is not None else 50, "n_drop": nd if nd is not None else 2, "rebalance_days": 20},
        }
    # A sample's Ref(...,-h) label must mature before the NEXT stage starts.
    # This also purges the validation tail used by LightGBM early stopping.
    if recent:
        _cal = read_trading_calendar(provider_dir or DATA_DIR)
        purge_cfg_splits(cfg, _cal)
    if smoke and enhanced:
        _model_kw = cfg['task']['model']['kwargs']
        if 'early_stop' in _model_kw:
            _model_kw['early_stop'] = 2
    return cfg


@app.function(volumes={str(VOL_ROOT): vol}, cpu=4, memory=8192, timeout=1800)
def bench_years():
    """拉基准指数年度收益（用于把滚动超额换算成绝对收益图景）。"""
    import pandas as pd

    import qlib
    from qlib.data import D

    qlib.init(provider_uri=str(DATA_DIR), region="cn")
    df = D.features(["SH000905", "SH000852"], ["$close"], start_time="2021-01-01",
                    end_time=_latest_trading_day(), freq="day")
    out = {}
    for inst in ["SH000905", "SH000852"]:
        s = df.loc[:, "$close"].xs(inst, level=0 if df.index.names[0] == "instrument" else 1)
        if s.index.nlevels > 1:
            s = s.droplevel(-1)
        rets = {}
        for y in range(2021, 2027):
            sy = s[s.index.year == y]
            if len(sy) > 5:
                rets[str(y)] = round(float(sy.iloc[-1] / sy.iloc[0] - 1), 4)
        out[inst] = rets
    print(f"[bench] {out}")
    return out


@app.function(volumes={str(VOL_ROOT): vol}, cpu=8, memory=24576, timeout=2 * 3600)
def independent_recheck():
    """终审：完全独立实现的端到端复算（不经过 _load_and_patch_cfg / risk_analysis 等任何本文件公共代码）。
    目标窗口：csi1000 w09 (test 2023-01-01~2023-03-31)，管道报告 excess_total = -0.0163。
    手写全部配置：handler / processors / label / LGB 超参 / 回测参数 / 超额计算。"""
    import numpy as np
    import pandas as pd

    import qlib
    from qlib.backtest import backtest as normal_backtest
    from qlib.data.dataset import DatasetH
    from qlib.model.base import Model
    from qlib.utils import init_instance_by_config

    _ensure_data()
    qlib.init(provider_uri=str(DATA_DIR), region="cn",
              exp_manager={"class": "MLflowExpManager", "module_path": "qlib.workflow.expm",
                           "kwargs": {"uri": f"file:{MLRUNS_DIR}", "default_exp_name": "qlib-cn-recheck"}})

    # ---- 手写 task 配置（对照 lgb158 yaml + 修复后批次C窗口 w09 的语义）----
    task = {
        "model": {
            "class": "LGBModel",
            "module_path": "qlib.contrib.model.gbdt",
            "kwargs": {
                "loss": "mse",
                "colsample_bytree": 0.8879,
                "learning_rate": 0.0421,
                "subsample": 0.8789,
                "lambda_l1": 205.6999,
                "lambda_l2": 580.9768,
                "max_depth": 8,
                "num_leaves": 210,
                "num_threads": 8,
            },
        },
        "dataset": {
            "class": "DatasetH",
            "module_path": "qlib.data.dataset",
            "kwargs": {
                "handler": {
                    "class": "Alpha158",
                    "module_path": "qlib.contrib.data.handler",
                    "kwargs": {
                        "start_time": "2015-01-01",
                        "end_time": "2023-03-31",
                        "fit_start_time": "2016-01-01",
                        "fit_end_time": "2022-09-30",
                        "instruments": "csi1000",
                        "label": ["Ref($close, -20)/$close - 1"],
                        "infer_processors": [
                            {"class": "RobustZScoreNorm", "kwargs": {"fields_group": "feature", "clip_outlier": True}},
                            {"class": "Fillna", "kwargs": {"fields_group": "feature"}},
                        ],
                        "learn_processors": [
                            {"class": "DropnaLabel"},
                            {"class": "CSRankNorm", "kwargs": {"fields_group": "label"}},
                        ],
                    },
                },
                "segments": {
                    "train": ["2016-01-01", "2022-09-30"],
                    "valid": ["2022-10-01", "2022-12-31"],
                    "test": ["2023-01-01", "2023-03-31"],
                },
            },
        },
    }
    model: Model = init_instance_by_config(task["model"], accept_types=Model)
    dataset: DatasetH = init_instance_by_config(task["dataset"], accept_types=DatasetH)
    model.fit(dataset)
    pred = model.predict(dataset)

    executor = {"class": "SimulatorExecutor", "module_path": "qlib.backtest.executor",
                "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True}}
    strategy = {"class": "TopkDropoutStrategy", "module_path": "qlib.contrib.strategy",
                "kwargs": {"signal": pred, "topk": 20, "n_drop": 2}}
    pm, _ = normal_backtest(strategy=strategy, executor=executor,
                             start_time="2023-01-01", end_time="2023-03-31", account=100000000,
                             benchmark="SH000852",
                             exchange_kwargs={"limit_threshold": 0.095, "deal_price": "close",
                                              "open_cost": 0.0005, "close_cost": 0.0015, "min_cost": 5})
    rep = pm["1day"][0]
    # 手动计算有成本超额（不用 risk_analysis）
    excess = rep["return"] - rep["bench"] - rep["cost"]
    result = {"excess_total": round(float(excess.sum()), 4),
              "daily_mean": round(float(excess.mean()), 6),
              "n_days": int(len(excess))}
    print(f"[recheck] 独立实现: {result}")
    print(f"[recheck] 管道报告: {{'excess_total': -0.0163, 'daily_mean': -0.000277, 'n_days': 59}}")
    diff = abs(result["excess_total"] - (-0.0163))
    print(f"[recheck] 偏差: {diff:.4f} ({'✅ 一致' if diff < 0.005 else '❌ 需排查'})")
    return result


@app.function(volumes={str(VOL_ROOT): vol}, cpu=4, memory=8192, timeout=1800)
def verify_integrity():
    """资金安全复核：
    1) chenditc $change 是否为"当日涨幅"（close/前收-1）——决定回测涨跌停模拟是否正确；
    2) daily_signal 修复后的涨跌停过滤（Ref($close,1)）是否与 $change 口径一致；
    3) 最近 5 个交易日 csi500 内 |涨幅|≥9.5% 的股票数（验证过滤非空转）。"""
    import numpy as np
    import pandas as pd

    import qlib
    from qlib.data import D

    _ensure_data()
    qlib.init(provider_uri=str(DATA_DIR), region="cn")
    end = _latest_trading_day()
    start = (pd.Timestamp(end) - pd.Timedelta(days=10)).strftime("%Y-%m-%d")

    insts = D.instruments("csi500")
    inst_list = D.list_instruments(insts, start_time=start, end_time=end, as_list=True)
    print(f"[verify] csi500 区间内 {len(inst_list)} 只")

    df = D.features(inst_list, ["$close", "Ref($close,1)", "$change", "$open"],
                    start_time=start, end_time=end, freq="day")
    print(f"[verify] D.features 返回列: {list(df.columns)}")
    # 1) $change 定义验证
    chg_calc = df["$close"] / df["Ref($close,1)"] - 1
    merged = pd.concat([df["$change"], chg_calc.rename("calc")], axis=1).dropna()
    diff = (merged["$change"] - merged["calc"]).abs()
    print(f"[verify] $change vs close/prev_close-1：中位偏差={diff.median():.2e} "
          f"95分位={diff.quantile(0.95):.2e} 样本={len(merged)}")
    if diff.quantile(0.95) < 1e-3:
        # 实测中位偏差 2.4e-5（复权/舍入噪声级别）；关键判据是下方逐日涨跌停计数 100% 一致
        print("[verify] ✅ $change 即当日涨幅（偏差为复权舍入噪声），QLib 回测涨跌停模拟口径正确")
    else:
        print("[verify] ❌ $change 与当日涨幅不一致！回测涨跌停口径存疑，需人工检查")

    # 2) 修复后的过滤口径 vs $change 口径（D.features index 为 (instrument, datetime)，按日期需 groupby level=1）
    limit_cnt_ref = (chg_calc.abs() >= 0.095).groupby(level=1).sum()
    limit_cnt_chg = (df["$change"].abs() >= 0.095).groupby(level=1).sum()
    cmp = pd.concat([limit_cnt_ref.rename("ref"), limit_cnt_chg.rename("change")], axis=1).fillna(0)
    print("[verify] 每日 |涨幅|≥9.5% 股票数（Ref口径 vs $change口径）：")
    print(cmp.to_string())
    agree = (cmp["ref"] == cmp["change"]).mean()
    print(f"[verify] 两口径逐日计数一致率={agree:.1%}")

    # 3) 另外验证日内振幅口径（旧错误逻辑）的误报规模
    amp = (df["$close"] / df["$open"] - 1).abs()
    amp_cnt = (amp >= 0.095).groupby(level=1).sum()
    cmp2 = pd.concat([cmp["change"], amp_cnt.rename("close_open误报")], axis=1).fillna(0)
    print("[verify] 正确口径 vs 旧错误口径（close/open 会误剔除日内大幅震荡但未涨停的股票）：")
    print(cmp2.to_string())


@app.function(volumes={str(VOL_ROOT): vol}, cpu=4, memory=8192, timeout=1800)
def debug_data():
    """诊断 chenditc features 存储格式 + 验证 QLib 能读出字段（动态取最新 5 个交易日）。"""
    import pandas as pd
    import qlib
    from qlib.data import D

    qlib.init(provider_uri=str(DATA_DIR), region="cn")
    # 1) features 下某股票的存储形态
    for name in ["sh600000", "sz000001"]:
        p = DATA_DIR / "features" / name
        print(f"[debug] {name}: is_dir={p.is_dir()}")
        if p.is_dir():
            files = list(p.iterdir())[:6]
            for f in files:
                print(f"   {f.name} size={f.stat().st_size}")
        else:
            print(f"   file size={p.stat().st_size}")
    # 2) QLib 实际读取（用最新交易日动态验证；$roe 等因子仅在 --build-fund 后存在，不在此验证）
    end = _latest_trading_day()
    start = (pd.Timestamp(end) - pd.Timedelta(days=7)).strftime("%Y-%m-%d")

    df = D.features(["SH600000"], ["$close", "$volume"], start_time=start, end_time=end, freq="day")
    print(f"[debug] D.features shape={df.shape} columns={list(df.columns)}")
    print(df.head().to_string())


@app.function(volumes={str(VOL_ROOT): vol}, cpu=4, memory=8192, timeout=3600)
def prepare_data(force: bool = False, skip_health: bool = False):
    _ensure_data(force=force)
    if skip_health:
        # 研究批路径：健康检查为 5-10 分钟的全市场扫描，在 preemptible 容器上
        # 反复被平台抢占重启会耗尽 timeout；数据已就绪时训练回测不依赖它
        print("[data] skip_health=True，跳过健康检查（研究批路径）")
        vol.commit()
        return
    # 校验数据健康（对应 scripts/check_data_health.py 的核心调用）
    import subprocess

    r = subprocess.run(
        [
            "python",
            "/root/qlib/scripts/check_data_health.py",
            "check_data",
            "--qlib_dir",
            str(DATA_DIR),
        ],
        capture_output=True,
        text=True,
        cwd="/root/qlib",
    )
    print(r.stdout[-4000:])
    print(r.stderr[-2000:])
    if r.returncode != 0:
        raise RuntimeError(f"Data health subprocess failed: exit={r.returncode}")
    vol.commit()


@app.function(
    volumes={str(VOL_ROOT): vol},
    cpu=CPU_COUNT,
    memory=32768,
    gpu="A10G",
    timeout=4 * 3600,
)
def check_gpu():
    import torch

    import qlib

    print(f"qlib={getattr(qlib, '__version__', 'unknown')} file={qlib.__file__}")
    print(f"torch={torch.__version__} cuda_available={torch.cuda.is_available()}")
    if torch.cuda.is_available():
        print(f"gpu={torch.cuda.get_device_name(0)} capability={torch.cuda.get_device_capability(0)}")
    else:
        raise RuntimeError("A10G 未生效：torch.cuda.is_available() 为 False")


LGB_MODELS = {"lgb158", "lgb360"}


def _train_impl(model: str, smoke: bool, recent: bool, enhanced: bool, long_train: bool, fund: bool,
                label20: bool, rolling: bool, verify: bool, topk: int, experiment_name: str, market: str = None, nd: int = None):
    """train 共享实现（CPU/GPU 两个 wrapper 调用）。LGB 系模型无需 GPU。"""
    import qlib
    from qlib.model.trainer import task_train

    assert model in MODEL_CFG, f"model 须为 {list(MODEL_CFG)}"
    print(f"[train] {model} smoke={smoke} recent={recent} enhanced={enhanced} long_train={long_train} fund={fund} label20={label20} rolling={rolling} verify={verify} topk={topk}")

    _ensure_data()
    cfg = _load_and_patch_cfg(MODEL_CFG[model], smoke=smoke, recent=recent, enhanced=enhanced, long_train=long_train, fund=fund, label20=label20, rolling=rolling, verify=verify, topk=topk, market=market, nd=nd)
    print(f"[train] provider_uri={cfg['qlib_init']['provider_uri']} region={cfg['qlib_init']['region']}")

    qlib.init(**cfg["qlib_init"])
    recorder = task_train(cfg["task"], experiment_name=f"{experiment_name}-{model}")
    recorder.save_objects(config=cfg)
    print(f"[train] done recorder_id={recorder.info['id']} exp={experiment_name}-{model}")
    vol.commit()
    return {"recorder_id": recorder.info["id"], "model": model, "smoke": smoke}


@app.function(
    volumes={str(VOL_ROOT): vol},
    cpu=CPU_COUNT,
    memory=32768,
    gpu="A10G",
    timeout=4 * 3600,
)
def train(model: str = "gru", smoke: bool = True, recent: bool = False, enhanced: bool = False, long_train: bool = False, fund: bool = False, label20: bool = False, rolling: bool = False, verify: bool = False, topk: int = None, experiment_name: str = "qlib-cn-daily-a10g", market: str = None, nd: int = None):
    """GPU 版训练（GRU/ALSTM 等 RNN 模型）。"""
    import torch

    assert torch.cuda.is_available(), "GPU 未生效，先跑 check_gpu 排查 Image"
    return _train_impl(model, smoke, recent, enhanced, long_train, fund, label20, rolling, verify, topk, experiment_name, market, nd)


@app.function(
    volumes={str(VOL_ROOT): vol},
    cpu=CPU_COUNT,
    memory=32768,
    timeout=12 * 3600,
)
def train_cpu(model: str = "lgb158", smoke: bool = True, recent: bool = False, enhanced: bool = False, long_train: bool = False, fund: bool = False, label20: bool = False, rolling: bool = False, verify: bool = False, topk: int = None, experiment_name: str = "qlib-cn-daily-a10g", market: str = None, nd: int = None):
    """CPU 版训练（LGB/XGB/Linear 等非 GPU 模型），不挂载 GPU，避免浪费。"""
    assert model in LGB_MODELS, f"train_cpu 仅用于 CPU 模型 {LGB_MODELS}，{model} 请用 train"
    return _train_impl(model, smoke, recent, enhanced, long_train, fund, label20, rolling, verify, topk, experiment_name, market, nd)


@app.function(
    volumes={str(VOL_ROOT): vol},
    cpu=8,
    memory=16384,
    timeout=7200,
)
def build_fund_factors(market: str = "csi500", start_year: int = 2021, max_stocks: int = None):
    """akshare 拉财报+估值 → 追加写入 QLib bin（features/{symbol}/{field}.day.bin）。

    字段（每日向前填充）：
      pe_ttm / pb        —— 日频估值（akshare stock_zh_valuation_baidu）
      roe / gross_margin / rev_growth / profit_growth —— 季度财报
      （akshare stock_financial_analysis_indicator，报告期+4个月近似披露延迟后生效）
    """
    import bisect
    from concurrent.futures import ThreadPoolExecutor

    import akshare as ak
    import numpy as np
    import pandas as pd

    _ensure_data()
    cal = [pd.Timestamp(x) for x in pd.read_csv(DATA_DIR / "calendars" / "day.txt", header=None)[0]]
    inst = pd.read_csv(
        DATA_DIR / "instruments" / f"{market}.txt", sep="\t", header=None, names=["symbol", "start", "end"]
    )
    symbols = inst["symbol"].unique().tolist()  # instruments 文件是成分变更区间记录（每股票多行），需去重
    if max_stocks is not None:
        symbols = symbols[:max_stocks]
    print(f"[fund] {market} 共 {len(symbols)} 只，唯一 {len(set(symbols))} 只，前8只: {symbols[:8]}，开始拉取...")

    FUND_COLS = ["roe", "gross_margin", "rev_growth", "profit_growth", "pe_ttm", "pb"]

    fail_cnt = {"fin": 0, "pe": 0, "pb": 0}

    def fetch_one(sym: str):
        code = sym[2:]
        df = pd.DataFrame()
        try:
            # start_year 必须传字符串（akshare 内部会 .isdigit()）
            fin = ak.stock_financial_analysis_indicator(symbol=code, start_year=str(start_year))
            fin = fin[
                ["日期", "净资产收益率(%)", "销售毛利率(%)", "主营业务收入增长率(%)", "净利润增长率(%)"]
            ].rename(
                columns={
                    "日期": "date",
                    "净资产收益率(%)": "roe",
                    "销售毛利率(%)": "gross_margin",
                    "主营业务收入增长率(%)": "rev_growth",
                    "净利润增长率(%)": "profit_growth",
                }
            )
            fin["date"] = pd.to_datetime(fin["date"]) + pd.DateOffset(months=4)  # 近似披露延迟
            df = fin
        except Exception:
            fail_cnt["fin"] += 1
        for indicator, col in [("市盈率(TTM)", "pe_ttm"), ("市净率", "pb")]:
            try:
                v = ak.stock_zh_valuation_baidu(symbol=code, indicator=indicator, period="全部")
                v = v[["date", "value"]].rename(columns={"value": col})
                v["date"] = pd.to_datetime(v["date"])
                df = pd.concat([df, v[[col, "date"]]], ignore_index=True) if not df.empty else v[[col, "date"]]
            except Exception:
                fail_cnt["pe" if col == "pe_ttm" else "pb"] += 1
        return sym, df

    all_rows = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(fetch_one, sym): sym for sym in symbols}
        from concurrent.futures import as_completed

        print(f"[fund] futs 数量: {len(futs)}")
        for i, fut in enumerate(as_completed(futs)):
            try:
                sym, df = fut.result(timeout=120)
            except Exception:
                continue
            if not df.empty:
                df = df.assign(symbol=sym)  # 用返回的 sym，不依赖 dict 查表
                all_rows.append(df)
            if (i + 1) % 50 == 0:
                print(f"[fund] 已处理 {i + 1}/{len(symbols)}")
    print(f"[fund] 拉取完成：{len(all_rows)} 只有数据；失败 财报={fail_cnt['fin']} 市盈率={fail_cnt['pe']} 市净率={fail_cnt['pb']}")
    if not all_rows:
        raise RuntimeError("全部股票拉取失败，请检查 akshare 接口")
    df_all = pd.concat(all_rows, ignore_index=True)
    df_all["date"] = pd.to_datetime(df_all["date"])
    print(
        f"[fund] df_all: 行数={len(df_all)} symbol唯一数={df_all['symbol'].nunique()} "
        f"NaN数={df_all['symbol'].isna().sum()} "
        f"样例={list(df_all['symbol'].dropna().unique()[:8])}"
    )
    cal_np = np.array(cal, dtype="datetime64[ns]")
    dumped = 0
    # 清理上一版 bug 写出的无前缀目录（如 600004）
    for p in (DATA_DIR / "features").iterdir():
        if p.is_dir() and len(p.name) == 6 and p.name.isdigit():
            import shutil

            shutil.rmtree(p)
            print(f"[fund] 清理旧错误目录 {p.name}")
    n_stocks = 0
    for sym, sub in df_all.groupby("symbol"):
        code = sym.lower()  # QLib 目录约定：sh600000（与 chenditc 一致）
        sub = sub.set_index("date")
        series_map = {col: sub[col].dropna() for col in FUND_COLS if col in sub.columns}
        if not series_map:
            print(f"[fund] {sym} 无有效因子列: {list(sub.columns)} 行数={len(sub)}")
            continue
        n_stocks += 1
        feat_dir = DATA_DIR / "features" / code
        feat_dir.mkdir(parents=True, exist_ok=True)
        for col, s in series_map.items():
            # 生效日对齐到 >= 该日期的第一个交易日；超出日历末尾的丢弃
            s = s.sort_index()
            s = s[s.index <= cal[-1]]
            eff_idx = [bisect.bisect_left(cal_np, np.datetime64(t)) for t in s.index]
            s.index = pd.DatetimeIndex([cal[i] for i in eff_idx])
            # 在日历上向前填充
            s = s[~s.index.duplicated(keep="first")].reindex(cal).ffill().dropna()
            if s.empty:
                continue
            start_idx = bisect.bisect_left(cal_np, np.datetime64(s.index[0]))
            arr = np.hstack([start_idx, s.values.astype(np.float32)])
            (feat_dir / f"{col}.day.bin").write_bytes(arr.astype("<f").tobytes())
            dumped += 1
    print(f"[fund] dump 完成：{dumped} 个字段文件，{n_stocks} 只股票，写入 {DATA_DIR}/features")
    vol.commit()
    return {"fields_dumped": dumped, "stocks": n_stocks}


def _save_and_commit_signal(res: dict):
    """每日信号落地本地 results/signals/ 并 git 提交推送（paper trading 留痕）。
    容错：git 不可用/推送失败时仅警告，不阻断主流程。"""
    import subprocess
    from datetime import datetime

    fname = Path(res.get("csv", f"signals/{res['date']}_top{res['topk']}_lgb158.csv")).name
    local_dir = Path(__file__).resolve().parent / "results" / "signals"
    local_dir.mkdir(parents=True, exist_ok=True)
    local_path = local_dir / fname
    local_path.write_text(res["csv_content"])
    print(f"[signal] 已取回本地: {local_path}")

    repo = Path(__file__).resolve().parent
    if not (repo / ".git").exists():
        print("[signal] 非 git 仓库，跳过提交")
        return
    try:
        subprocess.run(["git", "add", str(local_path.relative_to(repo))], cwd=repo, check=True)
        # 同日重跑（数据未更新）时信号无变化，git commit 会因 nothing-to-commit 失败——优雅跳过
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=repo)
        if diff.returncode != 0:
            subprocess.run(["git", "commit", "-q", "-m",
                            f"chore(signal): paper-trading record {fname} ({datetime.now():%Y-%m-%d %H:%M})"],
                           cwd=repo, check=True)
        else:
            print("[signal] 信号与上次一致（同日重跑/数据未更新），无需重复提交")
            return
        msg = f"signal: {fname.replace('_top20_lgb158.csv','')} daily top20 (csi1000)"
        r = subprocess.run(["git", "push", "fork", "main"], cwd=repo, capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            print(f"[signal] 已提交并推送 GitHub: {msg}")
        else:
            print(f"[signal] ⚠️ 推送失败（本地提交已保存）: {r.stderr.strip()[:120]}")
    except Exception as e:
        print(f"[signal] ⚠️ git 操作失败（信号文件已保存本地）: {e}")


def _daily_impl(model: str, topk: int, predict_date: str, enhanced: bool, long_train: bool, fund: bool, label20: bool, market: str = None, nd: int = None):
    """每日信号共享实现（CPU/GPU 两个 wrapper 调用）。LGB 系模型无需 GPU。"""
    import pandas as pd

    import qlib
    from qlib.data.dataset import Dataset
    from qlib.model.base import Model
    from qlib.utils import init_instance_by_config

    assert model in MODEL_CFG, f"model 须为 {list(MODEL_CFG)}"
    _ensure_data()
    cfg = _load_and_patch_cfg(MODEL_CFG[model], smoke=False, recent=True, enhanced=enhanced, long_train=long_train, fund=fund, label20=label20, market=market, nd=nd)
    qlib.init(**cfg["qlib_init"])
    task = cfg["task"]

    model_obj = init_instance_by_config(task["model"], accept_types=Model)
    dataset = init_instance_by_config(task["dataset"], accept_types=Dataset)
    model_obj.fit(dataset)

    pred = model_obj.predict(dataset)
    if predict_date is None:
        predict_date = pred.index.get_level_values(0).max()
    day = pred.loc[predict_date].dropna()
    top = day.sort_values(ascending=False).head(topk)

    # 过滤当日已涨/跌停（|当日涨幅|≥9.5%，涨幅= close/前收-1）：
    # 涨停买不进、跌停卖不出，剔除避免给不可交易信号
    from qlib.data import D

    day_df = D.features(
        [str(x) for x in day.index],
        ["$close", "Ref($close,1)"],
        start_time=predict_date,
        end_time=predict_date,
        freq="day",
    )
    if len(day_df) > 0 and ("Ref($close,1)" in day_df.columns):
        day_ret = (day_df["$close"] / day_df["Ref($close,1)"] - 1).dropna()
        # D.features 返回 (instrument, datetime) 双层 index，day.index 是 instrument 单层：
        # 必须先取 instrument 层再 isin，否则永远匹配不上（过滤静默失效）
        limited = day_ret[(day_ret >= 0.095) | (day_ret <= -0.095)].index.get_level_values(0)
        tradable = day.index[~day.index.isin(limited)]
        n_removed = len(day) - len(tradable)
        day = day.loc[tradable]
        top = day.sort_values(ascending=False).head(topk)
        print(f"[signal] 已剔除 {n_removed} 只涨/跌停股")

    horizon = "未来20日" if label20 else "未来2日"
    print(f"[signal] {predict_date} top{topk}（分数={horizon}收益率预测，越高越看好）:")
    for rank, (inst, score) in enumerate(top.items(), 1):
        print(f"  {rank:>2}. {inst}  score={score:.4f}")

    out = VOL_ROOT / "signals"
    out.mkdir(parents=True, exist_ok=True)
    csv = out / f"{str(predict_date)[:10]}_top{topk}_{model}.csv"
    pd.DataFrame(
        {"rank": range(1, len(top) + 1), "instrument": top.index, "score": top.values}
    ).to_csv(csv, index=False)
    print(f"[signal] 已保存 {csv}")
    csv_content = csv.read_text()
    vol.commit()
    return {"date": str(predict_date), "topk": topk, "csv": str(csv), "n_stocks": len(top),
            "csv_content": csv_content}


@app.function(
    volumes={str(VOL_ROOT): vol},
    cpu=CPU_COUNT,
    memory=32768,
    gpu="A10G",
    timeout=8 * 3600,
)
def daily_signal(model: str = "gru", topk: int = 50, predict_date: str = None, enhanced: bool = False, long_train: bool = False, fund: bool = False, label20: bool = False, market: str = None, nd: int = None):
    """GPU 版每日信号（GRU/ALSTM 等 RNN 模型）。"""
    import torch

    assert torch.cuda.is_available(), "GPU 未生效，先跑 check_gpu 排查 Image"
    return _daily_impl(model, topk, predict_date, enhanced, long_train, fund, label20, market, nd)


@app.function(
    volumes={str(VOL_ROOT): vol},
    cpu=CPU_COUNT,
    memory=32768,
    timeout=12 * 3600,
)
def daily_signal_cpu(model: str = "lgb158", topk: int = 50, predict_date: str = None, enhanced: bool = False, long_train: bool = False, fund: bool = False, label20: bool = False, market: str = None, nd: int = None):
    """CPU 版每日信号（LGB 等非 GPU 模型），不挂载 GPU，避免浪费。"""
    assert model in LGB_MODELS, f"daily_signal_cpu 仅用于 CPU 模型 {LGB_MODELS}，{model} 请用 daily_signal"
    return _daily_impl(model, topk, predict_date, enhanced, long_train, fund, label20, market, nd)


@app.function(
    volumes={str(VOL_ROOT): vol},
    cpu=CPU_COUNT,
    memory=32768,
    gpu="A10G",
    timeout=12 * 3600,
)
def train_ensemble(topk: int = 50, n_drop: int = 2):
    """三模型集成：LGB + GRU + ALSTM 各自训练（Alpha158 + 20日标签 + long_train），
    预测分数 z-score 标准化后平均，作为信号跑回测（TopkDropout，每日微调）。"""
    import numpy as np
    import pandas as pd

    import qlib
    import torch
    from qlib.backtest import backtest as normal_backtest
    from qlib.contrib.evaluate import risk_analysis
    from qlib.data.dataset import Dataset
    from qlib.model.base import Model
    from qlib.utils import init_instance_by_config

    assert torch.cuda.is_available(), "GPU 未生效"
    _ensure_data()
    qlib.init(provider_uri=str(DATA_DIR), region="cn",
              exp_manager={"class": "MLflowExpManager", "module_path": "qlib.workflow.expm",
                           "kwargs": {"uri": f"file:{MLRUNS_DIR}", "default_exp_name": "qlib-cn-ensemble"}})

    preds = {}
    for name, yaml_key in [("lgb", "lgb158"), ("gru", "gru"), ("alstm", "alstm")]:
        # enhanced=True 统一 csi500 股票池（GRU/ALSTM yaml 默认 csi300）
        cfg = _load_and_patch_cfg(MODEL_CFG[yaml_key], smoke=False, recent=True, long_train=True, label20=True, enhanced=True)
        print(f"[ens] 训练 {name} (market=csi500) ...")
        model = init_instance_by_config(cfg["task"]["model"], accept_types=Model)
        dataset = init_instance_by_config(cfg["task"]["dataset"], accept_types=Dataset)
        model.fit(dataset)
        p = model.predict(dataset)
        # 归一化 index：确保第 0 层是 datetime（与 LGB/GRU 顺序可能不同）
        if isinstance(p.index, pd.MultiIndex) and not pd.api.types.is_datetime64_any_dtype(p.index.levels[0]):
            p = p.swaplevel()
        p.index = p.index.set_names(["datetime", "instrument"])
        p = (p - p.mean()) / p.std()
        preds[name] = p
        print(f"[ens] {name} pred 覆盖 {p.index.get_level_values(0).min()} ~ {p.index.get_level_values(0).max()} 共 {len(p)} 行")

    # 对齐到共同 index（取三个 pred 的交集日期），分数平均
    common_idx = preds["lgb"].index
    for name, p in preds.items():
        common_idx = common_idx.intersection(p.index)
    ens = pd.concat({k: v.reindex(common_idx) for k, v in preds.items()}, axis=1).mean(axis=1)
    ens = ens.sort_index()
    print(f"[ens] 集成信号：{len(ens)} 行，{ens.index.get_level_values(0).nunique()} 个交易日")

    # 回测（TopkDropout 每日微调，与 20 日标签最优配置一致）
    bt = {
        "start_time": "2026-01-01",
        "end_time": _latest_trading_day(),
        "account": 100000000,
        "benchmark": "SH000905",
        "exchange_kwargs": {"limit_threshold": 0.095, "deal_price": "close", "open_cost": 0.0005, "close_cost": 0.0015, "min_cost": 5},
    }
    strategy = {
        "class": "TopkDropoutStrategy",
        "module_path": "qlib.contrib.strategy",
        "kwargs": {"signal": ens, "topk": topk, "n_drop": n_drop},
    }
    executor = {
        "class": "SimulatorExecutor",
        "module_path": "qlib.backtest.executor",
        "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True},
    }
    portfolio_metric_dict, indicator_dict = normal_backtest(
        strategy=strategy, executor=executor, **bt
    )
    for _freq, (report, _pos) in portfolio_metric_dict.items():
        print(f"[ens] === {_freq} 回测结果 ===")
        print(f"[ens] 策略收益:\n{risk_analysis(report['return']).to_string()}")
        print(f"[ens] 无成本超额:\n{risk_analysis(report['return'] - report['bench']).to_string()}")
        print(f"[ens] 有成本超额:\n{risk_analysis(report['return'] - report['bench'] - report['cost']).to_string()}")
    ens.name = "ensemble_signal"
    out = VOL_ROOT / "signals"
    out.mkdir(parents=True, exist_ok=True)
    latest = ens.index.get_level_values(0).max()
    day = ens.loc[latest].dropna().sort_values(ascending=False).head(topk)
    csv = out / f"{str(latest)[:10]}_top{topk}_ensemble.csv"
    pd.DataFrame({"rank": range(1, len(day) + 1), "instrument": day.index, "score": day.values}).to_csv(
        csv, index=False
    )
    print(f"[ens] 最新信号 {latest} top{topk} 已保存 {csv}")
    vol.commit()
    return {"signal_rows": len(ens), "latest_date": str(latest), "csv": str(csv)}


LGB_PARAM_SPACE = {
    "learning_rate": [0.02, 0.05, 0.08, 0.12],
    "num_leaves": [50, 100, 200, 300],
    "max_depth": [4, 6, 8, 10],
    "colsample_bytree": [0.6, 0.8, 1.0],
    "subsample": [0.7, 0.9, 1.0],
    "lambda_l1": [0.0, 10.0, 100.0],
    "lambda_l2": [0.0, 10.0, 100.0],
}


TUNE_WORKERS = 4  # 普通 modal run 模式（本地保持连接驱动 map），4 并发稳妥可控


@app.function(
    volumes={str(VOL_ROOT): vol},
    cpu=16,
    memory=16384,
    timeout=2 * 3600,
    max_containers=TUNE_WORKERS,
)
def tune_one(params: dict, horizon: int = 20):
    """给定一组 LGB 超参，训练（Alpha360 + N日标签 + long_train）并返回 test 段 Rank IC。
    供 --tune 并行搜索使用。LGB 是 CPU 模型，无需 GPU；max_containers=TUNE_WORKERS 限制并行数。
    容器会被 map 复用（一组容器跑多组参数），因此 init 用 skip_if_reg 防重复注册，
    Rank IC 用 dataset.prepare(DK_L) 拿 label——与 LGB 训练同路径；
    不要用 D.features（容器 fork 后会触发 LocalDatasetProvider 的
    inst_processors 参数冲突 TypeError）。"""
    import numpy as np
    import pandas as pd

    import qlib
    from qlib.data.dataset import Dataset
    from qlib.model.base import Model
    from qlib.utils import init_instance_by_config

    if horizon != 20:
        raise ValueError("Production hyperparameter tuning is restricted to the 20-day Alpha158 target")
    _ensure_data()
    cfg = _load_and_patch_cfg(
        MODEL_CFG["lgb158"], smoke=False, recent=True, long_train=True,
        label20=True, market="csi1000", topk=20, nd=2
    )
    cfg["task"]["model"]["kwargs"].update(params)
    qlib.init(
        provider_uri=str(DATA_DIR), region="cn", skip_if_reg=True,
        exp_manager={"class": "MLflowExpManager", "module_path": "qlib.workflow.expm",
                     "kwargs": {"uri": f"file:{MLRUNS_DIR}", "default_exp_name": "qlib-cn-tune"}},
    )
    model = init_instance_by_config(cfg["task"]["model"], accept_types=Model)
    dataset = init_instance_by_config(cfg["task"]["dataset"], accept_types=Dataset)
    model.fit(dataset)
    pred = model.predict(dataset, segment="valid")
    if pred.empty:
        raise RuntimeError("Empty validation predictions in hyperparameter search")
    # 用 dataset.prepare 拿 label（与 LGB 训练同路径 DK_L，可靠）；不要用 D.features——
    # 容器 fork 后会触发 LocalDatasetProvider 的 inst_processors 参数冲突 TypeError
    from qlib.data.dataset.handler import DataHandlerLP

    label_df = dataset.prepare("valid", col_set=["feature", "label"], data_key=DataHandlerLP.DK_L)
    if isinstance(label_df, pd.DataFrame) and "label" in label_df.columns:
        label = label_df["label"]
        if isinstance(label, pd.DataFrame):
            label = label.iloc[:, 0]
    else:
        label = label_df.iloc[:, -1]
    df = pd.concat([pred.rename("pred"), label.rename("label")], axis=1).dropna()
    ic = df.groupby(level=0).apply(
        lambda g: g["pred"].rank().corr(g["label"].rank()) if len(g) > 10 else np.nan
    )
    rank_ic = float(ic.dropna().mean())
    # Portfolio objective must match the actual daily strategy, not merely IC.
    # Backtest ONLY on validation dates; test remains untouched for evaluation.
    from qlib.backtest import backtest as normal_backtest
    strategy = {"class": "TopkDropoutStrategy", "module_path": "qlib.contrib.strategy",
                "kwargs": {"signal": pred, "topk": 20, "n_drop": 2}}
    executor = {"class": "SimulatorExecutor", "module_path": "qlib.backtest.executor",
                "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True}}
    valid_start, valid_end = cfg["task"]["dataset"]["kwargs"]["segments"]["valid"]
    pm, _ = normal_backtest(
        strategy=strategy, executor=executor, start_time=valid_start, end_time=valid_end,
        account=100000000, benchmark="SH000852",
        exchange_kwargs={"limit_threshold": 0.095, "deal_price": "close",
                         "open_cost": 0.0005, "close_cost": 0.0015, "min_cost": 5})
    report = pm["1day"][0]
    if report.empty:
        raise RuntimeError("Empty validation portfolio report")
    excess = report["return"] - report["bench"] - report["cost"]
    if not bool(np.isfinite(excess.to_numpy()).all()):
        raise RuntimeError("Non-finite validation net excess")
    net_ann = float(excess.mean() * 238)
    print(f"[tune] params={params} validation_net_annual={net_ann:.4f} rank_ic={rank_ic:.4f}")
    return {"rank_ic": rank_ic, "excess_with_cost_annual": net_ann, "params": params}


def _sample_params(rng):
    return {k: rng.choice(v) for k, v in LGB_PARAM_SPACE.items()}


@app.function(
    volumes={str(VOL_ROOT): vol},
    cpu=4,
    memory=8192,
    timeout=12 * 3600,
)
def tune_driver(n_trials: int = 40, horizon: int = 20):
    """云端超参搜索入口：内部并行 map N 组，Top10 与最优参数保存到 Volume（供 main 分支无 /vol 写权限时用）。
    用法：modal run modal_qlib_cn_a10g.py::tune_driver
    ⚠️ 用普通 modal run 保持本地连接；不要 --detach（本地断开后平台会取消未完成的输入，
    已被实证：两次 detach 均在约 10 分钟后输入被取消）。"""
    import json as _json

    _ensure_data()
    rng = __import__("random").Random(42)
    if n_trials < 1:
        raise ValueError("n_trials must be positive")
    params_list = [{}] + [_sample_params(rng) for _ in range(n_trials - 1)]
    print(f"[tune] 开始 {n_trials} 组搜索（{TUNE_WORKERS} 并发 CPU 容器，horizon={horizon}）...")
    results = list(tune_one.map([dict(p) for p in params_list], [horizon] * n_trials))
    results.sort(key=lambda r: r["excess_with_cost_annual"], reverse=True)
    tuning_dir = VOL_ROOT / "tuning"
    tuning_dir.mkdir(parents=True, exist_ok=True)
    with (tuning_dir / f"top10_h{horizon}.json").open("w") as f:
        _json.dump(results[:10], f, indent=2)
    with (tuning_dir / f"best_params_h{horizon}.json").open("w") as f:
        _json.dump({"rank_ic": results[0]["rank_ic"],
                    "excess_with_cost_annual": results[0]["excess_with_cost_annual"],
                    "params": results[0]["params"], "horizon": horizon,
                    "model": "lgb158", "market": "csi1000", "topk": 20, "n_drop": 2,
                    "warning": "validation-selected candidate; independent OOS required before adoption"}, f, indent=2)
    print(f"[tune] === Top 10（共 {n_trials} 组）===")
    for i, r in enumerate(results[:10], 1):
        print(f"[tune] {i}. net_annual={r['excess_with_cost_annual']:.4f} "
              f"rank_ic={r['rank_ic']:.4f} {r['params']}")
    print(f"[tune] 结果已保存 {tuning_dir}")
    vol.commit()
    return results[:10]


@app.function(
    volumes={str(VOL_ROOT): vol},
    cpu=CPU_COUNT,
    memory=32768,
    timeout=12 * 3600,
)
def dual_horizon(topk: int = 50, n_drop: int = 2, best_params: dict = None):
    """多周期融合：LGB+Alpha360 的 20日标签模型 与 60日标签模型 分数平均。
    best_params 可用超参搜索结果覆盖模型超参。纯 LGB，CPU 容器即可。"""
    import numpy as np
    import pandas as pd

    import qlib
    from qlib.backtest import backtest as normal_backtest
    from qlib.contrib.evaluate import risk_analysis
    from qlib.data.dataset import Dataset
    from qlib.model.base import Model
    from qlib.utils import init_instance_by_config

    _ensure_data()
    qlib.init(provider_uri=str(DATA_DIR), region="cn",
              exp_manager={"class": "MLflowExpManager", "module_path": "qlib.workflow.expm",
                           "kwargs": {"uri": f"file:{MLRUNS_DIR}", "default_exp_name": "qlib-cn-dual"}})

    preds = {}
    for name, horizon in [("h20", 20), ("h60", 60)]:
        cfg = _load_and_patch_cfg(
            MODEL_CFG["lgb360"], smoke=False, recent=True, long_train=True,
            label20=horizon == 20, label60=horizon == 60,
        )
        if best_params:
            cfg["task"]["model"]["kwargs"].update(best_params)
        print(f"[dual] 训练 {name}（{horizon}日标签）...")
        model = init_instance_by_config(cfg["task"]["model"], accept_types=Model)
        dataset = init_instance_by_config(cfg["task"]["dataset"], accept_types=Dataset)
        model.fit(dataset)
        p = model.predict(dataset)
        p = (p - p.mean()) / p.std()
        preds[name] = p
        print(f"[dual] {name} pred {len(p)} 行")

    common_idx = preds["h20"].index.intersection(preds["h60"].index)
    ens = pd.concat({k: v.reindex(common_idx) for k, v in preds.items()}, axis=1).mean(axis=1)
    ens = ens.sort_index()
    print(f"[dual] 融合信号：{len(ens)} 行，{ens.index.get_level_values(0).nunique()} 个交易日")

    bt = {
        "start_time": "2026-01-01",
        "end_time": _latest_trading_day(),
        "account": 100000000,
        "benchmark": "SH000905",
        "exchange_kwargs": {"limit_threshold": 0.095, "deal_price": "close", "open_cost": 0.0005, "close_cost": 0.0015, "min_cost": 5},
    }
    strategy = {
        "class": "TopkDropoutStrategy",
        "module_path": "qlib.contrib.strategy",
        "kwargs": {"signal": ens, "topk": topk, "n_drop": n_drop},
    }
    executor = {
        "class": "SimulatorExecutor",
        "module_path": "qlib.backtest.executor",
        "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True},
    }
    portfolio_metric_dict, _ = normal_backtest(strategy=strategy, executor=executor, **bt)
    for _freq, (report, _pos) in portfolio_metric_dict.items():
        print(f"[dual] === {_freq} 回测 ===")
        print(f"[dual] 策略收益:\n{risk_analysis(report['return']).to_string()}")
        print(f"[dual] 无成本超额:\n{risk_analysis(report['return'] - report['bench']).to_string()}")
        print(f"[dual] 有成本超额:\n{risk_analysis(report['return'] - report['bench'] - report['cost']).to_string()}")

    out = VOL_ROOT / "signals"
    out.mkdir(parents=True, exist_ok=True)
    latest = ens.index.get_level_values(0).max()
    day = ens.loc[latest].dropna().sort_values(ascending=False).head(topk)
    csv = out / f"{str(latest)[:10]}_top{topk}_dual.csv"
    pd.DataFrame({"rank": range(1, len(day) + 1), "instrument": day.index, "score": day.values}).to_csv(
        csv, index=False
    )
    print(f"[dual] 最新信号已保存 {csv}")
    vol.commit()
    return {"latest_date": str(latest), "csv": str(csv)}


@app.function(
    volumes={str(VOL_ROOT): vol},
    cpu=CPU_COUNT,
    memory=32768,
    timeout=4 * 3600,
)
def p0_diagnostics():
    """P0 证据补强（训练 1 次，四项分析复用同一 pred）：
    - P0-3 分组单调性：每日按预测分 10 组的 20 日真实收益是否单调递增
    - P0-5 IC 衰减曲线：未来 1/5/10/20/40/60 日的 Rank IC
    - P0-4 分月归因：月度有成本超额分布（是否集中于个别月份）
    - P0-1 n_drop 网格：1/2/3/5 换手参数的有成本超额对比
    结果全部写入 /vol/p0_results/ 并 vol.commit()，供取回本地。
    配置：lgb360 + long_train + label20（固化最优配置）。"""
    import json

    import numpy as np
    import pandas as pd

    import qlib
    from qlib.backtest import backtest as normal_backtest
    from qlib.contrib.evaluate import risk_analysis
    from qlib.data import D
    from qlib.data.dataset import Dataset
    from qlib.model.base import Model
    from qlib.utils import init_instance_by_config

    _ensure_data()
    END = _latest_trading_day()
    cfg = _load_and_patch_cfg(MODEL_CFG["lgb360"], smoke=False, recent=True, long_train=True, label20=True)
    qlib.init(provider_uri=str(DATA_DIR), region="cn",
              exp_manager={"class": "MLflowExpManager", "module_path": "qlib.workflow.expm",
                           "kwargs": {"uri": f"file:{MLRUNS_DIR}", "default_exp_name": "qlib-cn-p0"}})

    # ---------- 训练一次，取全 test 段预测 ----------
    model = init_instance_by_config(cfg["task"]["model"], accept_types=Model)
    dataset = init_instance_by_config(cfg["task"]["dataset"], accept_types=Dataset)
    model.fit(dataset)
    pred = model.predict(dataset)  # index (datetime, instrument)
    print(f"[p0] pred {len(pred)} 行，{pred.index.get_level_values(0).nunique()} 个交易日")

    # ---------- 拉收盘价，计算多周期前向收益（P0-3/5 共用） ----------
    insts = sorted(set(pred.index.get_level_values(1)))
    close = D.features(insts, ["$close"], start_time=pred.index.get_level_values(0).min(),
                       end_time=END, freq="day")["$close"]
    # D.features 返回 (instrument, datetime)；换到与 pred 一致的 (datetime, instrument)
    close = close.swaplevel().sort_index()
    assert not close.reindex(pred.index).isna().all(), "close 与 pred 索引对齐失败"

    results = {}

    # ---------- P0-5 IC 衰减曲线 ----------
    ic_rows = {}
    for h in [1, 5, 10, 20, 40, 60]:
        fwd = close.groupby(level=1).shift(-h) / close - 1
        label = fwd.reindex(pred.index)
        df = pd.concat([pred.rename("pred"), label.rename("fwd")], axis=1).dropna()
        if df.empty or df.index.get_level_values(0).nunique() < 30:
            ic_rows[h] = None
            continue
        ic = df.groupby(level=0).apply(
            lambda g: g["pred"].rank().corr(g["fwd"].rank()) if len(g) > 10 else np.nan
        ).dropna()
        ic_rows[h] = {"rank_ic": round(float(ic.mean()), 4),
                      "icir": round(float(ic.mean() / ic.std() * np.sqrt(len(ic))), 3) if ic.std() > 0 else None,
                      "n_days": int(len(ic))}
    results["ic_decay"] = ic_rows
    print(f"[p0] P0-5 IC 衰减: {ic_rows}")

    # ---------- P0-3 分组单调性（每日截面 10 分组，20 日真实收益） ----------
    fwd20 = close.groupby(level=1).shift(-20) / close - 1
    df = pd.concat([pred.rename("pred"), fwd20.reindex(pred.index).rename("fwd")], axis=1).dropna()
    # 逐日按预测分 10 组（rank(method='first') 保证并列分数均匀分配）
    df["group"] = df.groupby(level=0)["pred"].transform(
        lambda x: pd.qcut(x.rank(method="first"), 10, labels=False)
    )
    grp_mean = df.groupby("group")["fwd"].mean()  # 组号 0=最差 ~ 9=最好
    grp_std = df.groupby("group")["fwd"].std()
    # 分组序号即 rank，Pearson=Spearman；>0.9 视为单调
    mono = float(np.corrcoef(range(10), grp_mean.values)[0, 1])
    top_minus_bottom = float(grp_mean.iloc[-1] - grp_mean.iloc[0])
    results["group_monotonicity"] = {
        "group_mean_fwd20": {int(k): round(v, 5) for k, v in grp_mean.items()},
        "group_std_fwd20": {int(k): round(v, 5) for k, v in grp_std.items()},
        "spearman_groups_vs_return": round(mono, 4),
        "top_minus_bottom_20d": round(top_minus_bottom, 5),
        "verdict": "MONOTONIC" if mono > 0.9 else ("PARTIAL" if mono > 0.6 else "NON-MONOTONIC"),
    }
    print(f"[p0] P0-3 分组收益(组0最差~组9最好): {results['group_monotonicity']['group_mean_fwd20']}")
    print(f"[p0] P0-3 单调性 spearman={mono:.4f} 判定={results['group_monotonicity']['verdict']}")

    # ---------- 回测设置（P0-1/4 共用） ----------
    bt_kwargs = dict(start_time="2026-01-01", end_time=END, account=100000000, benchmark="SH000905",
                     exchange_kwargs={"limit_threshold": 0.095, "deal_price": "close",
                                      "open_cost": 0.0005, "close_cost": 0.0015, "min_cost": 5})
    executor = {"class": "SimulatorExecutor", "module_path": "qlib.backtest.executor",
                "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True}}

    # ---------- P0-1 n_drop 网格 ----------
    ndrop_rows = {}
    report_nd2 = None
    for nd in [1, 2, 3, 5]:
        strategy = {"class": "TopkDropoutStrategy", "module_path": "qlib.contrib.strategy",
                    "kwargs": {"signal": pred, "topk": 50, "n_drop": nd}}
        pm, _ = normal_backtest(strategy=strategy, executor=executor, **bt_kwargs)
        rep = pm["1day"][0]
        if nd == 2:
            report_nd2 = rep  # 留给 P0-4 分月归因
        ra = risk_analysis(rep["return"] - rep["bench"] - rep["cost"])
        ndrop_rows[nd] = {"excess_with_cost_annual": round(float(ra.loc["annualized_return", "risk"]), 4),
                          "ir": round(float(ra.loc["information_ratio", "risk"]), 3),
                          "max_drawdown": round(float(ra.loc["max_drawdown", "risk"]), 4)}
        print(f"[p0] P0-1 n_drop={nd}: {ndrop_rows[nd]}")
    results["ndrop_grid"] = ndrop_rows

    # ---------- P0-4 分月归因（n_drop=2 基准） ----------
    excess = report_nd2["return"] - report_nd2["bench"] - report_nd2["cost"]
    monthly = excess.groupby(excess.index.to_period("M")).agg(["mean", "sum", "count"])
    monthly.columns = ["daily_mean_excess", "cum_excess", "n_days"]
    results["monthly_attribution"] = {str(k): {c: round(v, 5) for c, v in row.items()}
                                      for k, row in monthly.iterrows()}
    pos_months = int((monthly["cum_excess"] > 0).sum())
    results["monthly_attribution_summary"] = {
        "n_months": len(monthly), "positive_months": pos_months,
        "best_month": str(monthly["cum_excess"].idxmax()), "worst_month": str(monthly["cum_excess"].idxmin()),
        "concentration_top2_share": round(float(monthly["cum_excess"].nlargest(2).clip(lower=0).sum()
                                          / max(monthly["cum_excess"].clip(lower=0).sum(), 1e-9)), 3),
    }
    print(f"[p0] P0-4 分月超额: {results['monthly_attribution']}")
    print(f"[p0] P0-4 集中度(前2月占正超额比例)={results['monthly_attribution_summary']['concentration_top2_share']}")

    # ---------- 持久化到 Volume ----------
    out = VOL_ROOT / "p0_results"
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(ic_rows).T.to_csv(out / "ic_decay.csv")
    pd.DataFrame({"group_mean_fwd20": grp_mean, "group_std_fwd20": grp_std}).to_csv(out / "group_monotonicity.csv")
    monthly.to_csv(out / "monthly_attribution.csv")
    pd.DataFrame(ndrop_rows).T.to_csv(out / "ndrop_grid.csv")
    with (out / "summary.json").open("w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"[p0] 全部结果已保存 {out}")
    vol.commit()
    return results


@app.function(
    volumes={str(VOL_ROOT): vol},
    cpu=CPU_COUNT,
    memory=32768,
    timeout=6 * 3600,
)
def p1_diagnostics():
    """P1 进攻批次（3 次训练 + 多次回测，统一 n_drop=3 / P0 修正参数）：
    - P1-7b 双 LGB 变体分数融合：lgb360(20日) + lgb158(20日) z-score 平均 vs 单模型
    - P0 升级项：40 日标签模型（IC 峰值 0.137）的收益验证
    - P1-7c 组合构造：等权 top30/50/70 网格（QLib 回测，含涨跌停约束）
      + 手动模拟"等权 vs 分数加权"对照（简化模拟，无涨跌停约束，仅内部对比）
    结果写入 /vol/p1_results/。"""
    import json

    import numpy as np
    import pandas as pd

    import qlib
    from qlib.backtest import backtest as normal_backtest
    from qlib.contrib.evaluate import risk_analysis
    from qlib.data import D
    from qlib.data.dataset import Dataset
    from qlib.model.base import Model
    from qlib.utils import init_instance_by_config

    _ensure_data()
    END = _latest_trading_day()
    qlib.init(provider_uri=str(DATA_DIR), region="cn",
              exp_manager={"class": "MLflowExpManager", "module_path": "qlib.workflow.expm",
                           "kwargs": {"uri": f"file:{MLRUNS_DIR}", "default_exp_name": "qlib-cn-p1"}})

    def train_predict(model_key, label40=False):
        cfg = _load_and_patch_cfg(MODEL_CFG[model_key], smoke=False, recent=True, long_train=True,
                                  label20=not label40, label40=label40)
        m = init_instance_by_config(cfg["task"]["model"], accept_types=Model)
        ds = init_instance_by_config(cfg["task"]["dataset"], accept_types=Dataset)
        m.fit(ds)
        p = m.predict(ds)
        return (p - p.mean()) / p.std()

    # ---------- 训练三个模型 ----------
    print("[p1] 训练 lgb360-20d ...")
    p360_20 = train_predict("lgb360")
    print("[p1] 训练 lgb158-20d ...")
    p158_20 = train_predict("lgb158")
    print("[p1] 训练 lgb360-40d ...")
    p360_40 = train_predict("lgb360", label40=True)
    # 融合（对齐 index 后平均）
    common = p360_20.index.intersection(p158_20.index)
    p_blend = ((p360_20.reindex(common) + p158_20.reindex(common)) / 2).sort_index()

    # ---------- QLib 回测（等权 TopkDropout，n_drop=3） ----------
    bt_kwargs = dict(start_time="2026-01-01", end_time=END, account=100000000, benchmark="SH000905",
                     exchange_kwargs={"limit_threshold": 0.095, "deal_price": "close",
                                      "open_cost": 0.0005, "close_cost": 0.0015, "min_cost": 5})
    executor = {"class": "SimulatorExecutor", "module_path": "qlib.backtest.executor",
                "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True}}

    def bt(signal, topk=50, n_drop=3):
        strategy = {"class": "TopkDropoutStrategy", "module_path": "qlib.contrib.strategy",
                    "kwargs": {"signal": signal, "topk": topk, "n_drop": n_drop}}
        pm, _ = normal_backtest(strategy=strategy, executor=executor, **bt_kwargs)
        rep = pm["1day"][0]
        ra = risk_analysis(rep["return"] - rep["bench"] - rep["cost"])
        return {"excess_with_cost_annual": round(float(ra.loc["annualized_return", "risk"]), 4),
                "ir": round(float(ra.loc["information_ratio", "risk"]), 3),
                "max_drawdown": round(float(ra.loc["max_drawdown", "risk"]), 4)}, rep

    results = {"data_package": END, "n_drop": 3}

    # P1-7b 信号对比（top50 等权）
    r, rep_blend = bt(p_blend)
    results["blend_20d"] = r; print(f"[p1] 7b 融合(158+360, 20日): {r}")
    r, _ = bt(p360_20)
    results["single_360_20d"] = r; print(f"[p1] 7b 单360(20日): {r}")
    r, _ = bt(p158_20)
    results["single_158_20d"] = r; print(f"[p1] 7b 单158(20日): {r}")
    # 40 日标签
    r, rep_40 = bt(p360_40)
    results["single_360_40d"] = r; print(f"[p1] 40日标签: {r}")

    # P1-7c topk 网格（用最优信号；先以 360-20d 为基准网格，融合信号跑 top50 已有）
    topk_grid = {}
    for tk in [30, 50, 70]:
        r, _ = bt(p360_20, topk=tk)
        topk_grid[tk] = r
        print(f"[p1] 7c topk={tk}: {r}")
    results["topk_grid_360_20d"] = topk_grid

    # P1-7c 分数加权 vs 等权（手动模拟：每 20 交易日调仓，无涨跌停约束，仅内部对比）
    insts = sorted(set(p360_20.index.get_level_values(1)))
    close = D.features(insts, ["$close"], start_time=p360_20.index.get_level_values(0).min(),
                       end_time=END, freq="day")["$close"]
    close = close.swaplevel().sort_index()  # (datetime, instrument)
    fwd20 = close.groupby(level=1).shift(-20) / close - 1

    def sim_weighted(pred, mode, topk=50, every=20, cost_one_side=0.002):
        dates = pred.index.get_level_values(0).unique().sort_values()
        reb_dates = dates[::every]
        prev_w = pd.Series(dtype=float)
        tot_ret, tot_cost, n = 0.0, 0.0, 0
        for d in reb_dates:
            day = pred.loc[d].dropna()
            top = day.sort_values(ascending=False).head(topk)
            if len(top) < 10:
                continue
            f = fwd20.reindex(pd.MultiIndex.from_arrays([[d] * len(top), top.index],
                                                        names=["datetime", "instrument"])).droplevel("datetime").dropna()
            if len(f) < 10:
                continue
            if mode == "equal":
                w = pd.Series(1.0 / len(top), index=top.index).reindex(f.index)
            else:  # score：分数线性加权，min 映射 0.5 防负/零权
                s = (top - top.min()) / (top.max() - top.min() + 1e-9) + 0.5
                w = (s / s.sum()).reindex(f.index)
            w = w / w.sum()  # Re-normalize after excluding missing future prices
            gross = float((w * f).sum())
            universe = w.index.union(prev_w.index)
            turnover = float((w.reindex(universe, fill_value=0) -
                              prev_w.reindex(universe, fill_value=0)).abs().sum())
            fee = turnover * cost_one_side
            tot_ret += gross - fee
            tot_cost += fee
            n += 1
            prev_w = w
        ann = tot_ret / max(n, 1) * (238 / every)
        return {"ann_portfolio_sim": round(ann, 4), "n_rebalance": n, "total_cost": round(tot_cost, 4)}

    sim_eq = sim_weighted(p360_20, "equal")
    sim_sc = sim_weighted(p360_20, "score")
    results["sim_equal_vs_score"] = {"equal": sim_eq, "score": sim_sc,
                                      "note": "手动模拟（每20日调仓、无涨跌停约束），仅内部对比可信"}
    print(f"[p1] 7c 手动模拟: 等权={sim_eq} 分数加权={sim_sc}")

    # ---------- 持久化 ----------
    out = VOL_ROOT / "p1_results"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "summary.json").open("w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    rep_blend.to_pickle(out / "report_blend_20d.pkl")
    rep_40.to_pickle(out / "report_360_40d.pkl")
    print(f"[p1] 全部结果已保存 {out}")
    vol.commit()
    return results


CHENDITC_HIST_URL = "https://github.com/chenditc/investment_data/releases/download/{tag}/qlib_bin.tar.gz"


def _read_bin(path):
    import numpy as np

    arr = np.fromfile(path, dtype="<f")
    return int(arr[0]), arr[1:]


def _load_0911_package():
    """下载并解压 chenditc 09-11 历史数据包到 /tmp/v0911，返回根目录。"""
    import shutil
    import tarfile

    import requests

    root = Path("/tmp/v0911")
    if (root / "features").exists():
        return root
    url = CHENDITC_HIST_URL.format(tag="2026-09-11")
    zip_path = Path("/tmp/chenditc_0911.tar.gz")
    print(f"[vcheck] 下载历史包 {url} ...")
    with requests.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        with zip_path.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    extract = Path("/tmp/v0911_extract")
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
    shutil.move(str(base), str(root))
    print(f"[vcheck] 历史包解压完成: {root}")
    return root


@app.function(
    volumes={str(VOL_ROOT): vol},
    cpu=CPU_COUNT,
    memory=32768,
    timeout=6 * 3600,
)
def version_check_0911():
    """版本敏感性验证（历史包侧）：
    1) bin 级 diff：09-11 包 vs 当前 09-16 包的 factor/close 历史修订幅度
    2) 用 09-11 包训练 Alpha158/Alpha360（20日标签，long_train），回测统一区间 2026-01-01~09-11（n_drop=3）
    结果存 /vol/p1_results/version_check/。"""
    import json

    import numpy as np
    import pandas as pd

    # ---------- 第一层：数据 diff（纯文件级，无需 qlib init） ----------
    v0911 = _load_0911_package()
    cal11 = pd.read_csv(v0911 / "calendars" / "day.txt", header=None)[0].tolist()
    cal16 = pd.read_csv(DATA_DIR / "calendars" / "day.txt", header=None)[0].tolist()
    overlap_end_idx = cal16.index("2026-09-11")
    print(f"[vcheck] cal11={len(cal11)}天 cal16={len(cal16)}天 重叠至09-11={overlap_end_idx + 1}天")

    feat11 = v0911 / "features"
    feat16 = DATA_DIR / "features"
    stocks = sorted(p.name for p in feat16.iterdir() if p.is_dir())
    stocks = [s for s in stocks if (feat11 / s).is_dir()]

    factor_diff_stocks, close_diff_stocks = 0, 0
    factor_max, close_max_rel = 0.0, 0.0
    diff_dates_min, diff_dates_max = None, None
    detail = {}
    for s in stocks:
        f11, f16 = feat11 / s, feat16 / s
        if not (f11 / "factor.day.bin").exists() or not (f16 / "factor.day.bin").exists():
            continue
        s11, v11 = _read_bin(f11 / "factor.day.bin")
        s16, v16 = _read_bin(f16 / "factor.day.bin")
        n = min(len(v11), len(v16))
        # 对齐：各自起始索引不同，取日期重叠部分（简化：比较相同日历偏移的重叠窗）
        off = max(s11, s16)
        a11 = v11[off - s11: off - s11 + min(n, overlap_end_idx - off)]
        a16 = v16[off - s16: off - s16 + min(n, overlap_end_idx - off)]
        m = min(len(a11), len(a16))
        if m <= 0:
            continue
        d = np.abs(a11[:m] - a16[:m])
        dmax = float(d.max()) if len(d) else 0.0
        if dmax > 1e-4:
            factor_diff_stocks += 1
            factor_max = max(factor_max, dmax)
            bad = np.nonzero(d > 1e-4)[0]
            dmin_date, dmax_date = cal16[off + bad.min()], cal16[off + bad.max()]
            diff_dates_min = dmin_date if diff_dates_min is None else min(diff_dates_min, dmin_date)
            diff_dates_max = dmax_date if diff_dates_max is None else max(diff_dates_max, dmax_date)
            if len(detail) < 5:
                detail[s] = {"factor_max_diff": round(dmax, 6), "n_days_diff": int(len(bad))}
        # close 差异比较：factor 有差异的股票 + 前 50 只（抽样），比较相对差
        want_close = (dmax > 1e-4) or (close_diff_stocks + factor_diff_stocks) < 50
        if want_close and (f11 / "close.day.bin").exists() and (f16 / "close.day.bin").exists():
            s11c, v11c = _read_bin(f11 / "close.day.bin")
            s16c, v16c = _read_bin(f16 / "close.day.bin")
            offc = max(s11c, s16c)
            c11 = v11c[offc - s11c: offc - s11c + 300]
            c16 = v16c[offc - s16c: offc - s16c + 300]
            mc = min(len(c11), len(c16))
            if mc > 0:
                rel = np.abs((c11[:mc] - c16[:mc]) / np.where(c16[:mc] != 0, c16[:mc], 1))
                rmax = float(rel.max())
                if rmax > 1e-6:
                    close_diff_stocks += 1
                    close_max_rel = max(close_max_rel, rmax)
                    if s in detail:
                        detail[s]["close_max_rel_diff"] = round(rmax, 8)

    diff_result = {
        "n_stocks_compared": len(stocks),
        "factor_diff_stocks": factor_diff_stocks,
        "factor_max_abs_diff": round(factor_max, 6),
        "close_sample_diff_stocks": close_diff_stocks,
        "close_sample_max_rel_diff": round(close_max_rel, 8),
        "factor_diff_date_range": [diff_dates_min, diff_dates_max],
        "sample_detail": detail,
    }
    print(f"[vcheck] diff 结果: {json.dumps(diff_result, ensure_ascii=False)}")

    # ---------- 第二层：09-11 包训练+回测 ----------
    import qlib
    from qlib.backtest import backtest as normal_backtest
    from qlib.contrib.evaluate import risk_analysis
    from qlib.data.dataset import Dataset
    from qlib.model.base import Model
    from qlib.utils import init_instance_by_config

    qlib.init(provider_uri=str(v0911), region="cn",
              exp_manager={"class": "MLflowExpManager", "module_path": "qlib.workflow.expm",
                           "kwargs": {"uri": f"file:{MLRUNS_DIR}", "default_exp_name": "qlib-cn-vcheck"}})

    bt_kwargs = dict(start_time="2026-01-01", end_time="2026-09-11", account=100000000, benchmark="SH000905",
                     exchange_kwargs={"limit_threshold": 0.095, "deal_price": "close",
                                      "open_cost": 0.0005, "close_cost": 0.0015, "min_cost": 5})
    executor = {"class": "SimulatorExecutor", "module_path": "qlib.backtest.executor",
                "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True}}

    matrix_0911 = {}
    for key in ["lgb158", "lgb360"]:
        cfg = _load_and_patch_cfg(MODEL_CFG[key], smoke=False, recent=True, long_train=True, label20=True)
        # recent 模式 END 取自 09-11 包自身日历（_latest_trading_day 读的是主包——手动覆写）
        dh = cfg["task"]["dataset"]["kwargs"]["handler"]["kwargs"]
        dh["end_time"] = "2026-09-11"
        cfg["task"]["dataset"]["kwargs"]["segments"]["test"] = ["2026-01-01", "2026-09-11"]
        m = init_instance_by_config(cfg["task"]["model"], accept_types=Model)
        ds = init_instance_by_config(cfg["task"]["dataset"], accept_types=Dataset)
        m.fit(ds)
        pred = m.predict(ds)
        strategy = {"class": "TopkDropoutStrategy", "module_path": "qlib.contrib.strategy",
                    "kwargs": {"signal": pred, "topk": 50, "n_drop": 3}}
        pm, _ = normal_backtest(strategy=strategy, executor=executor, **bt_kwargs)
        ra = risk_analysis(pm["1day"][0]["return"] - pm["1day"][0]["bench"] - pm["1day"][0]["cost"])
        matrix_0911[key] = {"excess_with_cost_annual": round(float(ra.loc["annualized_return", "risk"]), 4),
                             "ir": round(float(ra.loc["information_ratio", "risk"]), 3),
                             "max_drawdown": round(float(ra.loc["max_drawdown", "risk"]), 4)}
        print(f"[vcheck] 09-11包 {key}: {matrix_0911[key]}")

    out = VOL_ROOT / "p1_results" / "version_check"
    out.mkdir(parents=True, exist_ok=True)
    result = {"data_diff": diff_result, "matrix_0911": matrix_0911, "bt_window": "2026-01-01~2026-09-11", "n_drop": 3}
    with (out / "vcheck_0911.json").open("w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    vol.commit()
    return result


@app.function(
    volumes={str(VOL_ROOT): vol},
    cpu=CPU_COUNT,
    memory=32768,
    timeout=4 * 3600,
)
def version_check_0916():
    """版本敏感性验证（当前包侧）：09-16 包训练 Alpha158/Alpha360，回测统一区间 2026-01-01~09-11（n_drop=3）。"""
    import json

    import qlib
    from qlib.backtest import backtest as normal_backtest
    from qlib.contrib.evaluate import risk_analysis
    from qlib.data.dataset import Dataset
    from qlib.model.base import Model
    from qlib.utils import init_instance_by_config

    _ensure_data()
    qlib.init(provider_uri=str(DATA_DIR), region="cn",
              exp_manager={"class": "MLflowExpManager", "module_path": "qlib.workflow.expm",
                           "kwargs": {"uri": f"file:{MLRUNS_DIR}", "default_exp_name": "qlib-cn-vcheck"}})

    bt_kwargs = dict(start_time="2026-01-01", end_time="2026-09-11", account=100000000, benchmark="SH000905",
                     exchange_kwargs={"limit_threshold": 0.095, "deal_price": "close",
                                      "open_cost": 0.0005, "close_cost": 0.0015, "min_cost": 5})
    executor = {"class": "SimulatorExecutor", "module_path": "qlib.backtest.executor",
                "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True}}

    matrix_0916 = {}
    preds = {}
    for key in ["lgb158", "lgb360"]:
        cfg = _load_and_patch_cfg(MODEL_CFG[key], smoke=False, recent=True, long_train=True, label20=True)
        m = init_instance_by_config(cfg["task"]["model"], accept_types=Model)
        ds = init_instance_by_config(cfg["task"]["dataset"], accept_types=Dataset)
        m.fit(ds)
        preds[key] = m.predict(ds)
    # 158 特征下 n_drop 网格（P0 的网格是在 Alpha360 上做的，特征终判为 158 后需重测）
    for nd in [1, 2, 3, 5]:
        strategy = {"class": "TopkDropoutStrategy", "module_path": "qlib.contrib.strategy",
                    "kwargs": {"signal": preds["lgb158"], "topk": 50, "n_drop": nd}}
        pm, _ = normal_backtest(strategy=strategy, executor=executor, **bt_kwargs)
        ra = risk_analysis(pm["1day"][0]["return"] - pm["1day"][0]["bench"] - pm["1day"][0]["cost"])
        matrix_0916[f"lgb158_ndrop{nd}"] = {"excess_with_cost_annual": round(float(ra.loc["annualized_return", "risk"]), 4),
                                             "ir": round(float(ra.loc["information_ratio", "risk"]), 3),
                                             "max_drawdown": round(float(ra.loc["max_drawdown", "risk"]), 4)}
        print(f"[vcheck] 09-16包 lgb158 n_drop={nd}: {matrix_0916[f'lgb158_ndrop{nd}']}")
    # 360 对照（n_drop=3）
    strategy = {"class": "TopkDropoutStrategy", "module_path": "qlib.contrib.strategy",
                "kwargs": {"signal": preds["lgb360"], "topk": 50, "n_drop": 3}}
    pm, _ = normal_backtest(strategy=strategy, executor=executor, **bt_kwargs)
    ra = risk_analysis(pm["1day"][0]["return"] - pm["1day"][0]["bench"] - pm["1day"][0]["cost"])
    matrix_0916["lgb360_ndrop3"] = {"excess_with_cost_annual": round(float(ra.loc["annualized_return", "risk"]), 4),
                                     "ir": round(float(ra.loc["information_ratio", "risk"]), 3),
                                     "max_drawdown": round(float(ra.loc["max_drawdown", "risk"]), 4)}
    print(f"[vcheck] 09-16包 lgb360 n_drop=3: {matrix_0916['lgb360_ndrop3']}")

    out = VOL_ROOT / "p1_results" / "version_check"
    out.mkdir(parents=True, exist_ok=True)
    result = {"matrix_0916": matrix_0916, "bt_window": "2026-01-01~2026-09-11", "n_drop": 3}
    with (out / "vcheck_0916.json").open("w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    vol.commit()
    return result


ROLLING5Y_WINDOWS = [
    # 2021Q1 ~ 2026Q3 共 22 个季度窗口；train 固定 2016 起（expanding），valid=前一季度
    (f"{y}-{q}", f"{y}-{q}-{d}") for y, q, d in []
]


def _gen_5y_windows():
    """生成 2021Q1~2026Q3 的 22 个季度窗口：
    每窗口 train [2016-01-01, T-2季度末] / valid [T-1季度] / test [T 季度]，expanding 训练。"""
    from datetime import date

    def qtr_end(y, m):  # 该季度最后一天
        me = {1: 3, 4: 6, 7: 9, 10: 12}[m]
        return date(y, me, {3: 31, 6: 30, 9: 30, 12: 31}[me])

    def qtr_start(y, m):
        return date(y, m, 1)

    quarters = [(y, m) for y in range(2021, 2027) for m in (1, 4, 7, 10)]
    quarters = [(y, m) for (y, m) in quarters if not (y >= 2026 and m >= 10)]  # 截至 2026Q3
    wins = []
    for y, m in quarters:
        py, pm = (y, m - 3) if m > 1 else (y - 1, 10)      # T-1 季度 = valid
        ppy, ppm = (py, pm - 3) if pm > 1 else (py - 1, 10)  # T-2 季度
        # train_end = T-2Q末（与 P2 一致）：train 末样本的 20 日标签只落到 valid 期，
        # 不会泄露 test 头部。此前版本的 train_end=valid_end 会让标签直接 peek test —— 真前视。
        wins.append(("2016-01-01", str(qtr_end(ppy, ppm)),
                    str(qtr_start(py, pm)), str(qtr_end(py, pm)),
                    str(qtr_start(y, m)), str(qtr_end(y, m))))
    return wins


@app.function(
    volumes={str(VOL_ROOT): vol},
    cpu=8,
    memory=24576,
    timeout=2 * 3600,
    max_containers=8,
)
def batch_c_window(args: dict):
    """批次C 窗口级 worker：单窗口 训练+回测（严格无前视）。"""
    import numpy as np
    import pandas as pd

    import qlib
    from qlib.backtest import backtest as normal_backtest
    from qlib.contrib.evaluate import risk_analysis
    from qlib.data.dataset import Dataset
    from qlib.model.base import Model
    from qlib.utils import init_instance_by_config

    _ensure_data()
    qlib.init(provider_uri=str(DATA_DIR), region="cn", skip_if_reg=True,
              exp_manager={"class": "MLflowExpManager", "module_path": "qlib.workflow.expm",
                           "kwargs": {"uri": f"file:{MLRUNS_DIR}", "default_exp_name": "qlib-cn-batchC"}})

    tr_s, tr_e, va_s, va_e, te_s, te_e = args["segments"]
    cfg = _load_and_patch_cfg(MODEL_CFG["lgb158"], smoke=False, recent=False, long_train=False, label20=True)
    dh = cfg["task"]["dataset"]["kwargs"]["handler"]["kwargs"]
    dh["start_time"] = "2015-01-01"
    dh["end_time"] = te_e
    dh["fit_start_time"] = tr_s
    dh["fit_end_time"] = tr_e
    dh["instruments"] = args["market"]
    seg = cfg["task"]["dataset"]["kwargs"]["segments"]
    seg["train"] = [tr_s, tr_e]
    seg["valid"] = [va_s, va_e]
    seg["test"] = [te_s, te_e]
    # Validation targets near the boundary cannot use test-period closes.
    purge_cfg_splits(cfg, read_trading_calendar(DATA_DIR), horizon=20)
    m = init_instance_by_config(cfg["task"]["model"], accept_types=Model)
    ds = init_instance_by_config(cfg["task"]["dataset"], accept_types=Dataset)
    m.fit(ds)
    pred = m.predict(ds)
    if len(pred) == 0 or pred.index.get_level_values(0).nunique() < 20:
        return {"window": args["name"], "error": "insufficient predictions"}
    executor = {"class": "SimulatorExecutor", "module_path": "qlib.backtest.executor",
                "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True}}
    strategy = {"class": "TopkDropoutStrategy", "module_path": "qlib.contrib.strategy",
                "kwargs": {"signal": pred, "topk": args["topk"], "n_drop": args["nd"]}}
    pm, _ = normal_backtest(strategy=strategy, executor=executor,
                             start_time=te_s, end_time=te_e, account=100000000,
                             benchmark=args["bench"],
                             exchange_kwargs={"limit_threshold": 0.095, "deal_price": "close",
                                              "open_cost": 0.0005, "close_cost": 0.0015, "min_cost": 5})
    rep = pm["1day"][0]
    excess = rep["return"] - rep["bench"] - rep["cost"]
    return {"window": args["name"], "test": f"{te_s}~{te_e}",
            "excess_total": round(float(excess.sum()), 4),
            "daily_mean": round(float(excess.mean()), 6),
            "max_drawdown": round(float((excess.cumsum() - excess.cumsum().cummax()).min()), 4),
            "n_days": int(len(excess))}


@app.function(
    volumes={str(VOL_ROOT): vol},
    cpu=4,
    memory=8192,
    timeout=12 * 3600,
)
def batch_c(market: str = "csi1000", bench: str = "SH000852", topk: int = 20, nd: int = 2, tag: str = "c1000"):
    """批次C：候选配置 × 5 年滚动（2021Q1~2026Q3，22 个季度窗口，8 并行）。
    这是多重检验纪律下的唯一终审裁判。结果存 /vol/batch_c/。"""
    import json

    _ensure_data()
    wins = _gen_5y_windows()
    print(f"[batchC] {tag}: {market} top{topk}/nd{nd}，{len(wins)} 个窗口")
    latest = _latest_trading_day()
    jobs = []
    for i, (tr_s, tr_e, va_s, va_e, te_s, te_e) in enumerate(wins):
        if te_e > latest:  # 最后一窗口可能越过数据末日
            te_e = latest
        jobs.append({"name": f"w{i+1:02d}", "segments": [tr_s, tr_e, va_s, va_e, te_s, te_e],
                     "market": market, "bench": bench, "topk": topk, "nd": nd})
    results = list(batch_c_window.map(jobs))
    ok = [r for r in results if "error" not in r]
    errs = [r for r in results if "error" in r]
    excess_all = [r["excess_total"] for r in ok]
    import numpy as np

    pos = sum(1 for e in excess_all if e > 0)
    summary = {
        "config": f"{market} top{topk}/nd{nd} bench={bench}", "n_windows": len(ok), "n_errors": len(errs),
        "mean_q_excess": round(float(np.mean(excess_all)), 4),
        "median_q_excess": round(float(np.median(excess_all)), 4),
        "best_q": round(max(excess_all), 4), "worst_q": round(min(excess_all), 4),
        "positive_windows": pos, "ann_excess_approx": round(float(np.mean(excess_all)) * 4, 4),
        "note": "季度超额均值×4≈年化（expanding训练，窗口自相关未校正）",
        "windows": results,
    }
    print(f"[batchC] {tag} 汇总: 均值季度超额={summary['mean_q_excess']} 正窗口={pos}/{len(ok)} "
          f"年化≈{summary['ann_excess_approx']} 最差季={summary['worst_q']}")
    out = VOL_ROOT / "batch_c"
    out.mkdir(parents=True, exist_ok=True)
    with (out / f"rolling5y_{tag}.json").open("w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    vol.commit()
    return summary


@app.function(
    volumes={str(VOL_ROOT): vol},
    cpu=CPU_COUNT,
    memory=32768,
    timeout=12 * 3600,
)
def p2_rolling():
    """P2-13 滚动 walk-forward（Alpha158 + 20日标签 + top50 + n_drop=3）：
    7 个季度窗口，每个窗口只用截至当时的数据训练（严格无前视），回测该季度。
    拼接收益序列做 21 个月分月归因。
    回答的问题：+9.4% 是稳定水平，还是单窗口运气？"""
    import json

    import numpy as np
    import pandas as pd

    import qlib
    from qlib.backtest import backtest as normal_backtest
    from qlib.contrib.evaluate import risk_analysis
    from qlib.data.dataset import Dataset
    from qlib.model.base import Model
    from qlib.utils import init_instance_by_config

    _ensure_data()
    qlib.init(provider_uri=str(DATA_DIR), region="cn",
              exp_manager={"class": "MLflowExpManager", "module_path": "qlib.workflow.expm",
                           "kwargs": {"uri": f"file:{MLRUNS_DIR}", "default_exp_name": "qlib-cn-p2"}})

    executor = {"class": "SimulatorExecutor", "module_path": "qlib.backtest.executor",
                "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True}}
    quarterly = []
    report_parts = []
    _latest = _latest_trading_day()
    _windows = _gen_5y_windows()[-7:]
    for w_idx, (tr_s, tr_e, va_s, va_e, te_s, te_e) in enumerate(_windows, 1):
        if te_s > _latest:
            continue
        te_e = min(te_e, _latest)
        print(f"[p2] 窗口{w_idx}: train {tr_s}~{tr_e} valid {va_s}~{va_e} test {te_s}~{te_e}")
        cfg = _load_and_patch_cfg(MODEL_CFG["lgb158"], smoke=False, recent=False, long_train=False, label20=True)
        dh = cfg["task"]["dataset"]["kwargs"]["handler"]["kwargs"]
        dh["start_time"] = "2015-01-01"
        dh["end_time"] = te_e
        dh["fit_start_time"] = tr_s
        dh["fit_end_time"] = tr_e
        seg = cfg["task"]["dataset"]["kwargs"]["segments"]
        seg["train"] = [tr_s, tr_e]
        seg["valid"] = [va_s, va_e]
        seg["test"] = [te_s, te_e]
        purge_cfg_splits(cfg, read_trading_calendar(DATA_DIR), horizon=20)
        m = init_instance_by_config(cfg["task"]["model"], accept_types=Model)
        ds = init_instance_by_config(cfg["task"]["dataset"], accept_types=Dataset)
        m.fit(ds)
        pred = m.predict(ds)
        if len(pred) == 0 or pred.index.get_level_values(0).nunique() < 20:
            print(f"[p2] 窗口{w_idx} 预测样本不足，跳过")
            continue
        strategy = {"class": "TopkDropoutStrategy", "module_path": "qlib.contrib.strategy",
                    "kwargs": {"signal": pred, "topk": 50, "n_drop": 3}}
        pm, _ = normal_backtest(strategy=strategy, executor=executor,
                                start_time=te_s, end_time=te_e, account=100000000, benchmark="SH000905",
                                exchange_kwargs={"limit_threshold": 0.095, "deal_price": "close",
                                                 "open_cost": 0.0005, "close_cost": 0.0015, "min_cost": 5})
        rep = pm["1day"][0]
        ra = risk_analysis(rep["return"] - rep["bench"] - rep["cost"])
        quarterly.append({
            "window": f"w{w_idx}", "test": f"{te_s}~{te_e}",
            "excess_with_cost_total": round(float((rep["return"] - rep["bench"] - rep["cost"]).sum()), 4),
            "excess_daily_mean": round(float((rep["return"] - rep["bench"] - rep["cost"]).mean()), 6),
            "ir": round(float(ra.loc["information_ratio", "risk"]), 3),
            "max_drawdown": round(float(ra.loc["max_drawdown", "risk"]), 4),
        })
        print(f"[p2] 窗口{w_idx} 季度超额(累计)={quarterly[-1]['excess_with_cost_total']}")
        report_parts.append(rep[["return", "bench", "cost"]])

    # 拼接 21 个月收益序列 → 分月归因
    if not report_parts:
        raise RuntimeError("P2: no valid test windows")
    full = pd.concat(report_parts).sort_index()
    excess = full["return"] - full["bench"] - full["cost"]
    monthly = excess.groupby(excess.index.to_period("M")).agg(["mean", "sum", "count"])
    monthly.columns = ["daily_mean_excess", "cum_excess", "n_days"]
    total_mean = float(excess.mean())
    ann = total_mean * 238
    pos_months = int((monthly["cum_excess"] > 0).sum())
    # rolling(3) 的前 2 个值是 NaN，须 fillna(0) 再取 max
    neg_streak = int((monthly["cum_excess"] < 0).astype(int).rolling(3).sum().fillna(0).max())
    results = {
        "config": "Alpha158 + 20d label + long-rolling train + top50 + n_drop=3",
        "n_windows": len(quarterly), "n_days": int(len(excess)),
        "overall": {"ann_excess_with_cost": round(ann, 4), "daily_mean": round(total_mean, 6),
                     "positive_months": pos_months, "total_months": len(monthly),
                     "worst_3m_streak_neg": neg_streak},
        "quarterly": quarterly,
        "monthly": {str(k): {c: round(v, 5) for c, v in row.items()} for k, row in monthly.iterrows()},
    }
    print(f"[p2] 滚动总览: 年化超额={results['overall']['ann_excess_with_cost']} "
          f"正超额月份={pos_months}/{len(monthly)} 最差连续负月数={neg_streak}")
    out = VOL_ROOT / "p2_results"
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(quarterly).to_csv(out / "rolling_quarterly.csv", index=False)
    monthly.to_csv(out / "rolling_monthly.csv")
    with (out / "summary.json").open("w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    full.to_pickle(out / "rolling_daily_report.pkl")
    print(f"[p2] 全部结果已保存 {out}")
    vol.commit()
    return results


@app.function(
    volumes={str(VOL_ROOT): vol},
    cpu=CPU_COUNT,
    memory=32768,
    timeout=4 * 3600,
)
def topk_grid(topks="10,20,30,50", n_drop: int = 3):
    """topk 网格（Alpha158 + 20日标签 + long_train，统一区间 2026-01-01~09-11）。
    P1-7c 的网格是在 Alpha360 上做的，特征终判为 158 后需重测；
    且 top10 从未测过（P0-3 显示只有头部组正收益——更小 topk 可能放大头部 alpha）。
    结果存 /vol/p1_results/topk_grid_158.json。"""
    import json

    import qlib
    from qlib.backtest import backtest as normal_backtest
    from qlib.contrib.evaluate import risk_analysis
    from qlib.data.dataset import Dataset
    from qlib.model.base import Model
    from qlib.utils import init_instance_by_config

    _ensure_data()
    qlib.init(provider_uri=str(DATA_DIR), region="cn",
              exp_manager={"class": "MLflowExpManager", "module_path": "qlib.workflow.expm",
                           "kwargs": {"uri": f"file:{MLRUNS_DIR}", "default_exp_name": "qlib-cn-topk"}})
    cfg = _load_and_patch_cfg(MODEL_CFG["lgb158"], smoke=False, recent=True, long_train=True, label20=True)
    m = init_instance_by_config(cfg["task"]["model"], accept_types=Model)
    ds = init_instance_by_config(cfg["task"]["dataset"], accept_types=Dataset)
    m.fit(ds)
    pred = m.predict(ds)

    executor = {"class": "SimulatorExecutor", "module_path": "qlib.backtest.executor",
                "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True}}
    grid = {}
    for tk in [int(x) for x in topks.split(",")]:
        strategy = {"class": "TopkDropoutStrategy", "module_path": "qlib.contrib.strategy",
                    "kwargs": {"signal": pred, "topk": tk, "n_drop": n_drop}}
        pm, _ = normal_backtest(strategy=strategy, executor=executor,
                                 start_time="2026-01-01", end_time="2026-09-11", account=100000000,
                                 benchmark="SH000905",
                                 exchange_kwargs={"limit_threshold": 0.095, "deal_price": "close",
                                                  "open_cost": 0.0005, "close_cost": 0.0015, "min_cost": 5})
        rep = pm["1day"][0]
        ra = risk_analysis(rep["return"] - rep["bench"] - rep["cost"])
        grid[tk] = {"excess_with_cost_annual": round(float(ra.loc["annualized_return", "risk"]), 4),
                    "ir": round(float(ra.loc["information_ratio", "risk"]), 3),
                    "max_drawdown": round(float(ra.loc["max_drawdown", "risk"]), 4)}
        print(f"[topk] {tk}: {grid[tk]}")

    out = VOL_ROOT / "p1_results"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "topk_grid_158.json").open("w") as f:
        json.dump({"window": "2026-01-01~2026-09-11", "n_drop": n_drop, "grid": grid}, f, indent=2)
    vol.commit()
    return grid


@app.function(
    volumes={str(VOL_ROOT): vol},
    cpu=CPU_COUNT,
    memory=32768,
    timeout=6 * 3600,
)
def batch_a():
    """批次A 粗筛（多重检验纪律：记录全部结果而非只记最优；差异<3pp视为噪声）：
    A1. topk×n_drop 耦合：top20×n_drop{1,2,3}（对照 top50×nd3）
    A2. 股票池：{csi300, csi500(基线), csi1000}×top20×nd2（基准指数按池匹配，含SH000852存在性检查）
    统一区间 2026-01-01~09-11；Alpha158+20日标签+2016起训练。
    结果存 /vol/batch_a/。"""
    import json

    import qlib
    from qlib.backtest import backtest as normal_backtest
    from qlib.contrib.evaluate import risk_analysis
    from qlib.data import D
    from qlib.data.dataset import Dataset
    from qlib.model.base import Model
    from qlib.utils import init_instance_by_config

    _ensure_data()
    qlib.init(provider_uri=str(DATA_DIR), region="cn",
              exp_manager={"class": "MLflowExpManager", "module_path": "qlib.workflow.expm",
                           "kwargs": {"uri": f"file:{MLRUNS_DIR}", "default_exp_name": "qlib-cn-batchA"}})

    executor = {"class": "SimulatorExecutor", "module_path": "qlib.backtest.executor",
                "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True}}
    # csi1000 基准存在性检查
    bench1000 = D.features(["SH000852"], ["$close"], start_time="2026-01-01", end_time="2026-01-10", freq="day")
    print(f"[batchA] SH000852 数据存在: {len(bench1000) > 0}")

    def bt(signal, topk, nd, bench="SH000905"):
        strategy = {"class": "TopkDropoutStrategy", "module_path": "qlib.contrib.strategy",
                    "kwargs": {"signal": signal, "topk": topk, "n_drop": nd}}
        pm, _ = normal_backtest(strategy=strategy, executor=executor,
                                 start_time="2026-01-01", end_time="2026-09-11", account=100000000,
                                 benchmark=bench,
                                 exchange_kwargs={"limit_threshold": 0.095, "deal_price": "close",
                                                  "open_cost": 0.0005, "close_cost": 0.0015, "min_cost": 5})
        rep = pm["1day"][0]
        ra = risk_analysis(rep["return"] - rep["bench"] - rep["cost"])
        return {"excess_with_cost_annual": round(float(ra.loc["annualized_return", "risk"]), 4),
                "ir": round(float(ra.loc["information_ratio", "risk"]), 3),
                "max_drawdown": round(float(ra.loc["max_drawdown", "risk"]), 4)}

    def train_predict(market="csi500", bench=None):
        cfg = _load_and_patch_cfg(MODEL_CFG["lgb158"], smoke=False, recent=True, long_train=True, label20=True)
        dh = cfg["task"]["dataset"]["kwargs"]["handler"]["kwargs"]
        dh["instruments"] = market
        if bench:
            cfg["port_analysis_config"]["backtest"]["benchmark"] = bench
        m = init_instance_by_config(cfg["task"]["model"], accept_types=Model)
        ds = init_instance_by_config(cfg["task"]["dataset"], accept_types=Dataset)
        m.fit(ds)
        return m.predict(ds)

    results = {"window": "2026-01-01~2026-09-11", "note": "粗筛：差异<3pp视为噪声；终审以批次C滚动为准"}

    # A1: topk×n_drop 耦合（csi500 基线池）
    print("[batchA] A1: 训练 csi500 一次 ...")
    pred500 = train_predict("csi500")
    a1 = {}
    for tk, nd in [(20, 1), (20, 2), (20, 3), (50, 3)]:
        a1[f"top{tk}_nd{nd}"] = bt(pred500, tk, nd)
        print(f"[batchA] A1 top{tk}/nd{nd}: {a1[f'top{tk}_nd{nd}']}")
    results["a1_topk_ndrop"] = a1

    # A2: 股票池（top20/nd2 恒定，基准按池匹配）
    a2 = {}
    for market, bench in [("csi300", "SH000300"), ("csi500", "SH000905"), ("csi1000", "SH000852")]:
        if market == "csi500":
            pred = pred500
        else:
            print(f"[batchA] A2: 训练 {market} ...")
            pred = train_predict(market, bench)
        a2[market] = bt(pred, 20, 2, bench=bench)
        print(f"[batchA] A2 {market}: {a2[market]}")
    results["a2_market"] = a2

    out = VOL_ROOT / "batch_a"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "results.json").open("w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    vol.commit()
    return results


@app.function(
    volumes={str(VOL_ROOT): vol},
    cpu=CPU_COUNT,
    memory=32768,
    timeout=8 * 3600,
)
def batch_b(lgb_trials: int = 12, market: str = "csi1000", bench: str = "SH000852"):
    """批次B（削减版）：
    B1. 训练起点 {2011, 2013, 2016}（2018 砍掉：训练窗太短先验差），top20/nd2（默认在批次A胜出池 csi1000 上跑）
    B2. expanding vs sliding（6 年滑窗）在起点结论上做
    B3. LGB 超参 12 组快搜（随机种子固定；从 360 搜索的空间邻近采样）
    统一区间 2026-01-01~09-11。结果存 /vol/batch_b/。"""
    import json
    import random

    import qlib
    from qlib.backtest import backtest as normal_backtest
    from qlib.contrib.evaluate import risk_analysis
    from qlib.data.dataset import Dataset
    from qlib.model.base import Model
    from qlib.utils import init_instance_by_config

    _ensure_data()
    qlib.init(provider_uri=str(DATA_DIR), region="cn",
              exp_manager={"class": "MLflowExpManager", "module_path": "qlib.workflow.expm",
                           "kwargs": {"uri": f"file:{MLRUNS_DIR}", "default_exp_name": "qlib-cn-batchB"}})
    executor = {"class": "SimulatorExecutor", "module_path": "qlib.backtest.executor",
                "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True}}

    def run_bt(cfg, topk=20, nd=2):
        dh_ = cfg["task"]["dataset"]["kwargs"]["handler"]["kwargs"]
        dh_["instruments"] = market
        m = init_instance_by_config(cfg["task"]["model"], accept_types=Model)
        ds = init_instance_by_config(cfg["task"]["dataset"], accept_types=Dataset)
        m.fit(ds)
        pred = m.predict(ds)
        strategy = {"class": "TopkDropoutStrategy", "module_path": "qlib.contrib.strategy",
                    "kwargs": {"signal": pred, "topk": topk, "n_drop": nd}}
        pm, _ = normal_backtest(strategy=strategy, executor=executor,
                                 start_time="2026-01-01", end_time="2026-09-11", account=100000000,
                                 benchmark=bench,
                                 exchange_kwargs={"limit_threshold": 0.095, "deal_price": "close",
                                                  "open_cost": 0.0005, "close_cost": 0.0015, "min_cost": 5})
        rep = pm["1day"][0]
        ra = risk_analysis(rep["return"] - rep["bench"] - rep["cost"])
        return {"excess_with_cost_annual": round(float(ra.loc["annualized_return", "risk"]), 4),
                "ir": round(float(ra.loc["information_ratio", "risk"]), 3),
                "max_drawdown": round(float(ra.loc["max_drawdown", "risk"]), 4)}

    results = {"window": "2026-01-01~2026-09-11", "market": market, "note": "粗筛：终审以批次C滚动为准"}

    # B1: 训练起点
    b1 = {}
    for start in ["2011", "2013", "2016"]:
        cfg = _load_and_patch_cfg(MODEL_CFG["lgb158"], smoke=False, recent=True, long_train=True, label20=True)
        dh = cfg["task"]["dataset"]["kwargs"]["handler"]["kwargs"]
        dh["start_time"] = f"{int(start)-1}-01-01"
        dh["fit_start_time"] = f"{start}-01-01"
        seg = cfg["task"]["dataset"]["kwargs"]["segments"]
        seg["train"] = [f"{start}-01-01", "2024-12-31"]
        b1[start] = run_bt(cfg)
        print(f"[batchB] B1 起点{start}: {b1[start]}")
    results["b1_train_start"] = b1

    # B2: expanding vs sliding（6年滑窗，起点用 B1 最优；先以 2013 中值做，若 B1 结论反转在 C 里修正）
    best_start = max(b1, key=lambda k: b1[k]["excess_with_cost_annual"])
    b2 = {}
    cfg = _load_and_patch_cfg(MODEL_CFG["lgb158"], smoke=False, recent=True, long_train=True, label20=True)
    dh = cfg["task"]["dataset"]["kwargs"]["handler"]["kwargs"]
    dh["start_time"] = f"{int(best_start)-1}-01-01"
    dh["fit_start_time"] = f"{best_start}-01-01"
    seg = cfg["task"]["dataset"]["kwargs"]["segments"]
    seg["train"] = [f"{best_start}-01-01", "2024-12-31"]
    b2["expanding"] = b1[best_start]
    # sliding：只用最近6年
    cfg_s = _load_and_patch_cfg(MODEL_CFG["lgb158"], smoke=False, recent=True, long_train=True, label20=True)
    dh_s = cfg_s["task"]["dataset"]["kwargs"]["handler"]["kwargs"]
    dh_s["start_time"] = "2017-01-01"
    dh_s["fit_start_time"] = "2018-01-01"
    seg_s = cfg_s["task"]["dataset"]["kwargs"]["segments"]
    seg_s["train"] = ["2018-01-01", "2024-12-31"]
    b2["sliding_6y"] = run_bt(cfg_s)
    print(f"[batchB] B2 sliding_6y: {b2['sliding_6y']}")
    results["b2_window_mode"] = b2
    results["b2_note"] = f"expanding 即 B1 起点{best_start}的结果；sliding 固定 6 年窗"

    # B3: LGB 超参 12 组（158+20日标签上；固定 top20/nd2）
    rng = random.Random(7)
    b3 = {}
    for i in range(lgb_trials):
        params = _sample_params(rng)
        cfg = _load_and_patch_cfg(MODEL_CFG["lgb158"], smoke=False, recent=True, long_train=True, label20=True)
        cfg["task"]["model"]["kwargs"].update(params)
        r = run_bt(cfg)
        b3[str(params)] = r
        print(f"[batchB] B3 trial{i}: {r}")
    # 记录全部 trials + 默认参数基线（b1['2016'] 即默认超参 top20/nd2 版本）
    results["b3_lgb_trials"] = b3
    results["b3_baseline_default_params"] = b1["2016"]

    out = VOL_ROOT / "batch_b"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "results.json").open("w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    vol.commit()
    return results


CHENDITC_LATEST_URL = "https://github.com/chenditc/investment_data/releases/latest/download/qlib_bin.tar.gz"


@app.function(
    volumes={str(VOL_ROOT): vol},
    cpu=CPU_COUNT,
    memory=32768,
    timeout=4 * 3600,
)
def daily_standalone(topk: int = 20, nd: int = 2, market: str = "csi1000"):
    """每日信号（自包含单容器版，--best --daily 的实现）：
    下载 chenditc 最新全量包 → 解压到容器本地盘 → 训练终审候选 → top-k 信号 → 返回 CSV 内容。
    - 无 Volume 依赖：每次必然最新数据（根治"忘记 force-data 导致数据陈旧"）
    - 结果由本地入口写入 results/signals/ 并 git 推送（Volume 仅在研究批并行场景保留）
    """
    import shutil
    import tarfile
    import hashlib
    import json
    import os
    import pickle

    import numpy as np
    import pandas as pd
    import requests

    import qlib
    from qlib.data import D
    from qlib.data.dataset import Dataset
    from qlib.model.base import Model
    from qlib.utils import init_instance_by_config

    # ---- 1) 下载最新数据包并解压到容器本地盘 ----
    data_dir = Path("/tmp/cn_data")
    if data_dir.exists():
        shutil.rmtree(data_dir)
    zip_path = Path("/tmp/chenditc_latest.tar.gz")
    extract = Path("/tmp/chenditc_extract")
    print(f"[daily] 下载最新数据 {CHENDITC_LATEST_URL} ...")
    with requests.get(CHENDITC_LATEST_URL, stream=True, timeout=600) as r:
        r.raise_for_status()
        with zip_path.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
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
    shutil.move(str(base), str(data_dir))
    cal_lines = (data_dir / "calendars" / "day.txt").read_text().strip().splitlines()
    print(f"[daily] 数据就绪：日历 {cal_lines[0]} ~ {cal_lines[-1]}（{len(cal_lines)} 个交易日）")

    # ---- 2) 训练终审候选 ----
    qlib.init(provider_uri=str(data_dir), region="cn",
              exp_manager={"class": "MLflowExpManager", "module_path": "qlib.workflow.expm",
                           "kwargs": {"uri": "file:/tmp/mlruns", "default_exp_name": "qlib-cn-daily"}})
    cfg = _load_and_patch_cfg(MODEL_CFG["lgb158"], smoke=False, recent=True, long_train=True, label20=True,
                              topk=topk, nd=nd, market=market, provider_dir=str(data_dir))
    calendar = read_trading_calendar(data_dir)
    asof = calendar[-1]
    # Today is a feature/prediction date, NEVER a training/validation label date.
    live_split = configure_asof(cfg, calendar, asof, horizon=20)
    signature = cache_signature(cfg, horizon=20)
    cache_dir = VOL_ROOT / "live_models"
    cache_dir.mkdir(parents=True, exist_ok=True)
    model_file = cache_dir / f"{signature}.pkl"
    meta_file = cache_dir / f"{signature}.json"
    if model_file.exists() != meta_file.exists():
        raise RuntimeError("Partial model cache; refusing to load an unverified model")
    saved = json.loads(meta_file.read_text()) if meta_file.exists() else None
    if saved is not None and saved.get("signature") != signature:
        raise RuntimeError("Model cache signature mismatch")
    train_now = should_retrain(calendar, asof, saved["fit_asof"] if saved else None,
                               interval=RETRAIN_EVERY_SESSIONS)
    if train_now:
        model_obj = init_instance_by_config(cfg["task"]["model"], accept_types=Model)
        dataset = init_instance_by_config(cfg["task"]["dataset"], accept_types=Dataset)
        model_obj.fit(dataset)
        snapshot = {
            "signature": signature, "fit_asof": asof, "horizon": 20,
            "train": list(cfg["task"]["dataset"]["kwargs"]["segments"]["train"]),
            "valid": list(cfg["task"]["dataset"]["kwargs"]["segments"]["valid"]),
            "fit_start": cfg["task"]["dataset"]["kwargs"]["handler"]["kwargs"]["fit_start_time"],
            "fit_end": cfg["task"]["dataset"]["kwargs"]["handler"]["kwargs"]["fit_end_time"],
        }
        tmp_model = cache_dir / f".{signature}.{os.getpid()}.tmp"
        tmp_meta = cache_dir / f".{signature}.{os.getpid()}.json.tmp"
        try:
            with tmp_model.open("wb") as f:
                pickle.dump(model_obj, f, protocol=pickle.HIGHEST_PROTOCOL)
            snapshot["model_sha256"] = hashlib.sha256(tmp_model.read_bytes()).hexdigest()
            tmp_meta.write_text(json.dumps(snapshot, indent=2))
            os.replace(tmp_model, model_file)
            os.replace(tmp_meta, meta_file)
            vol.commit()
        finally:
            tmp_model.unlink(missing_ok=True)
            tmp_meta.unlink(missing_ok=True)
        saved = snapshot
        print(f"[daily] 模型已重训，训练结束={saved['train'][-1]} 验证结束={saved['valid'][-1]}")
    else:
        # Re-create the handler on fresh features but fit its processors ONLY on
        # the original model's training window. Otherwise daily refitting of
        # feature normalization changes the cached model's input distribution.
        if (saved.get("horizon") != 20 or not saved.get("model_sha256") or
                saved.get("fit_end") != saved.get("train", [None, None])[-1]):
            raise RuntimeError("Invalid cached model split metadata")
        if hashlib.sha256(model_file.read_bytes()).hexdigest() != saved["model_sha256"]:
            raise RuntimeError("Corrupt cached model; refusing unsafe inference")
        opts = cfg["task"]["dataset"]["kwargs"]
        opts["segments"]["train"] = saved["train"]
        opts["segments"]["valid"] = saved["valid"]
        opts["handler"]["kwargs"]["fit_start_time"] = saved["fit_start"]
        opts["handler"]["kwargs"]["fit_end_time"] = saved["fit_end"]
        # Today's only test row has no matured label and is never used in fit.
        opts["segments"]["test"] = [asof, asof]
        opts["handler"]["kwargs"]["end_time"] = asof
        dataset = init_instance_by_config(cfg["task"]["dataset"], accept_types=Dataset)
        with model_file.open("rb") as f:
            model_obj = pickle.load(f)
        print(f"[daily] 复用模型，训练日期={saved['fit_asof']}，距今未满 {RETRAIN_EVERY_SESSIONS} 交易日")
    pred = model_obj.predict(dataset, segment="test")
    if pred.empty:
        raise RuntimeError(f"No inference predictions for {asof}")
    predict_date = pred.index.get_level_values(0).max()
    if str(predict_date)[:10] != asof:
        raise RuntimeError(f"Inference date {predict_date} != latest bar {asof}")
    day = pred.loc[predict_date].dropna()
    # Ranking only: n_drop requires current holdings and an execution-day order planner.
    print("[daily] RANKING ONLY: not executable orders; nd does not apply to ranking CSV")
    top = day.sort_values(ascending=False).head(topk)

    # ---- 3) 涨跌停过滤（口径与 verify_integrity 审计一致：当日涨幅=close/前收-1，|涨幅|≥9.5% 剔除） ----
    day_df = D.features([str(x) for x in day.index], ["$close", "Ref($close,1)"],
                        start_time=predict_date, end_time=predict_date, freq="day")
    if len(day_df) > 0 and ("Ref($close,1)" in day_df.columns):
        day_ret = (day_df["$close"] / day_df["Ref($close,1)"] - 1).dropna()
        limited = day_ret[(day_ret >= 0.095) | (day_ret <= -0.095)].index.get_level_values(0)
        n_removed = len(day) - len(day.index[~day.index.isin(limited)])
        day = day.loc[day.index[~day.index.isin(limited)]]
        top = day.sort_values(ascending=False).head(topk)
        print(f"[daily] 已剔除 {n_removed} 只涨/跌停股")

    # ---- 4) 组装 CSV 内容返回 ----
    csv_content = "rank,instrument,score\n" + "\n".join(
        f"{i},{inst},{score}" for i, (inst, score) in enumerate(top.items(), 1)
    )
    print(f"[daily] {predict_date} top{topk}（{market}，未来20日收益预测）:")
    for rank, (inst, score) in enumerate(top.items(), 1):
        print(f"  {rank:>2}. {inst}  score={score:.4f}")
    return {"date": str(predict_date)[:10], "topk": topk, "n_stocks": len(top),
            "csv_content": csv_content, "data_calendar_end": cal_lines[-1],
            "ranking_only": True, "rebalance_applied": False,
            "model_fit_asof": saved["fit_asof"],
            "train_end": saved["train"][-1],
            "valid_end": saved["valid"][-1],
            "retrained_today": train_now}



# ===================== 每日定时任务（Modal Cron，云端全自动） =====================
# 部署：  modal secret create github-push GITHUB_TOKEN=<你的PAT>   # 一次性
#         modal deploy modal_qlib_cn_a10g.py                        # 部署（含 cron）
# 停止：  modal app stop <app名> 或 modal delete <app名>
# 说明：  每个 A 股交易日收盘后（北京 20:30）云端自动：下载最新数据 → 训练终审候选
#         → 生成 top20 信号 → 经 GitHub API 直接写入仓库（无需 git 二进制/本地机器）
GITHUB_REPO = "AT2018cow/qlib"
SIGNAL_BRANCH = "main"


@app.function(
    schedule=modal.Cron("30 20 * * 1-5", timezone="Asia/Shanghai"),
    secrets=[modal.Secret.from_name(_GH_SECRET_NAME := "github-push")],
    timeout=2 * 3600,
)
def daily_cron():
    """云端全自动每日信号：daily_standalone 训练 → GitHub API 提交（不依赖本地机器）。
    需 Modal Secret `github-push`（含 GITHUB_TOKEN，对 fork 仓库 Contents 读写权限的 PAT）。"""
    import base64
    import os

    import requests

    res = daily_standalone.remote(topk=20, nd=2, market="csi1000")
    print(f"[cron] 信号日期 {res['date']}（数据日历至 {res['data_calendar_end']}）")
    if res["date"] != res["data_calendar_end"]:
        raise RuntimeError("Signal date does not match last data calendar date")
    from datetime import datetime
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    if res["date"] != today:
        raise RuntimeError(f"Today={today} but latest dataset={res['date']}; "
                           "possible exchange holiday or stale release; do not publish old ranking")

    path = f"results/signals/{res['date']}_top20_lgb158.csv"
    token = os.environ["GITHUB_TOKEN"]
    headers = {"Authorization": f"Bearer {token}",
               "Accept": "application/vnd.github+json"}
    api = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"

    # 同日重跑 dedup：文件已存在且内容一致则跳过
    exist = requests.get(api, headers=headers, timeout=30)
    if exist.status_code == 200 and base64.b64decode(exist.json()["content"]).decode() == res["csv_content"]:
        print(f"[cron] {path} 已存在且内容一致，跳过（同日重跑）")
        return
    if exist.status_code == 200:
        raise RuntimeError("Same-date signal changed: refusing to rewrite immutable paper-trading record")
    if exist.status_code != 404:
        raise RuntimeError(f"Cannot check existing signal: HTTP {exist.status_code}")
    sha = None

    r = requests.put(api, headers=headers, timeout=30, json={
        "message": f"chore(signal): paper-trading record {res['date']} top20 (cron)",
        "content": base64.b64encode(res["csv_content"].encode()).decode(),
        "branch": SIGNAL_BRANCH,
        **({"sha": sha} if sha else {}),
    })
    if r.status_code in (200, 201):
        print(f"[cron] ✅ 已推送到 GitHub: {path}")
    else:
        raise RuntimeError(f"GitHub push failed: HTTP {r.status_code}: {r.text[:200]}")


# ===================== 第六步：重训频率对比实验（freq 5/20/60） =====================

@app.function(
    volumes={str(VOL_ROOT): vol},
    cpu=8,
    memory=24576,
    timeout=2 * 3600,
    max_containers=8,
)
def freq_window(args: dict):
    """频率实验窗口 worker：在重训日训练（purge 边界）→ 预测并回测执行区间。
    时序与 cron 生产一致：重训日 T 收盘后训练，信号用于 [T+1, T'] 的交易（T'=下一重训日）。"""
    from bisect import bisect_left

    import numpy as np
    import pandas as pd

    import qlib
    from qlib.backtest import backtest as normal_backtest
    from qlib.data.dataset import Dataset
    from qlib.model.base import Model
    from qlib.utils import init_instance_by_config

    from qlib_audit_fixes import last_matured_sample, purge_cfg_splits, read_trading_calendar

    qlib.init(provider_uri=str(DATA_DIR), region="cn", skip_if_reg=True,
              exp_manager={"class": "MLflowExpManager", "module_path": "qlib.workflow.expm",
                           "kwargs": {"uri": f"file:{MLRUNS_DIR}", "default_exp_name": "qlib-cn-freq"}})
    cal = read_trading_calendar(DATA_DIR)
    horizon = args.get("horizon", 20)
    asof_i = bisect_left(cal, args["retrain_asof"])
    # —— 分割构造：configure_asof 的泛化（允许 asof 为历史重训日）——
    valid_end_i = asof_i - horizon - 1                       # valid 末样本标签成熟于 T 前
    valid_start_i = valid_end_i - args.get("valid_sessions", 252) + 1
    if valid_start_i <= 0 or valid_end_i <= 0:
        raise RuntimeError(f"insufficient history at {args['retrain_asof']}")
    valid_start = cal[valid_start_i]
    train_end = last_matured_sample(cal, valid_start, horizon)
    cfg = _load_and_patch_cfg(MODEL_CFG["lgb158"], smoke=False, recent=True, long_train=True,
                              label20=True, topk=args["topk"], nd=args["nd"], market=args["market"])
    dk = cfg["task"]["dataset"]["kwargs"]
    seg = dk["segments"]
    handler = dk["handler"]["kwargs"]
    seg["train"] = ["2016-01-01", train_end]
    seg["valid"] = [valid_start, cal[valid_end_i]]
    seg["test"] = [args["eval_start"], args["eval_end"]]
    handler["start_time"] = "2015-01-01"
    handler["end_time"] = args["eval_end"]                   # 特征必须覆盖整个执行区间
    handler["fit_start_time"] = "2016-01-01"
    handler["fit_end_time"] = train_end
    purge_cfg_splits(cfg, cal, horizon=horizon)               # 硬断言所有跨段边界无泄漏
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
    bench = "SH000852" if args["market"] == "csi1000" else "SH000905"
    pm, _ = normal_backtest(strategy=strategy, executor=executor,
                             start_time=args["eval_start"], end_time=args["eval_end"],
                             account=100000000, benchmark=bench,
                             exchange_kwargs={"limit_threshold": 0.095, "deal_price": "close",
                                              "open_cost": 0.0005, "close_cost": 0.0015, "min_cost": 5})
    rep = pm["1day"][0]
    excess = rep["return"] - rep["bench"] - rep["cost"]
    if excess.empty:
        raise RuntimeError("empty excess series")
    return {"freq": args["freq"], "eval_start": args["eval_start"],
            "daily": [round(float(v), 8) for v in excess.tolist()], "n_days": int(len(excess))}


@app.function(
    volumes={str(VOL_ROOT): vol},
    cpu=4,
    memory=8192,
    timeout=8 * 3600,
)
def freq_driver(freqs="60,20", eval_from="2021-01-04", market="csi1000", topk=20, nd=2):
    """第六步主函数：重训频率对比。区间划分（日历索引，无缝衔接）：
    重训点 i 执行区间 = [cal[i+1], cal[i+freq]]；相邻区间无重叠无遗漏。
    结果存 /vol/freq_experiment/。"""
    import json as _json
    from bisect import bisect_left

    import numpy as np

    from qlib_audit_fixes import read_trading_calendar

    _ensure_data(force=True)   # 确保最新数据（infi Volume 可能是旧版）
    cal = read_trading_calendar(DATA_DIR)
    start_i = bisect_left(cal, eval_from)
    results = {}
    for freq in [int(x) for x in freqs.split(",")]:
        jobs = []
        i = start_i
        while i < len(cal) - 1:
            eval_end = cal[min(i + freq, len(cal) - 1)]
            jobs.append({"freq": freq, "retrain_asof": cal[i], "eval_start": cal[i + 1],
                         "eval_end": eval_end, "market": market, "topk": topk, "nd": nd})
            i += freq
        print(f"[freq] freq={freq}: {len(jobs)} 个重训点（首重训日 {jobs[0]['retrain_asof']}）")
        outs = list(freq_window.map(jobs))
        daily, errs = [], 0
        for o in outs:
            if o.get("daily"):
                daily.extend(o["daily"])
            else:
                errs += 1
        x = np.array(daily)
        cum = np.cumsum(x)
        mdd = float((cum - np.maximum.accumulate(cum)).min())
        results[str(freq)] = {
            "n_retrains": len(jobs), "n_errors": errs, "n_days": len(x),
            "ann_excess": round(float(x.mean() * 238), 4) if len(x) else None,
            "ir": round(float(x.mean() / x.std(ddof=1) * np.sqrt(238)), 3) if len(x) > 1 else None,
            "max_drawdown": round(mdd, 4),
            "positive_days": int((x > 0).sum()), "positive_ratio": round(float((x > 0).mean()), 3) if len(x) else None,
        }
        print(f"[freq] freq={freq}: 年化={results[str(freq)]['ann_excess']} "
              f"IR={results[str(freq)]['ir']} MDD={results[str(freq)]['max_drawdown']} "
              f"正日比例={results[str(freq)]['positive_ratio']} 错误窗口={errs}")
    out = VOL_ROOT / "freq_experiment"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "results.json").open("w") as f:
        _json.dump({"window": f"{eval_from}~{cal[-1]}", "market": market, "results": results}, f, indent=2)
    vol.commit()
    return results


@app.local_entrypoint()
def main(
    model: str = "gru",
    smoke: bool = False,
    recent: bool = False,
    data_only: bool = False,
    force_data: bool = False,
    daily: bool = False,
    topk: int = 20,
    nd: int = 2,
    market: str = "csi1000",
    predict_date: str = None,
    enhanced: bool = False,
    long_train: bool = False,
    fund: bool = False,
    label20: bool = False,
    rolling: bool = False,
    build_fund: bool = False,
    fund_market: str = "csi500",
    fund_max_stocks: int = None,
    ensemble: bool = False,
    topk_bt: int = 50,
    n_drop_bt: int = 2,
    verify: bool = False,
    tune: int = 0,
    tune_horizon: int = 20,
    dual: bool = False,
    best: bool = False,
    p0: bool = False,
    p1: bool = False,
    vcheck: bool = False,
    p2: bool = False,
    batcha: bool = False,
    batchb: bool = False,
    batchc: bool = False,
):
    """入口：
    modal run modal_qlib_cn_a10g.py --model gru [--smoke] [--recent] [--enhanced] [--long-train] [--fund] [--label20] [--force-data] [--data-only]
    modal run modal_qlib_cn_a10g.py --daily [--model lgb360] [--topk 50] [--label20] [--predict-date <最新交易日>]
    modal run modal_qlib_cn_a10g.py --build-fund [--fund-market csi500] [--fund-max-stocks 100]
    modal run modal_qlib_cn_a10g.py --ensemble [--topk-bt 50] [--n-drop-bt 2]
    modal run modal_qlib_cn_a10g.py --tune 40 [--tune-horizon 20]
    modal run modal_qlib_cn_a10g.py --dual
    modal run modal_qlib_cn_a10g.py --best              # 固化最优配置训练+回测
    modal run modal_qlib_cn_a10g.py --best --daily      # 固化最优配置出每日信号
    --best: 固化最优配置 = lgb360 + recent + long_train + label20（见 docs/experiments/），
        可与 --daily 组合出信号；被 --tune/--dual/--ensemble/--build-fund 覆盖时优先执行后者
    --recent: 训练 2021-2024/验证 2025/回测 2026-01~最新交易日，降换手+固定seed（预测未来用）
    --enhanced: csi500 股票池 + early_stop 放宽到 30（V2 增强）
    --long-train: 训练区间延长到 2016-2024（覆盖完整牛熊周期，需配合 --recent）
    --fund: 使用 Alpha158+基本面因子（先跑 --build-fund 生成因子 bin；force 更新数据后需重跑）
    --label20: 标签换成 20 日收益（与基本面因子周期匹配）
    --build-fund: akshare 拉财报/估值，生成基本面因子 bin 到 Volume
    --ensemble: 三模型集成（LGB+GRU+ALSTM，20日标签分数平均）训练+回测+输出信号
    --verify: walk-forward 验证（训练 2016-2023/验证 2024/回测 2025~今 约 1.7 年）
    --tune N: LGB 超参随机搜索 N 组（并行），输出按 Rank IC 排序
    --dual: 多周期融合（20日+60日标签 LGB 分数平均）回测+信号
    --topk N: 自定义持仓数（默认 50）"""
    assert model in MODEL_CFG, f"model 须为 {list(MODEL_CFG)}"
    if p0:
        # P0 证据补强：训练1次完成分组单调性/IC衰减/分月归因/n_drop网格
        # 结果存 /vol/p0_results/，取回：modal volume get qlib-cn-data p0_results ./p0_results
        prepare_data.remote(force=force_data)
        res = p0_diagnostics.remote()
        import json as _json

        print(_json.dumps(res, indent=2, ensure_ascii=False))
        return
    if p1:
        # P1 进攻批次：双变体融合 / 40日标签 / 组合构造（topk网格+分数加权）
        # 结果存 /vol/p1_results/，取回：modal volume get qlib-cn-data p1_results ./p1_results
        prepare_data.remote(force=force_data)
        res = p1_diagnostics.remote()
        import json as _json

        print(_json.dumps(res, indent=2, ensure_ascii=False))
        return
    if batcha:
        # 批次A 粗筛：topk×n_drop 耦合 + 股票池
        prepare_data.remote(force=force_data, skip_health=True)
        res = batch_a.remote()
        import json as _json

        print(_json.dumps(res, indent=2, ensure_ascii=False))
        return
    if batchb:
        # 批次B：训练起点 + 窗口模式 + LGB 超参快搜
        prepare_data.remote(force=force_data, skip_health=True)
        res = batch_b.remote()
        import json as _json

        print(_json.dumps(res, indent=2, ensure_ascii=False))
        return
    if batchc:
        # 批次C 终审：两个候选 × 5年滚动（2021-2026，23 窗口，8 并行）
        prepare_data.remote(force=force_data, skip_health=True)
        res1 = batch_c.remote(market="csi1000", bench="SH000852", topk=20, nd=2, tag="c1000_nd2")
        res2 = batch_c.remote(market="csi500", bench="SH000905", topk=20, nd=3, tag="c500_nd3")
        import json as _json

        print("[batchC] === csi1000 主候选 ===")
        print(_json.dumps({k: v for k, v in res1.items() if k != "windows"}, indent=2, ensure_ascii=False))
        print("[batchC] === csi500 对照 ===")
        print(_json.dumps({k: v for k, v in res2.items() if k != "windows"}, indent=2, ensure_ascii=False))
        return
    if p2:
        # P2-13 滚动 walk-forward + 21个月分月归因
        prepare_data.remote(force=force_data, skip_health=True)
        res = p2_rolling.remote()
        import json as _json

        print(_json.dumps(res, indent=2, ensure_ascii=False))
        return
    if vcheck:
        # 版本敏感性验证：数据diff + 2×2 特征矩阵（两数据包统一回测区间）
        prepare_data.remote(force=force_data, skip_health=True)
        res_0911 = version_check_0911.remote()
        res_0916 = version_check_0916.remote()
        import json as _json

        print("[vcheck] === 09-11 包 ===")
        print(_json.dumps(res_0911, indent=2, ensure_ascii=False))
        print("[vcheck] === 09-16 包 ===")
        print(_json.dumps(res_0916, indent=2, ensure_ascii=False))
        print("2x2 矩阵（有成本年化超额，区间 2026-01-01~09-11，n_drop=3）：")
        for k in ["lgb158", "lgb360"]:
            print(f"  {k}: 09-11包={res_0911['matrix_0911'][k]['excess_with_cost_annual']}  "
                  f"09-16包={res_0916['matrix_0916'][f'{k}_ndrop3']['excess_with_cost_annual']}")
        return
    if best:
        # 终审固化配置（2026-09-18，见 docs/experiments/05-final-audit.md）：
        # LGB + Alpha158 + 20日标签 + 2016起 expanding + csi1000 + top20 + nd2（唯一实盘候选，滚动 +7.5%）
        # 注意：--daily 时 topk/nd/market 生效；--best 单独跑完整训练+回测时回测参数同此
        model, recent, long_train, label20 = "lgb158", True, True, True
        topk, nd, market = 20, 2, (market if market else "csi1000")
        print(f"[best] 终审固化配置：lgb158 + Alpha158 + 20日标签 + {market} + top20 + nd2")
    if build_fund:
        res = build_fund_factors.remote(market=fund_market, max_stocks=fund_max_stocks)
        print(res)
        return
    if tune > 0:
        # 纯 CPU 流程：不调用 check_gpu（避免挂载 GPU 浪费）
        # 结果打印在本地，持久化到 Volume 请用云端入口 tune_driver（本地无 /vol 路径）
        prepare_data.remote(force=force_data)
        rng = __import__("random").Random(42)
        params_list = [{}] + [_sample_params(rng) for _ in range(tune - 1)]
        results = list(tune_one.map([dict(p) for p in params_list], [tune_horizon] * tune))
        results.sort(key=lambda r: r["excess_with_cost_annual"], reverse=True)
        print(f"[tune] === Top 10（共 {tune} 组）===")
        for i, r in enumerate(results[:10], 1):
            print(f"[tune] {i}. net_annual={r['excess_with_cost_annual']:.4f} "
                  f"rank_ic={r['rank_ic']:.4f} {r['params']}")
        print(f"[tune] 验证期候选（需独立样本外复验）: "
              f"net_annual={results[0]['excess_with_cost_annual']:.4f} "
              f"rank_ic={results[0]['rank_ic']:.4f} params={results[0]['params']}")
        return
    if dual:
        # 纯 LGB，CPU 容器
        prepare_data.remote(force=force_data)
        res = dual_horizon.remote(topk=topk_bt, n_drop=n_drop_bt)
        print(res)
        return
    if ensemble:
        # 含 GRU/ALSTM，需要 GPU
        check_gpu.remote()
        prepare_data.remote(force=force_data)
        res = train_ensemble.remote(topk=topk_bt, n_drop=n_drop_bt)
        print(res)
        return
    if data_only:
        prepare_data.remote(force=force_data)
        return
    if daily:
        # --best --daily → 自包含单容器：每次全量下载最新数据（必然最新，--force-data 不再需要），
        # 训练终审候选 → 信号返回 → 本地入库+git 推送。Volume 仅供研究批（并行 map）使用。
        res = daily_standalone.remote(topk=topk, nd=nd, market=market)
        print(f"[daily] 数据日历至: {res['data_calendar_end']}  信号日期: {res['date']}")
        _save_and_commit_signal(res)
        return
    prepare_data.remote(force=force_data)
    if model in LGB_MODELS:
        # LGB 系训练：CPU 容器，不挂 GPU
        res = train_cpu.remote(model=model, smoke=smoke, recent=recent, enhanced=enhanced, long_train=long_train, fund=fund, label20=label20, rolling=rolling, verify=verify, topk=topk, market=market, nd=nd)
    else:
        check_gpu.remote()
        res = train.remote(model=model, smoke=smoke, recent=recent, enhanced=enhanced, long_train=long_train, fund=fund, label20=label20, rolling=rolling, verify=verify, topk=topk, market=market, nd=nd)
    print(res)
    print("取回 mlruns：modal volume get qlib-cn-data /vol/mlruns ./mlruns_out")
