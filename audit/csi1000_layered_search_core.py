"""CSI1000 budgeted layered strategy search core (PR-S0).

Pure-Python search configuration, persistent budget ledger, SHA cache keys,
paired product-decision rules and auditable bundle writers for contract 53.
No Modal, no Qlib, no training.

Status: NOT EXECUTED. This module only defines the frozen protocol and
fail-closed gates. Any real Modal spend requires a separate explicit USD30
approval and a foreground operator run. Merging this code does not approve
spend, does not run a search, and does not change production.

MDD convention: drawdowns are positive magnitudes in percentage points
(e.g. 8.0 means an 8% peak-to-trough fall). Degradation is always
challenger minus baseline on those magnitudes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

PROTOCOL_VERSION = "csi1000_layered_search_v1"
CONTRACT_DOC = "docs/experiments/53-csi1000-budgeted-layered-strategy-search-contract-20261012.md"
CONTRACT_PR = 67
RESEARCH_SEED_L2 = 20261012

# Frozen identities (re-validated at runtime from verifiable files; see load_frozen_profiles).
BASELINE_CANDIDATE = "23b92de05cf36c82998de684d0fbf64d81ee54490d755bd3cf96311c00286785"
WINNER_CANDIDATE = "4e908173705c76fee3782be37068672a3a845bd61894202f3152afe3b9d81ef2"
BASELINE_CONFIG_SHA = "793021af66034ec508848ee032f6d309103291981abb63d11ae779da9c5ee7d3"
WINNER_CONFIG_SHA = "0f2cf94d179e7b9982f873588a3afe25fa02991c873954fea11d2db0e025cb88"
STAGE_B_SNAPSHOT_TOKEN = "51756897fc75230493194aef2e48815e4e9f3cc7426cc135f47bb8ba8e0d21e1"
STAGE_A_SNAPSHOT_TOKEN = "a2ecd8d1cadf404670762a81b1fd33a377f2ef2aaf27a0d9ac6787366defd65b"
SOURCE_VOLUME = "qlib-cn-data"
RESEARCH_VOLUME = "qlib-csi1000-layered-search"
FROZEN_STAGE_B_RESULT = "results/csi1000_stage_b/stage_b_full_51756897fc752304.json"

# Execution / accounting (frozen product contract).
INITIAL_CASH_CNY = 100_000_000
FEE_BUY_BP = 5.0
FEE_SELL_BP = 15.0
FEE_MIN_CNY = 5.0
TOPK_BASELINE = 20
DROPN_BASELINE = 2
EXECUTION_SCHEMA = "tclose_to_t1open"
QUOTE_PATH = "static_quote_union_f1"

# Stage-B phase geometry (freq20 canonical five phases; L1 screens on 0/10).
PHASES_FIVE_FREQ20 = [0, 4, 6, 10, 15]
PHASES_SCREEN_TWO = [0, 10]
PHASES_FINAL_FREQ10 = [0, 2, 3, 5, 7]
PHASES_FINAL_FREQ40 = [0, 8, 12, 20, 30]

# All 11 Stage-B models with full candidate ids (baseline rank 48, winner rank 5).
STAGE_B_11_MODELS = [
    "4e908173705c76fee3782be37068672a3a845bd61894202f3152afe3b9d81ef2",
    "c98856b460aba687640d422e85a82a545bf5b6ca932e906a180699e0f6cad19b",
    "e74b3bbe2278aa89b33375fe21bc308bc5cf0b2e5ee512ab1b05469b52a4825a",
    "1db146eec9deda008be3e6612a1fc48a057e34b7891d3d70ca69a6e4c4fe62a2",
    "23b92de05cf36c82998de684d0fbf64d81ee54490d755bd3cf96311c00286785",
    "40b872dfe452b649e666598d7438c0c342b6de45b9d8fa4df4d06e4080ab9d19",
    "c04ca4163f77e6e24a7c7adf556d5f11b85ab526d5c3e8b2c9dd11c2bfca7b46",
    "a419047ab97ec0273ebe67791b6e225e824b2764f5932d73651efd89add5a677",
    "17dbfe28fea8d7a782918ee426e01c7f7b23a08b44c00cc7e2d8391974efd5f2",
    "b034ac4645fb45bba35bc185693b7776344c9117f594e4c972671062cdcf9457",
    "59b93929bff1e736228d31403a167781aff433d27da1870bcf9fe34e04b6bbb9",
]
L1B_6_MODELS = [
    "23b92de05cf36c82998de684d0fbf64d81ee54490d755bd3cf96311c00286785",
    "4e908173705c76fee3782be37068672a3a845bd61894202f3152afe3b9d81ef2",
    "c98856b460aba687640d422e85a82a545bf5b6ca932e906a180699e0f6cad19b",
    "a419047ab97ec0273ebe67791b6e225e824b2764f5932d73651efd89add5a677",
    "e74b3bbe2278aa89b33375fe21bc308bc5cf0b2e5ee512ab1b05469b52a4825a",
    "59b93929bff1e736228d31403a167781aff433d27da1870bcf9fe34e04b6bbb9",
]

# Twelve pre-registered (TopK, DropN) combos; DropN <= TopK always.
L1_PORTFOLIO_GRID: List[Tuple[int, int]] = [
    (10, 1),
    (10, 2),
    (10, 3),
    (20, 1),
    (20, 2),
    (20, 3),
    (20, 5),
    (30, 1),
    (30, 3),
    (30, 5),
    (50, 2),
    (50, 5),
]
L1A_8_COMBOS: List[Tuple[int, int]] = [(20, 1), (20, 2), (20, 3), (20, 5), (10, 1), (10, 3), (30, 3), (50, 2)]

# Budgets (§2.1). Totals must sum exactly to the USD30 global cap.
LAYER_CAPS_USD = {"p0": 2.0, "l1": 6.0, "l2": 12.0, "l3": 6.0, "l4": 4.0}
GLOBAL_CAP_USD = 30.0
GLOBAL_FIT_CAP = 1200
L2_FIT_CAP = 320
L3_FIT_CAP = 250
L1_CELL_CAP = 264
L4_CONFIG_CAP = 6
L4_CELL_CAP = 30
WORKER_CAP = 8
TIME_CAP_HOURS = 72.0
WINDOW_DAYS = 7
RESERVE_FRACTION = 0.20

# Reproduction tolerances (P0 controls).
AMOUNT_TOL_CNY = 0.01
RETURN_TOL = 1e-10

# Final product thresholds (§1.2) and L1 coarse screen (§3).
FINAL_WORST_MIN_PP = 1.0
FINAL_MEDIAN_MIN_PP = 1.0
FINAL_MIN_PHASES_BEAT = 4
FINAL_MDD_MAX_DEGRADE_PP = 3.0
FINAL_TURNOVER_MAX_RATIO = 1.25
COARSE_MIN_PP = 0.5
COARSE_MDD_MAX_DEGRADE_PP = 5.0
COARSE_TURNOVER_MAX_RATIO = 1.5

# Paired phase rows must carry these fields; anything missing is NOT_ELIGIBLE, never assumed.
REQUIRED_PHASE_FIELDS = (
    "phase",
    "net_relative_cagr_pp",
    "net_total_return",
    "mdd_pp",
    "turnover",
    "n_days",
    "coverage_ok",
    "ledger_ok",
    "data_ok",
    "plus10bp_advantage_pp",
)

STOP_CODES = (
    "OK_CONTINUE",
    "STOP_BUDGET",
    "STOP_COST_UNKNOWN",
    "STOP_P0",
    "STOP_SOURCE_UNAVAILABLE",
    "STOP_L1",
    "STOP_L2",
    "STOP_L3",
    "STOP_INCOMPLETE_BUDGET",
    "KEEP_BASELINE_FOR_PRODUCT_SELECTION",
    "STOP_PRODUCT_BLOCKED",
    "PROPOSE_PRODUCT_PROFILE_VERSION",
)


def require(ok: bool, why: str) -> None:
    """Fail closed with a contract-scoped error."""
    if not ok:
        raise ValueError("LAYERED_SEARCH_BLOCKED: " + why)


def stable_sha(obj: Any) -> str:
    """Deterministic SHA256 over canonical JSON."""
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str).encode()
    ).hexdigest()


def sha256_file(path: str | Path) -> str:
    """SHA256 of a file's bytes."""
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def utcnow() -> str:
    """UTC timestamp for ledger/run-card entries."""
    return datetime.now(timezone.utc).isoformat()


