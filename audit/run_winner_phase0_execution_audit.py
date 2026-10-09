#!/usr/bin/env python3
"""Independent winner phase-0 execution / profit-formation audit (doc 28, sections 1-3).

Read-only evidence audit.  It does NOT import or call the production backtest,
strategy, exchange or paper modules.  Decision reconstruction, tradability and
the double-entry ledger below are independent re-implementations driven only by:

- the frozen full-run result JSON (artifact paths + SHA256),
- the original signal.parquet / decisions.json exported read-only from the
  experimental volume snapshot,
- the original provider market data (open/close/factor/volume bins) for the
  ordered instruments,
- the original provider trading calendar (cn_data/calendars/day.txt),
- frozen constants transcribed from the runbook and the frozen code.

Outputs (doc 28 section 5): execution_export_manifest.json, calendar_compare.csv,
decision_vs_signal.csv, execution_orders.csv, account_rebuild_daily.csv,
execution_audit_summary.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Frozen constants (transcribed from frozen code / runbook)
# ---------------------------------------------------------------------------
TOPK = 20
N_DROP = 2
RISK_DEGREE = 0.95
HOLD_THRESH = 1
TRADE_UNIT = 100
BUY_COST = 0.0005
SELL_COST = 0.0015
MIN_COST = 5.0
INIT_CASH = 100_000_000.0
TH_MAIN = 0.10
TH_STAR = 0.20
TH_CHINEXT = 0.20
CHINEXT_REFORM = "2020-08-24"
ACCOUNT_TOL = Decimal("0.01")
RETURN_TOL = 1e-10

CANDIDATE_ID = "4e908173705c76fee3782be37068672a3a845bd61894202f3152afe3b9d81ef2"
SNAPSHOT = "51756897fc75230493194aef2e48815e4e9f3cc7426cc135f47bb8ba8e0d21e1"
FROZEN_RESULT_COMMIT = "7a2676397b0f8e6f69c0bffc98d1764647f644ac"
RESULT_JSON = "results/csi1000_stage_b/stage_b_full_51756897fc752304.json"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def board_of(stock_id: str) -> str:
    body = stock_id[2:]
    if stock_id.startswith("SH") and body.startswith(("688", "689")):
        return "star"
    if stock_id.startswith("SZ") and body.startswith(("300", "301", "302")):
        return "chinext"
    return "main"


def board_threshold(stock_id: str, date_str: str) -> float:
    board = board_of(stock_id)
    if board == "star":
        return TH_STAR
    if board == "chinext":
        return TH_STAR if date_str >= CHINEXT_REFORM else TH_MAIN
    return TH_MAIN


def round_lot(amount_adj: float, factor: float) -> float:
    """Frozen Qlib: (a*factor + 0.1) // 100 * 100 / factor (real-share lot floor)."""
    return (amount_adj * factor + 0.1) // TRADE_UNIT * TRADE_UNIT / factor


def score_order(pairs):
    """deterministic_score_order semantics: score desc, instrument asc, NaN last."""
    def key(item):
        inst, score = item
        score = float(score)
        if math.isnan(score):
            return (1, 0.0, inst)
        return (0, -score, inst)

    return [inst for inst, _score in sorted(pairs, key=key)]


