"""Offline Gate 1 preflight for Stage 3 (L4) CSI300/CSI500 five-phase research.

Covers: plan-verbatim model/strategy rosters, 12-model SHA recompute,
660-l4retrain / 54-l4cell roster geometry, fail-closed prefix/relpath/lookup
guards, receipt round-trips (fit_performed / 22-prediction-SHA pin), 424-day
signal assembly from 22 fabricated slice fits with gap/duplicate rejection,
verified 36-prior-unit loader with byte/summary drift rejection, five-phase
ranking arithmetic, 90-row scoreboard / 18-row rankings CSV rendering, and
static Modal trap compliance (real fits only through fit_remote, 194-file pull
with fits/ skipped). No provider, no Modal, no fitting, no paid runs.
"""

from __future__ import annotations

import hashlib
import json
import re as _re
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

import stage0_p0_core as p0
import stage3_l4_core as core
from audit.csi1000_layered_search_core import sha256_file, stable_sha

MODAL_SRC = Path(core.__file__).resolve().parent / "stage3_l4_modal.py"

EXPECTED_MODELS_BY_MARKET = {
    "csi300": ("m03_4e908173", "m04_17dbfe28", "m06_b034ac46", "m01_59b93929", "baseline"),
    "csi500": ("m10_raw10d_c98856b4", "m09_raw10d_baseline", "c98856b4", "m03_4e908173", "baseline"),
}

EXPECTED_STRATEGIES_BY_MARKET = {
    "csi300": (
        ("m03_4e908173", "top8_drop1", "challenger"),
        ("m04_17dbfe28", "top5_drop1", "challenger"),
        ("m06_b034ac46", "top8_drop1", "challenger"),
        ("m01_59b93929", "top8_drop1", "challenger"),
        ("m01_59b93929", "top10_drop1", "challenger"),
        ("m06_b034ac46", "top10_drop1", "challenger"),
        ("m03_4e908173", "top10_drop1", "challenger"),
        ("baseline", "top10_drop1", "control_top10"),
        ("baseline", "top20_drop2", "official_baseline"),
    ),
    "csi500": (
        ("m10_raw10d_c98856b4", "top10_drop1", "challenger"),
        ("m10_raw10d_c98856b4", "top15_drop2", "challenger"),
        ("m10_raw10d_c98856b4", "top15_drop1", "challenger"),
        ("m10_raw10d_c98856b4", "top20_drop2", "challenger"),
        ("m09_raw10d_baseline", "top30_drop2", "challenger"),
        ("m09_raw10d_baseline", "top20_drop1", "challenger"),
        ("c98856b4", "top30_drop3", "challenger"),
        ("m03_4e908173", "top15_drop1", "challenger"),
        ("baseline", "top20_drop2", "official_baseline"),
    ),
}

EXPECTED_PRIOR_SPLIT = {
    "csi300": {"stage0_p0": 4, "stage1_m1": 6, "stage2_l1": 8},
    "csi500": {"stage0_p0": 2, "stage1_m1": 4, "stage2_l1": 12},
}


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


def _identity(job):
    return {
        "run_id": job["run_id"],
        "code_sha": job["code_sha"],
        "provider_fingerprint": job["provider_fingerprint"],
        "source_snapshot_sha256": job["source_snapshot_sha256"],
        "quote_sha256": job["quote_sha256"],
    }


def _bound_fit_job(calendar: list[str] | None = None):
    calendar = calendar or _synthetic_calendar()
    jobs = core.build_stage3_fit_jobs(calendar, "stage3_l4_20261016_a1", "a" * 40)
    raw = next(j for j in jobs if (j["market"], j["model_key"], j["phase"]) == ("csi300", "m03_4e908173", 4))
    return core.bind_stage3_job(
        raw,
        provider_fingerprint="f" * 64,
        source_snapshot_sha256="e" * 64,
        quote_sha256="d" * 64,
    )


def _bound_cell_job():
    jobs = core.build_stage3_cell_jobs("stage3_l4_20261016_a1", "a" * 40)
    raw = next(
        j
        for j in jobs
        if (j["market"], j["model_key"], j["combo_key"], j["phase"]) == ("csi300", "m03_4e908173", "top8_drop1", 4)
    )
    return core.bind_stage3_job(
        raw,
        provider_fingerprint="f" * 64,
        source_snapshot_sha256="e" * 64,
        quote_sha256="d" * 64,
    )