def load_frozen_profiles(repo_root: str | Path = ".") -> Dict[str, Dict[str, str]]:
    """Re-read frozen Baseline/Winner identities from verifiable repo files.

    Never blind-copies module constants: asserts equality with
    ``csi1000_production_config.py`` profiles.
    """
    import importlib.util

    mod_path = Path(repo_root) / "csi1000_production_config.py"
    require(mod_path.is_file(), f"production config unavailable: {mod_path}")
    spec = importlib.util.spec_from_file_location("csi1000_production_config_frozen_check", str(mod_path))
    require(spec is not None and spec.loader is not None, "cannot load production config module")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    profiles = {mod.BASELINE_PROFILE: None, mod.WINNER_PROFILE: None}
    out: Dict[str, Dict[str, str]] = {}
    for profile in profiles:
        manifest = mod.profile_manifest(profile)
        out[profile] = {
            "candidate_id": manifest["candidate_id"],
            "model_config_sha256": manifest["model_config_sha256"],
        }
    require(out[mod.BASELINE_PROFILE]["candidate_id"] == BASELINE_CANDIDATE, "baseline candidate drift")
    require(out[mod.BASELINE_PROFILE]["model_config_sha256"] == BASELINE_CONFIG_SHA, "baseline config SHA drift")
    require(out[mod.WINNER_PROFILE]["candidate_id"] == WINNER_CANDIDATE, "winner candidate drift")
    require(out[mod.WINNER_PROFILE]["model_config_sha256"] == WINNER_CONFIG_SHA, "winner config SHA drift")
    return out


def check_layer_totals() -> None:
    """The five layer caps must sum exactly to the USD30 global cap."""
    total = round(sum(LAYER_CAPS_USD.values()), 2)
    require(total == GLOBAL_CAP_USD, f"layer caps sum {total} != global {GLOBAL_CAP_USD}")


def enumerate_cells(stage: str) -> List[Dict[str, Any]]:
    """Enumerate deterministic (model, phase, TopK, DropN) cells per contract §3.

    Parameters
    ----------
    stage : str
        One of p0, l1-a, l1-b, l1-c.

    Returns
    -------
    list of dict
        Cell dicts with ``cell_key`` ``candidate_id`` ``phase`` ``topk`` ``dropn``.
    """
    require(stage in ("p0", "l1-a", "l1-b", "l1-c"), f"unsupported stage: {stage}")
    for topk, dropn in L1_PORTFOLIO_GRID:
        require(dropn <= topk, f"invalid combo TopK={topk} DropN={dropn}")
    if stage == "p0":
        models = [BASELINE_CANDIDATE, WINNER_CANDIDATE]
        combos = [(TOPK_BASELINE, DROPN_BASELINE)]
        phases = PHASES_SCREEN_TWO
    elif stage == "l1-a":
        models = [BASELINE_CANDIDATE, WINNER_CANDIDATE]
        combos = L1A_8_COMBOS
        phases = PHASES_SCREEN_TWO
    elif stage == "l1-b":
        models = L1B_6_MODELS
        combos = L1_PORTFOLIO_GRID
        phases = PHASES_SCREEN_TWO
    else:
        models = STAGE_B_11_MODELS
        combos = L1_PORTFOLIO_GRID
        phases = PHASES_SCREEN_TWO
    cells = []
    for cid in models:
        for phase in phases:
            for topk, dropn in combos:
                cells.append(
                    {
                        "cell_key": f"{cid[:8]}_p{phase:02d}_k{topk}_d{dropn}",
                        "candidate_id": cid,
                        "phase": phase,
                        "topk": topk,
                        "dropn": dropn,
                        "fees_bp": {"buy": FEE_BUY_BP, "sell": FEE_SELL_BP, "min_cny": FEE_MIN_CNY},
                        "execution": EXECUTION_SCHEMA,
                        "quote_path": QUOTE_PATH,
                        "initial_cash_cny": INITIAL_CASH_CNY,
                    }
                )
    expected = {"p0": 4, "l1-a": 32, "l1-b": 144, "l1-c": 264}[stage]
    require(len(cells) == expected, f"{stage} enumerates {len(cells)} != {expected}")
    keys = [c["cell_key"] for c in cells]
    require(len(set(keys)) == len(keys), f"{stage} duplicate cell keys")
    return cells


def signal_cache_key(
    code_sha: str, provider_sha: str, candidate_sha: str, train_asof: str, calendar: str, phase: int
) -> str:
    """Immutable signal-level cache key (L2/L3 retrains)."""
    require(
        len(code_sha) == 40 and re.fullmatch(r"[0-9a-f]{40}", code_sha) is not None, "code_sha must be full git SHA"
    )
    return stable_sha(
        {
            "version": PROTOCOL_VERSION,
            "code_sha": code_sha,
            "provider_sha": provider_sha,
            "candidate_sha": candidate_sha,
            "train_asof": train_asof,
            "calendar": calendar,
            "phase": phase,
        }
    )


def replay_cache_key(
    signal_sha: str, topk: int, dropn: int, quote_sha: str, engine_version: str = PROTOCOL_VERSION
) -> str:
    """Replay-level cache key from the real frozen signal SHA (never a placeholder).

    Parameters
    ----------
    signal_sha : str
        SHA256 of the frozen ``signal.parquet`` bytes for this candidate/phase.
    topk, dropn : int
        Portfolio combo under test.
    quote_sha : str
        SHA256 of the static F1 quote-union code list bytes.
    engine_version : str
        Replay engine version; any change invalidates the cache.
    """
    require(re.fullmatch(r"[0-9a-f]{64}", signal_sha or "") is not None, "signal_sha must be a real SHA256")
    require(re.fullmatch(r"[0-9a-f]{64}", quote_sha or "") is not None, "quote_sha must be a real SHA256")
    require(dropn <= topk, "DropN must be <= TopK")
    return stable_sha(
        {
            "signal_sha": signal_sha,
            "topk": topk,
            "dropn": dropn,
            "quote_sha": quote_sha,
            "fees": [FEE_BUY_BP, FEE_SELL_BP, FEE_MIN_CNY],
            "execution": EXECUTION_SCHEMA,
            "engine": engine_version,
        }
    )


def check_cache_hit(cached_record: Dict[str, Any], current_sources: Dict[str, Any]) -> str:
    """Strict SHA+version resume against real file bytes.

    Both sides must carry the source-file SHA256, the replay cache key and the
    output-artifact SHA256. mtime or filename equality alone never counts as a
    hit. Any mismatch is FAIL_CONFLICT: the caller must refuse to overwrite.
    """
    for k in ("source_sha256", "output_sha256", "cache_key"):
        require(k in cached_record, f"cached record missing {k}")
    for k in ("source_sha256", "cache_key", "output_sha256"):
        require(k in current_sources, f"current sources missing {k}")
    for label, val in (
        ("cached source_sha256", cached_record.get("source_sha256")),
        ("cached output_sha256", cached_record.get("output_sha256")),
        ("cached cache_key", cached_record.get("cache_key")),
    ):
        require(re.fullmatch(r"[0-9a-f]{64}", str(val or "")) is not None, f"{label} is not a real SHA")
    if current_sources["cache_key"] != cached_record["cache_key"]:
        return "FAIL_CONFLICT"
    if current_sources["source_sha256"] != cached_record["source_sha256"]:
        return "FAIL_CONFLICT"
    if not cached_record.get("output_sha256") or cached_record["output_sha256"] != current_sources["output_sha256"]:
        return "FAIL_CONFLICT"
    return "RESUME_VERIFIED"


def check_output_not_in_source(source_roots: List[str | Path] | str | Path, output: str | Path) -> Path:
    """Fail closed if a research output path lands inside immutable inputs or /vol.

    Parameters
    ----------
    source_roots : list
        Immutable input roots (snapshot root, provider dir, repo root).
    output : path
        Proposed research output directory.
    """
    roots = [Path(r).resolve() for r in ([source_roots] if isinstance(source_roots, (str, Path)) else source_roots)]
    out = Path(output).resolve()
    for src in roots:
        require(out != src and not out.is_relative_to(src), f"output inside immutable input: {src}")
    require(out != Path("/vol") and not str(out).startswith("/vol/"), "output inside original /vol source mount")
    return out


