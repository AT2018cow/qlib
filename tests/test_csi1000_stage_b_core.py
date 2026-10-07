import ast
from datetime import date, timedelta
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

from csi1000_stage_b_core import (
    BASELINE_CANDIDATE_ID,
    MODEL_NUM_THREADS,
    REFERENCE_PHASES,
    RESERVED_EXECUTION_END,
    RESERVED_EXECUTION_START,
    RESERVED_SIGNAL_ANCHOR,
    STAGE_A_EXPANDED_AUDIT,
    STAGE_A_EXPANDED_RESULT,
    STAGE_A_SNAPSHOT_TOKEN,
    STAGE_B_PROTOCOL_VERSION,
    STARTER_CONTAINER_LIMIT,
    WORKER_CPU,
    WORKER_MAX_CONTAINERS,
    WORKER_MEMORY_MIB,
    build_phase_jobs,
    frozen_stage_b_protocol,
    phase_ranking_contract,
    rank_stage_b_candidates,
    stage_b_candidates,
    stage_b_fit_budget,
    summarize_phase_results,
    validate_reserved_tail,
    validate_stage_a_evidence,
)


def _synthetic_calendar():
    start = date(2022, 1, 3)
    end = date.fromisoformat(RESERVED_EXECUTION_END)
    out = []
    current = start
    while current <= end:
        # Weekday-only is sufficient for index arithmetic. Remove 2025-01-01
        # so the frozen 2024-12-31 signal anchor is immediately followed by
        # the frozen 2025-01-02 execution start.
        if current.weekday() < 5 and current.isoformat() != "2025-01-01":
            out.append(current.isoformat())
        current += timedelta(days=1)
    return out


def _phase_row(phase, rel):
    return {
        "phase": phase,
        "metric_version": "portfolio_compound_v1",
        "relative_excess_cagr": rel,
        "strategy_max_drawdown": -0.20,
        "sharpe": 0.60,
        "information_ratio": 0.20,
        "account_return_max_error": 0.0,
        "mean_turnover": 0.05,
        "total_cost_sum": 0.02,
    }