class _DummyParquet:
    def __init__(self, data: bytes):
        self.data = data

    def to_parquet(self, path):
        Path(path).write_bytes(self.data)


def _fake_metrics(rel, sharpe=1.0, ir=0.5, mdd=-0.1, total=0.2, cagr=0.1, turnover=0.2):
    return {
        "n_days": 424,
        "strategy_total_return": total,
        "strategy_cagr": cagr,
        "benchmark_cagr": 0.05,
        "relative_excess_cagr": rel,
        "sharpe": sharpe,
        "information_ratio": ir,
        "strategy_max_drawdown": mdd,
        "mean_turnover": turnover,
        "total_cost_sum": 0.05,
        "account_return_max_error": 1e-12,
    }


def _fake_unit(market, model_key, combo_key, phase, rel, stressed10=0.1, stressed20=0.09):
    topk, n_drop = core._combo_specs()[combo_key]
    return {
        "market": market,
        "benchmark": core.MARKETS[market]["benchmark"],
        "model_key": model_key,
        "candidate_id": core.ALL_L4_MODELS[model_key]["candidate_id"],
        "label_horizon": int(core.ALL_L4_MODELS[model_key]["label_horizon"]),
        "source_stage": core.ALL_L4_MODELS[model_key]["source_stage"],
        "unit_source": "stage3_l4_new",
        "combo_key": combo_key,
        "topk": topk,
        "n_drop": n_drop,
        "role": core.strategy_role(market, model_key, combo_key),
        "phase": phase,
        "metrics": _fake_metrics(rel),
        "stressed": {
            "10bp": {"strategy_total_return": stressed10},
            "20bp": {"strategy_total_return": stressed20},
        },
    }


# ---------------------------------------------------------------------------
# Rosters, model merge and phase constants.
# ---------------------------------------------------------------------------


def test_stage3_rosters_plan_verbatim():
    assert core.STAGE3_MODELS_BY_MARKET == EXPECTED_MODELS_BY_MARKET
    assert core.STAGE3_STRATEGIES_BY_MARKET == EXPECTED_STRATEGIES_BY_MARKET
    for market in ("csi300", "csi500"):
        roles = [role for _, _, role in core.STAGE3_STRATEGIES_BY_MARKET[market]]
        assert roles.count("official_baseline") == 1
        assert roles.count("challenger") == (7 if market == "csi300" else 8)
        assert roles.count("control_top10") == (1 if market == "csi300" else 0)
    assert core.OFFICIAL_BASELINE == ("baseline", "top20_drop2")
    assert core.ROLES == ("challenger", "control_top10", "official_baseline")


def test_stage3_all_l4_models_merge_and_sha_recompute():
    assert set(core.ALL_L4_MODELS) == set(p0.MODELS) | set(core.STAGE1_NEW_MODELS)
    assert len(core.ALL_L4_MODELS) == 12
    for key in ("baseline", "c98856b4"):
        spec = core.ALL_L4_MODELS[key]
        assert spec["source_stage"] == "stage0_p0" and spec["label_horizon"] == 20
    for key, spec in core.STAGE1_NEW_MODELS.items():
        assert core.ALL_L4_MODELS[key]["source_stage"] == "stage1_m1"
        assert core.ALL_L4_MODELS[key]["label_horizon"] == spec["label_horizon"]
    for key, spec in core.ALL_L4_MODELS.items():
        assert _model_sha(spec["model_params"]) == spec["model_sha256"], key


def test_stage3_phase_constants_and_artifacts():
    assert core.ALL_5_PHASES == (0, 4, 6, 10, 15)
    assert core.PRIOR_PHASES == (0, 10)
    assert core.NEW_PHASES == (4, 6, 15)
    assert core.EXPECTED_FITS == 660
    assert core.EXPECTED_L4_CELLS == 54
    assert core.EXPECTED_PRIOR_UNITS == 36
    assert core.EXPECTED_FIVE_PHASE_UNITS == 90
    assert core.STAGE2_RUN_ID == "stage2_l1_20261016_a1"
    assert core.STAGE2_CODE_SHA == "81d40e265d0fcdf980dcf3fd1b2673c6145af23f"
    assert core.STAGE2_PREFIX.endswith("stage2_l1_20261016_a1/81d40e265d0f")
    assert core.STAGE3_ARTIFACTS == {
        "l4retrain": ("prediction.parquet", "checkpoint.txt"),
        "l4cell": ("fixed_report.parquet", "decisions.json"),
    }
    assert "signal.parquet" not in core.STAGE3_ARTIFACTS["l4cell"]