def check_cell_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Strictly validate one executed cell record; never trust an incoming PASS claim.

    Requires status exactly ``PASS``, real 64-hex source/output SHAs, finite NAV
    and fee totals, confirmed ledger/coverage flags, zero training and zero
    promotion authorization. Anything else raises.
    """
    require(record.get("status") == "PASS", f"cell {record.get('cell_key')} is not a verified PASS")
    for k in ("source_sha256", "output_sha256", "quote_sha256", "signal_sha256"):
        require(
            re.fullmatch(r"[0-9a-f]{64}", str(record.get(k) or "")) is not None,
            f"cell {record.get('cell_key')} has no real {k}",
        )
    require(record.get("cache_key"), f"cell {record.get('cell_key')} missing cache_key")
    nav = record.get("final_nav_cny")
    require(isinstance(nav, (int, float)) and math.isfinite(nav) and nav > 0, "cell NAV missing or non-finite")
    cost = record.get("total_cost_cny")
    require(isinstance(cost, (int, float)) and math.isfinite(cost) and cost >= 0, "cell fee total missing or invalid")
    for flag in ("ledger_ok", "coverage_ok", "data_ok"):
        require(record.get(flag) is True, f"cell {record.get('cell_key')} flag {flag} is not true")
    require(record.get("training_performed") is False, "training flag must be false for P0/L1 replay cells")
    require(record.get("promotion_authorized") is False, "promotion must never be pre-authorized")
    require(isinstance(record.get("n_days"), int) and record["n_days"] == 424, "cell must cover all 424 days")
    if record.get("topk") == 20 and record.get("dropn") == 2:
        require(record.get("legacy_reproduction") == "PASS", "Top20/Drop2 legacy control not reproduced")
        require(record.get("corrected_reference") == "PASS", "Top20/Drop2 corrected F1 control not reproduced")
    return {
        "cell_key": record["cell_key"],
        "final_nav_cny": float(nav),
        "total_cost_cny": float(cost),
        "n_days": record["n_days"],
        "output_sha256": record["output_sha256"],
    }


def require_p0_pass(summary: Dict[str, Any], outcomes: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    """Gate L1-A on a fully verified P0 summary: exactly 4/4 real PASS cells.

    Every outcome is re-validated with :func:`check_cell_record`; a skeleton,
    placeholder or unvalidated entry blocks L1 with STOP_P0.
    """
    require(summary.get("stage") == "p0", "P0 summary missing: run --stage p0 first")
    require(int(summary.get("expected", -1)) == 4, "P0 must cover exactly 4 cells")
    require(int(summary.get("completed", -1)) == 4 and int(summary.get("blocked", -1)) == 0, "P0 is not 4/4 PASS")
    rows = outcomes if outcomes is not None else summary.get("outcomes", [])
    require(len(rows) == 4, "P0 outcomes missing")
    expected_cells = {c["cell_key"] for c in enumerate_cells("p0")}
    actual_cells = [r.get("cell_key") for r in rows]
    require(
        len(set(actual_cells)) == 4 and set(actual_cells) == expected_cells, "P0 has wrong or duplicate cell identities"
    )
    for r in rows:
        require(r.get("candidate_id") in (BASELINE_CANDIDATE, WINNER_CANDIDATE), "P0 candidate identity invalid")
        require(r.get("phase") in (0, 10), "P0 phase invalid")
        require((r.get("topk"), r.get("dropn")) == (20, 2), "P0 portfolio identity invalid")
        require(r.get("legacy_reproduction") == "PASS", "P0 legacy reproducibility missing")
        require(r.get("corrected_reference") == "PASS", "P0 F1 reproducibility missing")
    checked = [check_cell_record(r) for r in rows]
    return {"status": "P0_VERIFIED", "cells": [c["cell_key"] for c in checked]}


def require_signed_l1a_export(summary: Dict[str, Any], outcomes: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    """Gate L1-B reuse on a fully verified L1-A summary: exactly 32/32 real PASS cells.

    Mirrors :func:`require_p0_pass` for the L1-A roster. Every record is
    re-validated with :func:`check_cell_record`; anything else blocks reuse
    with STOP_L1.
    """
    require(summary.get("stage") == "l1-a", "L1-A summary missing: run --stage l1-a first")
    require(int(summary.get("expected", -1)) == 32, "L1-A must cover exactly 32 cells")
    require(int(summary.get("completed", -1)) == 32 and int(summary.get("blocked", -1)) == 0, "L1-A is not 32/32 PASS")
    rows = outcomes if outcomes is not None else summary.get("verified_cells", [])
    require(len(rows) == 32, "L1-A verified cells missing")
    expected_cells = {c["cell_key"] for c in enumerate_cells("l1-a")}
    actual_cells = [r.get("cell_key") for r in rows]
    require(
        len(set(actual_cells)) == 32 and set(actual_cells) == expected_cells,
        "L1-A has wrong or duplicate cell identities",
    )
    checked = [check_cell_record(r) for r in rows]
    return {"status": "L1A_VERIFIED", "cells": [c["cell_key"] for c in checked]}


def verify_export_cells(
    evidence_root: str | Path,
    expected_keys: set,
    scope: str,
    summary_cells: Dict[str, Dict[str, Any]] | None = None,
) -> Dict[str, Path]:
    """Re-read a downloaded research export: manifest roster plus per-cell bytes.

    Checks the export ``source_manifest_final.json`` roster against the
    expected cell keys, then re-reads every ``cells/<key>/cell.json`` and
    verifies the recorded cache/source/output SHAs plus all four artifact
    file hashes against real bytes. When ``summary_cells`` is given, each
    persisted record must also match the signed summary on cache, source,
    output and provider-snapshot SHAs. The SHA256SUMS walk itself stays with
    the caller (reconcile owns it). Returns paths for further checks.
    """
    require(scope in ("P0", "L1-A"), f"unknown export scope {scope}")
    root = Path(evidence_root)
    manifest_path = root / "source_manifest_final.json"
    require(manifest_path.is_file(), f"{scope} source manifest missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_keys = {x.get("cell_key") for x in manifest.get("cells", [])}
    require(manifest_keys == expected_keys, f"{scope} source manifest roster differs from expected cells")
    for key in sorted(expected_keys):
        directory = root / "cells" / key
        persisted = json.loads((directory / "cell.json").read_text(encoding="utf-8"))
        require(persisted.get("cell_key") == key, f"{scope} exported cell identity drift: {key}")
        check_cell_record(persisted)
        if summary_cells is not None:
            signed = summary_cells.get(key)
            require(signed is not None, f"{scope} summary lacks exported cell: {key}")
            for sha_field in ("cache_key", "source_sha256", "output_sha256", "provider_snapshot_sha256"):
                require(
                    persisted.get(sha_field) == signed.get(sha_field),
                    f"{scope} exported cell differs from summary: {sha_field}",
                )
        names = ("daily_nav.csv", "fixed_report.parquet", "fixed_decisions.json", "metrics.json")
        recorded_hashes = persisted.get("artifact_sha256")
        require(
            isinstance(recorded_hashes, dict) and set(recorded_hashes) == set(names),
            f"{scope} exported artifact manifest incomplete: {key}",
        )
        for name in names:
            require(
                (directory / name).is_file() and sha256_file(directory / name) == recorded_hashes[name],
                f"{scope} exported {name} hash differs: {key}",
            )
    return {"root": root, "manifest": manifest_path}


def trial_row_key(run_id: str | None, cell_key: str | None) -> Tuple[str, str]:
    """Run-scoped trial identity. Re-execution under a new run is a new row."""
    require(bool(run_id) and isinstance(run_id, str), "trial row needs a real run-id")
    require(bool(cell_key), "trial row missing cell_key")
    return (run_id, cell_key)


def manifest_source_index(manifest: Dict[str, Any]) -> Dict[Tuple[str, int], Dict[str, str]]:
    """Index frozen 11x5 source SHAs by (candidate_id, phase)."""
    sources = manifest.get("frozen_11x5_sources", manifest.get("sources", []))
    index = {}
    for entry in sources:
        if isinstance(entry, dict) and entry.get("candidate_id") is not None and entry.get("phase") is not None:
            index[(entry["candidate_id"], int(entry["phase"]))] = entry
    require(len(index) == 55, f"frozen 11x5 source index has {len(index)} entries, need 55")
    return index


def _same_code(prior_sha: str, current_sha: str) -> bool:
    """Code identity across full (40-hex) and short (12-hex) SHA forms."""
    require(bool(prior_sha) and bool(current_sha), "code SHA missing for reuse comparison")
    return prior_sha == current_sha or current_sha.startswith(prior_sha) or prior_sha.startswith(current_sha)


def verify_reuse_compatibility(
    prior_record: Dict[str, Any],
    manifest_sources: Dict[Tuple[str, int], Dict[str, str]],
    current: Dict[str, Any],
) -> Dict[str, Any]:
    """Judge whether a prior verified cell may be reused without recomputation.

    Never trusts the incoming record: re-validates it with
    :func:`check_cell_record`, recomputes the replay cache key from real
    frozen SHAs (which transitively pins fees, execution schema and engine
    version to the frozen constants), and requires quote/provider/source
    identity with the current run. ``current`` must carry ``quote_sha``,
    ``provider_snapshot_sha256``, ``code_sha`` (this run), ``prior_run_id``
    and ``prior_code_sha`` (from the prior summary, which owns run identity —
    cell records do not), plus ``verified_output_sha`` (output SHAs re-measured
    from real bytes by the caller). A differing replay code SHA additionally
    requires an explicit reviewer-approved ``version_equivalence_ref``;
    without it the cell must be recomputed instead of reused. Returns an
    immutable attestation dict.
    """
    check_cell_record(prior_record)
    for ctx_field in ("quote_sha", "provider_snapshot_sha256", "prior_run_id", "prior_code_sha", "verified_output_sha"):
        require(current.get(ctx_field), f"reuse context missing {ctx_field}")
    require("code_sha" in current, "reuse context missing code_sha")
    require(prior_record.get("engine") == PROTOCOL_VERSION, "reuse engine version drift")
    require(prior_record.get("execution") == EXECUTION_SCHEMA, "reuse execution schema drift")
    require(prior_record.get("quote_sha256") == current["quote_sha"], "reuse quote union drift")
    require(
        prior_record.get("provider_snapshot_sha256") == current["provider_snapshot_sha256"],
        "reuse provider snapshot drift",
    )
    frozen = manifest_sources.get((prior_record.get("candidate_id"), int(prior_record.get("phase", -1))))
    require(frozen is not None, "reuse candidate/phase absent from frozen manifest")
    for record_key, frozen_key in (
        ("signal_sha256", "signal_sha256"),
        ("frozen_report_sha256", "report_sha256"),
        ("frozen_decision_sha256", "decision_sha256"),
    ):
        require(prior_record.get(record_key) == frozen.get(frozen_key), f"reuse frozen {frozen_key} drift")
    expected_cache = replay_cache_key(
        prior_record["signal_sha256"],
        int(prior_record["topk"]),
        int(prior_record["dropn"]),
        prior_record["quote_sha256"],
    )
    require(prior_record.get("cache_key") == expected_cache, "reuse cache key does not recompute")
    require(
        prior_record.get("output_sha256") == current["verified_output_sha"].get(prior_record["cell_key"]),
        "reuse output bytes were not re-measured",
    )
    if current.get("code_sha") and _same_code(current["prior_code_sha"], current["code_sha"]):
        equivalence = "IDENTICAL_CODE"
    else:
        require(
            bool(current.get("version_equivalence_ref")),
            "reuse across code versions needs an explicit reviewer-approved equivalence ref",
        )
        equivalence = f"REVIEWED_EQUIVALENT:{current['version_equivalence_ref']}"
    return {
        "cell_key": prior_record["cell_key"],
        "prior_run_id": current["prior_run_id"],
        "prior_code_sha": current["prior_code_sha"],
        "output_sha256": prior_record["output_sha256"],
        "source_sha256": prior_record["source_sha256"],
        "quote_sha256": prior_record["quote_sha256"],
        "engine": PROTOCOL_VERSION,
        "equivalence": equivalence,
        "verified_utc": utcnow(),
    }


def check_live_provider_match(
    live_snapshot_sha: str, live_quote_sha: str, plan_snapshot_sha: str, plan_quote_sha: str
) -> Dict[str, str]:
    """True cross-comparison: live volume identity must equal the reuse plan's.

    Called with values measured from the live source volume at dispatch time,
    against the unanimous values from the prior export. Any drift means the
    current provider snapshot differs from the one behind the old evidence, so
    mixing them into one evaluation is refused.
    """
    require(bool(live_snapshot_sha) and bool(live_quote_sha), "live provider identity missing")
    require(live_snapshot_sha == plan_snapshot_sha, "live provider snapshot differs from reuse evidence")
    require(live_quote_sha == plan_quote_sha, "live quote union differs from reuse evidence")
    return {"status": "PROVIDER_MATCH", "snapshot_sha256": live_snapshot_sha, "quote_sha256": live_quote_sha}


def materialize_reuse_cells(
    results_root: str | Path, new_prefix: str, old_prefix: str, specs: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Copy verified prior-run cells under the new prefix (all or nothing).

    Validates every spec against old-prefix bytes FIRST (record re-check,
    spec SHA agreement, all four artifact file hashes); only then copies each
    cell tree into the new prefix atomically (temp dir + rename, identical
    bytes resume, conflicts refuse). A later finalize therefore sees complete
    local files for reused cells, and ranking tools work over one directory.
    Returns the verified records in spec order.
    """
    require(bool(new_prefix) and bool(old_prefix) and new_prefix != old_prefix, "reuse prefixes invalid")
    root = Path(results_root)
    for part in (new_prefix, old_prefix):
        require(not part.startswith("/") and ".." not in part.split("/"), "unsafe reuse prefix")
    verified: List[Dict[str, Any]] = []
    for spec in specs:
        key = spec.get("cell_key")
        require(key, "reuse spec missing cell_key")
        src = root / old_prefix / "cells" / key
        record = json.loads((src / "cell.json").read_text(encoding="utf-8"))
        check_cell_record(record)
        for sha_field in ("source_sha256", "output_sha256", "cache_key"):
            require(record.get(sha_field) == spec.get(sha_field), f"reuse spec drift for {key}: {sha_field}")
        names = ("daily_nav.csv", "fixed_report.parquet", "fixed_decisions.json", "metrics.json")
        hashes = record.get("artifact_sha256")
        require(isinstance(hashes, dict) and set(hashes) == set(names), f"reuse artifact manifest absent: {key}")
        for name in names:
            require(
                (src / name).is_file() and sha256_file(src / name) == hashes[name],
                f"reuse artifact bytes drift: {key}/{name}",
            )
        verified.append(record)
    for spec, record in zip(specs, verified):
        key = spec["cell_key"]
        dest = root / new_prefix / "cells" / key
        if dest.exists():
            current = json.loads((dest / "cell.json").read_text(encoding="utf-8"))
            require(
                current.get("output_sha256") == record["output_sha256"]
                and current.get("source_sha256") == record["source_sha256"],
                f"reuse destination conflict: {key}",
            )
            manifest_hashes = current.get("artifact_sha256", {})
            for name in ("daily_nav.csv", "fixed_report.parquet", "fixed_decisions.json", "metrics.json"):
                require(
                    (dest / name).is_file() and sha256_file(dest / name) == manifest_hashes.get(name),
                    f"reuse destination bytes drift: {key}/{name}",
                )
            continue
        tmp = dest.parent / (dest.name + ".reuse-tmp")
        require(not tmp.exists(), f"stale reuse temp dir blocks copy: {key}")
        tmp.mkdir(parents=True, exist_ok=False)
        src = root / old_prefix / "cells" / key
        for name in ("cell.json", "daily_nav.csv", "fixed_report.parquet", "fixed_decisions.json", "metrics.json"):
            (tmp / name).write_bytes((src / name).read_bytes())
        tmp.replace(dest)
    return verified


