"""Pure zero-fit regime × model-age attribution helpers."""

from __future__ import annotations

from bisect import bisect_right
import math

import numpy as np
import pandas as pd


def _validate_calendar(calendar: list[str]) -> list[str]:
    days = [str(x)[:10] for x in calendar]
    if not days or days != sorted(set(days)):
        raise ValueError("calendar must be sorted, unique, and non-empty")
    return days


def model_age_frame(
    report_index: pd.Index,
    *,
    calendar: list[str],
    retrain_dates: list[str],
    freq: int,
) -> pd.DataFrame:
    """Map each execution day to signal day, active retrain, and model age.

    Execution on T+1 uses the signal formed on T. Model age is therefore measured
    on the signal day, in trading sessions since the active retrain date.
    """
    if freq < 2:
        raise ValueError("freq must be >= 2")
    days = _validate_calendar(calendar)
    pos = {day: i for i, day in enumerate(days)}
    retrains = sorted({str(x)[:10] for x in retrain_dates})
    if not retrains:
        raise ValueError("retrain_dates is empty")
    missing_retrains = [day for day in retrains if day not in pos]
    if missing_retrains:
        raise ValueError(f"retrain dates absent from calendar: {missing_retrains[:3]}")
    retrain_pos = [pos[day] for day in retrains]

    rows = []
    for value in pd.to_datetime(report_index):
        exec_day = value.strftime("%Y-%m-%d")
        if exec_day not in pos:
            raise ValueError(f"execution day absent from calendar: {exec_day}")
        exec_i = pos[exec_day]
        if exec_i <= 0:
            raise ValueError(f"no prior signal day for execution day: {exec_day}")
        signal_i = exec_i - 1
        j = bisect_right(retrain_pos, signal_i) - 1
        if j < 0:
            raise ValueError(f"no active retrain before signal day: {days[signal_i]}")
        active_i = retrain_pos[j]
        age = signal_i - active_i
        if age < 0:
            raise AssertionError("negative model age")
        if age >= freq:
            raise ValueError(
                f"model age outside expected freq window: exec={exec_day} age={age} freq={freq}"
            )
        rows.append(
            {
                "execution_date": value,
                "signal_date": days[signal_i],
                "active_retrain_asof": days[active_i],
                "model_age_sessions": int(age),
            }
        )

    out = pd.DataFrame(rows).set_index("execution_date")
    out["model_age_bin"] = out["model_age_sessions"].map(
        lambda age: _age_bin(int(age), freq)
    )
    return out


