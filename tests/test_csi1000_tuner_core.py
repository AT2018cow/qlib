import unittest

from csi1000_tuner_core import (
    BASELINE_TUNABLE_PARAMS,
    REFERENCE_PHASES,
    RETRAIN_FREQUENCY,
    SCREEN_EXECUTION_SESSIONS,
    build_candidate_spec,
    build_stage_a_folds,
    expanded_candidate_specs,
    frozen_protocol,
    rank_candidates,
    ranking_contract,
    reserved_tail,
    select_stage_b_candidates,
    smoke_candidate_specs,
    summarize_candidate,
    validate_reproducibility_pairs,
)


def _calendar():
    # Synthetic daily trading calendar is sufficient for boundary arithmetic tests.
    return [f"2026-{month:02d}-{day:02d}" for month in range(1, 13) for day in range(1, 29)]


def _repro_row(fold_id, *, signal="a", report="b", best_iteration=123):
    candidate = build_candidate_spec(dict(BASELINE_TUNABLE_PARAMS))
    return {
        "candidate_id": candidate["candidate_id"],
        "candidate": candidate,
        "snapshot_token": "s" * 64,
        "fold_id": fold_id,
        "fold": {"fold_id": fold_id},
        "model_config_sha256": "m" * 64,
        "best_iteration": best_iteration,
        "signal_sha256": signal * 64,
        "report_artifact": {"content_sha256": report * 64},
        "account_return_max_error": 0.0,
    }


def _metric_row(identifier, rel, *, kind="fold", error=0.0, turnover=0.05, cost=0.02):
    return {
        f"{kind}_id": identifier,
        "metric_version": "portfolio_compound_v1",
        "relative_excess_cagr": rel,
        "strategy_max_drawdown": -0.20,
        "sharpe": 0.60,
        "information_ratio": 0.20,
        "account_return_max_error": error,
        "mean_turnover": turnover,
        "total_cost_sum": cost,
    }


