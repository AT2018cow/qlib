#!/usr/bin/env python3
"""Research-only candidate backtest for the current 10-session window.

This module was moved out of the production daily Modal app.  It intentionally
reuses the production image and split/purge helpers, but runs as a separate
Modal app: it never publishes signals, mutates paper state, or deploys the
production app.

Usage (from the repository root):

    .venv/bin/modal run \
      quant/code/research/current_window/current_window_modal.py::current_window_driver

Add ``--decompose`` to run the 2x2 model/construction decomposition, or
``--smoke`` to run only the CSI1000 baseline control.
"""

from __future__ import annotations

import sys
from pathlib import Path

import modal

# Modal mounts the entrypoint at /root while the repository lives at
# /root/qlib.  Locally the repository root is discovered from this file.
_HERE = Path(__file__).resolve()
for _parent in _HERE.parents:
    _candidate = _parent / "quant" / "code" / "production"
    if _candidate.is_dir():
        sys.path.insert(0, str(_candidate))
        break
    _candidate = _parent / "qlib" / "quant" / "code" / "production"
    if _candidate.is_dir():
        sys.path.insert(0, str(_candidate))
        break
for _parent in _HERE.parents:
    for _rel in (
        "quant/code/execution",
        "qlib/quant/code/execution",
        "quant/code/research/stage_ab",
        "qlib/quant/code/research/stage_ab",
    ):
        _candidate = _parent / _rel
        if _candidate.is_dir():
            sys.path.insert(0, str(_candidate))
            break

from modal_qlib_cn_a10g import (  # noqa: E402
    MODEL_CFG,
    _load_and_patch_cfg,
    _load_latest_to_local,
    image,
)

app = modal.App("qlib-current-window-backtest", image=image)


