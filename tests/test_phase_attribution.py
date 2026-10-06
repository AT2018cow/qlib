import math
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

from phase_attribution_core import (
    annual_attribution,
    pairwise_gap_attribution,
    period_attribution,
    retrain_event_study,
)

ROOT = Path(__file__).resolve().parents[1]


def _report(index, returns, costs, bench=None, turnover=None):
    n = len(index)
    if bench is None:
        bench = [0.0] * n
    if turnover is None:
        turnover = [0.01] * n
    return pd.DataFrame(
        {
            "return": returns,
            "cost": costs,
            "bench": bench,
            "turnover": turnover,
        },
        index=pd.to_datetime(index),
    )


class PhaseAttributionTests(unittest.TestCase):
    def test_period_attribution_separates_gross_and_net(self):
        report = _report(
            ["2024-01-02", "2024-01-03", "2024-01-04"],
            [0.02, -0.01, 0.015],
            [0.001, 0.001, 0.001],
            bench=[0.005, 0.0, 0.002],
            turnover=[0.01, 0.02, 0.03],
        )
        out = period_attribution(report)
        gross = np.prod(1 + np.array([0.02, -0.01, 0.015])) - 1
        net = np.prod(1 + np.array([0.019, -0.011, 0.014])) - 1
        self.assertAlmostEqual(out["gross_strategy_total_return"], gross, places=8)
        self.assertAlmostEqual(out["net_strategy_total_return"], net, places=8)
        self.assertGreater(out["cost_drag_cagr_pp"], 0)
        self.assertAlmostEqual(out["mean_turnover"], 0.02, places=8)
        self.assertAlmostEqual(out["total_cost_sum"], 0.003, places=8)

    def test_pairwise_gap_attributes_cost_when_gross_paths_match(self):
        idx = pd.bdate_range("2024-01-02", periods=20)
        gross = [0.001] * len(idx)
        focus = _report(idx, gross, [0.0010] * len(idx))
        comparator = _report(idx, gross, [0.0002] * len(idx))
        out = pairwise_gap_attribution(focus, comparator)
        self.assertAlmostEqual(out["final_gross_log_gap"], 0.0, places=10)
        self.assertLess(out["final_net_log_gap"], 0.0)
        self.assertAlmostEqual(out["cost_effect_share_of_net_gap"], 1.0, places=6)

    def test_pairwise_gap_tracks_year_concentration_and_milestones(self):
        idx = pd.to_datetime(
            ["2023-01-03", "2023-12-20", "2024-01-03", "2024-12-20"]
        )
        focus = _report(idx, [-0.02, -0.02, -0.005, -0.005], [0.0] * 4)
        comparator = _report(idx, [0.0] * 4, [0.0] * 4)
        out = pairwise_gap_attribution(focus, comparator)
        self.assertEqual(
            out["negative_gap_concentration"]["top1_negative_years"], [2023]
        )
        self.assertGreater(
            out["negative_gap_concentration"]["top1_negative_year_share"], 0.7
        )
        self.assertIsNotNone(
            out["gap_formation_milestones"]["first_50pct_final_gap_date"]
        )

    def test_retrain_event_study_uses_next_report_session(self):
        idx = pd.bdate_range("2024-01-02", periods=8)
        turnover = [0.01] * 8
        turnover[2] = 0.05  # Jan 4, next session after Jan 3 retrain.
        report = _report(idx, [0.0] * 8, [0.0001] * 8, turnover=turnover)
        out = retrain_event_study(
            report,
            retrain_dates=["2024-01-03"],
            window=2,
        )
        first = out["by_offset"][0]
        self.assertEqual(first["first_date"], "2024-01-04")
        self.assertAlmostEqual(first["mean_turnover"], 0.05, places=8)
        self.assertGreater(out["first_execution_day_turnover_ratio_to_non_event"], 1)

    def test_event_study_skips_stale_pre_window_retrain(self):
        idx = pd.bdate_range("2024-01-08", periods=6)
        report = _report(idx, [0.0] * 6, [0.0] * 6)
        out = retrain_event_study(
            report,
            retrain_dates=["2023-12-28", "2024-01-09"],
            window=1,
        )
        self.assertEqual(out["n_retrain_events"], 1)
        self.assertEqual(out["by_offset"][0]["first_date"], "2024-01-10")

    def test_annual_attribution_marks_final_partial_year(self):
        report = _report(
            ["2025-01-02", "2025-12-22", "2026-01-05", "2026-09-30"],
            [0.01, 0.01, 0.01, 0.01],
            [0.0] * 4,
        )
        rows = annual_attribution(report)
        self.assertFalse(rows[0]["partial_year"])
        self.assertTrue(rows[1]["partial_year"])

    def test_modal_wrapper_is_zero_fit_only(self):
        src = (ROOT / "phase_attribution_diagnostic.py").read_text()
        forbidden = [
            "lightgbm",
            "freq_window",
            "retrain_phase_sensitivity_driver",
            "reproducibility_gate_driver",
            ".fit(",
            ".map(",
        ]
        for token in forbidden:
            self.assertNotIn(token, src)
        self.assertIn('"zero_model_fits": True', src)
        self.assertIn("_load_and_verify_report", src)
        self.assertIn("_assert_canonical_metrics", src)


if __name__ == "__main__":
    unittest.main()
