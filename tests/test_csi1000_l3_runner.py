"""L3 structural-search tests: calendars, labels, budgets, gates. No Modal, no fitting."""

import ast
from pathlib import Path

import pytest

from audit import csi1000_l3_core as l3


def _calendar(n_days=3000):
    import datetime

    end = datetime.date(2026, 9, 30)
    assert end.weekday() < 5
    days, day = [], end
    while len(days) < n_days:
        if day.weekday() < 5 and day != datetime.date(2025, 1, 1):
            days.append(day.isoformat())
        day -= datetime.timedelta(days=1)
    days.reverse()
    assert days[-1] == "2026-09-30"
    if n_days > 600:
        assert "2024-12-31" in days
        assert days[days.index("2024-12-31") + 1] == "2025-01-02"
    return days


def test_frozen_identities_match_production_lineage():
    import csi1000_stage_b_core as stage_b
    from csi1000_tuner_core import build_candidate_spec

    assert l3.ANCHOR_CANDIDATE == "23b92de05cf36c82998de684d0fbf64d81ee54490d755bd3cf96311c00286785"
    assert l3.ANCHOR_MODEL_PARAMS == {
        "learning_rate": 0.1,
        "colsample_bytree": 0.9,
        "lambda_l1": 205.6999,
        "lambda_l2": 580.9768,
        "max_depth": 8,
        "num_leaves": 250,
        "min_data_in_leaf": 20,
    }
    assert l3.TRAIN_START == stage_b.TRAIN_START
    assert l3.VALIDATION_SESSIONS == stage_b.VALIDATION_SESSIONS
    assert l3.EXECUTION_START == stage_b.RESERVED_EXECUTION_START
    assert l3.EXECUTION_END == stage_b.RESERVED_EXECUTION_END
    assert l3.SIGNAL_ANCHOR == stage_b.RESERVED_SIGNAL_ANCHOR
    built = build_candidate_spec(dict(l3.ANCHOR_MODEL_PARAMS))
    assert built["candidate_id"] == l3.ANCHOR_CANDIDATE and built["is_baseline"] is True
    assert l3.variant_key(l3.STAGE1_VARIANTS[0], 5) == "l3_r20_f10_p05_k20_d2"
    with pytest.raises(ValueError):
        l3.variant_key(l3.STAGE1_VARIANTS[0], 10)


def test_label_specs():
    assert l3.label_spec(10)["expression"] == "Ref($close, -10)/$close - 1"
    assert l3.label_spec(20)["expression"] == "Ref($close, -20)/$close - 1"
    assert l3.label_spec(40)["expression"] == "Ref($close, -40)/$close - 1"
    with pytest.raises(ValueError):
        l3.label_spec(5)
    with pytest.raises(ValueError):
        l3.label_spec(60)


def test_freq20_schedule_matches_frozen_geometry():
    import csi1000_stage_b_core as stage_b

    calendar = _calendar(2600)
    for phase in (0, 4, 6, 10, 15):
        mine = l3.build_l3_retrain_schedule(calendar, frequency=20, phase=phase)
        frozen = stage_b.build_phase_jobs(calendar, phase)
        assert [(p["retrain_asof"], p["signal_end"]) for p in mine] == [
            (p["retrain_asof"], p["signal_end"]) for p in frozen
        ]


def test_schedule_steps_and_offsets():
    calendar = _calendar()
    ten = l3.build_l3_retrain_schedule(calendar, frequency=10, phase=5)
    assert len(ten) > 40
    idx = [calendar.index(p["retrain_asof"]) for p in ten]
    assert all(b - a == 10 for a, b in zip(idx, idx[1:]))
    forty = l3.build_l3_retrain_schedule(calendar, frequency=40, phase=20)
    assert len(forty) > 9
    assert all(calendar.index(p["signal_end"]) - calendar.index(p["retrain_asof"]) == 39 for p in forty[:-1])
    with pytest.raises(ValueError):
        l3.build_l3_retrain_schedule(calendar, frequency=10, phase=10)
    with pytest.raises(ValueError):
        l3.build_l3_retrain_schedule(calendar, frequency=7, phase=0)
    with pytest.raises(ValueError):
        l3.build_l3_retrain_schedule(["2025-01-01"], frequency=20, phase=0)