@app.function(
    cpu=8,
    memory=32768,
    timeout=2 * 3600,
    max_containers=4,
    nonpreemptible=True,
)
def current_window_backtest(job: dict):
    """One exact candidate backtest for a short, explicit window.

    Trains once at the signal bar (``eval_start``), then runs Qlib's
    board-aware backtest over ``[eval_start, eval_end]``.  Qlib emits a zero
    return on the anchor bar; it and the matching benchmark bar are dropped so
    every reported day is actually traded (the first is ``eval_start + 1``).
    """
    from bisect import bisect_left

    import qlib
    from qlib.backtest import backtest as normal_backtest
    from qlib.data.dataset import Dataset
    from qlib.model.base import Model
    from qlib.utils import init_instance_by_config

    from board_execution import research_exchange
    from csi1000_production_config import profile_manifest
    from qlib_audit_fixes import (
        last_matured_sample,
        purge_cfg_splits,
        read_trading_calendar,
    )

    market = job["market"]
    profile = job["profile"]
    eval_start = job["eval_start"]
    eval_end = job["eval_end"]
    topk = int(job["topk"])
    nd = int(job["nd"])
    manifest = profile_manifest(profile)
    horizon = int(job.get("horizon") or manifest.get("label_horizon", 20))

    data_dir = Path("/tmp/cn_data")
    if not (data_dir / "calendars" / "day.txt").is_file():
        data_dir = _load_latest_to_local()
    cal = read_trading_calendar(data_dir)
    start_i = bisect_left(cal, eval_start)
    end_i = bisect_left(cal, eval_end)
    if start_i <= 0 or start_i >= len(cal) or cal[start_i] != eval_start:
        raise ValueError(f"eval_start {eval_start} not in provider calendar")
    if end_i >= len(cal) or cal[end_i] != eval_end:
        raise ValueError(f"eval_end {eval_end} not in provider calendar")

    # Production uses the signal bar's close for the next open.  Starting the
    # backtest on the signal bar makes Qlib's first (zero) return the anchor.
    retrain_asof = eval_start
    asof_i = bisect_left(cal, retrain_asof)
    valid_end_i = asof_i - horizon - 1
    valid_start_i = valid_end_i - 252 + 1
    if valid_start_i <= 0 or valid_end_i <= 0:
        raise ValueError(f"insufficient history at {retrain_asof}")
    valid_start = cal[valid_start_i]
    train_end = last_matured_sample(cal, valid_start, horizon)

    qlib.init(
        provider_uri=str(data_dir),
        region="cn",
        skip_if_reg=True,
        exp_manager={
            "class": "MLflowExpManager",
            "module_path": "qlib.workflow.expm",
            "kwargs": {
                "uri": "file:/tmp/mlruns",
                "default_exp_name": "qlib-current-window",
            },
        },
    )
    cfg = _load_and_patch_cfg(
        MODEL_CFG["lgb158"],
        smoke=False,
        recent=True,
        long_train=True,
        label20=True,
        topk=topk,
        nd=nd,
        market=market,
        provider_dir=str(data_dir),
        csi1000_profile=profile,
    )
    seg = cfg["task"]["dataset"]["kwargs"]["segments"]
    handler = cfg["task"]["dataset"]["kwargs"]["handler"]["kwargs"]
    seg["train"] = ["2016-01-01", train_end]
    seg["valid"] = [valid_start, cal[valid_end_i]]
    seg["test"] = [eval_start, eval_end]
    handler["start_time"] = "2015-01-01"
    handler["end_time"] = eval_end
    handler["fit_start_time"] = "2016-01-01"
    handler["fit_end_time"] = train_end
    purge_cfg_splits(cfg, cal, horizon=horizon)

    model = init_instance_by_config(cfg["task"]["model"], accept_types=Model)
    dataset = init_instance_by_config(cfg["task"]["dataset"], accept_types=Dataset)
    model.fit(dataset)
    pred = model.predict(dataset, segment="test")
    if pred.empty:
        raise RuntimeError(f"empty predictions {eval_start}~{eval_end}")

    benchmark = {
        "csi1000": "SH000852",
        "csi500": "SH000905",
        "csi300": "SH000300",
    }[market]
    strategy = {
        "class": "TopkDropoutStrategy",
        "module_path": "qlib.contrib.strategy",
        "kwargs": {
            "signal": pred,
            "topk": topk,
            "n_drop": nd,
            "forbid_all_trade_at_limit": False,
        },
    }
    executor = {
        "class": "SimulatorExecutor",
        "module_path": "qlib.backtest.executor",
        "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True},
    }
    pm, _ = normal_backtest(
        strategy=strategy,
        executor=executor,
        start_time=eval_start,
        end_time=eval_end,
        account=100_000_000,
        benchmark=benchmark,
        exchange_kwargs=research_exchange(eval_start, eval_end, codes=market),
    )
    report = pm["1day"][0]
    raw_ret = report["return"].dropna()
    ret = raw_ret.iloc[1:]
    bench_ret = report["bench"].reindex(raw_ret.index).fillna(0.0).iloc[1:]
    cost = report["cost"].reindex(raw_ret.index).fillna(0.0).iloc[1:]
    cumulative = float((1.0 + ret).prod() - 1.0)
    benchmark_cumulative = float((1.0 + bench_ret).prod() - 1.0)
    return {
        "market": market,
        "profile": profile,
        "topk": topk,
        "nd": nd,
        "horizon": horizon,
        "retrain_asof": retrain_asof,
        "train_end": train_end,
        "eval_start": eval_start,
        "eval_end": eval_end,
        "n_days": int(len(ret)),
        "cumulative_return": cumulative,
        "benchmark_cumulative_return": benchmark_cumulative,
        "excess_cumulative_return": cumulative - benchmark_cumulative,
        "total_cost": float(cost.sum()),
        "final_account": float(report["account"].iloc[-1]),
        "daily_return": [round(float(x), 8) for x in ret.tolist()],
        "daily_benchmark": [round(float(x), 8) for x in bench_ret.tolist()],
    }


