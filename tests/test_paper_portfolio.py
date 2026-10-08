import os
import tempfile
import unittest

from paper_portfolio import PaperPortfolio


class PaperPortfolioTests(unittest.TestCase):
    def setUp(self):
        self.pp = PaperPortfolio(topk=3, nd=1, initial_cash=100000, risk_degree=0.95)
        self.ranking = [("A", 0.5), ("B", 0.4), ("C", 0.3), ("D", 0.2), ("E", 0.1)]
        self.prices = {"A": 10.0, "B": 20.0, "C": 25.0, "D": 40.0, "E": 50.0}
        self.factors = {sym: 1.0 for sym in self.prices}

    def _plan_execute(self, signal_date, execution_date, ranking=None, buy_tradable=None, sell_tradable=None):
        self.pp.plan_signal(signal_date, execution_date, ranking or self.ranking)
        return self.pp.execute_pending(
            execution_date,
            self.prices,
            factors=self.factors,
            buy_tradable=buy_tradable,
            sell_tradable=sell_tradable,
        )

    def test_first_execution_fills_topk(self):
        r = self._plan_execute("2026-01-04", "2026-01-05")
        self.assertEqual(set(r["positions"]), {"A", "B", "C"})
        self.assertEqual([x["instrument"] for x in r["executed_buy"]], ["A", "B", "C"])
        self.assertLess(self.pp.cash, self.pp.initial_cash)

    def test_dropout_replaces_only_worst(self):
        self._plan_execute("2026-01-04", "2026-01-05")
        ranking2 = [("A", 0.5), ("B", 0.4), ("D", 0.35), ("C", 0.2), ("E", 0.1)]
        self.pp.plan_signal("2026-01-05", "2026-01-06", ranking2)
        r = self.pp.execute_pending("2026-01-06", self.prices, factors=self.factors)
        self.assertEqual(r["planned_sell"], ["C"])
        self.assertEqual(r["planned_buy"], ["D"])
        self.assertNotIn("C", self.pp.positions)
        self.assertIn("D", self.pp.positions)

    def test_blocked_buy_is_not_substituted(self):
        self._plan_execute("2026-01-04", "2026-01-05")
        ranking2 = [("D", 0.9), ("E", 0.8), ("A", 0.4), ("B", 0.3), ("C", 0.1)]
        self.pp.plan_signal("2026-01-05", "2026-01-06", ranking2)
        r = self.pp.execute_pending(
            "2026-01-06",
            self.prices,
            factors=self.factors,
            buy_tradable={"D": False, "E": True},
        )
        self.assertEqual(r["planned_buy"], ["D"])
        self.assertEqual(r["executed_buy"], [])
        self.assertNotIn("E", self.pp.positions)

    def test_sell_block_does_not_shrink_planned_buy_list(self):
        self._plan_execute("2026-01-04", "2026-01-05")
        ranking2 = [("D", 0.9), ("A", 0.4), ("B", 0.3), ("C", 0.1)]
        self.pp.plan_signal("2026-01-05", "2026-01-06", ranking2)
        r = self.pp.execute_pending(
            "2026-01-06",
            self.prices,
            factors=self.factors,
            sell_tradable={"C": False},
        )
        self.assertEqual(r["planned_sell"], ["C"])
        self.assertEqual(r["planned_buy"], ["D"])
        self.assertIn("C", self.pp.positions)
        self.assertIn("D", self.pp.positions)
        self.assertEqual(len(self.pp.positions), 4)  # mirrors Qlib default only_tradable=False behavior

    def test_qllib_cash_sizing_uses_planned_buy_count(self):
        self._plan_execute("2026-01-04", "2026-01-05")
        ranking2 = [("D", 0.9), ("A", 0.4), ("B", 0.3), ("C", 0.1)]
        before = self.pp.cash
        self.pp.plan_signal("2026-01-05", "2026-01-06", ranking2)
        r = self.pp.execute_pending("2026-01-06", self.prices, factors=self.factors)
        self.assertEqual(len(r["planned_buy"]), 1)
        # The single new buy receives nearly 95% of post-sell cash, not cash / portfolio-size.
        self.assertGreater(r["executed_buy"][0]["shares"] * r["executed_buy"][0]["price"], before * 0.8)

    def test_state_roundtrip_preserves_execution_config_and_pending(self):
        self.pp.plan_signal("2026-01-04", "2026-01-05", self.ranking)
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "state.json")
            self.pp.save(path)
            pp2 = PaperPortfolio.load(path)
        self.assertEqual(pp2.risk_degree, self.pp.risk_degree)
        self.assertEqual(pp2.open_cost, self.pp.open_cost)
        self.assertEqual(pp2.high_open_block, self.pp.high_open_block)
        self.assertEqual(pp2.pending_signal, self.pp.pending_signal)

    def test_pending_cannot_be_overwritten(self):
        self.pp.plan_signal("2026-01-04", "2026-01-05", self.ranking)
        with self.assertRaises(RuntimeError):
            self.pp.plan_signal("2026-01-05", "2026-01-06", self.ranking)

    def test_execution_date_must_match_pending(self):
        self.pp.plan_signal("2026-01-04", "2026-01-05", self.ranking)
        with self.assertRaises(ValueError):
            self.pp.execute_pending("2026-01-06", self.prices, factors=self.factors)

    def test_exact_score_ties_use_instrument_ascending(self):
        ranking = [("C", 0.5), ("A", 0.5), ("B", 0.5), ("D", 0.4)]
        self.pp.plan_signal("2026-01-04", "2026-01-05", ranking)
        r = self.pp.execute_pending("2026-01-05", self.prices, factors=self.factors)
        self.assertEqual(r["planned_buy"], ["A", "B", "C"])
        self.assertEqual(
            [x["instrument"] for x in r["executed_buy"]],
            ["A", "B", "C"],
        )

    def test_execution_report_records_pretrade_open_and_close_nav(self):
        self.pp.plan_signal("2026-10-09", "2026-10-12", self.ranking)
        close_prices = {"A": 11.0, "B": 21.0, "C": 26.0}
        r = self.pp.execute_pending(
            "2026-10-12",
            self.prices,
            factors=self.factors,
            close_prices=close_prices,
        )
        self.assertEqual(r["portfolio_value_pre_trade_open"], self.pp.initial_cash)
        self.assertIn("portfolio_value_close", r)
        self.assertGreater(r["portfolio_value_close"], r["portfolio_value"])
        for sym in ("A", "B", "C"):
            self.assertEqual(self.pp.positions[sym]["last_price"], close_prices[sym])

    def test_dropout_tie_boundary_and_sell_iteration_are_deterministic(self):
        ranking = [("C", 0.5), ("A", 0.5), ("B", 0.5), ("D", 0.4)]
        self._plan_execute("2026-01-04", "2026-01-05", ranking=ranking)
        ranking2 = [("C", 0.5), ("D", 0.9), ("B", 0.5), ("A", 0.5)]
        self.pp.plan_signal("2026-01-05", "2026-01-06", ranking2)
        r = self.pp.execute_pending("2026-01-06", self.prices, factors=self.factors)
        self.assertEqual(r["planned_sell"], ["C"])
        self.assertEqual(r["planned_buy"], ["D"])


    @staticmethod
    def _qlib_adjusted_units(target_amount, factor, trade_unit=100):
        """Reference Exchange.round_amount_by_trade_unit formula (no Cython dependency)."""
        return (target_amount * factor + 0.1) // trade_unit * trade_unit / factor

    def test_factor_aware_buy_matches_qlib_exchange_for_multiple_factors(self):
        for factor in (0.5, 1.0, 2.0, 3.0, 1.2345):
            with self.subTest(factor=factor):
                pp = PaperPortfolio(topk=1, nd=1, initial_cash=10000)
                pp.plan_signal("2026-10-09", "2026-10-12", [("A", 1.0), ("B", 0.0)])
                report = pp.execute_pending(
                    "2026-10-12", {"A": 10.0},
                    close_prices={"A": 11.0}, factors={"A": factor},
                )
                expected_shares = self._qlib_adjusted_units(9500.0 / 10.0, factor)
                fill = report["executed_buy"][0]
                self.assertAlmostEqual(fill["shares"], expected_shares, places=8)
                self.assertAlmostEqual(fill["physical_shares"], expected_shares * factor, places=6)
                self.assertEqual(fill["factor"], factor)
                expected_fee = max(expected_shares * 10.0 * 0.0005, 5.0)
                expected_cash = 10000 - expected_shares * 10 - expected_fee
                self.assertAlmostEqual(report["cash"], expected_cash, places=2)
                self.assertAlmostEqual(report["portfolio_value_close"],
                                       expected_cash + expected_shares * 11, places=2)

    def test_factor_two_exposes_old_rounding_difference_in_nav(self):
        pp = PaperPortfolio(topk=1, nd=1, initial_cash=10000)
        pp.plan_signal("2026-10-09", "2026-10-12", [("A", 1.0)])
        report = pp.execute_pending(
            "2026-10-12", {"A": 10.0}, factors={"A": 2.0},
            close_prices={"A": 11.0},
        )
        self.assertEqual(report["executed_buy"][0]["shares"], 950.0)
        self.assertEqual(report["executed_buy"][0]["physical_shares"], 1900)
        self.assertEqual(report["portfolio_value_close"], 10945.0)

    def test_missing_factor_fail_closed_before_any_sell_or_state_mutation(self):
        pp = PaperPortfolio(topk=1, nd=1, initial_cash=10000)
        pp.plan_signal("2026-10-09", "2026-10-12", [("A", 1.0), ("B", 0.0)])
        pp.execute_pending("2026-10-12", {"A": 10}, factors={"A": 1.0})
        pp.plan_signal("2026-10-12", "2026-10-13", [("B", 1.0), ("A", 0.0)])
        before = pp.to_dict()
        for invalid in ({}, {"B": 0}, {"B": float("nan")}, {"B": float("inf")}):
            with self.subTest(factors=invalid):
                with self.assertRaises(ValueError):
                    pp.execute_pending("2026-10-13", {"A": 11, "B": 10}, factors=invalid)
                self.assertEqual(pp.to_dict(), before)

    def test_factor_not_required_for_blocked_or_missing_open_buy(self):
        for kwargs in ({"buy_tradable": {"A": False}}, {"open_prices": {}}):
            with self.subTest(kwargs=kwargs):
                pp = PaperPortfolio(topk=1, nd=1, initial_cash=10000)
                pp.plan_signal("2026-10-09", "2026-10-12", [("A", 1.0)])
                report = pp.execute_pending(
                    "2026-10-12", kwargs.get("open_prices", {"A": 10}),
                    factors={}, buy_tradable=kwargs.get("buy_tradable"),
                )
                self.assertEqual(report["executed_buy"], [])
                self.assertEqual(report["portfolio_value_close"], 10000)

    def test_cash_shortfall_decrements_complete_physical_lots(self):
        pp = PaperPortfolio(topk=1, nd=1, initial_cash=10000, risk_degree=1)
        pp.plan_signal("2026-10-09", "2026-10-12", [("A", 1.0)])
        report = pp.execute_pending(
            "2026-10-12", {"A": 10.0}, factors={"A": 2.0},
        )
        fill = report["executed_buy"][0]
        self.assertEqual(fill["shares"], 950)
        self.assertEqual(fill["physical_shares"], 1900)
        self.assertGreaterEqual(report["cash"], 0)

    def test_fractional_adjusted_position_roundtrip_and_full_sell(self):
        pp = PaperPortfolio(topk=1, nd=1, initial_cash=10000)
        pp.plan_signal("2026-10-09", "2026-10-12", [("A", 1.0), ("B", 0.0)])
        buy = pp.execute_pending("2026-10-12", {"A": 10.0}, factors={"A": 3.0})
        shares = buy["executed_buy"][0]["shares"]
        self.assertNotEqual(shares, int(shares))
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "factor-state.json")
            pp.save(path)
            restored = PaperPortfolio.load(path)
        self.assertEqual(restored.positions["A"]["shares"], shares)
        restored.plan_signal("2026-10-12", "2026-10-13", [("B", 1.0), ("A", 0.0)])
        report = restored.execute_pending(
            "2026-10-13", {"A": 12.0, "B": 10.0},
            factors={"B": 1.0}, close_prices={"B": 11.0},
        )
        self.assertEqual(report["executed_sell"][0]["shares"], shares)
        self.assertNotIn("A", restored.positions)
        self.assertAlmostEqual(report["portfolio_value_close"],
                               restored.cash + sum(p["shares"] * p["last_price"]
                                                   for p in restored.positions.values()), places=2)


if __name__ == "__main__":
    unittest.main()
