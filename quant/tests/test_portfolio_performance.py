import unittest

import numpy as np
import pandas as pd

from portfolio_performance import portfolio_performance


class PortfolioPerformanceTests(unittest.TestCase):
    def test_compounded_metrics_from_qlib_account(self):
        idx = pd.to_datetime(["2025-01-02", "2025-06-30", "2026-01-02"])
        net = pd.Series([0.10, -0.05, 0.02], index=idx)
        cost = pd.Series([0.001, 0.001, 0.001], index=idx)
        gross = net + cost
        bench = pd.Series([0.02, 0.00, 0.01], index=idx)
        account = 100.0 * (1.0 + net).cumprod()
        report = pd.DataFrame({
            "return": gross,
            "cost": cost,
            "bench": bench,
            "account": account,
        })
        out = portfolio_performance(
            report, initial_cash=100.0, backtest_start="2025-01-02"
        )

        years = (idx[-1] - idx[0]).days / 365.2425
        nav_end = float((1.0 + net).prod())
        bench_end = float((1.0 + bench).prod())
        self.assertAlmostEqual(out["strategy_total_return"], nav_end - 1.0, places=6)
        self.assertAlmostEqual(out["benchmark_total_return"], bench_end - 1.0, places=6)
        self.assertAlmostEqual(out["strategy_cagr"], nav_end ** (1 / years) - 1, places=6)
        self.assertAlmostEqual(
            out["relative_excess_cagr"], (nav_end / bench_end) ** (1 / years) - 1, places=6
        )
        self.assertAlmostEqual(out["account_return_max_error"], 0.0, places=10)

        expected_sharpe = net.mean() / net.std(ddof=1) * np.sqrt(238)
        active = net - bench
        expected_ir = active.mean() / active.std(ddof=1) * np.sqrt(238)
        self.assertAlmostEqual(out["sharpe"], expected_sharpe, places=6)
        self.assertAlmostEqual(out["information_ratio"], expected_ir, places=6)

    def test_max_drawdown_includes_initial_nav(self):
        idx = pd.to_datetime(["2026-01-05", "2026-01-06"])
        net = pd.Series([-0.10, 0.05], index=idx)
        report = pd.DataFrame({
            "return": net,
            "cost": [0.0, 0.0],
            "bench": [0.0, 0.0],
            "account": [90.0, 94.5],
        }, index=idx)
        out = portfolio_performance(report, initial_cash=100.0, backtest_start="2026-01-05")
        self.assertAlmostEqual(out["strategy_max_drawdown"], -0.10, places=6)

    def test_falls_back_to_compounded_return_without_account(self):
        idx = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"])
        report = pd.DataFrame({
            "return": [0.02, -0.01, 0.03],
            "cost": [0.001, 0.001, 0.001],
            "bench": [0.0, 0.0, 0.0],
        }, index=idx)
        out = portfolio_performance(report)
        expected = (1.019 * 0.989 * 1.029) - 1.0
        self.assertAlmostEqual(out["strategy_total_return"], expected, places=6)
        self.assertIsNone(out["account_return_max_error"])

    def test_account_cross_check_detects_mismatch(self):
        idx = pd.to_datetime(["2026-01-05", "2026-01-06"])
        report = pd.DataFrame({
            "return": [0.01, 0.01],
            "cost": [0.0, 0.0],
            "bench": [0.0, 0.0],
            "account": [101.0, 105.0],
        }, index=idx)
        out = portfolio_performance(report, initial_cash=100.0)
        self.assertGreater(out["account_return_max_error"], 0.02)

    def test_missing_columns_fail_closed(self):
        report = pd.DataFrame({"return": [0.01], "cost": [0.0]})
        with self.assertRaises(ValueError):
            portfolio_performance(report)


if __name__ == "__main__":
    unittest.main()