@app.function(timeout=3 * 3600, cpu=1, memory=2048)
def current_window_driver(
    eval_start: str = "2026-10-15",
    eval_end: str = "2026-10-29",
    smoke: bool = False,
    decompose: bool = False,
):
    """Fan out the exact short-window candidate backtests across markets."""
    import json as _json

    specs = [
        ("csi1000", "baseline_control", 20, 2),
        ("csi1000", "stage_b_winner", 20, 2),
        ("csi1000", "c98856b4_top10_drop1", 10, 1),
        ("csi300", "baseline_control", 20, 2),
        ("csi300", "59b93929_top10_drop1", 10, 1),
        ("csi500", "baseline_control", 20, 2),
        ("csi500", "raw10d_c98856b4_top10_drop1", 10, 1),
    ]
    jobs = [
        {
            "market": market,
            "profile": profile,
            "topk": topk,
            "nd": nd,
            "eval_start": eval_start,
            "eval_end": eval_end,
        }
        for market, profile, topk, nd in specs
    ]
    if decompose:
        jobs += [
            {
                "market": market,
                "profile": profile,
                "topk": topk,
                "nd": nd,
                "eval_start": eval_start,
                "eval_end": eval_end,
            }
            for market, profile, topk, nd in [
                ("csi1000", "baseline_control", 10, 1),
                ("csi1000", "c98856b4_top10_drop1", 20, 2),
                ("csi300", "baseline_control", 10, 1),
                ("csi300", "59b93929_top10_drop1", 20, 2),
                ("csi500", "baseline_control", 10, 1),
                ("csi500", "raw10d_c98856b4_top10_drop1", 20, 2),
            ]
        ]
    if smoke:
        jobs = jobs[:1]
    results = list(current_window_backtest.map(jobs))
    results.sort(key=lambda x: (x["market"], x["profile"], x["topk"]))
    print(_json.dumps(results, indent=2, ensure_ascii=False))
    return results


@app.function(cpu=8, memory=32768, timeout=2 * 3600)
def default_yaml_ranking(
    market: str = "csi300",
    asof: str = "2026-09-30",
    topn: int = 40,
):
    """Rank with the literal Qlib example YAML (train 2008-2014 / valid 2015-16).

    The test date is moved to 2026-09-30 without retraining, matching a
    "use the default workflow as-is" interpretation.
    """
    from bisect import bisect_left
    import json as _json

    import qlib
    from qlib.data.dataset import Dataset
    from qlib.model.base import Model
    from qlib.utils import init_instance_by_config

    from qlib_audit_fixes import purge_cfg_splits, read_trading_calendar

    data_dir = Path("/tmp/cn_data")
    if not (data_dir / "calendars" / "day.txt").is_file():
        data_dir = _load_latest_to_local()
    cal = read_trading_calendar(data_dir)
    if bisect_left(cal, asof) >= len(cal) or cal[bisect_left(cal, asof)] != asof:
        raise ValueError(f"asof {asof} not in provider calendar")

    qlib.init(
        provider_uri=str(data_dir),
        region="cn",
        skip_if_reg=True,
        exp_manager={
            "class": "MLflowExpManager",
            "module_path": "qlib.workflow.expm",
            "kwargs": {"uri": "file:/tmp/mlruns", "default_exp_name": "qlib-default-yaml"},
        },
    )
    cfg = _load_and_patch_cfg(
        MODEL_CFG["lgb158"],
        smoke=False,
        recent=False,
        long_train=False,
        label20=False,
        topk=7,
        nd=7,
        market=market,
        provider_dir=str(data_dir),
    )
    seg = cfg["task"]["dataset"]["kwargs"]["segments"]
    handler = cfg["task"]["dataset"]["kwargs"]["handler"]["kwargs"]
    seg["train"] = ["2008-01-01", "2014-12-31"]
    seg["valid"] = ["2015-01-01", "2016-12-31"]
    seg["test"] = [asof, asof]
    handler["start_time"] = "2008-01-01"
    handler["end_time"] = asof
    handler["fit_start_time"] = "2008-01-01"
    handler["fit_end_time"] = "2014-12-31"
    purge_cfg_splits(cfg, cal, horizon=2)

    model = init_instance_by_config(cfg["task"]["model"], accept_types=Model)
    dataset = init_instance_by_config(cfg["task"]["dataset"], accept_types=Dataset)
    model.fit(dataset)
    pred = model.predict(dataset, segment="test")
    day = pred.loc[asof].dropna().sort_values(ascending=False).head(topn)
    result = {
        "market": market,
        "asof": asof,
        "label_horizon": 2,
        "train": seg["train"],
        "valid": seg["valid"],
        "top": [
            {"rank": i, "instrument": inst, "score": float(score)} for i, (inst, score) in enumerate(day.items(), 1)
        ],
    }
    print(_json.dumps(result, indent=2, ensure_ascii=False))
    return result


