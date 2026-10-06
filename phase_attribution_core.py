"""Pure zero-fit attribution helpers for retraining-phase diagnostics."""

from __future__ import annotations

import hashlib
import math

import numpy as np
import pandas as pd


def frame_sha256(frame: pd.DataFrame) -> str:
    """Match freq_experiment._frame_sha256 exactly."""
    text = frame.sort_index().to_csv(
        index=True,
        float_format="%.17g",
        date_format="%Y-%m-%dT%H:%M:%S.%f",
    )
    return hashlib.sha256(text.encode()).hexdigest()


def _compound(series: pd.Series) -> float:
    values = pd.to_numeric(series, errors="raise").astype(float)
    return float(np.prod(1.0 + values.to_numpy()) - 1.0)


def _ratio_return(numerator: pd.Series, denominator: pd.Series) -> float:
    num = pd.to_numeric(numerator, errors="raise").astype(float).to_numpy()
    den = pd.to_numeric(denominator, errors="raise").astype(float).to_numpy()
    if np.any(1.0 + den <= 0.0):
        raise ValueError("denominator return <= -100% in relative-return calculation")
    return float(np.prod((1.0 + num) / (1.0 + den)) - 1.0)


def _annualized(total_return: float, years: float) -> float:
    if years <= 0:
        raise ValueError("years must be positive")
    if total_return <= -1.0:
        raise ValueError("cannot annualize total return <= -100%")
    return float((1.0 + total_return) ** (1.0 / years) - 1.0)


def _validate_report(report: pd.DataFrame) -> pd.DataFrame:
    required = {"return", "cost", "bench"}
    missing = required - set(report.columns)
    if missing:
        raise ValueError(f"report missing required columns: {sorted(missing)}")
    turnover_col = "turnover" if "turnover" in report.columns else (
        "total_turnover" if "total_turnover" in report.columns else None
    )
    if turnover_col is None:
        raise ValueError("report missing turnover/total_turnover")
    out = report.copy()
    out.index = pd.to_datetime(out.index)
    out = out.sort_index()
    if out.index.has_duplicates:
        raise ValueError("report index contains duplicate dates")
    if out.empty:
        raise ValueError("report is empty")
    for col in ("return", "cost", "bench", turnover_col):
        if out[col].isna().any():
            raise ValueError(f"report column contains NaN: {col}")
    out.attrs["turnover_col"] = turnover_col
    return out


def period_attribution(report: pd.DataFrame) -> dict:
    report = _validate_report(report)
    gross = report["return"].astype(float)
    cost = report["cost"].astype(float)
    net = gross - cost
    bench = report["bench"].astype(float)
    turnover_col = report.attrs["turnover_col"]
    turnover = report[turnover_col].astype(float)

    start = report.index[0]
    end = report.index[-1]
    years = max((end - start).days / 365.2425, 1.0 / 365.2425)

    gross_total = _compound(gross)
    net_total = _compound(net)
    bench_total = _compound(bench)
    relative_gross_total = _ratio_return(gross, bench)
    relative_net_total = _ratio_return(net, bench)

    return {
        "start": start.strftime("%Y-%m-%d"),
        "end": end.strftime("%Y-%m-%d"),
        "n_days": int(len(report)),
        "years": round(float(years), 6),
        "gross_strategy_total_return": round(gross_total, 8),
        "net_strategy_total_return": round(net_total, 8),
        "benchmark_total_return": round(bench_total, 8),
        "relative_gross_total_return": round(relative_gross_total, 8),
        "relative_net_total_return": round(relative_net_total, 8),
        "gross_strategy_cagr": round(_annualized(gross_total, years), 8),
        "net_strategy_cagr": round(_annualized(net_total, years), 8),
        "benchmark_cagr": round(_annualized(bench_total, years), 8),
        "relative_gross_cagr": round(_annualized(relative_gross_total, years), 8),
        "relative_net_cagr": round(_annualized(relative_net_total, years), 8),
        "cost_drag_cagr_pp": round(
            (_annualized(gross_total, years) - _annualized(net_total, years)) * 100,
            6,
        ),
        "mean_turnover": round(float(turnover.mean()), 8),
        "total_turnover_sum": round(float(turnover.sum()), 8),
        "total_cost_sum": round(float(cost.sum()), 8),
        "turnover_source": turnover_col,
    }


