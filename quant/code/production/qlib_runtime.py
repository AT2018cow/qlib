"""Modal-free Qlib runtime configuration shared by signal and website code.

This module contains the model YAML patching, model paths, trading-calendar
helper, and CPU thread count.  Keeping it out of the Modal launcher means
resource/decorator/schedule edits do not enter the website semantic identity.
"""

from __future__ import annotations

from pathlib import Path

from qlib_live_retrain import apply_lgb_reproducibility
from qlib_audit_fixes import read_trading_calendar, purge_cfg_splits
from board_rules import EW_BENCH, POOL_BOARDS, build_custom_instruments, star_chn_backtest_guard
from board_execution import research_exchange

DATA_DIR = Path("/vol/cn_data")
MLRUNS_DIR = Path("/vol/mlruns")
CPU_COUNT = 8

MODEL_CFG = {
    "gru": "/root/qlib/examples/benchmarks/GRU/workflow_config_gru_Alpha158.yaml",
    "alstm": "/root/qlib/examples/benchmarks/ALSTM/workflow_config_alstm_Alpha158.yaml",
    "lgb360": "/root/qlib/examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha360_csi500.yaml",
    "lgb158": "/root/qlib/examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158_csi500.yaml",
}


def latest_trading_day(data_dir=None) -> str:
    """读取数据日历的最新交易日（YYYY-MM-DD）。data_dir=None 时用 Volume 路径；
    standalone 模式必须传容器本地解压目录（否则读到不存在的 Volume → 兜底旧日期）。"""
    cal_file = (data_dir or DATA_DIR) / "calendars" / "day.txt"
    if cal_file.exists():
        lines = cal_file.read_text().strip().splitlines()
        if lines:
            return lines[-1]
    return "2026-09-11"