def plan_l1b_reuse(
    prior_verified_cells: List[Dict[str, Any]],
    manifest_sources: Dict[Tuple[str, int], Dict[str, str]],
    current: Dict[str, Any],
) -> Dict[str, Any]:
    """Split the 144-cell L1-B roster into verified reuse and new compute.

    The overlap is exactly the 32 L1-A cells (same deterministic keys); the
    remaining 112 must be really executed. Every overlap cell needs a prior
    verified record passing :func:`verify_reuse_compatibility`; a missing or
    incompatible overlap cell is STOP, never silent recomputation (that would
    invalidate the bounded compute budget).
    """
    roster_b = {c["cell_key"]: c for c in enumerate_cells("l1-b")}
    roster_a = {c["cell_key"] for c in enumerate_cells("l1-a")}
    require(len(roster_b) == 144 and len(roster_a) == 32, "L1 roster geometry drift")
    overlap_keys = sorted(set(roster_b) & roster_a)
    new_keys = sorted(set(roster_b) - roster_a)
    require(len(overlap_keys) == 32, f"L1-B/L1-A overlap is {len(overlap_keys)}, need 32")
    require(len(new_keys) == 112, f"L1-B new compute is {len(new_keys)}, need 112")
    by_key = {r.get("cell_key"): r for r in prior_verified_cells}
    missing = [k for k in overlap_keys if k not in by_key]
    require(not missing, f"reuse source lacks overlap cells: {missing[:4]}")
    providers = {by_key[k].get("provider_snapshot_sha256") for k in overlap_keys}
    require(len(providers) == 1 and all(providers), "reuse export has no unanimous provider identity")
    attestations = [verify_reuse_compatibility(by_key[k], manifest_sources, current) for k in overlap_keys]
    return {
        "overlap_keys": overlap_keys,
        "new_keys": new_keys,
        "new_cells": [roster_b[k] for k in new_keys],
        "reuse_records": [by_key[k] for k in overlap_keys],
        "attestations": attestations,
        "provider_snapshot_sha256": providers.pop(),
    }


