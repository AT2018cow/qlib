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


def _load_and_patch_cfg(yaml_path: str, smoke: bool, recent: bool = False, enhanced: bool = False, long_train: bool = False) -> dict:
    """读 bundled yaml，打上 Modal 路径补丁。不改仓库原文件。

    recent=True 时把整套数据区间前移到 2026 年（训练 2021-2024 / 验证 2025 /
    回测 2026-01~今），并降换手（n_drop 5→2）+ 固定 seed，用于"预测未来"场景。
    enhanced=True 时：股票池 csi300→csi500（样本更多、QLib 评测 IC 普遍更高）、
    早停放宽 early_stop 10→30（让模型真正训完），benchmark 换 SH000905。
    long_train=True（需配合 recent）：训练区间延长到 2016-2024，覆盖完整牛熊周期。
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
    # 5) 近期模式：训练/验证/回测全部前移到 2026 年（配合 chenditc 每日更新数据）
    if recent:
        dh = cfg["task"]["dataset"]["kwargs"]["handler"]["kwargs"]
        train_start, fit_start = ("2016-01-01", "2016-01-01") if long_train else ("2021-01-01", "2021-01-01")
        dh["start_time"] = "2015-01-01" if long_train else "2020-01-01"  # 提前一年保证 Alpha158 60日窗口
        dh["end_time"] = "2026-09-11"
        dh["fit_start_time"] = fit_start
        dh["fit_end_time"] = "2024-12-31"
        segments = cfg["task"]["dataset"]["kwargs"]["segments"]
        segments["train"] = [train_start, "2024-12-31"]
        segments["valid"] = ["2025-01-01", "2025-12-31"]
        segments["test"] = ["2026-01-01", "2026-09-11"]
        bt = cfg["port_analysis_config"]["backtest"]
        bt["start_time"] = "2026-01-01"
        bt["end_time"] = "2026-09-11"
        # 降换手（n_drop 5→2）减少交易成本 + 固定 seed 保证可复现
        cfg["port_analysis_config"]["strategy"]["kwargs"]["n_drop"] = 2
        try:
            cfg["task"]["model"]["kwargs"]["seed"] = 0
        except KeyError:
            pass
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
    return cfg


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


@app.function(
    volumes={str(VOL_ROOT): vol},
    cpu=CPU_COUNT,
    memory=32768,
    gpu="A10G",
    timeout=12 * 3600,
)
def train(model: str = "gru", smoke: bool = True, recent: bool = False, enhanced: bool = False, long_train: bool = False, experiment_name: str = "qlib-cn-daily-a10g"):
    import torch

    import qlib
    from qlib.model.trainer import task_train

    assert model in MODEL_CFG, f"model 须为 {list(MODEL_CFG)}"
    assert torch.cuda.is_available(), "GPU 未生效，先跑 check_gpu 排查 Image"
    print(f"[train] {model} smoke={smoke} recent={recent} enhanced={enhanced} long_train={long_train} device={torch.cuda.get_device_name(0)}")

    _ensure_data()
    cfg = _load_and_patch_cfg(MODEL_CFG[model], smoke=smoke, recent=recent, enhanced=enhanced, long_train=long_train)
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
    timeout=8 * 3600,
)
def daily_signal(model: str = "gru", topk: int = 50, predict_date: str = None, enhanced: bool = False, long_train: bool = False):
    """每日信号闭环：recent 配置训练 → predict 全部 test 日 → 输出最新交易日 top-k 候选股。

    - 分数含义：对"未来 2 个交易日收益率"的预测（label = Ref($close,-2)/Ref($close,-1)-1）。
    - 停牌股在 TSDatasetH 中无样本，天然被排除。
    - 结果存 /vol/signals/{date}_top{topk}_{model}.csv
    """
    import pandas as pd

    import qlib
    import torch
    from qlib.data.dataset import Dataset
    from qlib.model.base import Model
    from qlib.utils import init_instance_by_config

    assert model in MODEL_CFG, f"model 须为 {list(MODEL_CFG)}"
    assert torch.cuda.is_available(), "GPU 未生效，先跑 check_gpu 排查 Image"
    _ensure_data()
    cfg = _load_and_patch_cfg(MODEL_CFG[model], smoke=False, recent=True, enhanced=enhanced, long_train=long_train)
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

    # 过滤当日涨/跌停（≥9.5%）：涨停买不进、跌停卖不出，剔除避免给不可交易信号
    from qlib.data import D

    day_df = D.features(
        [str(x) for x in day.index],
        ["$open", "$close"],
        start_time=predict_date,
        end_time=predict_date,
        freq="day",
    )
    if len(day_df) > 0:
        day_ret = (day_df["$close"] / day_df["$open"] - 1).dropna()
        tradable = day.index[~day.index.isin(day_ret.index[((day_ret >= 0.095) | (day_ret <= -0.095))])]
        day = day.loc[tradable]
        top = day.sort_values(ascending=False).head(topk)
        print(f"[signal] 已剔除 {len(pred.loc[predict_date].dropna()) - len(day)} 只涨/跌停股")

    print(f"[signal] {predict_date} top{topk}（分数=未来2日收益率预测，越高越看好）:")
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
):
    """入口：
    modal run modal_qlib_cn_a10g.py --model gru [--smoke] [--recent] [--enhanced] [--long-train] [--force-data] [--data-only]
    modal run modal_qlib_cn_a10g.py --daily [--model gru] [--topk 50] [--predict-date 2026-09-11] [--enhanced] [--long-train]
    --recent: 训练 2021-2024/验证 2025/回测 2026-01~今，降换手+固定seed（预测未来用）
    --enhanced: csi500 股票池 + early_stop 放宽到 30（V2 增强）
    --long-train: 训练区间延长到 2016-2024（覆盖完整牛熊周期，需配合 --recent）
    --daily: 每日信号闭环，输出最新交易日 top-k 候选股（含涨跌停过滤）"""
    assert model in MODEL_CFG, f"model 须为 {list(MODEL_CFG)}"
    if data_only:
        prepare_data.remote(force=force_data)
        return
    check_gpu.remote()
    prepare_data.remote(force=force_data)
    if daily:
        res = daily_signal.remote(
            model=model, topk=topk, predict_date=predict_date, enhanced=enhanced, long_train=long_train
        )
        print(res)
        print(f"取回信号：modal volume get qlib-cn-data signals ./signals_out")
        return
    res = train.remote(model=model, smoke=smoke, recent=recent, enhanced=enhanced, long_train=long_train)
    print(res)
    print("取回 mlruns：modal volume get qlib-cn-data /vol/mlruns ./mlruns_out")
