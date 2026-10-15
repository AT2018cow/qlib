"""Offline Gate 1 preflight for Stage 2 (L1) CSI300/CSI500 research.

Covers: 12-model merge identity (SHA recompute), plan-verbatim 10-combo
matrix, 384-l1cell roster uniqueness, zero-copy receipt pinning
(``source_signal_sha256``), verified prior-96 loader with byte-drift reject,
120-row strategy ranking, Top 4 diversity-constrained finalist selection,
480-row scoreboard / 240-row rankings CSV rendering, and static Modal trap
compliance including the absolute zero-training rule (no fitting call).
No provider, no Modal, no fitting, no paid runs.
"""

from __future__ import annotations

import hashlib
import json
import re as _re
from pathlib import Path

import pytest

import stage0_p0_core as p0
import stage2_l1_core as core
from audit.csi1000_layered_search_core import sha256_file, stable_sha

MODAL_SRC = Path(core.__file__).resolve().parent / "stage2_l1_modal.py"

EXPECTED_NEW_COMBOS = (
    ("top5_drop1", 5, 1),
    ("top8_drop1", 8, 1),
    ("top10_drop2", 10, 2),
    ("top15_drop1", 15, 1),
    ("top15_drop2", 15, 2),
    ("top20_drop1", 20, 1),
    ("top30_drop2", 30, 2),
    ("top30_drop3", 30, 3),
)
EXPECTED_ALL_COMBOS = (
    ("top5_drop1", 5, 1),
    ("top8_drop1", 8, 1),
    ("top10_drop1", 10, 1),
    ("top10_drop2", 10, 2),
    ("top15_drop1", 15, 1),
    ("top15_drop2", 15, 2),
    ("top20_drop1", 20, 1),
    ("top20_drop2", 20, 2),
    ("top30_drop2", 30, 2),
    ("top30_drop3", 30, 3),
)


def _model_sha(model_params: dict) -> str:
    from ruamel.yaml import YAML

    from qlib_live_retrain import apply_lgb_reproducibility

    cfg = YAML(typ="safe", pure=True).load(
        (Path(p0.REPO_ROOT) / "examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158_csi500.yaml").read_text(
            encoding="utf-8"
        )
    )
    cfg["task"]["model"]["kwargs"].update(model_params)
    apply_lgb_reproducibility(cfg)
    return stable_sha(cfg["task"]["model"])


def _fake_metrics(rel, sharpe=1.0, ir=0.5, mdd=-0.1, total=0.2, cagr=0.1):
    return {
        "n_days": 424,
        "strategy_total_return": total,
        "strategy_cagr": cagr,
        "benchmark_cagr": 0.05,
        "relative_excess_cagr": rel,
        "sharpe": sharpe,
        "information_ratio": ir,
        "strategy_max_drawdown": mdd,
        "mean_turnover": 0.2,
        "total_cost_sum": 0.05,
        "account_return_max_error": 1e-12,
    }


def _fake_cell(market, model_key, combo_key, phase, rel, anchor=False, source_stage=None, horizon=20):
    combos = {key: (topk, n_drop) for key, topk, n_drop in core.ALL_10_COMBOS}
    topk, n_drop = combos[combo_key]
    if source_stage is None:
        source_stage = core.ALL_12_MODELS[model_key]["source_stage"]
    return {
        "market": market,
        "benchmark": core.MARKETS[market]["benchmark"],
        "model_key": model_key,
        "candidate_id": core.ALL_12_MODELS[model_key]["candidate_id"],
        "label_horizon": horizon,
        "anchor": anchor,
        "source_stage": source_stage,
        "combo_key": combo_key,
        "topk": topk,
        "n_drop": n_drop,
        "phase": phase,
        "metrics": _fake_metrics(rel),
        "stressed": {"10bp": {"strategy_total_return": 0.1}, "20bp": {"strategy_total_return": 0.09}},
        "artifact_sha256": {"fixed_report.parquet": "a" * 64},
        "job_sha256": "c" * 64,
    }


# ---------------------------------------------------------------------------
# Model merge and combo matrix.
# ---------------------------------------------------------------------------