# Negative controls (§P0): each raises on violation, never warns-and-continues.
def neg_config_hash_tampered(expected: str, actual: str) -> None:
    require(expected == actual, f"model config hash drift: {actual} != {expected}")


def neg_future_universe_leak(signal_dates: List[str], universe_asof: str, quote_dates: List[str]) -> None:
    require(all(d <= universe_asof for d in signal_dates), "signal uses future index membership")
    require(all(d <= universe_asof for d in quote_dates), "quote union leaks future constituents into signal")


def neg_prediction_after_outcome(prediction_utc: str, first_outcome_utc: str) -> None:
    require(prediction_utc < first_outcome_utc, "prediction timestamp not strictly before outcome")


def neg_missing_open_factor(has_open: bool, has_factor: bool) -> None:
    require(has_open and has_factor, "missing execution open or factor bars")


def neg_delisted_missing_quote(is_delisted_holding: bool, quote_available: bool) -> None:
    require(not (is_delisted_holding and not quote_available), "delisted holding lost its quote")


def neg_halt_fake_sell(is_halted: bool, sold: bool) -> None:
    require(not (is_halted and sold), "halted stock must not report a fill")


def neg_zero_fee(bp: float) -> None:
    require(
        isinstance(bp, (int, float)) and math.isfinite(bp) and bp > 0, "fee schedule must be a positive finite number"
    )


def neg_cross_account(account_a: str, account_b: str) -> None:
    require(
        bool(account_a) and bool(account_b) and account_a != account_b,
        "challenger and baseline need independent paper accounts",
    )


def neg_duplicate_key(keys: List[str]) -> None:
    require(len(set(keys)) == len(keys), "duplicate cell key")


def neg_missing_baseline(baseline_present: bool) -> None:
    require(baseline_present, "baseline reference missing for paired comparison")


def neg_over_budget(estimated_usd: float, remaining_usd: float) -> None:
    for v, name in ((estimated_usd, "estimate"), (remaining_usd, "remaining")):
        require(isinstance(v, (int, float)) and math.isfinite(v), f"{name} cost is unknown or non-finite")
    require(estimated_usd <= remaining_usd, f"P90 estimate {estimated_usd} exceeds remaining {remaining_usd}")


def neg_nonfinite_spend(spend_usd: float) -> None:
    require(
        isinstance(spend_usd, (int, float)) and math.isfinite(spend_usd) and spend_usd >= 0, "spend must be finite >= 0"
    )


def neg_missing_nav(nav: Any) -> None:
    require(isinstance(nav, (int, float)) and math.isfinite(nav) and nav > 0, "NAV missing or non-finite")


