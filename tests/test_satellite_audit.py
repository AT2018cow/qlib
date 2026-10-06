from pathlib import Path
import unittest

from satellite_audit_core import (
    SCREEN_PHASES,
    compact_audit_summary,
    frozen_audit_config,
    validate_gate_payload,
    validate_screen_payload,
)

ROOT = Path(__file__).resolve().parents[1]


def _perf(strategy=0.08, bench=0.02, rel=0.058):
    return {
        "strategy_cagr": strategy,
        "benchmark_cagr": bench,
        "relative_excess_cagr": rel,
        "strategy_max_drawdown": -0.20,
        "benchmark_max_drawdown": -0.35,
        "relative_max_drawdown": -0.25,
        "sharpe": 0.65,
        "information_ratio": 0.25,
        "annual_volatility": 0.15,
        "account_return_max_error": 0.0,
    }


def _gate(market="chinext", topk=20, nd=3, freq=20):
    signal = "a" * 64
    report = "b" * 64
    result = {
        **_perf(),
        "signal_sha256": signal,
        "n_retrains": 70,
        "report_artifact": {
            "path": f"/vol/freq_experiment/reports/report_{market}.parquet",
            "content_sha256": report,
        },
    }
    return {
        "protocol": "continuous_account_board_aware_v5_repro",
        "market": market,
        "topk": topk,
        "n_drop": nd,
        "window": "2021-01-04~2026-09-30",
        "reproducibility": {
            "market": market,
            "provider_fingerprint": "c" * 64,
            "snapshot_token": "d" * 64,
            "freq_experiment_source_sha256": "e" * 64,
        },
        "reproducibility_gate": {
            "passed": True,
            "gate_version": "phase0_double_fit_v1",
            "prediction_chunks_match": True,
            "signal_hash_match": True,
            "report_hash_match": True,
            "repeat_a_signal_sha256": signal,
            "repeat_b_signal_sha256": signal,
            "repeat_a_report_content_sha256": report,
            "repeat_b_report_content_sha256": report,
            "n_retrains_per_repeat": 70,
        },
        "results": {str(freq): result},
    }


def _screen(gate, freq=20):
    base = gate["results"][str(freq)]
    results = {}
    for phase in SCREEN_PHASES:
        result = {
            **_perf(strategy=0.08 + phase / 1000),
            "phase": phase,
            "source": (
                "repro_gate_baseline_reuse" if phase == 0 else "deterministic_refit"
            ),
            "signal_sha256": (
                base["signal_sha256"] if phase == 0 else f"{phase:064d}"[-64:]
            ),
            "n_retrains": 70,
            "report_artifact": {
                "path": f"/vol/freq_phase_sensitivity/report_phase{phase}.parquet",
                "content_sha256": (
                    base["report_artifact"]["content_sha256"]
                    if phase == 0
                    else f"{phase + 100:064d}"[-64:]
                ),
            },
            "performance": _perf(strategy=0.08 + phase / 1000),
            "mean_turnover": 0.02,
            "total_cost_sum": 0.025,
        }
        if phase != 0:
            result["chunk_predictions"] = [
                {
                    "retrain_asof": "2021-01-04",
                    "prediction_sha256": f"{phase + 200:064d}"[-64:],
                }
            ]
        results[str(phase)] = result

    return {
        "protocol": "retrain_phase_sensitivity_v2_repro",
        "market": gate["market"],
        "freq": freq,
        "topk": gate["topk"],
        "n_drop": gate["n_drop"],
        "eval_from": "2021-01-04",
        "phases": list(SCREEN_PHASES),
        "complete_phase_grid": False,
        "phase0_reused_from_repro_gate": True,
        "reproducibility": gate["reproducibility"],
        "reproducibility_gate": gate["reproducibility_gate"],
        "results": results,
        "new_model_fits": 210,
        "summary": {
            "relative_excess_cagr": {
                "n": 4,
                "min": 0.05,
                "q25": 0.055,
                "median": 0.06,
                "q75": 0.065,
                "max": 0.07,
            }
        },
    }


