"""Pure-Python contracts for the production-aligned multi-pool tuner.

This module deliberately contains no Qlib or Modal imports. It defines the
research identities, purged-fold validation, robust candidate summaries, and
deterministic ranking rules that execution code must obey.
"""
from __future__ import annotations

import hashlib
import json
import math
from bisect import bisect_left
from copy import deepcopy
from statistics import median
from typing import Any, Iterable

PROTOCOL_VERSION = "multi_pool_tuner_v1"
ACCOUNT_ERROR_TOLERANCE = 1e-12
MIN_SCREEN_FOLDS = 4

_MARKET_POLICIES = {
    "csi1000": {
        "mode": "full_tuner",
        "repro_gate_required": True,
        "performance_tuning_allowed": True,
        "frozen_stage_a": {
            "target": "raw_20d",
            "retrain_frequency": 20,
            "topk": 20,
            "n_drop": 2,
            "execution": "t_close_t1_open",
        },
        "reference_phases": [0, 4, 6, 10, 15],
    },
    "chinext": {
        "mode": "bounded_rescue",
        "repro_gate_required": True,
        "performance_tuning_allowed": True,
        "frozen_stage_a": {
            "target": "raw_20d",
            "retrain_frequency": 20,
            "topk": 20,
            "n_drop": 3,
            "execution": "t_close_t1_open",
        },
        "reference_phases": [0, 5, 10, 15],
    },
    "star": {
        "mode": "blocked_until_repro_gate",
        "repro_gate_required": True,
        "performance_tuning_allowed": False,
        "frozen_stage_a": {
            "target": "raw_20d",
            "retrain_frequency": 20,
            "topk": 50,
            "n_drop": 2,
            "execution": "t_close_t1_open",
        },
        "reference_phases": [0, 5, 10, 15],
    },
}

_REQUIRED_RESULT_FIELDS = (
    "relative_excess_cagr",
    "strategy_max_drawdown",
    "sharpe",
    "information_ratio",
    "account_return_max_error",
    "mean_turnover",
    "total_cost_sum",
)


def market_policy(market: str) -> dict:
    """Return a copy of the frozen next-stage policy for one market."""
    if market not in _MARKET_POLICIES:
        raise ValueError(f"market must be one of {sorted(_MARKET_POLICIES)}; got {market!r}")
    return deepcopy(_MARKET_POLICIES[market])


def _stable_json_sha256(obj: Any) -> str:
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def build_candidate_spec(
    *,
    market: str,
    model_params: dict,
    target: str = "raw_20d",
    retrain_frequency: int = 20,
    topk: int | None = None,
    n_drop: int | None = None,
) -> dict:
    """Build an auditable candidate identity without changing execution semantics."""
    policy = market_policy(market)
    frozen = policy["frozen_stage_a"]
    if retrain_frequency < 1:
        raise ValueError("retrain_frequency must be positive")
    if not isinstance(model_params, dict) or not model_params:
        raise ValueError("model_params must be a non-empty mapping")
    topk = frozen["topk"] if topk is None else int(topk)
    n_drop = frozen["n_drop"] if n_drop is None else int(n_drop)
    if topk < 1 or n_drop < 0 or n_drop > topk:
        raise ValueError("invalid TopK/n_drop combination")
    spec = {
        "protocol": PROTOCOL_VERSION,
        "market": market,
        "mode": policy["mode"],
        "model": {"family": "lightgbm", "params": deepcopy(model_params)},
        "target": str(target),
        "retrain_frequency": int(retrain_frequency),
        "portfolio": {"topk": topk, "n_drop": n_drop},
        "execution": "t_close_t1_open",
    }
    spec["candidate_id"] = _stable_json_sha256(spec)
    return spec


def _calendar_index(calendar: list[str], date: str) -> int:
    i = bisect_left(calendar, str(date)[:10])
    if i >= len(calendar) or calendar[i] != str(date)[:10]:
        raise ValueError(f"fold date {date!r} is not in the trading calendar")
    return i


