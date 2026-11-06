"""A single historical winner cannot bypass phase and extension evidence."""

import importlib.util
import hashlib
import json
from pathlib import Path
import pytest
import pandas as pd

SPEC = importlib.util.spec_from_file_location(
    "analyze_prelive_results",
    Path(__file__).resolve().parents[1] / "code/research/regime_reselection/analyze_prelive_results.py",
)
ranking = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ranking)


def test_strong_primary_challenger_with_missing_phase_evidence_does_not_pass():
    candidates = []
    results = []
    for market in ("csi300", "csi500", "csi1000"):
        for incumbent, sharpe in ((True, -0.2), (False, 0.0)):
            name = market + ("_current" if incumbent else "_other")
            candidates.append({"id": name, "market": market, "incumbent": incumbent})
            metric = {
                "sharpe": sharpe,
                "strategy_cagr": 0.1,
                "strategy_max_drawdown": -0.2,
                "quote_gap_count": 0,
                "max_single_weight": 0.15,
                "annual": {"2020": {"strategy_total_return": 0.1 if incumbent else 0.2}},
            }
            results.append({"key": name, "metrics": [{**metric, "slippage_bps": b} for b in (0, 5, 10)]})
    register = {
        "candidates": candidates,
        "phase_replications": {"phases": [0, 4, 6, 15]},
        "comparison_rule": {
            "challenger_min_sharpe_improvement": 0.1,
            "challenger_max_mdd_deterioration": 0.02,
            "challenger_min_net_cagr_improvement": 0,
            "annual_relative_win_fraction_min": 0.5,
            "available_phase_net_outperformance_fraction_min": 0.75,
        },
    }
    result = ranking.analyze({"results": results}, {"results": results}, {"results": []}, register)
    assert result["primary_tables"]["csi300"][0]["sharpe"] == 0.0
    assert not result["live_trading_approved"]
    for decision in result["decisions"]:
        assert decision["primary_rank_first"].endswith("_other")
        assert decision["supported_challengers"] == []
        checks = decision["comparisons"][0]["checks"]
        assert checks["sharpe_improvement"]
        assert not checks["phase_support"]


def test_recovery_uses_completed_jobs_and_requires_whole_expected_count(tmp_path):
    for name, key, status in (("one", "A", "FAILED"), ("two", "B", "COMPLETED")):
        payload = {
            "status": status,
            "jobs": [{"key": key, "register_sha256": "reg", "engine_sha256": "engine"}],
            "results": [{"key": key, "status": "COMPLETED", "metrics": [{"sharpe": 0.5}]}],
        }
        (tmp_path / f"{name}_extension_results.json").write_text(json.dumps(payload))
    paths, merged = ranking.load_batch(tmp_path, "extension", "reg", "engine", expected_count=2)
    assert len(paths) == 2
    assert {r["key"] for r in merged["results"]} == {"A", "B"}
    with pytest.raises(ValueError, match="incomplete"):
        ranking.load_batch(tmp_path, "extension", "reg", "engine", expected_count=3)


def test_conflicting_recovery_outputs_fail(tmp_path):
    for name, value in (("one", 0.5), ("two", 0.6)):
        payload = {
            "status": "COMPLETED",
            "jobs": [{"key": "A", "register_sha256": "reg", "engine_sha256": "engine"}],
            "results": [{"key": "A", "status": "COMPLETED", "metrics": [{"sharpe": value}]}],
        }
        (tmp_path / f"{name}_extension_results.json").write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="conflicting"):
        ranking.load_batch(tmp_path, "extension", "reg", "engine", expected_count=1)


