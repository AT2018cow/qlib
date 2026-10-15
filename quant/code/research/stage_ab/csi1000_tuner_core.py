"""CSI1000 Stage-A LightGBM tuning contract.

This module is intentionally pure Python. It freezes the cheap temporal-fold
screen, candidate identities, search spaces, and robust ranking rules before
any Modal/Qlib compute is launched.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from bisect import bisect_left
from copy import deepcopy
from statistics import median
from typing import Any, Iterable

PROTOCOL_VERSION = "csi1000_lgb_stage_a_v3"
MARKET = "csi1000"
TARGET = "raw_20d"
LABEL_HORIZON = 20
RETRAIN_FREQUENCY = 20
TOPK = 20
N_DROP = 2
EXECUTION = "t_close_t1_open"
STRATEGY = "deterministic_topk_dropout_v1"
TRAIN_START = "2016-01-01"
VALIDATION_SESSIONS = 252
SCREEN_FOLDS = 4
SCREEN_EXECUTION_SESSIONS = RETRAIN_FREQUENCY
SCREEN_SIGNAL_ANCHOR = "2021-01-04"
SCREEN_LAST_EXECUTION_CUTOFF = "2024-12-31"
REFERENCE_PHASES = (0, 4, 6, 10, 15)
ACCOUNT_ERROR_TOLERANCE = 1e-12
METRIC_VERSION = "portfolio_compound_v1"
EXPANDED_SEARCH_SEED = 20261007
REPRO_GATE_VERSION = "baseline_double_fit_4fold_v3"
REPORT_REPRO_RTOL = 1e-10
REPORT_REPRO_ATOL = 1e-12

SCREEN_RANK_SPECS = (
    ("relative_excess_cagr_worst", 1),
    ("relative_excess_cagr_q25", 1),
    ("relative_excess_cagr_median", 1),
    ("positive_ratio", 1),
    ("information_ratio_median", 1),
    ("sharpe_median", 1),
    ("strategy_max_drawdown_worst", 1),
    ("mean_turnover_median", -1),
    ("total_cost_sum_median", -1),
)

BASELINE_TUNABLE_PARAMS = {
    "learning_rate": 0.1,
    "colsample_bytree": 0.9,
    "lambda_l1": 205.6999,
    "lambda_l2": 580.9768,
    "max_depth": 8,
    "num_leaves": 250,
    # LightGBM's default is 20; pin it so candidate identity is explicit.
    "min_data_in_leaf": 20,
}

_ALLOWED_TUNABLE_PARAMS = frozenset(BASELINE_TUNABLE_PARAMS)

_REQUIRED_RESULT_FIELDS = (
    "relative_excess_cagr",
    "strategy_max_drawdown",
    "sharpe",
    "information_ratio",
    "account_return_max_error",
    "mean_turnover",
    "total_cost_sum",
)

_SMOKE_OVERLAYS = (
    BASELINE_TUNABLE_PARAMS,
    {
        "learning_rate": 0.05,
        "colsample_bytree": 0.9,
        "lambda_l1": 50.0,
        "lambda_l2": 200.0,
        "max_depth": 8,
        "num_leaves": 63,
        "min_data_in_leaf": 100,
    },
    {
        "learning_rate": 0.05,
        "colsample_bytree": 0.8,
        "lambda_l1": 50.0,
        "lambda_l2": 200.0,
        "max_depth": 6,
        "num_leaves": 31,
        "min_data_in_leaf": 100,
    },
    {
        "learning_rate": 0.05,
        "colsample_bytree": 0.9,
        "lambda_l1": 100.0,
        "lambda_l2": 400.0,
        "max_depth": 8,
        "num_leaves": 127,
        "min_data_in_leaf": 50,
    },
    {
        "learning_rate": 0.08,
        "colsample_bytree": 0.8,
        "lambda_l1": 10.0,
        "lambda_l2": 100.0,
        "max_depth": 6,
        "num_leaves": 63,
        "min_data_in_leaf": 200,
    },
    {
        "learning_rate": 0.08,
        "colsample_bytree": 0.8,
        "lambda_l1": 10.0,
        "lambda_l2": 100.0,
        "max_depth": 10,
        "num_leaves": 127,
        "min_data_in_leaf": 100,
    },
    {
        "learning_rate": 0.05,
        "colsample_bytree": 0.7,
        "lambda_l1": 205.6999,
        "lambda_l2": 580.9768,
        "max_depth": 8,
        "num_leaves": 250,
        "min_data_in_leaf": 50,
    },
    {
        "learning_rate": 0.03,
        "colsample_bytree": 0.9,
        "lambda_l1": 50.0,
        "lambda_l2": 200.0,
        "max_depth": 8,
        "num_leaves": 127,
        "min_data_in_leaf": 200,
    },
    {
        "learning_rate": 0.1,
        "colsample_bytree": 0.7,
        "lambda_l1": 0.0,
        "lambda_l2": 50.0,
        "max_depth": 8,
        "num_leaves": 63,
        "min_data_in_leaf": 50,
    },
    {
        "learning_rate": 0.1,
        "colsample_bytree": 0.9,
        "lambda_l1": 100.0,
        "lambda_l2": 400.0,
        "max_depth": 6,
        "num_leaves": 31,
        "min_data_in_leaf": 200,
    },
    {
        "learning_rate": 0.05,
        "colsample_bytree": 1.0,
        "lambda_l1": 0.0,
        "lambda_l2": 0.0,
        "max_depth": 8,
        "num_leaves": 127,
        "min_data_in_leaf": 50,
    },
    {
        "learning_rate": 0.05,
        "colsample_bytree": 1.0,
        "lambda_l1": 205.6999,
        "lambda_l2": 580.9768,
        "max_depth": 8,
        "num_leaves": 63,
        "min_data_in_leaf": 100,
    },
)

_EXPANDED_DOMAINS = {
    "learning_rate": (0.03, 0.05, 0.08, 0.1),
    "colsample_bytree": (0.7, 0.8, 0.9, 1.0),
    "lambda_l1": (0.0, 10.0, 50.0, 100.0, 205.6999),
    "lambda_l2": (0.0, 50.0, 100.0, 200.0, 400.0, 580.9768),
    "max_depth": (6, 8, 10),
    "num_leaves": (31, 63, 127, 250),
    "min_data_in_leaf": (20, 50, 100, 200),
}


def frozen_protocol() -> dict:
    """Return the Stage-A dimensions that are not tunable."""
    return {
        "protocol": PROTOCOL_VERSION,
        "market": MARKET,
        "target": TARGET,
        "label_horizon": LABEL_HORIZON,
        "retrain_frequency": RETRAIN_FREQUENCY,
        "portfolio": {"topk": TOPK, "n_drop": N_DROP},
        "execution": EXECUTION,
        "strategy": STRATEGY,
        "tie_break": "score_desc_instrument_asc",
        "train_start": TRAIN_START,
        "validation_sessions": VALIDATION_SESSIONS,
        "screen_folds": SCREEN_FOLDS,
        "screen_execution_sessions": SCREEN_EXECUTION_SESSIONS,
        "screen_signal_anchor": SCREEN_SIGNAL_ANCHOR,
        "screen_last_execution_cutoff": SCREEN_LAST_EXECUTION_CUTOFF,
        "reference_phases": list(REFERENCE_PHASES),
    }


def deterministic_score_order(items: Iterable[tuple[Any, Any]]) -> list[Any]:
    """Return instrument ids ordered by score desc, instrument asc for exact ties.

    NaN scores are placed last, matching pandas' default sort_values behavior.
    The secondary instrument key is explicit so Python hash/set iteration order
    can never decide a TopK boundary.
    """
    rows = []
    seen = set()
    for instrument, score_raw in items:
        key = str(instrument)
        if key in seen:
            raise ValueError(f"duplicate instrument in score vector: {key}")
        seen.add(key)
        score = float(score_raw)
        rows.append((key, score, instrument))

    def rank_key(row):
        key, score, _instrument = row
        if math.isnan(score):
            return (1, 0.0, key)
        return (0, -score, key)

    rows.sort(key=rank_key)
    return [instrument for _key, _score, instrument in rows]


def _stable_json_sha256(obj: Any) -> str:
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def _validate_model_params(params: dict) -> dict:
    if not isinstance(params, dict):
        raise ValueError("model params must be a mapping")
    missing = sorted(_ALLOWED_TUNABLE_PARAMS - set(params))
    extra = sorted(set(params) - _ALLOWED_TUNABLE_PARAMS)
    if missing or extra:
        raise ValueError(f"candidate params mismatch: missing={missing}, extra={extra}")

    out = {}
    for key, value in params.items():
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be numeric") from exc
        if not math.isfinite(number):
            raise ValueError(f"{key} must be finite")
        out[key] = value

    if not (0 < float(out["learning_rate"]) <= 0.2):
        raise ValueError("learning_rate outside Stage-A bounds")
    if not (0.5 <= float(out["colsample_bytree"]) <= 1.0):
        raise ValueError("colsample_bytree outside Stage-A bounds")
    if float(out["lambda_l1"]) < 0 or float(out["lambda_l2"]) < 0:
        raise ValueError("regularization must be non-negative")

    max_depth = int(out["max_depth"])
    num_leaves = int(out["num_leaves"])
    min_data = int(out["min_data_in_leaf"])
    if max_depth < 2 or num_leaves < 2 or min_data < 1:
        raise ValueError("invalid tree-complexity parameters")
    if num_leaves > 2**max_depth:
        raise ValueError("num_leaves must not exceed 2**max_depth")

    out["max_depth"] = max_depth
    out["num_leaves"] = num_leaves
    out["min_data_in_leaf"] = min_data
    out["learning_rate"] = float(out["learning_rate"])
    out["colsample_bytree"] = float(out["colsample_bytree"])
    out["lambda_l1"] = float(out["lambda_l1"])
    out["lambda_l2"] = float(out["lambda_l2"])
    return out


def build_candidate_spec(model_params: dict) -> dict:
    """Build an immutable, content-addressed CSI1000 LightGBM candidate."""
    params = _validate_model_params(model_params)
    semantics = {
        **frozen_protocol(),
        "model_family": "lightgbm",
        "model_params": params,
        "is_baseline": params == _validate_model_params(BASELINE_TUNABLE_PARAMS),
    }
    return {**semantics, "candidate_id": _stable_json_sha256(semantics)}


def smoke_candidate_specs() -> list[dict]:
    """Return the pre-registered 12-candidate smoke screen."""
    candidates = [build_candidate_spec(dict(params)) for params in _SMOKE_OVERLAYS]
    ids = [candidate["candidate_id"] for candidate in candidates]
    if len(ids) != len(set(ids)):
        raise RuntimeError("smoke candidate set contains duplicates")
    if not candidates[0]["is_baseline"]:
        raise RuntimeError("first smoke candidate must remain the frozen baseline")
    return candidates


def expanded_candidate_specs(count: int = 80, seed: int = EXPANDED_SEARCH_SEED) -> list[dict]:
    """Generate a deterministic random screen after the smoke gate is accepted.

    The baseline and all smoke candidates are retained first. Additional
    candidates are sampled without replacement from the bounded Stage-A domain.
    Row bagging, target, retraining frequency, and portfolio parameters are not
    search axes in this stage.
    """
    smoke = smoke_candidate_specs()
    if count < len(smoke):
        raise ValueError(f"expanded search must keep all {len(smoke)} smoke candidates")
    if count > 100:
        raise ValueError("Stage-A expanded screen is capped at 100 candidates")

    candidates = list(smoke)
    seen = {candidate["candidate_id"] for candidate in candidates}
    rng = random.Random(int(seed))
    attempts = 0
    while len(candidates) < count:
        attempts += 1
        if attempts > 100000:
            raise RuntimeError("could not generate enough unique candidates")
        params = {key: rng.choice(values) for key, values in _EXPANDED_DOMAINS.items()}
        if int(params["num_leaves"]) > 2 ** int(params["max_depth"]):
            continue
        candidate = build_candidate_spec(params)
        if candidate["candidate_id"] in seen:
            continue
        seen.add(candidate["candidate_id"])
        candidates.append(candidate)
    return candidates


def _calendar_index(calendar: list[str], date: str) -> int:
    i = bisect_left(calendar, str(date)[:10])
    if i >= len(calendar) or calendar[i] != str(date)[:10]:
        raise ValueError(f"date {date!r} is not in the trading calendar")
    return i


def _calendar_index_on_or_before(calendar: list[str], date: str) -> int:
    i = bisect_left(calendar, str(date)[:10])
    if i < len(calendar) and calendar[i] == str(date)[:10]:
        return i
    i -= 1
    if i < 0:
        raise ValueError(f"no trading session on or before {date!r}")
    return i


def build_stage_a_folds(
    calendar: list[str],
    *,
    signal_anchor: str = SCREEN_SIGNAL_ANCHOR,
    n_folds: int = SCREEN_FOLDS,
    execution_sessions: int = SCREEN_EXECUTION_SESSIONS,
    validation_sessions: int = VALIDATION_SESSIONS,
    horizon: int = LABEL_HORIZON,
    train_start: str = TRAIN_START,
    last_execution_cutoff: str = SCREEN_LAST_EXECUTION_CUTOFF,
) -> list[dict]:
    """Build four cheap, regime-spread one-fit folds with a reserved recent tail.

    Anchors are spread deterministically from 2021 through the end of 2024.
    Each model is evaluated for exactly one production retrain interval
    (20 execution sessions). This keeps the cheap one-fit screen aligned with
    freq20 instead of letting a stale model survive beyond its production age.
    The folds are independent accounts and must never be concatenated into a
    headline return.
    """
    if not calendar or calendar != sorted(set(calendar)):
        raise ValueError("trading calendar must be sorted, unique, and non-empty")
    if n_folds < 2 or execution_sessions < 20 or validation_sessions < 60 or horizon < 1:
        raise ValueError("invalid fold geometry")

    anchor_i = _calendar_index(calendar, signal_anchor)
    cutoff_i = _calendar_index_on_or_before(calendar, last_execution_cutoff)
    latest_signal_i = cutoff_i - execution_sessions
    train_start_i = bisect_left(calendar, train_start)
    if train_start_i >= len(calendar):
        raise ValueError("train_start is after the provider calendar")
    if latest_signal_i <= anchor_i:
        raise ValueError("screen cutoff leaves no room for regime-spread folds")
    if cutoff_i + 1 >= len(calendar):
        raise ValueError("provider calendar leaves no reserved tail after the screen cutoff")

    span = latest_signal_i - anchor_i
    anchor_indices = [
        round(anchor_i + span * fold_index / (n_folds - 1))
        for fold_index in range(n_folds)
    ]
    if len(set(anchor_indices)) != n_folds:
        raise ValueError("fold anchors collapsed; increase screen span")

    folds = []
    for fold_index, signal_start_i in enumerate(anchor_indices):
        execution_start_i = signal_start_i + 1
        execution_end_i = signal_start_i + execution_sessions
        # T-close signal at t executes at t+1 open. A freq20 model therefore
        # owns exactly 20 signal dates: anchor .. anchor+19.
        signal_end_i = execution_end_i - 1

        valid_end_i = signal_start_i - horizon - 1
        valid_start_i = valid_end_i - validation_sessions + 1
        train_end_i = valid_start_i - horizon - 1
        if train_end_i <= train_start_i:
            raise ValueError(f"fold {fold_index}: insufficient purged training history")
        if train_end_i + horizon >= valid_start_i:
            raise AssertionError("train labels leak into validation")
        if valid_end_i + horizon >= signal_start_i:
            raise AssertionError("validation labels leak into test signal window")

        folds.append(
            {
                "fold_id": f"fold{fold_index + 1}",
                "train": [calendar[train_start_i], calendar[train_end_i]],
                "valid": [calendar[valid_start_i], calendar[valid_end_i]],
                "signal": [calendar[signal_start_i], calendar[signal_end_i]],
                "execution": [calendar[execution_start_i], calendar[execution_end_i]],
            }
        )

    for previous, current in zip(folds, folds[1:]):
        if previous["execution"][1] >= current["execution"][0]:
            raise AssertionError("fold execution windows overlap")
        if previous["signal"][1] >= current["signal"][0]:
            raise AssertionError("fold signal windows overlap")
    return folds


def reserved_tail(calendar: list[str], folds: list[dict]) -> dict:
    """Return the untouched execution tail after the Stage-A fold screen."""
    if not folds:
        raise ValueError("fold list is empty")
    last_exec_i = _calendar_index(calendar, folds[-1]["execution"][1])
    if last_exec_i + 1 >= len(calendar):
        raise ValueError("no reserved tail remains after screen folds")
    return {
        "signal_anchor": calendar[last_exec_i],
        "execution_start": calendar[last_exec_i + 1],
        "execution_end": calendar[-1],
        "n_execution_sessions": len(calendar) - last_exec_i - 1,
    }


def _finite_number(value: Any, *, label: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(out):
        raise ValueError(f"{label} must be finite")
    return out


def _validate_result(row: dict, *, label: str) -> dict:
    if row.get("metric_version") != METRIC_VERSION:
        raise ValueError(f"{label}: unexpected metric_version")
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


def _linear_quantile(values: list[float], q: float) -> float:
    """Numpy-style linear quantile (method='linear') without a NumPy dependency."""
    if not values:
        raise ValueError("cannot take a quantile of an empty sequence")
    if not 0 <= q <= 1:
        raise ValueError("q must be between 0 and 1")
    xs = sorted(float(value) for value in values)
    if len(xs) == 1:
        return xs[0]
    position = (len(xs) - 1) * q
    lo = math.floor(position)
    hi = math.ceil(position)
    if lo == hi:
        return xs[lo]
    weight = position - lo
    return xs[lo] * (1.0 - weight) + xs[hi] * weight


def _result_summary(rows: list[dict], *, id_field: str) -> dict:
    relative = [row["relative_excess_cagr"] for row in rows]
    return {
        "n": len(rows),
        "ids": [row[id_field] for row in rows],
        "relative_excess_cagr_q25": _linear_quantile(relative, 0.25),
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
) -> dict:
    """Validate and summarize one candidate without inventing a scalar objective."""
    expected = build_candidate_spec(candidate.get("model_params", {}))
    if expected["candidate_id"] != candidate.get("candidate_id"):
        raise ValueError("candidate_id does not match candidate semantics")

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
    if len(folds) != SCREEN_FOLDS:
        raise ValueError(f"Stage-A candidate requires exactly {SCREEN_FOLDS} fold results")
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
            if phase_id in seen_phases:
                raise ValueError(f"duplicate phase_id {phase_id}")
            seen_phases.add(phase_id)
            row = _validate_result(raw, label=f"phase {phase_id}")
            row["phase_id"] = phase_id
            phases.append(row)
        if seen_phases != set(REFERENCE_PHASES):
            raise ValueError(f"phase results must equal fixed set {list(REFERENCE_PHASES)}")
        phases.sort(key=lambda row: row["phase_id"])

    return {
        "candidate_id": candidate["candidate_id"],
        "is_baseline": bool(candidate.get("is_baseline")),
        "candidate": deepcopy(candidate),
        "fold_summary": _result_summary(folds, id_field="fold_id"),
        "phase_summary": None if phases is None else _result_summary(phases, id_field="phase_id"),
    }


def _rank_vector(metric_summary: dict) -> tuple[float, ...]:
    return tuple(
        float(metric_summary[field]) * direction
        for field, direction in SCREEN_RANK_SPECS
    )


def ranking_contract(stage: str = "screen") -> dict:
    """Machine-readable ranking metadata derived from the actual rank spec."""
    if stage not in {"screen", "final"}:
        raise ValueError("stage must be 'screen' or 'final'")
    order = [
        {
            "field": field,
            "direction": "higher" if direction > 0 else "lower",
        }
        for field, direction in SCREEN_RANK_SPECS
    ]
    if stage == "screen":
        return {
            "primary_scope": "temporal_folds",
            "order": order,
            "selection": "lexicographic",
            "single_fold_winner_allowed": False,
        }
    return {
        "primary_scope": "calendar_phases",
        "phase_order": order,
        "fold_tiebreak_order": order,
        "selection": "lexicographic",
        "single_phase_winner_allowed": False,
    }


def _screen_rank_tuple(summary: dict) -> tuple[float, ...]:
    return _rank_vector(summary["fold_summary"])


def _final_rank_tuple(summary: dict) -> tuple[float, ...]:
    phase = summary.get("phase_summary")
    if phase is None or int(phase.get("n", 0)) != len(REFERENCE_PHASES):
        raise ValueError("final ranking requires the fixed phase robustness set")
    return (*_rank_vector(phase), *_screen_rank_tuple(summary))


def numeric_reproducibility_diagnostics(
    values_a: Iterable[Any],
    values_b: Iterable[Any],
    *,
    rtol: float = REPORT_REPRO_RTOL,
    atol: float = REPORT_REPRO_ATOL,
) -> dict:
    """Compare numeric vectors with explicit floating-point diagnostics.

    NaN positions must match exactly. Infinities must match exactly, including sign.
    Finite values use abs(a-b) <= atol + rtol * max(abs(a), abs(b)).
    """
    left = list(values_a)
    right = list(values_b)
    if len(left) != len(right):
        return {
            "passed": False,
            "reason": "length_mismatch",
            "n": max(len(left), len(right)),
            "mismatch_count": abs(len(left) - len(right)),
            "max_abs_diff": None,
            "max_rel_diff": None,
        }
    if rtol < 0 or atol < 0:
        raise ValueError("rtol/atol must be non-negative")

    mismatch_count = 0
    finite_count = 0
    max_abs_diff = 0.0
    max_rel_diff = 0.0
    exact_match = True
    for a_raw, b_raw in zip(left, right):
        try:
            a = float(a_raw)
            b = float(b_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("numeric reproducibility inputs must be numeric") from exc

        a_nan = math.isnan(a)
        b_nan = math.isnan(b)
        if a_nan or b_nan:
            if not (a_nan and b_nan):
                mismatch_count += 1
                exact_match = False
            continue

        if math.isinf(a) or math.isinf(b):
            if a != b:
                mismatch_count += 1
                exact_match = False
            continue

        finite_count += 1
        diff = abs(a - b)
        scale = max(abs(a), abs(b))
        rel = diff / max(scale, 1e-300)
        max_abs_diff = max(max_abs_diff, diff)
        max_rel_diff = max(max_rel_diff, rel)
        if a != b:
            exact_match = False
        if diff > float(atol) + float(rtol) * scale:
            mismatch_count += 1

    return {
        "passed": mismatch_count == 0,
        "reason": None if mismatch_count == 0 else "numeric_tolerance_exceeded",
        "n": len(left),
        "finite_count": finite_count,
        "mismatch_count": mismatch_count,
        "exact_match": exact_match,
        "rtol": float(rtol),
        "atol": float(atol),
        "max_abs_diff": max_abs_diff,
        "max_rel_diff": max_rel_diff,
    }


def validate_reproducibility_pairs(
    repeat_a: Iterable[dict],
    repeat_b: Iterable[dict],
) -> dict:
    """Require exact model/prediction identity across two independent baseline fits.

    Raw backtest report bytes are audit evidence, not a bitwise gate. The Modal
    runner performs a separate structure/numeric/canonical-metric comparison.
    """
    expected_folds = {f"fold{i + 1}" for i in range(SCREEN_FOLDS)}

    def index(rows: Iterable[dict], label: str) -> dict[str, dict]:
        out = {}
        for raw in rows:
            fold_id = str(raw.get("fold_id", "")).strip()
            if not fold_id or fold_id in out:
                raise ValueError(f"{label}: duplicate or missing fold_id {fold_id!r}")
            out[fold_id] = deepcopy(raw)
        if set(out) != expected_folds:
            raise ValueError(
                f"{label}: fold set {sorted(out)} != {sorted(expected_folds)}"
            )
        return out

    left = index(repeat_a, "repeat_a")
    right = index(repeat_b, "repeat_b")
    details = {}
    candidate_id = None
    snapshot_token = None
    for fold_id in sorted(expected_folds):
        a = left[fold_id]
        b = right[fold_id]
        for row, label in ((a, "repeat_a"), (b, "repeat_b")):
            if not (row.get("candidate") or {}).get("is_baseline"):
                raise ValueError(f"{label} {fold_id}: reproducibility gate requires baseline")
            error = _finite_number(
                row.get("account_return_max_error"),
                label=f"{label} {fold_id}.account_return_max_error",
            )
            if abs(error) > ACCOUNT_ERROR_TOLERANCE:
                raise ValueError(f"{label} {fold_id}: account consistency failed")

        if a.get("candidate_id") != b.get("candidate_id"):
            raise RuntimeError(f"{fold_id}: candidate identity mismatch")
        if a.get("snapshot_token") != b.get("snapshot_token"):
            raise RuntimeError(f"{fold_id}: provider snapshot mismatch")
        if a.get("fold") != b.get("fold"):
            raise RuntimeError(f"{fold_id}: fold definition mismatch")

        comparisons = {
            "model_config_sha256": (a.get("model_config_sha256"), b.get("model_config_sha256")),
            "best_iteration": (a.get("best_iteration"), b.get("best_iteration")),
            "signal_sha256": (a.get("signal_sha256"), b.get("signal_sha256")),
            "decision_content_sha256": (
                (a.get("decision_artifact") or {}).get("content_sha256"),
                (b.get("decision_artifact") or {}).get("content_sha256"),
            ),
        }
        mismatches = [
            key for key, (value_a, value_b) in comparisons.items()
            if value_a is None or value_a != value_b
        ]
        if mismatches:
            raise RuntimeError(
                f"{fold_id}: reproducibility mismatch in {mismatches}"
            )

        candidate_id = candidate_id or a["candidate_id"]
        snapshot_token = snapshot_token or a["snapshot_token"]
        if candidate_id != a["candidate_id"] or snapshot_token != a["snapshot_token"]:
            raise RuntimeError("reproducibility gate mixes candidate or snapshot lineages")
        report_a = (a.get("report_artifact") or {}).get("content_sha256")
        report_b = (b.get("report_artifact") or {}).get("content_sha256")
        details[fold_id] = {
            "best_iteration": a.get("best_iteration"),
            "signal_sha256": a["signal_sha256"],
            "decision_content_sha256": a["decision_artifact"]["content_sha256"],
            "report_content_sha256_a": report_a,
            "report_content_sha256_b": report_b,
            "report_exact_match": bool(report_a and report_a == report_b),
        }

    return {
        "passed": True,
        "gate_version": REPRO_GATE_VERSION,
        "candidate_id": candidate_id,
        "snapshot_token": snapshot_token,
        "folds": details,
        "fits_per_repeat": SCREEN_FOLDS,
        "total_gate_fits": 2 * SCREEN_FOLDS,
    }


def rank_candidates(summaries: Iterable[dict], *, stage: str = "screen") -> list[dict]:
    """Rank robustly and deterministically; no one-fold or one-phase winner rule."""
    rows = [deepcopy(row) for row in summaries]
    if not rows:
        raise ValueError("at least one candidate summary is required")
    if stage not in {"screen", "final"}:
        raise ValueError("stage must be 'screen' or 'final'")
    ids = [row.get("candidate_id") for row in rows]
    if any(not value for value in ids) or len(ids) != len(set(ids)):
        raise ValueError("candidate_id values must be unique and non-empty")
    rank_key = _screen_rank_tuple if stage == "screen" else _final_rank_tuple

    # Stable deterministic tie-break: candidate hash ascending.
    rows.sort(key=lambda row: row["candidate_id"])
    rows.sort(key=rank_key, reverse=True)
    for position, row in enumerate(rows, start=1):
        row["rank"] = position
        row["ranking_stage"] = stage
        row["ranking_vector"] = list(rank_key(row))
    return rows


def select_stage_b_candidates(summaries: Iterable[dict], *, top_n: int = 8) -> list[dict]:
    """Promote the robust top-N while always retaining the frozen baseline."""
    if top_n < 1:
        raise ValueError("top_n must be positive")
    ranked = rank_candidates(summaries, stage="screen")
    selected = ranked[:top_n]
    baseline = next((row for row in ranked if row.get("is_baseline")), None)
    if baseline is None:
        raise ValueError("candidate summaries do not contain the frozen baseline")
    if all(row["candidate_id"] != baseline["candidate_id"] for row in selected):
        selected = selected[:-1] + [baseline] if len(selected) >= top_n else selected + [baseline]
        selected = rank_candidates(selected, stage="screen")
    return selected
