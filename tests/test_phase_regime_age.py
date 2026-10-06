from pathlib import Path
import unittest

import numpy as np
import pandas as pd

from phase_regime_age_core import (
    aggregate_gap_by,
    attribution_windows,
    benchmark_regime_by_year,
    decision_summary,
    market_regime_frame,
    model_age_frame,
    pairwise_daily_frame,
)

ROOT = Path(__file__).resolve().parents[1]


def _report(index, returns, costs=None, bench=None):
    n = len(index)
    if costs is None:
        costs = [0.0] * n
    if bench is None:
        bench = [0.0] * n
    return pd.DataFrame(
        {
            "return": returns,
            "cost": costs,
            "bench": bench,
        },
        index=pd.to_datetime(index),
    )


class PhaseRegimeAgeTests(unittest.TestCase):
    def test_model_age_uses_signal_day_before_execution(self):
        calendar = [
            "2024-01-02",
            "2024-01-03",
            "2024-01-04",
            "2024-01-05",
            "2024-01-08",
            "2024-01-09",
            "2024-01-10",
            "2024-01-11",
        ]
        execution = pd.to_datetime(
            [
                "2024-01-03",
                "2024-01-04",
                "2024-01-05",
                "2024-01-08",
                "2024-01-09",
                "2024-01-10",
            ]
        )
        out = model_age_frame(
            execution,
            calendar=calendar,
            retrain_dates=["2024-01-02", "2024-01-08"],
            freq=5,
        )
        self.assertEqual(out.loc["2024-01-03", "signal_date"], "2024-01-02")
        self.assertEqual(out.loc["2024-01-03", "model_age_sessions"], 0)
        self.assertEqual(out.loc["2024-01-08", "signal_date"], "2024-01-05")
        self.assertEqual(out.loc["2024-01-08", "model_age_sessions"], 3)
        self.assertEqual(
            out.loc["2024-01-09", "active_retrain_asof"], "2024-01-08"
        )
        self.assertEqual(out.loc["2024-01-09", "model_age_sessions"], 0)

    def test_regime_features_are_shifted_one_execution_day(self):
        idx = pd.bdate_range("2024-01-02", periods=24)
        bench = np.zeros(len(idx))
        bench[20] = 0.50
        report = _report(idx, [0.0] * len(idx), bench=bench)
        regime = market_regime_frame(report, lookback=20)

        # Day 21's current +50% benchmark return must not enter its own label.
        self.assertEqual(regime.iloc[20]["trend_regime"], "flat_pm5pct")
        self.assertAlmostEqual(regime.iloc[20]["trailing_return_20"], 0.0, places=12)

        # It becomes visible only on the following execution day.
        self.assertEqual(regime.iloc[21]["trend_regime"], "up_5pct_plus")
        self.assertGreater(regime.iloc[21]["trailing_return_20"], 0.49)

    def test_market_regime_thresholds_are_deterministic(self):
        idx = pd.bdate_range("2024-01-02", periods=30)
        bench = [0.0] * 30
        report = _report(idx, [0.0] * 30, bench=bench)
        regime = market_regime_frame(report, lookback=20)
        valid = regime.iloc[20:]
        self.assertTrue((valid["trend_regime"] == "flat_pm5pct").all())
        self.assertTrue((valid["vol_regime"] == "low_lt20").all())
        self.assertTrue((valid["drawdown_regime"] == "shallow_lt10").all())
        self.assertTrue((valid["stress_regime"] == "normal").all())

    def test_pairwise_daily_frame_carries_age_and_regime_without_retraining(self):
        idx = pd.bdate_range("2024-01-02", periods=4)
        focus = _report(idx, [0.01, 0.00, -0.01, 0.02], costs=[0.001] * 4)
        comp = _report(idx, [0.00, 0.01, -0.005, 0.01], costs=[0.0005] * 4)
        focus_age = pd.DataFrame(
            {
                "model_age_sessions": [10, 11, 12, 13],
                "model_age_bin": ["10-14"] * 4,
            },
            index=idx,
        )
        comp_age = pd.DataFrame(
            {
                "model_age_sessions": [2, 3, 4, 5],
                "model_age_bin": ["00-04", "00-04", "00-04", "05-09"],
            },
            index=idx,
        )
        regime = pd.DataFrame(
            {
                "trailing_return_20": [0.0] * 4,
                "trailing_vol_20_ann": [0.1] * 4,
                "prior_drawdown": [0.0] * 4,
                "trend_regime": ["flat_pm5pct"] * 4,
                "vol_regime": ["low_lt20"] * 4,
                "drawdown_regime": ["shallow_lt10"] * 4,
                "stress_regime": ["normal"] * 4,
            },
            index=idx,
        )
        out = pairwise_daily_frame(
            focus,
            comp,
            focus_age=focus_age,
            comparator_age=comp_age,
            regime=regime,
        )
        self.assertTrue((out["relative_age_state"] == "focus_much_older").all())
        self.assertEqual(out.iloc[0]["model_age_delta"], 8)
        expected = np.log1p(0.009) - np.log1p(-0.0005)
        self.assertAlmostEqual(out.iloc[0]["net_log_gap"], expected, places=12)

    def test_attribution_windows_separate_regime_years(self):
        idx = pd.to_datetime(
            [
                "2021-01-04",
                "2022-01-04",
                "2022-06-01",
                "2023-01-04",
                "2023-06-01",
                "2024-01-04",
            ]
        )
        frame = pd.DataFrame(
            {
                "gross_log_gap": [0.1, -0.2, -0.1, -0.2, -0.1, 0.1],
                "net_log_gap": [0.1, -0.2, -0.1, -0.2, -0.1, 0.1],
                "cost_effect_log_gap": [0.0] * 6,
                "focus_age_bin": ["00-04"] * 6,
                "comparator_age_bin": ["05-09"] * 6,
                "relative_age_state": ["focus_much_younger"] * 6,
                "trend_regime": ["flat_pm5pct"] * 6,
                "vol_regime": ["low_lt20"] * 6,
                "drawdown_regime": ["shallow_lt10"] * 6,
                "stress_regime": ["normal"] * 6,
                "year": idx.year,
            },
            index=idx,
        )
        out = attribution_windows(frame, regime_years=(2022, 2023))
        self.assertEqual(out["regime_years"]["n_days"], 4)
        self.assertAlmostEqual(out["regime_years"]["total_net_log_gap"], -0.6)
        self.assertAlmostEqual(out["full_sample"]["total_net_log_gap"], -0.4)
        self.assertAlmostEqual(
            out["regime_years"]["share_of_full_net_log_gap"], 1.5
        )

    def test_decision_summary_distinguishes_contribution_from_intensity(self):
        pairwise = {
            "6": {
                "regime_years": {
                    "share_of_full_net_log_gap": 1.2,
                    "by_dimension": {
                        "relative_age_state": [
                            {
                                "relative_age_state": "similar_within4",
                                "net_log_gap": -0.30,
                                "negative_gap_share": 0.75,
                                "mean_daily_net_log_gap": -0.001,
                            },
                            {
                                "relative_age_state": "focus_much_older",
                                "net_log_gap": -0.10,
                                "negative_gap_share": 0.25,
                                "mean_daily_net_log_gap": -0.005,
                            },
                        ],
                        "stress_regime": [
                            {
                                "stress_regime": "normal",
                                "net_log_gap": -0.25,
                                "negative_gap_share": 0.625,
                                "mean_daily_net_log_gap": -0.001,
                            },
                            {
                                "stress_regime": "stress",
                                "net_log_gap": -0.15,
                                "negative_gap_share": 0.375,
                                "mean_daily_net_log_gap": -0.004,
                            },
                        ],
                    },
                }
            }
        }
        out = decision_summary(pairwise)["6"]
        self.assertEqual(out["dominant_negative_age_state"], "similar_within4")
        self.assertEqual(out["worst_mean_daily_age_state"], "focus_much_older")
        self.assertEqual(out["dominant_negative_stress_state"], "normal")
        self.assertEqual(out["worst_mean_daily_stress_state"], "stress")

    def test_wrapper_is_zero_fit_and_no_backtest(self):
        src = (ROOT / "phase_regime_age_diagnostic.py").read_text()
        forbidden = [
            "lightgbm",
            "freq_window",
            "retrain_phase_sensitivity_driver",
            "reproducibility_gate_driver",
            "_run_signal_backtest",
            ".fit(",
            ".map(",
        ]
        for token in forbidden:
            self.assertNotIn(token, src)
        self.assertIn('"zero_model_fits": True', src)
        self.assertIn('"zero_signal_generation": True', src)
        self.assertIn("_load_report", src)
        self.assertIn("_retrain_dates", src)


if __name__ == "__main__":
    unittest.main()
