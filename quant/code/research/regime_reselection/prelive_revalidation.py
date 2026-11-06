"""Frozen-signal revalidation with the production paper account engine.

Historical quote gaps are disclosed reference carries, never certified halts.
This tool writes only a new evidence directory and cannot approve live trading.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from quant_bootstrap import ensure_quant_import_paths

ensure_quant_import_paths()
import artifact_io as json
import numpy as np
import pandas as pd

from execution_limits import compute_limit_masks
from order_quantity import EXECUTION_RULE_VERSION, quantity_rule
from paper_accounting import verify_ledger
from paper_portfolio import PaperPortfolio
from portfolio_performance import portfolio_performance

BENCHMARKS = {"csi300": "SH000300", "csi500": "SH000905", "csi1000": "SH000852"}


def file_sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_signal(paths, calendar, start, end):
    """Join only exactly matching overlaps, then require full session coverage."""
    combined = None
    receipts = []
    overlap_rows = 0
    for path in paths:
        frame = pd.read_parquet(path)
        if list(frame.index.names) != ["datetime", "instrument"] or "score" not in frame:
            raise ValueError("signal must have datetime/instrument index and score")
        signal = frame["score"].sort_index()
        if signal.index.has_duplicates or not np.isfinite(signal.to_numpy(dtype=float)).all():
            raise ValueError("duplicate/non-finite frozen signal")
        if combined is None:
            combined = signal
        else:
            overlap = combined.index.intersection(signal.index)
            shared_dates = set(combined.index.get_level_values(0)) & set(signal.index.get_level_values(0))
            left = combined[combined.index.get_level_values(0).isin(shared_dates)]
            right = signal[signal.index.get_level_values(0).isin(shared_dates)]
            if not left.index.equals(right.index) or not np.array_equal(left.to_numpy(), right.to_numpy()):
                raise ValueError("frozen prediction overlap mismatch")
            overlap_rows += len(overlap)
            combined = pd.concat([combined, signal[~signal.index.isin(overlap)]]).sort_index()
        receipts.append(
            {
                "sha256": file_sha(path),
                "rows": len(signal),
                "first": str(signal.index[0][0])[:10],
                "last": str(signal.index[-1][0])[:10],
            }
        )
    if combined is None:
        raise ValueError("no frozen signal")
    dates = combined.index.get_level_values(0)
    combined = combined[(dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))]
    actual = sorted(str(x)[:10] for x in combined.index.get_level_values(0).unique())
    expected = [x for x in calendar if start <= x <= end]
    if not expected or expected[0] != start or expected[-1] != end or actual != expected:
        raise ValueError("signal/calendar coverage mismatch")
    return combined, {"inputs": receipts, "overlap_rows_equal": overlap_rows, "signal_days": len(actual)}


def _positive_mapping(frame, column):
    values = frame[column]
    return {str(k): float(v) for k, v in values.items() if math.isfinite(float(v)) and float(v) > 0}


def prepare_data(provider, signal_paths, market, start, end):
    import qlib
    from qlib.data import D

    qlib.init(provider_uri=str(provider), region="cn", kernels=1, skip_if_reg=True)
    calendar = [str(d)[:10] for d in D.calendar(start_time="2000-01-01", end_time=end)]
    signal, provenance = load_signal(signal_paths, calendar, start, end)
    names = sorted(set(signal.index.get_level_values("instrument")))
    benchmark = BENCHMARKS[market]
    print(f"[inputs] {market} frozen signal loaded, names={len(names)} dates={start}..{end}", flush=True)
    root = Path(provider)
    missing_files = [
        f"{name}/{field}"
        for name in names
        for field in ("open", "close", "factor", "volume")
        if not (root / "features" / name.lower() / f"{field}.day.bin").is_file()
    ]
    if missing_files:
        raise ValueError(
            f"incomplete provider: {len(missing_files)} missing instrument fields; first={missing_files[:5]}"
        )
    if not (root / "features" / benchmark.lower() / "close.day.bin").is_file():
        raise ValueError("missing benchmark close file")
    fields = ["$open", "$close", "$factor", "Ref($close,1)", "$volume"]
    quotes = D.features(names + [benchmark], fields, start_time=start, end_time=end, freq="day")
    print(f"[inputs] {market} quotes loaded: {len(quotes)} rows", flush=True)
    if quotes.index.has_duplicates:
        raise ValueError("duplicate quote index")
    bench = quotes.xs(benchmark, level="instrument")
    expected = [d for d in calendar if start <= d <= end]
    bench = bench.reindex(pd.to_datetime(expected))
    closes, prevs = bench["$close"], bench["Ref($close,1)"]
    bench_returns = (closes / prevs - 1).iloc[1:]
    if not np.isfinite(bench_returns.to_numpy()).all():
        raise ValueError("missing benchmark days")
    stock_quotes = quotes.drop(index=benchmark, level="instrument")
    by_day = {}
    for day, frame in stock_quotes.groupby(level="datetime", sort=True):
        f = frame.droplevel("datetime")
        by_day[str(day)[:10]] = {field: _positive_mapping(f, field) for field in fields}
    spans = D.list_instruments(D.instruments("all"), as_list=False)
    listing = {name: str(min(a for a, _ in spans[name]))[:10] for name in names if spans.get(name)}
    provider_receipts = {}
    for rel in ("calendars/day.txt", "instruments/all.txt", f"instruments/{market}.txt"):
        p = root / rel
        if p.is_file():
            provider_receipts[rel] = file_sha(p)
    # Bind all price/factor/volume files used, including retired constituents.
    quote_hashes = {}
    for i, name in enumerate(names + [benchmark], 1):
        for field in ("open", "close", "factor", "volume"):
            p = root / "features" / name.lower() / f"{field}.day.bin"
            if p.is_file():
                quote_hashes[f"{name}/{field}"] = file_sha(p)
        if i % 500 == 0:
            print(f"[inputs] {market} quote hashes {i}/{len(names)+1}", flush=True)
    provenance.update(
        {
            "provider_metadata": provider_receipts,
            "quote_files": quote_hashes,
            "n_names": len(names),
            "execution_rule_version": EXECUTION_RULE_VERSION,
        }
    )
    return {
        "calendar": calendar,
        "days": expected,
        "signal": signal,
        "quotes": by_day,
        "listing": listing,
        "bench": bench_returns,
        "provenance": provenance,
    }


def numerical_ledger(report):
    """Check conservation separately from the stronger valuation-evidence gate."""
    for name in ("opening_cash", "cash", "cost", "portfolio_value_close", "portfolio_value_pre_trade_open"):
        value = float(report[name])
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"invalid ledger summary: {name}")
    cash = report["opening_cash"]
    inventory = {s: float(p["shares"]) for s, p in report["opening_positions"].items()}
    fees = 0.0
    for side, sign in (("executed_sell", -1), ("executed_buy", 1)):
        for fill in report[side]:
            qty, price, fee = (float(fill[k]) for k in ("shares", "price", "cost"))
            if not all(math.isfinite(v) for v in (qty, price, fee)) or min(qty, price) <= 0 or fee < 0:
                raise ValueError("invalid fill")
            cash -= sign * qty * price + fee
            inventory[fill["instrument"]] = inventory.get(fill["instrument"], 0) + sign * qty
            fees += fee
            if cash < -0.011 or inventory[fill["instrument"]] < -1e-7:
                raise ValueError("intraday cash/inventory deficit")
    marks = report["position_marks"]
    if any(not math.isfinite(float(v[k])) or float(v[k]) <= 0 for v in marks.values() for k in ("shares", "price")):
        raise ValueError("invalid position mark")
    for name, qty in inventory.items():
        if not math.isclose(qty, marks.get(name, {}).get("shares", 0), abs_tol=1e-7, rel_tol=1e-9):
            raise ValueError("inventory conservation failure")
    if set(marks) - set(inventory):
        raise ValueError("unexplained position")
    nav = report["cash"] + sum(v["shares"] * v["price"] for v in marks.values())
    if max(abs(cash - report["cash"]), abs(fees - report["cost"]), abs(nav - report["portfolio_value_close"])) > 0.011:
        raise ValueError("cash/fees/NAV conservation failure")


def replay(data, candidate, *, account=1_000_000, slippage_bps=0, progress_every=250, engine_history_limit=0):
    pp = PaperPortfolio(topk=candidate["topk"], nd=candidate["dropn"], initial_cash=account)
    signal_days = {
        str(day)[:10]: values.droplevel("datetime")
        for day, values in data["signal"].groupby(level="datetime", sort=True)
    }
    rows, reports, gaps, orders = [], [], [], []
    previous_nav = float(account)
    verified_sessions = 0
    peak = float(account)
    drawdown_length = max_drawdown_length = 0
    days = data["days"]
    started = time.monotonic()
    for i, (signal_day, day) in enumerate(zip(days, days[1:]), 1):
        ranking = [(str(s), float(v)) for s, v in signal_days[signal_day].items()]
        pp.plan_signal(signal_day, day, ranking, metadata={"evidence": "ST_HISTORY_MISSING_REFERENCE_ONLY"})
        active = sorted(
            set(pp.positions) | set(pp.pending_signal["planned_buy"]) | set(pp.pending_signal["planned_sell"])
        )
        q = data["quotes"].get(day, {k: {} for k in ("$open", "$close", "$factor", "Ref($close,1)", "$volume")})
        opens, closes, factors, prevs = (q[k] for k in ("$open", "$close", "$factor", "Ref($close,1)"))
        lb, ls = compute_limit_masks(
            active,
            [day] * len(active),
            [opens.get(s, np.nan) for s in active],
            [prevs.get(s, np.nan) for s in active],
            [s not in closes or s not in q["$volume"] for s in active],
            factors=[factors.get(s, np.nan) for s in active],
            calendar=data["calendar"],
            listing_dates=data["listing"],
        )
        opening = copy.deepcopy(pp.positions)
        # Account decisions never read history. Retain every ledger externally,
        # keeping only the rollback state needed for this isolated replay step.
        pp.history = pp.history[-engine_history_limit:] if engine_history_limit else []
        report = pp.execute_pending(
            day,
            {s: opens[s] for s in active if s in opens},
            buy_tradable={s: not bool(v) for s, v in zip(active, lb)},
            sell_tradable={s: not bool(v) for s, v in zip(active, ls)},
            close_prices={s: closes[s] for s in active if s in closes},
            factors={s: factors[s] for s in active if s in factors},
            strict_valuation=False,
            slippage_bps=slippage_bps,
        )
        report["opening_marks"] = {
            s: {
                "shares": p["shares"],
                "price": opens.get(s, p["last_price"]),
                "reason": "EXCHANGE_OPEN" if s in opens else "UNVERIFIED_REFERENCE_CARRY",
            }
            for s, p in opening.items()
        }
        for s, mark in report["position_marks"].items():
            mark["reason"] = "EXCHANGE_CLOSE" if s in closes else "UNVERIFIED_REFERENCE_CARRY"
        missing = [
            {"date": day, "instrument": s, "field": field}
            for field, wanted, mapping in (("open", opening, opens), ("close", report["position_marks"], closes))
            for s in wanted
            if s not in mapping
        ]
        gaps.extend(missing)
        numerical_ledger(report)
        if not missing:
            verify_ledger(report)
            verified_sessions += 1
        for side in ("executed_buy", "executed_sell"):
            for fill in report[side]:
                if side == "executed_buy":
                    physical = float(fill["shares"]) * float(fill["factor"])
                    rule = quantity_rule(fill["instrument"], day)
                    if physical + 1e-5 < rule.minimum or abs(physical / rule.step - round(physical / rule.step)) > 1e-5:
                        raise ValueError("illegal buy quantity")
                orders.append({"date": day, "side": side, **fill})
        nav = float(report["portfolio_value_close"])
        fee = float(report["cost"])
        net = nav / previous_nav - 1
        traded = sum(f["shares"] * f["price"] for side in ("executed_buy", "executed_sell") for f in report[side])
        rows.append(
            {
                "datetime": pd.Timestamp(day),
                "account": nav,
                "return": net + fee / previous_nav,
                "cost": fee / previous_nav,
                "bench": float(data["bench"].loc[pd.Timestamp(day)]),
                "turnover": traded / previous_nav,
                "cash": report["cash"],
                "n_positions": report["n_positions"],
                "max_single_weight": max(
                    (p["shares"] * p["price"] / nav for p in report["position_marks"].values()), default=0
                ),
                "blocked_buy": len(report["blocked_buy"]),
                "blocked_sell": len(report["blocked_sell"]),
                "quote_gap_count": len(missing),
            }
        )
        if nav >= peak:
            peak, drawdown_length = nav, 0
        else:
            drawdown_length += 1
            max_drawdown_length = max(max_drawdown_length, drawdown_length)
        reports.append(report)
        previous_nav = nav
        if progress_every and (i % progress_every == 0 or i == len(days) - 1):
            print(
                f"[replay] {candidate['id']} extra={slippage_bps}bp {i}/{len(days)-1} date={day} elapsed={time.monotonic()-started:.1f}s",
                flush=True,
            )
    report_frame = pd.DataFrame(rows).set_index("datetime")
    metrics = portfolio_performance(report_frame, initial_cash=account, backtest_start=days[1])
    annual = {}
    for year, frame in report_frame.groupby(report_frame.index.year):
        prior = (
            float(account)
            if frame.index[0] == report_frame.index[0]
            else float(report_frame.loc[report_frame.index < frame.index[0], "account"].iloc[-1])
        )
        annual[str(year)] = portfolio_performance(frame, initial_cash=prior, backtest_start=frame.index[0])
    metrics.update(
        {
            "candidate": candidate,
            "account": account,
            "slippage_bps": slippage_bps,
            "engine": "PRODUCTION_PAPER_PORTFOLIO",
            "evidence_status": "REFERENCE_ONLY",
            "st_history_verified": False,
            "auction_liquidity_verified": False,
            "valuation_verified_sessions": verified_sessions,
            "quote_gap_count": len(gaps),
            "mean_turnover": float(report_frame.turnover.mean()),
            "total_fees": sum(r["cost"] for r in reports),
            "max_single_weight": float(report_frame.max_single_weight.max()),
            "mean_cash_fraction": float((report_frame.cash / report_frame.account).mean()),
            "blocked_buy": int(report_frame.blocked_buy.sum()),
            "blocked_sell": int(report_frame.blocked_sell.sum()),
            "longest_underwater_sessions": max_drawdown_length,
            "annual": annual,
            "promotion_eligible": False,
            "live_approved": False,
        }
    )
    return {
        "metrics": metrics, "report": report_frame, "sessions": reports,
        "orders": orders, "gaps": gaps, "final_state": pp.to_dict(),
    }


def write_result(result, output):
    root = Path(output)
    root.mkdir(parents=True, exist_ok=False)
    result["report"].to_parquet(root / "report.parquet")
    pd.DataFrame(result["orders"]).to_parquet(root / "orders.parquet", index=False)
    for name in ("metrics", "gaps"):
        (root / f"{name}.json").write_text(json.dumps(result[name], indent=2, ensure_ascii=False) + "\n")
    # Full ledger retained as JSONL to make each day's cash/inventory independently inspectable.
    with (root / "sessions.jsonl").open("w") as stream:
        for row in result["sessions"]:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True, type=Path)
    parser.add_argument("--signal", required=True, action="append", type=Path)
    parser.add_argument("--register", required=True, type=Path)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--start", default="2018-01-02")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--slippage", default="0,5,10")
    args = parser.parse_args()
    register = json.loads(args.register.read_text())
    candidate = next(c for c in register["candidates"] if c["id"] == args.candidate)
    data = prepare_data(args.provider, args.signal, candidate["market"], args.start, args.end)
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "inputs.json").write_text(json.dumps(data["provenance"], indent=2) + "\n")
    for bps in [float(x) for x in args.slippage.split(",")]:
        result = replay(data, candidate, account=register["account"], slippage_bps=bps)
        write_result(result, args.out / f"extra_{bps:g}bp")
        print(
            json.dumps(
                {
                    k: result["metrics"][k]
                    for k in ("strategy_cagr", "strategy_max_drawdown", "sharpe", "quote_gap_count")
                }
            )
        )


if __name__ == "__main__":
    main()