@dataclass
class BudgetLedger:
    """Append-only real-cost ledger with fail-closed layered/global gates.

    The ledger persists to JSON so spend, fits and wall time accumulate across
    batches. CLI estimates are provisional ceiling gates only: real spend is
    recorded exclusively from operator billing reports via
    :meth:`record_actual`. A batch without recorded actuals stays
    ESTIMATED_PENDING and blocks the next batch's preflight.
    """

    spent_by_layer: Dict[str, float] = field(default_factory=lambda: {k: 0.0 for k in LAYER_CAPS_USD})
    fits_used: int = 0
    elapsed_hours: float = 0.0
    pending_estimate_usd: float = 0.0
    started_utc: str | None = None
    events: List[Dict[str, Any]] = field(default_factory=list)

    def remaining_layer(self, layer: str) -> float:
        require(layer in LAYER_CAPS_USD, f"unknown layer {layer}")
        return round(LAYER_CAPS_USD[layer] - self.spent_by_layer.get(layer, 0.0) - self._pending_for(layer), 6)

    def remaining_global(self) -> float:
        return round(GLOBAL_CAP_USD - sum(self.spent_by_layer.values()) - self.pending_estimate_usd, 6)

    def remaining_fits(self) -> int:
        return GLOBAL_FIT_CAP - self.fits_used

    def _pending_for(self, layer: str) -> float:
        return sum(
            float(ev.get("spend_usd", 0.0))
            for ev in self.events
            if ev.get("kind") == "estimate_pending" and ev.get("layer") == layer
        )

    @staticmethod
    def estimate_p90(
        samples: List[float],
        cpu_rate: float,
        mem_rate: float,
        cpu_seconds_p90: float,
        mem_gb_seconds_p90: float,
        overhead_usd: float = 0.0,
    ) -> float:
        """P90 batch-cost estimate from measured worker-seconds and real Modal rates.

        Unknown, NaN or infinite rates/samples raise STOP_COST_UNKNOWN immediately.
        """
        for v, name in (
            (cpu_rate, "cpu_rate"),
            (mem_rate, "mem_rate"),
            (cpu_seconds_p90, "cpu_seconds_p90"),
            (mem_gb_seconds_p90, "mem_gb_seconds_p90"),
            (overhead_usd, "overhead_usd"),
        ):
            if not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
                raise ValueError(f"STOP_COST_UNKNOWN: {name} is unknown or non-finite")
        if not samples:
            raise ValueError("STOP_COST_UNKNOWN: no measured worker-second samples for P90")
        if any(not isinstance(s, (int, float)) or not math.isfinite(s) or s < 0 for s in samples):
            raise ValueError("STOP_COST_UNKNOWN: worker-second samples contain unknown values")
        _ = statistics.quantiles(sorted(samples), n=10)[-1] if len(samples) >= 10 else max(samples)
        return round(
            float(cpu_seconds_p90) * float(cpu_rate)
            + float(mem_gb_seconds_p90) * float(mem_rate)
            + float(overhead_usd),
            6,
        )

    def preflight(self, layer: str, p90_estimate_usd: float, fits_needed: int = 0) -> str:
        """Batch-start gate. Unknown/non-finite cost, pending actuals or <20% reserve stops."""
        if (
            not isinstance(p90_estimate_usd, (int, float))
            or not math.isfinite(p90_estimate_usd)
            or p90_estimate_usd < 0
        ):
            return "STOP_COST_UNKNOWN"
        if self.pending_estimate_usd > 0:
            return "STOP_COST_UNKNOWN"
        if self.started_utc is not None:
            try:
                started = datetime.fromisoformat(self.started_utc.replace("Z", "+00:00"))
                if started.tzinfo is None or datetime.now(timezone.utc) - started > timedelta(days=WINDOW_DAYS):
                    return "STOP_BUDGET"
            except (ValueError, TypeError):
                return "STOP_COST_UNKNOWN"
        if p90_estimate_usd > self.remaining_global() * (1 - RESERVE_FRACTION):
            return "STOP_BUDGET"
        if p90_estimate_usd > self.remaining_layer(layer) or p90_estimate_usd > self.remaining_global():
            return "STOP_BUDGET"
        if self.fits_used + fits_needed > GLOBAL_FIT_CAP:
            return "STOP_BUDGET"
        if self.elapsed_hours >= TIME_CAP_HOURS:
            return "STOP_BUDGET"
        return "OK_CONTINUE"

    def record_estimate(self, layer: str, p90_estimate_usd: float, *, run_id: str, note: str = "") -> str:
        """Hold a provisional ceiling for a dispatched batch; never booked as spend."""
        neg_nonfinite_spend(p90_estimate_usd)
        require(layer in LAYER_CAPS_USD, f"unknown layer {layer}")
        if self.started_utc is None:
            self.started_utc = utcnow()
        self.pending_estimate_usd = round(self.pending_estimate_usd + p90_estimate_usd, 6)
        self.events.append(
            {
                "kind": "estimate_pending",
                "layer": layer,
                "spend_usd": p90_estimate_usd,
                "run_id": run_id,
                "utc": utcnow(),
                "note": note,
            }
        )
        return "OK_CONTINUE"

    def record_actual(
        self,
        layer: str,
        spend_usd: float,
        *,
        app_id: str,
        run_id: str,
        fits: int = 0,
        hours: float = 0.0,
        outcome: str = "success",
        note: str = "",
    ) -> str:
        """Book real billed spend (including failed/retry App spend). Clears one pending hold."""
        require(layer in LAYER_CAPS_USD, f"unknown layer {layer}")
        neg_nonfinite_spend(spend_usd)
        require(bool(app_id) and app_id not in ("pending", "null", "unknown"), "real App ID required for actuals")
        require(
            isinstance(fits, int)
            and fits >= 0
            and isinstance(hours, (int, float))
            and math.isfinite(hours)
            and hours >= 0,
            "fits/hours must be finite >= 0",
        )
        require(outcome in ("success", "failed", "retried", "blocked"), f"unknown outcome {outcome}")
        self.spent_by_layer[layer] = round(self.spent_by_layer.get(layer, 0.0) + spend_usd, 6)
        self.fits_used += fits
        self.elapsed_hours += hours
        cleared = 0.0
        for ev in self.events:
            if (
                ev.get("kind") == "estimate_pending"
                and ev.get("layer") == layer
                and ev.get("run_id") == run_id
                and cleared == 0.0
            ):
                ev["kind"] = "estimate_cleared"
                cleared = float(ev.get("spend_usd", 0.0))
        self.pending_estimate_usd = round(max(0.0, self.pending_estimate_usd - cleared), 6)
        self.events.append(
            {
                "kind": "actual",
                "layer": layer,
                "spend_usd": spend_usd,
                "fits": fits,
                "hours": hours,
                "app_id": app_id,
                "run_id": run_id,
                "outcome": outcome,
                "utc": utcnow(),
                "note": note,
            }
        )
        if (
            self.spent_by_layer[layer] > LAYER_CAPS_USD[layer] + 1e-9
            or sum(self.spent_by_layer.values()) > GLOBAL_CAP_USD + 1e-9
        ):
            return "STOP_BUDGET"
        if self.fits_used > GLOBAL_FIT_CAP or self.elapsed_hours > TIME_CAP_HOURS:
            return "STOP_BUDGET"
        return "OK_CONTINUE"

    # Compatibility shim: provisional hold, never booked as real spend.
    def record(self, layer: str, spend_usd: float, fits: int = 0, hours: float = 0.0, note: str = "") -> str:
        return self.record_estimate(
            layer, spend_usd, run_id=note or "legacy", note="legacy record() proxied to estimate hold"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "protocol": PROTOCOL_VERSION,
            "spent_by_layer": dict(self.spent_by_layer),
            "fits_used": self.fits_used,
            "elapsed_hours": self.elapsed_hours,
            "pending_estimate_usd": self.pending_estimate_usd,
            "started_utc": self.started_utc,
            "events": list(self.events),
        }

    def save(self, path: str | Path) -> Path:
        """Atomically persist the ledger (temp file + rename, never partial)."""
        out = Path(path).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(out.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(out)
        return out

    @classmethod
    def load(cls, path: str | Path) -> "BudgetLedger":
        """Load a persisted ledger; a missing file starts empty, a corrupt one blocks."""
        p = Path(path).expanduser()
        if not p.exists():
            return cls()
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"STOP_COST_UNKNOWN: corrupt ledger file: {exc}") from exc
        ledger = cls()
        spent = raw.get("spent_by_layer", {})
        for layer in LAYER_CAPS_USD:
            v = spent.get(layer, 0.0)
            neg_nonfinite_spend(v)
            ledger.spent_by_layer[layer] = float(v)
        ledger.fits_used = int(raw.get("fits_used", 0))
        ledger.elapsed_hours = float(raw.get("elapsed_hours", 0.0))
        ledger.pending_estimate_usd = float(raw.get("pending_estimate_usd", 0.0))
        ledger.events = list(raw.get("events", []))
        ledger.started_utc = raw.get("started_utc")
        require(
            ledger.fits_used >= 0
            and math.isfinite(ledger.elapsed_hours)
            and ledger.elapsed_hours >= 0
            and math.isfinite(ledger.pending_estimate_usd)
            and ledger.pending_estimate_usd >= 0,
            "ledger fits/hours/pending invalid",
        )
        actual_pending = sum(
            float(ev.get("spend_usd", 0.0)) for ev in ledger.events if ev.get("kind") == "estimate_pending"
        )
        require(
            math.isclose(actual_pending, ledger.pending_estimate_usd, abs_tol=1e-6),
            "ledger pending reserve differs from immutable events",
        )
        return ledger


def _paired_deltas(
    challenger: List[Dict[str, Any]], baseline: List[Dict[str, Any]], n_expected: int
) -> Tuple[str, Dict[str, Any], List[float]]:
    """Pair challenger vs baseline rows phase-by-phase; all deltas are chall minus base."""
    detail: Dict[str, Any] = {}
    if len(challenger) != n_expected or len(baseline) != n_expected:
        return "INCOMPLETE", {**detail, "reason": f"need exactly {n_expected} paired phases per leg"}, []
    for leg_name, leg in (("challenger", challenger), ("baseline", baseline)):
        for row in leg:
            missing = [k for k in REQUIRED_PHASE_FIELDS if k not in row]
            if missing:
                return "NOT_ELIGIBLE", {**detail, "reason": f"{leg_name} missing fields {missing}"}, []
    base_by_phase = {r["phase"]: r for r in baseline}
    chall_by_phase = {r["phase"]: r for r in challenger}
    expected_phases = {0, 10} if n_expected == 2 else [{0, 4, 6, 10, 15}, {0, 2, 3, 5, 7}, {0, 8, 12, 20, 30}]
    if len(base_by_phase) != n_expected or len(chall_by_phase) != n_expected:
        return "NOT_ELIGIBLE", {**detail, "reason": "duplicate phase in candidate or baseline"}, []
    if set(base_by_phase) != set(chall_by_phase):
        return "NOT_ELIGIBLE", {**detail, "reason": "phase sets differ between legs"}, []
    if n_expected == 2 and set(base_by_phase) != expected_phases:
        return "NOT_ELIGIBLE", {**detail, "reason": "wrong pre-registered screen phases"}, []
    if n_expected == 5 and set(base_by_phase) not in expected_phases:
        return "NOT_ELIGIBLE", {**detail, "reason": "wrong pre-registered final phases"}, []
    for leg in (challenger, baseline):
        for row in leg:
            for flag in ("coverage_ok", "ledger_ok", "data_ok"):
                if row.get(flag) is not True:
                    return (
                        "BASELINE_INELIGIBLE" if leg is baseline else "NOT_ELIGIBLE",
                        {**detail, "reason": f"{'baseline' if leg is baseline else 'challenger'} {flag} not true"},
                        [],
                    )
            for num in ("net_relative_cagr_pp", "net_total_return", "mdd_pp", "plus10bp_advantage_pp"):
                if not isinstance(row.get(num), (int, float)) or not math.isfinite(row[num]):
                    return "NOT_ELIGIBLE", {**detail, "reason": f"non-finite {num}"}, []
            if not isinstance(row.get("n_days"), int) or row["n_days"] != 424:
                return "NOT_ELIGIBLE", {**detail, "reason": "phase must have exactly 424 sessions"}, []
            if row["mdd_pp"] < 0:
                return "NOT_ELIGIBLE", {**detail, "reason": "mdd_pp must be a positive magnitude"}, []
            turnover = row["turnover"]
            if not isinstance(turnover, (int, float)) or not math.isfinite(turnover) or turnover < 0:
                return (
                    ("BASELINE_INELIGIBLE" if leg is baseline else "NOT_ELIGIBLE"),
                    {**detail, "reason": "phase turnover nonfinite or negative"},
                    [],
                )
            if leg is baseline and turnover <= 0:
                return "BASELINE_INELIGIBLE", {**detail, "reason": "baseline turnover must be positive"}, []
    for phase in base_by_phase:
        if chall_by_phase[phase]["n_days"] != base_by_phase[phase]["n_days"]:
            return "NOT_ELIGIBLE", {**detail, "reason": f"phase {phase} day-count mismatch between legs"}, []
    deltas = [
        chall_by_phase[p]["net_relative_cagr_pp"] - base_by_phase[p]["net_relative_cagr_pp"]
        for p in sorted(base_by_phase)
    ]
    return "OK", {}, deltas


