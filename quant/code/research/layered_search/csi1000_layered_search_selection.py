"""Offline selection and bounded proposal planning for CSI1000 contract 53.

This module never trains, opens a Modal app, writes production config, or treats
historical data as OOS. Reuses frozen Stage-A identities and the new paired
P0/L1 audit arithmetic. L2/L3 plans are NOT training results: a separately
reviewed, actual-fit runner must attach reproducible training lineages.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from audit.csi1000_layered_search_core import (
    BASELINE_CANDIDATE,
    L1B_6_MODELS,
    L1_PORTFOLIO_GRID,
    STAGE_B_11_MODELS,
    RESEARCH_SEED_L2,
    coarse_pass,
    decide_product,
    enumerate_cells,
    final_verdict,
    rank_challengers,
    require,
    stable_sha,
)
from audit.csi1000_layered_search_replay import paired_rows, report_metrics, sha_bytes

FROZEN_STAGE_A = "quant/data/round1_stage_ab/csi1000_tuner/stage_a_expanded_a2ecd8d1cadf4046.json"
FROZEN_STAGE_B = "quant/data/round1_stage_ab/csi1000_stage_b/stage_b_full_51756897fc752304.json"
NUMERIC_FEATURES = (
    "learning_rate",
    "colsample_bytree",
    "lambda_l1",
    "lambda_l2",
    "max_depth",
    "num_leaves",
    "min_data_in_leaf",
)


def load_json(path: str | Path) -> dict:
    with Path(path).open(encoding="utf-8") as stream:
        obj = json.load(stream)
    require(isinstance(obj, dict), f"expected JSON mapping: {path}")
    return obj


def stage_a_proposals(root: str | Path = ".", seed: int = RESEARCH_SEED_L2) -> dict:
    """Return up to 4 diverse unused Stage-A candidates and 4 new bounded specs.

    The original Stage-A fold summaries may be used for deterministic *proposal*,
    never misrepresented as 424-session Stage-B results.
    """
    from csi1000_tuner_core import build_candidate_spec

    root = Path(root)
    stage_a = load_json(root / FROZEN_STAGE_A)
    stage_b = load_json(root / FROZEN_STAGE_B)
    require(stage_a.get("candidate_count") == 80, "Stage-A full roster metadata drift")
    require(len(stage_a.get("ranked_candidates", [])) == 80, "Stage-A roster is not the frozen 80")
    old_ids = {c["candidate_id"] for c in stage_b.get("ranked_candidates", [])}
    require(len(old_ids) == 11 and BASELINE_CANDIDATE in old_ids, "Stage-B roster is not the frozen 11")
    candidates = [r for r in stage_a["ranked_candidates"] if r["candidate_id"] not in old_ids]
    require(len(candidates) == 69, "expected exactly 69 unused Stage-A candidates")
    # Use numeric distance with per-field scaling and logarithmic regularization.
    domains: dict[str, tuple[float, float]] = {
        k: (
            min(float(row["candidate"]["model_params"][k]) for row in stage_a["ranked_candidates"]),
            max(float(row["candidate"]["model_params"][k]) for row in stage_a["ranked_candidates"]),
        )
        for k in NUMERIC_FEATURES
    }

    def vector(params: dict) -> tuple[float, ...]:
        out = []
        for k in NUMERIC_FEATURES:
            v = float(params[k])
            lo, hi = domains[k]
            if k in ("lambda_l1", "lambda_l2"):
                v, lo, hi = math.log1p(v), math.log1p(lo), math.log1p(hi)
            out.append((v - lo) / (hi - lo) if hi > lo else 0.0)
        return tuple(out)

    def distance(a: tuple[float, ...], b: tuple[float, ...]) -> float:
        return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))

    chosen: list[dict] = []
    # Fold robustness first; then farthest-first diversification, with stable
    # candidate-ID ties. Never use corrected Stage-B returns in ranking.
    candidates.sort(
        key=lambda r: (
            -float(r["fold_summary"]["relative_excess_cagr_worst"]),
            -float(r["fold_summary"]["relative_excess_cagr_q25"]),
            -float(r["fold_summary"]["relative_excess_cagr_median"]),
            r["candidate_id"],
        )
    )
    chosen.append(candidates[0])
    while len(chosen) < 4:
        pool = [r for r in candidates if r["candidate_id"] not in {v["candidate_id"] for v in chosen}]
        pool.sort(
            key=lambda r: (
                -min(
                    distance(vector(r["candidate"]["model_params"]), vector(v["candidate"]["model_params"]))
                    for v in chosen
                ),
                int(r["rank"]),
                r["candidate_id"],
            )
        )
        chosen.append(pool[0])

    # Four deterministic, in-domain, distinct new parameter proposals.
    rng = random.Random(seed)
    seed_params = [dict(r["candidate"]["model_params"]) for r in chosen]
    widths = {
        "learning_rate": (0.04, 0.06, 0.08, 0.10, 0.12),
        "colsample_bytree": (0.6, 0.7, 0.8, 0.9, 1.0),
        "lambda_l1": (0.0, 10.0, 100.0, 300.0),
        "lambda_l2": (0.0, 100.0, 400.0, 700.0),
        "max_depth": (4, 6, 8),
        "num_leaves": (15, 31, 63, 127),
        "min_data_in_leaf": (20, 50, 100, 150),
    }
    seen = {r["candidate_id"] for r in stage_a["ranked_candidates"]}
    proposed = []
    attempts = 0
    while len(proposed) < 4 and attempts < 10000:
        attempts += 1
        i = len(proposed)
        base = dict(seed_params[i])
        for k in NUMERIC_FEATURES:
            if rng.random() < (0.50 if k in ("lambda_l1", "lambda_l2", "max_depth") else 0.30):
                base[k] = rng.choice(widths[k])
        if int(base["num_leaves"]) > 2 ** int(base["max_depth"]):
            continue
        spec = build_candidate_spec(base)
        if spec["candidate_id"] in seen:
            continue
        seen.add(spec["candidate_id"])
        proposed.append(
            {
                "candidate_id": spec["candidate_id"],
                "model_params": spec["model_params"],
                "source": "DETERMINISTIC_NEW_PROPOSAL_NOT_TRAINED",
            }
        )
    require(len(proposed) == 4, "cannot produce four unique bounded proposals")
    return {
        "stage": "l2",
        "status": "PLAN_ONLY_NOT_EXECUTED",
        "seed": seed,
        "historic_data": "HISTORICALLY_CONSUMED",
        "original_stage_a_source_sha256": sha_bytes(root / FROZEN_STAGE_A),
        "original_stage_b_source_sha256": sha_bytes(root / FROZEN_STAGE_B),
        "four_existing": [
            {
                "candidate_id": r["candidate_id"],
                "model_params": r["candidate"]["model_params"],
                "stage_a_rank": r["rank"],
                "fold_summary": r["fold_summary"],
            }
            for r in chosen
        ],
        "four_new": proposed,
        "stage_a_short_fold_fit_cap": 16,
        "l2_long_fit_cap": 320,
        "allowed_long_validation_challengers": 6,
    }


def l3_structure_plan(anchor: dict, freq_screen_qualified: bool = False) -> dict:
    """Pre-register structural lineages; label experiments require separate PIT verification."""
    require(set(("candidate_id", "topk", "dropn")).issubset(anchor), "L3 anchor profile incomplete")
    variants = [
        {"label_horizon": 20, "retrain_frequency": 10, "screen_phases": [0, 5]},
        {"label_horizon": 20, "retrain_frequency": 40, "screen_phases": [0, 20]},
    ]
    if freq_screen_qualified:
        variants.extend(
            [
                {"label_horizon": 10, "retrain_frequency": 20, "screen_phases": [0, 10]},
                {"label_horizon": 40, "retrain_frequency": 20, "screen_phases": [0, 10]},
            ]
        )
    for item in variants:
        item.update(
            {
                "candidate_id": anchor["candidate_id"],
                "topk": anchor["topk"],
                "dropn": anchor["dropn"],
                "status": "BLOCKED_UNTIL_REAL_FIT_LABEL_MATURITY_AND_PURGE",
            }
        )
        item["config_sha256"] = stable_sha(item)
    return {
        "stage": "l3",
        "status": "PLAN_ONLY_NOT_EXECUTED",
        "anchor_sha256": stable_sha(anchor),
        "variants": variants,
        "max_new_fit_calls": 250,
        "max_usd": 6.0,
    }


def _checked_persisted_cell(path: Path, expected: dict) -> dict:
    """Check actual report, NAV and metric bytes before candidate selection."""
    record = load_json(path / "cell.json")
    from audit.csi1000_layered_search_core import check_cell_record

    check_cell_record(record)
    require(record["cell_key"] == expected["cell_key"], "unexpected stage cell identity")
    require(
        record["candidate_id"] == expected["candidate_id"]
        and record["phase"] == expected["phase"]
        and record["topk"] == expected["topk"]
        and record["dropn"] == expected["dropn"],
        "candidate/phase/portfolio identity drift",
    )
    files = ("daily_nav.csv", "fixed_report.parquet", "fixed_decisions.json", "metrics.json")
    hashes = record.get("artifact_sha256")
    require(isinstance(hashes, dict) and set(hashes) == set(files), "artifact hashes absent")
    for filename in files:
        require(
            (path / filename).is_file() and sha_bytes(path / filename) == hashes[filename],
            f"missing or tampered evidence {filename}",
        )
    require(hashes["daily_nav.csv"] == record["output_sha256"], "NAV SHA drift")
    import pandas as pd

    frame = pd.read_parquet(path / "fixed_report.parquet")
    actual, _ = report_metrics(frame)
    for k in ("strategy_total_return", "relative_excess_cagr", "strategy_max_drawdown", "mean_turnover"):
        require(
            math.isclose(float(actual[k]), float(record["metrics"][k]), rel_tol=1e-10, abs_tol=1e-10),
            f"recomputed metric mismatch: {k}",
        )
    return record


def screen_l1(stage: str, research_cells: str | Path, baseline_p0_cells: str | Path) -> dict:
    """Rank all pre-registered stage cells against P0 Baseline Top20/Drop2.

    Requires complete inputs for the nominated L1 stage; never cherry-picks a
    subset of already observed cells and never performs model fitting.
    """
    require(stage in ("l1-a", "l1-b", "l1-c"), "invalid L1 scope")
    expected = enumerate_cells(stage)
    root = Path(research_cells)
    baseline_root = Path(baseline_p0_cells)
    records = {}
    for spec in expected:
        records[spec["cell_key"]] = _checked_persisted_cell(root / spec["cell_key"], spec)
    baseline = {}
    for spec in enumerate_cells("p0"):
        if spec["candidate_id"] == BASELINE_CANDIDATE:
            baseline[spec["phase"]] = _checked_persisted_cell(baseline_root / spec["cell_key"], spec)
    require(set(baseline) == {0, 10}, "P0 Baseline phases missing")
    import pandas as pd

    for r in records.values():
        b = baseline[r["phase"]]
        for key in ("quote_sha256", "provider_fingerprint", "provider_snapshot_sha256", "execution", "quote_path"):
            require(r.get(key) and r.get(key) == b.get(key), f"candidate/baseline execution lineage mismatch: {key}")
        trial_dates = pd.read_parquet(root / r["cell_key"] / "fixed_report.parquet")
        base_dates = pd.read_parquet(baseline_root / b["cell_key"] / "fixed_report.parquet")
        require(trial_dates.index.equals(base_dates.index), "candidate/baseline execution dates differ")
        require(trial_dates["bench"].equals(base_dates["bench"]), "candidate/baseline saved benchmark differs")

    def loader_for(directory: Path):
        return lambda rec: pd.read_parquet(directory / rec["cell_key"] / "fixed_report.parquet")

    loader = loader_for(root)
    ranking = []
    configs = sorted({(row["candidate_id"], row["topk"], row["dropn"]) for row in records.values()})
    for cid, k, d in configs:
        trial = sorted(
            [r for r in records.values() if (r["candidate_id"], r["topk"], r["dropn"]) == (cid, k, d)],
            key=lambda r: r["phase"],
        )
        require(len(trial) == 2, "L1 challenger missing a phase")
        # If the candidate is itself the frozen Baseline Top20/Drop2, the
        # candidate and control are the same strategy; it cannot self-promote.
        if cid == BASELINE_CANDIDATE and (k, d) == (20, 2):
            continue
        base_rows = [baseline[phase] for phase in (0, 10)]
        chall, reference, turnover = paired_rows(trial, base_rows, loader)
        passed, details = coarse_pass(chall, reference)
        ranking.append(
            {
                "candidate_id": cid,
                "topk": k,
                "dropn": d,
                "passed_coarse": passed,
                "worst_delta_pp": details.get("worst_delta_pp"),
                "median_delta_pp": details.get("median_delta_pp"),
                "mdd_degrade_pp": details.get("mdd_degrade_pp"),
                "turnover_ratio": turnover,
                "diagnostic": details,
                "config_sha256": stable_sha({"candidate_id": cid, "topk": k, "dropn": d}),
            }
        )
    ranking.sort(
        key=lambda r: (
            not r["passed_coarse"],
            -(r["worst_delta_pp"] if r["worst_delta_pp"] is not None else -1e6),
            -(r["median_delta_pp"] if r["median_delta_pp"] is not None else -1e6),
            r["config_sha256"],
        )
    )
    qualifying = [r for r in ranking if r["passed_coarse"]]
    # L1-C requires two distinct passing configurations after L1-A/B.
    return {
        "stage": stage,
        "status": "AUDITED_HISTORICALLY_CONSUMED",
        "expected_cells": len(expected),
        "candidate_configurations": len(configs),
        "qualified_configs": len(qualifying),
        "can_expand_to_l1_c": stage == "l1-b" and len(qualifying) >= 2,
        "top_three_for_final_pool": qualifying[:3],
        "all_ranked_trials": ranking,
    }


def freeze_finalists(challengers: list[dict], baseline: dict) -> dict:
    """Freeze no more than five diverse challengers plus Baseline for five-phase final."""
    require(baseline.get("candidate_id") == BASELINE_CANDIDATE, "baseline control is not the frozen candidate")
    require(baseline.get("topk") == 20 and baseline.get("dropn") == 2, "baseline portfolio not frozen Top20/Drop2")
    quotas = {"l1": 3, "l2": 2, "l3": 1}
    selected = []
    for layer, limit in quotas.items():
        rows = [r for r in challengers if r.get("layer") == layer and r.get("coarse_pass") is True]
        rows.sort(
            key=lambda r: (
                -float(r["worst_delta_pp"]),
                -float(r["median_delta_pp"]),
                float(r["mdd_degrade_pp"]),
                float(r["turnover_ratio"]),
                str(r["config_sha256"]),
            )
        )
        selected += rows[:limit]
    selected.sort(
        key=lambda r: (
            -float(r["worst_delta_pp"]),
            -float(r["median_delta_pp"]),
            float(r["mdd_degrade_pp"]),
            float(r["turnover_ratio"]),
            str(r["config_sha256"]),
        )
    )
    unique = []
    seen = {str(baseline["config_sha256"])}
    for item in selected:
        key = str(item["config_sha256"])
        require(len(key) == 64, "finalist config needs real content SHA256")
        if key not in seen:
            seen.add(key)
            unique.append(item)
        if len(unique) == 5:
            break
    frozen = [baseline] + unique
    return {
        "stage": "l4",
        "status": "FROZEN_FINALIST_PLAN_NOT_EXECUTED",
        "max_configs": 6,
        "max_cells": 30,
        "configurations": frozen,
        "finalist_manifest_sha256": stable_sha(frozen),
        "historic_data": "HISTORICALLY_CONSUMED",
    }


def decide_from_paired_file(path: str | Path) -> dict:
    """Apply final 5-phase hard gates to precomputed paired phase evidence."""
    manifest = load_json(path)
    require(manifest.get("stage") == "l4", "not an L4 decision input")
    finalists = manifest.get("finalists")
    require(isinstance(finalists, list) and len(finalists) <= 5, "too many challengers in L4 decision")
    verdict, detail = decide_product(finalists, baseline_eligible=manifest.get("baseline_eligible") is True)
    return {
        "decision": verdict,
        "detail": detail,
        "l4_input_sha256": sha_bytes(path),
        "historic_data": "HISTORICALLY_CONSUMED",
        "promotion_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="CSI1000 historical selection (pure offline, never fit or bill).")
    parser.add_argument(
        "--stage", required=True, choices=("l2-propose", "l3-plan", "l1-a", "l1-b", "l1-c", "l4-decision")
    )
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--cells-dir", default="")
    parser.add_argument("--baseline-cells-dir", default="")
    parser.add_argument("--anchor", default="")
    parser.add_argument("--freq-qualified", action="store_true")
    parser.add_argument("--decision-input", default="")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.stage == "l2-propose":
        result = stage_a_proposals(args.repo_root)
    elif args.stage == "l3-plan":
        result = l3_structure_plan(load_json(args.anchor), args.freq_qualified)
    elif args.stage in ("l1-a", "l1-b", "l1-c"):
        result = screen_l1(args.stage, args.cells_dir, args.baseline_cells_dir)
    else:
        result = decide_from_paired_file(args.decision_input)
    out = Path(args.output)
    if out.exists():
        raise ValueError("refusing to overwrite an existing selection artifact")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