# ---------------------------------------------------------------------------
# Roster geometry.
# ---------------------------------------------------------------------------


def test_stage3_fit_roster_660_geometry():
    calendar = _synthetic_calendar()
    jobs = core.build_stage3_fit_jobs(calendar, "stage3_l4_20261016_a1", "a" * 40)
    assert len(jobs) == 660
    assert len({core.job_id(j) for j in jobs}) == 660
    assert len({stable_sha(j) for j in jobs}) == 660
    assert {j["phase"] for j in jobs} == {4, 6, 15}
    for market, models in core.STAGE3_MODELS_BY_MARKET.items():
        for model_key in models:
            spec = core.ALL_L4_MODELS[model_key]
            for phase in (4, 6, 15):
                group = [j for j in jobs if (j["market"], j["model_key"], j["phase"]) == (market, model_key, phase)]
                assert len(group) == 22
                assert group[0]["retrain_asof"] == min(j["retrain_asof"] for j in group)
                assert group[-1]["signal_end"] == calendar[calendar.index("2026-09-30") - 1]
                for job in group:
                    assert job["kind"] == "l4retrain"
                    assert job["benchmark"] == core.MARKETS[market]["benchmark"]
                    assert job["candidate_id"] == spec["candidate_id"]
                    assert job["label_horizon"] == spec["label_horizon"]
                    assert job["source_stage"] == spec["source_stage"]
                    assert f"/h{job['label_horizon']}/phase{phase:02d}/{job['retrain_asof']}" in core.job_id(job)
    assert all(j["kind"] == "l4retrain" for j in jobs)


def test_stage3_cell_roster_54_and_writer_guard():
    jobs = core.build_stage3_cell_jobs("stage3_l4_20261016_a1", "a" * 40)
    assert len(jobs) == 54
    assert len({core.job_id(j) for j in jobs}) == 54
    assert len({stable_sha(j) for j in jobs}) == 54
    combos = core._combo_specs()
    for market, strategies in core.STAGE3_STRATEGIES_BY_MARKET.items():
        assert len([j for j in jobs if j["market"] == market]) == 27
        writers = {}
        for model_key, combo_key, role in strategies:
            for phase in (4, 6, 15):
                job = next(
                    j
                    for j in jobs
                    if (j["market"], j["model_key"], j["combo_key"], j["phase"])
                    == (market, model_key, combo_key, phase)
                )
                assert job["kind"] == "l4cell"
                assert job["role"] == role
                assert core.strategy_role(market, model_key, combo_key) == role
                assert (job["topk"], job["n_drop"]) == combos[combo_key]
                assert job["canonical_signal_writer"] == core.is_canonical_signal_writer(market, model_key, combo_key)
                writers[(model_key, combo_key)] = job["canonical_signal_writer"]
        for model_key in core.STAGE3_MODELS_BY_MARKET[market]:
            canonical = [combo for (m, combo), flag in writers.items() if m == model_key and flag]
            assert len(canonical) == 1
            assert canonical[0] == core.canonical_signal_combo(market, model_key)
            assert canonical[0] == next(
                combo for m, combo, _ in core.STAGE3_STRATEGIES_BY_MARKET[market] if m == model_key
            )


# ---------------------------------------------------------------------------
# Fail-closed guards.
# ---------------------------------------------------------------------------


def test_stage3_prefix_and_lookup_fail_closed():
    assert core.prefix_for("stage3_l4_20261016_a1", "b" * 40).endswith("stage3_l4_20261016_a1/bbbbbbbbbbbb")
    for bad in ("stage2_l1_20261016_a1", "stage3_l4_x", "../stage3_l4_abc"):
        with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
            core.prefix_for(bad, "b" * 40)
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.prefix_for("stage3_l4_20261016_a1", "b" * 12)
    assert core.signal_relpath("csi300", "m03_4e908173", 4) == "signals/csi300/m03_4e908173/phase04/signal.parquet"
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.signal_relpath("csi300", "m03_4e908173", 0)
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.signal_relpath("csi300", "m09_raw10d_baseline", 4)
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.strategy_role("csi300", "m03_4e908173", "top20_drop1")
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.canonical_signal_combo("csi500", "unknown_model")
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.is_canonical_signal_writer("csi300", "m03_4e908173", "nope")
    calendar = _synthetic_calendar()
    jobs = core.build_stage3_fit_jobs(calendar, "stage3_l4_20261016_a1", "a" * 40)
    phase6_first = core.build_phase_jobs(calendar, 6)[0]["retrain_asof"]
    first = core.fit_job_for_slice(jobs, "csi300", "m03_4e908173", 6, phase6_first)
    assert (first["market"], first["model_key"], first["phase"]) == ("csi300", "m03_4e908173", 6)
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.fit_job_for_slice(jobs, "csi300", "m03_4e908173", 5, phase6_first)


