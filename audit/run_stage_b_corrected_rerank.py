"""Re-evaluate FROZEN Stage-B candidates under unchanged and F1-fixed execution.

No model fit, no hyperparameter search, no provider or Volume writes, and no
production promotion. This is a retrospective diagnosis on a consumed tail.
Execute only with the original SHA-pinned snapshot and read-only Qlib provider.

Fast controls: --scope controls => winner + baseline, 10 phase cells.
Full rerank:   --scope all      => ALL frozen 11 candidates x 5 phases.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from audit.replay_stage_b_f1 import (
    ACCOUNT_TOL_CNY, FROZEN_RESULT, _backtest, _checked,
    _compare_original_decisions, _compare_original_report, _metrics, _sha,
)
from audit.stage_b_f6_preflight import BASELINE, WINNER, PHASES, SNAPSHOT, source_suffix
from csi1000_stage_b_core import (
    MARKET, RESERVED_EXECUTION_END, RESERVED_EXECUTION_START,
    rank_stage_b_candidates,
)
from execution_quote_universe import execution_quote_codes

FROZEN_N = 11
DAYS = 424
ORIGINAL_WINNER_FIXED = 0.9988782409498526
ORIGINAL_WINNER_FIXED_ORDERS = 1696


def require(condition, message):
    if not condition:
        raise ValueError("Stage-B corrected replay BLOCKED: " + message)


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def plan_sources(result: dict, snapshot_root: Path, scope: str) -> list[dict]:
    """Use frozen artifact pointers, including baseline phase0 repeat_b.

    Validate all input *bytes* before invoking ANY Qlib simulation. Missing
    originals are a blocking error; never fall back to newer model signals.
    """
    require(scope in {"controls", "all"}, "unsupported scope")
    root = Path(snapshot_root).resolve()
    require(root.is_dir(), "original snapshot root unavailable")
    candidates = result.get("ranked_candidates", [])
    require(len(candidates) == FROZEN_N and
            len({c["candidate_id"] for c in candidates}) == FROZEN_N,
            "frozen 11-candidate roster drift")
    chosen = (candidates if scope == "all" else
              [c for c in candidates if c["candidate_id"] in {WINNER, BASELINE}])
    require(len(chosen) == (FROZEN_N if scope == "all" else 2),
            "missing controls or candidates")
    jobs = []
    for c in chosen:
        phase_rows = c.get("phase_results", [])
        require(sorted(int(p["phase"]) for p in phase_rows) == list(PHASES),
                "candidate does not have exactly the five frozen phases")
        for phase in sorted(phase_rows, key=lambda row: row["phase"]):
            ident = c["candidate_id"]
            phase_id = int(phase["phase"])
            paths = {}
            for kind, filename in (
                ("report", "report.parquet"),
                ("signal", "signal.parquet"),
                ("decision", "decisions.json"),
            ):
                meta = phase[kind + "_artifact"]
                relative = source_suffix(meta["path"], phase=phase_id, candidate_id=ident)
                require(relative.name == filename, "frozen " + kind + " file name drift")
                path = (root / relative).resolve()
                require(path.is_relative_to(root), "source path escaped frozen snapshot root")
                require(path.is_file(), f"missing {kind}: {ident[:12]} phase{phase_id:02d}; "
                        "supply ORIGINAL snapshot, never substitute another phase")
                _checked(path, meta["sha256"])
                paths[kind] = path
            require(paths["report"].parent == paths["signal"].parent ==
                    paths["decision"].parent,
                    "phase sources do not share original directory")
            jobs.append({
                "cohort": "baseline" if ident == BASELINE else
                          ("winner" if ident == WINNER else "other"),
                "candidate_id": ident, "stage_a_rank": c["stage_a_rank"],
                "original_rank": c["rank"], "phase": phase_id,
                "original": phase, "paths": paths,
            })
    require(len(jobs) == (55 if scope == "all" else 10), "missing phase cells")
    return jobs


def collect_orders(rows: list[dict]) -> int:
    require(len(rows) == DAYS, "unexpected decision days")
    return sum(len(d["orders"]) for d in rows)


def order_key(rows: list[dict]) -> list[list[tuple]]:
    return [[(o["stock_id"], o["direction"], o["amount"], o["deal_amount"])
             for o in d["orders"]] for d in rows]


def compare_phase(phase: dict, legacy_report, legacy_decisions: list[dict],
                  fixed_report, fixed_decisions: list[dict]) -> dict:
    """Independent metric arithmetic; no promotion claims."""
    require(len(legacy_report) == DAYS and legacy_report.index.is_unique, "legacy calendar coverage drift")
    require(legacy_report.index.equals(fixed_report.index), "fixed calendar changed")
    legacy_metrics, _, _, _ = _metrics(legacy_report)
    fixed_metrics, _, _, _ = _metrics(fixed_report)
    for field in ("sharpe", "strategy_cagr", "relative_excess_cagr",
                  "strategy_max_drawdown"):
        require(abs(float(phase["original"][field]) - legacy_metrics[field]) <= 1e-5,
                "frozen metric drift: " + field)
    old_orders = order_key(legacy_decisions)
    new_orders = order_key(fixed_decisions)
    changed_orders = [str(d.date()) for d, a, b in
                      zip(legacy_report.index, old_orders, new_orders) if a != b]
    delta_account = (fixed_report["account"] - legacy_report["account"]).to_numpy(dtype=float)
    require(np.isfinite(delta_account).all(), "nonfinite NAV delta")
    changed_nav = [str(legacy_report.index[i].date()) for i, diff in
                   enumerate(delta_account) if abs(diff) > ACCOUNT_TOL_CNY]
    old_count = collect_orders(legacy_decisions)
    fixed_count = collect_orders(fixed_decisions)
    require(old_count == phase["original"]["decision_artifact"]["order_count"],
            "frozen original order count changed")
    require(legacy_metrics["n_days"] == fixed_metrics["n_days"] == DAYS,
            "phase duration changed")
    return {
        "cohort": phase["cohort"],
        "candidate_id": phase["candidate_id"],
        "phase": phase["phase"],
        "original_rank": phase["original_rank"],
        "original_orders": old_count,
        "fixed_orders": fixed_count,
        "changed_order_days": len(changed_orders),
        "first_changed_order_day": changed_orders[0] if changed_orders else None,
        "changed_nav_days": len(changed_nav),
        "first_changed_nav_day": changed_nav[0] if changed_nav else None,
        "old_metrics": legacy_metrics,
        "fixed_metrics": fixed_metrics,
        "sharpe_delta": fixed_metrics["sharpe"] - legacy_metrics["sharpe"],
        "cagr_delta": fixed_metrics["strategy_cagr"] - legacy_metrics["strategy_cagr"],
        "old_report_sha256": phase["original"]["report_artifact"]["sha256"],
        "frozen_signal_sha256": phase["original"]["signal_artifact"]["sha256"],
        "frozen_decisions_sha256": phase["original"]["decision_artifact"]["sha256"],
        "legacy_byte_and_decision_reproduction": "PASS",
        "independent_fixed_execution_ledger": "BLOCKED_REQUIRES_PHASE_MARKET_EXPORT",
        "external_market_execution": "BLOCKED",
    }


def diagnose_ranking(result: dict, records: list[dict], scope: str) -> dict:
    if scope != "all":
        return {"status": "BLOCKED_NOT_ALL_11_CANDIDATES",
                "compared_candidates": sorted({r["candidate_id"] for r in records}),
                "new_winner_certified": False}
    require(len(records) == 55 and
            len({(r["candidate_id"], r["phase"]) for r in records}) == 55,
            "not all frozen candidates and phases completed")
    per_candidate = {}
    for row in records:
        per_candidate.setdefault(row["candidate_id"], []).append(
            {"phase": row["phase"], **row["fixed_metrics"]}
        )
    frozen = {c["candidate_id"]: c for c in result["ranked_candidates"]}
    require(set(per_candidate) == set(frozen), "not all frozen candidates completed")
    rank_input = [{
        "candidate_id": cid, "stage_a_rank": frozen[cid]["stage_a_rank"],
        "phase_results": per_candidate[cid],
    } for cid in frozen]
    ordered = rank_stage_b_candidates(rank_input)
    return {
        "status": "DIAGNOSTIC_RERANK_ON_CONSUMED_TAIL_ONLY",
        "new_winner_certified": False,
        "previous_winner": WINNER,
        "diagnostic_top_candidate": ordered[0]["candidate_id"],
        "same_winner_after_execution_correction": ordered[0]["candidate_id"] == WINNER,
        "new_ranking": [
            {"candidate_id": c["candidate_id"], "new_rank": c["rank"],
             "frozen_original_rank": frozen[c["candidate_id"]]["rank"],
             "phase_summary": c["phase_summary"]}
            for c in ordered
        ],
    }


def run(args):
    repo = args.repo_root.resolve()
    snapshot_root = args.snapshot_root.resolve()
    provider = args.provider_uri.resolve()
    out = args.out_dir.resolve()
    require(not out.exists(), "output directory already exists")
    require(not out.is_relative_to(Path("/vol")), "forbidden writes to /vol")
    for protected in (repo, snapshot_root, provider):
        require(out != protected and not out.is_relative_to(protected),
                "output cannot be inside source, snapshot or provider")
    require(provider.is_dir(), "original provider directory missing")
    require(args.provider_snapshot_file.is_file(), "original provider snapshot file missing")
    result = read_json(repo / FROZEN_RESULT)
    meta = result["manifest"]
    original_snapshot = read_json(args.provider_snapshot_file)
    require(meta["snapshot_token"] == SNAPSHOT and
            original_snapshot.get("snapshot_token") == meta["snapshot_token"] and
            original_snapshot.get("provider_fingerprint") == meta["provider_fingerprint"],
            "frozen provider token/fingerprint mismatch")

    # Complete preflight of 30 or 165 source files BEFORE first simulation.
    jobs = plan_sources(result, snapshot_root, args.scope)

    import qlib
    from qlib.data import D
    qlib.init(provider_uri=str(provider), region="cn")
    calendar = pd.DatetimeIndex(D.calendar(start_time=RESERVED_EXECUTION_START,
                                           end_time=RESERVED_EXECUTION_END, freq="day"))
    require(len(calendar) == DAYS, "provider execution calendar not 424 days")
    static_codes = execution_quote_codes(
        MARKET, RESERVED_EXECUTION_START, RESERVED_EXECUTION_END)
    require(len(static_codes) > 0, "static historical constituent quote union empty")
    static_sha = hashlib.sha256(("\n".join(static_codes) + "\n").encode()).hexdigest()

    # Proven Qlib simulation path reused from PR #45; exact same frozen signal.
    # Relative to legacy, ONLY quote-universe coverage changes.
    out.mkdir(parents=True, exist_ok=False)
    records = []
    for job in jobs:
        old_report = pd.read_parquet(job["paths"]["report"])
        require(old_report.index.equals(calendar), "provider/report calendar mismatch")
        source_signal = pd.read_parquet(job["paths"]["signal"])
        require(list(source_signal.columns) == ["score"] and
                len(source_signal) == job["original"]["signal_artifact"]["rows"],
                "frozen signal schema/row count mismatch")
        original_decisions = read_json(job["paths"]["decision"])
        require(len(original_decisions) == DAYS, "original decision count mismatch")
        old_backtest, old_orders = _backtest(source_signal["score"], MARKET)
        _compare_original_report(old_report, old_backtest)
        _compare_original_decisions(original_decisions, old_orders)
        fixed_report, fixed_orders = _backtest(source_signal["score"], static_codes)
        record = compare_phase(job, old_report, old_orders, fixed_report, fixed_orders)
        if job["candidate_id"] == WINNER and job["phase"] == 0:
            require(abs(record["fixed_metrics"]["sharpe"] - ORIGINAL_WINNER_FIXED) <= 1e-8,
                    "known fixed winner phase0 Sharpe not reproduced")
            require(record["fixed_orders"] == ORIGINAL_WINNER_FIXED_ORDERS,
                    "known fixed winner phase0 1696 orders not reproduced")
        name = job["candidate_id"][:16] + "_p" + f"{job['phase']:02d}"
        folder = out / name
        folder.mkdir()
        fixed_report.to_parquet(folder / "fixed_diagnostic_report.parquet")
        (folder / "fixed_diagnostic_decisions.json").write_text(
            json.dumps(fixed_orders, indent=2, allow_nan=False) + "\n",
            encoding="utf-8")
        (folder / "comparison.json").write_text(
            json.dumps(record, indent=2, allow_nan=False) + "\n",
            encoding="utf-8")
        record["corrected_report_sha256"] = _sha(folder / "fixed_diagnostic_report.parquet")
        record["corrected_decisions_sha256"] = _sha(folder / "fixed_diagnostic_decisions.json")
        records.append(record)
        print(f"[diagnostic] {len(records)}/{len(jobs)} {job['cohort']} "
              f"phase{job['phase']:02d} old={record['old_metrics']['sharpe']:.5f} "
              f"fixed={record['fixed_metrics']['sharpe']:.5f}", flush=True)

    rank = diagnose_ranking(result, records, args.scope)
    result_summary = {
        "audit": "F1_EXECUTION_ONLY_DIAGNOSTIC_NOT_UNBIASED_OUT_OF_SAMPLE",
        "scope": args.scope,
        "original_snapshot_token": SNAPSHOT,
        "original_provider_fingerprint": meta["provider_fingerprint"],
        "static_quote_union_size": len(static_codes),
        "static_quote_union_sha256": static_sha,
        "candidate_count": 11 if args.scope == "all" else 2,
        "completed_phase_replays": len(records),
        "original_legacy_reproductions": len(records),
        "training_performed": False,
        "parameter_search_performed": False,
        "promotion_authorized": False,
        "retune_approved": False,
        "external_market_execution": "BLOCKED",
        "label_maturity_and_pit": "BLOCKED",
        "corrected_independent_ledgers": "BLOCKED_PENDING_PHASE_MARKET_EXPORTS",
        "ranking": rank,
        "phase_comparisons": records,
    }
    (out / "batch_result.json").write_text(
        json.dumps(result_summary, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "scope": args.scope, "phase_replays": len(records),
        "rerank": rank["status"], "suggested_top_candidate": rank.get("diagnostic_top_candidate"),
        "retune": "NOT_AUTHORIZED", "real_execution": "BLOCKED",
    }, indent=2, allow_nan=False))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parent.parent)
    p.add_argument("--snapshot-root", type=Path, required=True,
                   help="READ-ONLY copy of ORIGINAL /vol/csi1000_stage_b/<token> root")
    p.add_argument("--provider-uri", type=Path, required=True,
                   help="original read-only Qlib cn_data directory")
    p.add_argument("--provider-snapshot-file", type=Path, required=True,
                   help="original frozen provider_snapshot.json")
    p.add_argument("--out-dir", type=Path, required=True,
                   help="NEW separate output outside all original input roots")
    p.add_argument("--scope", choices=("controls", "all"), default="all",
                   help="controls first for fast diagnosis; all for all-11 ranking")
    run(p.parse_args())


if __name__ == "__main__":
    main()