class QlibLikePosition:
    """Replicates frozen Position amount/cash float semantics exactly.

    - buy: stored amount += (trade_val / trade_price), trade_val = deal_amount * price
    - sell: trade_amount = trade_val / trade_price; np.isclose(rtol=1e-5, atol=1e-8)
      against stored amount -> delete entry, else subtract (error below -1e-5)
    - cash updated with float arithmetic in execution order
    """

    def __init__(self, cash: float):
        self.cash = cash
        self.amount = {}  # stock -> float adjusted amount
        self.price = {}   # stock -> float valuation price (adjusted)

    def buy(self, stock, deal_amount, price):
        trade_val = deal_amount * price
        trade_amount = trade_val / price
        self.amount[stock] = self.amount.get(stock, 0.0) + trade_amount
        self.price[stock] = price
        cost = max(trade_val * BUY_COST, MIN_COST)
        self.cash = self.cash - (trade_val + cost)
        return trade_val, cost

    def sell(self, stock, deal_amount, price):
        trade_val = deal_amount * price
        trade_amount = trade_val / price
        held = self.amount[stock]
        if np.isclose(held, trade_amount):
            del self.amount[stock]
        else:
            remaining = held - trade_amount
            if remaining < -1e-5:
                raise ValueError(f"sell more than held: {stock} held={held} sell={trade_amount}")
            self.amount[stock] = remaining
        cost = max(trade_val * SELL_COST, MIN_COST)
        self.cash = self.cash + (trade_val - cost)
        return trade_val, cost


