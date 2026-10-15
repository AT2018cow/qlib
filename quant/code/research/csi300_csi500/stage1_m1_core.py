"""Stage 1 (M1) offline control plane for CSI300/CSI500 research.

Pure-Python planners for 10 new candidate models (m01..m10, label horizons
10/20) plus Stage 0 anchor reuse, per-market two-phase ranking and 96-cell
scoreboard rendering. No Modal import, no remote calls, no model fitting.

Receipt read/write, replay and stress helpers are reused from
:mod:`stage0_p0_core` (single implementation, no duplication).

Roster:
- 2 markets x 10 models x 2 phases x 22 as-of = 880 new ``retrain`` jobs.
- 2 markets x 10 models x 2 combos x 2 phases = 80 new ``cell`` jobs.
- Ranking merges the 80 new cells with the 16 verified Stage 0 anchor cells
  (``baseline`` / ``c98856b4``) into 96 cells = 12 models x 2 combos x 2
  phases per two markets (48 each).
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

import stage0_p0_core as p0
from audit.csi1000_layered_search_core import require, sha256_file, stable_sha
from csi1000_stage_b_core import (
    RESERVED_EXECUTION_END,
    RESERVED_EXECUTION_START,
    RESERVED_SIGNAL_ANCHOR,
    TRAIN_START,
    VALIDATION_SESSIONS,
    build_phase_jobs,
)

RESEARCH_VOLUME = p0.RESEARCH_VOLUME
SOURCE_VOLUME = p0.SOURCE_VOLUME
MARKETS = p0.MARKETS
COMBOS = p0.COMBOS
PHASES = p0.PHASES
N_EXECUTION_DAYS = p0.N_EXECUTION_DAYS
EXPECTED_PROVIDER_FINGERPRINT = p0.EXPECTED_PROVIDER_FINGERPRINT
EXPECTED_SNAPSHOT_TOKEN = p0.EXPECTED_SNAPSHOT_TOKEN
EXPECTED_CALENDAR_DAYS = p0.EXPECTED_CALENDAR_DAYS
EXPECTED_CALENDAR_END = p0.EXPECTED_CALENDAR_END
quote_sha_of = p0.quote_sha_of

# Verified Stage 0 anchor export (same v2 research Volume).
STAGE0_RUN_ID = "stage0_p0_20261015_a1"
STAGE0_CODE_SHA = "93bab198d812ad56fec1476e524eb80d25f9da4a"
STAGE0_PREFIX = f"csi300_csi500_research/stage0_p0/{STAGE0_RUN_ID}/{STAGE0_CODE_SHA[:12]}"
STAGE0_ANCHOR_MODELS = ("baseline", "c98856b4")

# Plan-verbatim roster (all 10 model_sha256 re-verified locally 2026-10-15).
STAGE1_NEW_MODELS = {
    "m01_59b93929": {
        "candidate_id": "59b93929bff1e736228d31403a167781aff433d27da1870bcf9fe34e04b6bbb9",
        "model_sha256": "b97bc44d1c02acb4d85c344d2d570e00ef9ff365228c8a9550fa45393859ae67",
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
    },
    "m02_a419047a": {
        "candidate_id": "a419047ab97ec0273ebe67791b6e225e824b2764f5932d73651efd89add5a677",
        "model_sha256": "28596be4fbe4f24b20fe419a3cfc2016192169b81b07eec50a7181119f9726d8",
        "label_horizon": 20,
        "model_params": {
            "learning_rate": 0.1,
            "colsample_bytree": 1.0,
            "lambda_l1": 50.0,
            "lambda_l2": 200.0,
            "max_depth": 6,
            "num_leaves": 31,
            "min_data_in_leaf": 200,
        },
    },
    "m03_4e908173": {
        "candidate_id": "4e908173705c76fee3782be37068672a3a845bd61894202f3152afe3b9d81ef2",
        "model_sha256": "0f2cf94d179e7b9982f873588a3afe25fa02991c873954fea11d2db0e025cb88",
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
    },
    "m04_17dbfe28": {
        "candidate_id": "17dbfe28fea8d7a782918ee426e01c7f7b23a08b44c00cc7e2d8391974efd5f2",
        "model_sha256": "5a311bfc78b40b2758d77c1ba55c092c1953447f133a111d4ed6b28bae39cbc2",
        "label_horizon": 20,
        "model_params": {
            "learning_rate": 0.08,
            "colsample_bytree": 0.8,
            "lambda_l1": 10.0,
            "lambda_l2": 100.0,
            "max_depth": 10,
            "num_leaves": 127,
            "min_data_in_leaf": 100,
        },
    },
    "m05_e74b3bbe": {
        "candidate_id": "e74b3bbe2278aa89b33375fe21bc308bc5cf0b2e5ee512ab1b05469b52a4825a",
        "model_sha256": "e4d33fa7175a1e01068b6d4afcd8525c706079d2f9fc8d668ef41a1b8ee6eae4",
        "label_horizon": 20,
        "model_params": {
            "learning_rate": 0.1,
            "colsample_bytree": 0.9,
            "lambda_l1": 100.0,
            "lambda_l2": 400.0,
            "max_depth": 6,
            "num_leaves": 31,
            "min_data_in_leaf": 200,
        },
    },
    "m06_b034ac46": {
        "candidate_id": "b034ac4645fb45bba35bc185693b7776344c9117f594e4c972671062cdcf9457",
        "model_sha256": "d095b0fe976e8f438f086a2b3a31bbb70d8d8560aa18e1594c3b2d299329a901",
        "label_horizon": 20,
        "model_params": {
            "learning_rate": 0.1,
            "colsample_bytree": 0.8,
            "lambda_l1": 50.0,
            "lambda_l2": 200.0,
            "max_depth": 8,
            "num_leaves": 31,
            "min_data_in_leaf": 200,
        },
    },
    "m07_shallow_leaf15": {
        "candidate_id": "364c2d58afb727d7051f0910e0ebe476caec55a271cd314faeda240e3c8b8ced",
        "model_sha256": "5c4543183558ada6b58ccb0833e7422d9643d643914d3d8f69dd766018deb1b9",
        "label_horizon": 20,
        "model_params": {
            "learning_rate": 0.05,
            "colsample_bytree": 0.7,
            "lambda_l1": 20.0,
            "lambda_l2": 100.0,
            "max_depth": 4,
            "num_leaves": 15,
            "min_data_in_leaf": 50,
        },
    },
    "m08_mid_leaf31_lr05": {
        "candidate_id": "0bb48b85dc56b19683ad77c061e4b44df8ec4ea603a63da516b745eac65c657b",
        "model_sha256": "b3c4345e07f1bd5f75bb7aeb505f40d8a20e28648459678ddb0efce0efe3fdf6",
        "label_horizon": 20,
        "model_params": {
            "learning_rate": 0.05,
            "colsample_bytree": 0.7,
            "lambda_l1": 50.0,
            "lambda_l2": 200.0,
            "max_depth": 6,
            "num_leaves": 31,
            "min_data_in_leaf": 50,
        },
    },
    "m09_raw10d_baseline": {
        "candidate_id": "4f11110de555e962750048aaa68f3c5d636ffe59e835ee37b51ce991dc79e5ce",
        "model_sha256": "793021af66034ec508848ee032f6d309103291981abb63d11ae779da9c5ee7d3",
        "label_horizon": 10,
        "model_params": {
            "learning_rate": 0.1,
            "colsample_bytree": 0.9,
            "lambda_l1": 205.6999,
            "lambda_l2": 580.9768,
            "max_depth": 8,
            "num_leaves": 250,
            "min_data_in_leaf": 20,
        },
    },
    "m10_raw10d_c98856b4": {
        "candidate_id": "99627a9d20842f5ac43874593b71b175cc533fe2f09666a9417a589f35c7eb1f",
        "model_sha256": "d49e893ad37c821a6a092fb17baf7ee29c9ff71289bbeb1205132e34e3854c50",
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
    },
}

RUN_ID_PATTERN = re.compile(r"stage1_m1_[A-Za-z0-9_]{4,50}")
HEX40_PATTERN = re.compile(r"[a-f0-9]{40}")
HEX64_PATTERN = re.compile(r"[a-f0-9]{64}")


def prefix_for(run_id: str, code_sha: str) -> str:
    """Isolated research prefix bound to the full immutable code SHA."""
    require(bool(RUN_ID_PATTERN.fullmatch(run_id or "")), "STAGE1_M1 invalid run ID")
    require(bool(HEX40_PATTERN.fullmatch(code_sha or "")), "STAGE1_M1 needs full immutable code SHA")
    return f"csi300_csi500_research/stage1_m1/{run_id}/{code_sha[:12]}"


def label_expr(label_horizon: int) -> str:
    """Dynamic label expression for horizon 10 vs 20 (never hardcoded)."""
    horizon = int(label_horizon)
    require(horizon in (10, 20), "STAGE1_M1 invalid label_horizon")
    return f"Ref($close, -{horizon})/$close - 1"


def job_id(job: dict[str, Any]) -> str:
    """Immutable job identity; horizon is part of retrain keys."""
    require(job.get("kind") in ("retrain", "cell"), "STAGE1_M1 unknown job type")
    require(bool(HEX64_PATTERN.fullmatch(job.get("candidate_id", "") or "")), "STAGE1_M1 invalid candidate ID")
    require(job.get("market") in MARKETS, "STAGE1_M1 unknown market")
    require(job.get("model_key") in STAGE1_NEW_MODELS, "STAGE1_M1 unknown model")
    require(int(job.get("label_horizon", -1)) in (10, 20), "STAGE1_M1 invalid label_horizon")
    require(job.get("phase") in (0, 10), "STAGE1_M1 invalid phase")
    model_short = job["candidate_id"][:8]
    if job["kind"] == "retrain":
        require(
            bool(re.fullmatch(r"\d{4}-\d\d-\d\d", job.get("retrain_asof", "") or "")), "STAGE1_M1 invalid as-of"
        )
        return (
            f"retrain/{job['market']}/{job['model_key']}/{model_short}"
            f"/h{int(job['label_horizon'])}/phase{job['phase']:02d}/{job['retrain_asof']}"
        )
    require(job.get("combo_key") in ("top20_drop2", "top10_drop1"), "STAGE1_M1 unknown combo")
    return f"cell/{job['market']}/{job['model_key']}/{model_short}/{job['combo_key']}/phase{job['phase']:02d}"


def receipt_path(root: Path, job: dict[str, Any]) -> Path:
    """Receipt directory: fits/ for retrain jobs, cells/ for replay cells."""
    sub = "fits" if job["kind"] == "retrain" else "cells"
    return root / sub / job_id(job)


def shared_signal_path(root: Path, market: str, model_key: str, phase: int) -> Path:
    """Stage-2 reusable 424-day signal (zero refit traversal over TopK/DropN)."""
    require(market in MARKETS, "STAGE1_M1 unknown market")
    require(model_key in STAGE1_NEW_MODELS, "STAGE1_M1 unknown model")
    require(phase in (0, 10), "STAGE1_M1 invalid phase")
    return root / "signals" / market / model_key / f"phase{phase:02d}" / "signal.parquet"


def _base_job(run_id: str, code_sha: str) -> dict[str, Any]:
    prefix_for(run_id, code_sha)
    return {
        "run_id": run_id,
        "code_sha": code_sha,
        "provider_fingerprint": "",
        "source_snapshot_sha256": "",
        "quote_sha256": "",
    }


def build_stage1_fit_jobs(calendar: list[str], run_id: str, code_sha: str) -> list[dict[str, Any]]:
    """Build all 2 markets x 10 models x 2 phases x 22 as-of = 880 retrain jobs."""
    jobs: list[dict[str, Any]] = []
    for market in ("csi300", "csi500"):
        for model_key, spec in STAGE1_NEW_MODELS.items():
            for phase in PHASES:
                for slice_job in build_phase_jobs(calendar, phase):
                    jobs.append(
                        {
                            **_base_job(run_id, code_sha),
                            "kind": "retrain",
                            "market": market,
                            "benchmark": MARKETS[market]["benchmark"],
                            "model_key": model_key,
                            "candidate_id": spec["candidate_id"],
                            "model_params": dict(spec["model_params"]),
                            "model_sha256": spec["model_sha256"],
                            "label_horizon": spec["label_horizon"],
                            "phase": phase,
                            "retrain_asof": slice_job["retrain_asof"],
                            "signal_end": slice_job["signal_end"],
                        }
                    )
    require(len(jobs) == 880, f"STAGE1_M1 fit roster has {len(jobs)} jobs, need 880")
    keys = [stable_sha(job) for job in jobs]
    require(len(set(keys)) == len(keys), "STAGE1_M1 duplicate fit jobs")
    return jobs


def build_stage1_cell_jobs(run_id: str, code_sha: str) -> list[dict[str, Any]]:
    """Build all 2 markets x 10 models x 2 combos x 2 phases = 80 replay cells."""
    jobs: list[dict[str, Any]] = []
    for market in ("csi300", "csi500"):
        for model_key, spec in STAGE1_NEW_MODELS.items():
            for combo_key, topk, n_drop in COMBOS:
                for phase in PHASES:
                    jobs.append(
                        {
                            **_base_job(run_id, code_sha),
                            "kind": "cell",
                            "market": market,
                            "benchmark": MARKETS[market]["benchmark"],
                            "model_key": model_key,
                            "candidate_id": spec["candidate_id"],
                            "label_horizon": spec["label_horizon"],
                            "combo_key": combo_key,
                            "topk": topk,
                            "n_drop": n_drop,
                            "phase": phase,
                        }
                    )
    require(len(jobs) == 80, f"STAGE1_M1 cell roster has {len(jobs)} jobs, need 80")
    keys = [stable_sha(job) for job in jobs]
    require(len(set(keys)) == len(keys), "STAGE1_M1 duplicate cell jobs")
    return jobs


def bind_job_identity(
    job: dict[str, Any],
    *,
    provider_fingerprint: str,
    source_snapshot_sha256: str,
    quote_sha256: str,
) -> dict[str, Any]:
    """Bind measured source identities to a planned job (deterministic reuse)."""
    require(bool(HEX64_PATTERN.fullmatch(provider_fingerprint or "")), "STAGE1_M1 bad provider fingerprint")
    require(bool(HEX64_PATTERN.fullmatch(source_snapshot_sha256 or "")), "STAGE1_M1 bad snapshot SHA")
    require(bool(HEX64_PATTERN.fullmatch(quote_sha256 or "")), "STAGE1_M1 bad quote SHA")
    return {
        **job,
        "provider_fingerprint": provider_fingerprint,
        "source_snapshot_sha256": source_snapshot_sha256,
        "quote_sha256": quote_sha256,
    }


def retrain_splits(
    calendar: list[str],
    retrain_asof: str,
    label_horizon: int,
    *,
    train_start: str = TRAIN_START,
    validation_sessions: int = VALIDATION_SESSIONS,
) -> dict[str, Any]:
    """Maturity-safe train/valid/test splits for one as-of and label horizon.

    Uses ``horizon = label_horizon`` everywhere (never a hardcoded 20): the
    validation block trails the as-of by ``horizon + 1`` sessions and the
    training tail is clipped to the last label-matured sample.
    """
    from bisect import bisect_left

    from qlib_audit_fixes import last_matured_sample

    horizon = int(label_horizon)
    require(horizon in (10, 20), "STAGE1_M1 invalid label_horizon")
    asof = bisect_left(calendar, retrain_asof)
    require(asof < len(calendar) and calendar[asof] == retrain_asof, "STAGE1_M1 as-of missing")
    valid_end = asof - horizon - 1
    valid_start = valid_end - int(validation_sessions) + 1
    require(valid_start > 0, "STOP_STAGE1_LABEL_MATURITY")
    train_end = last_matured_sample(calendar, calendar[valid_start], horizon)
    return {
        "train": [train_start, train_end],
        "valid": [calendar[valid_start], calendar[valid_end]],
        "horizon": horizon,
        "valid_start": calendar[valid_start],
        "valid_end": calendar[valid_end],
        "train_end": train_end,
    }


def checked_receipt(root: Path, job: dict[str, Any], identity: dict[str, str]) -> dict | None:
    """Stage 1 receipt verification (same immutable semantics as Stage 0)."""
    from stage0_p0_core import ARTIFACTS as P0_ARTIFACTS

    directory = receipt_path(root, job)
    if not directory.exists():
        return None
    import json as _json

    path = directory / "receipt.json"
    require(path.is_file(), "STAGE1_M1 incomplete published receipt")
    record = _json.loads(path.read_text(encoding="utf-8"))
    for key, expected in identity.items():
        require(record.get(key) == expected, f"STAGE1_M1 receipt identity drift: {key}")
    require(record.get("job_sha256") == stable_sha(job), "STAGE1_M1 receipt job spec mismatch")
    require(
        record.get("status") == "PASS" and record.get("kind") == job["kind"],
        "STAGE1_M1 receipt is not a PASS of the requested kind",
    )
    names = P0_ARTIFACTS[job["kind"]]
    hashes = record.get("artifact_sha256")
    require(isinstance(hashes, dict) and set(hashes) == set(names), "STAGE1_M1 receipt artifact manifest incomplete")
    for name in names:
        target = directory / name
        require(target.is_file() and sha256_file(target) == hashes[name], f"STAGE1_M1 artifact SHA drift: {name}")
    return record


def publish_receipt(
    root: Path, job: dict[str, Any], identity: dict[str, str], artifacts: dict[str, Any], details: dict
) -> dict:
    """Stage 1 receipt publication (atomic temp-dir + rename, never overwrite)."""
    import json as _json
    import os as _os
    import shutil as _shutil

    from stage0_p0_core import ARTIFACTS as P0_ARTIFACTS

    dest = receipt_path(root, job)
    require(set(artifacts) == set(P0_ARTIFACTS[job["kind"]]), "STAGE1_M1 wrong artifact list")
    protected = set(identity) | {"status", "kind", "job_sha256", "artifact_sha256"}
    require(not protected.intersection(details), "STAGE1_M1 receipt cannot override signed identity fields")
    if dest.exists():
        existing = checked_receipt(root, job, identity)
        require(existing is not None, "STAGE1_M1 existing incomplete receipt")
        return existing
    tmp = dest.parent / (dest.name + ".tmp")
    require(not tmp.exists(), "STAGE1_M1 stale temp artifact; manual evidence check required")
    tmp.mkdir(parents=True, exist_ok=False)
    try:
        for name, obj in artifacts.items():
            path = tmp / name
            if name.endswith(".parquet"):
                obj.to_parquet(path)
            elif isinstance(obj, bytes):
                path.write_bytes(obj)
            elif isinstance(obj, str):
                path.write_text(obj, encoding="utf-8")
            else:
                path.write_text(_json.dumps(obj, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
        hashes = {name: sha256_file(tmp / name) for name in sorted(artifacts)}
        record = {
            "status": "PASS",
            **identity,
            "kind": job["kind"],
            "job_sha256": stable_sha(job),
            "artifact_sha256": hashes,
            **details,
        }
        (tmp / "receipt.json").write_text(
            _json.dumps(record, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        _os.replace(tmp, dest)
        return record
    except BaseException:
        if tmp.exists():
            _shutil.rmtree(tmp)
        raise


def load_stage0_anchor_cells(volume_root: str | Path, stage0_prefix: str = STAGE0_PREFIX) -> list[dict[str, Any]]:
    """Load the 16 verified Stage 0 anchor cells from the shared v2 Volume.

    Returns normalized cell dicts (same shape as Stage 1 export cells) with
    ``anchor=True``. Fails closed on any missing receipt, byte drift or
    identity mismatch.
    """
    import json as _json

    root = Path(volume_root) / stage0_prefix
    summary_path = root / "stage0_p0_summary.json"
    require(summary_path.is_file(), "STAGE1_M1 Stage 0 summary missing")
    summary = _json.loads(summary_path.read_text(encoding="utf-8"))
    require(summary.get("status") == "PASS" and int(summary.get("completed_cells", -1)) == 16, "STAGE1_M1 Stage 0 summary not PASS/16")
    require(summary.get("run_id") == STAGE0_RUN_ID and summary.get("code_sha") == STAGE0_CODE_SHA, "STAGE1_M1 Stage 0 lineage drift")
    cells = summary.get("cells")
    require(isinstance(cells, list) and len(cells) == 16, "STAGE1_M1 Stage 0 must carry exactly 16 cells")
    out = []
    for cell in cells:
        require(cell.get("model_key") in STAGE0_ANCHOR_MODELS, "STAGE1_M1 foreign Stage 0 model")
        rec_path = (
            root
            / "cells"
            / f"cell/{cell['market']}/{cell['model_key']}/{cell['candidate_id'][:8]}"
            / f"{cell['combo_key']}/phase{int(cell['phase']):02d}"
            / "receipt.json"
        )
        require(rec_path.is_file(), f"STAGE1_M1 Stage 0 receipt missing: {rec_path}")
        rec = _json.loads(rec_path.read_text(encoding="utf-8"))
        require(rec.get("status") == "PASS" and rec.get("job_sha256") == cell.get("job_sha256"), "STAGE1_M1 Stage 0 receipt drift")
        for name, digest in (rec.get("artifact_sha256") or {}).items():
            target = rec_path.parent / name
            require(target.is_file() and sha256_file(target) == digest, f"STAGE1_M1 Stage 0 bytes drift: {name}")
        out.append({**cell, "anchor": True})
    return sorted(out, key=lambda row: (row["market"], row["model_key"], row["combo_key"], row["phase"]))


def rank_market_cells(cells_48: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank one market's 12 models x 2 combos from paired phase (0, 10) cells.

    Expects exactly 48 cells (24 (model, combo) pairs x 2 phases). Sort key is
    ``(worst_relative_excess_cagr, mean_relative_excess_cagr, mean_ir,
    mean_sharpe, worst_max_drawdown)`` descending; drawdowns are negative so
    descending prefers the shallowest loss.
    """
    require(isinstance(cells_48, list) and len(cells_48) == 48, "STAGE1_M1 ranking needs exactly 48 cells")
    by_pair: dict[tuple[str, str], dict[int, dict]] = {}
    for cell in cells_48:
        key = (cell["model_key"], cell["combo_key"])
        require(cell["phase"] in (0, 10), "STAGE1_M1 ranking phase invalid")
        require(cell["phase"] not in by_pair.setdefault(key, {}), "STAGE1_M1 duplicate ranking cell")
        by_pair[key][cell["phase"]] = cell
    require(len(by_pair) == 24 and all(set(v) == {0, 10} for v in by_pair.values()), "STAGE1_M1 ranking pairs incomplete")
    rows = []
    for (model_key, combo_key), phases in by_pair.items():
        m0, m10 = phases[0]["metrics"], phases[10]["metrics"]
        s0, s10 = phases[0]["stressed"], phases[10]["stressed"]
        rows.append(
            {
                "model_key": model_key,
                "combo_key": combo_key,
                "label_horizon": int(phases[0].get("label_horizon", 20)),
                "phase0_relative_excess_cagr": float(m0["relative_excess_cagr"]),
                "phase10_relative_excess_cagr": float(m10["relative_excess_cagr"]),
                "worst_relative_excess_cagr": min(float(m0["relative_excess_cagr"]), float(m10["relative_excess_cagr"])),
                "mean_relative_excess_cagr": 0.5 * (float(m0["relative_excess_cagr"]) + float(m10["relative_excess_cagr"])),
                "worst_strategy_cagr": min(float(m0["strategy_cagr"]), float(m10["strategy_cagr"])),
                "mean_strategy_cagr": 0.5 * (float(m0["strategy_cagr"]) + float(m10["strategy_cagr"])),
                "mean_sharpe": 0.5 * (float(m0["sharpe"]) + float(m10["sharpe"])),
                "mean_ir": 0.5 * (float(m0["information_ratio"]) + float(m10["information_ratio"])),
                "worst_max_drawdown": min(float(m0["strategy_max_drawdown"]), float(m10["strategy_max_drawdown"])),
                "worst_stressed10bp_total_return": min(
                    float(s0["10bp"]["strategy_total_return"]), float(s10["10bp"]["strategy_total_return"])
                ),
            }
        )
    rows.sort(
        key=lambda r: (
            r["worst_relative_excess_cagr"],
            r["mean_relative_excess_cagr"],
            r["mean_ir"],
            r["mean_sharpe"],
            r["worst_max_drawdown"],
        ),
        reverse=True,
    )
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    return rows


