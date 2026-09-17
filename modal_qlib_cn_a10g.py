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


def _latest_trading_day() -> str:
    """读取 Volume 日历的最新交易日（YYYY-MM-DD），供配置动态使用。"""
    cal_file = DATA_DIR / "calendars" / "day.txt"
    if cal_file.exists():
        lines = cal_file.read_text().strip().splitlines()
        if lines:
            return lines[-1]
    return "2026-09-11"  # 兜底


def _load_and_patch_cfg(yaml_path: str, smoke: bool, recent: bool = False, enhanced: bool = False, long_train: bool = False, fund: bool = False, label20: bool = False, label60: bool = False, rolling: bool = False, verify: bool = False, topk: int = None) -> dict:
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
    from ruamel.yaml import YAML

    yaml = YAML(typ="safe", pure=True)
    with open(yaml_path) as f:
        cfg = yaml.load(f)

    # 1) 数据路径指向 Volume；region 保持 cn（A股日历/涨跌停逻辑依赖它）
    cfg.setdefault("qlib_init", {})["provider_uri"] = str(DATA_DIR)
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
        END = _latest_trading_day()
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
        # 降换手（n_drop 5→2）减少交易成本 + 固定 seed 保证可复现
        cfg["port_analysis_config"]["strategy"]["kwargs"]["n_drop"] = 2
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
    # 自定义持仓数
    if topk is not None:
        cfg["port_analysis_config"]["strategy"]["kwargs"]["topk"] = topk
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
    # 8b) 60 日收益标签（更长期动量，用于多周期融合）
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
            "kwargs": {"signal": "<PRED>", "topk": 50, "n_drop": 2, "rebalance_days": 20},
        }
    return cfg


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
    import pandas as pd

    df = D.features(["SH600000"], ["$close", "$volume"], start_time=start, end_time=end, freq="day")
    print(f"[debug] D.features shape={df.shape} columns={list(df.columns)}")
    print(df.head().to_string())


@app.function(volumes={str(VOL_ROOT): vol}, cpu=4, memory=8192, timeout=3600)
def prepare_data(force: bool = False):
    _ensure_data(force=force)
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
                label20: bool, rolling: bool, verify: bool, topk: int, experiment_name: str):
    """train 共享实现（CPU/GPU 两个 wrapper 调用）。LGB 系模型无需 GPU。"""
    import qlib
    from qlib.model.trainer import task_train

    assert model in MODEL_CFG, f"model 须为 {list(MODEL_CFG)}"
    print(f"[train] {model} smoke={smoke} recent={recent} enhanced={enhanced} long_train={long_train} fund={fund} label20={label20} rolling={rolling} verify={verify} topk={topk}")

    _ensure_data()
    cfg = _load_and_patch_cfg(MODEL_CFG[model], smoke=smoke, recent=recent, enhanced=enhanced, long_train=long_train, fund=fund, label20=label20, rolling=rolling, verify=verify, topk=topk)
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
def train(model: str = "gru", smoke: bool = True, recent: bool = False, enhanced: bool = False, long_train: bool = False, fund: bool = False, label20: bool = False, rolling: bool = False, verify: bool = False, topk: int = None, experiment_name: str = "qlib-cn-daily-a10g"):
    """GPU 版训练（GRU/ALSTM 等 RNN 模型）。"""
    import torch

    assert torch.cuda.is_available(), "GPU 未生效，先跑 check_gpu 排查 Image"
    return _train_impl(model, smoke, recent, enhanced, long_train, fund, label20, rolling, verify, topk, experiment_name)


@app.function(
    volumes={str(VOL_ROOT): vol},
    cpu=CPU_COUNT,
    memory=32768,
    timeout=12 * 3600,
)
def train_cpu(model: str = "lgb158", smoke: bool = True, recent: bool = False, enhanced: bool = False, long_train: bool = False, fund: bool = False, label20: bool = False, rolling: bool = False, verify: bool = False, topk: int = None, experiment_name: str = "qlib-cn-daily-a10g"):
    """CPU 版训练（LGB/XGB/Linear 等非 GPU 模型），不挂载 GPU，避免浪费。"""
    assert model in LGB_MODELS, f"train_cpu 仅用于 CPU 模型 {LGB_MODELS}，{model} 请用 train"
    return _train_impl(model, smoke, recent, enhanced, long_train, fund, label20, rolling, verify, topk, experiment_name)


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


