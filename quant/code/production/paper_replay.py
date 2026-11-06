"""Historical replay adapter for the exact forward PaperPortfolio execution engine.

Input is an explicit sequence of signal-date / next-open sessions with PIT
ranking, normalized Qlib prices + factors, execution-date preopen ST snapshot,
and optional listing metadata. This does NOT alter frozen Stage-B artifacts.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quant_bootstrap import ensure_quant_import_paths

ensure_quant_import_paths()
import artifact_io as json

from paper_portfolio import PaperPortfolio


def replay_paper_sessions(rows, *, config=None, calendar=None, listing_dates=None):
    """Run historical execution using the same class/rules as forward paper.

    No fill-volume/queue model: deliberate small-notional research assumption.
    """
    from execution_limits import compute_limit_masks

    pp = PaperPortfolio(**(config or {}))
    reports = []
    last_date = ""
    for row in rows:
        signal_date = str(row["signal_date"])
        execution_date = str(row["execution_date"])
        if not (last_date < execution_date and signal_date < execution_date):
            raise ValueError("dates must be strictly ordered and signals precede execution")
        last_date = execution_date
        snapshot = row.get("execution_st_snapshot")
        if not isinstance(snapshot, dict) or snapshot.get("date") != execution_date or not isinstance(snapshot.get("st_symbols"), list) or not snapshot.get("source"):
            raise ValueError("PIT execution-date ST evidence is required, including an explicit empty list")
        pp.plan_signal(signal_date, execution_date, row["ranking"])
        pending = pp.pending_signal
        instruments = sorted(set(pp.positions).union(pending["planned_sell"], pending["planned_buy"]))
        opens = row["open_prices"]
        closes = row["close_prices"]
        prevs = row["previous_close"]
        factors = row["factors"]
        suspended = set(row.get("confirmed_halts", []))
        for sym in instruments:
            if sym not in prevs:
                raise ValueError(f"missing previous close for {sym}")
        limit_buy, limit_sell = compute_limit_masks(
            instruments, [execution_date] * len(instruments),
            [opens.get(s, float("nan")) for s in instruments],
            [prevs[s] for s in instruments],
            [s not in closes for s in instruments],
            listing_dates=listing_dates or {},
            calendar=calendar,
            st_symbols=set(snapshot["st_symbols"]),
            factors=[factors.get(s, float("nan")) for s in instruments],
        )
        report = pp.execute_pending(
            execution_date,
            opens,
            buy_tradable={s: not bool(v) for s, v in zip(instruments, limit_buy)},
            sell_tradable={s: not bool(v) for s, v in zip(instruments, limit_sell)},
            close_prices=closes,
            factors=factors,
            strict_valuation=True,
            confirmed_halts=suspended,
            halt_evidence=row.get("halt_evidence", {}),
            liquidity_evidence=row.get("liquidity_evidence"),
            max_participation=row.get("max_participation"),
            slippage_bps=row.get("slippage_bps", 0.0),
        )
        reports.append(report)
    return reports


def replay_capacity_scenarios(
    rows, *, account_sizes, participation_rates, slippage_grid, config=None, calendar=None, listing_dates=None
):
    """Evaluate frozen signals across explicit capital/liquidity assumptions."""
    rows = list(rows)
    if not rows or any(not r.get("liquidity_evidence") for r in rows):
        raise ValueError("capacity evaluation requires dated auction evidence for every session")
    results = []
    for capital in account_sizes:
        for rate in participation_rates:
            for slippage in slippage_grid:
                sessions = [{**r, "max_participation": rate, "slippage_bps": slippage} for r in rows]
                reports = replay_paper_sessions(
                    sessions,
                    config={**(config or {}), "initial_cash": capital},
                    calendar=calendar,
                    listing_dates=listing_dates,
                )
                peak, maxdd = float(capital), 0.0
                for report in reports:
                    value = report["portfolio_value_close"]
                    peak = max(peak, value)
                    maxdd = min(maxdd, value / peak - 1)
                results.append(
                    {
                        "initial_cash": capital,
                        "participation": rate,
                        "slippage_bps": slippage,
                        "total_return": reports[-1]["portfolio_value_close"] / capital - 1,
                        "max_drawdown": maxdd,
                        "cost": sum(r["cost"] for r in reports),
                        "filled_orders": sum(len(r["executed_buy"]) + len(r["executed_sell"]) for r in reports),
                        "execution_mode": "OPEN_AUCTION_CONSTRAINED",
                        "broker_fills_certified": False,
                    }
                )
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--capacity-grid", type=Path, help="JSON account_sizes / participation_rates / slippage_bps arrays"
    )
    args = parser.parse_args()
    raw = json.loads(args.input.read_text(encoding="utf-8"))
    if args.capacity_grid:
        grid = json.loads(args.capacity_grid.read_text())
        reports = replay_capacity_scenarios(
            raw["sessions"],
            account_sizes=grid["account_sizes"],
            participation_rates=grid["participation_rates"],
            slippage_grid=grid["slippage_bps"],
            config=raw.get("config"),
            calendar=raw.get("calendar"),
            listing_dates=raw.get("listing_dates"),
        )
    else:
        reports = replay_paper_sessions(
            raw["sessions"],
            config=raw.get("config"),
            calendar=raw.get("calendar"),
            listing_dates=raw.get("listing_dates"),
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"reports": reports}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