def coarse_pass(challenger: List[Dict[str, Any]], baseline: List[Dict[str, Any]]) -> Tuple[bool, Dict[str, Any]]:
    """L1 two-phase coarse screen on paired improvements (never absolute levels).

    Both phases need worst and median paired improvement >= +0.5pp, MDD
    degradation <= 5pp and sane turnover. Returns (passed, detail).
    """
    code, detail, deltas = _paired_deltas(challenger, baseline, 2)
    if code != "OK":
        return False, {"screen": "coarse", "gate": code, **detail}
    base_by_phase = {r["phase"]: r for r in baseline}
    chall_by_phase = {r["phase"]: r for r in challenger}
    worst = min(deltas)
    median = statistics.median(deltas)
    mdd_deg = max(r["mdd_pp"] for r in challenger) - max(r["mdd_pp"] for r in baseline)
    turnovers = []
    for p in sorted(base_by_phase):
        base_t = base_by_phase[p].get("turnover")
        chall_t = chall_by_phase[p].get("turnover")
        if base_t is None or chall_t is None:
            return False, {"screen": "coarse", "gate": "NOT_ELIGIBLE", "reason": "turnover missing"}
        if not (isinstance(base_t, (int, float)) and math.isfinite(base_t) and base_t > 0):
            return False, {"screen": "coarse", "gate": "NOT_ELIGIBLE", "reason": "baseline turnover invalid"}
        turnovers.append(chall_t / base_t)
    turnover_ratio = max(turnovers)
    info = {
        "worst_delta_pp": worst,
        "median_delta_pp": median,
        "mdd_degrade_pp": mdd_deg,
        "turnover_ratio": turnover_ratio,
    }
    if worst < COARSE_MIN_PP or median < COARSE_MIN_PP:
        return False, {**info, "failed": "improvement"}
    if mdd_deg > COARSE_MDD_MAX_DEGRADE_PP:
        return False, {**info, "failed": "mdd"}
    if not math.isfinite(turnover_ratio) or turnover_ratio > COARSE_TURNOVER_MAX_RATIO:
        return False, {**info, "failed": "turnover"}
    return True, info


def final_verdict(
    challenger: List[Dict[str, Any]], baseline: List[Dict[str, Any]], turnover_ratio: float
) -> Tuple[str, Dict[str, Any]]:
    """Apply the seven §1.2 product gates over five paired phases.

    All return comparisons are challenger-minus-baseline improvements. A
    baseline leg that fails data/ledger/coverage yields BASELINE_INELIGIBLE so
    the caller can escalate to STOP_PRODUCT_BLOCKED instead of crowning a
    challenger against a broken reference.
    """
    code, detail, deltas = _paired_deltas(challenger, baseline, 5)
    if code != "OK":
        return code, {**detail, "n_phases": len(challenger)}
    base_by_phase = {r["phase"]: r for r in baseline}
    chall_by_phase = {r["phase"]: r for r in challenger}
    worst = min(deltas)
    median = sorted(deltas)[2]
    n_beat = sum(
        1 for p in base_by_phase if chall_by_phase[p]["net_total_return"] > base_by_phase[p]["net_total_return"]
    )
    mdd_deg = max(r["mdd_pp"] for r in challenger) - max(r["mdd_pp"] for r in baseline)
    # Contract 53 §1.2: median of five phase turnovers for each leg,
    # rather than the maximum of five phase-by-phase ratios.
    baseline_median_turnover = statistics.median(r["turnover"] for r in baseline)
    challenger_median_turnover = statistics.median(r["turnover"] for r in challenger)
    actual_turnover_ratio = challenger_median_turnover / baseline_median_turnover
    stress = all(chall_by_phase[p]["plus10bp_advantage_pp"] > 0 for p in base_by_phase)
    info = {
        "n_phases": 5,
        "worst_delta_pp": worst,
        "median_delta_pp": median,
        "n_beat": n_beat,
        "mdd_degrade_pp": mdd_deg,
        "stress10_ok": stress,
        "turnover_ratio": actual_turnover_ratio,
        "baseline_median_turnover": baseline_median_turnover,
        "challenger_median_turnover": challenger_median_turnover,
    }
    if (
        not isinstance(turnover_ratio, (int, float))
        or not math.isfinite(turnover_ratio)
        or not math.isclose(turnover_ratio, actual_turnover_ratio, rel_tol=1e-10, abs_tol=1e-10)
    ):
        return "NOT_ELIGIBLE", {
            **info,
            "failed": "turnover",
            "reason": "claimed turnover ratio differs from independently recomputed five-phase medians",
        }
    if worst < FINAL_WORST_MIN_PP:
        return "NOT_ELIGIBLE", {**info, "failed": "worst_phase"}
    if median < FINAL_MEDIAN_MIN_PP:
        return "NOT_ELIGIBLE", {**info, "failed": "median"}
    if n_beat < FINAL_MIN_PHASES_BEAT:
        return "NOT_ELIGIBLE", {**info, "failed": "breadth"}
    if mdd_deg > FINAL_MDD_MAX_DEGRADE_PP:
        return "NOT_ELIGIBLE", {**info, "failed": "mdd"}
    if actual_turnover_ratio > FINAL_TURNOVER_MAX_RATIO:
        return "NOT_ELIGIBLE", {**info, "failed": "turnover"}
    if not stress:
        return "NOT_ELIGIBLE", {**info, "failed": "cost_stress_10bp"}
    return "PASS", info


