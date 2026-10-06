import unittest

from multi_pool_tuner_core import (
    build_candidate_spec,
    market_policy,
    rank_candidates,
    summarize_candidate,
    validate_purged_folds,
)


def _row(
    identifier,
    rel,
    *,
    kind="fold",
    ir=0.2,
    sharpe=0.6,
    maxdd=-0.2,
    turnover=0.05,
    cost=0.02,
):
    return {
        f"{kind}_id": identifier,
        "relative_excess_cagr": rel,
        "strategy_max_drawdown": maxdd,
        "sharpe": sharpe,
        "information_ratio": ir,
        "account_return_max_error": 0.0,
        "mean_turnover": turnover,
        "total_cost_sum": cost,
    }


class MultiPoolTunerCoreTests(unittest.TestCase):
    def candidate(self, leaf=31):
        return build_candidate_spec(
            market="csi1000",
            model_params={"num_leaves": leaf, "learning_rate": 0.05},
        )

    def summary(self, candidate, rels, phases=None):
        folds = [_row(f"f{i}", rel) for i, rel in enumerate(rels)]
        phase_rows = None
        if phases is not None:
            phase_rows = [_row(phase, rel, kind="phase") for phase, rel in phases]
        return summarize_candidate(candidate, folds, phase_results=phase_rows)

    def test_market_modes_are_asymmetric(self):
        self.assertEqual(market_policy("csi1000")["mode"], "full_tuner")
        self.assertEqual(market_policy("chinext")["mode"], "bounded_rescue")
        self.assertEqual(market_policy("star")["mode"], "blocked_until_repro_gate")
        self.assertFalse(market_policy("star")["performance_tuning_allowed"])

    def test_candidate_id_is_stable_to_parameter_mapping_order(self):
        a = build_candidate_spec(
            market="csi1000",
            model_params={"num_leaves": 31, "learning_rate": 0.05},
        )
        b = build_candidate_spec(
            market="csi1000",
            model_params={"learning_rate": 0.05, "num_leaves": 31},
        )
        self.assertEqual(a["candidate_id"], b["candidate_id"])

    def test_purged_fold_validation_rejects_label_leakage(self):
        calendar = [f"2026-01-{day:02d}" for day in range(1, 31)]
        good = [
            {
                "fold_id": "f0",
                "train": ["2026-01-01", "2026-01-05"],
                "valid": ["2026-01-09", "2026-01-12"],
                "test": ["2026-01-16", "2026-01-20"],
            }
        ]
        self.assertEqual(validate_purged_folds(good, calendar, horizon=3)[0]["fold_id"], "f0")

        bad = [dict(good[0], valid=["2026-01-08", "2026-01-12"])]
        with self.assertRaises(ValueError):
            validate_purged_folds(bad, calendar, horizon=3)

    def test_screen_ranking_prefers_lower_tail_over_headline_median(self):
        robust = self.summary(self.candidate(20), [0.04, 0.04, 0.04, 0.04])
        flashy = self.summary(self.candidate(40), [-0.10, 0.12, 0.12, 0.12])
        ranked = rank_candidates([flashy, robust], stage="screen")
        self.assertEqual(ranked[0]["candidate_id"], robust["candidate_id"])

    def test_final_ranking_puts_phase_robustness_before_fold_winner(self):
        robust = self.summary(
            self.candidate(20),
            [0.04] * 4,
            phases=[(0, 0.03), (5, 0.03), (10, 0.03), (15, 0.03)],
        )
        fold_winner = self.summary(
            self.candidate(40),
            [0.08] * 4,
            phases=[(0, -0.08), (5, 0.06), (10, 0.06), (15, 0.06)],
        )
        ranked = rank_candidates([fold_winner, robust], stage="final")
        self.assertEqual(ranked[0]["candidate_id"], robust["candidate_id"])

    def test_final_ranking_requires_phase_results(self):
        summary = self.summary(self.candidate(), [0.04] * 4)
        with self.assertRaises(ValueError):
            rank_candidates([summary], stage="final")

    def test_account_inconsistency_fails_closed(self):
        rows = [_row(f"f{i}", 0.03) for i in range(4)]
        rows[2]["account_return_max_error"] = 1e-6
        with self.assertRaises(ValueError):
            summarize_candidate(self.candidate(), rows)

    def test_required_phase_set_is_enforced(self):
        rows = [_row(f"f{i}", 0.03) for i in range(4)]
        phases = [_row(phase, 0.02, kind="phase") for phase in (0, 5, 10)]
        with self.assertRaises(ValueError):
            summarize_candidate(
                self.candidate(),
                rows,
                phase_results=phases,
                required_phases=[0, 5, 10, 15],
            )

    def test_equal_vectors_use_candidate_id_as_deterministic_tiebreak(self):
        a = self.summary(self.candidate(20), [0.04] * 4)
        b = self.summary(self.candidate(40), [0.04] * 4)
        ranked = rank_candidates([b, a], stage="screen")
        expected = sorted([a["candidate_id"], b["candidate_id"]])
        self.assertEqual([row["candidate_id"] for row in ranked], expected)


if __name__ == "__main__":
    unittest.main()
