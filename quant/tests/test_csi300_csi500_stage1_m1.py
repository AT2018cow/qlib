"""Offline Gate 1 preflight for Stage 1 (M1) CSI300/CSI500 research.

Covers: 10 new models' candidate_id/model_sha256 exact recompute, 880-fit /
80-cell roster geometry, horizon-10 vs horizon-20 split/purge correctness,
per-market ranking, Stage 0 anchor loading, scoreboard rendering and static
Modal trap compliance. No provider, no Modal, no fitting, no paid runs.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

import stage1_m1_core as core
from audit.csi1000_layered_search_core import stable_sha
from csi1000_stage_b_core import build_phase_jobs

MODAL_SRC = Path(core.__file__).resolve().parent / "stage1_m1_modal.py"

EXPECTED_IDS = {
    "m01_59b93929": "59b93929bff1e736228d31403a167781aff433d27da1870bcf9fe34e04b6bbb9",
    "m02_a419047a": "a419047ab97ec0273ebe67791b6e225e824b2764f5932d73651efd89add5a677",
    "m03_4e908173": "4e908173705c76fee3782be37068672a3a845bd61894202f3152afe3b9d81ef2",
    "m04_17dbfe28": "17dbfe28fea8d7a782918ee426e01c7f7b23a08b44c00cc7e2d8391974efd5f2",
    "m05_e74b3bbe": "e74b3bbe2278aa89b33375fe21bc308bc5cf0b2e5ee512ab1b05469b52a4825a",
    "m06_b034ac46": "b034ac4645fb45bba35bc185693b7776344c9117f594e4c972671062cdcf9457",
    "m07_shallow_leaf15": "364c2d58afb727d7051f0910e0ebe476caec55a271cd314faeda240e3c8b8ced",
    "m08_mid_leaf31_lr05": "0bb48b85dc56b19683ad77c061e4b44df8ec4ea603a63da516b745eac65c657b",
    "m09_raw10d_baseline": "4f11110de555e962750048aaa68f3c5d636ffe59e835ee37b51ce991dc79e5ce",
    "m10_raw10d_c98856b4": "99627a9d20842f5ac43874593b71b175cc533fe2f09666a9417a589f35c7eb1f",
}
EXPECTED_SHAS = {
    "m01_59b93929": "b97bc44d1c02acb4d85c344d2d570e00ef9ff365228c8a9550fa45393859ae67",
    "m02_a419047a": "28596be4fbe4f24b20fe419a3cfc2016192169b81b07eec50a7181119f9726d8",
    "m03_4e908173": "0f2cf94d179e7b9982f873588a3afe25fa02991c873954fea11d2db0e025cb88",
    "m04_17dbfe28": "5a311bfc78b40b2758d77c1ba55c092c1953447f133a111d4ed6b28bae39cbc2",
    "m05_e74b3bbe": "e4d33fa7175a1e01068b6d4afcd8525c706079d2f9fc8d668ef41a1b8ee6eae4",
    "m06_b034ac46": "d095b0fe976e8f438f086a2b3a31bbb70d8d8560aa18e1594c3b2d299329a901",
    "m07_shallow_leaf15": "5c4543183558ada6b58ccb0833e7422d9643d643914d3d8f69dd766018deb1b9",
    "m08_mid_leaf31_lr05": "b3c4345e07f1bd5f75bb7aeb505f40d8a20e28648459678ddb0efce0efe3fdf6",
    "m09_raw10d_baseline": "793021af66034ec508848ee032f6d309103291981abb63d11ae779da9c5ee7d3",
    "m10_raw10d_c98856b4": "d49e893ad37c821a6a092fb17baf7ee29c9ff71289bbeb1205132e34e3854c50",
}
EXPECTED_HORIZONS = {
    "m01_59b93929": 20,
    "m02_a419047a": 20,
    "m03_4e908173": 20,
    "m04_17dbfe28": 20,
    "m05_e74b3bbe": 20,
    "m06_b034ac46": 20,
    "m07_shallow_leaf15": 20,
    "m08_mid_leaf31_lr05": 20,
    "m09_raw10d_baseline": 10,
    "m10_raw10d_c98856b4": 10,
}


def _model_sha(model_params: dict) -> str:
    from ruamel.yaml import YAML

    from qlib_live_retrain import apply_lgb_reproducibility
    from stage0_p0_core import REPO_ROOT

    cfg = YAML(typ="safe", pure=True).load(
        (Path(REPO_ROOT) / "examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158_csi500.yaml")
        .read_text(encoding="utf-8")
    )
    cfg["task"]["model"]["kwargs"].update(model_params)
    apply_lgb_reproducibility(cfg)
    return stable_sha(cfg["task"]["model"])


def _synthetic_calendar() -> list[str]:
    anchor = date(2024, 12, 31)
    start = date(2025, 1, 2)
    end = date(2026, 9, 30)
    span = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    middle = span[1:-1][:422]
    execution = [start.isoformat()] + [d.isoformat() for d in middle] + [end.isoformat()]
    assert len(execution) == 424
    hist = [anchor - timedelta(days=i) for i in range(1500, 0, -1)]
    calendar = [d.isoformat() for d in hist] + [anchor.isoformat()] + execution
    assert calendar == sorted(set(calendar))
    return calendar


# ---------------------------------------------------------------------------
# Model identities.
# ---------------------------------------------------------------------------


def test_stage1_ten_models_exact_and_sha_recompute():
    assert set(core.STAGE1_NEW_MODELS) == set(EXPECTED_IDS)
    for key, spec in core.STAGE1_NEW_MODELS.items():
        assert spec["candidate_id"] == EXPECTED_IDS[key]
        assert spec["label_horizon"] == EXPECTED_HORIZONS[key]
        assert _model_sha(spec["model_params"]) == spec["model_sha256"] == EXPECTED_SHAS[key]


def test_stage1_label_expr_dynamic():
    assert core.label_expr(20) == "Ref($close, -20)/$close - 1"
    assert core.label_expr(10) == "Ref($close, -10)/$close - 1"
    for bad in (0, 5, 15, 30):
        with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
            core.label_expr(bad)
    with pytest.raises(ValueError):
        core.label_expr("20x")


def test_stage1_prefix_and_run_id_fail_closed():
    assert core.prefix_for("stage1_m1_20261015_a1", "a" * 40).endswith("stage1_m1_20261015_a1/aaaaaaaaaaaa")
    for bad in ("stage0_p0_20261015_a1", "stage1_m1_x", "../stage1_m1_abc"):
        with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
            core.prefix_for(bad, "a" * 40)


# ---------------------------------------------------------------------------
# Roster geometry.
# ---------------------------------------------------------------------------


def test_stage1_fit_roster_is_880_unique_and_horizon_mapped():
    calendar = _synthetic_calendar()
    jobs = core.build_stage1_fit_jobs(calendar, "stage1_m1_20261015_a1", "a" * 40)
    assert len(jobs) == 880
    assert len({core.job_id(j) for j in jobs}) == 880
    assert len({stable_sha(j) for j in jobs}) == 880
    for market in ("csi300", "csi500"):
        for model_key, spec in core.STAGE1_NEW_MODELS.items():
            for phase in (0, 10):
                group = [j for j in jobs if j["market"] == market and j["model_key"] == model_key and j["phase"] == phase]
                assert len(group) == 22
                legal = {(p["retrain_asof"], p["signal_end"]) for p in build_phase_jobs(calendar, phase)}
                assert {(j["retrain_asof"], j["signal_end"]) for j in group} == legal
    for job in jobs:
        assert job["benchmark"] == core.MARKETS[job["market"]]["benchmark"]
        assert job["label_horizon"] == EXPECTED_HORIZONS[job["model_key"]]
        assert f"/h{job['label_horizon']}/" in core.job_id(job)


def test_stage1_cell_roster_is_80_unique():
    jobs = core.build_stage1_cell_jobs("stage1_m1_20261015_a1", "a" * 40)
    assert len(jobs) == 80
    assert len({core.job_id(j) for j in jobs}) == 80
    assert len({stable_sha(j) for j in jobs}) == 80
    for market in ("csi300", "csi500"):
        for model_key in core.STAGE1_NEW_MODELS:
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
                    assert group[0]["label_horizon"] == EXPECTED_HORIZONS[model_key]


def test_stage1_smoke_slices_are_raw20_plus_raw10_first_asof():
    jobs = core.build_stage1_fit_jobs(_synthetic_calendar(), "stage1_m1_20261015_a1", "a" * 40)
    first20 = next(j for j in jobs if j["market"] == "csi300" and j["model_key"] == "m01_59b93929" and j["phase"] == 0)
    first10 = next(j for j in jobs if j["market"] == "csi500" and j["model_key"] == "m09_raw10d_baseline" and j["phase"] == 0)
    all_first = sorted({(j["retrain_asof"], j["signal_end"]) for j in jobs if j["phase"] == 0})
    assert (first20["retrain_asof"], first20["signal_end"]) == all_first[0]
    assert (first10["retrain_asof"], first10["signal_end"]) == all_first[0]
    assert first20["retrain_asof"] == "2024-12-31" and first10["retrain_asof"] == "2024-12-31"
    assert first20["label_horizon"] == 20 and first10["label_horizon"] == 10


# ---------------------------------------------------------------------------
# Horizon-10 vs horizon-20 splits and purge.
# ---------------------------------------------------------------------------


def test_stage1_retrain_splits_maturity_for_both_horizons():
    calendar = _synthetic_calendar()
    asof = "2025-06-05"
    asof_i = calendar.index(asof)
    for horizon in (10, 20):
        splits = core.retrain_splits(calendar, asof, horizon)
        assert splits["horizon"] == horizon
        valid = splits["valid"]
        assert calendar.index(valid[1]) - calendar.index(valid[0]) + 1 == 252
        assert valid[1] == calendar[asof_i - horizon - 1]
        # Training tail is label-matured: last train bar + horizon < first valid bar.
        assert calendar.index(splits["train"][1]) + horizon < calendar.index(valid[0])
        assert splits["train"][0] == "2016-01-01"


def test_stage1_purge_cfg_splits_horizon_dynamic():
    from qlib_audit_fixes import purge_cfg_splits

    def _cfg():
        return {
            "task": {
                "dataset": {
                    "kwargs": {
                        "segments": {
                            "train": ["2016-01-01", "2024-06-01"],
                            "valid": ["2024-06-02", "2025-06-05"],
                            "test": ["2025-06-06", "2025-07-03"],
                        },
                        "handler": {"kwargs": {"fit_end_time": "2024-06-01"}},
                    }
                }
            }
        }

    calendar = _synthetic_calendar()
    purged20 = purge_cfg_splits(_cfg(), calendar, horizon=20)["task"]["dataset"]["kwargs"]["segments"]
    purged10 = purge_cfg_splits(_cfg(), calendar, horizon=10)["task"]["dataset"]["kwargs"]["segments"]
    # Horizon-20 purges harder (earlier safe ends) than horizon-10.
    assert purged20["train"][1] <= purged10["train"][1]
    assert purged20["valid"][1] <= purged10["valid"][1]


# ---------------------------------------------------------------------------
# Receipts, anchors, ranking, scoreboard.
# ---------------------------------------------------------------------------


class _DummyParquet:
    def __init__(self, data: bytes):
        self.data = data

    def to_parquet(self, path):
        Path(path).write_bytes(self.data)


def _retrain_job():
    return {**_meta(), "kind": "retrain", "market": "csi300", "benchmark": "SH000300",
            "model_key": "m01_59b93929", "candidate_id": "c" * 64, "model_params": {"learning_rate": 0.1},
            "model_sha256": "1" * 64, "label_horizon": 20, "phase": 0,
            "retrain_asof": "2025-01-02", "signal_end": "2025-01-29"}


def _meta():
    return {
        "run_id": "stage1_m1_20261015_a1",
        "code_sha": "a" * 40,
        "provider_fingerprint": "f" * 64,
        "source_snapshot_sha256": "e" * 64,
        "quote_sha256": "d" * 64,
    }


def test_stage1_receipt_roundtrip_and_tamper_reject(tmp_path):
    job, identity = _retrain_job(), _meta()
    artifacts = {"prediction.parquet": _DummyParquet(b"prediction"), "checkpoint.txt": "booster"}
    row = core.publish_receipt(tmp_path, job, identity, artifacts, {"fit_performed": True})
    assert row["status"] == "PASS"
    assert core.checked_receipt(tmp_path, job, identity)["job_sha256"] == stable_sha(job)
    (core.receipt_path(tmp_path, job) / "checkpoint.txt").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.checked_receipt(tmp_path, job, identity)


def _fake_metrics(rel, sharpe=1.0, ir=0.5, mdd=-0.1, total=0.2, cagr=0.1):
    return {
        "n_days": 424, "strategy_total_return": total, "strategy_cagr": cagr, "benchmark_cagr": 0.05,
        "relative_excess_cagr": rel, "sharpe": sharpe, "information_ratio": ir,
        "strategy_max_drawdown": mdd, "mean_turnover": 0.2, "total_cost_sum": 0.05,
        "account_return_max_error": 1e-12,
    }


def _fake_cell(market, model_key, combo_key, phase, rel, anchor=False, horizon=20):
    return {
        "market": market, "benchmark": core.MARKETS[market]["benchmark"], "model_key": model_key,
        "candidate_id": "c" * 64, "label_horizon": horizon, "anchor": anchor, "combo_key": combo_key,
        "topk": 20 if combo_key == "top20_drop2" else 10, "n_drop": 2 if combo_key == "top20_drop2" else 1,
        "phase": phase, "metrics": _fake_metrics(rel),
        "stressed": {"10bp": {"strategy_total_return": 0.1}, "20bp": {"strategy_total_return": 0.09}},
        "artifact_sha256": {"a": "b"}, "job_sha256": "c" * 64,
    }


def test_stage1_rank_market_cells_24_rows_sorted():
    cells = []
    combos = ["top20_drop2", "top10_drop1"]
    models = [f"m{i:02d}" for i in range(1, 11)] + ["baseline", "c98856b4"]
    for i, model_key in enumerate(models):
        for combo_key in combos:
            rel0 = 0.01 * (i + (0.001 if combo_key == "top10_drop1" else 0.0))
            rel10 = 0.01 * (i + 1 + (0.001 if combo_key == "top10_drop1" else 0.0))
            anchor = model_key in ("baseline", "c98856b4")
            cells.append(_fake_cell("csi300", model_key, combo_key, 0, rel0, anchor=anchor))
            cells.append(_fake_cell("csi300", model_key, combo_key, 10, rel10, anchor=anchor))
    assert len(cells) == 48
    rows = core.rank_market_cells(cells)
    assert len(rows) == 24
    assert [r["rank"] for r in rows] == list(range(1, 25))
    worsts = [r["worst_relative_excess_cagr"] for r in rows]
    assert worsts == sorted(worsts, reverse=True)
    assert rows[0]["model_key"] == "c98856b4" and rows[0]["combo_key"] == "top10_drop1"
    assert rows[0]["worst_relative_excess_cagr"] == pytest.approx(0.11001)
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.rank_market_cells(cells[:47])


def test_stage1_load_anchor_cells_and_drift_reject(tmp_path):
    import hashlib

    prefix = f"csi300_csi500_research/stage0_p0/{core.STAGE0_RUN_ID}/{core.STAGE0_CODE_SHA[:12]}"
    root = tmp_path / prefix
    cells = []
    for market in ("csi300", "csi500"):
        for model_key in ("baseline", "c98856b4"):
            for combo_key in ("top20_drop2", "top10_drop1"):
                for phase in (0, 10):
                    cid = "c" * 64
                    job_sha = stable_sha({"x": (market, model_key, combo_key, phase)})
                    metrics = _fake_metrics(0.05)
                    cell = {
                        "market": market, "benchmark": core.MARKETS[market]["benchmark"], "model_key": model_key,
                        "candidate_id": cid, "combo_key": combo_key, "topk": 20, "n_drop": 2, "phase": phase,
                        "metrics": metrics, "stressed": {"10bp": {"strategy_total_return": 0.1}},
                        "artifact_sha256": {}, "job_sha256": job_sha,
                    }
                    cells.append(cell)
                    cdir = (root / "cells" / f"cell/{market}/{model_key}/{cid[:8]}/{combo_key}/phase{phase:02d}")
                    cdir.mkdir(parents=True)
                    report = b"report-bytes-" + market.encode() + str(phase).encode()
                    (cdir / "fixed_report.parquet").write_bytes(report)
                    digest = hashlib.sha256(report).hexdigest()
                    cell["artifact_sha256"] = {"fixed_report.parquet": digest}
                    (cdir / "receipt.json").write_text(
                        json.dumps({"status": "PASS", "job_sha256": job_sha, "artifact_sha256": cell["artifact_sha256"]})
                    )
    (root / "stage0_p0_summary.json").write_text(
        json.dumps({"status": "PASS", "completed_cells": 16, "run_id": core.STAGE0_RUN_ID,
                    "code_sha": core.STAGE0_CODE_SHA, "cells": cells})
    )
    anchors = core.load_stage0_anchor_cells(tmp_path)
    assert len(anchors) == 16 and all(c["anchor"] is True for c in anchors)
    # Byte drift is rejected.
    first = root / "cells" / "cell/csi300/baseline/cccccccc/top20_drop2/phase00/fixed_report.parquet"
    first.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.load_stage0_anchor_cells(tmp_path)


def test_stage1_render_scoreboard_96(tmp_path):
    cells = []
    for market in ("csi300", "csi500"):
        for i, model_key in enumerate([f"m{j:02d}" for j in range(1, 11)] + ["baseline", "c98856b4"]):
            for combo_key in ("top20_drop2", "top10_drop1"):
                for phase in (0, 10):
                    anchor = model_key in ("baseline", "c98856b4")
                    cells.append(_fake_cell(market, model_key, combo_key, phase, 0.01 * i, anchor=anchor))
    assert len(cells) == 96
    out = core.render_stage1_scoreboard({"cells": cells}, tmp_path / "scoreboard.csv")
    rows = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 97
    assert rows[0].split(",")[:6] == ["market", "benchmark", "model_key", "candidate_id", "label_horizon", "anchor"]
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.render_stage1_scoreboard({"cells": cells[:95]}, tmp_path / "bad.csv")


def test_stage1_shared_signal_path():
    path = core.shared_signal_path(Path("/results/x"), "csi300", "m01_59b93929", 0)
    assert str(path) == "/results/x/signals/csi300/m01_59b93929/phase00/signal.parquet"
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.shared_signal_path(Path("/results/x"), "csi300", "nope", 0)


# ---------------------------------------------------------------------------
# Static Modal trap compliance.
# ---------------------------------------------------------------------------


def test_stage1_modal_imports_all_resolve_in_core():
    """Every name the Modal runner imports from core must exist (catches --help-only failures)."""
    import re as _re

    src = MODAL_SRC.read_text(encoding="utf-8")
    match = _re.search(r"from stage1_m1_core import \((.*?)\)", src, _re.DOTALL)
    assert match, "core import block missing"
    names = [n.strip() for n in match.group(1).split(",") if n.strip() and not n.strip().startswith("#")]
    assert len(names) > 10
    for name in names:
        assert hasattr(core, name), f"core missing Modal import: {name}"


def test_stage1_modal_traps_static():
    src = MODAL_SRC.read_text(encoding="utf-8")
    for token in ("quant/local_backup", "quant/data", ".worktrees", ".venv", "mlruns", "__pycache__", ".git",
                  ".cpp", ".so"):
        assert token in src, f"image ignore missing: {token}"
    for token in (
        "/root/qlib:",
        "/root/qlib/quant/code:",
        "/root/qlib/quant/code/execution:",
        "/root/qlib/quant/code/research/stage_ab:",
        "/root/qlib/quant/code/research/layered_search:",
        "/root/qlib/quant/code/research/csi300_csi500",
    ):
        assert token in src, f"PYTHONPATH missing: {token}"
    assert 'ds["handler"]["kwargs"]["instruments"] = market' in src
    assert 'job["benchmark"]' in src
    assert '"SH000852"' not in src
    assert "--detach" not in src
    assert "parents[4]" not in src
    assert "_find_repo_root" in src
    assert "load_frozen_result" not in src
    assert "cpu=8, memory=16384, timeout=3600, retries=1" in src
    assert "cpu=2, memory=4096, timeout=1800" in src
    # Horizon dynamics (never hardcoded 20).
    assert 'require(horizon in (10, 20), "STOP_STAGE1_HORIZON")' in src
    assert "horizon = int(job[" in src
    assert "purge_cfg_splits(config, calendar, horizon=horizon)" in src
    assert '"horizon=20"' not in src
    assert "STAGE1_SMOKE_PASS 2/2 fits verified (raw20 + raw10)" in src
    assert "STAGE1_M1_COMPLETE 880/880 fits 80/80 cells 96/96 ranking PASS" in src
    assert "STAGE1_SAFE_PAUSE" in src
    assert "max_workers: int = 64" in src
    assert "version=2" in src
    assert "qlib-csi300-csi500-stage1-m1" in src or "stage1-m1" in src
