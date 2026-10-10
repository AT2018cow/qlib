"""Negative controls for full-calendar inventory and the MVP paper contract."""
import copy
import unittest

from audit.stage_b_quote_inventory import scan_holding_quotes
from audit.mvp_entry_review import evaluate_paper_artifact
from csi1000_production_config import (
    CANONICAL_PAPER_LINEAGE, CANONICAL_PROFILE, profile_manifest,
)


class QuoteInventoryTests(unittest.TestCase):
    def sample(self):
        days = ["2025-02-19", "2025-02-20", "2025-04-28",
                "2025-04-29", "2025-05-06"]
        decisions = []
        quotes = {}
        for i, day in enumerate(days):
            orders = []
            if i in (0, 4):
                orders = [{
                    "order_index": 0, "stock_id": "SZ000488",
                    "start_time": day, "direction": 1 if i == 0 else 0,
                    "deal_amount": 2.0, "amount": 2.0,
                }]
            decisions.append({"decision_index": i, "start_time": day, "orders": orders})
            quotes[(day, "SZ000488")] = (10.0, 10.0, 1.0, 10000)
        return days, decisions, quotes

    def test_all_gaps_reported_without_first_error_abort(self):
        days, decisions, quotes = self.sample()
        quotes[("2025-02-20", "SZ000488")] = (None, None, None, None)
        quotes[("2025-04-28", "SZ000488")] = (None, None, None, None)
        evidence = {("2025-02-20", "SZ000488"): "https://example.test/e1"}
        r = scan_holding_quotes(decisions, quotes, days, evidence)
        self.assertEqual(r["complete_inventory_calendar_days"], 5)
        self.assertEqual(r["status"], "BLOCKED_INPUT_QUOTES")
        self.assertEqual(r["documented_suspension_carry_count"], 1)
        self.assertEqual(r["unknown_held_close_count"], 1)
        self.assertEqual([x["date"] for x in r["issues"]],
                         ["2025-02-20", "2025-04-28"])

    def test_two_evidenced_suspensions_allow_accounting_only(self):
        days, decisions, quotes = self.sample()
        quotes[("2025-02-20", "SZ000488")] = (None, None, None, None)
        quotes[("2025-04-28", "SZ000488")] = (None, None, None, None)
        evidence = {(day, "SZ000488"): "https://example.test/notice"
                    for day in ("2025-02-20", "2025-04-28")}
        r = scan_holding_quotes(decisions, quotes, days, evidence)
        self.assertEqual(r["status"], "READY_FOR_ACCOUNTING_ONLY")
        self.assertEqual(r["documented_suspension_carry_count"], 2)
        self.assertEqual(r["blocking_count"], 0)
        self.assertEqual(r["market_execution"], "NOT_CERTIFIED")

    def test_even_late_unknown_gap_is_included(self):
        days, decisions, quotes = self.sample()
        quotes[("2025-02-20", "SZ000488")] = (None, None, None, None)
        quotes[("2025-04-28", "SZ000488")] = (None, None, None, None)
        quotes[("2025-04-29", "SZ000488")] = (None, None, None, None)
        evidence = {(day, "SZ000488"): "https://example.test/notice"
                    for day in ("2025-02-20", "2025-04-28")}
        r = scan_holding_quotes(decisions, quotes, days, evidence)
        self.assertEqual(r["unknown_held_close_count"], 1)
        self.assertEqual(r["issues"][-1]["date"], "2025-04-29")

    def test_frozen_order_with_missing_close_blocks_even_if_sale(self):
        days, decisions, quotes = self.sample()
        quotes[(days[-1], "SZ000488")] = (10, None, 1, 100)
        r = scan_holding_quotes(decisions, quotes, days, {})
        self.assertEqual(r["status"], "BLOCKED_INPUT_QUOTES")
        self.assertIn("INVALID_FROZEN_FILL_QUOTE", [x["issue"] for x in r["issues"]])

    def test_documented_halt_conflicting_close_blocks(self):
        days, decisions, quotes = self.sample()
        r = scan_holding_quotes(decisions, quotes, days,
                                {("2025-02-20", "SZ000488"): "https://example.test"})
        self.assertIn("DOCUMENTED_SUSPENSION_HAS_CLOSE", [x["issue"] for x in r["issues"]])
        self.assertGreater(r["blocking_count"], 0)

    def test_st_risk_flag_does_not_masquerade_as_trade_approval(self):
        days, decisions, quotes = self.sample()
        r = scan_holding_quotes(decisions, quotes, days, {},
                                {(days[-1], "SZ000488"): {"notice": "https://example.test"}})
        self.assertEqual(r["st_execution_warning_count"], 1)
        self.assertEqual(r["blocking_count"], 0)
        self.assertEqual(r["market_execution"], "NOT_CERTIFIED")

    def test_invalid_frozen_sell_raises_without_invented_position(self):
        days, decisions, quotes = self.sample()
        decisions[0]["orders"] = []
        with self.assertRaisesRegex(ValueError, "impossible frozen sell"):
            scan_holding_quotes(decisions, quotes, days, {})

    def test_duplicate_calendar_rejected(self):
        days, decisions, quotes = self.sample()
        with self.assertRaisesRegex(ValueError, "coverage drift"):
            scan_holding_quotes(decisions, quotes, days[:-1] + days[-2:-1], {})


class MVPEntryReviewTests(unittest.TestCase):
    @staticmethod
    def artifact():
        return {
            "market": "csi1000", "paper_lineage": CANONICAL_PAPER_LINEAGE,
            "production_lineage": profile_manifest(CANONICAL_PROFILE),
            "signal_data_date": "2026-10-08", "usage_date": "2026-10-09",
            "model_fit_asof": "2026-10-08",
            "pending_orders": {
                "signal_date": "2026-10-08", "execution_date": "2026-10-09",
                "buy": ["SH600000"], "sell": [],
            },
        }

    def test_paper_identity_and_supplied_calendar_static_pass_only(self):
        r = evaluate_paper_artifact(self.artifact(), ["2026-10-08", "2026-10-09"])
        self.assertEqual(r["static_artifact_contract"], "PASS_STATIC_ONLY")
        self.assertEqual(r["supplied_calendar_gate"], "PASS_SUPPLIED_CALENDAR_ONLY")
        self.assertFalse(r["mvp_activation_approved"])
        self.assertEqual(r["current_st_suspension_status"], "NOT_VERIFIED")

    def test_future_fit_or_reused_signal_is_rejected(self):
        obj = self.artifact()
        obj["model_fit_asof"] = "2026-10-10"
        r = evaluate_paper_artifact(obj)
        self.assertIn("FUTURE_OR_OUT_OF_ORDER_MODEL_SIGNAL_DATE", r["errors"])

    def test_candidate_identity_mismatch_rejected(self):
        obj = self.artifact()
        obj["production_lineage"]["candidate_id"] = "other"
        r = evaluate_paper_artifact(obj)
        self.assertEqual(r["static_artifact_contract"], "FAIL")

    def test_not_prior_trading_session_rejected(self):
        r = evaluate_paper_artifact(self.artifact(),
                                    ["2026-10-08", "2026-10-08a", "2026-10-09"])
        self.assertIn("PUBLICATION_NOT_APPROVED", " ".join(r["errors"]))

    def test_date_and_order_binding_rejected(self):
        obj = self.artifact()
        obj["pending_orders"]["execution_date"] = "2026-10-10"
        r = evaluate_paper_artifact(obj)
        self.assertIn("PENDING_ORDER_DATES_MISMATCH", r["errors"])


if __name__ == "__main__":
    unittest.main()
