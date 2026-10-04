import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def function_source(path: str, name: str) -> str:
    text = (ROOT / path).read_text()
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(text, node) or ""
    raise AssertionError(f"{name} not found in {path}")


class ReviewRegressionTests(unittest.TestCase):
    def test_batch_c_keeps_label_purge(self):
        src = function_source("modal_qlib_cn_a10g.py", "batch_c_window")
        self.assertIn("purge_cfg_splits", src)
        self.assertIn("horizon=20", src)

    def test_frequency_worker_does_not_backtest_or_reset_account(self):
        src = function_source("freq_experiment.py", "freq_window")
        self.assertNotIn("normal_backtest", src)
        self.assertNotIn("account=100000000", src)
        self.assertIn('seg["test"] = [args["retrain_asof"], args["signal_end"]]', src)
        self.assertIn("pred_zlib_pickle", src)

    def test_frequency_driver_runs_one_continuous_account(self):
        src = function_source("freq_experiment.py", "freq_driver")
        self.assertIn("pd.concat(chunks)", src)
        self.assertIn("execution_start = cal[start_i + 1]", src)
        self.assertIn('"protocol": "continuous_account_v2"', src)
        self.assertEqual(src.count("normal_backtest("), 1)

    def test_independent_recheck_has_its_own_maturity_guard(self):
        src = function_source("modal_qlib_cn_a10g.py", "independent_recheck")
        self.assertIn("_last_matured_before", src)
        self.assertIn("train labels leak into validation", src)
        self.assertIn("validation labels leak into test", src)
        self.assertNotIn("purge_cfg_splits(", src)

    def test_independent_recheck_matches_current_alpha158_config(self):
        src = function_source("modal_qlib_cn_a10g.py", "independent_recheck")
        self.assertIn('"learning_rate": 0.1', src)
        self.assertIn('"num_leaves": 250', src)
        self.assertIn('"CSZScoreNorm"', src)
        self.assertNotIn('"CSRankNorm"', src)
        self.assertIn("expected_excess = -0.0298", src)


if __name__ == "__main__":
    unittest.main()
