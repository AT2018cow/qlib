"""Historical replay adapter for the exact forward PaperPortfolio execution engine.

Input is an explicit sequence of signal-date / next-open sessions with PIT
ranking, normalized Qlib prices + factors, execution-date preopen ST snapshot,
and optional listing metadata. This does NOT alter frozen Stage-B artifacts.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

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
        )
        reports.append(report)
    return reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    raw = json.loads(args.input.read_text(encoding="utf-8"))
    reports = replay_paper_sessions(
        raw["sessions"], config=raw.get("config"),
        calendar=raw.get("calendar"), listing_dates=raw.get("listing_dates"),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"reports": reports}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