def load_and_patch_cfg(
    yaml_path: str,
    smoke: bool,
    recent: bool = False,
    enhanced: bool = False,
    long_train: bool = False,
    fund: bool = False,
    label20: bool = False,
    label40: bool = False,
    label60: bool = False,
    rolling: bool = False,
    verify: bool = False,
    topk: int = None,
    market: str = None,
    nd: int = None,
    provider_dir: str = None,
    csi1000_profile: str = None,
    data_dir: str = None,
    mlruns_dir: str = None,
) -> dict:
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
    cfg.setdefault("qlib_init", {})["provider_uri"] = str(provider_dir) if provider_dir else str(data_dir or DATA_DIR)
    cfg["qlib_init"]["region"] = "cn"
    # 2) mlflow 落到 Volume，否则容器退出即丢
    cfg["qlib_init"]["exp_manager"] = {
        "class": "MLflowExpManager",
        "module_path": "qlib.workflow.expm",
        "kwargs": {"uri": f"file:{mlruns_dir or MLRUNS_DIR}", "default_exp_name": "qlib-cn-daily"},
    }
    # 3) LightGBM 统一使用 canonical num_threads，并启用共享的可复现训练策略。
    try:
        _model_kw = cfg["task"]["model"]["kwargs"]
        if "num_threads" in _model_kw or cfg["task"]["model"].get("class") == "LGBModel":
            _model_kw["num_threads"] = CPU_COUNT
            _model_kw.pop("n_jobs", None)
        apply_lgb_reproducibility(cfg)
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
        END = latest_trading_day(Path(provider_dir) if provider_dir else (Path(data_dir) if data_dir else None))
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
        if market in EW_BENCH:
            # 自定义池（star_chn 合并 / chinext / star 单板）：从 all.txt 前缀过滤生成 instruments 文件。
            # 训练用池文件、基准与回测阈值全部 fail-closed（数据缺失显式报错而非静默退化）。
            provider_root = Path(provider_dir) if provider_dir else (Path(data_dir) if data_dir else DATA_DIR)
            all_txt = provider_root / "instruments" / "all.txt"
            pool_txt = provider_root / "instruments" / f"{market}.txt"
            stats = build_custom_instruments(all_txt, pool_txt, boards=POOL_BOARDS[market])
            print(f"[cfg] 自定义池 {market} 已生成: {stats}")
            _bench = EW_BENCH[market]
            features_dir = provider_root / "features"
            if not features_dir.is_dir():
                raise FileNotFoundError(f"{market} 数据不完整：features 目录缺失 {features_dir}")
            bench_bin = features_dir / _bench.lower() / "close.day.bin"
            if not bench_bin.is_file():
                raise FileNotFoundError(
                    f"{market} 基准 {_bench} 无数据（找不到 {bench_bin}）；"
                    "先跑 modal run modal_qlib_cn_a10g.py::build_star_chn_bench 构造等权合成基准"
                )
            dh = cfg["task"]["dataset"]["kwargs"]["handler"]["kwargs"]
            dh["instruments"] = market
            cfg["market"] = market
            cfg["benchmark"] = _bench
            cfg["port_analysis_config"]["backtest"]["benchmark"] = _bench
            # 池内全部为 ±20% 板块（见 board_rules），回测窗口必须不跨创业板改革日。
            # 直接的回测执行统一走 board_execution.research_exchange()（逐股掩码 + T+1 开盘价），
            # 此处不再设置标量 limit_threshold。
            bt_start = cfg["port_analysis_config"]["backtest"]["start_time"]
            star_chn_backtest_guard(bt_start)
        else:
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
            "kwargs": {
                "signal": "<PRED>",
                "topk": topk if topk is not None else 50,
                "n_drop": nd if nd is not None else 2,
                "rebalance_days": 20,
            },
        }
    # A sample's Ref(...,-h) label must mature before the NEXT stage starts.
    # This also purges the validation tail used by LightGBM early stopping.
    if recent:
        _cal = read_trading_calendar(provider_dir or data_dir or DATA_DIR)
        purge_cfg_splits(cfg, _cal)
    if smoke and enhanced:
        _model_kw = cfg["task"]["model"]["kwargs"]
        if "early_stop" in _model_kw:
            _model_kw["early_stop"] = 2
    # Wrapper execution protocol: use Qlib's supported exchange= config hook
    # to instantiate BoardAwareExchange without modifying Qlib core.
    if "port_analysis_config" in cfg:
        _bt = cfg["port_analysis_config"]["backtest"]
        _codes = cfg["task"]["dataset"]["kwargs"]["handler"]["kwargs"].get("instruments", "all")
        _bt["exchange_kwargs"] = research_exchange(_bt["start_time"], _bt["end_time"], codes=_codes)
        cfg["port_analysis_config"]["strategy"]["kwargs"]["forbid_all_trade_at_limit"] = False
    # Apply production CSI1000 profiles last so generic research patches cannot
    # silently overwrite frozen Stage-B model semantics.  Research callers that
    # omit csi1000_profile retain their historical behavior.
    if csi1000_profile is not None:
        if market not in ("csi1000", "csi500", "csi300"):
            raise ValueError("csi1000_profile is valid only for market in ('csi1000', 'csi500', 'csi300')")
        from csi1000_production_config import apply_csi1000_model_profile

        cfg["_csi1000_production_manifest"] = apply_csi1000_model_profile(cfg, csi1000_profile)

    return cfg


def cn_trading_calendar():
    """akshare 全年 A 股交易日历（含未来日期，到年末）。返回 {"YYYY-MM-DD"} 集合，失败抛异常。

    数据自带的 day.txt 日历只含已发生的交易日，无法判断"今天是否交易日"；
    本函数用 akshare 的 Sina 交易日历（含未来）补足。已对照 2026-09 实况验证：
    09-25（中秋）不在、09-28/09-24 在、国庆 10-01~10-07 不在、10-08 复市。
    重试 2 次（间隔 10 秒）：akshare/Sina 间歇断连常见，单次失败即降级太敏感——
    降级会让 gate 前移失效（节假日也要全量跑）并进入 fail-open 发布口径。
    """
    import time

    import akshare as ak

    last_err = None
    for attempt in (1, 2):
        try:
            cal_df = ak.tool_trade_date_hist_sina()
            break
        except Exception as e:  # pylint: disable=W0703
            last_err = e
            if attempt == 1:
                print(f"[calendar] 拉取失败（第 1 次），10 秒后重试: {e}")
                time.sleep(10)
    else:
        raise RuntimeError(f"交易日历两次拉取均失败: {last_err}") from last_err
    cal = set()
    for d in cal_df.iloc[:, 0]:
        try:
            cal.add(d.strftime("%Y-%m-%d"))
        except AttributeError:
            cal.add(str(d)[:10])
    return cal
