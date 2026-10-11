"""CSI1000 budgeted layered strategy search core (PR-S0).

Pure-Python search configuration, budget ledger, SHA cache keys and
product-decision rules for contract 53. No Modal, no Qlib, no training.

Status: NOT EXECUTED. This module only defines the frozen protocol and
fail-closed gates. Any real Modal spend requires a separate explicit USD30
approval and a foreground operator run. Merging this code does not approve
spend, does not run a search, and does not change production.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass, field
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
    signal_key: str, topk: int, dropn: int, quote_sha: str, engine_version: str = PROTOCOL_VERSION
) -> str:
    """Replay-level cache key: same frozen signal, different portfolio combo."""
    require(dropn <= topk, "DropN must be <= TopK")
    return stable_sha(
        {
            "signal_key": signal_key,
            "topk": topk,
            "dropn": dropn,
            "quote_sha": quote_sha,
            "fees": [FEE_BUY_BP, FEE_SELL_BP, FEE_MIN_CNY],
            "execution": EXECUTION_SCHEMA,
            "engine": engine_version,
        }
    )


def check_cache_hit(cached_record: Dict[str, Any], current_sources: Dict[str, Any]) -> str:
    """Strict SHA+version resume: mtime or filename alone never counts as a hit."""
    for k in ("source_sha256", "output_sha256", "cache_key"):
        require(k in cached_record, f"cached record missing {k}")
    for k in ("source_sha256", "cache_key"):
        require(k in current_sources, f"current sources missing {k}")
    if current_sources["cache_key"] != cached_record["cache_key"]:
        return "FAIL_CONFLICT"
    if current_sources["source_sha256"] != cached_record["source_sha256"]:
        return "FAIL_CONFLICT"
    if cached_record.get("output_sha256") in (None, ""):
        return "FAIL_CONFLICT"
    return "RESUME_VERIFIED"


def check_output_not_in_source(source_root: str | Path, output: str | Path) -> Path:
    """Fail closed if a research output path lands inside immutable inputs."""
    src = Path(source_root).resolve()
    out = Path(output).resolve()
    require(out != src and not out.is_relative_to(src), "output inside immutable source root")
    require(out != Path("/vol") and not str(out).startswith("/vol/"), "output inside original /vol source mount")
    return out


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
    require(bp > 0, "fee schedule must be positive")


def neg_cross_account(account_a: str, account_b: str) -> None:
    require(account_a != account_b, "challenger and baseline must use independent paper accounts")


def neg_duplicate_key(keys: List[str]) -> None:
    require(len(set(keys)) == len(keys), "duplicate cell key")


def neg_missing_baseline(baseline_present: bool) -> None:
    require(baseline_present, "baseline reference missing for paired comparison")


def neg_over_budget(estimated_usd: float, remaining_usd: float) -> None:
    require(estimated_usd <= remaining_usd, f"P90 estimate {estimated_usd} exceeds remaining {remaining_usd}")


@dataclass
class BudgetLedger:
    """Append-only real-cost ledger with fail-closed layered/global gates."""

    spent_by_layer: Dict[str, float] = field(default_factory=lambda: {k: 0.0 for k in LAYER_CAPS_USD})
    fits_used: int = 0
    elapsed_hours: float = 0.0
    events: List[Dict[str, Any]] = field(default_factory=list)

    def remaining_layer(self, layer: str) -> float:
        require(layer in LAYER_CAPS_USD, f"unknown layer {layer}")
        return round(LAYER_CAPS_USD[layer] - self.spent_by_layer.get(layer, 0.0), 6)

    def remaining_global(self) -> float:
        return round(GLOBAL_CAP_USD - sum(self.spent_by_layer.values()), 6)

    def remaining_fits(self) -> int:
        return GLOBAL_FIT_CAP - self.fits_used

    def preflight(self, layer: str, p90_estimate_usd: float, fits_needed: int = 0) -> str:
        """Batch-start gate. Unknown cost or <20% reserve for in-flight work stops."""
        if p90_estimate_usd is None or not isinstance(p90_estimate_usd, (int, float)) or p90_estimate_usd < 0:
            return "STOP_COST_UNKNOWN"
        reserve = RESERVE_FRACTION * GLOBAL_CAP_USD
        if self.remaining_global() < reserve and (p90_estimate_usd > 0 or fits_needed > 0):
            return "STOP_COST_UNKNOWN"
        if p90_estimate_usd > self.remaining_layer(layer) or p90_estimate_usd > self.remaining_global():
            return "STOP_BUDGET"
        if self.fits_used + fits_needed > GLOBAL_FIT_CAP:
            return "STOP_BUDGET"
        if self.elapsed_hours >= TIME_CAP_HOURS:
            return "STOP_BUDGET"
        return "OK_CONTINUE"

    def record(self, layer: str, spend_usd: float, fits: int = 0, hours: float = 0.0, note: str = "") -> str:
        require(layer in LAYER_CAPS_USD, f"unknown layer {layer}")
        require(spend_usd >= 0 and fits >= 0 and hours >= 0, "negative ledger entry")
        self.spent_by_layer[layer] = round(self.spent_by_layer.get(layer, 0.0) + spend_usd, 6)
        self.fits_used += fits
        self.elapsed_hours += hours
        self.events.append({"layer": layer, "spend_usd": spend_usd, "fits": fits, "hours": hours, "note": note})
        if (
            self.spent_by_layer[layer] > LAYER_CAPS_USD[layer] + 1e-9
            or sum(self.spent_by_layer.values()) > GLOBAL_CAP_USD + 1e-9
        ):
            return "STOP_BUDGET"
        if self.fits_used > GLOBAL_FIT_CAP or self.elapsed_hours > TIME_CAP_HOURS:
            return "STOP_BUDGET"
        return "OK_CONTINUE"


def coarse_pass(two_phase: List[Dict[str, float]]) -> bool:
    """L1 two-phase coarse screen: both phases need +0.5pp worst+median, sane risk."""
    require(len(two_phase) == 2, "coarse screen needs exactly two phases")
    for row in two_phase:
        if row.get("coverage_ok") is False or row.get("ledger_ok") is False:
            return False
        if row.get("worst_delta_pp", -1e9) < COARSE_MIN_PP or row.get("median_delta_pp", -1e9) < COARSE_MIN_PP:
            return False
        if row.get("mdd_degrade_pp", 1e9) > COARSE_MDD_MAX_DEGRADE_PP:
            return False
    return True


def final_verdict(phases: List[Dict[str, Any]], turnover_ratio: float) -> Tuple[str, Dict[str, Any]]:
    """Apply the seven §1.2 product gates over five paired phases."""
    detail: Dict[str, Any] = {"n_phases": len(phases)}
    if len(phases) != 5:
        return "INCOMPLETE", {**detail, "reason": "need exactly five phases"}
    for row in phases:
        if row.get("coverage_ok") is False or row.get("ledger_ok") is False or row.get("data_ok") is False:
            return "NOT_ELIGIBLE", {**detail, "reason": "data/ledger/coverage gate failed"}
    worst = min(r["net_relative_cagr_pp"] for r in phases)
    median = sorted(r["net_relative_cagr_pp"] for r in phases)[2]
    n_beat = sum(1 for r in phases if r["net_total_return"] > r["baseline_net_total_return"])
    mdd_deg = max(r["mdd_pp"] for r in phases) - min(r["baseline_mdd_pp"] for r in phases)
    stress = all(r.get("plus10bp_advantage_pp", -1e9) > 0 for r in phases)
    detail.update(
        {"worst_pp": worst, "median_pp": median, "n_beat": n_beat, "mdd_degrade_pp": mdd_deg, "stress10_ok": stress}
    )
    if worst < FINAL_WORST_MIN_PP:
        return "NOT_ELIGIBLE", {**detail, "failed": "worst_phase"}
    if median < FINAL_MEDIAN_MIN_PP:
        return "NOT_ELIGIBLE", {**detail, "failed": "median"}
    if n_beat < FINAL_MIN_PHASES_BEAT:
        return "NOT_ELIGIBLE", {**detail, "failed": "breadth"}
    if mdd_deg > FINAL_MDD_MAX_DEGRADE_PP:
        return "NOT_ELIGIBLE", {**detail, "failed": "mdd"}
    if turnover_ratio > FINAL_TURNOVER_MAX_RATIO:
        return "NOT_ELIGIBLE", {**detail, "failed": "turnover"}
    if not stress:
        return "NOT_ELIGIBLE", {**detail, "failed": "cost_stress_10bp"}
    return "PASS", detail


def rank_challengers(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort by worst-phase improvement, then median, risk, turnover, config-SHA tiebreak."""
    require(len(rows) > 0, "nothing to rank")

    def key(r: Dict[str, Any]) -> tuple:
        return (-r["worst_pp"], -r["median_pp"], r["mdd_degrade_pp"], r["turnover_ratio"], r["config_sha"])

    return sorted(rows, key=key)


def decide_product(finalists: List[Dict[str, Any]]) -> Tuple[str, Dict[str, Any]]:
    """Global decision: unique PASS winner or KEEP_BASELINE_FOR_PRODUCT_SELECTION."""
    passes = [f for f in finalists if f.get("verdict") == "PASS"]
    if not passes:
        return "KEEP_BASELINE_FOR_PRODUCT_SELECTION", {"n_finalists": len(finalists), "n_pass": 0}
    ordered = rank_challengers(passes)
    return "PROPOSE_PRODUCT_PROFILE_VERSION", {"winner": ordered[0], "n_pass": len(passes)}


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
        assert len(enumerate_cells("p0")) == 4
        print("DRY_RUN_OK")
        return 0
    build_cli().print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