def test_full_extension_strength_cannot_hide_a_weaker_new_tail():
    candidates, results, extension, phases = [], [], [], []
    for market in ("csi300", "csi500", "csi1000"):
        for incumbent, sharpe in ((True, 0.5), (False, 1.0)):
            key = market + ("_current" if incumbent else "_other")
            candidates.append({"id": key, "market": market, "incumbent": incumbent})
            metric = {
                "sharpe": sharpe,
                "strategy_cagr": 0.1,
                "strategy_max_drawdown": -0.2,
                "strategy_total_return": 0.1 if incumbent else 0.2,
                "quote_gap_count": 0,
                "max_single_weight": 0.15,
                "annual": {"2020": {"strategy_total_return": 0.1 if incumbent else 0.2}},
            }
            row = {"key": key, "metrics": [{**metric, "slippage_bps": b} for b in (0, 5, 10)]}
            results.append(row)
            extension.append(
                {
                    **row,
                    "tail_metrics": {"start": "2025-01-02", "end": "2026-09-30", "sharpe": 1.4 if incumbent else 1.1},
                }
            )
            for phase in (0, 4, 6, 15):
                phases.append({**row, "key": f"{key}_phase{phase:02}"})
    register = {
        "candidates": candidates,
        "phase_replications": {"phases": [0, 4, 6, 15]},
        "comparison_rule": {
            "challenger_min_sharpe_improvement": 0.1,
            "challenger_max_mdd_deterioration": 0.02,
            "challenger_min_net_cagr_improvement": 0,
            "annual_relative_win_fraction_min": 0.5,
            "available_phase_net_outperformance_fraction_min": 0.75,
        },
    }
    actual = ranking.analyze({"results": results}, {"results": extension}, {"results": phases}, register)
    for decision in actual["decisions"]:
        checks = decision["comparisons"][0]["checks"]
        assert all(value for name, value in checks.items() if name != "extension_support")
        assert checks["extension_support"] is False
        assert decision["supported_challengers"] == []
    for row in extension:
        if row["key"].endswith("_other"):
            row["tail_metrics"]["sharpe"] = 1.5
    actual = ranking.analyze({"results": results}, {"results": extension}, {"results": phases}, register)
    assert all(len(row["supported_challengers"]) == 1 for row in actual["decisions"])


def test_extension_tail_uses_previous_nav_and_verified_report_bytes(tmp_path):
    path = tmp_path / "batch" / "candidate" / "extra_0bp" / "report.parquet"
    path.parent.mkdir(parents=True)
    frame = pd.DataFrame(
        {
            "account": [2_000_000, 2_040_000, 2_020_000],
            "return": [0.0, 0.02, 2_020_000 / 2_040_000 - 1],
            "cost": 0.0,
            "bench": 0.0,
        },
        index=pd.to_datetime(["2024-12-31", "2025-01-02", "2025-01-03"]),
    )
    frame.to_parquet(path)
    extension = {
        "jobs": [{"key": "candidate", "run_id": "batch"}],
        "results": [
            {"key": "candidate", "files": {"extra_0bp/report.parquet": hashlib.sha256(path.read_bytes()).hexdigest()}}
        ],
    }
    ranking.attach_extension_tails(tmp_path, extension, "2024-12-31")
    tail = extension["results"][0]["tail_metrics"]
    assert tail["initial_nav"] == 2_000_000
    assert tail["strategy_total_return"] == pytest.approx(0.01)
    assert tail["start"] == "2025-01-02"
    path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="fingerprint"):
        ranking.attach_extension_tails(tmp_path, extension, "2024-12-31")


def test_current_analysis_rejects_entry_script_only_receipts(tmp_path):
    implementation = {"schema_version": 2, "sha256": "source", "files": {"helper.py": "digest"}}
    job = {"key": "A", "engine_sha256": "source", "register_sha256": "reg", "provider_contract_sha256": "provider"}
    result = {
        "status": "COMPLETED",
        **job,
        "source_identity_version": 2,
        "source_manifest": implementation,
        "metrics": [],
    }
    payload = {"status": "COMPLETED", "source_manifest": implementation, "jobs": [job], "results": [result]}
    path = tmp_path / "batch_primary_results.json"
    path.write_text(json.dumps(payload))
    assert len(ranking.load_batch(tmp_path, "primary", "reg", "source", 1, implementation)[1]["results"]) == 1
    payload.pop("source_manifest")
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="incomplete"):
        ranking.load_batch(tmp_path, "primary", "reg", "source", 1, implementation)
