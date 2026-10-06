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
        self.assertIn('"protocol": "continuous_account_board_aware_v4_cny_tick"', src)
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
        self.assertIn('"board_aware_open_bootstrap_v4_cny_tick"', src)
        self.assertIn("unanchored_pending_batch_c_rerun", src)
        self.assertNotIn("expected_excess = -0.0223", src)
        # Alpha158 类默认 infer 处理器必须镜像（review 2026-10-05：[] 与生产不符）
        self.assertIn('"ProcessInf"', src)
        self.assertIn('"ZScoreNorm"', src)
        self.assertIn('"Fillna"', src)
        self.assertNotIn('"infer_processors": []', src)


    def test_batch_c_bootstraps_previous_signal_day(self):
        src = function_source("modal_qlib_cn_a10g.py", "batch_c_window")
        self.assertIn("signal_start = _cal[_te_i - 1]", src)
        self.assertIn('seg["test"] = [signal_start, te_e]', src)
        self.assertIn("start_time=te_s", src)

    def test_p2_bootstraps_previous_signal_day(self):
        src = function_source("modal_qlib_cn_a10g.py", "p2_rolling")
        self.assertIn('seg["test"] = [_cal[_te_i - 1], te_e]', src)
        self.assertIn("start_time=te_s", src)

    def test_daily_cron_isolates_both_pools_and_repairs_missing_files(self):
        src = function_source("modal_qlib_cn_a10g.py", "daily_cron")
        self.assertIn('pool_errors["csi1000"]', src)
        self.assertIn('pool_errors["chinext"]', src)
        self.assertIn("if res is None and res_chi is None", src)
        self.assertIn("missing_files = {}", src)
        self.assertIn("Same-date artifact changed", src)
        self.assertIn("files=missing_files", src)

    def test_runtime_lineage_participates_in_cache_signature(self):
        src = function_source("qlib_live_retrain.py", "cache_signature")
        self.assertIn("runtime_lineage", src)
        daily = function_source("modal_qlib_cn_a10g.py", "daily_standalone")
        self.assertIn("runtime_lineage = _runtime_cache_lineage()", daily)
        self.assertIn("saved.get(\"runtime_lineage\") != runtime_lineage", daily)


    def test_r24_research_backtests_use_custom_exchange_config(self):
        src = (ROOT / "board_execution.py").read_text()
        self.assertIn('"exchange": {', src)
        self.assertIn('"class": "BoardAwareExchange"', src)
        self.assertIn('"limit_threshold": None', src)
        main = (ROOT / "modal_qlib_cn_a10g.py").read_text()
        self.assertNotIn("exchange_kwargs=research_exchange(),", main)
        freq = (ROOT / "freq_experiment.py").read_text()
        self.assertNotIn("exchange_kwargs=research_exchange(),", freq)

    def test_r27_live_cache_checks_provider_prefix_fingerprint(self):
        daily = function_source("modal_qlib_cn_a10g.py", "daily_standalone")
        self.assertIn("provider_training_fingerprint", daily)
        self.assertIn("data_revision", daily)
        self.assertIn('"data_fingerprint"', daily)
        helper = function_source("qlib_live_retrain.py", "provider_training_fingerprint")
        self.assertIn("cutoff_i", helper)
        self.assertIn("--members--", helper)
        self.assertIn("--features--", helper)

    def test_r28_daily_cron_publishes_paper_portfolio_and_commits_state_after_push(self):
        cron = function_source("modal_qlib_cn_a10g.py", "daily_cron")
        self.assertIn("_apply_paper_portfolio", cron)
        self.assertIn("_paper_portfolio.json", cron)
        self.assertIn("_paper_portfolio_chinext.json", cron)
        self.assertIn("_persist_paper_states()", cron)
        self.assertLess(cron.index("push_files("), cron.rindex("_persist_paper_states()"))
        daily = function_source("modal_qlib_cn_a10g.py", "daily_standalone")
        self.assertIn('"ranking_full": ranking_full', daily)
        self.assertIn('"paper_context": paper_context', daily)


    def test_batch_c_artifact_has_board_aware_protocol(self):
        src = function_source("modal_qlib_cn_a10g.py", "batch_c")
        self.assertIn('"protocol": "board_aware_open_bootstrap_v4_cny_tick"', src)
        worker = function_source("modal_qlib_cn_a10g.py", "batch_c_window")
        self.assertIn('"protocol": "board_aware_open_bootstrap_v4_cny_tick"', worker)

    def test_execution_attribution_reuses_frozen_signal(self):
        src = function_source("freq_experiment.py", "execution_attribution_driver")
        self.assertEqual(src.count("freq_window.map(jobs)"), 1)
        self.assertIn("REF_LEGACY_EXACT", src)
        self.assertIn("A_LEGACY_ORACLE_CURRENT_DIRECTION", src)
        self.assertIn("B_UNIFORM_OPEN_095", src)
        self.assertIn("C_BOARD_AWARE", src)
        self.assertIn("D_BOARD_AWARE_HIGH_OPEN_5", src)
        self.assertIn("E_BOARD_AWARE_NO_CHINEXT_STAR", src)
        self.assertIn('"protocol": "execution_attribution_v2_cny_tick"', src)

    def test_execution_attribution_defines_clean_deltas(self):
        src = function_source("freq_experiment.py", "_run_execution_protocol")
        self.assertIn("legacy_scalar_exchange()", src)
        self.assertIn("uniform_open_exchange(", src)
        self.assertIn("high_open_block=0.05", src)
        self.assertIn("_filter_signal_excluding_growth_boards", src)



    def test_paper_execution_uses_factor_and_no_high_open_overlay(self):
        src = function_source("modal_qlib_cn_a10g.py", "_paper_execution_context")
        self.assertIn('"$factor"', src)
        self.assertIn("factors=factors", src)
        self.assertIn("high_open_block=None", src)
        self.assertNotIn("high_open_block=0.05", src)

    def test_frequency_driver_persists_raw_daily_report(self):
        src = function_source("freq_experiment.py", "freq_driver")
        self.assertIn("_write_report_artifact(", src)
        self.assertIn('"report_artifact": report_artifact', src)
        self.assertIn("reports_dir", src)

    def test_phase_sensitivity_uses_common_window_and_all_offsets(self):
        src = function_source("freq_experiment.py", "retrain_phase_sensitivity_driver")
        self.assertIn("list(range(freq))", src)
        self.assertIn("execution_start = cal[start_i + 1]", src)
        self.assertIn("_phase_jobs(", src)
        self.assertIn("audit_only_do_not_select_best_phase", src)
        self.assertIn("_write_report_artifact(", src)
        helper = function_source("freq_experiment.py", "_phase_jobs")
        self.assertIn("first_i = start_i - phase", helper)
        self.assertIn('"phase": phase', helper)

    def test_frequency_driver_reports_compounded_portfolio_metrics(self):
        src = function_source("freq_experiment.py", "freq_driver")
        self.assertIn("portfolio_performance(", src)
        self.assertIn('"strategy_cagr": perf["strategy_cagr"]', src)
        self.assertIn('"relative_excess_cagr": perf["relative_excess_cagr"]', src)
        self.assertIn('"strategy_max_drawdown": perf["strategy_max_drawdown"]', src)
        self.assertIn('"sharpe": perf["sharpe"]', src)
        self.assertIn('"information_ratio": perf["information_ratio"]', src)
        self.assertIn("legacy_ann_excess_arithmetic", src)
        self.assertNotIn('"max_drawdown": round(float((cum', src)

    def test_execution_attribution_primary_delta_uses_relative_cagr(self):
        src = function_source("freq_experiment.py", "execution_attribution_driver")
        self.assertIn('"metric": "relative_excess_cagr"', src)
        self.assertIn("legacy_attribution_arithmetic_pp", src)

    def test_batch_c_does_not_claim_continuous_account_metrics(self):
        worker = function_source("modal_qlib_cn_a10g.py", "batch_c_window")
        self.assertIn("portfolio_performance(", worker)
        self.assertIn('"strategy_max_drawdown": perf["strategy_max_drawdown"]', worker)
        summary = function_source("modal_qlib_cn_a10g.py", "batch_c")
        self.assertIn('"continuous_account_cagr": None', summary)
        self.assertIn('"continuous_account_max_drawdown": None', summary)
        self.assertIn("ann_excess_approx_legacy_arithmetic", summary)


if __name__ == "__main__":
    unittest.main()