def test_stage2_all12_models_merge_and_sha_recompute():
    assert set(core.ALL_12_MODELS) == set(p0.MODELS) | set(core.STAGE1_NEW_MODELS)
    assert len(core.ALL_12_MODELS) == 12
    for key in ("baseline", "c98856b4"):
        spec = core.ALL_12_MODELS[key]
        assert spec["source_stage"] == "stage0_p0" and spec["label_horizon"] == 20
    for key, spec in core.STAGE1_NEW_MODELS.items():
        assert core.ALL_12_MODELS[key]["source_stage"] == "stage1_m1"
        assert core.ALL_12_MODELS[key]["label_horizon"] == spec["label_horizon"]
    for key, spec in core.ALL_12_MODELS.items():
        assert _model_sha(spec["model_params"]) == spec["model_sha256"], key


def test_stage2_combo_matrix_plan_verbatim():
    assert core.STAGE2_NEW_COMBOS == EXPECTED_NEW_COMBOS
    assert core.ALL_10_COMBOS == EXPECTED_ALL_COMBOS
    assert len(core.ALL_10_COMBOS) == len(set(core.ALL_10_COMBOS)) == 10
    assert set(core.STAGE2_NEW_COMBOS) | {("top20_drop2", 20, 2), ("top10_drop1", 10, 1)} == set(core.ALL_10_COMBOS)
    for _, topk, n_drop in core.ALL_10_COMBOS:
        assert topk >= 1 and 1 <= n_drop <= topk


# ---------------------------------------------------------------------------
# Roster geometry.
# ---------------------------------------------------------------------------


def test_stage2_cell_roster_is_384_unique_and_mapped():
    jobs = core.build_stage2_cell_jobs("stage2_l1_20261016_a1", "a" * 40)
    assert len(jobs) == 384
    assert len({core.job_id(j) for j in jobs}) == 384
    assert len({stable_sha(j) for j in jobs}) == 384
    combos = {key: (topk, n_drop) for key, topk, n_drop in core.ALL_10_COMBOS}
    for market in ("csi300", "csi500"):
        for model_key, spec in core.ALL_12_MODELS.items():
            for phase in (0, 10):
                group = [
                    j for j in jobs if j["market"] == market and j["model_key"] == model_key and j["phase"] == phase
                ]
                assert len(group) == 8
                assert {j["combo_key"] for j in group} == {key for key, _, _ in core.STAGE2_NEW_COMBOS}
                for job in group:
                    assert job["kind"] == "l1cell"
                    assert job["benchmark"] == core.MARKETS[market]["benchmark"]
                    assert job["candidate_id"] == spec["candidate_id"]
                    assert job["label_horizon"] == spec["label_horizon"]
                    assert job["source_stage"] == spec["source_stage"]
                    assert (job["topk"], job["n_drop"]) == combos[job["combo_key"]]
                    assert job["source_signal_relpath"] == core.source_signal_relpath(market, model_key, phase)
                    assert f"/{job['combo_key']}/phase{phase:02d}" in core.job_id(job)
    assert not any(j["combo_key"] in ("top20_drop2", "top10_drop1") for j in jobs)


def test_stage2_prefix_and_source_relpath_fail_closed():
    assert core.prefix_for("stage2_l1_20261016_a1", "b" * 40).endswith("stage2_l1_20261016_a1/bbbbbbbbbbbb")
    for bad in ("stage1_m1_20261015_a1", "stage2_l1_x", "../stage2_l1_abc"):
        with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
            core.prefix_for(bad, "b" * 40)
    anchor = core.source_signal_relpath("csi300", "baseline", 0)
    assert anchor == (f"{core.STAGE0_PREFIX}/cells/cell/csi300/baseline/23b92de0/top20_drop2/phase00/signal.parquet")
    fresh = core.source_signal_relpath("csi500", "m01_59b93929", 10)
    assert fresh == (f"{core.STAGE1_PREFIX}/cells/cell/csi500/m01_59b93929/59b93929/top20_drop2/phase10/signal.parquet")
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.source_signal_relpath("csi300", "nope", 0)
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.source_signal_relpath("csi300", "baseline", 5)
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.source_signal_relpath("csi1000", "baseline", 0)