def annual_attribution(report: pd.DataFrame) -> list[dict]:
    report = _validate_report(report)
    turnover_col = report.attrs["turnover_col"]
    final_year = int(report.index[-1].year)
    final_month = int(report.index[-1].month)
    final_day = int(report.index[-1].day)
    rows = []

    for year, frame in report.groupby(report.index.year):
        gross = frame["return"].astype(float)
        cost = frame["cost"].astype(float)
        net = gross - cost
        bench = frame["bench"].astype(float)
        turnover = frame[turnover_col].astype(float)
        gross_ret = _compound(gross)
        net_ret = _compound(net)
        bench_ret = _compound(bench)
        rel_gross = _ratio_return(gross, bench)
        rel_net = _ratio_return(net, bench)
        relative_log_return = float(
            np.log1p(net.to_numpy()).sum() - np.log1p(bench.to_numpy()).sum()
        )
        rows.append(
            {
                "year": int(year),
                "start": frame.index[0].strftime("%Y-%m-%d"),
                "end": frame.index[-1].strftime("%Y-%m-%d"),
                "n_days": int(len(frame)),
                "partial_year": bool(
                    int(year) == final_year and (final_month, final_day) < (12, 20)
                ),
                "gross_strategy_return": round(gross_ret, 8),
                "net_strategy_return": round(net_ret, 8),
                "benchmark_return": round(bench_ret, 8),
                "relative_gross_return": round(rel_gross, 8),
                "relative_net_return": round(rel_net, 8),
                "relative_log_return": round(relative_log_return, 8),
                "mean_turnover": round(float(turnover.mean()), 8),
                "turnover_sum": round(float(turnover.sum()), 8),
                "cost_sum": round(float(cost.sum()), 8),
            }
        )
    return rows


def _execution_dates_for_retrains(
    report_index: pd.DatetimeIndex, retrain_dates: list[str]
) -> list[pd.Timestamp]:
    idx = pd.DatetimeIndex(report_index).sort_values()
    execution_dates = []
    for value in retrain_dates:
        retrain = pd.Timestamp(value)
        # A retrain well before the retained report window already executed
        # before attribution starts. Do not map it spuriously to report day 1.
        if retrain < idx[0] and (idx[0] - retrain).days > 3:
            continue
        pos = int(idx.searchsorted(retrain, side="right"))
        if pos < len(idx):
            execution_dates.append(idx[pos])
    return sorted(set(execution_dates))


def retrain_event_study(
    report: pd.DataFrame, retrain_dates: list[str], window: int = 5
) -> dict:
    if window < 1:
        raise ValueError("window must be >= 1")
    report = _validate_report(report)
    turnover_col = report.attrs["turnover_col"]
    idx = pd.DatetimeIndex(report.index)
    execution_dates = _execution_dates_for_retrains(idx, retrain_dates)
    if not execution_dates:
        raise ValueError("no retrain events overlap report window")

    by_offset = []
    event_positions = set()
    for offset in range(window):
        values_turnover = []
        values_cost = []
        dates = []
        for event_date in execution_dates:
            start_pos = int(idx.get_loc(event_date))
            pos = start_pos + offset
            if pos >= len(idx):
                continue
            event_positions.add(pos)
            dates.append(idx[pos])
            values_turnover.append(float(report.iloc[pos][turnover_col]))
            values_cost.append(float(report.iloc[pos]["cost"]))
        by_offset.append(
            {
                "offset_sessions_after_retrain": offset,
                "n": int(len(values_turnover)),
                "mean_turnover": round(float(np.mean(values_turnover)), 8)
                if values_turnover
                else None,
                "mean_cost": round(float(np.mean(values_cost)), 8)
                if values_cost
                else None,
                "first_date": min(dates).strftime("%Y-%m-%d") if dates else None,
                "last_date": max(dates).strftime("%Y-%m-%d") if dates else None,
            }
        )

    non_event_positions = [i for i in range(len(report)) if i not in event_positions]
    if not non_event_positions:
        raise ValueError("event window covers all report rows")
    non_event = report.iloc[non_event_positions]
    non_event_turnover = float(non_event[turnover_col].mean())
    non_event_cost = float(non_event["cost"].mean())

    event = report.iloc[sorted(event_positions)]
    event_turnover = float(event[turnover_col].mean())
    event_cost = float(event["cost"].mean())
    total_turnover = float(report[turnover_col].sum())
    total_cost = float(report["cost"].sum())
    first = by_offset[0]

    return {
        "window_sessions": int(window),
        "n_retrain_events": int(len(execution_dates)),
        "n_event_days": int(len(event_positions)),
        "n_non_event_days": int(len(non_event_positions)),
        "by_offset": by_offset,
        "non_event_mean_turnover": round(non_event_turnover, 8),
        "non_event_mean_cost": round(non_event_cost, 8),
        "event_window_mean_turnover": round(event_turnover, 8),
        "event_window_mean_cost": round(event_cost, 8),
        "event_window_turnover_ratio_to_non_event": round(
            event_turnover / non_event_turnover, 6
        )
        if non_event_turnover
        else None,
        "event_window_cost_ratio_to_non_event": round(event_cost / non_event_cost, 6)
        if non_event_cost
        else None,
        "first_execution_day_turnover_ratio_to_non_event": round(
            first["mean_turnover"] / non_event_turnover, 6
        )
        if first["mean_turnover"] is not None and non_event_turnover
        else None,
        "first_execution_day_cost_ratio_to_non_event": round(
            first["mean_cost"] / non_event_cost, 6
        )
        if first["mean_cost"] is not None and non_event_cost
        else None,
        "event_window_turnover_share": round(
            float(event[turnover_col].sum()) / total_turnover, 8
        )
        if total_turnover
        else None,
        "event_window_cost_share": round(float(event["cost"].sum()) / total_cost, 8)
        if total_cost
        else None,
    }