def _age_bin(age: int, freq: int) -> str:
    # The production audit is freq=20. Four equal-ish age buckets keep the
    # output interpretable while remaining deterministic for other frequencies.
    width = max(1, int(math.ceil(freq / 4)))
    lo = (age // width) * width
    hi = min(freq - 1, lo + width - 1)
    return f"{lo:02d}-{hi:02d}"


def market_regime_frame(
    report: pd.DataFrame,
    *,
    lookback: int = 20,
    annual_sessions: int = 238,
) -> pd.DataFrame:
    """Build point-in-time market regime labels from benchmark returns only.

    All rolling features are shifted by one execution session, so the label for
    day T uses information available before T's realized return.
    """
    if lookback < 2:
        raise ValueError("lookback must be >= 2")
    if annual_sessions <= 0:
        raise ValueError("annual_sessions must be positive")
    if "bench" not in report.columns:
        raise ValueError("report missing bench")

    rep = report.sort_index().copy()
    rep.index = pd.to_datetime(rep.index)
    bench = rep["bench"].astype(float)
    if bench.isna().any() or (bench <= -1.0).any():
        raise ValueError("invalid benchmark returns")

    prior = bench.shift(1)
    trailing_return = (1.0 + prior).rolling(lookback, min_periods=lookback).apply(
        np.prod, raw=True
    ) - 1.0
    trailing_vol = (
        prior.rolling(lookback, min_periods=lookback).std(ddof=1)
        * math.sqrt(annual_sessions)
    )

    nav = (1.0 + bench).cumprod()
    prior_nav = nav.shift(1)
    prior_peak = prior_nav.cummax()
    drawdown = prior_nav / prior_peak - 1.0

    out = pd.DataFrame(
        {
            "trailing_return_20": trailing_return,
            "trailing_vol_20_ann": trailing_vol,
            "prior_drawdown": drawdown,
        },
        index=rep.index,
    )
    out["trend_regime"] = out["trailing_return_20"].map(_trend_label)
    out["vol_regime"] = out["trailing_vol_20_ann"].map(_vol_label)
    out["drawdown_regime"] = out["prior_drawdown"].map(_drawdown_label)
    out["stress_regime"] = out.apply(_stress_label, axis=1)
    return out


def _trend_label(value) -> str:
    if pd.isna(value):
        return "warmup"
    if value <= -0.05:
        return "down_5pct_plus"
    if value >= 0.05:
        return "up_5pct_plus"
    return "flat_pm5pct"


def _vol_label(value) -> str:
    if pd.isna(value):
        return "warmup"
    if value < 0.20:
        return "low_lt20"
    if value < 0.30:
        return "mid_20_30"
    return "high_ge30"


def _drawdown_label(value) -> str:
    if pd.isna(value):
        return "warmup"
    if value <= -0.20:
        return "deep_ge20"
    if value <= -0.10:
        return "medium_10_20"
    return "shallow_lt10"


def _stress_label(row) -> str:
    if row["trend_regime"] == "warmup":
        return "warmup"
    stress = (
        float(row["trailing_return_20"]) <= -0.05
        or float(row["trailing_vol_20_ann"]) >= 0.30
        or float(row["prior_drawdown"]) <= -0.20
    )
    return "stress" if stress else "normal"


def pairwise_daily_frame(
    focus_report: pd.DataFrame,
    comparator_report: pd.DataFrame,
    *,
    focus_age: pd.DataFrame,
    comparator_age: pd.DataFrame,
    regime: pd.DataFrame,
) -> pd.DataFrame:
    """Create the daily zero-fit attribution frame for one phase pair."""
    focus = focus_report.sort_index().copy()
    comparator = comparator_report.sort_index().copy()
    focus.index = pd.to_datetime(focus.index)
    comparator.index = pd.to_datetime(comparator.index)
    common = focus.index.intersection(comparator.index)
    if len(common) != len(focus) or len(common) != len(comparator):
        raise ValueError("pairwise reports do not share the exact same daily index")
    for frame, label in (
        (focus_age, "focus_age"),
        (comparator_age, "comparator_age"),
        (regime, "regime"),
    ):
        if not common.equals(pd.DatetimeIndex(frame.index)):
            raise ValueError(f"{label} index does not match report index")

    focus_gross = focus.loc[common, "return"].astype(float)
    focus_cost = focus.loc[common, "cost"].astype(float)
    comp_gross = comparator.loc[common, "return"].astype(float)
    comp_cost = comparator.loc[common, "cost"].astype(float)
    focus_net = focus_gross - focus_cost
    comp_net = comp_gross - comp_cost
    if (focus_gross <= -1).any() or (comp_gross <= -1).any():
        raise ValueError("gross return <= -100%")
    if (focus_net <= -1).any() or (comp_net <= -1).any():
        raise ValueError("net return <= -100%")

    out = pd.DataFrame(index=common)
    out["focus_gross"] = focus_gross
    out["comparator_gross"] = comp_gross
    out["focus_cost"] = focus_cost
    out["comparator_cost"] = comp_cost
    out["gross_log_gap"] = np.log1p(focus_gross) - np.log1p(comp_gross)
    out["net_log_gap"] = np.log1p(focus_net) - np.log1p(comp_net)
    out["cost_effect_log_gap"] = out["net_log_gap"] - out["gross_log_gap"]

    out["focus_model_age"] = focus_age["model_age_sessions"].astype(int)
    out["comparator_model_age"] = comparator_age["model_age_sessions"].astype(int)
    out["focus_age_bin"] = focus_age["model_age_bin"].astype(str)
    out["comparator_age_bin"] = comparator_age["model_age_bin"].astype(str)
    out["model_age_delta"] = out["focus_model_age"] - out["comparator_model_age"]
    out["relative_age_state"] = out["model_age_delta"].map(_relative_age_state)

    for column in (
        "trailing_return_20",
        "trailing_vol_20_ann",
        "prior_drawdown",
        "trend_regime",
        "vol_regime",
        "drawdown_regime",
        "stress_regime",
    ):
        out[column] = regime[column]
    out["year"] = out.index.year.astype(int)
    return out


def _relative_age_state(delta: int) -> str:
    if delta >= 5:
        return "focus_much_older"
    if delta <= -5:
        return "focus_much_younger"
    return "similar_within4"


def aggregate_gap_by(frame: pd.DataFrame, columns: str | list[str]) -> list[dict]:
    """Aggregate log-gap attribution by one or more categorical dimensions."""
    if isinstance(columns, str):
        columns = [columns]
    required = set(columns) | {"gross_log_gap", "net_log_gap", "cost_effect_log_gap"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"aggregation missing columns: {sorted(missing)}")

    rows = []
    total_days = len(frame)
    grouped = frame.groupby(columns, dropna=False, observed=False)
    for key, group in grouped:
        if not isinstance(key, tuple):
            key = (key,)
        gross_gap = float(group["gross_log_gap"].sum())
        net_gap = float(group["net_log_gap"].sum())
        cost_gap = float(group["cost_effect_log_gap"].sum())
        row = {name: _json_scalar(value) for name, value in zip(columns, key)}
        row.update(
            {
                "n_days": int(len(group)),
                "day_share": round(len(group) / total_days, 6) if total_days else None,
                "gross_log_gap": round(gross_gap, 8),
                "net_log_gap": round(net_gap, 8),
                "cost_effect_log_gap": round(cost_gap, 8),
                "net_nav_ratio_gap": round(float(math.exp(net_gap) - 1.0), 8),
                "mean_daily_net_log_gap": round(
                    float(group["net_log_gap"].mean()), 10
                ),
            }
        )
        rows.append(row)

    negative_total = sum(-row["net_log_gap"] for row in rows if row["net_log_gap"] < 0)
    for row in rows:
        row["negative_gap_share"] = (
            round((-row["net_log_gap"]) / negative_total, 6)
            if row["net_log_gap"] < 0 and negative_total
            else 0.0
        )
    return sorted(rows, key=lambda row: row["net_log_gap"])


def _json_scalar(value):
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if pd.isna(value):
        return None
    return value


def attribution_windows(
    frame: pd.DataFrame,
    *,
    regime_years: tuple[int, ...] = (2022, 2023),
) -> dict:
    """Summarize full-sample and target-regime slices."""
    target = frame[frame["year"].isin(regime_years)]
    if target.empty:
        raise ValueError(f"no rows in requested regime_years={regime_years}")

    dimensions = [
        "focus_age_bin",
        "comparator_age_bin",
        "relative_age_state",
        "trend_regime",
        "vol_regime",
        "drawdown_regime",
        "stress_regime",
    ]
    interactions = [
        ["relative_age_state", "trend_regime"],
        ["relative_age_state", "vol_regime"],
        ["relative_age_state", "drawdown_regime"],
        ["focus_age_bin", "stress_regime"],
    ]

    def pack(subset: pd.DataFrame) -> dict:
        return {
            "n_days": int(len(subset)),
            "total_net_log_gap": round(float(subset["net_log_gap"].sum()), 8),
            "total_gross_log_gap": round(float(subset["gross_log_gap"].sum()), 8),
            "total_cost_effect_log_gap": round(
                float(subset["cost_effect_log_gap"].sum()), 8
            ),
            "by_dimension": {
                dim: aggregate_gap_by(subset, dim) for dim in dimensions
            },
            "interactions": {
                "x".join(cols): aggregate_gap_by(subset, cols)
                for cols in interactions
            },
        }

    full = pack(frame)
    focused = pack(target)
    return {
        "full_sample": full,
        "regime_years": {
            "years": list(regime_years),
            **focused,
            "share_of_full_net_log_gap": round(
                focused["total_net_log_gap"] / full["total_net_log_gap"], 6
            )
            if full["total_net_log_gap"]
            else None,
        },
    }


def benchmark_regime_by_year(regime: pd.DataFrame) -> list[dict]:
    """Compact annual summary of the benchmark state variables."""
    rows = []
    for year, group in regime.groupby(regime.index.year):
        valid = group.dropna(
            subset=["trailing_return_20", "trailing_vol_20_ann", "prior_drawdown"]
        )
        if valid.empty:
            continue
        rows.append(
            {
                "year": int(year),
                "n_days": int(len(valid)),
                "mean_trailing_return_20": round(
                    float(valid["trailing_return_20"].mean()), 8
                ),
                "mean_trailing_vol_20_ann": round(
                    float(valid["trailing_vol_20_ann"].mean()), 8
                ),
                "mean_prior_drawdown": round(
                    float(valid["prior_drawdown"].mean()), 8
                ),
                "stress_day_share": round(
                    float((valid["stress_regime"] == "stress").mean()), 6
                ),
                "downtrend_day_share": round(
                    float((valid["trend_regime"] == "down_5pct_plus").mean()), 6
                ),
                "high_vol_day_share": round(
                    float((valid["vol_regime"] == "high_ge30").mean()), 6
                ),
                "deep_drawdown_day_share": round(
                    float((valid["drawdown_regime"] == "deep_ge20").mean()), 6
                ),
            }
        )
    return rows


def decision_summary(pairwise_windows: dict[str, dict]) -> dict:
    """Extract a compact mechanism-oriented summary from pairwise outputs."""
    out = {}
    for comparator, result in pairwise_windows.items():
        target = result["regime_years"]
        age_rows = target["by_dimension"]["relative_age_state"]
        stress_rows = target["by_dimension"]["stress_regime"]

        age_contribution = min(age_rows, key=lambda row: row["net_log_gap"])
        age_intensity = min(age_rows, key=lambda row: row["mean_daily_net_log_gap"])
        stress_contribution = min(stress_rows, key=lambda row: row["net_log_gap"])
        stress_intensity = min(
            stress_rows, key=lambda row: row["mean_daily_net_log_gap"]
        )

        out[str(comparator)] = {
            "regime_year_share_of_full_net_gap": target[
                "share_of_full_net_log_gap"
            ],
            "dominant_negative_age_state": age_contribution["relative_age_state"],
            "dominant_negative_age_state_net_log_gap": age_contribution[
                "net_log_gap"
            ],
            "dominant_negative_age_state_share": age_contribution[
                "negative_gap_share"
            ],
            "worst_mean_daily_age_state": age_intensity["relative_age_state"],
            "worst_mean_daily_age_state_gap": age_intensity[
                "mean_daily_net_log_gap"
            ],
            "dominant_negative_stress_state": stress_contribution[
                "stress_regime"
            ],
            "dominant_negative_stress_state_net_log_gap": stress_contribution[
                "net_log_gap"
            ],
            "dominant_negative_stress_state_share": stress_contribution[
                "negative_gap_share"
            ],
            "worst_mean_daily_stress_state": stress_intensity["stress_regime"],
            "worst_mean_daily_stress_state_gap": stress_intensity[
                "mean_daily_net_log_gap"
            ],
        }
    return out