def validate_purged_folds(folds: Iterable[dict], calendar: list[str], *, horizon: int = 20) -> list[dict]:
    """Validate chronological train/valid/test folds and strict label maturity.

    For a forward horizon H, the final train label must mature strictly before
    validation starts, and the final validation label must mature strictly before
    test starts. Test windows must be ordered and non-overlapping.
    """
    if horizon < 1:
        raise ValueError("horizon must be positive")
    if not calendar or calendar != sorted(set(calendar)):
        raise ValueError("trading calendar must be sorted, unique, and non-empty")

    normalized = []
    seen = set()
    previous_test_end = None
    for raw in folds:
        fold = deepcopy(raw)
        fold_id = str(fold.get("fold_id", "")).strip()
        if not fold_id or fold_id in seen:
            raise ValueError(f"fold_id must be unique and non-empty; got {fold_id!r}")
        seen.add(fold_id)

        positions = {}
        for segment in ("train", "valid", "test"):
            dates = fold.get(segment)
            if not isinstance(dates, (list, tuple)) or len(dates) != 2:
                raise ValueError(f"{fold_id}: {segment} must be [start, end]")
            start, end = str(dates[0])[:10], str(dates[1])[:10]
            start_i, end_i = _calendar_index(calendar, start), _calendar_index(calendar, end)
            if start_i > end_i:
                raise ValueError(f"{fold_id}: {segment} starts after it ends")
            fold[segment] = [start, end]
            positions[segment] = (start_i, end_i)

        train_start, train_end = positions["train"]
        valid_start, valid_end = positions["valid"]
        test_start, test_end = positions["test"]
        if not (train_start <= train_end < valid_start <= valid_end < test_start <= test_end):
            raise ValueError(f"{fold_id}: train/valid/test are not strictly chronological")
        if train_end + horizon >= valid_start:
            raise ValueError(f"{fold_id}: train labels leak into validation")
        if valid_end + horizon >= test_start:
            raise ValueError(f"{fold_id}: validation labels leak into test")
        if previous_test_end is not None and test_start <= previous_test_end:
            raise ValueError(f"{fold_id}: test window overlaps or precedes an earlier fold")
        previous_test_end = test_end
        normalized.append(fold)
    if not normalized:
        raise ValueError("at least one fold is required")
    return normalized