def pairwise_gap_attribution(
    focus_report: pd.DataFrame, comparator_report: pd.DataFrame
) -> dict:
    focus = _validate_report(focus_report)
    comparator = _validate_report(comparator_report)
    common = focus.index.intersection(comparator.index)
    if len(common) != len(focus) or len(common) != len(comparator):
        raise ValueError("pairwise reports do not have the same daily index")

    focus_gross = focus.loc[common, "return"].astype(float)
    comp_gross = comparator.loc[common, "return"].astype(float)
    focus_net = focus_gross - focus.loc[common, "cost"].astype(float)
    comp_net = comp_gross - comparator.loc[common, "cost"].astype(float)

    daily_gross_log_gap = (
        np.log1p(focus_gross.to_numpy()) - np.log1p(comp_gross.to_numpy())
    )
    daily_log_gap = np.log1p(focus_net.to_numpy()) - np.log1p(comp_net.to_numpy())
    cumulative_log_gap = np.cumsum(daily_log_gap)
    final_gross_log_gap = float(daily_gross_log_gap.sum())
    final_log_gap = float(cumulative_log_gap[-1])
    final_ratio = float(math.exp(final_log_gap) - 1.0)
    gross_ratio = float(math.exp(final_gross_log_gap) - 1.0)
    cost_effect_log_gap = final_log_gap - final_gross_log_gap

    annual = []
    years = pd.Index(common.year).unique().tolist()
    for year in years:
        mask = common.year == year
        gap = float(daily_log_gap[mask].sum())
        annual.append(
            {
                "year": int(year),
                "log_gap": round(gap, 8),
                "nav_ratio_gap": round(float(math.exp(gap) - 1.0), 8),
            }
        )

    negative = sorted(
        [row for row in annual if row["log_gap"] < 0],
        key=lambda row: row["log_gap"],
    )
    negative_abs_total = float(sum(-row["log_gap"] for row in negative))
    concentration = {}
    for n in (1, 2, 3):
        selected = negative[:n]
        concentration[f"top{n}_negative_year_share"] = (
            round(sum(-row["log_gap"] for row in selected) / negative_abs_total, 6)
            if negative_abs_total
            else None
        )
        concentration[f"top{n}_negative_years"] = [row["year"] for row in selected]

    milestones = {}
    if final_log_gap != 0.0:
        for frac in (0.25, 0.5, 0.75):
            target = final_log_gap * frac
            if final_log_gap < 0:
                positions = np.flatnonzero(cumulative_log_gap <= target)
            else:
                positions = np.flatnonzero(cumulative_log_gap >= target)
            milestones[f"first_{int(frac * 100)}pct_final_gap_date"] = (
                common[int(positions[0])].strftime("%Y-%m-%d")
                if len(positions)
                else None
            )

    min_pos = int(np.argmin(cumulative_log_gap))
    max_pos = int(np.argmax(cumulative_log_gap))
    return {
        "n_days": int(len(common)),
        "final_gross_log_gap": round(final_gross_log_gap, 8),
        "final_net_log_gap": round(final_log_gap, 8),
        "final_focus_vs_comparator_gross_nav_ratio_gap": round(gross_ratio, 8),
        "final_focus_vs_comparator_net_nav_ratio_gap": round(final_ratio, 8),
        "cost_effect_log_gap": round(cost_effect_log_gap, 8),
        "cost_effect_share_of_net_gap": round(
            cost_effect_log_gap / final_log_gap, 6
        )
        if final_log_gap
        else None,
        "annual": annual,
        "negative_gap_concentration": concentration,
        "gap_formation_milestones": milestones,
        "max_disadvantage": {
            "date": common[min_pos].strftime("%Y-%m-%d"),
            "log_gap": round(float(cumulative_log_gap[min_pos]), 8),
            "nav_ratio_gap": round(float(math.exp(cumulative_log_gap[min_pos]) - 1.0), 8),
        },
        "max_advantage": {
            "date": common[max_pos].strftime("%Y-%m-%d"),
            "log_gap": round(float(cumulative_log_gap[max_pos]), 8),
            "nav_ratio_gap": round(float(math.exp(cumulative_log_gap[max_pos]) - 1.0), 8),
        },
    }
