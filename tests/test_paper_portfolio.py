import unittest

from paper_portfolio import PaperPortfolio


class PaperPortfolioTests(unittest.TestCase):
    def setUp(self):
        self.pp = PaperPortfolio(topk=3, nd=1, initial_cash=100000)
        self.prices = {"A": 10.0, "B": 20.0, "C": 30.0, "D": 40.0, "E": 50.0}

    def test_first_day_fills_topk(self):
        ranking = [("A", 0.5), ("B", 0.4), ("C", 0.3), ("D", 0.2)]
        r = self.pp.step("2026-01-05", ranking, self.prices, self.prices)
        self.assertEqual(len(r["held"]), 3)
        self.assertEqual(set(r["held"]), {"A", "B", "C"})
        self.assertEqual(r["buys"], ["A", "B", "C"])

    def test_dropout_replaces_worst(self):
        # Day 1: fill with A, B, C
        self.pp.step("2026-01-05", [("A", 0.5), ("B", 0.4), ("C", 0.3)], self.prices, self.prices)
        # Day 2: D rises above C → C should be dropped, D bought (nd=1)
        p2 = {**self.prices, "D": 40.0}
        r = self.pp.step("2026-01-06", [("A", 0.5), ("B", 0.4), ("D", 0.35), ("C", 0.2)], p2, self.prices)
        self.assertIn("C", r["sells"])
        self.assertIn("D", r["buys"])
        self.assertNotIn("C", r["held"])
        self.assertIn("D", r["held"])

    def test_nd_limits_replacement(self):
        self.pp = PaperPortfolio(topk=3, nd=1, initial_cash=100000)
        self.pp.step("2026-01-05", [("A", 0.5), ("B", 0.4), ("C", 0.3)], self.prices, self.prices)
        # Day 2: A,B,C all drop out; only 1 replacement allowed (nd=1)
        ranking = [("D", 0.9), ("E", 0.8), ("F", 0.7), ("A", 0.1), ("B", 0.05), ("C", 0.01)]
        p2 = {**self.prices, "D": 40.0, "E": 50.0, "F": 60.0}
        r = self.pp.step("2026-01-06", ranking, p2, self.prices)
        # nd=1: sell 1 worst, buy 1 best → 2 of 3 holdings unchanged
        self.assertEqual(len(r["sells"]), 1)
        self.assertEqual(len(r["buys"]), 1)
        self.assertEqual(len(r["held"]), 3)

    def test_state_roundtrip(self):
        self.pp.step("2026-01-05", [("A", 0.5), ("B", 0.4), ("C", 0.3)], self.prices, self.prices)
        import tempfile, os
        f = os.path.join(tempfile.mkdtemp(), "state.json")
        self.pp.save(f)
        pp2 = PaperPortfolio.load(f)
        self.assertEqual(pp2.cash, self.pp.cash)
        self.assertEqual(set(pp2.positions), set(self.pp.positions))

    def test_no_double_step(self):
        self.pp.step("2026-01-05", [("A", 0.5)], self.prices, self.prices)
        with self.assertRaises(ValueError):
            self.pp.step("2026-01-05", [("A", 0.5)], self.prices, self.prices)


if __name__ == "__main__":
    unittest.main()