# ---------------------------------------------------------------------------
# Receipts (zero-copy signal pin) and artifacts manifest.
# ---------------------------------------------------------------------------


class _DummyParquet:
    def __init__(self, data: bytes):
        self.data = data

    def to_parquet(self, path):
        Path(path).write_bytes(self.data)


def _bound_job():
    job = next(
        j
        for j in core.build_stage2_cell_jobs("stage2_l1_20261016_a1", "a" * 40)
        if j["market"] == "csi300"
        and j["model_key"] == "m01_59b93929"
        and j["combo_key"] == "top8_drop1"
        and j["phase"] == 0
    )
    return core.bind_stage2_job(
        job,
        provider_fingerprint="f" * 64,
        source_snapshot_sha256="e" * 64,
        quote_sha256="d" * 64,
        source_signal_sha256="9" * 64,
    )


def _identity(job):
    return {
        "run_id": job["run_id"],
        "code_sha": job["code_sha"],
        "provider_fingerprint": job["provider_fingerprint"],
        "source_snapshot_sha256": job["source_snapshot_sha256"],
        "quote_sha256": job["quote_sha256"],
    }


def test_stage2_artifacts_manifest_forbids_signal_copy():
    assert core.STAGE2_ARTIFACTS == {"l1cell": ("fixed_report.parquet", "decisions.json")}
    assert "signal.parquet" not in core.STAGE2_ARTIFACTS["l1cell"]


def test_stage2_receipt_roundtrip_and_signal_pin(tmp_path):
    job = _bound_job()
    details = {
        "source_signal_sha256": job["source_signal_sha256"],
        "source_signal_relpath": job["source_signal_relpath"],
        "fit_performed": False,
        "new_fits": 0,
    }
    artifacts = {"fixed_report.parquet": _DummyParquet(b"report"), "decisions.json": [{"d": 1}]}
    row = core.publish_receipt(tmp_path, job, _identity(job), artifacts, details)
    assert row["status"] == "PASS" and row["new_fits"] == 0
    checked = core.checked_receipt(tmp_path, job, _identity(job))
    assert checked["job_sha256"] == stable_sha(job)
    assert checked["source_signal_sha256"] == "9" * 64
    # Byte tamper of a published artifact is rejected.
    (core.receipt_path(tmp_path, job) / "decisions.json").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.checked_receipt(tmp_path, job, _identity(job))


def test_stage2_receipt_rejects_signal_mismatch_and_bad_manifest(tmp_path):
    job = _bound_job()
    details = {
        "source_signal_sha256": job["source_signal_sha256"],
        "source_signal_relpath": job["source_signal_relpath"],
    }
    artifacts = {"fixed_report.parquet": _DummyParquet(b"report"), "decisions.json": [{"d": 1}]}
    core.publish_receipt(tmp_path, job, _identity(job), artifacts, details)
    # A receipt whose signal pin drifted is rejected even if bytes are intact.
    rec_file = core.receipt_path(tmp_path, job) / "receipt.json"
    record = json.loads(rec_file.read_text(encoding="utf-8"))
    record["source_signal_sha256"] = "8" * 64
    rec_file.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.checked_receipt(tmp_path, job, _identity(job))
    # Publishing a signal artifact (the forbidden 384x copy) is rejected.
    other = tmp_path / "second"
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.publish_receipt(
            other,
            job,
            _identity(job),
            {"fixed_report.parquet": _DummyParquet(b"r"), "decisions.json": [], "signal.parquet": _DummyParquet(b"s")},
            details,
        )
    # Details missing the signal pin are rejected.
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.publish_receipt(
            other,
            job,
            _identity(job),
            {"fixed_report.parquet": _DummyParquet(b"r"), "decisions.json": []},
            {"fit_performed": False},
        )


# ---------------------------------------------------------------------------
# Prior-96 loader (Stage 1 summary re-verification).
# ---------------------------------------------------------------------------