@app.function(cpu=8, memory=32768, timeout=2 * 3600)
def profile_ranking(
    profile: str = "baseline_control",
    market: str = "csi300",
    asof: str = "2026-09-30",
    topn: int = 40,
):
    """Rank names at ``asof`` with one frozen production profile."""
    from bisect import bisect_left
    import json as _json

    import qlib
    from qlib.data.dataset import Dataset
    from qlib.model.base import Model
    from qlib.utils import init_instance_by_config

    from csi1000_production_config import profile_manifest
    from qlib_audit_fixes import last_matured_sample, purge_cfg_splits, read_trading_calendar

    data_dir = Path("/tmp/cn_data")
    if not (data_dir / "calendars" / "day.txt").is_file():
        data_dir = _load_latest_to_local()
    cal = read_trading_calendar(data_dir)
    asof_i = bisect_left(cal, asof)
    if asof_i >= len(cal) or cal[asof_i] != asof:
        raise ValueError(f"asof {asof} not in provider calendar")
    manifest = profile_manifest(profile)
    horizon = int(manifest.get("label_horizon", 20))
    valid_end_i = asof_i - horizon - 1
    valid_start_i = valid_end_i - 252 + 1
    if valid_start_i <= 0:
        raise ValueError("insufficient history")
    valid_start = cal[valid_start_i]
    train_end = last_matured_sample(cal, valid_start, horizon)

    qlib.init(
        provider_uri=str(data_dir),
        region="cn",
        skip_if_reg=True,
        exp_manager={
            "class": "MLflowExpManager",
            "module_path": "qlib.workflow.expm",
            "kwargs": {"uri": "file:/tmp/mlruns", "default_exp_name": "qlib-profile-ranking"},
        },
    )
    cfg = _load_and_patch_cfg(
        MODEL_CFG["lgb158"],
        smoke=False,
        recent=True,
        long_train=True,
        label20=True,
        topk=10,
        nd=1,
        market=market,
        provider_dir=str(data_dir),
        csi1000_profile=profile,
    )
    seg = cfg["task"]["dataset"]["kwargs"]["segments"]
    handler = cfg["task"]["dataset"]["kwargs"]["handler"]["kwargs"]
    seg["train"] = ["2016-01-01", train_end]
    seg["valid"] = [valid_start, cal[valid_end_i]]
    seg["test"] = [asof, asof]
    handler["start_time"] = "2015-01-01"
    handler["end_time"] = asof
    handler["fit_start_time"] = "2016-01-01"
    handler["fit_end_time"] = train_end
    purge_cfg_splits(cfg, cal, horizon=horizon)

    model = init_instance_by_config(cfg["task"]["model"], accept_types=Model)
    dataset = init_instance_by_config(cfg["task"]["dataset"], accept_types=Dataset)
    model.fit(dataset)
    pred = model.predict(dataset, segment="test")
    day = pred.loc[asof].dropna().sort_values(ascending=False).head(topn)
    result = {
        "market": market,
        "profile": profile,
        "asof": asof,
        "label_horizon": horizon,
        "train_end": train_end,
        "valid": [valid_start, cal[valid_end_i]],
        "top": [
            {"rank": i, "instrument": inst, "score": float(score)} for i, (inst, score) in enumerate(day.items(), 1)
        ],
    }
    print(_json.dumps(result, indent=2, ensure_ascii=False))
    return result