def test_stage3_receipt_roundtrip_fit_and_tamper(tmp_path):
    job = _bound_fit_job()
    details = {
        "retrain_asof": job["retrain_asof"],
        "signal_end": job["signal_end"],
        "fit_performed": True,
        "best_iteration": 42,
    }
    artifacts = {"prediction.parquet": _DummyParquet(b"pred"), "checkpoint.txt": "tree\n" * 50}
    row = core.publish_receipt(tmp_path, job, _identity(job), artifacts, details)
    assert row["status"] == "PASS" and row["fit_performed"] is True
    checked = core.checked_receipt(tmp_path, job, _identity(job))
    assert checked["job_sha256"] == stable_sha(job)
    assert checked["artifact_sha256"]["prediction.parquet"] == hashlib.sha256(b"pred").hexdigest()
    # fit_performed=False can never be published.
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.publish_receipt(
            tmp_path / "second",
            job,
            _identity(job),
            {"prediction.parquet": _DummyParquet(b"p"), "checkpoint.txt": "x"},
            {"fit_performed": False},
        )
    # Byte tamper is rejected.
    (core.receipt_path(tmp_path, job) / "checkpoint.txt").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.checked_receipt(tmp_path, job, _identity(job))


def test_stage3_receipt_l4cell_pins_prediction_shas(tmp_path):
    job = _bound_cell_job()
    shas = [f"{index:064x}" for index in range(22)]
    details = {
        "combo_key": job["combo_key"],
        "phase": job["phase"],
        "signal_relpath": core.signal_relpath(job["market"], job["model_key"], job["phase"]),
        "signal_written": True,
        "source_prediction_shas": shas,
        "fit_performed": False,
    }
    artifacts = {"fixed_report.parquet": _DummyParquet(b"report"), "decisions.json": [{"d": 1}]}
    row = core.publish_receipt(tmp_path, job, _identity(job), artifacts, details)
    assert row["signal_written"] is True and len(row["source_prediction_shas"]) == 22
    checked = core.checked_receipt(tmp_path, job, _identity(job))
    assert checked["source_prediction_shas"] == shas
    # Wrong relpath is rejected at publication.
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.publish_receipt(
            tmp_path / "second",
            job,
            _identity(job),
            artifacts,
            {**details, "signal_relpath": "signals/csi300/m03_4e908173/phase06/signal.parquet"},
        )
    # Only 21 prediction SHAs is rejected at publication.
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.publish_receipt(
            tmp_path / "third",
            job,
            _identity(job),
            artifacts,
            {**details, "source_prediction_shas": shas[:21]},
        )
    # A receipt with a non-boolean signal_written flag is rejected on read.
    rec_file = core.receipt_path(tmp_path, job) / "receipt.json"
    record = json.loads(rec_file.read_text(encoding="utf-8"))
    record["signal_written"] = "yes"
    rec_file.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.checked_receipt(tmp_path, job, _identity(job))


# ---------------------------------------------------------------------------
# 424-day signal assembly from 22 verified slice fits.
# ---------------------------------------------------------------------------