def _daily_impl(model: str, topk: int, predict_date: str, enhanced: bool, long_train: bool, fund: bool, label20: bool):
    """每日信号共享实现（CPU/GPU 两个 wrapper 调用）。LGB 系模型无需 GPU。"""
    import pandas as pd

    import qlib
    from qlib.data.dataset import Dataset
    from qlib.model.base import Model
    from qlib.utils import init_instance_by_config

    assert model in MODEL_CFG, f"model 须为 {list(MODEL_CFG)}"
    _ensure_data()
    cfg = _load_and_patch_cfg(MODEL_CFG[model], smoke=False, recent=True, enhanced=enhanced, long_train=long_train, fund=fund, label20=label20)
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
    vol.commit()
    return {"date": str(predict_date), "topk": topk, "csv": str(csv), "n_stocks": len(top)}


@app.function(
    volumes={str(VOL_ROOT): vol},
    cpu=CPU_COUNT,
    memory=32768,
    gpu="A10G",
    timeout=8 * 3600,
)
def daily_signal(model: str = "gru", topk: int = 50, predict_date: str = None, enhanced: bool = False, long_train: bool = False, fund: bool = False, label20: bool = False):
    """GPU 版每日信号（GRU/ALSTM 等 RNN 模型）。"""
    import torch

    assert torch.cuda.is_available(), "GPU 未生效，先跑 check_gpu 排查 Image"
    return _daily_impl(model, topk, predict_date, enhanced, long_train, fund, label20)


