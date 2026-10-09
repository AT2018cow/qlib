"""Synthetic fixtures ONLY; not a real provider replay or execution approval."""
import datetime as dt
import hashlib
import json
import math
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from audit.verify_stage_b_fixed_ledger import (
    CAPITAL, DAYS, checked, reconstruct, score, require,
)


def fixture():
    days = [dt.date(2025, 1, 2) + dt.timedelta(days=i) for i in range(DAYS)]
    quotes = {(day.isoformat(), "SH600000"): (10.0, 10.0, 1.0, 100000.0) for day in days}
    quotes[(days[1].isoformat(), "SH600000")] = (11.0, 11.0, 1.0, 100000.0)
    orders = [[{"order_index": 0, "stock_id": "SH600000", "direction": 1,
                "amount": 2.0, "deal_amount": 2.0, "factor": 1.0,
                "start_time": str(days[0])}],
              [{"order_index": 0, "stock_id": "SH600000", "direction": 0,
                "amount": 2.0, "deal_amount": 2.0, "factor": 1.0,
                "start_time": str(days[1])}]] + [[] for _ in range(DAYS - 2)]
    decisions = [{"decision_index": i, "start_time": str(day), "orders": day_orders}
                 for i, (day, day_orders) in enumerate(zip(days, orders))]
    nav = [CAPITAL - 5, CAPITAL - 8] + [CAPITAL - 8] * (DAYS - 2)
    cash = [CAPITAL - 25, CAPITAL - 8] + [CAPITAL - 8] * (DAYS - 2)
    fees = [5.0, 10.0] + [10.0] * (DAYS - 2)
    turnover = [20.0, 42.0] + [42.0] * (DAYS - 2)
    cost = [5 / CAPITAL, 5 / (CAPITAL - 5)] + [0.0] * (DAYS - 2)
    turn_rate = [20 / CAPITAL, 22 / (CAPITAL - 5)] + [0.0] * (DAYS - 2)
    gross_returns = [(nav[0] / CAPITAL - 1) + cost[0],
                     (nav[1] / nav[0] - 1) + cost[1]] + [0.0] * (DAYS - 2)
    report = pd.DataFrame({"account": nav, "cash": cash,
                           "value": [20.0] + [0.0] * (DAYS - 1),
                           "total_cost": fees, "total_turnover": turnover,
                           "return": gross_returns, "cost": cost,
                           "turnover": turn_rate, "bench": [0.0] * DAYS})
    return days, decisions, report, quotes


class FixedLedgerUnitTest(unittest.TestCase):
    def test_exact_two_order_cash_fee_nav(self):
        days, decisions, report, quotes = fixture()
        result = reconstruct(decisions, report, quotes, days)
        self.assertEqual(result["ledger"], "PASS_RESEARCH_ACCOUNTING_ONLY")
        self.assertEqual(result["filled_orders"], 2)
        self.assertEqual(result["daily_net_notional_summary"]["total"], 42.0)
        self.assertEqual(result["extra_cost_bps_on_original_notional_not_new_fills"]["0"]["strategy_total_return"],
                         (CAPITAL - 8) / CAPITAL - 1)
        self.assertEqual(result["external_fill_liquidity_st_ipo_pit_maturity"], "BLOCKED_NOT_TESTED")

    def test_altered_cash_fails_closed(self):
        days, decisions, report, quotes = fixture()
        report.loc[0, "cash"] += 1000
        result = reconstruct(decisions, report, quotes, days)
        self.assertEqual(result["ledger"], "FAIL")
        self.assertIn("cash", result["fail_fields"])

    def test_missing_price_blocks(self):
        days, decisions, report, quotes = fixture()
        del quotes[(days[0].isoformat(), "SH600000")]
        with self.assertRaisesRegex(ValueError, "missing trade quote"):
            reconstruct(decisions, report, quotes, days)

    def test_factor_drift_blocks(self):
        days, decisions, report, quotes = fixture()
        decisions[0]["orders"][0]["factor"] = 2
        with self.assertRaisesRegex(ValueError, "factor mismatch"):
            reconstruct(decisions, report, quotes, days)

    def test_partial_fill_blocks(self):
        days, decisions, report, quotes = fixture()
        decisions[0]["orders"][0]["deal_amount"] = 1
        with self.assertRaisesRegex(ValueError, "partial fill"):
            reconstruct(decisions, report, quotes, days)

    def test_invalid_sell_blocks(self):
        days, decisions, report, quotes = fixture()
        decisions[1]["orders"][0]["deal_amount"] = 3
        decisions[1]["orders"][0]["amount"] = 3
        with self.assertRaisesRegex(ValueError, "not a full sell"):
            reconstruct(decisions, report, quotes, days)

    def test_duplicate_days_cannot_be_used_by_main_runner(self):
        days, decisions, report, quotes = fixture()
        decisions[1]["decision_index"] = 0
        with self.assertRaisesRegex(ValueError, "decision clock/index"):
            reconstruct(decisions, report, quotes, days)

    def test_sha_guard(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "input.json"
            p.write_text('{"a":1}')
            expected = hashlib.sha256(p.read_bytes()).hexdigest()
            self.assertEqual(checked(p, expected, "fixture")["sha256"], expected)
            p.write_text('{"a":2}')
            with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                checked(p, expected, "fixture")

    def test_sharpe_238_and_cost_stress(self):
        days, decisions, report, quotes = fixture()
        result = reconstruct(decisions, report, quotes, days)
        self.assertLess(result["extra_cost_bps_on_original_notional_not_new_fills"]["20"]["strategy_total_return"],
                        result["extra_cost_bps_on_original_notional_not_new_fills"]["0"]["strategy_total_return"])


if __name__ == "__main__":
    unittest.main()