def _fabricate_slice_fits(root: Path, job: dict, drop_day: str | None = None, dup_day: str | None = None) -> list[dict]:
    """Publish all 22 real-parquet slice receipts for one (market, model, phase)."""
    calendar = _synthetic_calendar()
    fit_jobs = [
        core.bind_stage3_job(
            raw,
            provider_fingerprint=job["provider_fingerprint"],
            source_snapshot_sha256=job["source_snapshot_sha256"],
            quote_sha256=job["quote_sha256"],
        )
        for raw in core.build_stage3_fit_jobs(calendar, job["run_id"], job["code_sha"])
    ]
    identity = _identity(job)
    shas = []
    for slice_job in core.build_phase_jobs(calendar, job["phase"]):
        fit_job = core.fit_job_for_slice(
            fit_jobs, job["market"], job["model_key"], job["phase"], slice_job["retrain_asof"]
        )
        days = calendar[calendar.index(slice_job["retrain_asof"]) : calendar.index(slice_job["signal_end"]) + 1]
        if drop_day is not None and drop_day in days:
            days = [day for day in days if day != drop_day]
        if dup_day is not None and dup_day in days:
            days = days + [dup_day]
        index = pd.MultiIndex.from_tuples(
            [(day, inst) for day in days for inst in ("SH600000", "SH600004")], names=["datetime", "instrument"]
        )
        frame = pd.DataFrame({"score": [0.01 * idx for idx in range(len(index))]}, index=index)
        row = core.publish_receipt(
            root,
            fit_job,
            identity,
            {"prediction.parquet": frame, "checkpoint.txt": "tree\n" * 50},
            {"retrain_asof": fit_job["retrain_asof"], "signal_end": fit_job["signal_end"], "fit_performed": True},
        )
        shas.append(row["artifact_sha256"]["prediction.parquet"])
    return shas


def test_stage3_assemble_phase_signal_22_slices(tmp_path):
    job = _bound_cell_job()
    shas = _fabricate_slice_fits(tmp_path, job)
    calendar = _synthetic_calendar()
    fit_jobs = [
        core.bind_stage3_job(
            raw,
            provider_fingerprint=job["provider_fingerprint"],
            source_snapshot_sha256=job["source_snapshot_sha256"],
            quote_sha256=job["quote_sha256"],
        )
        for raw in core.build_stage3_fit_jobs(calendar, job["run_id"], job["code_sha"])
    ]
    assembled = core.assemble_phase_signal(tmp_path, calendar, fit_jobs, job, _identity(job))
    signal = assembled["signal"]
    assert len(signal) == 424 * 2
    assert list(signal.columns) == ["score"]
    assert signal.index.is_unique
    assert assembled["source_prediction_shas"] == shas
    assert len(assembled["slice_job_shas"]) == 22
    days = sorted({str(v)[:10] for v in signal.index.get_level_values(0)})
    first = calendar.index(core.RESERVED_SIGNAL_ANCHOR)
    last = calendar.index(core.RESERVED_EXECUTION_END)
    assert days == calendar[first:last]
    # Missing receipt fails closed.
    missing = core.receipt_path(tmp_path, fit_jobs[0])
    backup = (missing / "receipt.json").read_bytes()
    (missing / "receipt.json").unlink()
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.assemble_phase_signal(tmp_path, calendar, fit_jobs, job, _identity(job))
    (missing / "receipt.json").write_bytes(backup)
    assert len(core.assemble_phase_signal(tmp_path, calendar, fit_jobs, job, _identity(job))["signal"]) == 424 * 2


def test_stage3_assemble_rejects_gap_and_duplicate(tmp_path):
    job = _bound_cell_job()
    calendar = _synthetic_calendar()
    anchor_i = calendar.index(core.RESERVED_SIGNAL_ANCHOR)
    _fabricate_slice_fits(tmp_path, job, drop_day=calendar[anchor_i + 10])
    fit_jobs = [
        core.bind_stage3_job(
            raw,
            provider_fingerprint=job["provider_fingerprint"],
            source_snapshot_sha256=job["source_snapshot_sha256"],
            quote_sha256=job["quote_sha256"],
        )
        for raw in core.build_stage3_fit_jobs(calendar, job["run_id"], job["code_sha"])
    ]
    with pytest.raises(ValueError, match="STOP_STAGE3_SIGNAL_GAP"):
        core.assemble_phase_signal(tmp_path, calendar, fit_jobs, job, _identity(job))
    other = tmp_path / "duplicate"
    _fabricate_slice_fits(other, job, dup_day=calendar[anchor_i + 10])
    with pytest.raises(ValueError, match="STOP_STAGE3_SIGNAL_DUPLICATE_DAY"):
        core.assemble_phase_signal(other, calendar, fit_jobs, job, _identity(job))


# ---------------------------------------------------------------------------
# Verified 36 prior phase-(0, 10) units.
# ---------------------------------------------------------------------------