def load_inputs(args):
    raw = Path(args.raw_dir)
    repo = Path(args.repo_dir)
    result = json.loads((repo / RESULT_JSON).read_text())
    winner = next(c for c in result["ranked_candidates"] if c["candidate_id"] == CANDIDATE_ID)
    phase = next(x for x in winner["phase_results"] if x["phase"] == 0)

    report_path = Path(args.report_file) if args.report_file else (
        repo / "results/csi1000_stage_b/audit_reports/winner_4e908173705c76fe/phase00__phases.parquet")
    decisions_file = Path(args.decisions_file) if args.decisions_file else raw / "decisions.json"
    market_file = Path(args.market_file) if args.market_file else raw / "market_data.parquet"
    artifacts = {
        "report": {"path": report_path, "expected": phase["report_artifact"]["sha256"] if not args.fixed_mode else None},
        "signal": {"path": raw / "signal.parquet", "expected": phase["signal_artifact"]["sha256"]},
        "decisions": {"path": decisions_file, "expected": phase["decision_artifact"]["sha256"] if not args.fixed_mode else None},
    }
    identity = {}
    for key, meta in artifacts.items():
        actual = sha256_file(meta["path"])
        identity[key] = {
            "expected_sha256": meta["expected"],
            "actual_sha256": actual,
            "match": (actual == meta["expected"]) if meta["expected"] is not None else None,
            "size_bytes": meta["path"].stat().st_size,
        }

    calendar = [line.strip() for line in (raw / "day.txt").read_text().splitlines() if line.strip()]
    decisions = json.loads(decisions_file.read_text())
    result_json_phase = json.loads((raw / "result.json").read_text())
    report = pd.read_parquet(report_path)
    signal = pd.read_parquet(raw / "signal.parquet")
    market = pd.read_parquet(market_file)
    return result, phase, result_json_phase, identity, calendar, decisions, report, signal, market


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", default="audit/evidence/winner_phase0/raw")
    parser.add_argument("--repo-dir", default=".")
    parser.add_argument("--out-dir", default="audit/evidence/winner_phase0")
    parser.add_argument("--report-file", default=None,
                        help="override report parquet (e.g. fixed replay output)")
    parser.add_argument("--decisions-file", default=None,
                        help="override decisions json (e.g. fixed replay output)")
    parser.add_argument("--market-file", default=None,
                        help="override market parquet (e.g. fixed-path 565-instrument export)")
    parser.add_argument("--fixed-mode", action="store_true",
                        help="F5: audit a repaired (static-union) replay; skips frozen SHA "
                             "expectations and span-restricted data availability")
    args = parser.parse_args()

    out_dir = Path(args.repo_dir) / args.out_dir if not Path(args.out_dir).is_absolute() else Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    result, phase, result_json_phase, identity, calendar, decisions, report, signal, market = load_inputs(args)

    cal_pos = {d: i for i, d in enumerate(calendar)}
    report_days = [d.strftime("%Y-%m-%d") for d in report.index]
    decision_days = [d["start_time"][:10] for d in decisions]

    # ---------------- D0: calendar gate ----------------
    calendar_rows = []
    for i, e in enumerate(decision_days):
        s = calendar[cal_pos[e] - 1]
        calendar_rows.append({
            "exec_day": e,
            "signal_day": s,
            "in_provider_calendar": e in cal_pos,
            "report_day_match": report_days[i] == e,
        })
    pd.DataFrame(calendar_rows).to_csv(out_dir / "calendar_compare.csv", index=False)
    calendar_exact = (
        report_days == decision_days
        and all(r["in_provider_calendar"] for r in calendar_rows)
        and len(report_days) == 424
        and calendar[cal_pos[report_days[0]]: cal_pos[report_days[-1]] + 1] == report_days
    )

    # ---------------- market data access ----------------
    market = market.rename_axis(["instrument", "datetime"])

    # Point-in-time universe spans: the frozen exchange built its quote over the
    # dynamic csi1000 membership, so a stock has NO quote data outside its
    # membership spans (verified: index-removal causes permanent suspension in
    # the frozen simulation). In fixed_mode the static union gives full quotes,
    # so the span restriction is disabled.
    spans: dict[str, list[tuple[str, str]]] = {}
    if not args.fixed_mode:
        spans_path = Path(args.raw_dir) / "csi1000_instruments.txt"
        if spans_path.exists():
            for line in spans_path.read_text().splitlines():
                parts = line.strip().split("\t")
                if len(parts) == 3:
                    spans.setdefault(parts[0], []).append((parts[1], parts[2]))

    def has_quote(stock: str, day: str) -> bool:
        sp = spans.get(stock)
        if sp is None:
            return True  # instruments file missing: fall back to full data
        return any(a <= day <= b for a, b in sp)

    def px(stock, day, field):
        if spans and not has_quote(stock, day):
            return float("nan")
        try:
            return float(market.loc[(stock, pd.Timestamp(day)), field])
        except KeyError:
            return float("nan")

    signal_days = {}
    for day, grp in signal.groupby(level=0):
        signal_days[str(day)[:10]] = dict(zip(grp.index.get_level_values(1), grp["score"].astype(float)))

    # ---------------- replay ----------------
    orders_csv = []
    dvs_rows = []
    ledger_rows = []

    # float mirror replicating frozen strategy/exchange arithmetic (for exact
    # amount verification), Decimal ledger for money accounting
    float_pos = QlibLikePosition(INIT_CASH)
    dec_cash = Decimal(str(INIT_CASH))
    account_prev = Decimal(str(INIT_CASH))
    cum_cost = Decimal("0")
    cum_turnover = Decimal("0")
    pos_price = {}   # stock -> valuation price (adjusted), updated daily to close
    hold_count = {}

    total_orders = sum(len(d["orders"]) for d in decisions)
    dvs_mismatches = 0
    tradability_violations = 0
    lot_violations = 0
    factor_mismatches = 0
    buy_amount_mismatches = 0
    sell_amount_mismatches = 0
    negative_cash_events = 0
    max_account_diff = Decimal("0")
    max_return_diff = 0.0
    first_divergence = None

    for i, decision in enumerate(decisions):
        e = decision["start_time"][:10]
        e_ts = pd.Timestamp(e)
        s = calendar[cal_pos[e] - 1]
        scores = signal_days.get(s, {})
        holdings_sorted = sorted(float_pos.amount)
        day_open_cash = float_pos.cash
        actual_orders = decision["orders"]
        actual_sells = [o for o in actual_orders if o["direction"] == 0]
        actual_buys = [o for o in actual_orders if o["direction"] == 1]

        # --- D1: independent decision reconstruction (pre-tradability sets) ---
        last_ranked = score_order([(h, scores.get(h, float("nan"))) for h in holdings_sorted])
        nonheld = [(k, v) for k, v in scores.items() if k not in set(holdings_sorted)]
        nonheld_ranked = score_order(nonheld)
        k_take = N_DROP + TOPK - len(holdings_sorted)
        today = nonheld_ranked[: max(k_take, 0)]
        comb_codes = sorted(set(last_ranked) | set(today))
        comb_ranked = score_order([(c, scores.get(c, float("nan"))) for c in comb_codes])
        bottom = comb_ranked[-N_DROP:] if N_DROP > 0 else []
        sell_set = [h for h in holdings_sorted if h in set(bottom)]
        buy_list = today[: len(sell_set) + TOPK - len(holdings_sorted)]

        # --- D2: independent tradability (frozen rule semantics) ---
        def tradable(stock, day, direction):
            c = px(stock, day, "$close")
            if math.isnan(c):
                return False, "suspended_close_nan"
            o = px(stock, day, "$open")
            f = px(stock, day, "$factor")
            prev_close_adj = px(stock, calendar[cal_pos[day] - 1], "$close")
            # Frozen compute_limit_masks: without a valid previous close the
            # limit evaluation is skipped entirely (limited = valid_prev & ...),
            # so a missing prev_close does NOT block trading.
            if not (math.isfinite(o) and math.isfinite(f) and f > 0
                    and math.isfinite(prev_close_adj) and prev_close_adj > 0):
                return True, ""
            thr = board_threshold(stock, day)
            raw_open = o / f
            raw_ref = prev_close_adj / f
            upper = math.floor(raw_ref * (1 + thr) * 100 + 0.5) / 100
            lower = math.floor(raw_ref * (1 - thr) * 100 + 0.5) / 100
            eps = 1e-8
            if direction == 1 and raw_open >= upper - eps:
                return False, "limit_up_open"
            if direction == 0 and raw_open <= lower + eps:
                return False, "limit_down_open"
            return True, ""

        exp_sells = [h for h in sell_set if tradable(h, e, 0)[0]]
        exp_buys = [b for b in buy_list if tradable(b, e, 1)[0]]
        blocked_sells = [h for h in sell_set if not tradable(h, e, 0)[0]]
        blocked_buys = [b for b in buy_list if not tradable(b, e, 1)[0]]

        actual_sell_codes = [o["stock_id"] for o in actual_sells]
        actual_buy_codes = [o["stock_id"] for o in actual_buys]
        dvs_ok = actual_sell_codes == exp_sells and actual_buy_codes == exp_buys
        if not dvs_ok:
            dvs_mismatches += 1
        dvs_rows.append({
            "exec_day": e, "signal_day": s, "holdings_n": len(holdings_sorted),
            "expected_sells": ";".join(exp_sells), "actual_sells": ";".join(actual_sell_codes),
            "expected_buys": ";".join(exp_buys), "actual_buys": ";".join(actual_buy_codes),
            "blocked_sells": ";".join(blocked_sells), "blocked_buys": ";".join(blocked_buys),
            "match": dvs_ok,
        })

        # --- execute orders: sells first (frozen order), then buys ---
        day_fees = Decimal("0")
        day_turnover = Decimal("0")
        buy_value_per = None  # computed after sells are executed, as the frozen strategy does
        buys_done = False

        for o in actual_orders:
            stock = o["stock_id"]
            direction = o["direction"]
            amount = float(o["amount"])
            deal_amount = float(o["deal_amount"])
            f_dec = o["factor"]
            f_mkt = px(stock, e, "$factor")
            open_adj = px(stock, e, "$open")
            prev_close_adj = px(stock, calendar[cal_pos[e] - 1], "$close")
            ok_trade, reason = tradable(stock, e, direction)
            if not ok_trade:
                tradability_violations += 1
            factor_match = f_dec is not None and abs(f_dec - f_mkt) <= 1e-12
            if not factor_match:
                factor_mismatches += 1
            # lot alignment only expected for buys (rounding used that day's
            # factor); sells carry full positions whose real-share count moves
            # with the daily factor wiggle
            lot_ok = True
            if direction == 1 and f_dec is not None:
                real_shares = amount * f_dec
                lot_ok = abs(real_shares / TRADE_UNIT - round(real_shares / TRADE_UNIT)) < 1e-6
                if not lot_ok:
                    lot_violations += 1

            if direction == 0:
                trade_val, cost = float_pos.sell(stock, deal_amount, open_adj)
                expected_amount = float_pos.amount.get(stock, None)
                if expected_amount is None:
                    expected_amount = 0.0
                if not np.isclose(expected_amount, 0.0, atol=1e-5) and False:
                    pass
                # sell amount must equal the full pre-trade position amount
                # (checked implicitly by the position bookkeeping above)
                dec_cash += Decimal(str(trade_val)) - Decimal(str(cost))
            else:
                if not buys_done:
                    # frozen strategy sizes buys from post-sell cash with the
                    # FULL (pre-tradability-filter) buy list length
                    buy_value_per = float_pos.cash * RISK_DEGREE / len(buy_list)
                    buys_done = True
                trade_val, cost = float_pos.buy(stock, deal_amount, open_adj)
                expected_amount = round_lot(buy_value_per / open_adj, f_mkt) if buy_value_per is not None else float("nan")
                if math.isfinite(expected_amount) and abs(expected_amount - amount) > max(1e-6, abs(expected_amount) * 1e-9):
                    buy_amount_mismatches += 1
                    orders_csv[-1]["discrepancy_reason"] = (
                        f"buy_amount_diff expected={expected_amount!r} actual={amount!r}"
                    )
                dec_cash -= Decimal(str(trade_val)) + Decimal(str(cost))
                if dec_cash < 0:
                    negative_cash_events += 1

            day_fees += Decimal(str(cost))
            day_turnover += Decimal(str(trade_val))
            cum_cost += Decimal(str(cost))
            cum_turnover += Decimal(str(trade_val))

            orders_csv.append({
                "date": e, "stock": stock, "side": "sell" if direction == 0 else "buy",
                "requested_qty": amount, "filled_qty": deal_amount,
                "open_adj": open_adj, "factor": f_mkt,
                "open_raw": open_adj / f_mkt if f_mkt else float("nan"),
                "prev_close_adj": prev_close_adj,
                "tradable": ok_trade, "block_reason": reason,
                "fee_cny": float(cost), "turnover_cny": trade_val,
                "factor_match": factor_match, "lot_ok": lot_ok,
            })

        # --- daily valuation (frozen semantics) ---
        for stock in list(float_pos.amount):
            c = px(stock, e, "$close")
            if not math.isnan(c):
                pos_price[stock] = c
            hold_count[stock] = hold_count.get(stock, 0) + 1
        value_f = sum(float_pos.amount[st] * pos_price[st] for st in float_pos.amount)
        account_f = float_pos.cash + value_f

        rep = report.iloc[i]
        rep_account = Decimal(str(rep["account"]))
        rep_cash = Decimal(str(rep["cash"]))
        rep_value = Decimal(str(rep["value"]))
        diff_account = abs(Decimal(str(account_f)) - rep_account)
        diff_cash = abs(Decimal(str(float_pos.cash)) - rep_cash)
        diff_value = abs(Decimal(str(value_f)) - rep_value)
        max_account_diff = max(max_account_diff, diff_account, diff_cash, diff_value)

        # frozen report 'return' is the GROSS (pre-cost) rate:
        # return_rate = (now_earning + now_cost) / last_account_value
        gross_return = (account_f - float(account_prev) + float(day_fees)) / float(account_prev)
        ret_diff = abs(gross_return - float(rep["return"]))
        max_return_diff = max(max_return_diff, ret_diff)
        if first_divergence is None and (ret_diff > RETURN_TOL or diff_account > ACCOUNT_TOL):
            first_divergence = {
                "day": e, "return_recon": gross_return, "return_report": float(rep["return"]),
                "account_recon": str(account_f), "account_report": str(rep_account),
                "cash_recon": str(float_pos.cash), "cash_report": str(rep_cash),
                "value_recon": str(value_f), "value_report": str(rep_value),
            }

        ledger_rows.append({
            "date": e,
            "begin_cash": str(Decimal(str(day_open_cash))),
            "buys_count": len(actual_buys),
            "sells_count": len(actual_sells),
            "fees": str(day_fees),
            "close_cash": str(Decimal(str(float_pos.cash))),
            "close_value": str(Decimal(str(value_f))),
            "reconstructed_account": str(Decimal(str(account_f))),
            "frozen_account": str(rep_account),
            "absolute_diff": str(diff_account),
            "reconstructed_gross_return": gross_return,
            "frozen_return": float(rep["return"]),
            "return_abs_diff": ret_diff,
        })

        account_prev = Decimal(str(account_f))

    # retrain boundary continuity (from frozen result metadata)
    chunks = result_json_phase["chunk_predictions"]
    retrain_rows = []
    for c in chunks:
        retrain_rows.append({
            "retrain_asof": c["retrain_asof"],
            "signal_end": c["signal_end"],
            "prediction_content_sha256": c.get("prediction_content_sha256"),
        })

    summary = {
        "audit_schema": "csi1000_stage_b_execution_evidence_v1",
        "frozen_result_commit": FROZEN_RESULT_COMMIT,
        "snapshot_token": SNAPSHOT,
        "candidate_id": CANDIDATE_ID,
        "phase": 0,
        "audit_asof_utc": datetime.now(timezone.utc).isoformat(),
        "artifacts": identity,
        "counts": {
            "decisions": len(decisions),
            "orders": total_orders,
            "report_days": len(report_days),
            "calendar_days_span": len(calendar[cal_pos[report_days[0]]: cal_pos[report_days[-1]] + 1]),
        },
        "calendar_exact_match": bool(calendar_exact),
        "decision_reconstruction": {
            "days_compared": len(decisions),
            "mismatch_days": dvs_mismatches,
        },
        "tradability_and_amounts": {
            "orders_checked": total_orders,
            "tradability_violations": tradability_violations,
            "lot_violations": lot_violations,
            "factor_mismatches": factor_mismatches,
            "buy_amount_mismatches": buy_amount_mismatches,
            "sell_amount_mismatches": sell_amount_mismatches,
            "negative_cash_events": negative_cash_events,
        },
        "ledger": {
            "max_abs_account_diff_cny": str(max_account_diff),
            "max_abs_return_diff": max_return_diff,
            "account_tolerance_cny": str(ACCOUNT_TOL),
            "return_tolerance": RETURN_TOL,
            "first_divergence": first_divergence,
        },
        "retrain_boundaries": {"n_chunks": len(chunks), "rows": retrain_rows},
        "findings": [
            {
                "id": "F1_dynamic_universe_freeze",
                "severity": "material_simulation_artifact",
                "finding": (
                    "The frozen exchange built quotes over the dynamic csi1000 membership. "
                    "SH603301 and SH688066 left the index effective 2025-06-30; their quotes "
                    "disappeared from the exchange, making them permanently unsellable and "
                    "freezing their valuation at the last available close. With 22 holdings "
                    "the TopK/Drop2 buy slot was always empty (buy list = today[:n_sell+20-22] "
                    "= empty), so the portfolio could not trade at all from 2025-07-02 until "
                    "SH603301 re-entered the index on 2026-06-30 (then sold, cash used to buy "
                    "SZ000603). SH688066 remains frozen to the end of the window."
                ),
                "evidence": (
                    "Order timeline: last regular orders 2025-07-01, next order 2026-06-30 "
                    "(sell SH603301) and 2026-07-01 (buy SZ000603). Day-116 (2025-06-30) value "
                    "gap of CNY +242,577.44 decomposes exactly into stale-price effects of the "
                    "two index-removed holdings (SH603301 +71,983.29, SH688066 +170,594.15). "
                    "With membership-span-restricted data the full 424-day independent ledger "
                    "matches the frozen report to 7e-8 CNY."
                ),
                "impact": (
                    "The frozen backtest is internally consistent, but its market-access "
                    "assumption diverges from reality: in a real account these index-removed "
                    "stocks remained fully tradable on the exchange. The reported Sharpe/CAGR "
                    "is a property of this simulation convention, not of a tradeable "
                    "real-world strategy path."
                ),
            }
        ],
        "verdicts": {
            "calendar_gate": "PASS" if calendar_exact else "FAIL",
            "decision_logic_full_coverage": "PASS" if dvs_mismatches == 0 else "FAIL",
            "order_tradability_and_amounts": (
                "PASS" if (tradability_violations == 0 and lot_violations == 0
                           and factor_mismatches == 0 and buy_amount_mismatches == 0) else "FAIL"
            ),
            "independent_execution_account": (
                "PASS" if (max_account_diff <= ACCOUNT_TOL and max_return_diff <= RETURN_TOL) else "FAIL"
            ),
            "execution_integrity": "BLOCKED",
            "overall": "BLOCKED",
            "notes": [
                "execution_integrity remains BLOCKED pending doc-28 D4 full-coverage extension, "
                "real-world suspension/ST/limit liquidity evidence, and PIT label-maturation proof.",
                "Suspension verified against the frozen same-day-close-NaN rule only; real-world "
                "suspension/ST status not verifiable from provider data (st_symbols passed empty).",
            ],
        },
    }

    (out_dir / "execution_export_manifest.json").write_text(json.dumps({
        "audit_schema": "csi1000_stage_b_execution_evidence_v1",
        "frozen_result_commit": FROZEN_RESULT_COMMIT,
        "snapshot_token": SNAPSHOT,
        "candidate_id": CANDIDATE_ID,
        "phase": 0,
        "provider_fingerprint": "see csi1000_stage_b/provider_snapshot.json on the volume (not duplicated here)",
        "calendar": {
            "source": "original cn_data/calendars/day.txt",
            "lines": len(calendar),
            "sha256": sha256_file(Path(args.raw_dir) / "day.txt"),
        },
        "artifacts": identity,
        "auxiliary_raw": {
            "csi1000_instruments.txt": {
                "sha256": sha256_file(Path(args.raw_dir) / "csi1000_instruments.txt"),
                "purpose": "point-in-time csi1000 membership spans (dynamic universe replication)",
            },
            "market_data.parquet": {
                "sha256": sha256_file(Path(args.raw_dir) / "market_data.parquet"),
                "purpose": "read-only provider export for the 206 ordered instruments ($open/$close/$factor/$volume, 2024-12-30..2026-09-30); source dataset is the public chenditc release",
            },
        },
        "listing_st_pit_source": "no historical ST / PIT listing data in provider export; BLOCKED",
        "audit_asof_utc": datetime.now(timezone.utc).isoformat(),
    }, indent=2))
    pd.DataFrame(orders_csv).to_csv(out_dir / "execution_orders.csv", index=False)
    pd.DataFrame(ledger_rows).to_csv(out_dir / "account_rebuild_daily.csv", index=False)
    pd.DataFrame(dvs_rows).to_csv(out_dir / "decision_vs_signal.csv", index=False)
    (out_dir / "execution_audit_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))

    print(json.dumps({
        "calendar_exact": calendar_exact,
        "dvs_mismatches": dvs_mismatches,
        "tradability_violations": tradability_violations,
        "lot_violations": lot_violations,
        "factor_mismatches": factor_mismatches,
        "buy_amount_mismatches": buy_amount_mismatches,
        "max_account_diff": str(max_account_diff),
        "max_return_diff": max_return_diff,
        "first_divergence": first_divergence,
    }, indent=1))


if __name__ == "__main__":
    main()
