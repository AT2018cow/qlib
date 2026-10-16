"""Stage 3 (L4) offline control plane for CSI300/CSI500 five-phase research.

Completes the rolling LightGBM training for the three remaining reference
phases ``4 / 6 / 15`` for 10 (market, model_key) pairs, replays the 54 new
phase F1 units of the 9 frozen finalist strategies per market, merges the 36
verified Stage 0/1/2 phase-(0, 10) units and ranks every strategy over the
full five-phase set ``[0, 4, 6, 10, 15]``.

Roster geometry:
- 10 (market, model_key) pairs x 3 new phases x 22 as-of slices = 660 new
  ``l4retrain`` fits (no phase-0/10 refit anywhere).
- 2 markets x 9 strategies x 3 new phases = 54 new ``l4cell`` replays
  (``STAGE3_ARTIFACTS["l4cell"]`` never publishes a signal artifact; the
  424-day signal is assembled from the 22 verified fits and only the first
  ``combo_key`` per (market, model_key) writes the canonical shared file).
- 2 markets x 9 strategies x 5 phases = 90 five-phase units = 36 verified
  prior units + 54 new units.

Pure-Python planners/renderers only: no Modal import, no remote calls, no
model fitting. Replay/stress arithmetic is reused from :mod:`stage0_p0_core`;
split planning is reused from :mod:`stage1_m1_core`.
"""

from __future__ import annotations

import csv
import json
import re
import statistics
from pathlib import Path
from typing import Any

import stage0_p0_core as p0
import stage2_l1_core as l1
from audit.csi1000_layered_search_core import require, sha256_file, stable_sha
from csi1000_stage_b_core import (
    RESERVED_EXECUTION_END,
    RESERVED_EXECUTION_START,
    RESERVED_SIGNAL_ANCHOR,
    build_phase_jobs,
)
from stage1_m1_core import (
    STAGE0_CODE_SHA,
    STAGE0_PREFIX,
    STAGE0_RUN_ID,
    STAGE1_NEW_MODELS,
    label_expr,
    retrain_splits,
)

RESEARCH_VOLUME = p0.RESEARCH_VOLUME
SOURCE_VOLUME = p0.SOURCE_VOLUME
MARKETS = p0.MARKETS
N_EXECUTION_DAYS = p0.N_EXECUTION_DAYS
EXPECTED_PROVIDER_FINGERPRINT = p0.EXPECTED_PROVIDER_FINGERPRINT
EXPECTED_SNAPSHOT_TOKEN = p0.EXPECTED_SNAPSHOT_TOKEN
EXPECTED_CALENDAR_DAYS = p0.EXPECTED_CALENDAR_DAYS
EXPECTED_CALENDAR_END = p0.EXPECTED_CALENDAR_END
quote_sha_of = p0.quote_sha_of

# Verified Stage 1/2 exports (same v2 research Volume).
STAGE1_RUN_ID = l1.STAGE1_RUN_ID
STAGE1_CODE_SHA = l1.STAGE1_CODE_SHA
STAGE1_PREFIX = l1.STAGE1_PREFIX
STAGE2_RUN_ID = "stage2_l1_20261016_a1"
STAGE2_CODE_SHA = "81d40e265d0fcdf980dcf3fd1b2673c6145af23f"
STAGE2_PREFIX = f"csi300_csi500_research/stage2_l1/{STAGE2_RUN_ID}/{STAGE2_CODE_SHA[:12]}"

ALL_5_PHASES = (0, 4, 6, 10, 15)
PRIOR_PHASES = (0, 10)
NEW_PHASES = (4, 6, 15)

EXPECTED_FITS = 660
EXPECTED_L4_CELLS = 54
EXPECTED_PRIOR_UNITS = 36
EXPECTED_FIVE_PHASE_UNITS = 90
EXPECTED_UNITS_PER_MARKET = 45

ROLES = ("challenger", "control_top10", "official_baseline")
OFFICIAL_BASELINE = ("baseline", "top20_drop2")

# Plan-verbatim 5 base models per market (10 (market, model_key) pairs).
STAGE3_MODELS_BY_MARKET = {
    "csi300": (
        "m03_4e908173",
        "m04_17dbfe28",
        "m06_b034ac46",
        "m01_59b93929",
        "baseline",
    ),
    "csi500": (
        "m10_raw10d_c98856b4",
        "m09_raw10d_baseline",
        "c98856b4",
        "m03_4e908173",
        "baseline",
    ),
}

# Plan-verbatim 9 five-phase finalist strategies per market.
STAGE3_STRATEGIES_BY_MARKET = {
    "csi300": (
        ("m03_4e908173", "top8_drop1", "challenger"),
        ("m04_17dbfe28", "top5_drop1", "challenger"),
        ("m06_b034ac46", "top8_drop1", "challenger"),
        ("m01_59b93929", "top8_drop1", "challenger"),
        ("m01_59b93929", "top10_drop1", "challenger"),
        ("m06_b034ac46", "top10_drop1", "challenger"),
        ("m03_4e908173", "top10_drop1", "challenger"),
        ("baseline", "top10_drop1", "control_top10"),
        ("baseline", "top20_drop2", "official_baseline"),
    ),
    "csi500": (
        ("m10_raw10d_c98856b4", "top10_drop1", "challenger"),
        ("m10_raw10d_c98856b4", "top15_drop2", "challenger"),
        ("m10_raw10d_c98856b4", "top15_drop1", "challenger"),
        ("m10_raw10d_c98856b4", "top20_drop2", "challenger"),
        ("m09_raw10d_baseline", "top30_drop2", "challenger"),
        ("m09_raw10d_baseline", "top20_drop1", "challenger"),
        ("c98856b4", "top30_drop3", "challenger"),
        ("m03_4e908173", "top15_drop1", "challenger"),
        ("baseline", "top20_drop2", "official_baseline"),
    ),
}