def test_training_slice_maturity():
    calendar = _calendar()
    window = l3.training_slice(calendar, asof="2025-03-06", horizon=20)
    assert window["train_start"] == "2016-01-01"
    assert window["valid_start"] < window["valid_end"] < "2025-03-06"
    assert calendar.index(window["valid_end"]) == calendar.index("2025-03-06") - 20 - 1
    assert calendar.index(window["train_end"]) < calendar.index(window["valid_start"])
    w10 = l3.training_slice(calendar, asof="2025-03-06", horizon=10)
    w40 = l3.training_slice(calendar, asof="2025-03-06", horizon=40)
    assert w10["train_end"] >= window["train_end"] >= w40["train_end"]
    short = _calendar(300)
    with pytest.raises(ValueError, match="insufficient"):
        l3.training_slice(short, asof=short[5], horizon=40)
    with pytest.raises(ValueError):
        l3.training_slice(calendar, asof="not-a-date", horizon=20)


def test_purge_plumbing_per_horizon():
    import copy

    calendar = _calendar()
    base = {
        "task": {
            "dataset": {
                "kwargs": {
                    "segments": {
                        "train": ["2016-01-01", "2024-01-01"],
                        "valid": ["2024-01-02", "2024-06-01"],
                        "test": ["2025-01-02", "2025-02-01"],
                    },
                    "handler": {
                        "kwargs": {
                            "label": ["Ref($close, -20)/$close - 1"],
                            "fit_start_time": "2016-01-01",
                            "fit_end_time": "2024-01-01",
                        }
                    },
                }
            }
        }
    }
    from qlib_audit_fixes import purge_cfg_splits

    ends = {}
    for horizon in (10, 20, 40):
        cfg = copy.deepcopy(base)
        purge_cfg_splits(cfg, calendar, horizon=horizon)
        segs = cfg["task"]["dataset"]["kwargs"]["segments"]
        assert segs["test"] == ["2025-01-02", "2025-02-01"]
        ends[horizon] = segs["train"][1]
    assert ends[10] > ends[20] > ends[40]


def test_variant_fit_math_within_cap():
    calendar = _calendar()
    stage1 = sum(
        l3.estimate_variant_fits(l3.build_l3_retrain_schedule(calendar, frequency=v["retrain_frequency"], phase=p))
        for v in l3.STAGE1_VARIANTS
        for p in v["screen_phases"]
    )
    stage2 = sum(
        l3.estimate_variant_fits(l3.build_l3_retrain_schedule(calendar, frequency=v["retrain_frequency"], phase=p))
        for v in l3.STAGE2_VARIANTS
        for p in v["screen_phases"]
    )
    assert 0 < stage1 < 250 and 0 < stage2 < 250 and stage1 + stage2 + 2 <= 250
    assert l3.check_l3_fit_scope(stage1 + stage2 + 2, 0) == "OK_CONTINUE"
    assert l3.check_l3_fit_scope(251, 0) == "STOP_EXPERIMENT_SCOPE"
    assert l3.check_l3_fit_scope(0, 251) == "STOP_EXPERIMENT_SCOPE"
    assert l3.check_l3_fit_scope(0, 0) == "OK_CONTINUE"
    with pytest.raises(ValueError):
        l3.check_l3_fit_scope(0, -1)


def test_plan_frozen_and_deterministic():
    first, second = l3.make_l3_plan(), l3.make_l3_plan()
    assert first == second
    assert first["status"] == "PLAN_ONLY_NOT_EXECUTED"
    assert first["fit_cap_including_retries"] == 250 and "cost_ceiling_usd" not in first
    assert first["max_advance"] == 1
    assert [v["screen_phases"] for v in first["stage1_variants"]] == [[0, 5], [0, 20]]
    assert l3.variant_key(first["stage1_variants"][0], 5).startswith("l3_r20_f10_p05")


def _phase_rows(phases, delta):
    return [
        {
            "phase": p,
            "net_relative_cagr_pp": delta,
            "net_total_return": 0.10,
            "mdd_pp": 5.0,
            "n_days": 424,
            "turnover": 0.02,
            "coverage_ok": True,
            "ledger_ok": True,
            "data_ok": True,
            "plus10bp_advantage_pp": 0.5,
        }
        for p in phases
    ]


def _base_cells(phases):
    return [
        {
            "phase": p,
            "metrics": {"strategy_total_return": 0.05},
            "mean_turnover": 0.02,
            "n_days": 424,
            "coverage_ok": True,
            "ledger_ok": True,
            "data_ok": True,
        }
        for p in phases
    ]


def test_ordinal_remap_documented_and_strict():
    remapped, mapping = l3.ordinal_remap_for_pairing([{"phase": p} for p in (0, 5)], _base_cells((0, 10)))
    assert mapping == {"challenger_phases": [0, 5], "baseline_phases": [0, 10]}
    assert [r["phase"] for r in remapped] == [0, 5]
    assert [r["_source_phase"] for r in remapped] == [0, 10]
    with pytest.raises(ValueError):
        l3.ordinal_remap_for_pairing([{"phase": 0}], _base_cells((0, 10)))