def _fabricate_prior(root: Path) -> dict:
    cells = []
    stages = (
        ("stage0_p0", core.STAGE0_PREFIX, core.STAGE0_RUN_ID, core.STAGE0_CODE_SHA, ("baseline", "c98856b4")),
        (
            "stage1_m1",
            core.STAGE1_PREFIX,
            core.STAGE1_RUN_ID,
            core.STAGE1_CODE_SHA,
            tuple(k for k, s in core.ALL_12_MODELS.items() if s["source_stage"] == "stage1_m1"),
        ),
    )
    combos = {key: (topk, n_drop) for key, topk, n_drop in core.ALL_10_COMBOS}
    for stage_key, prefix, run_id, code_sha, models in stages:
        for market in ("csi300", "csi500"):
            for model_key in models:
                spec = core.ALL_12_MODELS[model_key]
                for combo_key in core.PRIOR_COMBOS:
                    topk, n_drop = combos[combo_key]
                    for phase in (0, 10):
                        job_sha = stable_sha(
                            {
                                "stage": stage_key,
                                "market": market,
                                "model": model_key,
                                "combo": combo_key,
                                "phase": phase,
                            }
                        )
                        cdir = (
                            root
                            / prefix
                            / f"cells/cell/{market}/{model_key}/{spec['candidate_id'][:8]}/{combo_key}/phase{phase:02d}"
                        )
                        cdir.mkdir(parents=True, exist_ok=True)
                        blobs = {
                            "fixed_report.parquet": b"report-" + job_sha[:12].encode(),
                            "decisions.json": b"[]",
                            "signal.parquet": b"signal-" + job_sha[:12].encode(),
                        }
                        hashes = {}
                        for name, blob in blobs.items():
                            (cdir / name).write_bytes(blob)
                            hashes[name] = hashlib.sha256(blob).hexdigest()
                        record = {
                            "status": "PASS",
                            "kind": "cell",
                            "run_id": run_id,
                            "code_sha": code_sha,
                            "provider_fingerprint": "f" * 64,
                            "source_snapshot_sha256": "e" * 64,
                            "quote_sha256": "d" * 64,
                            "job_sha256": job_sha,
                            "artifact_sha256": hashes,
                        }
                        (cdir / "receipt.json").write_text(json.dumps(record), encoding="utf-8")
                        cells.append(
                            {
                                "market": market,
                                "benchmark": core.MARKETS[market]["benchmark"],
                                "model_key": model_key,
                                "candidate_id": spec["candidate_id"],
                                "combo_key": combo_key,
                                "topk": topk,
                                "n_drop": n_drop,
                                "phase": phase,
                                "metrics": _fake_metrics(0.01 * len(cells)),
                                "stressed": {
                                    "10bp": {"strategy_total_return": 0.1},
                                    "20bp": {"strategy_total_return": 0.09},
                                },
                                "artifact_sha256": hashes,
                                "job_sha256": job_sha,
                                "anchor": stage_key == "stage0_p0",
                            }
                        )
    summary = {
        "status": "PASS",
        "run_id": core.STAGE1_RUN_ID,
        "code_sha": core.STAGE1_CODE_SHA,
        "completed_cells": 80,
        "anchor_cells": 16,
        "total_cells": 96,
        "cells": cells,
    }
    summary_path = root / core.STAGE1_PREFIX / "stage1_m1_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    return summary


def test_stage2_load_prior_96_cells_ok_and_drift_reject(tmp_path):
    _fabricate_prior(tmp_path)
    prior = core.load_prior_96_cells(tmp_path)
    assert len(prior) == 96
    assert all(c["receipt_sha256"] for c in prior)
    assert {(c["model_key"], c["combo_key"]) for c in prior} == {
        (m, k) for m in core.ALL_12_MODELS for k in core.PRIOR_COMBOS
    }
    assert sum(c["anchor"] for c in prior) == 16
    assert {c["source_stage"] for c in prior if c["anchor"]} == {"stage0_p0"}
    assert {c["source_stage"] for c in prior if not c["anchor"]} == {"stage1_m1"}
    assert all(c["label_horizon"] == core.ALL_12_MODELS[c["model_key"]]["label_horizon"] for c in prior)


