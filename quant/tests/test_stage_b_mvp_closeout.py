"""Negative controls for full-calendar inventory and the MVP paper contract."""
import copy
import unittest

from audit.stage_b_quote_inventory import scan_holding_quotes
from audit.verify_stage_b_fixed_ledger import (
    VERIFIED_SUSPENSION_DATES, KNOWN_ST_EXECUTION_REVIEW,
)
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

    def test_zero_close_on_documented_halt_is_not_silently_carried(self):
        days, decisions, quotes = self.sample()
        quotes[("2025-02-20", "SZ000488")] = (None, 0.0, None, None)
        r = scan_holding_quotes(decisions, quotes, days,
                                {("2025-02-20", "SZ000488"): "https://example.test"})
        self.assertIn("DOCUMENTED_SUSPENSION_HAS_CLOSE", [x["issue"] for x in r["issues"]])
        self.assertEqual(r["status"], "BLOCKED_INPUT_QUOTES")

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


class DatedSuspensionEvidenceRegressionTests(unittest.TestCase):
    # Exact frozen baseline p00 held-price gaps from the PR #54 Modal JSON.
    SEVEN_GAPS = {
        ("2025-04-30", "SH603398"),
        ("2025-04-30", "SZ300379"),
        ("2025-12-18", "SZ002036"),
        ("2025-12-19", "SZ002036"),
        ("2025-12-22", "SZ002036"),
        ("2025-12-23", "SZ002036"),
        ("2025-12-24", "SZ002036"),
    }

    @staticmethod
    def _multiday_fixture():
        dates = [
            "2025-04-29", "2025-04-30", "2025-05-06",
            "2025-12-17", "2025-12-18", "2025-12-19",
            "2025-12-22", "2025-12-23", "2025-12-24", "2025-12-25",
        ]
        first = ["SH603398", "SZ300379"]
        last = ["SZ002036"]
        def orders(day, symbols, direction):
            return [
                {"order_index": i, "start_time": day, "stock_id": s,
                 "direction": direction, "deal_amount": 2.0, "amount": 2.0}
                for i, s in enumerate(symbols)
            ]
        decisions = []
        quotes = {}
        for i, day in enumerate(dates):
            o = (orders(day, first, 1) if i == 0 else
                 orders(day, first, 0) if i == 2 else
                 orders(day, last, 1) if i == 3 else
                 orders(day, last, 0) if i == 9 else [])
            decisions.append({"decision_index": i, "start_time": day, "orders": o})
            for s in first + last:
                quotes[(day, s)] = (10.0, 10.0, 1.0, 10000.0)
        for item in DatedSuspensionEvidenceRegressionTests.SEVEN_GAPS:
            quotes[item] = (None, None, None, None)
        return dates, decisions, quotes

    def test_all_seven_from_original_modal_are_exactly_allowlisted(self):
        previous = {("2025-02-20", "SZ000488"),
                    ("2025-04-28", "SZ002214")}
        self.assertEqual(set(VERIFIED_SUSPENSION_DATES), previous | self.SEVEN_GAPS)
        for date, stock in self.SEVEN_GAPS:
            url = VERIFIED_SUSPENSION_DATES[(date, stock)]
            self.assertTrue(url.startswith("https://"))
            self.assertTrue(url.lower().endswith(".pdf"))
        self.assertNotIn(("2025-12-25", "SZ002036"), VERIFIED_SUSPENSION_DATES)
        self.assertNotIn(("2025-05-06", "SH603398"), VERIFIED_SUSPENSION_DATES)
        self.assertNotIn(("2025-05-06", "SZ300379"), VERIFIED_SUSPENSION_DATES)

    def test_seven_suspensions_carry_prior_mark_without_claiming_fills(self):
        dates, decisions, quotes = self._multiday_fixture()
        inv = scan_holding_quotes(
            decisions, quotes, dates, VERIFIED_SUSPENSION_DATES,
            KNOWN_ST_EXECUTION_REVIEW)
        self.assertEqual(inv["status"], "READY_FOR_ACCOUNTING_ONLY")
        self.assertEqual(inv["documented_suspension_carry_count"], 7)
        self.assertEqual(inv["unknown_held_close_count"], 0)
        self.assertEqual(inv["blocking_count"], 0)
        self.assertEqual(inv["st_execution_warning_count"], 2)
        self.assertEqual(inv["market_execution"], "NOT_CERTIFIED")
        self.assertEqual(
            {(x["date"], x["stock_id"]) for x in inv["issues"]
             if x["issue"] == "DOCUMENTED_SUSPENSION_CARRY_MARK"},
            self.SEVEN_GAPS,
        )
        multi = [x for x in inv["issues"]
                 if x["issue"] == "DOCUMENTED_SUSPENSION_CARRY_MARK"
                 and x["stock_id"] == "SZ002036"]
        self.assertEqual({x["prior_mark_date"] for x in multi}, {"2025-12-17"})
        self.assertEqual(
            KNOWN_ST_EXECUTION_REVIEW[("2025-05-06", "SZ300379")]["issue"],
            "HISTORIC_ST_20PCT_LIMIT_SELL_NOT_EXTERNALLY_VALIDATED",
        )

    def test_unknown_new_missing_day_still_blocks(self):
        dates, decisions, quotes = self._multiday_fixture()
        # A valid held position on 2025-12-17 must not be silently ffilled.
        # New gaps must still be visible in the full inventory.
        quotes[("2025-12-17", "SZ002036")] = (None, None, None, None)
        result = scan_holding_quotes(
            decisions, quotes, dates, VERIFIED_SUSPENSION_DATES,
            KNOWN_ST_EXECUTION_REVIEW)
        self.assertEqual(result["status"], "BLOCKED_INPUT_QUOTES")
        self.assertTrue(any(x["issue"] == "INVALID_FROZEN_FILL_QUOTE"
                            for x in result["issues"]))
        self.assertGreater(result["blocking_count"], 0)

    def test_halt_with_valid_quote_conflicts_and_blocks(self):
        dates, decisions, quotes = self._multiday_fixture()
        quotes[("2025-12-22", "SZ002036")] = (10.0, 10.0, 1.0, 10000)
        result = scan_holding_quotes(
            decisions, quotes, dates, VERIFIED_SUSPENSION_DATES)
        self.assertIn("DOCUMENTED_SUSPENSION_HAS_CLOSE",
                      [x["issue"] for x in result["issues"]])
        self.assertEqual(result["status"], "BLOCKED_INPUT_QUOTES")


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
        obj = self.artifact()
        obj["signal_data_date"] = "2026-10-07"
        obj["model_fit_asof"] = "2026-10-07"
        obj["pending_orders"]["signal_date"] = "2026-10-07"
        r = evaluate_paper_artifact(obj, ["2026-10-07", "2026-10-08", "2026-10-09"])
        self.assertIn("PUBLICATION_NOT_APPROVED", " ".join(r["errors"]))

    def test_invalid_calendar_rejected_without_exception(self):
        r = evaluate_paper_artifact(self.artifact(), ["2026-10-08", "2026-10-99"])
        self.assertIn("INVALID_SUPPLIED_TRADING_CALENDAR", r["errors"])

    def test_invalid_pending_order_object_rejected_without_exception(self):
        obj = self.artifact()
        obj["pending_orders"]["buy"] = [{"not": "a stock id"}]
        r = evaluate_paper_artifact(obj)
        self.assertIn("PENDING_ORDER_SET_INVALID", r["errors"])

    def test_date_and_order_binding_rejected(self):
        obj = self.artifact()
        obj["pending_orders"]["execution_date"] = "2026-10-10"
        r = evaluate_paper_artifact(obj)
        self.assertIn("PENDING_ORDER_DATES_MISMATCH", r["errors"])


if __name__ == "__main__":
    unittest.main()