class SatelliteAuditTests(unittest.TestCase):
    def test_frozen_configs_match_pre_tuner_matrix(self):
        self.assertEqual(frozen_audit_config("chinext"), {"topk": 20, "nd": 3})
        self.assertEqual(frozen_audit_config("star"), {"topk": 50, "nd": 2})
        self.assertEqual(SCREEN_PHASES, [0, 5, 10, 15])
        with self.assertRaises(ValueError):
            frozen_audit_config("csi1000")

        freq_src = (ROOT / "freq_experiment.py").read_text()
        self.assertIn('"chinext": {"topk": 20, "nd": 3}', freq_src)
        self.assertIn('"star": {"topk": 50, "nd": 2}', freq_src)

    def test_gate_validation_accepts_complete_double_fit(self):
        gate = _gate()
        result = validate_gate_payload(
            gate,
            market="chinext",
            freq=20,
            eval_from="2021-01-04",
            topk=20,
            nd=3,
        )
        self.assertEqual(result["signal_sha256"], "a" * 64)

    def test_gate_validation_fails_closed_on_hash_or_status_mismatch(self):
        gate = _gate()
        gate["reproducibility_gate"]["passed"] = False
        with self.assertRaises(RuntimeError):
            validate_gate_payload(
                gate,
                market="chinext",
                freq=20,
                eval_from="2021-01-04",
                topk=20,
                nd=3,
            )

        gate = _gate()
        gate["reproducibility_gate"]["repeat_b_signal_sha256"] = "x" * 64
        with self.assertRaises(RuntimeError):
            validate_gate_payload(
                gate,
                market="chinext",
                freq=20,
                eval_from="2021-01-04",
                topk=20,
                nd=3,
            )

    def test_screen_validation_requires_exact_gate_reuse_and_four_phases(self):
        gate = _gate()
        screen = _screen(gate)
        results = validate_screen_payload(
            screen,
            gate,
            market="chinext",
            freq=20,
            eval_from="2021-01-04",
            topk=20,
            nd=3,
        )
        self.assertEqual(set(results), {"0", "5", "10", "15"})

        bad = _screen(gate)
        bad["reproducibility"] = dict(bad["reproducibility"])
        bad["reproducibility"]["snapshot_token"] = "z" * 64
        with self.assertRaises(RuntimeError):
            validate_screen_payload(
                bad,
                gate,
                market="chinext",
                freq=20,
                eval_from="2021-01-04",
                topk=20,
                nd=3,
            )

        bad = _screen(gate)
        bad["results"]["0"]["signal_sha256"] = "z" * 64
        with self.assertRaises(RuntimeError):
            validate_screen_payload(
                bad,
                gate,
                market="chinext",
                freq=20,
                eval_from="2021-01-04",
                topk=20,
                nd=3,
            )

    def test_screen_requires_enriched_turnover_and_nonzero_lineage(self):
        gate = _gate()
        bad = _screen(gate)
        del bad["results"]["5"]["mean_turnover"]
        with self.assertRaises(RuntimeError):
            validate_screen_payload(
                bad,
                gate,
                market="chinext",
                freq=20,
                eval_from="2021-01-04",
                topk=20,
                nd=3,
            )

        bad = _screen(gate)
        bad["results"]["10"]["chunk_predictions"] = []
        with self.assertRaises(RuntimeError):
            validate_screen_payload(
                bad,
                gate,
                market="chinext",
                freq=20,
                eval_from="2021-01-04",
                topk=20,
                nd=3,
            )

    def test_compact_summary_keeps_canonical_metrics(self):
        gate = _gate()
        screen = _screen(gate)
        summary = compact_audit_summary(gate, screen, freq=20)
        self.assertTrue(summary["gate_passed"])
        self.assertEqual(summary["screen_phases"], [0, 5, 10, 15])
        self.assertEqual(summary["new_model_fits"], 210)
        self.assertIn("relative_excess_cagr", summary["phases"]["15"])
        self.assertIn("mean_turnover", summary["phases"]["15"])

    def test_wrapper_uses_cli_boundaries_and_validates_gate_before_screen(self):
        src = (ROOT / "satellite_pre_tuner_audit.py").read_text()
        gate_pos = src.index("freq_experiment.py::reproducibility_gate_driver")
        verify_pos = src.index("read_and_validate_gate.remote(")
        screen_pos = src.index(
            "phase_audit_extend.py::extend_phase_sensitivity_driver"
        )
        self.assertLess(gate_pos, verify_pos)
        self.assertLess(verify_pos, screen_pos)
        self.assertIn("@app.local_entrypoint()", src)
        self.assertIn("subprocess.run(", src)
        self.assertNotIn("reproducibility_gate_driver.remote(", src)
        self.assertNotIn("retrain_phase_sensitivity_driver.remote(", src)


if __name__ == "__main__":
    unittest.main()
