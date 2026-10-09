"""Diagnostic replay for F1: original vs fixed CSI1000 quote coverage.

NO training, Modal access, Volume writes, or artifact overwrite. Qlib is called
only for diagnostic execution replay; the independent arithmetic below uses
audit.independent_stage_b_metrics, never canonical portfolio_performance.

Use the *original* provider snapshot, original frozen signal, and same frozen
execution interval. Reproduce the legacy report/decisions first; fail closed if
they cannot be reproduced. Then switch only the exchange's quote coverage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from audit.independent_stage_b_metrics import REQUIRED, metrics
from csi1000_stage_b_core import (
    BENCHMARK, MARKET, N_DROP, RESERVED_EXECUTION_END,
    RESERVED_EXECUTION_START, TOPK,
)
from execution_quote_universe import execution_quote_codes

WINNER = "4e908173705c76fee3782be37068672a3a845bd61894202f3152afe3b9d81ef2"
SOURCE_SHA = "7a2676397b0f8e6f69c0bffc98d1764647f644ac"
FROZEN_RESULT = "results/csi1000_stage_b/stage_b_full_51756897fc752304.json"
REPORT = "results/csi1000_stage_b/audit_reports/winner_4e908173705c76fe/phase00__phases.parquet"
SIGNAL = "audit/evidence/winner_phase0/raw/signal.parquet"
DECISIONS = "audit/evidence/winner_phase0/raw/decisions.json"
RELATIVE_TOL = 1e-10
ACCOUNT_TOL_CNY = 0.01


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _checked(path, expected):
    if _sha(path) != expected:
        raise ValueError(f"frozen source bytes differ: {path}")


def _audit_decisions(decisions):
    """Same serialization fields, independently assembled from Qlib objects."""
    rows = []
    for index, d in enumerate(decisions):
        orders = [
            {
                "order_index": j, "stock_id": str(o.stock_id),
                "direction": int(o.direction), "amount": float(o.amount),
                "deal_amount": float(o.deal_amount),
                "factor": None if o.factor is None else float(o.factor),
                "start_time": str(o.start_time), "end_time": str(o.end_time),
            }
            for j, o in enumerate(d.get_decision())
        ]
        rows.append({
            "decision_index": index, "start_time": str(d.start_time),
            "end_time": str(d.end_time), "orders": orders,
        })
    return rows


def _compare_original_report(expected, actual):
    if not expected.index.equals(actual.index):
        raise ValueError("legacy replay calendar differs from frozen report")
    if list(expected.columns) != list(actual.columns):
        raise ValueError("legacy replay schema differs from frozen report")
    for col in REQUIRED:
        difference = float(np.max(np.abs(
            expected[col].to_numpy(dtype=float) - actual[col].to_numpy(dtype=float)
        )))
        tolerance = ACCOUNT_TOL_CNY if col in {
            "account", "cash", "value", "total_cost", "total_turnover"
        } else RELATIVE_TOL
        if not math.isfinite(difference) or difference > tolerance:
            raise ValueError(
                f"LEGACY REPLAY NOT REPRODUCIBLE: {col} max diff {difference} > {tolerance}"
            )


def _compare_original_decisions(expected, actual):
    if len(expected) != len(actual):
        raise ValueError("legacy replay decision count differs")
    for old, new in zip(expected, actual):
        if old["start_time"] != new["start_time"] or old["end_time"] != new["end_time"]:
            raise ValueError("legacy replay decision clock differs")
        if len(old["orders"]) != len(new["orders"]):
            raise ValueError(f"legacy order count mismatch on {old['start_time']}")
        for o, a in zip(old["orders"], new["orders"]):
            for key in ("stock_id", "direction", "start_time", "end_time"):
                if o[key] != a[key]:
                    raise ValueError(f"legacy {key} differs at {old['start_time']}")
            for key in ("amount", "deal_amount", "factor"):
                if (o[key] is None) != (a[key] is None):
                    raise ValueError(f"legacy {key} null differs")
                if o[key] is not None and not math.isclose(
                    float(o[key]), float(a[key]), rel_tol=1e-10, abs_tol=1e-7
                ):
                    raise ValueError(f"legacy {key} differs at {old['start_time']}")


def _backtest(signal, codes):
    from board_execution import research_exchange
    from qlib.backtest import collect_data

    result = {}
    decisions = list(collect_data(
        strategy={
            "class": "DeterministicTopkDropoutStrategy",
            "module_path": "deterministic_strategy",
            "kwargs": {
                "signal": signal,
                "topk": TOPK,
                "n_drop": N_DROP,
                "forbid_all_trade_at_limit": False,
            },
        },
        executor={
            "class": "SimulatorExecutor",
            "module_path": "qlib.backtest.executor",
            "kwargs": {
                "time_per_step": "day",
                "generate_portfolio_metrics": True,
                "track_data": True,
            },
        },
        start_time=RESERVED_EXECUTION_START,
        end_time=RESERVED_EXECUTION_END,
        account=100_000_000,
        benchmark=BENCHMARK,
        exchange_kwargs=research_exchange(
            RESERVED_EXECUTION_START, RESERVED_EXECUTION_END, codes=codes
        ),
        return_value=result,
    ))
    return result["portfolio_dict"]["1day"][0], _audit_decisions(decisions)


def _metrics(report):
    dates = list(pd.DatetimeIndex(report.index).date)
    inputs = {key: report[key].astype(float).to_list() for key in REQUIRED}
    numbers, sensitivity, diagnostics, daily = metrics(dates, inputs)
    return numbers, sensitivity, diagnostics, pd.DataFrame(daily)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider-uri", required=True, type=Path,
                        help="Read-only path of ORIGINAL provider, not refreshed data")
    parser.add_argument("--provider-snapshot", required=True, type=Path,
                        help="Original csi1000_stage_b/provider_snapshot.json from frozen volume")
    parser.add_argument("--output-dir", required=True, type=Path,
                        help="New separate local audit-only directory; will not overwrite")
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    args = parser.parse_args()
    repo = args.repo_root.resolve()
    out = args.output_dir.resolve()
    if out.exists():
        parser.error("output directory already exists: never overwrite prior audit")
    if out == args.provider_uri.resolve() or args.provider_uri.resolve() in out.parents:
        parser.error("output cannot be placed inside original provider")
    if Path("/vol") == out or Path("/vol") in out.parents:
        parser.error("write operations to /vol are forbidden")
    result = json.loads((repo / FROZEN_RESULT).read_text())
    snapshot = json.loads(args.provider_snapshot.read_text())
    manifest = result["manifest"]
    if snapshot.get("snapshot_token") != manifest["snapshot_token"]:
        raise ValueError("provider snapshot-token drift")
    if snapshot.get("provider_fingerprint") != manifest["provider_fingerprint"]:
        raise ValueError("original provider fingerprint drift")
    winner = next(c for c in result["ranked_candidates"] if c["candidate_id"] == WINNER)
    phase = next(p for p in winner["phase_results"] if p["phase"] == 0)
    report_path = repo / REPORT
    signal_path = repo / SIGNAL
    decisions_path = repo / DECISIONS
    for kind, path in (
        ("report_artifact", report_path),
        ("signal_artifact", signal_path),
        ("decision_artifact", decisions_path),
    ):
        _checked(path, phase[kind]["sha256"])

    old_report = pd.read_parquet(report_path)
    frame = pd.read_parquet(signal_path)
    if list(frame.columns) != ["score"] or len(frame) != phase["signal_artifact"]["rows"]:
        raise ValueError("frozen signal schema/count mismatch")
    signal = frame["score"]
    frozen_decisions = json.loads(decisions_path.read_text())

    import qlib
    from qlib.data import D
    qlib.init(provider_uri=str(args.provider_uri.resolve()), region="cn")

    expected_calendar = list(D.calendar(
        start_time=RESERVED_EXECUTION_START,
        end_time=RESERVED_EXECUTION_END, freq="day",
    ))
    if list(old_report.index) != list(pd.DatetimeIndex(expected_calendar)):
        raise ValueError("newly initialized provider calendar differs from frozen report")

    expanded_codes = execution_quote_codes(
        MARKET, RESERVED_EXECUTION_START, RESERVED_EXECUTION_END
    )
    for stock in ("SH603301", "SH688066"):
        if stock not in expanded_codes:
            raise ValueError(f"frozen F1 former constituent omitted from quote union: {stock}")
    # Early fail-close: static list must actually expose the old holdings'
    # raw provider quotes after they leave the *index*, not merely their codes.
    probe = D.features(
        ["SH603301", "SH688066"],
        ["$open", "$close", "$factor"],
        "2025-07-02", "2025-07-02", freq="day",
    )
    for stock in ("SH603301", "SH688066"):
        try:
            fields = probe.loc[(stock, pd.Timestamp("2025-07-02"))]
        except KeyError as exc:
            raise ValueError(f"underlying F1 post-removal market quote unavailable: {stock}") from exc
        if not all(math.isfinite(float(fields[k])) and float(fields[k]) > 0 for k in
                   ("$open", "$close", "$factor")):
            raise ValueError(f"underlying F1 post-removal market quote invalid: {stock}")

    # Original must replay before diagnosing any new path.
    old_replay, old_orders = _backtest(signal, MARKET)
    _compare_original_report(old_report, old_replay)
    _compare_original_decisions(frozen_decisions, old_orders)

    fixed_report, fixed_orders = _backtest(signal, expanded_codes)
    if not old_report.index.equals(fixed_report.index):
        raise ValueError("fixed replay changed date window")
    old_m, old_s, old_d, _ = _metrics(old_replay)
    fixed_m, fixed_s, fixed_d, _ = _metrics(fixed_report)
    daily = pd.DataFrame(index=old_report.index)
    daily.index.name = "datetime"
    for col in ("account", "cash", "value", "return", "cost", "total_cost", "total_turnover"):
        daily[f"legacy_{col}"] = old_report[col]
        daily[f"fixed_{col}"] = fixed_report[col]
        daily[f"delta_{col}"] = fixed_report[col] - old_report[col]
    daily["legacy_net_return"] = old_report["return"] - old_report["cost"]
    daily["fixed_net_return"] = fixed_report["return"] - fixed_report["cost"]
    daily["delta_net_return"] = daily["fixed_net_return"] - daily["legacy_net_return"]

    def order_list(rows):
        return [
            [(o["stock_id"], o["direction"], o["amount"], o["deal_amount"]) for o in d["orders"]]
            for d in rows
        ]
    old_orders_by_day = order_list(old_orders)
    new_orders_by_day = order_list(fixed_orders)
    changed_order_days = [
        str(day.date()) for day, before, after in zip(
            old_report.index, old_orders_by_day, new_orders_by_day
        ) if before != after
    ]
    diffs = np.abs(daily["delta_account"].to_numpy(dtype=float))
    changed_nav_dates = [
        str(old_report.index[i].date()) for i, difference in enumerate(diffs)
        if difference > ACCOUNT_TOL_CNY
    ]
    if not changed_order_days or not changed_nav_dates:
        raise ValueError(
            "F1 FIX DID NOT CHANGE ORDERS AND NAV: do not call this a repaired "
            "execution replay; examine static quote coverage, signal, and cash constraints"
        )
    output = {
        "audit_type": "diagnostic_Qlib_replay_not_independent_execution_proof",
        "frozen_original_result_commit": SOURCE_SHA,
        "frozen_snapshot_token": manifest["snapshot_token"],
        "provider_fingerprint": manifest["provider_fingerprint"],
        "candidate_id": WINNER,
        "phase": 0,
        "signal_frozen_byte_sha256": _sha(signal_path),
        "report_frozen_byte_sha256": _sha(report_path),
        "decision_frozen_byte_sha256": _sha(decisions_path),
        "quote_codes_count": len(expanded_codes),
        "quote_codes_sha256": hashlib.sha256(
            ("\n".join(expanded_codes) + "\n").encode()
        ).hexdigest(),
        "fixed_quote_codes_include_f1_removed_holdings": True,
        "legacy_reproduction": "PASS",
        "first_changed_order_date": changed_order_days[0] if changed_order_days else None,
        "changed_order_days_count": len(changed_order_days),
        "first_changed_nav_date": changed_nav_dates[0] if changed_nav_dates else None,
        "changed_nav_days_count": len(changed_nav_dates),
        "legacy_metrics": old_m,
        "fixed_metrics": fixed_m,
        "metric_deltas": {
            key: fixed_m[key] - old_m[key] for key in old_m
            if isinstance(fixed_m.get(key), (int, float)) and
            isinstance(old_m.get(key), (int, float))
        },
        "legacy_diagnostics": old_d,
        "fixed_diagnostics": fixed_d,
        "legacy_sensitivity": old_s,
        "fixed_sensitivity": fixed_s,
        "further_validation_required": [
            "Independent corrected-order-and-price ledger, not this same-Qlib diagnostic replay",
            "True daily suspensions, time-aware ST/IPO limits, and auction liquidity",
            "Label maturation, point-in-time features and stock-universe provenance",
            "Other four winner and five baseline phases: each frozen signal, separate source hashes",
        ],
        "verdict": "INCONCLUSIVE_pending_independent_fixed_execution_and_external_market_checks",
    }

    out.mkdir(parents=True, exist_ok=False)
    old_replay.to_parquet(out / "legacy_diagnostic_report.parquet")
    fixed_report.to_parquet(out / "fixed_diagnostic_report.parquet")
    (out / "fixed_diagnostic_decisions.json").write_text(
        json.dumps(fixed_orders, indent=2, allow_nan=False) + "\n"
    )
    daily.to_csv(out / "daily_legacy_vs_fixed.csv")
    (out / "comparison.json").write_text(json.dumps(output, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        "legacy_reproduction": "PASS",
        "first_changed_order_date": output["first_changed_order_date"],
        "first_changed_nav_date": output["first_changed_nav_date"],
        "changed_order_days": len(changed_order_days),
        "legacy_sharpe": old_m["sharpe"],
        "fixed_sharpe": fixed_m["sharpe"],
        "legacy_CAGR": old_m["strategy_cagr"],
        "fixed_CAGR": fixed_m["strategy_cagr"],
        "execution_integrity": output["verdict"],
        "saved_under": str(out),
    }, indent=2))


if __name__ == "__main__":
    main()
