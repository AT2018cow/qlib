"""Canonical portfolio performance metrics for Qlib backtest reports.

Qlib's report columns have important semantics:
- return: portfolio return before transaction cost.
- cost: transaction cost/slippage rate for the bar.
- account: true end-of-bar account value after costs.
- bench: benchmark return.

This module centralizes the repository's production/research metrics so individual
experiments do not mix arithmetic annualization with compounded portfolio risk.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

DEFAULT_ANNUAL_SESSIONS = 238
CALENDAR_DAYS_PER_YEAR = 365.2425


def _finite_float(value: Any):
    value = float(value)
    return value if math.isfinite(value) else None


def _max_drawdown_from_nav(nav: pd.Series) -> float:
    """Maximum percentage drawdown, including the initial NAV=1 peak."""
    values = nav.to_numpy(dtype=float)
    if values.size == 0:
        raise ValueError("empty NAV")
    if not np.all(np.isfinite(values)):
        raise ValueError("NAV contains non-finite values")
    if np.any(values <= 0):
        raise ValueError("NAV must stay positive")
    peaks = np.maximum.accumulate(np.r_[1.0, values])[1:]
    return float(np.min(values / peaks - 1.0))


def _elapsed_years(index: pd.Index, backtest_start=None) -> float:
    if len(index) == 0:
        raise ValueError("empty report")
    end = pd.Timestamp(index[-1])
    start = pd.Timestamp(backtest_start) if backtest_start is not None else pd.Timestamp(index[0])
    if end < start:
        raise ValueError("backtest end precedes start")
    days = (end - start).total_seconds() / 86400.0
    if days <= 0:
        days = 1.0
    return days / CALENDAR_DAYS_PER_YEAR


def portfolio_performance(
    report: pd.DataFrame,
    *,
    initial_cash: float = 100_000_000,
    backtest_start=None,
    annual_sessions: int = DEFAULT_ANNUAL_SESSIONS,
    rf_annual: float = 0.0,
) -> dict:
    """Return compounded portfolio/benchmark performance from a Qlib report.

    CAGR and MaxDD use the after-cost account/NAV path. Sharpe uses after-cost
    daily strategy returns. Information ratio uses after-cost active returns.

    account_return_max_error cross-checks the Qlib account-value path against
    return - cost and should normally be close to floating-point noise.
    """
    required = {"return", "cost", "bench"}
    missing = required - set(report.columns)
    if missing:
        raise ValueError(f"report missing columns: {sorted(missing)}")
    if initial_cash <= 0:
        raise ValueError("initial_cash must be positive")
    if annual_sessions <= 0:
        raise ValueError("annual_sessions must be positive")
    if rf_annual <= -1:
        raise ValueError("rf_annual must be greater than -100%")

    rep = report.sort_index().copy()
    if rep.index.has_duplicates:
        raise ValueError("report index contains duplicates")

    aligned = rep[["return", "cost", "bench"]].astype(float).dropna()
    if aligned.empty:
        raise ValueError("report contains no complete return/cost/bench rows")

    net_ret = aligned["return"] - aligned["cost"]
    bench_ret = aligned["bench"]
    if (net_ret <= -1).any():
        raise ValueError("strategy return <= -100%")
    if (bench_ret <= -1).any():
        raise ValueError("benchmark return <= -100%")

    if 'account' in rep.columns:
        account = rep["account"].astype(float).reindex(aligned.index)
        if account.isna().any() or (account <= 0).any():
            raise ValueError("invalid Qlib account path")
        strategy_nav = account / float(initial_cash)
        implied = account.pct_change()
        implied.iloc[0] = account.iloc[0] / float(initial_cash) - 1.0
        account_error = _finite_float((implied - net_ret).abs().max())
    else:
        strategy_nav = (1.0 + net_ret).cumprod()
        account_error = None

    bench_nav = (1.0 + bench_ret).cumprod()
    relative_nav = strategy_nav / bench_nav

    years = _elapsed_years(aligned.index, backtest_start=backtest_start)
    strategy_total = float(strategy_nav.iloc[-1] - 1.0)
    benchmark_total = float(bench_nav.iloc[-1] - 1.0)
    relative_total = float(relative_nav.iloc[-1] - 1.0)
    strategy_cagr = float(strategy_nav.iloc[-1] ** (1.0 / years) - 1.0)
    benchmark_cagr = float(bench_nav.iloc[-1] ** (1.0 / years) - 1.0)
    relative_cagr = float(relative_nav.iloc[-1] ** (1.0 / years) - 1.0)

    rf_daily = (1.0 + float(rf_annual)) ** (1.0 / annual_sessions) - 1.0
    sharpe_input = net_ret - rf_daily
    sharpe_std = float(sharpe_input.std(ddof=1))
    sharpe = (
        float(sharpe_input.mean() / sharpe_std * np.sqrt(annual_sessions))
        if len(sharpe_input) > 1 and sharpe_std > 0
        else None
    )

    active_ret = net_ret - bench_ret
    active_std = float(active_ret.std(ddof=1))
    information_ratio = (
        float(active_ret.mean() / active_std * np.sqrt(annual_sessions))
        if len(active_ret) > 1 and active_std > 0
        else None
    )
    annual_volatility = (
        float(net_ret.std(ddof=1) * np.sqrt(annual_sessions))
        if len(net_ret) > 1
        else None
    )

    return {
        "metric_version": "portfolio_compound_v1",
        "start": str(pd.Timestamp(aligned.index[0]).date()),
        "end": str(pd.Timestamp(aligned.index[-1]).date()),
        "years": round(float(years), 6),
        "n_days": int(len(aligned)),
        "strategy_total_return": round(strategy_total, 6),
        "strategy_cagr": round(strategy_cagr, 6),
        "benchmark_total_return": round(benchmark_total, 6),
        "benchmark_cagr": round(benchmark_cagr, 6),
        "relative_total_return": round(relative_total, 6),
        "relative_excess_cagr": round(relative_cagr, 6),
        "strategy_max_drawdown": round(_max_drawdown_from_nav(strategy_nav), 6),
        "benchmark_max_drawdown": round(_max_drawdown_from_nav(bench_nav), 6),
        "relative_max_drawdown": round(_max_drawdown_from_nav(relative_nav), 6),
        "sharpe": None if sharpe is None else round(sharpe, 6),
        "information_ratio": None if information_ratio is None else round(information_ratio, 6),
        "annual_volatility": None if annual_volatility is None else round(annual_volatility, 6),
        "mean_daily_net_return": round(float(net_ret.mean()), 8),
        "mean_daily_active_return": round(float(active_ret.mean()), 8),
        "account_return_max_error": (
            None if account_error is None else round(float(account_error), 12)
        ),
    }