def _fabricate_stage2_prior(root: Path) -> dict:
    combos = core._combo_specs()
    required = {}
    for market in ("csi300", "csi500"):
        for model_key, combo_key, _role in core.STAGE3_STRATEGIES_BY_MARKET[market]:
            for phase in core.PRIOR_PHASES:
                required[(market, model_key, combo_key, phase)] = core.prior_unit_source(market, model_key, combo_key)
    assert len(required) == 36
    cells = []
    index = 0
    for market in ("csi300", "csi500"):
        for model_key, spec in core.ALL_L4_MODELS.items():
            for combo_key, (topk, n_drop) in combos.items():
                for phase in core.PRIOR_PHASES:
                    key = (market, model_key, combo_key, phase)
                    index += 1
                    job_sha = stable_sha({"key": key, "index": index})
                    cell = {
                        "market": market,
                        "benchmark": core.MARKETS[market]["benchmark"],
                        "model_key": model_key,
                        "candidate_id": spec["candidate_id"],
                        "combo_key": combo_key,
                        "topk": topk,
                        "n_drop": n_drop,
                        "phase": phase,
                        "metrics": _fake_metrics(0.001 * index),
                        "stressed": {
                            "10bp": {"strategy_total_return": 0.1},
                            "20bp": {"strategy_total_return": 0.09},
                        },
                        "job_sha256": job_sha,
                        "source_signal_sha256": "",
                        "artifact_sha256": {"fixed_report.parquet": "f" * 64},
                    }
                    if key in required:
                        source = required[key]
                        if source == "stage2_l1":
                            prefix, kind = core.STAGE2_PREFIX, "l1cell"
                            run_id, code_sha = core.STAGE2_RUN_ID, core.STAGE2_CODE_SHA
                            names = ("fixed_report.parquet", "decisions.json")
                        elif source == "stage0_p0":
                            prefix, kind = core.STAGE0_PREFIX, "cell"
                            run_id, code_sha = core.STAGE0_RUN_ID, core.STAGE0_CODE_SHA
                            names = ("fixed_report.parquet", "decisions.json", "signal.parquet")
                        else:
                            prefix, kind = core.STAGE1_PREFIX, "cell"
                            run_id, code_sha = core.STAGE1_RUN_ID, core.STAGE1_CODE_SHA
                            names = ("fixed_report.parquet", "decisions.json", "signal.parquet")
                        cdir = (
                            root
                            / prefix
                            / "cells"
                            / kind
                            / market
                            / model_key
                            / spec["candidate_id"][:8]
                            / combo_key
                            / f"phase{phase:02d}"
                        )
                        cdir.mkdir(parents=True, exist_ok=True)
                        hashes = {}
                        for name in names:
                            blob = f"{name}-{index}".encode()
                            (cdir / name).write_bytes(blob)
                            hashes[name] = hashlib.sha256(blob).hexdigest()
                        record = {
                            "status": "PASS",
                            "kind": kind,
                            "run_id": run_id,
                            "code_sha": code_sha,
                            "provider_fingerprint": "f" * 64,
                            "source_snapshot_sha256": "e" * 64,
                            "quote_sha256": "d" * 64,
                            "job_sha256": job_sha,
                            "artifact_sha256": hashes,
                        }
                        (cdir / "receipt.json").write_text(json.dumps(record), encoding="utf-8")
                        cell["artifact_sha256"] = hashes
                        cell["source_signal_sha256"] = hashes.get("signal.parquet", "")
                    cells.append(cell)
    assert len(cells) == 480
    summary = {
        "status": "PASS",
        "run_id": core.STAGE2_RUN_ID,
        "code_sha": core.STAGE2_CODE_SHA,
        "completed_l1cells": 384,
        "prior_cells": 96,
        "total_cells": 480,
        "cells": cells,
    }
    path = root / core.STAGE2_PREFIX / "stage2_l1_summary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary), encoding="utf-8")
    return summary


def test_stage3_prior_units_source_split(tmp_path):
    _fabricate_stage2_prior(tmp_path)
    units = core.load_stage2_prior_units(tmp_path)
    assert len(units) == 36
    assert len({(u["market"], u["model_key"], u["combo_key"], u["phase"]) for u in units}) == 36
    for market in ("csi300", "csi500"):
        market_units = [u for u in units if u["market"] == market]
        assert len(market_units) == 18
        counts = {}
        for unit in market_units:
            counts[unit["unit_source"]] = counts.get(unit["unit_source"], 0) + 1
        assert counts == EXPECTED_PRIOR_SPLIT[market]
    for unit in units:
        assert unit["label_horizon"] == core.ALL_L4_MODELS[unit["model_key"]]["label_horizon"]
        assert unit["source_stage"] == core.ALL_L4_MODELS[unit["model_key"]]["source_stage"]
        assert unit["receipt_sha256"]


