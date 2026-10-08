"""Frozen CSI1000 production model profiles after Stage-B confirmation.

This module is the bridge from the accepted Stage-B research result to the
forward/paper production path.  It deliberately contains no selection logic:
the winner is already frozen by the Stage-B result and must not be re-tuned
against the consumed 2025-01-02..2026-09-30 confirmation tail.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy

STAGE_B_RESULT_COMMIT = "7a2676397b0f8e6f69c0bffc98d1764647f644ac"
STAGE_B_SNAPSHOT_TOKEN = (
    "51756897fc75230493194aef2e48815e4e9f3cc7426cc135f47bb8ba8e0d21e1"
)
STAGE_B_RESULT_PATH = (
    "results/csi1000_stage_b/stage_b_full_51756897fc752304.json"
)
PRODUCTION_CONTRACT_VERSION = "csi1000_stage_b_forward_v1"
MODEL_NUM_THREADS = 20

BASELINE_PROFILE = "baseline_control"
WINNER_PROFILE = "stage_b_winner"

_MODEL_PROFILES = {
    BASELINE_PROFILE: {
        "candidate_id": "23b92de05cf36c82998de684d0fbf64d81ee54490d755bd3cf96311c00286785",
        "stage_a_rank": 48,
        "model_params": {
            "learning_rate": 0.1,
            "colsample_bytree": 0.9,
            "lambda_l1": 205.6999,
            "lambda_l2": 580.9768,
            "max_depth": 8,
            "num_leaves": 250,
            "min_data_in_leaf": 20,
        },
        "model_config_sha256": "793021af66034ec508848ee032f6d309103291981abb63d11ae779da9c5ee7d3",
    },
    WINNER_PROFILE: {
        "candidate_id": "4e908173705c76fee3782be37068672a3a845bd61894202f3152afe3b9d81ef2",
        "stage_a_rank": 5,
        "model_params": {
            "learning_rate": 0.08,
            "colsample_bytree": 0.8,
            "lambda_l1": 10.0,
            "lambda_l2": 400.0,
            "max_depth": 6,
            "num_leaves": 63,
            "min_data_in_leaf": 50,
        },
        "model_config_sha256": "0f2cf94d179e7b9982f873588a3afe25fa02991c873954fea11d2db0e025cb88",
    },
}


def stable_json_sha256(obj) -> str:
    return hashlib.sha256(
        json.dumps(
            obj,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=str,
        ).encode()
    ).hexdigest()


def profile_manifest(profile: str) -> dict:
    if profile not in _MODEL_PROFILES:
        raise ValueError(f"unknown CSI1000 production profile: {profile}")
    row = deepcopy(_MODEL_PROFILES[profile])
    row.update(
        {
            "profile": profile,
            "contract_version": PRODUCTION_CONTRACT_VERSION,
            "stage_b_result_commit": STAGE_B_RESULT_COMMIT,
            "stage_b_snapshot_token": STAGE_B_SNAPSHOT_TOKEN,
            "stage_b_result_path": STAGE_B_RESULT_PATH,
            "lightgbm_num_threads": MODEL_NUM_THREADS,
        }
    )
    return row


def apply_csi1000_model_profile(cfg: dict, profile: str) -> dict:
    """Overlay one frozen Stage-B model profile and fail closed on config drift."""
    from qlib_live_retrain import apply_lgb_reproducibility

    manifest = profile_manifest(profile)
    model = cfg.get("task", {}).get("model", {})
    if model.get("class") != "LGBModel":
        raise ValueError("CSI1000 production profile requires LGBModel")
    kwargs = model.setdefault("kwargs", {})
    kwargs.update(manifest["model_params"])
    kwargs["num_threads"] = MODEL_NUM_THREADS
    kwargs.pop("n_jobs", None)
    apply_lgb_reproducibility(cfg)

    actual_sha = stable_json_sha256(model)
    expected_sha = manifest["model_config_sha256"]
    if actual_sha != expected_sha:
        raise RuntimeError(
            f"CSI1000 production model config drift for {profile}: "
            f"{actual_sha} != {expected_sha}"
        )
    return manifest
