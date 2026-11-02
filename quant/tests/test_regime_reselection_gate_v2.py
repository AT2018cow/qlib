"""Tests for the G2 v2 gates (stdlib only)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code" / "research" / "regime_reselection"))
from gates import d2_v2, d7_v2, evaluate_s02_v2


class GateV2Tests(unittest.TestCase):
    def test_d2_tolerance_and_compensation(self):
        base_2018, base_full = -0.2081, -0.5032
        # 1.94pp deeper in 2018, 10.5pp better full period -> pass
        self.assertTrue(d2_v2(-0.2275, base_2018, -0.3976, base_full))
        # 2.1pp deeper -> fail
        self.assertFalse(d2_v2(-0.2291, base_2018, -0.3976, base_full))
        # only 4pp full-period improvement -> fail
        self.assertFalse(d2_v2(-0.2275, base_2018, -0.4632, base_full))
        with self.assertRaises(ValueError):
            d2_v2(-0.22, base_2018, -0.40, base_full, mdd_tolerance=-0.01)

    def test_d7_v2_tolerances(self):
        base_cal, base_sharpe = 0.338, 0.693
        # S02 actual-cost values
        self.assertTrue(d7_v2(0.339, base_cal, 0.682, base_sharpe, 0.1018))
        # Sharpe 0.02 tolerance boundary
        self.assertTrue(d7_v2(0.339, base_cal, 0.673, base_sharpe, 0.1018))
        # Sharpe too low
        self.assertFalse(d7_v2(0.339, base_cal, 0.6729, base_sharpe, 0.1018))
        # +20bp not positive
        self.assertFalse(d7_v2(0.339, base_cal, 0.682, base_sharpe, 0.0))

    def test_evaluate_s02_v2(self):
        base = {"cagr": 0.170061, "mdd": -0.503191, "sharpe": 0.692619, "2018_mdd": -0.208128, "d1": False, "d3": False}
        cand = {"cagr": 0.209836, "mdd": -0.397574, "sharpe": 0.862784, "2018_mdd": -0.227523, "d1": True, "d3": True}
        cost = {"calmar": 0.338946, "sharpe": 0.682049, "cagr20": 0.101753}
        out = evaluate_s02_v2(base, cand, cost)
        self.assertTrue(out["d1"])
        self.assertTrue(out["d3"])
        self.assertTrue(out["d2_v2"])
        self.assertTrue(out["d7_v2"])
        self.assertGreater(out["d2_margin_pp"], 0)
        self.assertGreater(out["d7_calmar_margin"], 0)
        self.assertGreater(out["d7_sharpe_margin"], 0)


if __name__ == "__main__":
    unittest.main()