STAGE1_SCOREBOARD_COLUMNS = (
    "market",
    "benchmark",
    "model_key",
    "candidate_id",
    "label_horizon",
    "anchor",
    "combo_key",
    "topk",
    "n_drop",
    "phase",
    "n_days",
    "strategy_total_return",
    "strategy_cagr",
    "benchmark_cagr",
    "relative_excess_cagr",
    "sharpe",
    "information_ratio",
    "strategy_max_drawdown",
    "mean_turnover",
    "total_cost_sum",
    "stressed10bp_strategy_total_return",
    "stressed20bp_strategy_total_return",
    "account_return_max_error",
)


def render_stage1_scoreboard(summary: dict[str, Any], csv_path: str | Path) -> Path:
    """Render the 96-cell scoreboard CSV (80 new + 16 anchors)."""
    cells = summary.get("cells")
    require(isinstance(cells, list) and len(cells) == 96, "STAGE1_M1 summary must carry exactly 96 cells")
    out = Path(csv_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(STAGE1_SCOREBOARD_COLUMNS))
        writer.writeheader()
        for cell in sorted(cells, key=lambda row: (row["market"], row["model_key"], row["combo_key"], row["phase"])):
            metrics = cell.get("metrics") or {}
            writer.writerow(
                {
                    "market": cell["market"],
                    "benchmark": cell["benchmark"],
                    "model_key": cell["model_key"],
                    "candidate_id": cell["candidate_id"],
                    "label_horizon": cell.get("label_horizon", ""),
                    "anchor": bool(cell.get("anchor", False)),
                    "combo_key": cell["combo_key"],
                    "topk": cell["topk"],
                    "n_drop": cell["n_drop"],
                    "phase": cell["phase"],
                    "n_days": metrics.get("n_days"),
                    "strategy_total_return": metrics.get("strategy_total_return"),
                    "strategy_cagr": metrics.get("strategy_cagr"),
                    "benchmark_cagr": metrics.get("benchmark_cagr"),
                    "relative_excess_cagr": metrics.get("relative_excess_cagr"),
                    "sharpe": metrics.get("sharpe"),
                    "information_ratio": metrics.get("information_ratio"),
                    "strategy_max_drawdown": metrics.get("strategy_max_drawdown"),
                    "mean_turnover": metrics.get("mean_turnover"),
                    "total_cost_sum": metrics.get("total_cost_sum"),
                    "stressed10bp_strategy_total_return": (cell.get("stressed") or {}).get("10bp", {}).get(
                        "strategy_total_return"
                    ),
                    "stressed20bp_strategy_total_return": (cell.get("stressed") or {}).get("20bp", {}).get(
                        "strategy_total_return"
                    ),
                    "account_return_max_error": metrics.get("account_return_max_error"),
                }
            )
    return out