def test_stage2_load_prior_96_cells_rejects_byte_and_receipt_drift(tmp_path):
    _fabricate_prior(tmp_path)
    target = (
        tmp_path / core.STAGE0_PREFIX / ("cells/cell/csi300/baseline/23b92de0/top20_drop2/phase00/fixed_report.parquet")
    )
    original = target.read_bytes()
    target.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.load_prior_96_cells(tmp_path)
    target.write_bytes(original)
    assert len(core.load_prior_96_cells(tmp_path)) == 96
    rec_file = target.parent / "receipt.json"
    record = json.loads(rec_file.read_text(encoding="utf-8"))
    record["job_sha256"] = "1" * 64
    rec_file.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.load_prior_96_cells(tmp_path)


def test_stage2_load_prior_96_cells_rejects_summary_drift(tmp_path):
    summary = _fabricate_prior(tmp_path)
    summary["completed_cells"] = 79
    (tmp_path / core.STAGE1_PREFIX / "stage1_m1_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.load_prior_96_cells(tmp_path)


# ---------------------------------------------------------------------------
# Ranking and diversified finalist selection.
# ---------------------------------------------------------------------------


def _synthetic_market_cells(market: str = "csi300") -> list[dict]:
    cells = []
    for model_idx, model_key in enumerate(core.ALL_12_MODELS):
        for combo_idx, (combo_key, _, _) in enumerate(core.ALL_10_COMBOS):
            score = 1.0 - 0.01 * model_idx - 0.0005 * combo_idx
            cells.append(_fake_cell(market, model_key, combo_key, 0, score))
            cells.append(_fake_cell(market, model_key, combo_key, 10, score + 0.02))
    return cells


def test_stage2_rank_market_120_rows_sorted():
    cells = _synthetic_market_cells()
    assert len(cells) == 240
    rows = core.rank_market_120_strategies(cells)
    assert len(rows) == 120
    assert [r["rank"] for r in rows] == list(range(1, 121))
    worsts = [r["worst_relative_excess_cagr"] for r in rows]
    assert worsts == sorted(worsts, reverse=True)
    assert rows[0]["model_key"] == "baseline"
    assert {r["model_key"] for r in rows} == set(core.ALL_12_MODELS)
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.rank_market_120_strategies(cells[:239])
    mixed = cells[:120] + [_fake_cell("csi500", c["model_key"], c["combo_key"], c["phase"], 0.5) for c in cells[120:]]
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.rank_market_120_strategies(mixed)


def test_stage2_select_top4_diverse_finalists():
    rows = core.rank_market_120_strategies(_synthetic_market_cells())
    picked = core.select_top4_diverse_finalists(rows, max_per_model=2, top_n=4)
    assert len(picked) == 4
    assert [r["finalist_rank"] for r in picked] == [1, 2, 3, 4]
    counts = {}
    for row in picked:
        counts[row["model_key"]] = counts.get(row["model_key"], 0) + 1
    assert max(counts.values()) <= 2
    # The single best model owns the top 10 rows, yet cannot exceed 2 slots.
    assert set(counts) == {"baseline", "c98856b4"}
    strict = core.select_top4_diverse_finalists(rows, max_per_model=1, top_n=4)
    assert sorted(r["model_key"] for r in strict) == ["baseline", "c98856b4", "m01_59b93929", "m02_a419047a"]
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.select_top4_diverse_finalists([rows[0]] * 120, max_per_model=2, top_n=4)
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.select_top4_diverse_finalists(rows[:119], max_per_model=2, top_n=4)


def test_stage2_render_scoreboard_480_and_rankings_240(tmp_path):
    cells = []
    for market in ("csi300", "csi500"):
        for combo_key, _, _ in core.STAGE2_NEW_COMBOS:
            for model_key in core.ALL_12_MODELS:
                for phase in (0, 10):
                    cell = _fake_cell(market, model_key, combo_key, phase, 0.02)
                    cell["source_signal_sha256"] = "7" * 64
                    cells.append(cell)
        for combo_key in core.PRIOR_COMBOS:
            for model_key in core.ALL_12_MODELS:
                for phase in (0, 10):
                    anchor = core.ALL_12_MODELS[model_key]["source_stage"] == "stage0_p0"
                    cells.append(_fake_cell(market, model_key, combo_key, phase, 0.01, anchor=anchor))
    assert len(cells) == 480
    rankings = {m: core.rank_market_120_strategies(_synthetic_market_cells(m)) for m in ("csi300", "csi500")}
    finalists = {m: core.select_top4_diverse_finalists(rankings[m]) for m in ("csi300", "csi500")}
    summary = {"cells": cells, "rankings": rankings, "finalists": finalists}
    scoreboard = core.render_stage2_scoreboard(summary, tmp_path / "scoreboard.csv")
    rows = scoreboard.read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 481
    assert rows[0].split(",")[:7] == [
        "market",
        "benchmark",
        "model_key",
        "candidate_id",
        "label_horizon",
        "anchor",
        "source_stage",
    ]
    assert rows[0].endswith("source_signal_sha256")
    ranking_csv = core.render_stage2_rankings(summary, tmp_path / "rankings.csv")
    rrows = ranking_csv.read_text(encoding="utf-8").strip().splitlines()
    assert len(rrows) == 241
    assert rrows[0].split(",")[:3] == ["market", "rank", "finalist_rank"]
    annotated = sum(1 for line in rrows[1:] if line.split(",")[2] != "")
    assert annotated == 8
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.render_stage2_scoreboard({"cells": cells[:479]}, tmp_path / "bad.csv")
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.render_stage2_rankings(
            {"rankings": {"csi300": rankings["csi300"]}, "finalists": finalists}, tmp_path / "bad2.csv"
        )


# ---------------------------------------------------------------------------
# Static Modal trap compliance (zero fits by construction).
# ---------------------------------------------------------------------------


def test_stage2_modal_imports_all_resolve_in_core():
    src = MODAL_SRC.read_text(encoding="utf-8")
    match = _re.search(r"from stage2_l1_core import \((.*?)\)", src, _re.DOTALL)
    assert match, "core import block missing"
    names = []
    for line in match.group(1).splitlines():
        name = line.split("#")[0].strip().rstrip(",").strip()
        if name:
            names.append(name)
    assert len(names) > 20
    for name in names:
        assert hasattr(core, name), f"core missing Modal import: {name}"


def test_stage2_modal_zero_fits_and_traps_static():
    src = MODAL_SRC.read_text(encoding="utf-8")
    # Absolute zero-training rule (word-boundary check on the call form).
    assert ".fit(" not in src
    assert "model.fit" not in src
    assert "fit_remote" not in src
    assert "--detach" not in src
    for token in (
        "quant/local_backup",
        "quant/data",
        ".worktrees",
        ".venv",
        "mlruns",
        "__pycache__",
        ".git",
        ".cpp",
        ".so",
    ):
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
    assert '"SH000852"' not in src
    assert "_find_repo_root" in src
    assert "load_frozen_result" not in src
    assert 'ds["handler"]' not in src  # no training dataset config at all
    assert "cpu=2, memory=4096, timeout=1800, retries=1" in src
    assert "STAGE2_SMOKE_PASS 2/2 replay cells verified (0 fits)" in src
    assert "STAGE2_L1_COMPLETE 384/384 new cells 480/480 total ranking PASS (0 fits)" in src
    assert "STAGE2_L1CELL_WAVE" in src
    assert "ThreadPoolExecutor(max_workers=16)" in src
    assert "max_workers: int = 64" in src
    assert "version=2" in src
    assert "qlib-csi300-csi500-stage2-l1" in src
    assert '"top8_drop1"' in src and '"top15_drop2"' in src
    assert "row is None" in src  # verified-receipt resume path


def test_stage2_modal_never_publishes_signal_artifact():
    src = MODAL_SRC.read_text(encoding="utf-8")
    assert '"signal.parquet":' not in src
    assert 'hashes.get("signal.parquet")' in src  # read-only SHA verification of sources