def rank_challengers(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort by worst-phase improvement, then median, risk, turnover, config-SHA tiebreak."""
    require(len(rows) > 0, "nothing to rank")

    def key(r: Dict[str, Any]) -> tuple:
        return (-r["worst_delta_pp"], -r["median_delta_pp"], r["mdd_degrade_pp"], r["turnover_ratio"], r["config_sha"])

    return sorted(rows, key=key)


def decide_product(finalists: List[Dict[str, Any]], baseline_eligible: bool = True) -> Tuple[str, Dict[str, Any]]:
    """Global decision with recomputed verdicts; embedded PASS claims are ignored.

    Each finalist must carry ``challenger_phases``, ``baseline_phases`` and
    ``turnover_ratio`` so every verdict is recomputed here. With zero passing
    challengers the decision is KEEP_BASELINE_FOR_PRODUCT_SELECTION, or
    STOP_PRODUCT_BLOCKED when the baseline reference itself is ineligible.
    """
    scored = []
    for f in finalists:
        verdict, info = final_verdict(
            f.get("challenger_phases", []), f.get("baseline_phases", []), f.get("turnover_ratio")
        )
        if verdict == "PASS":
            scored.append({"config_sha": f.get("config_sha", ""), **info})
        elif verdict == "BASELINE_INELIGIBLE":
            return "STOP_PRODUCT_BLOCKED", {"reason": "baseline reference ineligible"}
    if not scored:
        if not baseline_eligible:
            return "STOP_PRODUCT_BLOCKED", {"n_finalists": len(finalists), "n_pass": 0}
        return "KEEP_BASELINE_FOR_PRODUCT_SELECTION", {"n_finalists": len(finalists), "n_pass": 0}
    ordered = rank_challengers(scored)
    return "PROPOSE_PRODUCT_PROFILE_VERSION", {"winner": ordered[0], "n_pass": len(scored)}


def build_protocol(run_id: str, approved_by: str = "PENDING_BUDGET_APPROVAL") -> Dict[str, Any]:
    """Machine-readable protocol.json content (frozen before any L4 final)."""
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{3,63}", run_id) is not None, "invalid run-id")
    check_layer_totals()
    return {
        "protocol": PROTOCOL_VERSION,
        "contract_doc": CONTRACT_DOC,
        "contract_pr": CONTRACT_PR,
        "run_id": run_id,
        "frozen": {
            "baseline_candidate": BASELINE_CANDIDATE,
            "winner_candidate": WINNER_CANDIDATE,
            "baseline_config_sha": BASELINE_CONFIG_SHA,
            "winner_config_sha": WINNER_CONFIG_SHA,
            "stage_b_snapshot": STAGE_B_SNAPSHOT_TOKEN,
            "phases_freq20": PHASES_FIVE_FREQ20,
            "screen_phases": PHASES_SCREEN_TWO,
            "portfolio_grid": [{"topk": k, "dropn": d} for k, d in L1_PORTFOLIO_GRID],
            "fees": {"buy_bp": FEE_BUY_BP, "sell_bp": FEE_SELL_BP, "min_cny": FEE_MIN_CNY},
            "execution": EXECUTION_SCHEMA,
            "capital_cny": INITIAL_CASH_CNY,
            "seed_l2": RESEARCH_SEED_L2,
        },
        "budgets": {"layers_usd": dict(LAYER_CAPS_USD), "global_usd": GLOBAL_CAP_USD, "fits": GLOBAL_FIT_CAP},
        "thresholds": {
            "final_worst_pp": FINAL_WORST_MIN_PP,
            "final_median_pp": FINAL_MEDIAN_MIN_PP,
            "final_phases_beat": FINAL_MIN_PHASES_BEAT,
            "final_mdd_pp": FINAL_MDD_MAX_DEGRADE_PP,
            "final_turnover": FINAL_TURNOVER_MAX_RATIO,
        },
        "approval": {"approved_by": approved_by, "training_performed": False, "promotion_authorized": False},
    }


def append_trial_ledger(path: str | Path, rows: List[Dict[str, Any]]) -> Path:
    """Append trial rows keyed by (run_id, cell_key); same-run duplicates refused.

    A re-execution under a new run-id is a distinct row; cross-run reuse is
    referenced by attestations, never by silently reusing rows.
    """
    out = Path(path)
    existing: List[Dict[str, Any]] = []
    if out.exists():
        existing = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
    keys = [trial_row_key(r.get("run_id"), r.get("cell_key")) for r in existing + rows]
    require(len(set(keys)) == len(keys), "duplicate (run_id, cell_key) in trial ledger")
    for r in rows:
        require(r.get("cell_key"), "trial row missing cell_key")
        require(r.get("app_id") and r["app_id"] not in ("pending", "null", "unknown"), "trial row needs a real App ID")
        require(r.get("status") in ("PASS", "BLOCKED", "RESUME_VERIFIED"), "unknown trial status")
        neg_nonfinite_spend(float(r.get("spend_usd", 0.0)))
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({**r, "utc": r.get("utc", utcnow())}, sort_keys=True) + "\n")
    return out


def write_sha256sums(root: str | Path) -> Path:
    """Write SHA256SUMS.txt over every file under root except itself."""
    root = Path(root)
    lines = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.name != "SHA256SUMS.txt":
            lines.append(f"{sha256_file(p)}  {p.relative_to(root).as_posix()}\n")
    (root / "SHA256SUMS.txt").write_text("".join(lines), encoding="utf-8")
    return root / "SHA256SUMS.txt"


def build_cli() -> argparse.ArgumentParser:
    """CLI for local inventory/dry-run. Real Modal execution uses the modal runner."""
    p = argparse.ArgumentParser(
        description="PR-S0 layered-search core: inventory, budget and decision gates (no Modal, no training)."
    )
    p.add_argument(
        "--inventory", action="store_true", help="Print frozen cell counts and budget caps without running anything."
    )
    p.add_argument(
        "--dry-run", action="store_true", help="Validate enumeration, thresholds and STOP gates on synthetic inputs."
    )
    p.add_argument(
        "--check-frozen", action="store_true", help="Re-read frozen profiles from csi1000_production_config.py."
    )
    p.add_argument("--repo-root", default=".", help="Repository root for --check-frozen.")
    p.add_argument("--book-actual", action="store_true", help="Book billed USD after an attached Modal batch.")
    p.add_argument("--ledger", default="~/.local/state/qlib-csi1000-layered/cost_ledger.json")
    p.add_argument("--layer", choices=tuple(LAYER_CAPS_USD), default="p0")
    p.add_argument("--run-id", default="")
    p.add_argument("--app-id", default="")
    p.add_argument("--spent-usd", type=float, default=float("nan"))
    p.add_argument("--elapsed-hours", type=float, default=float("nan"))
    p.add_argument("--fits", type=int, default=0)
    p.add_argument("--billing-report", default="", help="Saved operator billing report evidence (never credentials).")
    return p


def main(argv: List[str] | None = None) -> int:
    """CLI entry point.

    Parameters
    ----------
    argv : list of str or None
        Argument list; None uses sys.argv.

    Returns
    -------
    int
        Process exit code.
    """
    args = build_cli().parse_args(argv)
    if args.book_actual:
        require(
            args.billing_report and Path(args.billing_report).is_file(),
            "booking requires an actual saved Modal billing report",
        )
        require(args.run_id and args.app_id, "booking requires run-id and real App ID")
        require(
            args.elapsed_hours is not None and math.isfinite(args.elapsed_hours) and args.elapsed_hours >= 0,
            "elapsed hours unknown",
        )
        ledger = BudgetLedger.load(args.ledger)
        status = ledger.record_actual(
            args.layer,
            args.spent_usd,
            app_id=args.app_id,
            run_id=args.run_id,
            fits=args.fits,
            hours=args.elapsed_hours,
            note=f"operator billing report sha256={sha256_file(args.billing_report)}",
        )
        ledger.save(args.ledger)
        ledger_dir = Path(args.ledger).expanduser().parent
        pending_file = ledger_dir / "trial_events_pending.jsonl"
        final_file = ledger_dir / "trial_ledger.jsonl"
        booked = 0
        if pending_file.exists():
            event_rows = [
                json.loads(line) for line in pending_file.read_text(encoding="utf-8").splitlines() if line.strip()
            ]
            existing_keys = set()
            if final_file.exists():
                existing_keys = {
                    json.loads(line)["cell_key"]
                    for line in final_file.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                }
            new_rows = []
            for event in event_rows:
                if event.get("run_id") != args.run_id or event.get("cell_key") in existing_keys:
                    continue
                new_rows.append(
                    {
                        "cell_key": event["cell_key"],
                        "run_id": args.run_id,
                        "stage": event.get("stage"),
                        "status": event["status"],
                        "app_id": args.app_id,
                        "spend_usd": 0.0,
                        "per_cell_spend_unallocated": True,
                        "billed_app_total_usd": args.spent_usd,
                        "billing_report_sha256": sha256_file(args.billing_report),
                    }
                )
                existing_keys.add(event["cell_key"])
            if new_rows:
                append_trial_ledger(final_file, new_rows)
                booked = len(new_rows)
        print(
            json.dumps(
                {
                    "booked_final_trial_rows": booked,
                    "status": status,
                    "ledger": str(Path(args.ledger).expanduser()),
                    "remaining_global_usd": ledger.remaining_global(),
                },
                indent=2,
            )
        )
        return 0 if status == "OK_CONTINUE" else 3
    if args.inventory:
        check_layer_totals()
        print(
            json.dumps(
                {"protocol": PROTOCOL_VERSION, "budgets": LAYER_CAPS_USD, "global_usd": GLOBAL_CAP_USD}, indent=2
            )
        )
        for stage in ("p0", "l1-a", "l1-b", "l1-c"):
            print(f"{stage}: {len(enumerate_cells(stage))} cells")
        return 0
    if args.check_frozen:
        print(json.dumps(load_frozen_profiles(args.repo_root), indent=2))
        return 0
    if args.dry_run:
        check_layer_totals()
        ledger = BudgetLedger()
        assert ledger.preflight("p0", 1.0) == "OK_CONTINUE"
        assert ledger.preflight("p0", 99.0) == "STOP_BUDGET"
        assert ledger.preflight("p0", float("nan")) == "STOP_COST_UNKNOWN"
        assert len(enumerate_cells("p0")) == 4
        print("DRY_RUN_OK")
        return 0
    build_cli().print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
