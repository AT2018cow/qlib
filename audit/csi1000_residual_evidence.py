"""Read-only CSI1000 B-line residual checks, not a backtest or fill certification.

Use pinned, existing Stage-B JSON + phase0 decisions. Optional external index
price calculation is ONLY a numerical parity check, never source certification.

Run:
 python -m audit.csi1000_residual_evidence --output /tmp/csi1000-residual.json
 python -m audit.csi1000_residual_evidence --frozen-report PATH.parquet \\
   --external-index independent_000852_closes.csv --output /tmp/bench-check.json

No training, no model configuration edits, no provider writes, no old forward fill.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "23b92de05cf36c82998de684d0fbf64d81ee54490d755bd3cf96311c00286785"
WIN = "4e908173705c76fee3782be37068672a3a845bd61894202f3152afe3b9d81ef2"
TOKEN = "51756897fc75230493194aef2e48815e4e9f3cc7426cc135f47bb8ba8e0d21e1"
FINGERPRINT = "cd2e68f571c6bd19fbb2b79089e891bb1c43b96ab1fcdb4d2b2a9b1fdd9ce5d5"
FILES = {
    "batch": ("audit/evidence/stage_b_corrected_rerank/batch_result.json",
              "ce0075f5d8c73325a12a4ecdf8fa4be856443a6fc1bac933f3b82d5c5035ff1c"),
    "ledger": ("audit/evidence/stage_b_seven_halts_baseline_p00/csi1000-seven-halts-p00-20261010b.json",
               "522f496e40c055f8f5126b1de65e8b5af2f044788529e51892956f9deb3825f3"),
    "baseline": ("audit/evidence/stage_b_corrected_rerank/23b92de05cf36c82_p00/fixed_diagnostic_decisions.json",
                 "ce7d37cdeb65b095114ad2ef16798b55958150a743692db8e3a292ee1badf8d7"),
    "winner": ("audit/evidence/stage_b_corrected_rerank/4e908173705c76fe_p00/fixed_diagnostic_decisions.json",
               "4694e67bd1a1870707d02d0eaf0e9bb0b29533cb209707e894d648fdd2a11063"),
}
ST = (
    ("2025-02-21", "SZ000488", 5),
    ("2025-04-29", "SZ002214", 5),
    ("2025-05-06", "SH603398", 5),
    ("2025-05-06", "SZ300379", 20),
)


def require(ok, reason):
    if not ok:
        raise ValueError("BLOCKED_EVIDENCE: " + reason)


def digest(content: bytes):
    return hashlib.sha256(content).hexdigest()


def read_pinned(root: Path, name: str):
    rel, sha = FILES[name]
    content = (root / rel).read_bytes()
    require(digest(content) == sha, name + " content SHA256 drift")
    return json.loads(content)


def sell_orders(rows, day, code):
    matches = []
    for entry in rows:
        if str(entry["start_time"])[:10] == day:
            matches.extend(
                x for x in entry["orders"]
                if x["stock_id"] == code and x["direction"] == 0
            )
    return matches


def validate_residual(batch, ledger, baseline, winner):
    """Reject inconsistent frozen identities, histories and ST order metadata."""
    require(batch.get("original_snapshot_token") == TOKEN and ledger.get("snapshot") == TOKEN,
            "snapshot identity drift")
    require(batch.get("original_provider_fingerprint") == FINGERPRINT
            and ledger.get("provider_fingerprint") == FINGERPRINT, "provider drift")
    require(batch.get("candidate_count") == 11 and batch.get("completed_phase_replays") == 55
            and batch.get("original_legacy_reproductions") == 55,
            "rerank evidence incomplete")
    require(batch.get("training_performed") is False
            and batch.get("parameter_search_performed") is False
            and batch.get("promotion_authorized") is False, "not read-only diagnostic")
    require(batch.get("corrected_independent_ledgers")
            == "BLOCKED_PENDING_PHASE_MARKET_EXPORTS", "unexpected corrected-ledger status")
    cell = ledger["cells"][0]
    require(len(ledger["cells"]) == 1 and cell["candidate_id"] == BASE
            and cell["phase"] == 0 and cell["ledger"] == "PASS_RESEARCH_ACCOUNTING_ONLY"
            and cell["matched_trading_days"] == 424 and cell["filled_orders"] == 1700
            and not cell["fail_fields"], "baseline ledger does not match accepted scope")
    require(ledger["independent_benchmark_price_audit"] == "BLOCKED_SAVED_BENCH_RETURNS_USED",
            "external benchmark status drift")
    require(len(baseline) == len(winner) == 424, "phase0 decision dates drift")
    require(all(r["decision_index"] == i for rows in (baseline, winner)
                for i, r in enumerate(rows)), "decision indices drift")
    for rows in (baseline, winner):
        ds = [str(r["start_time"])[:10] for r in rows]
        require(ds == sorted(set(ds)), "decision dates not strictly sorted unique")
    require(sum(len(r["orders"]) for r in baseline) == 1700, "baseline order total drift")

    rank = batch["ranking"]
    require(rank["status"] == "DIAGNOSTIC_RERANK_ON_CONSUMED_TAIL_ONLY"
            and rank["new_winner_certified"] is False, "rank audit status drift")
    ranks = {x["candidate_id"]: x for x in rank["new_ranking"]}
    require(len(ranks) == 11 and ranks[BASE]["new_rank"] == 1
            and ranks[WIN]["new_rank"] == 10
            and ranks[WIN]["frozen_original_rank"] == 1,
            "rank identities or ordering drift")
    pairs = {(r["candidate_id"], r["phase"]): r for r in batch["phase_comparisons"]}
    require(len(pairs) == 55 and set(pairs) >= {(BASE, 0), (WIN, 0)},
            "phase comparisons missing or duplicated")
    require(all(r["legacy_byte_and_decision_reproduction"] == "PASS"
                for r in pairs.values()), "failed legacy negative-control reproduction")
    old_new = [pairs[(cid, 0)] for cid in (BASE, WIN)]
    require(old_new[0]["corrected_decisions_sha256"] == FILES["baseline"][1]
            and old_new[1]["corrected_decisions_sha256"] == FILES["winner"][1],
            "corrected decision SHA in batch drift")
    require(old_new[0]["corrected_report_sha256"]
            == cell["fixed_sha256"]["report"]["sha256"]
            and old_new[0]["corrected_decisions_sha256"]
            == cell["fixed_sha256"]["decisions"]["sha256"], "phase0 ledger linkage drift")
    require(all(r["first_changed_nav_day"] == "2025-06-30"
                and r["first_changed_order_day"] == "2025-07-01"
                for r in old_new), "F1 first-diff changed")

    evidence = cell["known_st_execution_review"]
    require(len(evidence) == 4 and len(set((x["date"], x["stock_id"]) for x in evidence)) == 4,
            "ST ledger count or duplicate changed")
    by_key = {(e["date"], e["stock_id"]): e for e in evidence}
    events = []
    for day, code, limit in ST:
        require((day, code) in by_key and
                by_key[(day, code)]["status"] == "BLOCKED_MARKET_EXECUTION_NOT_CERTIFIED",
                "ST status missing: " + day + "/" + code)
        expected_issue = "HISTORIC_ST_20PCT_LIMIT_SELL_NOT_EXTERNALLY_VALIDATED" if limit == 20 else (
            "HISTORIC_ST_5PCT_LIMIT_SELL_NOT_EXTERNALLY_VALIDATED")
        require(by_key[(day, code)]["issue"] == expected_issue, "ST board rule status mismatch")
        sells = sell_orders(baseline, day, code)
        require(len(sells) == 1 and len(sell_orders(winner, day, code)) == 0,
                "ST order occurrence drift: " + day + "/" + code)
        o = sells[0]
        require(o["amount"] > 0 and math.isclose(o["amount"], o["deal_amount"],
                                                  rel_tol=1e-12, abs_tol=1e-8)
                and o["factor"] > 0 and o["order_index"] >= 0,
                "ST saved full-fill or factor drift")
        events.append({
            "date": day, "stock_id": code, "limit_pct": limit,
            "baseline_order_index": o["order_index"],
            "adjusted_units": o["amount"], "saved_factor": o["factor"],
            "saved_full_fill": True, "winner_same_stock_same_day_sell": False,
            "certification": "BLOCKED_MARKET_EXECUTION_NOT_CERTIFIED",
            "source_notice": by_key[(day, code)]["notice"],
        })
    bench_values = {r["fixed_metrics"]["benchmark_total_return"] for r in pairs.values()}
    require(len(bench_values) == 1, "saved benchmark inconsistent across phase comparisons")
    return {
        "audit": "SCOPED_STATIC_RESIDUAL_DIAGNOSTIC_NOT_MARKET_CERTIFICATION",
        "snapshot_token": TOKEN, "provider_fingerprint": FINGERPRINT,
        "pinned_source_sha256": {k: FILES[k][1] for k in FILES},
        "ledger": "PASS_RESEARCH_ACCOUNTING_ONLY_BASELINE_PHASE0_ONLY",
        "st_events": events, "st_exposure": "UNQUANTIFIED_PATH_DEPENDENCE",
        "benchmark_saved_total_return": next(iter(bench_values)),
        "independent_benchmark_price_audit": "DEFERRED_NO_EXTERNAL_PRICES",
        "ranking": {
            "baseline_diagnostic_rank": 1, "winner_diagnostic_rank": 10,
            "baseline_phase0_old_new_sharpe": [
                old_new[0][k]["sharpe"] for k in ("old_metrics", "fixed_metrics")
            ],
            "winner_phase0_old_new_sharpe": [
                old_new[1][k]["sharpe"] for k in ("old_metrics", "fixed_metrics")
            ],
            "baseline_phase0_old_new_orders": [
                old_new[0][k] for k in ("original_orders", "fixed_orders")
            ],
            "winner_phase0_old_new_orders": [
                old_new[1][k] for k in ("original_orders", "fixed_orders")
            ],
            "first_changed_nav_day": "2025-06-30",
            "new_out_of_sample": False,
        },
        "negative_controls": {
            "pinned_bytes_and_lineage": "PASS",
            "four_orders_exact_and_wrong_candidate_absent": "PASS",
            "55_legacy_reproductions_recorded": "PASS_RECORD_CHECK_ONLY",
            "shared_saved_benchmark": "PASS_INTERNAL_ONLY",
            "new_data_provider_market_fills": "NOT_TESTED",
        },
        "recommendation": "KEEP_CANONICAL_PENDING_FORWARD",
    }


def compare_index_prices(external_rows, dates, saved_bench):
    """Numerical close-to-close comparison. Source provenance is NOT certified.

    external_rows: strict 425 rows, prior session + all 424 dates;
    date,close,index_code,return_type,source_id,source_url are compulsory.
    """
    require(len(dates) == len(saved_bench) == 424, "frozen daily report length not 424")
    require(dates == sorted(set(dates)), "saved report dates not unique/increasing")
    require(len(external_rows) == 425, "requires 424 sessions plus previous index close")
    index_dates, levels = [], []
    sources = set()
    for row in external_rows:
        require(row["index_code"] in ("000852", "000852.SH"),
                "wrong benchmark index identity")
        require(row["return_type"] == "price_return",
                "index total-return vs price-return methodology mismatch")
        require(bool(row["source_url"].strip()) and bool(row["source_id"].strip()),
                "no independently documented source")
        require(row["source_id"].lower() not in
                ("qlib", "stage_b", "saved_bench", "same_provider", "frozen_provider"),
                "cannot self-certify source as external")
        index_dates.append(date.fromisoformat(row["date"]))
        level = float(row["close"])
        require(math.isfinite(level) and level > 0, "invalid price level")
        levels.append(level)
        sources.add((row["source_id"], row["source_url"]))
    require(len(sources) == 1, "source switched within comparison")
    require(index_dates == sorted(set(index_dates)), "index dates duplicate/unsorted")
    require(index_dates[0] < dates[0] and index_dates[1:] == dates,
            "external dates not exact trading-calendar match")
    computed = [levels[i+1] / levels[i] - 1 for i in range(424)]
    require(all(math.isfinite(float(x)) for x in saved_bench), "bad saved benchmark returns")
    errors = [abs(a - float(b)) for a, b in zip(computed, saved_bench)]
    require(max(errors) <= 1e-6, "external prices vs saved bench not within 1e-6")
    return {"status": "NUMERICAL_MATCH_PENDING_HUMAN_SOURCE_CERTIFICATION",
            "n_sessions": 424, "max_abs_daily_return_error": max(errors),
            "source": sorted(sources)[0]}


def benchmark_optional(args, result):
    require(bool(args.external_index) == bool(args.frozen_report),
            "supply BOTH external-index and frozen-report or neither")
    if not args.external_index:
        return
    batch_row = result["pinned_source_sha256"]
    _ = batch_row  # Freeze linkage audited in validate_residual.
    expected_report_sha = "7d8fbf8f03c608fc6e4e839ef116a9e34d439d772cacf04569fa85785d52f436"
    require(digest(args.frozen_report.read_bytes()) == expected_report_sha,
            "corrected phase0 report Parquet content SHA mismatch")
    try:
        import pandas as pd
    except ImportError as e:
        raise RuntimeError("pandas+pyarrow required for pinned frozen Parquet") from e
    frame = pd.read_parquet(args.frozen_report)
    require(list(frame.columns).count("bench") == 1 and len(frame) == 424,
            "frozen report schema/length drift")
    dates = [x.date() for x in pd.DatetimeIndex(frame.index)]
    with args.external_index.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {"date", "close", "index_code", "return_type", "source_id", "source_url"}
        require(reader.fieldnames is not None and required <= set(reader.fieldnames),
                "external CSV missing metadata columns")
        rows = list(reader)
    check = compare_index_prices(rows, dates, list(frame["bench"]))
    check["external_csv_sha256"] = digest(args.external_index.read_bytes())
    check["frozen_report_sha256"] = expected_report_sha
    result["external_benchmark_numerical_check"] = check
    # Even a numerical match does not certify independent vendor provenance.
    result["independent_benchmark_price_audit"] = "DEFERRED_PENDING_INDEPENDENT_PROVENANCE"


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo-root", type=Path, default=ROOT)
    p.add_argument("--external-index", type=Path)
    p.add_argument("--frozen-report", type=Path)
    p.add_argument("--output", type=Path)
    args = p.parse_args(argv)
    try:
        objects = [read_pinned(args.repo_root, k) for k in
                   ("batch", "ledger", "baseline", "winner")]
        result = validate_residual(*objects)
        benchmark_optional(args, result)
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2
    serialized = json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.output:
        out = args.output.resolve()
        require(not out.exists(), "output must be new; never overwrite")
        require(not out.is_relative_to(args.repo_root.resolve()),
                "write derived audit result OUTSIDE the repository")
        out.write_text(serialized, encoding="utf-8")
    else:
        print(serialized, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
