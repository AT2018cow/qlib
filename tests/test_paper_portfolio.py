import os
import tempfile
import unittest

from paper_portfolio import PaperPortfolio


class PaperPortfolioTests(unittest.TestCase):
    def setUp(self):
        self.pp = PaperPortfolio(topk=3, nd=1, initial_cash=100000, risk_degree=0.95)
        self.ranking = [("A", 0.5), ("B", 0.4), ("C", 0.3), ("D", 0.2), ("E", 0.1)]
        self.prices = {"A": 10.0, "B": 20.0, "C": 25.0, "D": 40.0, "E": 50.0}

    def _plan_execute(self, signal_date, execution_date, ranking=None, buy_tradable=None, sell_tradable=None):
        self.pp.plan_signal(signal_date, execution_date, ranking or self.ranking)
        return self.pp.execute_pending(
            execution_date,
            self.prices,
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
        r = self.pp.execute_pending("2026-01-06", self.prices)
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
        r = self.pp.execute_pending("2026-01-06", self.prices)
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
            self.pp.execute_pending("2026-01-06", self.prices)

    def test_exact_score_ties_use_instrument_ascending(self):
        ranking = [("C", 0.5), ("A", 0.5), ("B", 0.5), ("D", 0.4)]
        self.pp.plan_signal("2026-01-04", "2026-01-05", ranking)
        r = self.pp.execute_pending("2026-01-05", self.prices)
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
        r = self.pp.execute_pending("2026-01-06", self.prices)
        self.assertEqual(r["planned_sell"], ["C"])
        self.assertEqual(r["planned_buy"], ["D"])


if __name__ == "__main__":
    unittest.main()
