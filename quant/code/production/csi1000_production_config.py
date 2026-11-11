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
STAGE_B_SNAPSHOT_TOKEN = "51756897fc75230493194aef2e48815e4e9f3cc7426cc135f47bb8ba8e0d21e1"
STAGE_B_RESULT_PATH = "quant/data/round1_stage_ab/csi1000_stage_b/stage_b_full_51756897fc752304.json"
PRODUCTION_CONTRACT_VERSION = "csi1000_stage_b_forward_v1"
MODEL_NUM_THREADS = 20

BASELINE_PROFILE = "baseline_control"
WINNER_PROFILE = "stage_b_winner"
TOP10_CHAMPION_PROFILE = "c98856b4_top10_drop1"
CSI500_CHAMPION_PROFILE = "raw10d_c98856b4_top10_drop1"
CSI300_CHAMPION_PROFILE = "59b93929_top10_drop1"
CANONICAL_PROFILE = TOP10_CHAMPION_PROFILE
PAPER_INITIAL_CASH = 1_000_000
CANONICAL_PAPER_LINEAGE = "c98856b4_top10_drop1_canonical_1m"
CANONICAL_TOPK = 10
CANONICAL_ND = 1

CSI300_B_PROFILE = "59b93929_top10_drop1_10d"
CSI500_B_PROFILE = "raw10d_c98856b4_top20_drop2_20d"

# New-strategy production mapping (cut over together with 1M accounts).
CANONICAL_PROFILES_BY_MARKET = {
    "csi1000": TOP10_CHAMPION_PROFILE,
    "csi500": CSI500_B_PROFILE,
    "csi300": CSI300_B_PROFILE,
}

CANONICAL_TOPK_BY_MARKET = {"csi1000": 10, "csi500": 20, "csi300": 10}
CANONICAL_ND_BY_MARKET = {"csi1000": 1, "csi500": 2, "csi300": 1}

CANONICAL_PAPER_LINEAGES_BY_MARKET = {
    "csi1000": "c98856b4_top10_drop1_canonical_1m",
    "csi500": "raw10d_c98856b4_top20_drop2_20d_canonical_1m",
    "csi300": "59b93929_top10_drop1_10d_canonical_1m",
}

LEGACY_CANONICAL_PAPER_LINEAGES_BY_MARKET = {
    "csi1000": "c98856b4_top10_drop1_canonical",
    "csi500": "raw10d_c98856b4_top10_drop1_canonical",
    "csi300": "59b93929_top10_drop1_canonical",
}