def _finite_number(value: Any, *, label: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(out):
        raise ValueError(f"{label} must be finite")
    return out


def _validate_result(row: dict, *, label: str) -> dict:
    missing = [key for key in _REQUIRED_RESULT_FIELDS if row.get(key) is None]
    if missing:
        raise ValueError(f"{label}: missing ranking fields {missing}")
    out = deepcopy(row)
    for key in _REQUIRED_RESULT_FIELDS:
        out[key] = _finite_number(row[key], label=f"{label}.{key}")
    if abs(out["account_return_max_error"]) > ACCOUNT_ERROR_TOLERANCE:
        raise ValueError(f"{label}: account_return_max_error exceeds tolerance")
    if out["strategy_max_drawdown"] > ACCOUNT_ERROR_TOLERANCE:
        raise ValueError(f"{label}: strategy_max_drawdown must be <= 0")
    if out["mean_turnover"] < 0 or out["total_cost_sum"] < 0:
        raise ValueError(f"{label}: turnover/cost must be non-negative")
    return out


def _lower_quantile(values: list[float], q: float) -> float:
    if not values:
        raise ValueError("cannot take a quantile of an empty sequence")
    if not 0 <= q <= 1:
        raise ValueError("q must be between 0 and 1")
    xs = sorted(float(x) for x in values)
    index = max(0, math.ceil(len(xs) * q) - 1)
    return xs[index]


def _result_summary(rows: list[dict], *, kind: str) -> dict:
    relative = [row["relative_excess_cagr"] for row in rows]
    return {
        "n": len(rows),
        f"{kind}_ids": [row[f"{kind}_id"] for row in rows],
        "relative_excess_cagr_q25": _lower_quantile(relative, 0.25),
        "relative_excess_cagr_median": float(median(relative)),
        "relative_excess_cagr_worst": min(relative),
        "positive_ratio": sum(value > 0 for value in relative) / len(relative),
        "information_ratio_median": float(median(row["information_ratio"] for row in rows)),
        "sharpe_median": float(median(row["sharpe"] for row in rows)),
        "strategy_max_drawdown_worst": min(row["strategy_max_drawdown"] for row in rows),
        "mean_turnover_median": float(median(row["mean_turnover"] for row in rows)),
        "total_cost_sum_median": float(median(row["total_cost_sum"] for row in rows)),
    }


def summarize_candidate(
    candidate: dict,
    fold_results: Iterable[dict],
    *,
    phase_results: Iterable[dict] | None = None,
    min_folds: int = MIN_SCREEN_FOLDS,
    required_phases: Iterable[int] | None = None,
) -> dict:
    """Validate and summarize one candidate without collapsing metrics to a scalar."""
    if candidate.get("protocol") != PROTOCOL_VERSION or not candidate.get("candidate_id"):
        raise ValueError("candidate must come from build_candidate_spec")
    semantic_candidate = {key: value for key, value in candidate.items() if key != "candidate_id"}
    if candidate["candidate_id"] != _stable_json_sha256(semantic_candidate):
        raise ValueError("candidate_id does not match candidate semantics")
    if min_folds < 1:
        raise ValueError("min_folds must be positive")

    folds = []
    seen_folds = set()
    for raw in fold_results:
        fold_id = str(raw.get("fold_id", "")).strip()
        if not fold_id or fold_id in seen_folds:
            raise ValueError(f"duplicate or missing fold_id {fold_id!r}")
        seen_folds.add(fold_id)
        row = _validate_result(raw, label=f"fold {fold_id}")
        row["fold_id"] = fold_id
        folds.append(row)
    if len(folds) < min_folds:
        raise ValueError(f"candidate requires at least {min_folds} fold results")
    folds.sort(key=lambda row: row["fold_id"])

    phases = None
    if phase_results is not None:
        phases = []
        seen_phases = set()
        for raw in phase_results:
            try:
                phase_id = int(raw.get("phase_id"))
            except (TypeError, ValueError) as exc:
                raise ValueError("phase_id must be an integer") from exc
            if phase_id < 0 or phase_id in seen_phases:
                raise ValueError(f"duplicate or invalid phase_id {phase_id!r}")
            seen_phases.add(phase_id)
            row = _validate_result(raw, label=f"phase {phase_id}")
            row["phase_id"] = phase_id
            phases.append(row)
        phases.sort(key=lambda row: row["phase_id"])
        required = set(int(value) for value in (required_phases or []))
        missing = sorted(required - seen_phases)
        if missing:
            raise ValueError(f"candidate missing required phases {missing}")
        if not phases:
            raise ValueError("phase_results cannot be empty")
    elif required_phases:
        raise ValueError("required_phases were supplied without phase_results")

    return {
        "candidate_id": candidate["candidate_id"],
        "candidate": deepcopy(candidate),
        "fold_summary": _result_summary(folds, kind="fold"),
        "phase_summary": None if phases is None else _result_summary(phases, kind="phase"),
    }


def _screen_rank_tuple(summary: dict) -> tuple[float, ...]:
    fold = summary["fold_summary"]
    return (
        fold["relative_excess_cagr_q25"],
        fold["relative_excess_cagr_median"],
        fold["relative_excess_cagr_worst"],
        fold["positive_ratio"],
        fold["information_ratio_median"],
        fold["sharpe_median"],
        fold["strategy_max_drawdown_worst"],
        -fold["mean_turnover_median"],
        -fold["total_cost_sum_median"],
    )


def _final_rank_tuple(summary: dict) -> tuple[float, ...]:
    phase = summary.get("phase_summary")
    if phase is None or int(phase.get("n", 0)) < 4:
        raise ValueError("final ranking requires at least four fixed phase robustness results")
    return (
        phase["relative_excess_cagr_q25"],
        phase["relative_excess_cagr_median"],
        phase["relative_excess_cagr_worst"],
        phase["positive_ratio"],
        phase["information_ratio_median"],
        phase["sharpe_median"],
        phase["strategy_max_drawdown_worst"],
        -phase["mean_turnover_median"],
        -phase["total_cost_sum_median"],
        *_screen_rank_tuple(summary),
    )


def rank_candidates(summaries: Iterable[dict], *, stage: str = "screen") -> list[dict]:
    """Rank deterministically with robust lexicographic priorities.

    `screen` ranks on purged temporal folds only. `final` first ranks fixed
    calendar-phase robustness, then uses the complete screen vector. Equal
    vectors are broken by candidate_id ascending; no historically best single
    phase can dominate selection.
    """
    rows = [deepcopy(row) for row in summaries]
    if not rows:
        raise ValueError("at least one candidate summary is required")
    if stage not in {"screen", "final"}:
        raise ValueError("stage must be 'screen' or 'final'")
    ids = [row.get("candidate_id") for row in rows]
    if any(not value for value in ids) or len(ids) != len(set(ids)):
        raise ValueError("candidate_id values must be unique and non-empty")
    rank_key = _screen_rank_tuple if stage == "screen" else _final_rank_tuple
    rows.sort(key=lambda row: row["candidate_id"])
    rows.sort(key=rank_key, reverse=True)
    for position, row in enumerate(rows, start=1):
        row["rank"] = position
        row["ranking_stage"] = stage
        row["ranking_vector"] = list(rank_key(row))
    return rows