class CSI1000StageBCoreTests(unittest.TestCase):
    def test_protocol_and_resources_are_frozen(self):
        protocol = frozen_stage_b_protocol()
        self.assertEqual(protocol["protocol"], STAGE_B_PROTOCOL_VERSION)
        self.assertEqual(protocol["source_stage_a_snapshot_token"], STAGE_A_SNAPSHOT_TOKEN)
        self.assertEqual(protocol["reference_phases"], [0, 4, 6, 10, 15])
        self.assertEqual(protocol["reserved_signal_anchor"], RESERVED_SIGNAL_ANCHOR)
        self.assertEqual(protocol["reserved_execution_start"], RESERVED_EXECUTION_START)
        self.assertEqual(protocol["reserved_execution_end"], RESERVED_EXECUTION_END)
        self.assertEqual(protocol["strategy"], "deterministic_topk_dropout_v1")
        self.assertEqual(WORKER_CPU, 8)
        self.assertEqual(WORKER_MEMORY_MIB, 16384)
        self.assertEqual(WORKER_MAX_CONTAINERS, 64)
        self.assertEqual(STARTER_CONTAINER_LIMIT, 100)
        self.assertEqual(MODEL_NUM_THREADS, 20)

    def test_candidate_manifest_is_top10_plus_baseline_control(self):
        rows = stage_b_candidates()
        self.assertEqual(len(rows), 11)
        tuned = [row for row in rows if not row["is_baseline"]]
        baseline = [row for row in rows if row["is_baseline"]]
        self.assertEqual([row["stage_a_rank"] for row in tuned], list(range(1, 11)))
        self.assertEqual(len(baseline), 1)
        self.assertEqual(baseline[0]["candidate_id"], BASELINE_CANDIDATE_ID)
        self.assertEqual(baseline[0]["stage_a_rank"], 48)
        self.assertEqual(len({row["candidate_id"] for row in rows}), 11)

    def test_committed_stage_a_evidence_supports_freeze(self):
        expanded = json.loads((ROOT / STAGE_A_EXPANDED_RESULT).read_text())
        audit = json.loads((ROOT / STAGE_A_EXPANDED_AUDIT).read_text())
        result = validate_stage_a_evidence(expanded, audit)
        self.assertTrue(result["passed"])
        self.assertEqual(result["baseline_rank"], 48)
        self.assertEqual(len(result["frozen_tuned_ids"]), 10)
        self.assertTrue(all(value >= 3 for value in result["lofo_top10_counts"].values()))

    def test_reserved_tail_and_phase_geometry(self):
        calendar = _synthetic_calendar()
        tail = validate_reserved_tail(calendar)
        self.assertEqual(tail["signal_anchor"], "2024-12-31")
        self.assertEqual(tail["execution_start"], "2025-01-02")
        self.assertEqual(tail["execution_end"], "2026-09-30")
        self.assertGreater(tail["n_execution_sessions"], 400)

        for phase in REFERENCE_PHASES:
            jobs = build_phase_jobs(calendar, phase)
            self.assertGreater(len(jobs), 0)
            self.assertLessEqual(jobs[0]["retrain_asof"], RESERVED_SIGNAL_ANCHOR)
            self.assertGreaterEqual(jobs[0]["signal_end"], RESERVED_SIGNAL_ANCHOR)
            end_i = calendar.index(RESERVED_EXECUTION_END)
            self.assertEqual(jobs[-1]["signal_end"], calendar[end_i - 1])

        with self.assertRaises(ValueError):
            build_phase_jobs(calendar, 1)

    def test_fit_budget_reuses_passing_preflight_phase0(self):
        budget = stage_b_fit_budget(_synthetic_calendar())
        phase0 = budget["retrain_jobs_per_phase"][0]
        self.assertEqual(budget["candidate_count"], 11)
        self.assertEqual(budget["phase_count"], 5)
        self.assertEqual(budget["preflight_model_fits"], 2 * phase0)
        self.assertEqual(
            budget["full_grid_new_fits_after_preflight_reuse"],
            budget["full_grid_model_fits"] - phase0,
        )

    def test_phase_summary_and_final_ranking_are_lower_tail_first(self):
        robust = stage_b_candidates()[0]
        flashy = stage_b_candidates()[1]
        rows = []
        for candidate, rels in (
            (robust, [0.04, 0.04, 0.04, 0.04, 0.04]),
            (flashy, [-0.10, 0.20, 0.20, 0.20, 0.20]),
        ):
            rows.append(
                {
                    "candidate_id": candidate["candidate_id"],
                    "stage_a_rank": candidate["stage_a_rank"],
                    "phase_results": [
                        _phase_row(phase, rel)
                        for phase, rel in zip(REFERENCE_PHASES, rels)
                    ],
                }
            )

        # rank_stage_b_candidates requires the exact frozen set; independently
        # verify the summary lower-tail semantics here.
        a = summarize_phase_results(rows[0]["phase_results"])
        b = summarize_phase_results(rows[1]["phase_results"])
        self.assertGreater(a["relative_excess_cagr_worst"], b["relative_excess_cagr_worst"])

        contract = phase_ranking_contract()
        self.assertEqual(contract["primary_scope"], "reserved_tail_calendar_phases")
        self.assertEqual(contract["order"][0]["field"], "relative_excess_cagr_worst")
        self.assertTrue(contract["reserved_tail_is_final_confirmation_not_retuning_data"])

    def test_final_rank_requires_exact_frozen_candidate_set(self):
        with self.assertRaises(ValueError):
            rank_stage_b_candidates([])

    def test_runtime_worker_resource_bounds(self):
        from csi1000_stage_b_core import validate_worker_resources

        defaults = validate_worker_resources(
            cpu=WORKER_CPU,
            memory_mib=WORKER_MEMORY_MIB,
            max_containers=WORKER_MAX_CONTAINERS,
        )
        self.assertEqual(defaults["retrain_worker_cpu_physical_cores"], 8.0)
        self.assertEqual(defaults["retrain_worker_memory_mib"], 16384)
        self.assertEqual(defaults["retrain_worker_max_containers"], 64)
        self.assertEqual(defaults["lightgbm_num_threads"], 20)

        with self.assertRaises(ValueError):
            validate_worker_resources(cpu=3, memory_mib=16384, max_containers=64)
        with self.assertRaises(ValueError):
            validate_worker_resources(cpu=8, memory_mib=8192, max_containers=64)
        with self.assertRaises(ValueError):
            validate_worker_resources(cpu=8, memory_mib=16384, max_containers=101)

    def test_stage_b_runner_is_deterministic_and_resume_aware(self):
        source = (ROOT / "csi1000_stage_b.py").read_text()
        ast.parse(source)
        self.assertIn("DeterministicTopkDropoutStrategy", source)
        self.assertIn("collect_data(", source)
        self.assertIn("max_containers=WORKER_MAX_CONTAINERS", source)
        self.assertIn("with_options(", source)
        self.assertIn("cpu=worker_cpu_value", source)
        self.assertIn("memory=worker_memory_value", source)
        self.assertIn("max_containers=worker_cap", source)
        self.assertIn("worker_cpu: float = WORKER_CPU", source)
        self.assertIn("worker_memory_mib: int = WORKER_MEMORY_MIB", source)
        self.assertIn("worker_max_containers: int = WORKER_MAX_CONTAINERS", source)
        self.assertIn("cpu=WORKER_CPU", source)
        self.assertIn("memory=WORKER_MEMORY_MIB", source)
        self.assertIn("retries=WORKER_RETRIES", source)
        self.assertIn("_load_reusable_chunk(args)", source)
        self.assertIn("_load_reusable_phase(args)", source)
        self.assertIn("stage_b_baseline_phase0_double_run_v1", source)
        self.assertIn("stage_b_preflight_reuse", source)
        self.assertNotIn("requests.get(", source)
        self.assertNotIn("prepare.remote(force=True", source)

    def test_stage_a_audit_plans_are_immutable_and_labeled(self):
        source = (ROOT / "csi1000_tuner_audit.py").read_text()
        ast.parse(source)
        self.assertIn("plan_label: str = \"\"", source)
        self.assertIn("plan_label is required in plan mode", source)
        self.assertIn("immutable reuse plan already exists", source)
        self.assertIn("if out_path.exists()", source)


if __name__ == "__main__":
    unittest.main()