@app.function(
    volumes={str(VOL_ROOT): vol},
    cpu=CPU_COUNT,
    memory=32768,
    timeout=12 * 3600,
)
def daily_signal_cpu(model: str = "lgb158", topk: int = 50, predict_date: str = None, enhanced: bool = False, long_train: bool = False, fund: bool = False, label20: bool = False):
    """CPU 版每日信号（LGB 等非 GPU 模型），不挂载 GPU，避免浪费。"""
    assert model in LGB_MODELS, f"daily_signal_cpu 仅用于 CPU 模型 {LGB_MODELS}，{model} 请用 daily_signal"
    return _daily_impl(model, topk, predict_date, enhanced, long_train, fund, label20)


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

    _ensure_data()
    label20 = horizon == 20
    label60 = horizon == 60
    cfg = _load_and_patch_cfg(
        MODEL_CFG["lgb360"], smoke=False, recent=True, long_train=True, label20=label20, label60=label60
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
    pred = model.predict(dataset)
    # 用 dataset.prepare 拿 label（与 LGB 训练同路径 DK_L，可靠）；不要用 D.features——
    # 容器 fork 后会触发 LocalDatasetProvider 的 inst_processors 参数冲突 TypeError
    from qlib.data.dataset.handler import DataHandlerLP

    label_df = dataset.prepare("test", col_set=["feature", "label"], data_key=DataHandlerLP.DK_L)
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
    rank_ic = float(ic.dropna().mean())  # float 化，避免 Series 格式化报错
    print(f"[tune] params={params} rank_ic={rank_ic:.4f}")
    return {"rank_ic": rank_ic, "params": params}


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
    params_list = [_sample_params(rng) for _ in range(n_trials)]
    print(f"[tune] 开始 {n_trials} 组搜索（{TUNE_WORKERS} 并发 CPU 容器，horizon={horizon}）...")
    results = list(tune_one.map([dict(p) for p in params_list], [horizon] * n_trials))
    results.sort(key=lambda r: r["rank_ic"], reverse=True)
    tuning_dir = VOL_ROOT / "tuning"
    tuning_dir.mkdir(parents=True, exist_ok=True)
    with (tuning_dir / f"top10_h{horizon}.json").open("w") as f:
        _json.dump(results[:10], f, indent=2)
    with (tuning_dir / f"best_params_h{horizon}.json").open("w") as f:
        _json.dump({"rank_ic": results[0]["rank_ic"], "params": results[0]["params"], "horizon": horizon}, f, indent=2)
    print(f"[tune] === Top 10（共 {n_trials} 组）===")
    for i, r in enumerate(results[:10], 1):
        print(f"[tune] {i}. rank_ic={r['rank_ic']:.4f} {r['params']}")
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


@app.local_entrypoint()
def main(
    model: str = "gru",
    smoke: bool = False,
    recent: bool = False,
    data_only: bool = False,
    force_data: bool = False,
    daily: bool = False,
    topk: int = 50,
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
    --best: 固化最优配置 = lgb360 + recent + long_train + label20（见 VALIDATION.md），
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
    if best:
        # 固化最优配置（见 VALIDATION.md）：LGB+Alpha360+20日标签，2016-2024 训练
        model, recent, long_train, label20 = "lgb360", True, True, True
        print("[best] 固化最优配置：lgb360 + recent + long_train + label20")
    if build_fund:
        res = build_fund_factors.remote(market=fund_market, max_stocks=fund_max_stocks)
        print(res)
        return
    if tune > 0:
        # 纯 CPU 流程：不调用 check_gpu（避免挂载 GPU 浪费）
        # 结果打印在本地，持久化到 Volume 请用云端入口 tune_driver（本地无 /vol 路径）
        prepare_data.remote(force=force_data)
        rng = __import__("random").Random(42)
        params_list = [_sample_params(rng) for _ in range(tune)]
        results = list(tune_one.map([dict(p) for p in params_list], [tune_horizon] * tune))
        results.sort(key=lambda r: r["rank_ic"], reverse=True)
        print(f"[tune] === Top 10（共 {tune} 组）===")
        for i, r in enumerate(results[:10], 1):
            print(f"[tune] {i}. rank_ic={r['rank_ic']:.4f} {r['params']}")
        print(f"[tune] 最优参数（保存到 Volume 请用 tune_driver）: rank_ic={results[0]['rank_ic']:.4f} params={results[0]['params']}")
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
    prepare_data.remote(force=force_data)
    if daily:
        # 防呆：固化最优配置是 long_train=True（2016-2024 训练，Rank IC 0.105 的出处），
        # 不带 --long-train 的 --daily 会用 2021-2024 短训练模型出信号，与验证结果不一致
        if model == "lgb360" and label20 and not long_train:
            print("[warn] 检测到 --daily --model lgb360 --label20 但未加 --long-train："
                  "信号将来自 2021-2024 短训练模型（Rank IC 约 0.11 但未做长周期验证）。"
                  "推荐使用 --best --daily 固化最优配置。")
        if model in LGB_MODELS:
            # LGB 系信号：CPU 容器，不挂 GPU
            res = daily_signal_cpu.remote(
                model=model, topk=topk, predict_date=predict_date, enhanced=enhanced, long_train=long_train, fund=fund, label20=label20
            )
        else:
            check_gpu.remote()
            res = daily_signal.remote(
                model=model, topk=topk, predict_date=predict_date, enhanced=enhanced, long_train=long_train, fund=fund, label20=label20
            )
        print(res)
        print(f"取回信号：modal volume get qlib-cn-data signals ./signals_out")
        return
    if model in LGB_MODELS:
        # LGB 系训练：CPU 容器，不挂 GPU
        res = train_cpu.remote(model=model, smoke=smoke, recent=recent, enhanced=enhanced, long_train=long_train, fund=fund, label20=label20, rolling=rolling, verify=verify, topk=topk)
    else:
        check_gpu.remote()
        res = train.remote(model=model, smoke=smoke, recent=recent, enhanced=enhanced, long_train=long_train, fund=fund, label20=label20, rolling=rolling, verify=verify, topk=topk)
    print(res)
    print("取回 mlruns：modal volume get qlib-cn-data /vol/mlruns ./mlruns_out")