@app.function(cpu=8, memory=32768, timeout=2 * 3600)
def default_alpha158_ranking(
    market: str = "csi300",
    asof: str = "2026-09-30",
    topn: int = 40,
):
    """Rank names with the raw Qlib default LGB + Alpha158 config.

    Unlike the production profiles this keeps the YAML's default 2-day label
    and default LightGBM parameters.  It is a diagnostic for the colleague's
    "Qlib Alpha158 default" claim, not a production candidate.
    """
    from bisect import bisect_left
    import json as _json

    import qlib
    from qlib.data.dataset import Dataset
    from qlib.model.base import Model
    from qlib.utils import init_instance_by_config

    from qlib_audit_fixes import last_matured_sample, purge_cfg_splits, read_trading_calendar

    data_dir = Path("/tmp/cn_data")
    if not (data_dir / "calendars" / "day.txt").is_file():
        data_dir = _load_latest_to_local()
    cal = read_trading_calendar(data_dir)
    asof_i = bisect_left(cal, asof)
    if asof_i >= len(cal) or cal[asof_i] != asof:
        raise ValueError(f"asof {asof} not in provider calendar")
    horizon = 2
    valid_end_i = asof_i - horizon - 1
    valid_start_i = valid_end_i - 252 + 1
    if valid_start_i <= 0:
        raise ValueError("insufficient history")
    valid_start = cal[valid_start_i]
    train_end = last_matured_sample(cal, valid_start, horizon)

    qlib.init(
        provider_uri=str(data_dir),
        region="cn",
        skip_if_reg=True,
        exp_manager={
            "class": "MLflowExpManager",
            "module_path": "qlib.workflow.expm",
            "kwargs": {"uri": "file:/tmp/mlruns", "default_exp_name": "qlib-default-diagnostic"},
        },
    )
    cfg = _load_and_patch_cfg(
        MODEL_CFG["lgb158"],
        smoke=False,
        recent=True,
        long_train=True,
        label20=False,
        topk=7,
        nd=7,
        market=market,
        provider_dir=str(data_dir),
    )
    seg = cfg["task"]["dataset"]["kwargs"]["segments"]
    handler = cfg["task"]["dataset"]["kwargs"]["handler"]["kwargs"]
    seg["train"] = ["2016-01-01", train_end]
    seg["valid"] = [valid_start, cal[valid_end_i]]
    seg["test"] = [asof, asof]
    handler["start_time"] = "2015-01-01"
    handler["end_time"] = asof
    handler["fit_start_time"] = "2016-01-01"
    handler["fit_end_time"] = train_end
    purge_cfg_splits(cfg, cal, horizon=horizon)

    model = init_instance_by_config(cfg["task"]["model"], accept_types=Model)
    dataset = init_instance_by_config(cfg["task"]["dataset"], accept_types=Dataset)
    model.fit(dataset)
    pred = model.predict(dataset, segment="test")
    day = pred.loc[asof].dropna().sort_values(ascending=False).head(topn)
    result = {
        "market": market,
        "asof": asof,
        "label_horizon": horizon,
        "train_end": train_end,
        "valid": [valid_start, cal[valid_end_i]],
        "top": [
            {"rank": i, "instrument": inst, "score": float(score)} for i, (inst, score) in enumerate(day.items(), 1)
        ],
    }
    print(_json.dumps(result, indent=2, ensure_ascii=False))
    return result


