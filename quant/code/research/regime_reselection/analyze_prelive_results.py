"""Rank the registered corrected replays and retain evidence limitations."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "execution"))
from research_identity import SOURCE_IDENTITY_VERSION, source_manifest


def load_batch(root, mode, register_sha, engine_sha, expected_count=None, expected_source_manifest=None):
    matches = []
    results, jobs = {}, {}
    for path in Path(root).glob(f"*_{mode}_results.json"):
        payload = json.loads(path.read_text())
        if payload.get("status") not in ("COMPLETED", "FAILED"):
            continue
        if expected_source_manifest is not None and payload.get("source_manifest") != expected_source_manifest:
            continue
        if all(
            j.get("register_sha256") == register_sha and j.get("engine_sha256") == engine_sha for j in payload["jobs"]
        ):
            matches.append(path)
            by_key = {job["key"]: job for job in payload["jobs"]}
            for result in payload["results"]:
                if result.get("status") != "COMPLETED" or result["key"] not in by_key:
                    raise ValueError("only independently completed job receipts can be recovered")
                if expected_source_manifest is not None and (
                    result.get("source_identity_version") != SOURCE_IDENTITY_VERSION
                    or result.get("source_manifest") != expected_source_manifest
                    or result.get("engine_sha256") != engine_sha
                    or result.get("register_sha256") != register_sha
                    or result.get("provider_contract_sha256") != by_key[result["key"]].get("provider_contract_sha256")
                ):
                    raise ValueError("recovered job implementation or input identity mismatch")
                key = result["key"]
                if key in results and results[key]["metrics"] != result["metrics"]:
                    raise ValueError(f"conflicting recovered metrics for {key}")
                results[key], jobs[key] = result, by_key[key]
    if not results or (expected_count is not None and len(results) != expected_count):
        raise ValueError(f"incomplete {mode} receipts: {len(results)} of {expected_count}")
    return matches, {
        "status": "COMPLETED_FROM_VERIFIED_JOB_RECEIPTS",
        "mode": mode,
        "results": list(results.values()),
        "jobs": list(jobs.values()),
    }


def scenario(result, bps):
    return next(m for m in result["metrics"] if m["slippage_bps"] == bps)


def attach_extension_tails(root, extension, primary_end):
    """Measure only new dates, retaining the existing account's starting NAV.

    A strong primary window must not conceal a reversal in the extension tail.
    Original receipts remain unchanged; report bytes must match those receipts.
    """
    import pandas as pd

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "execution"))
    from portfolio_performance import portfolio_performance

    jobs = {job["key"]: job for job in extension["jobs"]}
    for result in extension["results"]:
        job = jobs[result["key"]]
        relative = "extra_0bp/report.parquet"
        path = Path(root) / job["run_id"] / result["key"] / relative
        expected = result["files"][relative]
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("extension report fingerprint mismatch")
        frame = pd.read_parquet(path)
        before = frame.loc[frame.index <= pd.Timestamp(primary_end)]
        tail = frame.loc[frame.index > pd.Timestamp(primary_end)]
        if before.empty or tail.empty or str(before.index[-1])[:10] != primary_end:
            raise ValueError("extension tail requires the primary closing NAV")
        initial_nav = float(before["account"].iloc[-1])
        metrics = portfolio_performance(tail, initial_cash=initial_nav, backtest_start=tail.index[0])
        result["tail_metrics"] = {
            **metrics,
            "initial_nav": initial_nav,
            "source_report_sha256": expected,
            "method": "CONTINUOUS_ACCOUNT_DATES_AFTER_PRIMARY_WINDOW",
        }


def analyze(primary, extension, phases, register):
    by_id = {r["key"]: r for r in primary["results"]}
    ext = {r["key"]: r for r in extension["results"]}
    phase = {r["key"]: r for r in phases["results"]}
    if set(by_id) != {c["id"] for c in register["candidates"]}:
        raise ValueError("primary candidates differ from registered universe")
    rule = register["comparison_rule"]
    decisions = []
    tables = {}
    for market in ("csi300", "csi500", "csi1000"):
        candidates = [c for c in register["candidates"] if c["market"] == market]
        incumbent = next(c for c in candidates if c["incumbent"])
        base = scenario(by_id[incumbent["id"]], 0)
        stress = scenario(by_id[incumbent["id"]], 10)
        ranked = sorted(
            candidates,
            key=lambda c: (
                -(
                    scenario(by_id[c["id"]], 0)["sharpe"]
                    if scenario(by_id[c["id"]], 0)["sharpe"] is not None
                    else -1e10
                ),
                -scenario(by_id[c["id"]], 0)["strategy_max_drawdown"],
                -scenario(by_id[c["id"]], 0)["strategy_cagr"],
                c["id"],
            ),
        )
        tables[market] = []
        comparisons = []
        for c in ranked:
            m = scenario(by_id[c["id"]], 0)
            pressure = scenario(by_id[c["id"]], 10)
            tables[market].append(
                {
                    "id": c["id"],
                    "incumbent": c["incumbent"],
                    "cagr": m["strategy_cagr"],
                    "mdd": m["strategy_max_drawdown"],
                    "sharpe": m["sharpe"],
                    "cagr_plus_10bp": pressure["strategy_cagr"],
                    "sharpe_plus_10bp": pressure["sharpe"],
                    "quote_gaps": m["quote_gap_count"],
                    "max_single_weight": m["max_single_weight"],
                }
            )
            if c["incumbent"]:
                continue
            years = sorted(set(m["annual"]) & set(base["annual"]))
            annual_wins = sum(
                m["annual"][y]["strategy_total_return"] > base["annual"][y]["strategy_total_return"] for y in years
            )
            checks = {
                "sharpe_improvement": m["sharpe"] is not None
                and base["sharpe"] is not None
                and m["sharpe"] - base["sharpe"] >= rule["challenger_min_sharpe_improvement"],
                "mdd_tolerance": m["strategy_max_drawdown"]
                >= base["strategy_max_drawdown"] - rule["challenger_max_mdd_deterioration"],
                "cagr_improvement": m["strategy_cagr"]
                >= base["strategy_cagr"] + rule["challenger_min_net_cagr_improvement"],
                "annual_wins": bool(years) and annual_wins / len(years) >= rule["annual_relative_win_fraction_min"],
                "pressure_10bp": pressure["sharpe"] is not None
                and stress["sharpe"] is not None
                and pressure["sharpe"] > stress["sharpe"],
            }
            extension_check = False
            if c["id"] in ext and incumbent["id"] in ext:
                em = ext[c["id"]].get("tail_metrics", {})
                eb = ext[incumbent["id"]].get("tail_metrics", {})
                extension_check = (
                    bool(em)
                    and bool(eb)
                    and (em["start"], em["end"]) == (eb["start"], eb["end"])
                    and em["sharpe"] is not None
                    and eb["sharpe"] is not None
                    and em["sharpe"] >= eb["sharpe"]
                )
            checks["extension_support"] = extension_check
            phase_details = []
            for p in register["phase_replications"]["phases"]:
                ck, bk = f"{c['id']}_phase{p:02}", f"{incumbent['id']}_phase{p:02}"
                if ck in phase and bk in phase:
                    cm, bm = scenario(phase[ck], 0), scenario(phase[bk], 0)
                    phase_details.append(
                        {
                            "phase": p,
                            "candidate_return": cm["strategy_total_return"],
                            "incumbent_return": bm["strategy_total_return"],
                            "wins": cm["strategy_total_return"] > bm["strategy_total_return"],
                        }
                    )
            checks["phase_support"] = (
                len(phase_details) == len(register["phase_replications"]["phases"])
                and sum(x["wins"] for x in phase_details) / len(phase_details)
                >= rule["available_phase_net_outperformance_fraction_min"]
            )
            comparisons.append(
                {
                    "candidate": c["id"],
                    "checks": checks,
                    "annual_wins": annual_wins,
                    "years": len(years),
                    "phase_details": phase_details,
                    "passes_registered_comparison": all(checks.values()),
                }
            )
        supported = [r["candidate"] for r in comparisons if r["passes_registered_comparison"]]
        decisions.append(
            {
                "market": market,
                "incumbent": incumbent["id"],
                "primary_rank_first": ranked[0]["id"],
                "supported_challengers": supported,
                "comparisons": comparisons,
                "status": "REFERENCE_ONLY_PENDING_OPERATIONAL_REVIEW",
                "switch_authorized": False,
                "live_trading_approved": False,
                "limitations": [
                    "Historical ST and auction liquidity not independently verified",
                    "Missing quotes are disclosed reference carries, not certified halts",
                    "Used history is consumed and is not new OOS",
                    "Production state/current data acceptance remains separate",
                ],
            }
        )
    return {
        "kind": "PRELIVE_CORRECTED_RANKING",
        "account": 1000000,
        "engine": "PRODUCTION_PAPER_PORTFOLIO",
        "primary_tables": tables,
        "decisions": decisions,
        "extension_metrics": [m for r in extension["results"] for m in r["metrics"]],
        "extension_tail_metrics": [
            {"candidate": r["key"], **r["tail_metrics"]} for r in extension["results"] if "tail_metrics" in r
        ],
        "phase_metrics": [m for r in phases["results"] for m in r["metrics"]],
        "live_trading_approved": False,
        "research_promotion_eligible": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--register", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--historical-source-manifest",
        type=Path,
        help="explicit read-only analysis of legacy receipts; never authorizes replay or cache reuse",
    )
    args = parser.parse_args()
    register_sha = hashlib.sha256(args.register.read_bytes()).hexdigest()
    implementation = source_manifest("replay")
    engine_sha = implementation["sha256"]
    if args.historical_source_manifest is not None:
        historical = json.loads(args.historical_source_manifest.read_text())
        if historical.get("register_sha256") != register_sha:
            raise ValueError("historical source receipt does not bind this registration")
        engine_sha = historical["code"]["quant/code/research/regime_reselection/prelive_revalidation.py"]
        implementation = None
    register = json.loads(args.register.read_text())
    current_spines = {c["market"]: c["spine"] for c in register["candidates"] if c["incumbent"]}
    phase_market = register["phase_replications"]["market"]
    counts = {
        "primary": len(register["candidates"]),
        "extension": sum(c["spine"] == current_spines[c["market"]] for c in register["candidates"]),
        "phases": len(register["phase_replications"]["phases"])
        * sum(c["market"] == phase_market for c in register["candidates"]),
    }
    selected = {
        mode: load_batch(args.results, mode, register_sha, engine_sha, count, implementation)
        for mode, count in counts.items()
    }
    attach_extension_tails(args.results, selected["extension"][1], register["primary_window"]["last_execution"])
    result = analyze(
        *(selected[m][1] for m in ("primary", "extension", "phases")), json.loads(args.register.read_text())
    )
    result["register_sha256"] = register_sha
    result["engine_sha256"] = engine_sha
    result["source_identity_version"] = 1 if implementation is None else SOURCE_IDENTITY_VERSION
    result["historical_evidence_only"] = implementation is None
    if implementation is not None:
        result["source_manifest"] = implementation
    else:
        result["historical_source_receipt_sha256"] = hashlib.sha256(
            args.historical_source_manifest.read_bytes()
        ).hexdigest()
    result["batch_hashes"] = {
        mode: [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths] for mode, (paths, _) in selected.items()
    }
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    for market, rows in result["primary_tables"].items():
        for row in rows:
            print(market, row["id"], row["cagr"], row["mdd"], row["sharpe"], "incumbent" if row["incumbent"] else "")


if __name__ == "__main__":
    main()