STAGE3_ARTIFACTS = {
    "l4retrain": ("prediction.parquet", "checkpoint.txt"),
    "l4cell": ("fixed_report.parquet", "decisions.json"),
}

# 12 frozen models (2 Stage 0 anchors + 10 Stage 1), same merge as Stage 2.
ALL_L4_MODELS: dict[str, dict[str, Any]] = {}
for _key, _spec in p0.MODELS.items():
    ALL_L4_MODELS[_key] = {**_spec, "label_horizon": 20, "source_stage": "stage0_p0"}
for _key, _spec in STAGE1_NEW_MODELS.items():
    ALL_L4_MODELS[_key] = {**_spec, "source_stage": "stage1_m1"}
require(
    len(ALL_L4_MODELS) == 12 and set(ALL_L4_MODELS) == set(p0.MODELS) | set(STAGE1_NEW_MODELS),
    "STAGE3_L4 model merge must cover 12 frozen models",
)

_STAGE2_NEW_COMBO_KEYS = tuple(key for key, _, _ in l1.STAGE2_NEW_COMBOS)
_COMBO_KEYS = tuple(key for key, _, _ in l1.ALL_10_COMBOS)

require(set(STAGE3_MODELS_BY_MARKET) == set(MARKETS), "STAGE3_L4 model roster must cover both markets")
for _market in MARKETS:
    _models = STAGE3_MODELS_BY_MARKET[_market]
    _strategies = STAGE3_STRATEGIES_BY_MARKET[_market]
    require(len(_models) == 5 and len(set(_models)) == 5, f"STAGE3_L4 {_market} needs 5 unique models")
    require(all(_model in ALL_L4_MODELS for _model in _models), f"STAGE3_L4 {_market} unknown model")
    require(len(_strategies) == 9, f"STAGE3_L4 {_market} needs 9 strategies")
    require(len({(_m, _k) for _m, _k, _ in _strategies}) == 9, f"STAGE3_L4 {_market} duplicate strategy")
    require(
        all(_m in _models and _k in _COMBO_KEYS and _r in ROLES for _m, _k, _r in _strategies),
        f"STAGE3_L4 {_market} strategy roster drift",
    )
    require(
        OFFICIAL_BASELINE in {(_m, _k) for _m, _k, _ in _strategies}, f"STAGE3_L4 {_market} needs official baseline"
    )

RUN_ID_PATTERN = re.compile(r"stage3_l4_[A-Za-z0-9_]{4,50}")
HEX40_PATTERN = re.compile(r"[a-f0-9]{40}")
HEX64_PATTERN = re.compile(r"[a-f0-9]{64}")


def _combo_specs() -> dict[str, tuple[int, int]]:
    return {key: (topk, n_drop) for key, topk, n_drop in l1.ALL_10_COMBOS}


def prefix_for(run_id: str, code_sha: str) -> str:
    """Isolated research prefix bound to the full immutable code SHA."""
    require(bool(RUN_ID_PATTERN.fullmatch(run_id or "")), "STAGE3_L4 invalid run ID")
    require(bool(HEX40_PATTERN.fullmatch(code_sha or "")), "STAGE3_L4 needs full immutable code SHA")
    return f"csi300_csi500_research/stage3_l4/{run_id}/{code_sha[:12]}"


def strategy_role(market: str, model_key: str, combo_key: str) -> str:
    """Plan-declared role of one frozen finalist strategy."""
    require(market in MARKETS, "STAGE3_L4 unknown market")
    for model, combo, role in STAGE3_STRATEGIES_BY_MARKET[market]:
        if (model, combo) == (model_key, combo_key):
            return role
    require(False, f"STAGE3_L4 strategy not in roster: {market}/{model_key}/{combo_key}")


def canonical_signal_combo(market: str, model_key: str) -> str:
    """First ``combo_key`` of a (market, model_key): the shared-signal writer."""
    require(market in MARKETS, "STAGE3_L4 unknown market")
    require(model_key in STAGE3_MODELS_BY_MARKET[market], "STAGE3_L4 model not in market roster")
    return next(combo for model, combo, _role in STAGE3_STRATEGIES_BY_MARKET[market] if model == model_key)


def is_canonical_signal_writer(market: str, model_key: str, combo_key: str) -> bool:
    """Exactly one combo per (market, model_key) may write the shared signal."""
    require(combo_key in _combo_specs(), "STAGE3_L4 unknown combo")
    return combo_key == canonical_signal_combo(market, model_key)