class CSI1000TunerCoreTests(unittest.TestCase):
    def candidate(self, params=None):
        return build_candidate_spec(params or dict(BASELINE_TUNABLE_PARAMS))

    def summary(self, params, fold_rels, phase_rels=None):
        candidate = self.candidate(params)
        folds = [_metric_row(f"fold{i + 1}", rel) for i, rel in enumerate(fold_rels)]
        phases = None
        if phase_rels is not None:
            phases = [
                _metric_row(phase, rel, kind="phase")
                for phase, rel in zip(REFERENCE_PHASES, phase_rels)
            ]
        return summarize_candidate(candidate, folds, phase_results=phases)

    def test_frozen_protocol_is_csi1000_only(self):
        protocol = frozen_protocol()
        self.assertEqual(protocol["market"], "csi1000")
        self.assertEqual(protocol["target"], "raw_20d")
        self.assertEqual(protocol["retrain_frequency"], 20)
        self.assertEqual(protocol["portfolio"], {"topk": 20, "n_drop": 2})
        self.assertEqual(protocol["execution"], "t_close_t1_open")
        self.assertEqual(RETRAIN_FREQUENCY, 20)
        self.assertEqual(SCREEN_EXECUTION_SESSIONS, RETRAIN_FREQUENCY)
        self.assertEqual(protocol["screen_execution_sessions"], 20)

    def test_candidate_identity_is_order_independent(self):
        a = self.candidate()
        reversed_params = dict(reversed(list(BASELINE_TUNABLE_PARAMS.items())))
        b = self.candidate(reversed_params)
        self.assertEqual(a["candidate_id"], b["candidate_id"])
        self.assertTrue(a["is_baseline"])

    def test_candidate_rejects_non_stage_a_axes(self):
        bad = dict(BASELINE_TUNABLE_PARAMS)
        bad["bagging_freq"] = 1
        with self.assertRaises(ValueError):
            self.candidate(bad)

        bad = dict(BASELINE_TUNABLE_PARAMS)
        bad["num_leaves"] = 250
        bad["max_depth"] = 6
        with self.assertRaises(ValueError):
            self.candidate(bad)

    def test_smoke_set_is_frozen_unique_and_keeps_baseline(self):
        candidates = smoke_candidate_specs()
        self.assertEqual(len(candidates), 12)
        self.assertTrue(candidates[0]["is_baseline"])
        self.assertEqual(len({row["candidate_id"] for row in candidates}), 12)

    def test_expanded_set_is_deterministic_and_capped(self):
        a = expanded_candidate_specs(count=20)
        b = expanded_candidate_specs(count=20)
        self.assertEqual([row["candidate_id"] for row in a], [row["candidate_id"] for row in b])
        self.assertTrue(a[0]["is_baseline"])
        with self.assertRaises(ValueError):
            expanded_candidate_specs(count=101)

    def test_fold_geometry_is_purged_non_overlapping_and_leaves_tail(self):
        # Build a longer synthetic calendar with valid ISO dates by using pandas-like
        # preconstructed weekday-neutral labels; only ordering/index arithmetic matters.
        calendar = [f"202{i // 300 + 1}-{(i % 300) // 25 + 1:02d}-{i % 25 + 1:02d}" for i in range(1600)]
        # The generated labels may repeat years/months ordering boundaries, so use simple sortable tokens instead.
        calendar = [f"D{i:04d}" for i in range(1600)]
        folds = build_stage_a_folds(
            calendar,
            signal_anchor="D0500",
            n_folds=4,
            execution_sessions=100,
            validation_sessions=80,
            horizon=20,
            train_start="D0000",
            last_execution_cutoff="D1000",
        )
        self.assertEqual(len(folds), 4)
        for previous, current in zip(folds, folds[1:]):
            self.assertLess(previous["execution"][1], current["execution"][0])
            self.assertLess(previous["signal"][1], current["signal"][0])
        tail = reserved_tail(calendar, folds)
        self.assertGreater(tail["n_execution_sessions"], 0)
        self.assertGreater(tail["execution_start"], folds[-1]["execution"][1])

    def test_screen_ranking_prioritizes_lower_tail(self):
        robust_params = dict(BASELINE_TUNABLE_PARAMS)
        robust_params["num_leaves"] = 127
        robust = self.summary(robust_params, [0.04, 0.04, 0.04, 0.04])

        flashy_params = dict(BASELINE_TUNABLE_PARAMS)
        flashy_params["num_leaves"] = 63
        flashy = self.summary(flashy_params, [-0.10, 0.12, 0.12, 0.12])

        ranked = rank_candidates([flashy, robust], stage="screen")
        self.assertEqual(ranked[0]["candidate_id"], robust["candidate_id"])

    def test_final_ranking_prioritizes_phase_robustness(self):
        robust_params = dict(BASELINE_TUNABLE_PARAMS)
        robust_params["num_leaves"] = 127
        robust = self.summary(
            robust_params,
            [0.04] * 4,
            phase_rels=[0.03, 0.03, 0.03, 0.03, 0.03],
        )

        fold_winner_params = dict(BASELINE_TUNABLE_PARAMS)
        fold_winner_params["num_leaves"] = 63
        fold_winner = self.summary(
            fold_winner_params,
            [0.08] * 4,
            phase_rels=[-0.08, 0.06, 0.06, 0.06, 0.06],
        )

        ranked = rank_candidates([fold_winner, robust], stage="final")
        self.assertEqual(ranked[0]["candidate_id"], robust["candidate_id"])

    def test_ranking_contract_is_worst_fold_first_and_machine_derived(self):
        contract = ranking_contract("screen")
        self.assertEqual(contract["primary_scope"], "temporal_folds")
        self.assertEqual(
            contract["order"][:3],
            [
                {"field": "relative_excess_cagr_worst", "direction": "higher"},
                {"field": "relative_excess_cagr_q25", "direction": "higher"},
                {"field": "relative_excess_cagr_median", "direction": "higher"},
            ],
        )
        self.assertEqual(
            contract["order"][-2:],
            [
                {"field": "mean_turnover_median", "direction": "lower"},
                {"field": "total_cost_sum_median", "direction": "lower"},
            ],
        )

    def test_double_fit_reproducibility_gate_requires_exact_hashes(self):
        repeat_a = [_repro_row(f"fold{i}") for i in range(1, 5)]
        repeat_b = [_repro_row(f"fold{i}") for i in range(1, 5)]
        gate = validate_reproducibility_pairs(repeat_a, repeat_b)
        self.assertTrue(gate["passed"])
        self.assertEqual(gate["fits_per_repeat"], 4)
        self.assertEqual(gate["total_gate_fits"], 8)

        bad = [_repro_row(f"fold{i}") for i in range(1, 5)]
        bad[2]["signal_sha256"] = "z" * 64
        with self.assertRaises(RuntimeError):
            validate_reproducibility_pairs(repeat_a, bad)

    def test_double_fit_reproducibility_gate_rejects_bad_account(self):
        repeat_a = [_repro_row(f"fold{i}") for i in range(1, 5)]
        repeat_b = [_repro_row(f"fold{i}") for i in range(1, 5)]
        repeat_b[0]["account_return_max_error"] = 1e-6
        with self.assertRaises(ValueError):
            validate_reproducibility_pairs(repeat_a, repeat_b)

    def test_account_consistency_fails_closed(self):
        candidate = self.candidate()
        folds = [_metric_row(f"fold{i + 1}", 0.03) for i in range(4)]
        folds[2]["account_return_max_error"] = 1e-6
        with self.assertRaises(ValueError):
            summarize_candidate(candidate, folds)

    def test_phase_set_must_be_exact(self):
        candidate = self.candidate()
        folds = [_metric_row(f"fold{i + 1}", 0.03) for i in range(4)]
        phases = [_metric_row(phase, 0.02, kind="phase") for phase in REFERENCE_PHASES[:-1]]
        with self.assertRaises(ValueError):
            summarize_candidate(candidate, folds, phase_results=phases)

    def test_stage_b_selection_retains_baseline(self):
        summaries = []
        baseline = self.summary(dict(BASELINE_TUNABLE_PARAMS), [-0.02] * 4)
        summaries.append(baseline)
        for leaves, rel in ((31, 0.02), (63, 0.03), (127, 0.04)):
            params = dict(BASELINE_TUNABLE_PARAMS)
            params["num_leaves"] = leaves
            params["max_depth"] = 8
            summaries.append(self.summary(params, [rel] * 4))
        selected = select_stage_b_candidates(summaries, top_n=2)
        self.assertEqual(len(selected), 2)
        self.assertTrue(any(row["is_baseline"] for row in selected))


if __name__ == "__main__":
    unittest.main()
