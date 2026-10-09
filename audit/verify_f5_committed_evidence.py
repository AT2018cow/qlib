#!/usr/bin/env python3
"""Offline, read-only verifier for published winner phase-0 F5 evidence.

Only Python stdlib; does NOT run Qlib, recalculate fills from private provider,
verify source-of-truth market exports, or certify real-world execution.
A successful check means the *published* report, orders, daily ledger, SHA
pins, and reported metrics agree. External market validity remains BLOCKED.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import defaultdict
from datetime import date
from pathlib import Path

FROZEN_SIGNAL_SHA = "189d06cce7cdc52b1908439fb18fd9e3f1b8dcc8875a87bf5b6ec75d82e0278e"
FROZEN_PROVIDER_FINGERPRINT = "cd2e68f571c6bd19fbb2b79089e891bb1c43b96ab1fcdb4d2b2a9b1fdd9ce5d5"
INITIAL_ACCOUNT = 100_000_000.0
ACCOUNT_TOL = 0.01
RATE_TOL = 1e-10


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError("F5 published evidence FAIL: " + message)


def number(value, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"F5 published evidence FAIL: {name}: not numeric") from exc
    require(math.isfinite(result), name + ": not finite")
    return result


def near(actual, expected, *, tolerance: float, label: str) -> None:
    require(abs(number(actual, label) - number(expected, label)) <= tolerance,
            f"{label}: mismatch {actual} vs {expected}, tolerance={tolerance}")


def quantity_tolerance(held: float) -> float:
    return max(1e-6, abs(held) * 1e-10)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as reader:
        for part in iter(lambda: reader.read(1024 * 1024), b""):
            digest.update(part)
    return digest.hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as stream:
        result = list(csv.DictReader(stream))
    require(len(result) == 424 if path.name != "execution_orders.csv" else len(result) == 1696,
            path.name + ": wrong row count")
    return result


def metrics(net_returns: list[float], accounts: list[float], dates: list[str]) -> dict:
    """Published Stage-B convention: 238 annual sessions, sample standard dev."""
    require(len(net_returns) == len(accounts) == len(dates) == 424, "metric calendar length")
    mean = sum(net_returns) / len(net_returns)
    std = math.sqrt(sum((x - mean) ** 2 for x in net_returns) / (len(net_returns) - 1))
    require(std > 0, "returns have zero volatility")
    high_water = INITIAL_ACCOUNT
    max_dd = 0.0
    for account in accounts:
        high_water = max(high_water, account)
        max_dd = min(max_dd, account / high_water - 1.0)
    duration = (date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])).days
    require(duration > 0, "nonpositive period")
    return {
        "sharpe": math.sqrt(238) * mean / std,
        "strategy_cagr": (accounts[-1] / INITIAL_ACCOUNT) ** (365.2425 / duration) - 1.0,
        "strategy_max_drawdown": max_dd,
        "annual_volatility": math.sqrt(238) * std,
        "strategy_total_return": accounts[-1] / INITIAL_ACCOUNT - 1.0,
    }


def verify_repo(root: Path) -> dict:
    base = root / "audit/evidence/winner_phase0_fixed"
    raw = root / "audit/evidence/winner_phase0/raw"
    manifest = read_json(base / "f5_hardened_manifest.json")
    summary = read_json(base / "f5_hardened_summary.json")
    comparison = read_json(base / "comparison.json")
    require(manifest["fixed_mode"] is True, "not fixed execution mode")
    require(manifest["provider_fingerprint"] == FROZEN_PROVIDER_FINGERPRINT,
            "frozen provider identity changed")
    require(summary["verdicts"]["f5_internal_evidence_gate"] == "PASS",
            "hardened internal F5 verdict missing")
    require(summary["verdicts"]["overall"] == "BLOCKED" and
            summary["verdicts"]["execution_integrity"] == "BLOCKED",
            "external execution falsely certified")

    files = {
        "report": base / "fixed_diagnostic_report.parquet",
        "signal": raw / "signal.parquet",
        "decisions": base / "fixed_diagnostic_decisions.json",
        "calendar": raw / "day.txt",
        "instruments": raw / "csi1000_instruments.txt",
    }
    for label, path in files.items():
        identity = manifest["verified_inputs"][label]
        require(identity["match"] is True and
                identity["expected_sha256"] == identity["actual_sha256"],
                label + ": manifest identity mismatch")
        require(sha256(path) == identity["actual_sha256"], label + ": committed byte SHA mismatch")
        require(path.stat().st_size == identity["size_bytes"],
                label + ": committed byte size mismatch")
        require(summary["artifacts"][label]["actual_sha256"] == identity["actual_sha256"],
                label + ": hardened summary identity mismatch")
    require(manifest["verified_inputs"]["signal"]["actual_sha256"] == FROZEN_SIGNAL_SHA,
            "original frozen signal changed")
    market = manifest["verified_inputs"]["market"]
    require(market["match"] is True and
            market["actual_sha256"] == market["expected_sha256"] and
            len(market["actual_sha256"]) == 64,
            "unmatched market-export SHA in manifest")
    require(summary["artifacts"]["market"]["actual_sha256"] == market["actual_sha256"],
            "market identity differs between hardened summaries")
    require(manifest["verified_inputs"]["provider_snapshot"]["token_match"] is True and
            manifest["verified_inputs"]["provider_snapshot"]["provider_fingerprint_match"] is True,
            "provider snapshot check not reported")
    # Original private provider market export is not committed; never claim its
    # content has been independently hashed by this offline verifier.

    daily = read_csv(base / "daily_legacy_vs_fixed.csv")
    ledger = read_csv(base / "account_rebuild_daily.csv")
    orders = read_csv(base / "execution_orders.csv")
    decision_audit = read_csv(base / "decision_vs_signal.csv")
    decisions = read_json(base / "fixed_diagnostic_decisions.json")
    original_decisions = read_json(raw / "decisions.json")
    require(len(decisions) == len(original_decisions) == 424, "decision count")
    require(summary["counts"]["decisions"] == 424 and summary["counts"]["orders"] == 1696,
            "hardened summary order coverage")
    for key, count in summary["tradability_and_amounts"].items():
        if key != "orders_checked":
            require(count == 0, "hardened " + key + " is nonzero")
    require(summary["tradability_and_amounts"]["orders_checked"] == 1696,
            "hardened orders_checked")

    order_by_day = defaultdict(list)
    for order in orders:
        order_by_day[order["date"]].append(order)
        for flag in ("tradable", "factor_match", "lot_ok"):
            require(order[flag] == "True", "order " + flag + " FAIL")
    position = {}
    order_count = 0
    cash_fee = 0.0
    cash_turnover = 0.0
    previous_account = INITIAL_ACCOUNT
    fixed_net_returns = []
    fixed_accounts = []
    legacy_net_returns = []
    old_stall_orders = 0
    fixed_stall_orders = 0
    nav_changed = []
    order_changed = []
    for i, (row, acct, audit, dec, old_dec) in enumerate(
        zip(daily, ledger, decision_audit, decisions, original_decisions)
    ):
        day = row["datetime"]
        require(row["datetime"] == acct["date"] == audit["exec_day"] ==
                dec["start_time"][:10] == old_dec["start_time"][:10],
                f"day {i} has inconsistent dates")
        require(audit["match"] == "True" and
                audit["expected_sells"] == audit["actual_sells"] and
                audit["expected_buys"] == audit["actual_buys"],
                "signal/order reconstruction mismatch on " + day)
        require(len(order_by_day[day]) == len(dec["orders"]),
                "order count differs from serialized decisions on " + day)
        require(len(order_by_day[day]) > 0, "fixed path lacks orders on " + day)
        new_orders = dec["orders"]
        old_orders = old_dec["orders"]
        old_key = [(o["stock_id"], o["direction"], o["amount"], o["deal_amount"])
                   for o in old_orders]
        new_key = [(o["stock_id"], o["direction"], o["amount"], o["deal_amount"])
                   for o in new_orders]
        if old_key != new_key:
            order_changed.append(day)
        if "2025-07-02" <= day <= "2026-06-29":
            old_stall_orders += len(old_orders)
            fixed_stall_orders += len(new_orders)
        daily_fee = 0.0
        daily_turnover = 0.0
        for o, entry in zip(new_orders, order_by_day[day]):
            stock = o["stock_id"]
            side = "buy" if int(o["direction"]) == 1 else "sell"
            require(stock == entry["stock"] and side == entry["side"],
                    "decision/order stock or side differs on " + day)
            requested = number(entry["requested_qty"], "requested_qty")
            filled = number(entry["filled_qty"], "filled_qty")
            near(requested, o["amount"],
                 tolerance=quantity_tolerance(requested), label="decision requested quantity")
            near(filled, o["deal_amount"],
                 tolerance=quantity_tolerance(filled), label="decision filled quantity")
            fee = number(entry["fee_cny"], "fee_cny")
            trade_value = number(entry["turnover_cny"], "turnover_cny")
            require(fee >= 0 and trade_value > 0, "negative fee/nonpositive order value")
            daily_fee += fee
            daily_turnover += trade_value
            if side == "sell":
                held = position.get(stock, 0.0)
                near(requested, held,
                     tolerance=quantity_tolerance(held), label="pre-sell holding")
                near(filled, requested,
                     tolerance=quantity_tolerance(requested), label="full sell")
                position.pop(stock)
            else:
                near(filled, requested,
                     tolerance=quantity_tolerance(requested), label="full buy")
                position[stock] = position.get(stock, 0.0) + filled
            order_count += 1
        cash_fee += daily_fee
        cash_turnover += daily_turnover
        near(cash_fee, row["fixed_total_cost"],
             tolerance=ACCOUNT_TOL, label="cumulative fee on " + day)
        near(cash_turnover, row["fixed_total_turnover"],
             tolerance=ACCOUNT_TOL, label="cumulative turnover on " + day)
        near(daily_fee / previous_account, row["fixed_cost"],
             tolerance=RATE_TOL, label="daily fee rate " + day)
        near(daily_fee, acct["fees"],
             tolerance=ACCOUNT_TOL, label="ledger daily fees " + day)
        near(acct["reconstructed_account"], row["fixed_account"],
             tolerance=ACCOUNT_TOL, label="reconstructed account " + day)
        near(row["fixed_cash"] + float(row["fixed_value"]), row["fixed_account"],
             tolerance=ACCOUNT_TOL, label="cash + holdings " + day)
        near(acct["reconstructed_gross_return"], row["fixed_return"],
             tolerance=RATE_TOL, label="ledger return " + day)
        near(float(row["fixed_return"]) - float(row["fixed_cost"]), row["fixed_net_return"],
             tolerance=RATE_TOL, label="net return " + day)
        current_account = number(row["fixed_account"], "fixed_account")
        require(current_account > 0 and number(row["fixed_cash"], "fixed_cash") >= -ACCOUNT_TOL,
                "nonpositive account/negative cash " + day)
        if abs(number(row["delta_account"], "delta_account")) > ACCOUNT_TOL:
            nav_changed.append(day)
        previous_account = current_account
        fixed_net_returns.append(number(row["fixed_net_return"], "fixed_net_return"))
        fixed_accounts.append(current_account)
        legacy_net_returns.append(number(row["legacy_net_return"], "legacy_net_return"))
    require(order_count == len(orders) == 1696, "missing or extra orders")
    require(len(position) == 20, "unexpected final portfolio size")
    require(old_stall_orders == 0 and fixed_stall_orders == 950,
            "F1 stalled period not reproduced")
    require(order_changed and order_changed[0] == "2025-07-01",
            "first changed order date differs")
    require(nav_changed and nav_changed[0] == "2025-06-30",
            "first changed account date differs")
    require(len(order_changed) == comparison["changed_order_days_count"] and
            len(nav_changed) == comparison["changed_nav_days_count"],
            "changed-day counts differ")
    computed = metrics(fixed_net_returns, fixed_accounts,
                       [r["datetime"] for r in daily])
    for key, value in computed.items():
        near(value, comparison["fixed_metrics"][key],
             tolerance=1e-11, label="fixed " + key)
    require(summary["verdicts"]["f5_internal_evidence_gate"] == "PASS",
            "F5 must not be silently promoted")
    return {
        "status": "PASS_PUBLISHED_SIMULATION_EVIDENCE_ONLY",
        "days": len(daily), "orders": order_count,
        "stall_legacy_orders": old_stall_orders,
        "stall_fixed_orders": fixed_stall_orders,
        "market_byte_provenance": "DECLARED_NOT_INDEPENDENTLY_RECHECKED",
        "market_execution": "BLOCKED",
        "sharpe": computed["sharpe"],
        "strategy_cagr": computed["strategy_cagr"],
        "max_drawdown": computed["strategy_max_drawdown"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    print(json.dumps(verify_repo(args.repo_root.resolve()), sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