def signal_relpath(market: str, model_key: str, phase: int) -> str:
    """Canonical Volume-relative path of a new-phase 424-day shared signal."""
    require(market in MARKETS, "STAGE3_L4 unknown market")
    require(model_key in STAGE3_MODELS_BY_MARKET[market], "STAGE3_L4 model not in market roster")
    require(int(phase) in NEW_PHASES, "STAGE3_L4 shared signals exist for new phases only")
    return f"signals/{market}/{model_key}/phase{int(phase):02d}/signal.parquet"


def shared_signal_path(root: str | Path, market: str, model_key: str, phase: int) -> Path:
    """Absolute path of a new-phase shared 424-day signal inside the Volume."""
    return Path(root) / signal_relpath(market, model_key, phase)


def job_id(job: dict[str, Any]) -> str:
    """Immutable job identity; model/combo/phase/as-of are all in the key."""
    require(job.get("kind") in ("l4retrain", "l4cell"), "STAGE3_L4 unknown job type")
    require(bool(HEX64_PATTERN.fullmatch(job.get("candidate_id", "") or "")), "STAGE3_L4 invalid candidate ID")
    require(job.get("market") in MARKETS, "STAGE3_L4 unknown market")
    require(job.get("model_key") in STAGE3_MODELS_BY_MARKET[job["market"]], "STAGE3_L4 model not in market roster")
    require(job.get("phase") in NEW_PHASES, "STAGE3_L4 invalid phase")
    require(int(job.get("label_horizon", -1)) in (10, 20), "STAGE3_L4 invalid label_horizon")
    model_short = job["candidate_id"][:8]
    horizon = int(job["label_horizon"])
    if job["kind"] == "l4retrain":
        require(bool(re.fullmatch(r"\d{4}-\d\d-\d\d", job.get("retrain_asof", "") or "")), "STAGE3_L4 invalid as-of")
        return (
            f"l4retrain/{job['market']}/{job['model_key']}/{model_short}"
            f"/h{horizon}/phase{job['phase']:02d}/{job['retrain_asof']}"
        )
    combos = _combo_specs()
    require(job.get("combo_key") in combos, "STAGE3_L4 unknown combo")
    require(
        (int(job.get("topk", -1)), int(job.get("n_drop", -1))) == combos[job["combo_key"]],
        "STAGE3_L4 combo geometry mismatch",
    )
    return f"l4cell/{job['market']}/{job['model_key']}/{model_short}/{job['combo_key']}/phase{job['phase']:02d}"


def receipt_path(root: Path, job: dict[str, Any]) -> Path:
    """Receipt directory: fits/ for l4retrain jobs, cells/ for l4cell replays."""
    sub = "fits" if job["kind"] == "l4retrain" else "cells"
    return Path(root) / sub / job_id(job)


def _identity(job: dict[str, Any]) -> dict[str, str]:
    return {
        key: job[key]
        for key in (
            "run_id",
            "code_sha",
            "provider_fingerprint",
            "source_snapshot_sha256",
            "quote_sha256",
        )
    }


def checked_receipt(root: Path, job: dict[str, Any], identity: dict[str, str]) -> dict | None:
    """Verify one Stage 3 receipt; incomplete/mismatched files block.

    ``l4retrain`` receipts must record ``fit_performed=True`` so a skipped or
    failed fit can never be counted as evidence. ``l4cell`` receipts must pin
    the canonical shared-signal relpath, the 22 verified fit prediction SHAs
    and the ``signal_written`` flag so the writer guard is auditable offline.
    """
    directory = receipt_path(root, job)
    if not directory.exists():
        return None
    path = directory / "receipt.json"
    require(path.is_file(), "STAGE3_L4 incomplete published receipt")
    record = json.loads(path.read_text(encoding="utf-8"))
    for key, expected in identity.items():
        require(record.get(key) == expected, f"STAGE3_L4 receipt identity drift: {key}")
    require(record.get("job_sha256") == stable_sha(job), "STAGE3_L4 receipt job spec mismatch")
    require(
        record.get("status") == "PASS" and record.get("kind") == job["kind"],
        "STAGE3_L4 receipt is not a PASS of the requested kind",
    )
    if job["kind"] == "l4retrain":
        require(record.get("fit_performed") is True, "STAGE3_L4 receipt must record fit_performed=True")
    else:
        expected_relpath = signal_relpath(job["market"], job["model_key"], int(job["phase"]))
        require(record.get("signal_relpath") == expected_relpath, "STAGE3_L4 receipt signal relpath drift")
        shas = record.get("source_prediction_shas")
        require(
            isinstance(shas, list) and len(shas) == 22 and all(bool(HEX64_PATTERN.fullmatch(s or "")) for s in shas),
            "STAGE3_L4 receipt must pin 22 source prediction SHAs",
        )
        require(isinstance(record.get("signal_written"), bool), "STAGE3_L4 receipt signal_written must be boolean")
    names = STAGE3_ARTIFACTS[job["kind"]]
    hashes = record.get("artifact_sha256")
    require(isinstance(hashes, dict) and set(hashes) == set(names), "STAGE3_L4 receipt artifact manifest incomplete")
    for name in names:
        target = directory / name
        require(target.is_file() and sha256_file(target) == hashes[name], f"STAGE3_L4 artifact SHA drift: {name}")
    return record


