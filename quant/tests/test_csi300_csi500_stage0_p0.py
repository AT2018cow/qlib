"""Offline preflight gate for Stage 0 (P0) CSI300/CSI500 research.

Covers: frozen constant exactness (incl. SHA-authoritative c98856b4 params),
176-fit / 16-cell roster geometry on a synthetic calendar, receipt
write/verify/tamper rejection, cost-stress arithmetic, scoreboard rendering
and static Modal trap compliance. No provider, no Modal, no fitting.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

import stage0_p0_core as core
from audit.csi1000_layered_search_core import stable_sha
from csi1000_stage_b_core import build_phase_jobs

MODAL_SRC = Path(core.__file__).resolve().parent / "stage0_p0_modal.py"


def _synthetic_calendar() -> list[str]:
    """424 execution sessions (2025-01-02..2026-09-30 shape) + history.

    All labels are real YYYY-MM-DD dates (sorted, unique); middle execution
    days are a subsample of the true span so geometry matches production.
    """
    from datetime import date, timedelta

    anchor = date(2024, 12, 31)
    start = date(2025, 1, 2)
    end = date(2026, 9, 30)
    span = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    assert len(span) >= 424
    middle = span[1:-1][:422]
    execution = [start.isoformat()] + [d.isoformat() for d in middle] + [end.isoformat()]
    assert len(execution) == 424
    hist = [anchor - timedelta(days=i) for i in range(1500, 0, -1)]
    calendar = [d.isoformat() for d in hist] + [anchor.isoformat()] + execution
    assert calendar == sorted(set(calendar))
    return calendar


def _model_sha(model_params: dict) -> str:
    from ruamel.yaml import YAML

    from qlib_live_retrain import apply_lgb_reproducibility

    cfg = YAML(typ="safe", pure=True).load(
        (Path(core.REPO_ROOT) / "examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158_csi500.yaml")
        .read_text(encoding="utf-8")
    )
    cfg["task"]["model"]["kwargs"].update(model_params)
    apply_lgb_reproducibility(cfg)
    return stable_sha(cfg["task"]["model"])


def _meta() -> dict:
    return {
        "run_id": "stage0_p0_20261015_a1",
        "code_sha": "a" * 40,
        "provider_fingerprint": "f" * 64,
        "source_snapshot_sha256": "e" * 64,
        "quote_sha256": "d" * 64,
    }


# ---------------------------------------------------------------------------
# Frozen constants.
# ---------------------------------------------------------------------------


def test_stage0_markets_exact():
    assert set(core.MARKETS) == {"csi300", "csi500"}
    assert core.MARKETS["csi300"]["benchmark"] == "SH000300"
    assert core.MARKETS["csi300"]["expected_quote_count"] == 338
    assert core.MARKETS["csi300"]["expected_quote_sha256"] == "9f3756ea8d2e0ff0bb12c3dce6d81888fe7a5df978a5f5669a4ce07b1a043f5b"
    assert core.MARKETS["csi500"]["benchmark"] == "SH000905"
    assert core.MARKETS["csi500"]["expected_quote_count"] == 643
    assert core.MARKETS["csi500"]["expected_quote_sha256"] == "cfcbc21efaba156173ff3c0aa09afc4f1fda5e94d9046f32bf37aa8b32f5e4fb"
    assert core.COMBOS == (("top20_drop2", 20, 2), ("top10_drop1", 10, 1))
    assert core.PHASES == (0, 10)
    assert core.EXPECTED_PROVIDER_FINGERPRINT == "cd2e68f571c6bd19fbb2b79089e891bb1c43b96ab1fcdb4d2b2a9b1fdd9ce5d5"
    assert core.EXPECTED_SNAPSHOT_TOKEN == "51756897fc75230493194aef2e48815e4e9f3cc7426cc135f47bb8ba8e0d21e1"
    assert core.SOURCE_VOLUME == "qlib-cn-data"
    assert core.RESEARCH_VOLUME == "qlib-csi300-csi500-research-v2"


def test_stage0_model_identities_reproduce_config_sha():
    assert core.MODELS["baseline"]["candidate_id"] == "23b92de05cf36c82998de684d0fbf64d81ee54490d755bd3cf96311c00286785"
    assert core.MODELS["c98856b4"]["candidate_id"] == "c98856b460aba687640d422e85a82a545bf5b6ca932e906a180699e0f6cad19b"
    assert _model_sha(core.MODELS["baseline"]["model_params"]) == core.MODELS["baseline"]["model_sha256"]
    assert core.MODELS["baseline"]["model_sha256"] == "793021af66034ec508848ee032f6d309103291981abb63d11ae779da9c5ee7d3"
    # SHA-authoritative frozen production params (plan params text would NOT hash here).
    assert _model_sha(core.MODELS["c98856b4"]["model_params"]) == core.MODELS["c98856b4"]["model_sha256"]
    assert core.MODELS["c98856b4"]["model_sha256"] == "d49e893ad37c821a6a092fb17baf7ee29c9ff71289bbeb1205132e34e3854c50"


def test_stage0_prefix_and_run_id_fail_closed():
    assert core.prefix_for("stage0_p0_20261015_a1", "a" * 40).endswith("stage0_p0_20261015_a1/aaaaaaaaaaaa")
    for bad in ("l2_20261014_a1", "stage0_p0_x", "../stage0_p0_abc", "stage0_p0_"):
        with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
            core.prefix_for(bad, "a" * 40)
    with pytest.raises(ValueError, match="immutable code SHA"):
        core.prefix_for("stage0_p0_20261015_a1", "abc")


# ---------------------------------------------------------------------------
# Roster geometry.
# ---------------------------------------------------------------------------


def test_stage0_fit_roster_is_176_unique_and_market_mapped():
    calendar = _synthetic_calendar()
    jobs = core.build_stage0_fit_jobs(calendar, "stage0_p0_20261015_a1", "a" * 40)
    assert len(jobs) == 176
    assert len({core.job_id(j) for j in jobs}) == 176
    assert len({stable_sha(j) for j in jobs}) == 176
    for market in ("csi300", "csi500"):
        for model_key in ("baseline", "c98856b4"):
            for phase in (0, 10):
                group = [j for j in jobs if j["market"] == market and j["model_key"] == model_key and j["phase"] == phase]
                assert len(group) == 22
                legal = {(p["retrain_asof"], p["signal_end"]) for p in build_phase_jobs(calendar, phase)}
                assert {(j["retrain_asof"], j["signal_end"]) for j in group} == legal
    for job in jobs:
        assert job["kind"] == "retrain"
        assert job["benchmark"] == core.MARKETS[job["market"]]["benchmark"]
        assert job["candidate_id"] == core.MODELS[job["model_key"]]["candidate_id"]
        assert job["model_sha256"] == core.MODELS[job["model_key"]]["model_sha256"]
    first = [j for j in jobs if j["market"] == "csi300" and j["model_key"] == "baseline" and j["phase"] == 0][0]
    assert first["retrain_asof"] <= "2024-12-31" <= first["signal_end"]


def test_stage0_cell_roster_is_16_unique_and_combo_mapped():
    jobs = core.build_stage0_cell_jobs("stage0_p0_20261015_a1", "a" * 40)
    assert len(jobs) == 16
    assert len({core.job_id(j) for j in jobs}) == 16
    assert len({stable_sha(j) for j in jobs}) == 16
    for market in ("csi300", "csi500"):
        for model_key in ("baseline", "c98856b4"):
            for combo_key, topk, dropn in core.COMBOS:
                for phase in (0, 10):
                    group = [
                        j
                        for j in jobs
                        if j["market"] == market
                        and j["model_key"] == model_key
                        and j["combo_key"] == combo_key
                        and j["phase"] == phase
                    ]
                    assert len(group) == 1
                    assert group[0]["topk"] == topk and group[0]["n_drop"] == dropn
                    assert group[0]["benchmark"] == core.MARKETS[market]["benchmark"]


def test_stage0_bind_identity():
    job = core.build_stage0_cell_jobs("stage0_p0_20261015_a1", "a" * 40)[0]
    bound = core.bind_job_identity(job, provider_fingerprint="f" * 64, source_snapshot_sha256="e" * 64,
                                   quote_sha256="d" * 64)
    assert bound["provider_fingerprint"] == "f" * 64
    assert job["provider_fingerprint"] == ""
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.bind_job_identity(job, provider_fingerprint="zz", source_snapshot_sha256="e" * 64,
                               quote_sha256="d" * 64)


def test_stage0_quote_sha_helper():
    assert core.quote_sha_of(["SH600000", "SH600001"]) == __import__("hashlib").sha256(b"SH600000\nSH600001\n").hexdigest()
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.quote_sha_of([])
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.quote_sha_of(["SH600001", "SH600000"])


# ---------------------------------------------------------------------------
# Receipt roundtrip + tamper rejection.
# ---------------------------------------------------------------------------


class _DummyParquet:
    def __init__(self, data: bytes):
        self.data = data

    def to_parquet(self, path):
        Path(path).write_bytes(self.data)


def _retrain_job():
    return {**_meta(), "kind": "retrain", "market": "csi300", "benchmark": "SH000300", "model_key": "baseline",
            "candidate_id": "c" * 64, "model_params": {"learning_rate": 0.1}, "model_sha256": "1" * 64,
            "phase": 0, "retrain_asof": "2025-01-02", "signal_end": "2025-01-29"}


def test_stage0_receipt_roundtrip_and_tamper_reject(tmp_path):
    job, identity = _retrain_job(), _meta()
    artifacts = {"prediction.parquet": _DummyParquet(b"prediction"), "checkpoint.txt": "booster"}
    row = core.publish_receipt(tmp_path, job, identity, artifacts, {"fit_performed": True})
    assert row["status"] == "PASS"
    assert core.checked_receipt(tmp_path, job, identity)["job_sha256"] == stable_sha(job)
    # Tampered bytes are rejected.
    (core.receipt_path(tmp_path, job) / "checkpoint.txt").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.checked_receipt(tmp_path, job, identity)


def test_stage0_receipt_rejects_identity_and_job_drift(tmp_path):
    job, identity = _retrain_job(), _meta()
    artifacts = {"prediction.parquet": _DummyParquet(b"prediction"), "checkpoint.txt": "booster"}
    core.publish_receipt(tmp_path, job, identity, artifacts, {"fit_performed": True})
    with pytest.raises(ValueError, match="identity drift"):
        core.checked_receipt(tmp_path, job, {**identity, "quote_sha256": "0" * 64})
    with pytest.raises(ValueError, match="job spec mismatch"):
        core.checked_receipt(tmp_path, {**job, "signal_end": "2025-01-30"}, identity)
    with pytest.raises(ValueError, match="signed identity"):
        core.publish_receipt(tmp_path, _retrain_job(), identity, artifacts, {"quote_sha256": "x"})


# ---------------------------------------------------------------------------
# Cost stress + scoreboard.
# ---------------------------------------------------------------------------


def _synthetic_report() -> pd.DataFrame:
    idx = pd.date_range("2025-01-02", periods=424, freq="B")
    nav = 100_000_000.0 * (1.0 + 0.0004 * pd.Series(range(424), index=idx)).values
    frame = pd.DataFrame(index=idx)
    frame["account"] = nav
    frame["value"] = nav * 0.9
    frame["cash"] = nav * 0.1
    frame["return"] = pd.Series(nav).pct_change().fillna(nav[0] / 100_000_000.0 - 1.0).values + 0.0005
    frame["cost"] = 0.0005
    frame["total_cost"] = (0.0005 * nav).cumsum()
    frame["turnover"] = 0.05
    frame["total_turnover"] = (0.05 * nav).cumsum()
    frame["bench"] = 0.0002
    return frame


def test_stage0_cost_stress_is_conservative_and_reconciled():
    report = _synthetic_report()
    stressed = core.apply_cost_stress(report, 10.0)
    assert (stressed["account"] < report["account"]).all()
    assert ((stressed["value"] + stressed["cash"] - stressed["account"]).abs() < 1e-6).all()
    assert (stressed["total_cost"] > report["total_cost"]).all()
    with pytest.raises(ValueError, match="BLOCKED"):
        core.apply_cost_stress(report, -1.0)


def test_stage0_render_scoreboard(tmp_path):
    cells = []
    for market in ("csi300", "csi500"):
        for model_key in ("baseline", "c98856b4"):
            for combo_key, topk, dropn in core.COMBOS:
                for phase in (0, 10):
                    cells.append(
                        {
                            "market": market, "benchmark": core.MARKETS[market]["benchmark"], "model_key": model_key,
                            "candidate_id": core.MODELS[model_key]["candidate_id"], "combo_key": combo_key,
                            "topk": topk, "n_drop": dropn, "phase": phase,
                            "metrics": {"n_days": 424, "strategy_total_return": 0.1, "strategy_cagr": 0.06,
                                        "benchmark_cagr": 0.02, "relative_excess_cagr": 0.04, "sharpe": 1.2,
                                        "information_ratio": 0.8, "strategy_max_drawdown": -0.1,
                                        "mean_turnover": 0.3, "total_cost_sum": 0.05,
                                        "account_return_max_error": 1e-12},
                            "stressed": {"10bp": {"strategy_total_return": 0.09},
                                         "20bp": {"strategy_total_return": 0.08}},
                        }
                    )
    out = core.render_scoreboard({"cells": cells}, tmp_path / "scoreboard.csv")
    rows = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 17
    assert rows[0].split(",")[:8] == ["market", "benchmark", "model_key", "candidate_id", "combo_key", "topk",
                                      "n_drop", "phase"]


# ---------------------------------------------------------------------------
# Static Modal trap compliance (plan section 3.2).
# ---------------------------------------------------------------------------


def test_stage0_modal_traps_static():
    src = MODAL_SRC.read_text(encoding="utf-8")
    for token in ("quant/local_backup", "quant/data", ".worktrees", ".venv", "mlruns", "__pycache__", ".git",
                  ".cpp", ".so"):
        assert token in src, f"image ignore missing: {token}"
    for token in (
        "/root/qlib:",
        "/root/qlib/quant/code:",
        "/root/qlib/quant/code/execution:",
        "/root/qlib/quant/code/production:",
        "/root/qlib/quant/code/research/stage_ab:",
        "/root/qlib/quant/code/research/layered_search:",
        "/root/qlib/quant/code/research/csi300_csi500",
    ):
        assert token in src, f"PYTHONPATH missing: {token}"
    assert 'ds["handler"]["kwargs"]["instruments"] = market' in src
    assert 'job["benchmark"]' in src
    assert '"SH000852"' not in src
    assert "--detach" not in src
    assert "cpu=8, memory=16384, timeout=3600" in src
    assert "cpu=2, memory=4096, timeout=1800" in src
    assert "STAGE0_SMOKE_PASS 2/2 fits verified" in src
    assert "STAGE0_P0_COMPLETE 176/176 fits 16/16 cells PASS" in src
    assert "RESEARCH_VOLUME" in src
    assert "version=2" in src