def test_stage2_gate_and_advance_cap():
    assert l3.stage2_qualified([{"passed_coarse": False}, {"passed_coarse": True}]) is True
    assert l3.stage2_qualified([{"passed_coarse": False}, {"passed_coarse": False}]) is False
    with pytest.raises(ValueError):
        l3.stage2_qualified([])
    code, info = l3.l3_decision(
        [
            {
                "label_horizon": 20,
                "retrain_frequency": 10,
                "worst_delta_pp": 1.5,
                "median_delta_pp": 2.0,
                "passed_coarse": True,
            }
        ]
    )
    assert code == "PROPOSE_STRUCTURE" and info["winner"]["retrain_frequency"] == 10
    rows = [
        {
            "label_horizon": 20,
            "retrain_frequency": f,
            "worst_delta_pp": 1.0 + f / 100,
            "median_delta_pp": 2.0,
            "passed_coarse": True,
        }
        for f in (10, 40)
    ]
    code, info = l3.l3_decision(rows)
    assert code == "PROPOSE_STRUCTURE" and len(info) >= 1 and info["winner"]["retrain_frequency"] == 40
    assert len(info["dropped"]) == 1
    code, info = l3.l3_decision([{"passed_coarse": False}])
    assert code == "KEEP_BASELINE_FOR_PRODUCT_SELECTION" and info["n_pass"] == 0


def test_core_cli():
    assert l3.main(["--inventory"]) == 0
    assert l3.main(["--dry-run"]) == 0
    assert l3.main([]) == 2


def test_modal_static_gates():
    text = Path("audit/csi1000_l3_modal.py").read_text(encoding="utf-8")
    assert "create_if_missing=False" in text
    assert "with_mount_options(read_only=True)" in text
    assert "original.commit()" not in text and "source.commit()" not in text
    assert "research.commit()" in text
    assert "--detach" not in text or "never passes --detach" in text.lower() or "never runs in the background" in text
    assert text.count("model.fit(dataset)") == 1
    assert "STOP_L3_FIT_CAP" in text and "250" in text
    assert "purge_cfg_splits" in text and "training_slice" in text
    assert "ordinal_remap_for_pairing" in text
    assert "l3_decision" in text and "stage2_qualified" in text
    assert "L3_SMOKE_PASS 2/2" in text
    tree = ast.parse(text)
    entry = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "run_l3")
    params = {a.arg for a in entry.args.args} | {a.arg for a in entry.args.kwonlyargs}
    for flag in (
        "mode",
        "run_id",
        "p0_summary",
        "p0_prefix",
        "max_workers",
        "max_fit_waves",
    ):
        assert flag in params, f"entrypoint missing --{flag.replace('_', '-')}"


def test_no_production_or_ledger_touch():
    for name in ("audit/csi1000_l3_core.py", "audit/csi1000_l3_modal.py"):
        text = Path(name).read_text(encoding="utf-8")
        assert "CANONICAL_PROFILE" not in text
        assert "stage_b_winner_canonical" not in text
        assert ".fit(" not in text or "model.fit(dataset)" in text
    assert "BudgetLedger" not in Path("audit/csi1000_l3_modal.py").read_text(encoding="utf-8")


def _screen_frame(drift, seed):
    import math

    import pandas as pd

    from audit.independent_stage_b_metrics import REQUIRED, metrics

    n = 424
    account, cash = 100_000_000.0, 100_000_000.0
    cols = {c: [] for c in REQUIRED}
    total_cost, total_turn = 0.0, 0.0
    for i in range(n):
        daily = drift + math.sin(i / 9.0 + seed) * 0.0004
        account *= 1 + daily
        total_cost += account * 0.00005
        total_turn += account * 0.02
        cols["account"].append(account)
        cols["cash"].append(cash)
        cols["value"].append(account - cash)
        cols["return"].append(daily + 0.00005)
        cols["cost"].append(0.00005)
        cols["total_cost"].append(total_cost)
        cols["total_turnover"].append(total_turn)
        cols["turnover"].append(0.02)
        cols["bench"].append(0.0001)
    frame = pd.DataFrame(cols, index=pd.date_range("2025-01-02", periods=n, freq="B"))
    frame.index.name = "datetime"
    numbers, _, _, _ = metrics(list(frame.index.date), {k: frame[k].astype(float).to_list() for k in REQUIRED})
    clean = {k: (float(v) if isinstance(v, float) else v) for k, v in numbers.items()}
    return frame, clean


def _screen_record(phase, metrics, turnover=0.02):
    return {
        "phase": phase,
        "metrics": metrics,
        "mean_turnover": float(metrics["mean_turnover"]),
        "n_days": 424,
        "coverage_ok": True,
        "ledger_ok": True,
        "data_ok": True,
    }