def publish_receipt(
    root: Path, job: dict[str, Any], identity: dict[str, str], artifacts: dict[str, Any], details: dict
) -> dict:
    """Atomic temp-dir + rename publication; existing files are never overwritten."""
    import os as _os
    import shutil as _shutil

    dest = receipt_path(root, job)
    require(set(artifacts) == set(STAGE3_ARTIFACTS[job["kind"]]), "STAGE3_L4 wrong artifact list")
    if job["kind"] == "l4retrain":
        require(details.get("fit_performed") is True, "STAGE3_L4 l4retrain details must record fit_performed=True")
    else:
        expected_relpath = signal_relpath(job["market"], job["model_key"], int(job["phase"]))
        require(details.get("signal_relpath") == expected_relpath, "STAGE3_L4 l4cell details signal relpath drift")
        shas = details.get("source_prediction_shas")
        require(
            isinstance(shas, list) and len(shas) == 22 and all(bool(HEX64_PATTERN.fullmatch(s or "")) for s in shas),
            "STAGE3_L4 l4cell details must pin 22 source prediction SHAs",
        )
        require(
            isinstance(details.get("signal_written"), bool), "STAGE3_L4 l4cell details signal_written must be boolean"
        )
    protected = set(identity) | {"status", "kind", "job_sha256", "artifact_sha256"}
    require(not protected.intersection(details), "STAGE3_L4 receipt cannot override signed identity fields")
    if dest.exists():
        existing = checked_receipt(root, job, identity)
        require(existing is not None, "STAGE3_L4 existing incomplete receipt")
        return existing
    tmp = dest.parent / (dest.name + ".tmp")
    require(not tmp.exists(), "STAGE3_L4 stale temp artifact; manual evidence check required")
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
                path.write_text(json.dumps(obj, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
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
            json.dumps(record, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        _os.replace(tmp, dest)
        return record
    except BaseException:
        if tmp.exists():
            _shutil.rmtree(tmp)
        raise


def _base_job(run_id: str, code_sha: str) -> dict[str, Any]:
    prefix_for(run_id, code_sha)
    return {
        "run_id": run_id,
        "code_sha": code_sha,
        "provider_fingerprint": "",
        "source_snapshot_sha256": "",
        "quote_sha256": "",
    }


def build_stage3_fit_jobs(calendar: list[str], run_id: str, code_sha: str) -> list[dict[str, Any]]:
    """Build all 2 markets x 5 models x 3 new phases x 22 slices = 660 l4retrains."""
    jobs: list[dict[str, Any]] = []
    for market in MARKETS:
        for model_key in STAGE3_MODELS_BY_MARKET[market]:
            spec = ALL_L4_MODELS[model_key]
            for phase in NEW_PHASES:
                for slice_job in build_phase_jobs(calendar, phase):
                    jobs.append(
                        {
                            **_base_job(run_id, code_sha),
                            "kind": "l4retrain",
                            "market": market,
                            "benchmark": MARKETS[market]["benchmark"],
                            "model_key": model_key,
                            "candidate_id": spec["candidate_id"],
                            "label_horizon": int(spec["label_horizon"]),
                            "source_stage": spec["source_stage"],
                            "phase": phase,
                            "retrain_asof": slice_job["retrain_asof"],
                            "signal_end": slice_job["signal_end"],
                        }
                    )
    require(len(jobs) == EXPECTED_FITS, f"STAGE3_L4 fit roster has {len(jobs)} jobs, need {EXPECTED_FITS}")
    for market in MARKETS:
        for model_key in STAGE3_MODELS_BY_MARKET[market]:
            for phase in NEW_PHASES:
                count = sum(
                    1 for job in jobs if (job["market"], job["model_key"], job["phase"]) == (market, model_key, phase)
                )
                require(count == 22, f"STAGE3_L4 {market}/{model_key}/phase{phase} has {count} slices, need 22")
    keys = [stable_sha(job) for job in jobs]
    require(len(set(keys)) == len(keys), "STAGE3_L4 duplicate l4retrain jobs")
    return jobs


def build_stage3_cell_jobs(run_id: str, code_sha: str) -> list[dict[str, Any]]:
    """Build all 2 markets x 9 strategies x 3 new phases = 54 l4cell replays."""
    combos = _combo_specs()
    jobs: list[dict[str, Any]] = []
    for market in MARKETS:
        for model_key, combo_key, role in STAGE3_STRATEGIES_BY_MARKET[market]:
            spec = ALL_L4_MODELS[model_key]
            topk, n_drop = combos[combo_key]
            for phase in NEW_PHASES:
                jobs.append(
                    {
                        **_base_job(run_id, code_sha),
                        "kind": "l4cell",
                        "market": market,
                        "benchmark": MARKETS[market]["benchmark"],
                        "model_key": model_key,
                        "candidate_id": spec["candidate_id"],
                        "label_horizon": int(spec["label_horizon"]),
                        "source_stage": spec["source_stage"],
                        "combo_key": combo_key,
                        "topk": topk,
                        "n_drop": n_drop,
                        "role": role,
                        "canonical_signal_writer": is_canonical_signal_writer(market, model_key, combo_key),
                        "phase": phase,
                    }
                )
    require(len(jobs) == EXPECTED_L4_CELLS, f"STAGE3_L4 cell roster has {len(jobs)} jobs, need {EXPECTED_L4_CELLS}")
    keys = [stable_sha(job) for job in jobs]
    require(len(set(keys)) == len(keys), "STAGE3_L4 duplicate l4cell jobs")
    return jobs


def bind_stage3_job(
    job: dict[str, Any],
    *,
    provider_fingerprint: str,
    source_snapshot_sha256: str,
    quote_sha256: str,
) -> dict[str, Any]:
    """Bind measured identities to a planned Stage 3 job."""
    require(bool(HEX64_PATTERN.fullmatch(provider_fingerprint or "")), "STAGE3_L4 bad provider fingerprint")
    require(bool(HEX64_PATTERN.fullmatch(source_snapshot_sha256 or "")), "STAGE3_L4 bad snapshot SHA")
    require(bool(HEX64_PATTERN.fullmatch(quote_sha256 or "")), "STAGE3_L4 bad quote SHA")
    require(job.get("kind") in ("l4retrain", "l4cell"), "STAGE3_L4 bind supports Stage 3 jobs only")
    return {
        **job,
        "provider_fingerprint": provider_fingerprint,
        "source_snapshot_sha256": source_snapshot_sha256,
        "quote_sha256": quote_sha256,
    }


def fit_job_for_slice(
    fit_jobs: list[dict[str, Any]],
    market: str,
    model_key: str,
    phase: int,
    retrain_asof: str,
) -> dict[str, Any]:
    """Exactly one planned l4retrain slice per (market, model, phase, as-of)."""
    wanted = (market, model_key, int(phase), retrain_asof)
    matched = [
        job for job in fit_jobs if (job["market"], job["model_key"], job["phase"], job["retrain_asof"]) == wanted
    ]
    require(len(matched) == 1, f"STAGE3_L4 slice fit lookup failed: {wanted}")
    return matched[0]


def assemble_phase_signal(
    volume_root: str | Path,
    calendar: list[str],
    fit_jobs: list[dict[str, Any]],
    job: dict[str, Any],
    identity: dict[str, str],
) -> dict[str, Any]:
    """Assemble the 424-day new-phase signal from the 22 verified slice fits.

    Every slice prediction is read from its sealed ``l4retrain`` receipt,
    concatenated, deduplicated by trading day and clipped to the common
    reserved window ``[RESERVED_SIGNAL_ANCHOR, RESERVED_EXECUTION_END)``.
    Fails closed on any missing receipt, missing day, duplicate day or wrong
    coverage so a partial signal can never reach a replay.
    """
    import pandas as pd

    root = Path(volume_root)
    market, model_key, phase = job["market"], job["model_key"], int(job["phase"])
    slices = build_phase_jobs(calendar, phase)
    frames: list[Any] = []
    source_shas: list[str] = []
    slice_job_shas: list[str] = []
    for slice_job in slices:
        fit_job = fit_job_for_slice(fit_jobs, market, model_key, phase, slice_job["retrain_asof"])
        receipt = checked_receipt(root, fit_job, identity)
        require(receipt is not None, f"STAGE3_L4 missing slice receipt: {fit_job['retrain_asof']}")
        require(receipt.get("fit_performed") is True, "STAGE3_L4 slice fit_performed drift")
        path = receipt_path(root, fit_job) / "prediction.parquet"
        frame = pd.read_parquet(path)
        require(len(frame) > 0, f"STAGE3_L4 empty slice prediction: {fit_job['retrain_asof']}")
        frames.append(frame)
        source_shas.append(receipt["artifact_sha256"]["prediction.parquet"])
        slice_job_shas.append(receipt["job_sha256"])
    merged = pd.concat(frames).sort_index()
    require(not merged.index.duplicated().any(), "STOP_STAGE3_SIGNAL_DUPLICATE_DAY")
    dates = merged.index.get_level_values(0).astype(str).str[:10]
    window = merged.loc[(dates >= str(RESERVED_SIGNAL_ANCHOR)) & (dates < str(RESERVED_EXECUTION_END))]
    require(len(window) == N_EXECUTION_DAYS, f"STOP_STAGE3_SIGNAL_GAP: {len(window)} rows, need {N_EXECUTION_DAYS}")
    return {
        "signal": window,
        "source_prediction_shas": source_shas,
        "slice_job_shas": slice_job_shas,
    }


# ---------------------------------------------------------------------------
# Prior phase-(0, 10) units from the verified Stage 0/1/2 exports.
# ---------------------------------------------------------------------------

PRIOR_UNITS_BY_MARKET_SOURCE = {
    "csi300": {"stage0_p0": 4, "stage1_m1": 6, "stage2_l1": 8},
    "csi500": {"stage0_p0": 2, "stage1_m1": 4, "stage2_l1": 12},
}


def prior_unit_source(market: str, model_key: str, combo_key: str) -> str:
    """Which verified export owns one strategy's phase-(0, 10) unit."""
    require(market in MARKETS, "STAGE3_L4 unknown market")
    require(combo_key in _combo_specs(), "STAGE3_L4 unknown combo")
    if combo_key in _STAGE2_NEW_COMBO_KEYS:
        return "stage2_l1"
    return "stage0_p0" if model_key in p0.MODELS else "stage1_m1"


def _prior_source_spec(source: str) -> dict[str, Any]:
    if source == "stage2_l1":
        return {
            "prefix": STAGE2_PREFIX,
            "kind": "l1cell",
            "run_id": STAGE2_RUN_ID,
            "code_sha": STAGE2_CODE_SHA,
            "artifacts": l1.STAGE2_ARTIFACTS["l1cell"],
        }
    if source == "stage0_p0":
        return {
            "prefix": STAGE0_PREFIX,
            "kind": "cell",
            "run_id": STAGE0_RUN_ID,
            "code_sha": STAGE0_CODE_SHA,
            "artifacts": p0.ARTIFACTS["cell"],
        }
    require(source == "stage1_m1", "STAGE3_L4 unknown prior source")
    return {
        "prefix": STAGE1_PREFIX,
        "kind": "cell",
        "run_id": STAGE1_RUN_ID,
        "code_sha": STAGE1_CODE_SHA,
        "artifacts": p0.ARTIFACTS["cell"],
    }


def load_stage2_prior_units(volume_root: str | Path) -> list[dict[str, Any]]:
    """Load the 36 verified phase-(0, 10) units of the 18 finalist strategies.

    Reads ``<stage2_prefix>/stage2_l1_summary.json`` (the only export carrying
    the 384 new l1cells and the 96 prior cells merged) and re-verifies, for
    every required unit, the stage-specific receipt on disk (status, kind,
    job SHA, lineage, sealed artifact bytes). Fails closed on any drift.
    """
    root = Path(volume_root)
    summary_path = root / STAGE2_PREFIX / "stage2_l1_summary.json"
    require(summary_path.is_file(), "STAGE3_L4 Stage 2 summary missing")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    require(summary.get("status") == "PASS", "STAGE3_L4 Stage 2 summary is not PASS")
    require(
        summary.get("run_id") == STAGE2_RUN_ID and summary.get("code_sha") == STAGE2_CODE_SHA,
        "STAGE3_L4 Stage 2 lineage drift",
    )
    require(
        int(summary.get("completed_l1cells", -1)) == 384
        and int(summary.get("prior_cells", -1)) == 96
        and int(summary.get("total_cells", -1)) == 480,
        "STAGE3_L4 Stage 2 summary coverage drift",
    )
    cells = summary.get("cells")
    require(isinstance(cells, list) and len(cells) == 480, "STAGE3_L4 Stage 2 summary must carry 480 cells")
    lookup: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    for cell in cells:
        key = (cell.get("market"), cell.get("model_key"), cell.get("combo_key"), int(cell.get("phase", -1)))
        require(key not in lookup, f"STAGE3_L4 duplicate Stage 2 summary cell: {key}")
        lookup[key] = cell
    combos = _combo_specs()
    units: list[dict[str, Any]] = []
    for market in MARKETS:
        for model_key, combo_key, role in STAGE3_STRATEGIES_BY_MARKET[market]:
            spec_model = ALL_L4_MODELS[model_key]
            short = spec_model["candidate_id"][:8]
            for phase in PRIOR_PHASES:
                key = (market, model_key, combo_key, phase)
                cell = lookup.get(key)
                require(cell is not None, f"STAGE3_L4 missing Stage 2 summary cell: {key}")
                require(
                    cell.get("candidate_id") == spec_model["candidate_id"]
                    and cell.get("benchmark") == MARKETS[market]["benchmark"],
                    f"STAGE3_L4 prior model identity drift: {key}",
                )
                require(
                    (int(cell.get("topk", -1)), int(cell.get("n_drop", -1))) == combos[combo_key],
                    f"STAGE3_L4 prior combo drift: {key}",
                )
                source = prior_unit_source(market, model_key, combo_key)
                src = _prior_source_spec(source)
                directory = root / src["prefix"] / "cells" / src["kind"]
                directory = directory / market / model_key / short / combo_key / f"phase{phase:02d}"
                rec_file = directory / "receipt.json"
                require(rec_file.is_file(), f"STAGE3_L4 prior receipt missing: {key}")
                rec = json.loads(rec_file.read_text(encoding="utf-8"))
                require(
                    rec.get("status") == "PASS" and rec.get("kind") == src["kind"],
                    f"STAGE3_L4 prior receipt not PASS/{src['kind']}: {key}",
                )
                require(rec.get("job_sha256") == cell.get("job_sha256"), f"STAGE3_L4 prior job SHA drift: {key}")
                require(
                    rec.get("run_id") == src["run_id"] and rec.get("code_sha") == src["code_sha"],
                    f"STAGE3_L4 prior receipt lineage drift: {key}",
                )
                hashes = rec.get("artifact_sha256")
                require(
                    isinstance(hashes, dict) and set(hashes) == set(src["artifacts"]),
                    f"STAGE3_L4 prior artifact manifest drift: {key}",
                )
                require(cell.get("artifact_sha256") == hashes, f"STAGE3_L4 prior summary manifest drift: {key}")
                for name, digest in hashes.items():
                    target = directory / name
                    require(target.is_file() and sha256_file(target) == digest, f"STAGE3_L4 prior bytes drift: {key}")
                units.append(
                    {
                        "market": market,
                        "benchmark": cell["benchmark"],
                        "model_key": model_key,
                        "candidate_id": spec_model["candidate_id"],
                        "label_horizon": int(spec_model["label_horizon"]),
                        "source_stage": spec_model["source_stage"],
                        "unit_source": source,
                        "combo_key": combo_key,
                        "topk": combos[combo_key][0],
                        "n_drop": combos[combo_key][1],
                        "role": role,
                        "phase": phase,
                        "anchor": source != "stage2_l1" and phase == 0,
                        "metrics": cell["metrics"],
                        "stressed": cell["stressed"],
                        "source_signal_sha256": cell.get("source_signal_sha256", "")
                        or (hashes.get("signal.parquet") if isinstance(hashes, dict) else ""),
                        "artifact_sha256": hashes,
                        "job_sha256": cell["job_sha256"],
                        "receipt_sha256": sha256_file(rec_file),
                        "provider_fingerprint": rec.get("provider_fingerprint", ""),
                    }
                )
    require(len(units) == EXPECTED_PRIOR_UNITS, f"STAGE3_L4 prior coverage must be {EXPECTED_PRIOR_UNITS} units")
    for market in MARKETS:
        market_units = [unit for unit in units if unit["market"] == market]
        require(len(market_units) == 18, f"STAGE3_L4 prior coverage drift: {market}")
        counts: dict[str, int] = {}
        for unit in market_units:
            counts[unit["unit_source"]] = counts.get(unit["unit_source"], 0) + 1
        require(counts == PRIOR_UNITS_BY_MARKET_SOURCE[market], f"STAGE3_L4 prior source split drift: {market}")
    require(
        len({(unit["market"], unit["model_key"], unit["combo_key"], unit["phase"]) for unit in units}) == len(units),
        "STAGE3_L4 duplicate prior units",
    )
    return sorted(units, key=lambda row: (row["market"], row["model_key"], row["combo_key"], row["phase"]))


# ---------------------------------------------------------------------------
# Five-phase ranking and committed CSV renderers.
# ---------------------------------------------------------------------------

PHASE_KEYS = tuple(f"phase{phase:02d}" for phase in ALL_5_PHASES)


def _median(values: list[float]) -> float:
    return float(statistics.median(values)) if values else float("nan")


def rank_five_phase_strategies(units_45: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank one market's 9 finalist strategies over the full five phases.

    Expects exactly 45 units (9 (model, combo) pairs x 5 phases). Challenger
    excess is measured against the same-phase official baseline
    (``OFFICIAL_BASELINE``) so every phase compares like for like. Sort key is
    ``(worst_relative_excess_cagr, median_relative_excess_cagr,
    mean_relative_excess_cagr, median_ir, median_sharpe, worst_max_drawdown)``
    descending; drawdowns are negative so descending prefers the shallowest.
    """
    require(
        isinstance(units_45, list) and len(units_45) == EXPECTED_UNITS_PER_MARKET,
        f"STAGE3_L4 ranking needs exactly {EXPECTED_UNITS_PER_MARKET} units",
    )
    markets = {unit["market"] for unit in units_45}
    require(len(markets) == 1, "STAGE3_L4 ranking must stay within one market")
    market = next(iter(markets))
    roster_pairs = [(model, combo) for model, combo, _role in STAGE3_STRATEGIES_BY_MARKET[market]]
    by_pair: dict[tuple[str, str], dict[int, dict[str, Any]]] = {}
    for unit in units_45:
        pair = (unit["model_key"], unit["combo_key"])
        require(pair in roster_pairs, f"STAGE3_L4 ranking foreign pair: {pair}")
        require(unit["phase"] in ALL_5_PHASES, f"STAGE3_L4 ranking phase invalid: {unit['phase']}")
        require(
            unit["phase"] not in by_pair.setdefault(pair, {}),
            f"STAGE3_L4 duplicate ranking unit: {pair}/phase{unit['phase']}",
        )
        by_pair[pair][unit["phase"]] = unit
    require(
        len(by_pair) == 9 and all(set(phases) == set(ALL_5_PHASES) for phases in by_pair.values()),
        "STAGE3_L4 ranking pairs incomplete",
    )
    require(set(by_pair) == set(roster_pairs), "STAGE3_L4 ranking must cover the roster")
    baseline_phases = by_pair[OFFICIAL_BASELINE]
    rows: list[dict[str, Any]] = []
    for (model_key, combo_key), phases in by_pair.items():
        model_spec = ALL_L4_MODELS[model_key]
        excess = [float(phases[phase]["metrics"]["relative_excess_cagr"]) for phase in ALL_5_PHASES]
        cagr = [float(phases[phase]["metrics"]["strategy_cagr"]) for phase in ALL_5_PHASES]
        sharpe = [float(phases[phase]["metrics"]["sharpe"]) for phase in ALL_5_PHASES]
        ir = [float(phases[phase]["metrics"]["information_ratio"]) for phase in ALL_5_PHASES]
        mdd = [float(phases[phase]["metrics"]["strategy_max_drawdown"]) for phase in ALL_5_PHASES]
        turnover = [float(phases[phase]["metrics"]["mean_turnover"]) for phase in ALL_5_PHASES]
        beat_baseline = sum(
            1
            for phase in ALL_5_PHASES
            if float(phases[phase]["metrics"]["relative_excess_cagr"])
            > float(baseline_phases[phase]["metrics"]["relative_excess_cagr"])
        )
        stressed10 = [
            float(phases[phase]["stressed"]["10bp"]["strategy_total_return"])
            - float(baseline_phases[phase]["stressed"]["10bp"]["strategy_total_return"])
            for phase in ALL_5_PHASES
        ]
        stressed20 = [
            float(phases[phase]["stressed"]["20bp"]["strategy_total_return"])
            - float(baseline_phases[phase]["stressed"]["20bp"]["strategy_total_return"])
            for phase in ALL_5_PHASES
        ]
        row = {
            "market": market,
            "model_key": model_key,
            "combo_key": combo_key,
            "role": strategy_role(market, model_key, combo_key),
            "label_horizon": int(model_spec["label_horizon"]),
            "worst_relative_excess_cagr": min(excess),
            "median_relative_excess_cagr": _median(excess),
            "mean_relative_excess_cagr": float(statistics.mean(excess)),
            "worst_strategy_cagr": min(cagr),
            "median_strategy_cagr": _median(cagr),
            "mean_strategy_cagr": float(statistics.mean(cagr)),
            "median_sharpe": _median(sharpe),
            "median_ir": _median(ir),
            "worst_max_drawdown": min(mdd),
            "median_max_drawdown": _median(mdd),
            "median_mean_turnover": _median(turnover),
            "positive_excess_phases": sum(1 for value in excess if value > 0),
            "beats_official_baseline_phases": beat_baseline,
            "worst_stressed10bp_excess_vs_baseline": min(stressed10),
            "worst_stressed20bp_excess_vs_baseline": min(stressed20),
        }
        for phase_index, phase in enumerate(ALL_5_PHASES):
            row[f"phase{phase:02d}_relative_excess_cagr"] = excess[phase_index]
        rows.append(row)
    rows.sort(
        key=lambda r: (
            r["worst_relative_excess_cagr"],
            r["median_relative_excess_cagr"],
            r["mean_relative_excess_cagr"],
            r["median_ir"],
            r["median_sharpe"],
            r["worst_max_drawdown"],
        ),
        reverse=True,
    )
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    return rows


STAGE3_SCOREBOARD_COLUMNS = (
    "market",
    "benchmark",
    "model_key",
    "candidate_id",
    "label_horizon",
    "source_stage",
    "unit_source",
    "combo_key",
    "topk",
    "n_drop",
    "role",
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
    "source_signal_sha256",
    "job_sha256",
)


def render_stage3_scoreboard(summary: dict[str, Any], csv_path: str | Path) -> Path:
    """Render the 90-unit five-phase scoreboard CSV (36 prior + 54 new)."""
    cells = summary.get("cells")
    require(
        isinstance(cells, list) and len(cells) == EXPECTED_FIVE_PHASE_UNITS,
        f"STAGE3_L4 summary must carry exactly {EXPECTED_FIVE_PHASE_UNITS} cells",
    )
    out = Path(csv_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(STAGE3_SCOREBOARD_COLUMNS))
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
                    "source_stage": cell.get("source_stage", ""),
                    "unit_source": cell.get("unit_source", ""),
                    "combo_key": cell["combo_key"],
                    "topk": cell["topk"],
                    "n_drop": cell["n_drop"],
                    "role": cell.get("role", ""),
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
                    "stressed10bp_strategy_total_return": (cell.get("stressed") or {})
                    .get("10bp", {})
                    .get("strategy_total_return"),
                    "stressed20bp_strategy_total_return": (cell.get("stressed") or {})
                    .get("20bp", {})
                    .get("strategy_total_return"),
                    "account_return_max_error": metrics.get("account_return_max_error"),
                    "source_signal_sha256": cell.get("source_signal_sha256", "")
                    or (cell.get("artifact_sha256") or {}).get("signal.parquet", ""),
                    "job_sha256": cell.get("job_sha256", ""),
                }
            )
    return out


STAGE3_RANKING_COLUMNS = (
    "market",
    "rank",
    "model_key",
    "combo_key",
    "role",
    "label_horizon",
    "worst_relative_excess_cagr",
    "median_relative_excess_cagr",
    "mean_relative_excess_cagr",
    "phase00_relative_excess_cagr",
    "phase04_relative_excess_cagr",
    "phase06_relative_excess_cagr",
    "phase10_relative_excess_cagr",
    "phase15_relative_excess_cagr",
    "worst_strategy_cagr",
    "median_strategy_cagr",
    "mean_strategy_cagr",
    "median_sharpe",
    "median_ir",
    "worst_max_drawdown",
    "median_max_drawdown",
    "median_mean_turnover",
    "positive_excess_phases",
    "beats_official_baseline_phases",
    "worst_stressed10bp_excess_vs_baseline",
    "worst_stressed20bp_excess_vs_baseline",
)


def render_stage3_rankings(summary: dict[str, Any], csv_path: str | Path) -> Path:
    """Render the 2 x 9 five-phase strategy ranking CSV (18 rows)."""
    rankings = summary.get("rankings")
    require(isinstance(rankings, dict) and set(rankings) == set(MARKETS), "STAGE3_L4 rankings must cover both markets")
    for market in MARKETS:
        require(isinstance(rankings[market], list) and len(rankings[market]) == 9, f"STAGE3_L4 {market} ranking drift")
    out = Path(csv_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(STAGE3_RANKING_COLUMNS))
        writer.writeheader()
        for market in MARKETS:
            for row in rankings[market]:
                writer.writerow({**{key: row.get(key, "") for key in STAGE3_RANKING_COLUMNS}, "market": market})
    return out