def test_stage3_prior_units_reject_byte_and_summary_drift(tmp_path):
    summary = _fabricate_stage2_prior(tmp_path)
    target = (
        tmp_path
        / core.STAGE0_PREFIX
        / "cells"
        / "cell"
        / "csi300"
        / "baseline"
        / "23b92de0"
        / "top10_drop1"
        / "phase00"
        / "signal.parquet"
    )
    original = target.read_bytes()
    target.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.load_stage2_prior_units(tmp_path)
    target.write_bytes(original)
    assert len(core.load_stage2_prior_units(tmp_path)) == 36
    summary["completed_l1cells"] = 383
    (tmp_path / core.STAGE2_PREFIX / "stage2_l1_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.load_stage2_prior_units(tmp_path)


# ---------------------------------------------------------------------------
# Five-phase ranking and CSV renderers.
# ---------------------------------------------------------------------------


def _synthetic_five_phase_units(market: str = "csi300") -> list[dict]:
    units = []
    for pair_index, (model_key, combo_key, _role) in enumerate(core.STAGE3_STRATEGIES_BY_MARKET[market]):
        for phase in core.ALL_5_PHASES:
            rel = 0.01 * (9 - pair_index) + 0.001 * phase
            units.append(
                _fake_unit(
                    market,
                    model_key,
                    combo_key,
                    phase,
                    rel,
                    stressed10=0.1 + 0.01 * (9 - pair_index) - 0.001 * phase,
                    stressed20=0.09 + 0.01 * (9 - pair_index) - 0.001 * phase,
                )
            )
    return units


def test_stage3_rank_five_phase_ordering_and_metrics():
    units = _synthetic_five_phase_units()
    assert len(units) == 45
    rows = core.rank_five_phase_strategies(units)
    assert len(rows) == 9
    assert [row["rank"] for row in rows] == list(range(1, 10))
    worsts = [row["worst_relative_excess_cagr"] for row in rows]
    assert worsts == sorted(worsts, reverse=True)
    assert (rows[0]["model_key"], rows[0]["combo_key"]) == ("m03_4e908173", "top8_drop1")
    assert rows[0]["role"] == "challenger"
    top = rows[0]
    assert top["worst_relative_excess_cagr"] == pytest.approx(0.09)
    assert top["median_relative_excess_cagr"] == pytest.approx(0.096)
    assert top["mean_relative_excess_cagr"] == pytest.approx(sum(0.09 + 0.001 * p for p in core.ALL_5_PHASES) / 5)
    assert top["positive_excess_phases"] == 5
    assert top["beats_official_baseline_phases"] == 5
    assert top["worst_stressed10bp_excess_vs_baseline"] == pytest.approx(0.08)
    baseline = next(row for row in rows if (row["model_key"], row["combo_key"]) == core.OFFICIAL_BASELINE)
    assert baseline["role"] == "official_baseline"
    assert baseline["beats_official_baseline_phases"] == 0
    assert set(top) >= {"phase00_relative_excess_cagr", "phase15_relative_excess_cagr"}
    assert top["phase00_relative_excess_cagr"] == pytest.approx(0.09)
    assert top["phase15_relative_excess_cagr"] == pytest.approx(0.105)
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.rank_five_phase_strategies(units[:44])
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.rank_five_phase_strategies(units + units[:1])


def _synthetic_cells_90() -> list[dict]:
    cells = []
    for market in ("csi300", "csi500"):
        for unit in _synthetic_five_phase_units(market):
            unit = dict(unit)
            unit["artifact_sha256"] = {"fixed_report.parquet": "a" * 64}
            unit["job_sha256"] = stable_sha({"m": market, "u": unit["model_key"], "p": unit["phase"]})
            cells.append(unit)
    return cells


def test_stage3_renderers_scoreboard_90_and_rankings_18(tmp_path):
    cells = _synthetic_cells_90()
    assert len(cells) == 90
    rankings = {
        market: core.rank_five_phase_strategies([c for c in cells if c["market"] == market])
        for market in ("csi300", "csi500")
    }
    summary = {"cells": cells, "rankings": rankings}
    scoreboard = core.render_stage3_scoreboard(summary, tmp_path / "scoreboard.csv")
    rows = scoreboard.read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 91
    assert rows[0].split(",")[:7] == [
        "market",
        "benchmark",
        "model_key",
        "candidate_id",
        "label_horizon",
        "source_stage",
        "unit_source",
    ]
    assert rows[0].endswith("job_sha256")
    assert rows[0].count("role") == 1
    ranking_csv = core.render_stage3_rankings(summary, tmp_path / "rankings.csv")
    rrows = ranking_csv.read_text(encoding="utf-8").strip().splitlines()
    assert len(rrows) == 19
    assert rrows[0].split(",")[:5] == ["market", "rank", "model_key", "combo_key", "role"]
    assert rrows[0].endswith("worst_stressed20bp_excess_vs_baseline")
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.render_stage3_scoreboard({"cells": cells[:89]}, tmp_path / "bad.csv")
    with pytest.raises(ValueError, match="LAYERED_SEARCH_BLOCKED"):
        core.render_stage3_rankings({"rankings": {"csi300": rankings["csi300"]}}, tmp_path / "bad2.csv")


# ---------------------------------------------------------------------------
# Static Modal trap compliance (real fits only through fit_remote).
# ---------------------------------------------------------------------------


def test_stage3_modal_imports_all_resolve_in_core():
    import ast
    import builtins

    src = MODAL_SRC.read_text(encoding="utf-8")
    match = _re.search(r"from stage3_l4_core import \((.*?)\)", src, _re.DOTALL)
    assert match, "core import block missing"
    names = []
    for line in match.group(1).splitlines():
        name = line.split("#")[0].strip().rstrip(",").strip()
        if name:
            names.append(name)
    assert len(names) > 25
    for name in names:
        assert hasattr(core, name), f"core missing Modal import: {name}"
    for path in (Path(core.__file__), MODAL_SRC):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        defined = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__package__"}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    defined.add(alias.asname or alias.name.split(".")[0])
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defined.add(node.name)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                for arg in node.args.args + node.args.kwonlyargs:
                    defined.add(arg.arg)
                if node.args.vararg:
                    defined.add(node.args.vararg.arg)
                if node.args.kwarg:
                    defined.add(node.args.kwarg.arg)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Param)):
                defined.add(node.id)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                defined.add(node.name)
        missing = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id not in defined
        }
        assert not missing, f"undefined names in {path.name}: {missing}"