@app.function(cpu=4, memory=16384, timeout=3600)
def fixed_basket_diagnostic(
    buy_date: str = "2026-10-08",
    sell_date: str = "2026-10-29",
):
    """Equal-weight return of the colleague's reported 2026-10 CSI300 list."""
    import json as _json

    import numpy as np
    import pandas as pd

    import qlib
    from qlib.data import D

    codes = [
        "SZ000651",  # 格力电器
        "SH601166",  # 兴业银行
        "SH601318",  # 中国平安
        "SH601838",  # 成都银行
        "SZ000001",  # 平安银行
        "SH600926",  # 杭州银行
        "SH601919",  # 中远海控
    ]
    data_dir = Path("/tmp/cn_data")
    if not (data_dir / "calendars" / "day.txt").is_file():
        data_dir = _load_latest_to_local()
    qlib.init(provider_uri=str(data_dir), region="cn", skip_if_reg=True)
    df = D.features(
        codes,
        ["$open", "$close", "$factor"],
        start_time=buy_date,
        end_time=sell_date,
        freq="day",
    )
    per_stock = {}
    daily = {}
    for code in codes:
        sub = df.loc[code].dropna()
        if sub.empty:
            raise RuntimeError(f"no price data for {code}")
        entry = float(sub.iloc[0]["$open"]) * float(sub.iloc[0]["$factor"])
        exit_ = float(sub.iloc[-1]["$close"]) * float(sub.iloc[-1]["$factor"])
        per_stock[code] = {
            "entry_open_adjusted": entry,
            "exit_close_adjusted": exit_,
            "return": exit_ / entry - 1.0,
        }
        close_adj = sub["$close"].astype(float) * sub["$factor"].astype(float)
        daily[code] = close_adj.pct_change()
    daily_df = pd.DataFrame(daily)
    # First traded day is open-to-close; later days are close-to-close.
    first = df.loc[(slice(None), pd.Timestamp(buy_date)), :]
    first_ret = {}
    for code in codes:
        row = first.loc[code]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        first_ret[code] = (
            float(row["$close"]) * float(row["$factor"]) / (float(row["$open"]) * float(row["$factor"])) - 1.0
        )
    daily_df.iloc[0] = pd.Series(first_ret)
    ew_daily = daily_df.mean(axis=1)
    cumulative = float((1.0 + ew_daily).prod() - 1.0)
    result = {
        "buy_date": buy_date,
        "sell_date": sell_date,
        "codes": codes,
        "per_stock": per_stock,
        "equal_weight_cumulative_return": cumulative,
        "equal_weight_daily": [round(float(v), 8) for v in ew_daily.tolist()],
        "mean_stock_return": float(np.mean([x["return"] for x in per_stock.values()])),
    }
    print(_json.dumps(result, indent=2, ensure_ascii=False))
    return result


@app.function(timeout=3 * 3600, cpu=1, memory=2048)
def october_csi300_driver(
    eval_start: str = "2026-09-30",
    eval_end: str = "2026-10-29",
):
    """October 2026: the early baseline strategy on the CSI300 universe.

    The first website used ``lgb158 + Alpha158 + 20d + csi1000 +
    Top20/Drop2``.  Here the same model family is applied to CSI300, with
    the current CSI300 profile and two construction controls for attribution.
    """
    import json as _json

    specs = [
        ("baseline_control", 20, 2),
        ("baseline_control", 10, 1),
        ("59b93929_top10_drop1", 10, 1),
        ("59b93929_top10_drop1", 20, 2),
    ]
    jobs = [
        {
            "market": "csi300",
            "profile": profile,
            "topk": topk,
            "nd": nd,
            "eval_start": eval_start,
            "eval_end": eval_end,
        }
        for profile, topk, nd in specs
    ]
    results = list(current_window_backtest.map(jobs))
    results.sort(key=lambda x: (x["profile"], x["topk"], x["nd"]))
    print(_json.dumps(results, indent=2, ensure_ascii=False))
    return results
