"""Pure-Python contract for CSI1000 Stage-B confirmation.

Stage B is not another hyperparameter search. It freezes the Stage-A full-rank
top-10 candidates plus the pre-tuner baseline control, evaluates the untouched
reserved tail with production-aligned freq20 rolling, and ranks candidates by
retraining-calendar robustness across fixed phases.
"""
from __future__ import annotations

from bisect import bisect_left
from copy import deepcopy
import math
from statistics import median
from typing import Any, Iterable


STAGE_B_PROTOCOL_VERSION = "csi1000_lgb_stage_b_v1"
STAGE_A_PROTOCOL_VERSION = "csi1000_lgb_stage_a_v3"
STAGE_A_SNAPSHOT_TOKEN = (
    "a2ecd8d1cadf404670762a81b1fd33a377f2ef2aaf27a0d9ac6787366defd65b"
)
STAGE_A_EXPANDED_RESULT = (
    "results/csi1000_tuner/stage_a_expanded_a2ecd8d1cadf4046.json"
)
STAGE_A_EXPANDED_AUDIT = (
    "results/csi1000_tuner/stage_a_expanded_audit_a2ecd8d1cadf4046.json"
)

MARKET = "csi1000"
BENCHMARK = "SH000852"
TARGET = "raw_20d"
LABEL_HORIZON = 20
RETRAIN_FREQUENCY = 20
VALIDATION_SESSIONS = 252
TOPK = 20
N_DROP = 2
EXECUTION = "t_close_t1_open"
STRATEGY = "deterministic_topk_dropout_v1"
TIE_BREAK = "score_desc_instrument_asc"
TRAIN_START = "2016-01-01"

RESERVED_SIGNAL_ANCHOR = "2024-12-31"
RESERVED_EXECUTION_START = "2025-01-02"
RESERVED_EXECUTION_END = "2026-09-30"
REFERENCE_PHASES = (0, 4, 6, 10, 15)

# Starter-safe resources. Stage A already ran successfully with 8 Modal physical
# CPU cores and 24 GiB. Modal documents one CPU unit as one physical core
# (~2 conventional vCPU), so keep the proven allocation and preserve the
# Stage-A LightGBM num_threads=20 training semantics.
WORKER_CPU = 8
WORKER_MEMORY_MIB = 16384
WORKER_MAX_CONTAINERS = 64
STARTER_CONTAINER_LIMIT = 100
WORKER_CPU_MIN = 4
WORKER_CPU_MAX = 8
WORKER_MEMORY_MIB_MIN = 12288
WORKER_MEMORY_MIB_MAX = 24576
WORKER_RETRIES = 2
# Stage-A v3 actually trained from the checked-in LightGBM YAML with
# num_threads=20.  Do not confuse Modal worker CPU (8 physical cores) with
# LightGBM's model parameter.  Stage-B must preserve the Stage-A model config
# exactly; _runtime_manifest additionally checks base_model_config_sha256.
MODEL_NUM_THREADS = 20

REPORT_REPRO_RTOL = 1e-10
REPORT_REPRO_ATOL = 1e-12
ACCOUNT_ERROR_TOLERANCE = 1e-12

