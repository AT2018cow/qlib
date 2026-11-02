"""Sanity tests for G1 portfolio overlays (no Qlib provider needed)."""

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code" / "research" / "regime_reselection"))
from run_g1_overlays import apply_drawdown_breaker, apply_trend_gate, combine_sleeves


def _flat_report(index, daily=0.001):
    return pd.DataFrame(
        {
            "return": daily,
            "cost": 0.0,
            "bench": 0.0,
            "turnover": 0.0,
        },
        index=index,
    )


class OverlayTests(unittest.TestCase):
    def test_trend_gate_is_noop_in_uptrend(self):
        index = pd.date_range("2020-01-01", periods=400, freq="B")
        close = pd.Series(np.linspace(1.0, 2.0, len(index)), index=index)
        report = _flat_report(index[-100:])
        out, weight = apply_trend_gate(report, close)
        self.assertTrue(np.allclose(weight.to_numpy(), 1.0))
        self.assertTrue(np.allclose(out["return"].to_numpy(), report["return"].to_numpy()))
        self.assertTrue(np.allclose(out["cost"].to_numpy(), report["cost"].to_numpy()))

    def test_drawdown_breaker_is_noop_without_drawdown(self):
        index = pd.date_range("2021-01-01", periods=100, freq="B")
        report = _flat_report(index)
        out, weight = apply_drawdown_breaker(report, soft=0.12, hard=0.18)
        self.assertTrue(np.allclose(weight.to_numpy(), 1.0))
        self.assertTrue(np.allclose(out["return"].to_numpy(), report["return"].to_numpy()))
        self.assertTrue(np.allclose(out["cost"].to_numpy(), report["cost"].to_numpy()))

    def test_combine_single_sleeve_is_identity(self):
        index = pd.date_range("2021-01-01", periods=60, freq="B")
        report = _flat_report(index)
        out = combine_sleeves({"csi300": report}, "equal", rebalance_every=20, cost_bps=10.0)
        self.assertTrue(np.allclose(out["return"].to_numpy(), report["return"].to_numpy()))
        self.assertTrue(np.allclose(out["cost"].to_numpy(), report["cost"].to_numpy()))
        self.assertTrue(np.allclose(out["bench"].to_numpy(), report["bench"].to_numpy()))


if __name__ == "__main__":
    unittest.main()