def test_stage3_modal_traps_static():
    src = MODAL_SRC.read_text(encoding="utf-8")
    assert src.count("model.fit(dataset)") == 1
    assert src.count(".fit(") == 1
    assert "--detach" not in src
    assert '"SH000852"' not in src
    assert '"signal.parquet":' not in src
    assert "load_frozen_result" not in src
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
    assert "version=2" in src
    assert "qlib-csi300-csi500-stage3-l4" in src
    assert "cpu=8, memory=16384, timeout=3600, retries=1" in src
    assert "cpu=2, memory=4096, timeout=1800, retries=0" in src
    assert "STAGE3_L4RETRAIN_WAVE" in src and "STAGE3_L4CELL_WAVE" in src
    assert "STAGE3_SMOKE_PASS 2/2 new-phase fits verified (phase4 first slices)" in src
    assert "STAGE3_L4_COMPLETE 660/660 new-phase fits 54/54 new cells 90/90 five-phase units PASS" in src
    assert "STAGE3_SAFE_PAUSE" in src
    assert "STOP_STAGE3_SHARED_SIGNAL_TEMP" in src and "STOP_STAGE3_SHARED_SIGNAL_DRIFT" in src
    assert "STOP_STAGE3_HORIZON" in src
    assert "purge_cfg_splits(config, calendar, horizon=horizon)" in src
    assert '"fit_performed": True' in src
    assert 'startswith("fits/")' in src
    assert "require(len(selected) == 194" in src
    assert "ignored_fits >= 1980" in src
    assert "ThreadPoolExecutor(max_workers=16)" in src
    assert '"m03_4e908173"' in src and '"m10_raw10d_c98856b4"' in src
    assert "max_workers: int = 66" in src and "max_fit_waves: int = 0" in src
    assert "STOP_STAGE3_SMOKE_WORKERS" in src
    assert "row is None" in src  # verified-receipt resume path