_MODEL_PROFILES = {
    BASELINE_PROFILE: {
        "candidate_id": "23b92de05cf36c82998de684d0fbf64d81ee54490d755bd3cf96311c00286785",
        "stage_a_rank": 48,
        "label_horizon": 20,
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
        "label_horizon": 20,
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
    TOP10_CHAMPION_PROFILE: {
        "candidate_id": "c98856b460aba687640d422e85a82a545bf5b6ca932e906a180699e0f6cad19b",
        "stage_a_rank": 8,
        "topk": 10,
        "nd": 1,
        "label_horizon": 20,
        "model_params": {
            "learning_rate": 0.05,
            "colsample_bytree": 1.0,
            "lambda_l1": 205.6999,
            "lambda_l2": 200.0,
            "max_depth": 10,
            "num_leaves": 250,
            "min_data_in_leaf": 200,
        },
        "model_config_sha256": "d49e893ad37c821a6a092fb17baf7ee29c9ff71289bbeb1205132e34e3854c50",
        "config_sha256": "1ca6fae56e8c4e40c73b1a45866825930aecba36ec529544a50da5408c93d317",
    },
    CSI500_CHAMPION_PROFILE: {
        "candidate_id": "99627a9d20842f5ac43874593b71b175cc533fe2f09666a9417a589f35c7eb1f",
        "stage_a_rank": 8,
        "topk": 10,
        "nd": 1,
        "label_horizon": 10,
        "model_params": {
            "learning_rate": 0.05,
            "colsample_bytree": 1.0,
            "lambda_l1": 205.6999,
            "lambda_l2": 200.0,
            "max_depth": 10,
            "num_leaves": 250,
            "min_data_in_leaf": 200,
        },
        "model_config_sha256": "d49e893ad37c821a6a092fb17baf7ee29c9ff71289bbeb1205132e34e3854c50",
    },
    CSI300_CHAMPION_PROFILE: {
        "candidate_id": "59b93929bff1e736228d31403a167781aff433d27da1870bcf9fe34e04b6bbb9",
        "stage_a_rank": 1,
        "topk": 10,
        "nd": 1,
        "label_horizon": 20,
        "model_params": {
            "learning_rate": 0.1,
            "colsample_bytree": 0.8,
            "lambda_l1": 10.0,
            "lambda_l2": 400.0,
            "max_depth": 6,
            "num_leaves": 31,
            "min_data_in_leaf": 100,
        },
        "model_config_sha256": "b97bc44d1c02acb4d85c344d2d570e00ef9ff365228c8a9550fa45393859ae67",
    },
    CSI300_B_PROFILE: {
        "candidate_id": "59b93929bff1e736228d31403a167781aff433d27da1870bcf9fe34e04b6bbb9",
        "stage_a_rank": 1,
        "topk": 10,
        "nd": 1,
        "label_horizon": 10,
        "model_params": {
            "learning_rate": 0.1,
            "colsample_bytree": 0.8,
            "lambda_l1": 10.0,
            "lambda_l2": 400.0,
            "max_depth": 6,
            "num_leaves": 31,
            "min_data_in_leaf": 100,
        },
        "model_config_sha256": "b97bc44d1c02acb4d85c344d2d570e00ef9ff365228c8a9550fa45393859ae67",
    },
    CSI500_B_PROFILE: {
        "candidate_id": "99627a9d20842f5ac43874593b71b175cc533fe2f09666a9417a589f35c7eb1f",
        "stage_a_rank": 8,
        "topk": 20,
        "nd": 2,
        "label_horizon": 20,
        "model_params": {
            "learning_rate": 0.05,
            "colsample_bytree": 1.0,
            "lambda_l1": 205.6999,
            "lambda_l2": 200.0,
            "max_depth": 10,
            "num_leaves": 250,
            "min_data_in_leaf": 200,
        },
        "model_config_sha256": "d49e893ad37c821a6a092fb17baf7ee29c9ff71289bbeb1205132e34e3854c50",
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


STAGE_B_PROFILES = frozenset((BASELINE_PROFILE, WINNER_PROFILE, TOP10_CHAMPION_PROFILE))
PROFILE_MARKET = {profile: market for market, profile in CANONICAL_PROFILES_BY_MARKET.items()}


def profile_manifest(profile: str) -> dict:
    if profile not in _MODEL_PROFILES:
        raise ValueError(f"unknown CSI1000 production profile: {profile}")
    row = deepcopy(_MODEL_PROFILES[profile])
    row.update(
        {
            "profile": profile,
            "contract_version": "cn_equity_strategy_contract_v1",
            "lightgbm_num_threads": MODEL_NUM_THREADS,
        }
    )
    if profile in STAGE_B_PROFILES:
        row.update(
            {
                "stage_b_result_commit": STAGE_B_RESULT_COMMIT,
                "stage_b_snapshot_token": STAGE_B_SNAPSHOT_TOKEN,
                "stage_b_result_path": STAGE_B_RESULT_PATH,
                "research_lineage_version": PRODUCTION_CONTRACT_VERSION,
            }
        )
    market = PROFILE_MARKET.get(profile)
    if market is not None:
        from strategy_release import release_for

        release = release_for(market, profile)
        if release["model_config_sha256"] != row["model_config_sha256"]:
            raise ValueError(f"release model hash differs for {market}/{profile}")
        if row.get("label_horizon") != release.get("label_horizon"):
            raise ValueError(f"release label_horizon differs for {market}/{profile}")
        if row.get("topk") != release.get("topk") or row.get("nd") != release.get("dropn"):
            raise ValueError(f"release topk/dropn differs for {market}/{profile}")
        if release.get("universe") != market:
            raise ValueError(f"release universe differs for {market}/{profile}")
        row.update(
            {
                "market": market,
                "release_id": release["release_id"],
                "release_status": release["status"],
                "release_manifest_path": f"quant/data/strategy_releases/{market}.json",
                "release_manifest_sha256": release["manifest_sha256"],
                "strategy_contract_sha256": release["strategy_contract_sha256"],
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
        raise RuntimeError(f"CSI1000 production model config drift for {profile}: " f"{actual_sha} != {expected_sha}")
    horizon = int(manifest.get("label_horizon", 20))
    dh = cfg.get("task", {}).get("dataset", {}).get("kwargs", {}).get("handler", {}).get("kwargs")
    if isinstance(dh, dict):
        dh["label"] = [f"Ref($close, -{horizon})/$close - 1"]
    return manifest