def test_ordinal_screen_end_to_end_two_phases(tmp_path):
    import pandas as pd

    from audit.csi1000_layered_search_core import coarse_pass
    from audit.csi1000_layered_search_replay import paired_rows

    records = {}
    for phase, drift in ((0, 0.0009), (5, 0.0009)):
        frame, numbers = _screen_frame(drift, seed=phase)
        frame.to_parquet(tmp_path / f"chall_{phase}.parquet")
        records[phase] = _screen_record(phase, numbers)
    base_records = {}
    for phase in (0, 10):
        frame, numbers = _screen_frame(0.0003, seed=phase + 100.0)
        frame.to_parquet(tmp_path / f"base_{phase}.parquet")
        base_records[phase] = _screen_record(phase, numbers)
    assert set(base_records) == {0, 10}
    loader = lambda rec: pd.read_parquet(tmp_path / f"chall_{rec['phase']}.parquet")  # noqa: E731
    remapped, mapping = l3.ordinal_remap_for_pairing([{"phase": p} for p in (0, 5)], list(base_records.values()))
    assert mapping == {"challenger_phases": [0, 5], "baseline_phases": [0, 10]}
    assert [r["phase"] for r in remapped] == [0, 5]
    base_cells = []
    ordered_base = sorted(base_records.values(), key=lambda r: r["phase"])
    for position, phase in enumerate((0, 5)):
        base_cells.append({**ordered_base[position], "phase": phase, "_source_phase": ordered_base[position]["phase"]})
    chall_rows, base_rows, _ = paired_rows([records[0], records[5]], base_cells, loader)
    passed, detail = coarse_pass(chall_rows, base_rows, {0, 5})
    assert passed is True
    assert detail["worst_delta_pp"] > 0.5 and detail["median_delta_pp"] > 0.5


def test_ordinal_screen_rejects_losing_challenger(tmp_path):
    import pandas as pd

    from audit.csi1000_layered_search_core import coarse_pass
    from audit.csi1000_layered_search_replay import paired_rows
    from audit.independent_stage_b_metrics import REQUIRED, metrics

    for phase, drift in ((0, 0.0001), (5, 0.0001)):
        frame, _ = _screen_frame(drift, seed=phase)
        frame.to_parquet(tmp_path / f"c{phase}.parquet")
    for phase in (0, 10):
        frame, _ = _screen_frame(0.0009, seed=phase)
        frame.to_parquet(tmp_path / f"b{phase}.parquet")

    def numbers_of(path):
        frame = pd.read_parquet(path)
        numbers, _, _, _ = metrics(list(frame.index.date), {k: frame[k].astype(float).to_list() for k in REQUIRED})
        return {k: (float(v) if isinstance(v, float) else v) for k, v in numbers.items()}

    chall = [_screen_record(p, numbers_of(tmp_path / f"c{p}.parquet")) for p in (0, 5)]
    base = [_screen_record(p, numbers_of(tmp_path / f"b{p}.parquet")) for p in (0, 10)]
    remapped, _ = l3.ordinal_remap_for_pairing([{"phase": p} for p in (0, 5)], base)
    assert [r["_source_phase"] for r in remapped] == [0, 10]
    loader = lambda rec: pd.read_parquet(tmp_path / f"c{rec['phase']}.parquet")  # noqa: E731
    base_cells = [
        {**b, "phase": p, "_source_phase": b["phase"]} for b, p in zip(sorted(base, key=lambda r: r["phase"]), (0, 5))
    ]
    chall_rows, base_rows, _ = paired_rows(chall, base_cells, loader)
    passed, detail = coarse_pass(chall_rows, base_rows, {0, 5})
    assert passed is False and detail["failed"] == "improvement"


def test_coarse_default_still_rejects_unregistered_phases():
    from audit.csi1000_layered_search_core import coarse_pass

    def rows(phases, delta=3.0):
        return [
            {
                "phase": p,
                "net_relative_cagr_pp": delta,
                "net_total_return": 0.10,
                "mdd_pp": 5.0,
                "n_days": 424,
                "turnover": 0.02,
                "coverage_ok": True,
                "ledger_ok": True,
                "data_ok": True,
                "plus10bp_advantage_pp": 0.5,
            }
            for p in phases
        ]

    passed, _ = coarse_pass(rows((0, 10), 3.0), rows((0, 10), 0.0))
    assert passed is True
    passed, detail = coarse_pass(rows((0, 5), 3.0), rows((0, 5), 0.0))
    assert passed is False and detail["reason"] == "wrong pre-registered screen phases"
    passed, _ = coarse_pass(rows((0, 5), 3.0), rows((0, 5), 0.0), {0, 5})
    assert passed is True