PHASE_RANK_SPECS = (
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

BASELINE_CANDIDATE_ID = (
    "23b92de05cf36c82998de684d0fbf64d81ee54490d755bd3cf96311c00286785"
)
BASELINE_MODEL_PARAMS = {
    "learning_rate": 0.1,
    "colsample_bytree": 0.9,
    "lambda_l1": 205.6999,
    "lambda_l2": 580.9768,
    "max_depth": 8,
    "num_leaves": 250,
    "min_data_in_leaf": 20,
}

# Frozen after the accepted Stage-A 80-candidate expansion.
_TUNED_CANDIDATES = (
    (1, "59b93929bff1e736228d31403a167781aff433d27da1870bcf9fe34e04b6bbb9",
     {"learning_rate": 0.1, "colsample_bytree": 0.8, "lambda_l1": 10.0,
      "lambda_l2": 400.0, "max_depth": 6, "num_leaves": 31,
      "min_data_in_leaf": 100}),
    (2, "a419047ab97ec0273ebe67791b6e225e824b2764f5932d73651efd89add5a677",
     {"learning_rate": 0.1, "colsample_bytree": 1.0, "lambda_l1": 50.0,
      "lambda_l2": 200.0, "max_depth": 6, "num_leaves": 31,
      "min_data_in_leaf": 200}),
    (3, "b034ac4645fb45bba35bc185693b7776344c9117f594e4c972671062cdcf9457",
     {"learning_rate": 0.1, "colsample_bytree": 0.8, "lambda_l1": 50.0,
      "lambda_l2": 200.0, "max_depth": 8, "num_leaves": 31,
      "min_data_in_leaf": 200}),
    (4, "e74b3bbe2278aa89b33375fe21bc308bc5cf0b2e5ee512ab1b05469b52a4825a",
     {"learning_rate": 0.1, "colsample_bytree": 0.9, "lambda_l1": 100.0,
      "lambda_l2": 400.0, "max_depth": 6, "num_leaves": 31,
      "min_data_in_leaf": 200}),
    (5, "4e908173705c76fee3782be37068672a3a845bd61894202f3152afe3b9d81ef2",
     {"learning_rate": 0.08, "colsample_bytree": 0.8, "lambda_l1": 10.0,
      "lambda_l2": 400.0, "max_depth": 6, "num_leaves": 63,
      "min_data_in_leaf": 50}),
    (6, "17dbfe28fea8d7a782918ee426e01c7f7b23a08b44c00cc7e2d8391974efd5f2",
     {"learning_rate": 0.08, "colsample_bytree": 0.8, "lambda_l1": 10.0,
      "lambda_l2": 100.0, "max_depth": 10, "num_leaves": 127,
      "min_data_in_leaf": 100}),
    (7, "c04ca4163f77e6e24a7c7adf556d5f11b85ab526d5c3e8b2c9dd11c2bfca7b46",
     {"learning_rate": 0.1, "colsample_bytree": 1.0, "lambda_l1": 50.0,
      "lambda_l2": 200.0, "max_depth": 10, "num_leaves": 127,
      "min_data_in_leaf": 50}),
    (8, "c98856b460aba687640d422e85a82a545bf5b6ca932e906a180699e0f6cad19b",
     {"learning_rate": 0.05, "colsample_bytree": 1.0, "lambda_l1": 205.6999,
      "lambda_l2": 200.0, "max_depth": 10, "num_leaves": 250,
      "min_data_in_leaf": 200}),
    (9, "1db146eec9deda008be3e6612a1fc48a057e34b7891d3d70ca69a6e4c4fe62a2",
     {"learning_rate": 0.1, "colsample_bytree": 1.0, "lambda_l1": 205.6999,
      "lambda_l2": 400.0, "max_depth": 8, "num_leaves": 63,
      "min_data_in_leaf": 20}),
    (10, "40b872dfe452b649e666598d7438c0c342b6de45b9d8fa4df4d06e4080ab9d19",
     {"learning_rate": 0.03, "colsample_bytree": 0.7, "lambda_l1": 100.0,
      "lambda_l2": 50.0, "max_depth": 10, "num_leaves": 63,
      "min_data_in_leaf": 20}),
)

_REQUIRED_PHASE_METRICS = (
    "relative_excess_cagr",
    "strategy_max_drawdown",
    "sharpe",
    "information_ratio",
    "account_return_max_error",
    "mean_turnover",
    "total_cost_sum",
)


def validate_worker_resources(
    *,
    cpu: float,
    memory_mib: int,
    max_containers: int,
) -> dict:
    """Validate execution-only Modal resource overrides.

    These settings affect throughput/capacity, not candidate/model semantics.
    Keep them within the deliberately bounded envelope established for the
    Stage-B preflight rather than allowing arbitrary large Starter requests.
    """
    cpu_value = float(cpu)
    memory_value = int(memory_mib)
    container_value = int(max_containers)
    if not (WORKER_CPU_MIN <= cpu_value <= WORKER_CPU_MAX):
        raise ValueError(
            f"worker_cpu must be within {WORKER_CPU_MIN}..{WORKER_CPU_MAX}"
        )
    if not (WORKER_MEMORY_MIB_MIN <= memory_value <= WORKER_MEMORY_MIB_MAX):
        raise ValueError(
            "worker_memory_mib must be within "
            f"{WORKER_MEMORY_MIB_MIN}..{WORKER_MEMORY_MIB_MAX}"
        )
    if not (1 <= container_value <= STARTER_CONTAINER_LIMIT):
        raise ValueError(
            f"worker_max_containers must be within 1..{STARTER_CONTAINER_LIMIT}"
        )
    return {
        "retrain_worker_cpu_physical_cores": cpu_value,
        "retrain_worker_memory_mib": memory_value,
        "retrain_worker_max_containers": container_value,
        "lightgbm_num_threads": MODEL_NUM_THREADS,
    }


def frozen_stage_b_protocol() -> dict:
    return {
        "protocol": STAGE_B_PROTOCOL_VERSION,
        "source_stage_a_protocol": STAGE_A_PROTOCOL_VERSION,
        "source_stage_a_snapshot_token": STAGE_A_SNAPSHOT_TOKEN,
        "market": MARKET,
        "benchmark": BENCHMARK,
        "target": TARGET,
        "label_horizon": LABEL_HORIZON,
        "retrain_frequency": RETRAIN_FREQUENCY,
        "validation_sessions": VALIDATION_SESSIONS,
        "portfolio": {"topk": TOPK, "n_drop": N_DROP},
        "execution": EXECUTION,
        "strategy": STRATEGY,
        "tie_break": TIE_BREAK,
        "train_start": TRAIN_START,
        "reserved_signal_anchor": RESERVED_SIGNAL_ANCHOR,
        "reserved_execution_start": RESERVED_EXECUTION_START,
        "reserved_execution_end": RESERVED_EXECUTION_END,
        "reference_phases": list(REFERENCE_PHASES),
        "selection_rule": (
            "stage_a_full_rank_top10_and_lofo_top10_in_at_least_3_of_4"
            "_plus_baseline_control"
        ),
        "tuned_candidate_count": 10,
        "control_candidate_count": 1,
    }


def stage_b_candidates() -> list[dict]:
    rows = [
        {
            "candidate_id": candidate_id,
            "stage_a_rank": rank,
            "is_baseline": False,
            "model_params": deepcopy(params),
        }
        for rank, candidate_id, params in _TUNED_CANDIDATES
    ]
    rows.append(
        {
            "candidate_id": BASELINE_CANDIDATE_ID,
            "stage_a_rank": 48,
            "is_baseline": True,
            "model_params": deepcopy(BASELINE_MODEL_PARAMS),
        }
    )
    return rows


def validate_stage_a_evidence(expanded: dict, audit: dict) -> dict:
    """Fail closed if committed Stage-A evidence no longer supports the freeze."""
    if expanded.get("protocol") != STAGE_A_PROTOCOL_VERSION:
        raise ValueError("Stage-A evidence protocol mismatch")
    if expanded.get("screen_kind") != "expanded":
        raise ValueError("Stage-A evidence must be the expanded screen")
    if int(expanded.get("candidate_count", -1)) != 80:
        raise ValueError("Stage-A evidence must contain exactly 80 candidates")
    if (expanded.get("manifest") or {}).get("snapshot_token") != STAGE_A_SNAPSHOT_TOKEN:
        raise ValueError("Stage-A evidence snapshot mismatch")
    if not (expanded.get("reproducibility_gate") or {}).get("passed"):
        raise ValueError("Stage-A reproducibility gate is not passing")
    if not audit.get("all_candidate_fold_results_present"):
        raise ValueError("Stage-A expanded audit is incomplete")

    ranked = expanded.get("ranked_candidates") or []
    by_id = {row.get("candidate_id"): row for row in ranked}
    if len(by_id) != 80:
        raise ValueError("Stage-A ranking is incomplete or contains duplicate IDs")

    audit_ranked = audit.get("recomputed_full_rank") or []
    if len(audit_ranked) != 80:
        raise ValueError("Stage-A audit recomputed ranking is incomplete")
    if [row.get("candidate_id") for row in audit_ranked] != [
        row.get("candidate_id") for row in ranked
    ]:
        raise ValueError("Stage-A expanded ranking disagrees with independent audit ranking")

    tuned = [row for row in stage_b_candidates() if not row["is_baseline"]]
    if [row.get("candidate_id") for row in ranked[:10]] != [
        row["candidate_id"] for row in tuned
    ]:
        raise ValueError("frozen tuned set no longer equals Stage-A full-rank top 10")

    lofo = audit.get("leave_one_fold_out_rankings") or {}
    if set(lofo) != {"fold1", "fold2", "fold3", "fold4"}:
        raise ValueError("Stage-A audit must contain all four LOFO rankings")

    lofo_top10_counts = {}
    for expected in stage_b_candidates():
        row = by_id.get(expected["candidate_id"])
        if row is None:
            raise ValueError(f"frozen candidate missing: {expected['candidate_id']}")
        if int(row.get("rank", -1)) != expected["stage_a_rank"]:
            raise ValueError(f"Stage-A rank drift for {expected['candidate_id']}")
        if bool(row.get("is_baseline")) != expected["is_baseline"]:
            raise ValueError("Stage-A baseline flag drift")
        params = (row.get("candidate") or {}).get("model_params") or {}
        if set(params) != set(expected["model_params"]):
            raise ValueError("Stage-A model-param key drift")
        for key, value in expected["model_params"].items():
            if float(params[key]) != float(value):
                raise ValueError(
                    f"Stage-A model-param drift for {expected['candidate_id']}.{key}"
                )
        if not expected["is_baseline"]:
            count = 0
            for ranking in lofo.values():
                lofo_row = next(
                    (item for item in ranking if item.get("candidate_id") == expected["candidate_id"]),
                    None,
                )
                if lofo_row is None:
                    raise ValueError("candidate missing from LOFO ranking")
                count += int(int(lofo_row.get("rank", 9999)) <= 10)
            if count < 3:
                raise ValueError(
                    f"frozen tuned candidate fails 3/4 LOFO-top10 rule: "
                    f"{expected['candidate_id']} ({count}/4)"
                )
            lofo_top10_counts[expected["candidate_id"]] = count

    return {
        "passed": True,
        "stage_a_candidate_count": 80,
        "frozen_tuned_ids": [row["candidate_id"] for row in tuned],
        "baseline_id": BASELINE_CANDIDATE_ID,
        "baseline_rank": by_id[BASELINE_CANDIDATE_ID]["rank"],
        "lofo_top10_counts": lofo_top10_counts,
    }


def _calendar_index(calendar: list[str], date: str) -> int:
    i = bisect_left(calendar, date)
    if i >= len(calendar) or calendar[i] != date:
        raise ValueError(f"date {date!r} is not in trading calendar")
    return i


def validate_reserved_tail(calendar: list[str]) -> dict:
    if calendar != sorted(set(calendar)):
        raise ValueError("trading calendar must be sorted and unique")
    anchor_i = _calendar_index(calendar, RESERVED_SIGNAL_ANCHOR)
    execution_start_i = _calendar_index(calendar, RESERVED_EXECUTION_START)
    execution_end_i = _calendar_index(calendar, RESERVED_EXECUTION_END)
    if execution_start_i != anchor_i + 1:
        raise ValueError("reserved execution must start one trading session after signal anchor")
    if execution_end_i != len(calendar) - 1:
        raise ValueError("provider calendar must end exactly at frozen reserved-tail cutoff")
    return {
        "signal_anchor": RESERVED_SIGNAL_ANCHOR,
        "execution_start": RESERVED_EXECUTION_START,
        "execution_end": RESERVED_EXECUTION_END,
        "n_execution_sessions": execution_end_i - execution_start_i + 1,
    }


def build_phase_jobs(calendar: list[str], phase: int) -> list[dict]:
    """Build one freq20 lineage needed for the common reserved-tail evaluation."""
    if phase not in REFERENCE_PHASES:
        raise ValueError(f"phase must be one of {list(REFERENCE_PHASES)}")
    validate_reserved_tail(calendar)
    anchor_i = _calendar_index(calendar, RESERVED_SIGNAL_ANCHOR)
    last_signal_i = _calendar_index(calendar, RESERVED_EXECUTION_END) - 1
    first_i = anchor_i - phase
    if first_i < 0:
        raise ValueError("insufficient history for requested phase")

    jobs = []
    i = first_i
    while i <= last_signal_i:
        signal_end_i = min(i + RETRAIN_FREQUENCY - 1, last_signal_i)
        jobs.append(
            {
                "phase": phase,
                "retrain_asof": calendar[i],
                "signal_end": calendar[signal_end_i],
            }
        )
        i += RETRAIN_FREQUENCY

    if not jobs:
        raise RuntimeError("phase generated no retraining jobs")
    if jobs[-1]["signal_end"] != calendar[last_signal_i]:
        raise RuntimeError("phase jobs do not cover final executable signal day")
    if not (
        jobs[0]["retrain_asof"] <= RESERVED_SIGNAL_ANCHOR <= jobs[0]["signal_end"]
    ):
        raise RuntimeError("first phase job does not cover common signal anchor")
    return jobs


def stage_b_fit_budget(calendar: list[str], *, include_preflight: bool = True) -> dict:
    per_phase = {phase: len(build_phase_jobs(calendar, phase)) for phase in REFERENCE_PHASES}
    candidate_count = len(stage_b_candidates())
    full = candidate_count * sum(per_phase.values())
    preflight = 2 * per_phase[0] if include_preflight else 0
    # The passing preflight phase0 baseline is reused by the full run.
    full_new_after_preflight = full - per_phase[0] if include_preflight else full
    return {
        "candidate_count": candidate_count,
        "phase_count": len(REFERENCE_PHASES),
        "retrain_jobs_per_phase": per_phase,
        "preflight_model_fits": preflight,
        "full_grid_model_fits": full,
        "full_grid_new_fits_after_preflight_reuse": full_new_after_preflight,
        "total_new_fits_preflight_plus_full": preflight + full_new_after_preflight,
    }


def _finite(value: Any, label: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(out):
        raise ValueError(f"{label} must be finite")
    return out


def _linear_quantile(values: list[float], q: float) -> float:
    xs = sorted(float(value) for value in values)
    if not xs:
        raise ValueError("cannot take quantile of empty values")
    position = (len(xs) - 1) * q
    lo = math.floor(position)
    hi = math.ceil(position)
    if lo == hi:
        return xs[lo]
    weight = position - lo
    return xs[lo] * (1 - weight) + xs[hi] * weight


def summarize_phase_results(rows: Iterable[dict]) -> dict:
    indexed = {}
    for raw in rows:
        phase = int(raw.get("phase"))
        if phase in indexed:
            raise ValueError(f"duplicate phase result {phase}")
        if phase not in REFERENCE_PHASES:
            raise ValueError(f"unexpected phase result {phase}")
        row = deepcopy(raw)
        for key in _REQUIRED_PHASE_METRICS:
            row[key] = _finite(row.get(key), f"phase{phase}.{key}")
        if abs(row["account_return_max_error"]) > ACCOUNT_ERROR_TOLERANCE:
            raise ValueError(f"phase{phase}: account consistency failed")
        if row["strategy_max_drawdown"] > ACCOUNT_ERROR_TOLERANCE:
            raise ValueError(f"phase{phase}: max drawdown must be <= 0")
        if row["mean_turnover"] < 0 or row["total_cost_sum"] < 0:
            raise ValueError(f"phase{phase}: turnover/cost must be non-negative")
        indexed[phase] = row
    if set(indexed) != set(REFERENCE_PHASES):
        raise ValueError("Stage-B candidate requires exactly the fixed five phases")

    ordered = [indexed[phase] for phase in REFERENCE_PHASES]
    rel = [row["relative_excess_cagr"] for row in ordered]
    return {
        "n": len(ordered),
        "ids": list(REFERENCE_PHASES),
        "relative_excess_cagr_worst": min(rel),
        "relative_excess_cagr_q25": _linear_quantile(rel, 0.25),
        "relative_excess_cagr_median": float(median(rel)),
        "positive_ratio": sum(value > 0 for value in rel) / len(rel),
        "information_ratio_median": float(
            median(row["information_ratio"] for row in ordered)
        ),
        "sharpe_median": float(median(row["sharpe"] for row in ordered)),
        "strategy_max_drawdown_worst": min(
            row["strategy_max_drawdown"] for row in ordered
        ),
        "mean_turnover_median": float(
            median(row["mean_turnover"] for row in ordered)
        ),
        "total_cost_sum_median": float(
            median(row["total_cost_sum"] for row in ordered)
        ),
    }


def phase_ranking_contract() -> dict:
    return {
        "primary_scope": "reserved_tail_calendar_phases",
        "phases": list(REFERENCE_PHASES),
        "selection": "lexicographic",
        "order": [
            {
                "field": field,
                "direction": "higher" if direction > 0 else "lower",
            }
            for field, direction in PHASE_RANK_SPECS
        ],
        "stage_a_tiebreak": "lower frozen Stage-A rank",
        "single_phase_winner_allowed": False,
        "reserved_tail_is_final_confirmation_not_retuning_data": True,
    }


def rank_stage_b_candidates(rows: Iterable[dict]) -> list[dict]:
    candidates = [deepcopy(row) for row in rows]
    if not candidates:
        raise ValueError("no Stage-B candidate results")
    frozen = {row["candidate_id"]: row for row in stage_b_candidates()}
    ids = [row.get("candidate_id") for row in candidates]
    if set(ids) != set(frozen) or len(ids) != len(frozen):
        raise ValueError("Stage-B result set must equal the frozen 11 candidates")

    for row in candidates:
        expected = frozen[row["candidate_id"]]
        if int(row.get("stage_a_rank", -1)) != expected["stage_a_rank"]:
            raise ValueError("Stage-A tiebreak rank drift")
        summary = summarize_phase_results(row.get("phase_results") or [])
        row["phase_summary"] = summary
        row["ranking_vector"] = [
            float(summary[field]) * direction
            for field, direction in PHASE_RANK_SPECS
        ]

    # candidate ID ascending is the final deterministic tie break.
    candidates.sort(key=lambda row: row["candidate_id"])
    candidates.sort(key=lambda row: int(row["stage_a_rank"]))
    candidates.sort(key=lambda row: tuple(row["ranking_vector"]), reverse=True)
    for rank, row in enumerate(candidates, start=1):
        row["rank"] = rank
        row["ranking_stage"] = "stage_b_final"
    return candidates
