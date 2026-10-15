"""PR-S0 replay tests: source resolution, real-execution wiring, publish, pairing.

No Modal, no private provider, no training. Qlib is never imported: the
backtest seam is injected as a fake runner with the same call signature.
"""

import hashlib
import json

import pandas as pd
import pytest

from audit import csi1000_layered_search_core as core
from audit import csi1000_layered_search_reconcile as rec
from audit.csi1000_layered_search_modal import _check_manifest
from audit.csi1000_layered_search_replay import (
    execute_cell,
    lookup_phase,
    paired_rows,
    publish_cell,
    report_metrics,
    resolve_phase_sources,
    stress_advantage_pp,
)
from audit.independent_stage_b_metrics import REQUIRED

WINNER = core.WINNER_CANDIDATE
TOKEN = core.STAGE_B_SNAPSHOT_TOKEN
N = 424


def _frame(drift=0.0006, wiggle=0.0004, turnover=0.02, cost=0.00005):
    import math

    account = 100_000_000.0
    cash = 100_000_000.0
    rows = {c: [] for c in REQUIRED}
    total_cost, total_turn = 0.0, 0.0
    for i in range(N):
        daily = drift + math.sin(i / 9.0) * wiggle
        prior_account = account
        account *= 1 + daily
        # Independent ledger uses *opening* equity for fee and turnover notional.
        # Qlib's 'return' is gross before cost; account NAV compounds net daily.
        total_cost += prior_account * cost
        total_turn += prior_account * turnover
        rows["account"].append(account)
        rows["cash"].append(cash)
        rows["value"].append(account - cash)
        rows["return"].append(daily + cost)
        rows["cost"].append(cost)
        rows["total_cost"].append(total_cost)
        rows["total_turnover"].append(total_turn)
        rows["turnover"].append(turnover)
        rows["bench"].append(0.0001)
    frame = pd.DataFrame(rows, index=pd.date_range("2025-01-02", periods=N, freq="B"))
    frame.index.name = "datetime"
    return frame


def _decisions(n=N):
    return [
        {
            "start_time": f"2025-01-{(i % 28) + 1:02d}T00:00:00",
            "end_time": f"2025-01-{(i % 28) + 1:02d}T23:59:59",
            "orders": [],
        }
        for i in range(n)
    ]


def _snapshot_tree(tmp_path, candidate=WINNER, phase=0, tamper=None):
    root = tmp_path / "snap"
    d = root / "phases" / candidate / f"phase{phase:02d}"
    d.mkdir(parents=True)
    _frame().to_parquet(d / "report.parquet")
    pd.DataFrame({"score": [0.1] * 50}).to_parquet(d / "signal.parquet")
    (d / "decisions.json").write_text(json.dumps(_decisions()), encoding="utf-8")
    if tamper:
        with (d / tamper).open("ab") as f:
            f.write(b"\x00")
    row = {"phase": phase, "candidate_id": candidate}
    for kind, name in (("report", "report.parquet"), ("signal", "signal.parquet"), ("decision", "decisions.json")):
        rel = f"/vol/csi1000_stage_b/{TOKEN}/phases/{candidate}/phase{phase:02d}/{name}"
        row[kind + "_artifact"] = {"path": rel, "sha256": hashlib.sha256((d / name).read_bytes()).hexdigest()}
    return root, row


def _sources_dict(root, row, candidate=WINNER, phase=0):
    paths = resolve_phase_sources(root, row, candidate, phase)
    return paths


def test_lookup_missing_is_source_unavailable():
    with pytest.raises(ValueError, match="STOP_SOURCE_UNAVAILABLE"):
        lookup_phase({"ranked_candidates": []}, WINNER, 0)


def test_resolve_missing_root_is_source_unavailable(tmp_path):
    _, row = _snapshot_tree(tmp_path)
    with pytest.raises(ValueError, match="STOP_SOURCE_UNAVAILABLE"):
        resolve_phase_sources(tmp_path / "nope", row, WINNER, 0)


def test_resolve_tampered_bytes_blocked(tmp_path):
    root, row = _snapshot_tree(tmp_path)
    (root / "phases" / WINNER / "phase00" / "signal.parquet").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="frozen source bytes differ"):
        resolve_phase_sources(root, row, WINNER, 0)


def _cell(topk=20, dropn=2, phase=0):
    return {
        "cell_key": f"cell_p{phase:02d}_k{topk}_d{dropn}",
        "candidate_id": WINNER,
        "phase": phase,
        "topk": topk,
        "dropn": dropn,
    }


def _runner_factory(frozen, mode="match", calls=None):
    def fake(signal, topk, dropn, codes, start, end):
        if calls is not None:
            calls.append({"topk": topk, "dropn": dropn, "codes": codes})
        if mode == "match":
            return frozen.copy(), _decisions()
        if mode == "drift":
            out = frozen.copy()
            out["account"] = out["account"] + 1.0
            return out, _decisions()
        if mode == "window":
            extra = frozen.iloc[:1].copy()
            return pd.concat([frozen, extra]), _decisions() + _decisions(1)
        raise AssertionError("unknown mode")

    return fake


def test_execute_baseline_combo_reproduces_and_records(tmp_path):
    root, row = _snapshot_tree(tmp_path)
    sources = _sources_dict(root, row)
    frozen = pd.read_parquet(sources["report"])
    calls = []
    record, artifacts = execute_cell(
        cell=_cell(),
        sources=sources,
        signal=pd.Series([0.1] * 50),
        frozen_report=frozen,
        frozen_decisions=_decisions(),
        quote_codes=["SH600000"],
        quote_sha="f" * 64,
        market_codes="csi1000",
        runner=_runner_factory(frozen, "match", calls),
        reference_validator=lambda *a: {"status": "PASS"},
    )
    assert record["status"] == "PASS" and record["legacy_reproduction"] == "PASS"
    assert calls[0] == {"topk": 20, "dropn": 2, "codes": "csi1000"}
    assert calls[1]["codes"] == ["SH600000"]
    core.check_cell_record({**record, "output_sha256": "b" * 64})
    assert record["n_days"] == 424 and record["final_nav_cny"] > 0
    assert record["total_cost_cny"] == pytest.approx(frozen["total_cost"].iloc[-1])
    assert record["ledger_ok"] is True


def test_execute_legacy_drift_is_stop_p0(tmp_path):
    root, row = _snapshot_tree(tmp_path)
    sources = _sources_dict(root, row)
    frozen = pd.read_parquet(sources["report"])
    with pytest.raises(ValueError, match="STOP_P0"):
        execute_cell(
            cell=_cell(),
            sources=sources,
            signal=pd.Series([0.1] * 50),
            frozen_report=frozen,
            frozen_decisions=_decisions(),
            quote_codes=["SH600000"],
            quote_sha="f" * 64,
            market_codes="csi1000",
            runner=_runner_factory(frozen, "drift"),
            reference_validator=lambda *a: {"status": "PASS"},
        )


def test_execute_window_change_is_stop_p0(tmp_path):
    root, row = _snapshot_tree(tmp_path)
    sources = _sources_dict(root, row)
    frozen = pd.read_parquet(sources["report"])
    with pytest.raises(ValueError, match="STOP_P0"):
        execute_cell(
            cell=_cell(),
            sources=sources,
            signal=pd.Series([0.1] * 50),
            frozen_report=frozen,
            frozen_decisions=_decisions(),
            quote_codes=["SH600000"],
            quote_sha="f" * 64,
            market_codes="csi1000",
            runner=_runner_factory(frozen, "window"),
            reference_validator=lambda *a: {"status": "PASS"},
        )


def test_execute_portfolio_combo_skips_legacy_but_verifies(tmp_path):
    root, row = _snapshot_tree(tmp_path)
    sources = _sources_dict(root, row)
    frozen = pd.read_parquet(sources["report"])
    calls = []
    record, _ = execute_cell(
        cell=_cell(topk=10, dropn=1),
        sources=sources,
        signal=pd.Series([0.1] * 50),
        frozen_report=frozen,
        frozen_decisions=_decisions(),
        quote_codes=["SH600000"],
        quote_sha="f" * 64,
        market_codes="csi1000",
        runner=_runner_factory(frozen, "match", calls),
    )
    assert record["legacy_reproduction"] == "NOT_APPLICABLE"
    assert all(c["topk"] == 10 and c["dropn"] == 1 for c in calls)
    core.check_cell_record({**record, "output_sha256": "b" * 64})


def test_execute_rejects_synthetic_fee_accounting_tamper(tmp_path):
    root, row = _snapshot_tree(tmp_path)
    sources = _sources_dict(root, row)
    frozen = pd.read_parquet(sources["report"])

    def wrong_cumulative_fee(signal, topk, dropn, codes, start, end):
        out = frozen.copy()
        out["total_cost"] += 100.0  # Fee increments no longer equal prior NAV * daily rate.
        return out, _decisions()

    with pytest.raises(ValueError, match="daily return or fee reconciliation"):
        execute_cell(
            cell=_cell(topk=10, dropn=1),
            sources=sources,
            signal=pd.Series([0.1] * 50),
            frozen_report=frozen,
            frozen_decisions=_decisions(),
            quote_codes=["SH600000"],
            quote_sha="f" * 64,
            market_codes="csi1000",
            runner=wrong_cumulative_fee,
        )


def test_report_metrics_rejects_missing_nav_and_nonfinite():
    frame = _frame()
    numbers, _ = report_metrics(frame)
    assert numbers["n_days"] == 424
    with pytest.raises(ValueError, match="missing NAV"):
        report_metrics(frame.drop(columns=["account"]))
    bad = frame.copy()
    bad.loc[bad.index[0], "return"] = float("inf")
    with pytest.raises(ValueError, match="non-finite"):
        report_metrics(bad)


def test_stress_advantage_positive_and_blocked_negative():
    frame = _frame(drift=0.0009, turnover=0.022)
    adv, status = stress_advantage_pp(frame, 0.02, 10.0)
    assert status == "OK" and adv is not None and adv > 0
    killer = _frame(drift=0.0001, turnover=50.0)
    adv, status = stress_advantage_pp(killer, 0.02, 10.0)
    assert adv is None and status == "BLOCKED_NEGATIVE_STRESSED_NAV"


def test_publish_atomic_resume_and_conflict(tmp_path):
    frame = _frame()
    record = {
        "status": "PASS",
        "cell_key": "ck1",
        "candidate_id": WINNER,
        "phase": 0,
        "topk": 20,
        "dropn": 2,
        "engine": "t",
        "execution": "tclose_to_t1open",
        "quote_path": "static_quote_union_f1",
        "quote_sha256": "f" * 64,
        "signal_sha256": "e" * 64,
        "source_sha256": "d" * 64,
        "frozen_report_sha256": "c" * 64,
        "frozen_decision_sha256": "b" * 64,
        "cache_key": "",
        "output_sha256": "",
        "legacy_reproduction": "PASS",
        "final_nav_cny": 1e8,
        "total_cost_cny": 1.0,
        "total_turnover_cny": 1.0,
        "mean_turnover": 0.02,
        "n_days": 424,
        "order_count": 0,
        "ledger_ok": True,
        "coverage_ok": True,
        "data_ok": True,
        "training_performed": False,
        "promotion_authorized": False,
        "metrics": {
            "relative_excess_cagr": 0.03,
            "strategy_total_return": 0.1,
            "strategy_max_drawdown": -0.05,
            "mean_turnover": 0.02,
            "total_cost_sum": 1.0,
            "n_days": 424,
        },
    }
    arts = {"fixed_report": frame, "fixed_orders": []}
    first = publish_cell(tmp_path / "cells", dict(record), arts, "")
    assert first["output_sha256"] and first["cache_key"]
    before = (tmp_path / "cells" / "ck1" / "cell.json").read_text(encoding="utf-8")
    second = publish_cell(tmp_path / "cells", dict(record), arts, first["cache_key"])
    assert second["resumed"] == "RESUME_VERIFIED"
    with (tmp_path / "cells" / "ck1" / "daily_nav.csv").open("a", encoding="utf-8") as f:
        f.write("tampered\n")
    with pytest.raises(ValueError, match="FAIL_CONFLICT"):
        publish_cell(tmp_path / "cells", dict(record), arts, first["cache_key"])
    assert (tmp_path / "cells" / "ck1" / "cell.json").read_text(encoding="utf-8") == before
    with pytest.raises(ValueError, match="FAIL_CONFLICT"):
        publish_cell(tmp_path / "cells", dict(record), arts, "0" * 64)


def _cell_records_for_pairing():
    chall, base = [], []
    frames = {}
    for phase in [0, 4, 6, 10, 15]:
        for key, drift, turnover, store in ((f"c{phase}", 0.0009, 0.022, chall), (f"b{phase}", 0.0004, 0.020, base)):
            fr = _frame(drift=drift, turnover=turnover)
            from audit.csi1000_layered_search_replay import report_metrics as _rm

            numbers, _ = _rm(fr)
            store.append(
                {
                    "cell_key": key,
                    "phase": phase,
                    "n_days": 424,
                    "mean_turnover": float(numbers["mean_turnover"]),
                    "coverage_ok": True,
                    "ledger_ok": True,
                    "data_ok": True,
                    "metrics": numbers,
                }
            )
            frames[key] = fr
    return chall, base, frames


def test_paired_rows_and_final_verdict_end_to_end():
    chall, base, frames = _cell_records_for_pairing()
    rows_c, rows_b, turnover = paired_rows(chall, base, lambda c: frames[c["cell_key"]])
    assert abs(turnover - 1.1) < 0.05
    verdict, info = core.final_verdict(rows_c, rows_b, turnover)
    assert verdict == "PASS", info
    assert info["n_beat"] == 5 and info["stress10_ok"] is True


def test_demo_fee_is_real_cny_and_reconciles_to_opening_equity():
    from audit.csi1000_layered_search_reconcile import _demo_record, _synthetic_frame

    frame = _synthetic_frame(0.0006, 0.0002, 0.02, 0.00005)
    record = _demo_record("unit_checked", WINNER, 0, frame)
    numbers, diagnostics = report_metrics(frame)
    assert record["total_cost_cny"] == pytest.approx(float(frame["total_cost"].iloc[-1]))
    assert record["total_cost_cny"] > numbers["total_cost_sum"] * 1_000_000
    assert diagnostics["max_cost_rate_delta"] < 1e-10
    assert diagnostics["max_turnover_rate_delta"] < 1e-10


def test_reconcile_summary_ledger_and_demo(tmp_path):
    records = []
    for i in range(4):
        r = {
            "status": "PASS",
            "cell_key": f"c{i}",
            "source_sha256": "a" * 64,
            "output_sha256": "b" * 64,
            "quote_sha256": "c" * 64,
            "signal_sha256": "d" * 64,
            "cache_key": "e" * 64,
            "final_nav_cny": 1e8,
            "total_cost_cny": 1.0,
            "ledger_ok": True,
            "coverage_ok": True,
            "data_ok": True,
            "training_performed": False,
            "promotion_authorized": False,
            "n_days": 424,
            "topk": 20,
            "dropn": 2,
            "legacy_reproduction": "PASS",
            "corrected_reference": "PASS",
        }
        records.append(r)
    summary = {
        "stage": "p0",
        "expected": 4,
        "completed": 4,
        "blocked": 0,
        "verified_cells": records,
        "training_performed": False,
        "promotion_authorized": False,
        "source_volume_read_only": True,
    }
    assert rec.check_stage_summary(summary)["completed"] == 4
    with pytest.raises(ValueError):
        rec.check_stage_summary({**summary, "verified_cells": records[:3]})
    ledger = tmp_path / "trial.jsonl"
    ledger.write_text(
        json.dumps({"cell_key": "c0", "run_id": "r1", "status": "PASS", "app_id": "app-1", "spend_usd": 0.4, "fits": 0})
        + "\n",
        encoding="utf-8",
    )
    assert rec.check_trial_ledger(ledger)["trials"] == 1
    ledger.write_text(
        json.dumps({"cell_key": "c0", "run_id": "r2", "status": "PASS", "app_id": "app-2", "spend_usd": 0.1}) + "\n",
        encoding="utf-8",
    )
    assert rec.check_trial_ledger(ledger)["trials"] == 1  # same cell, new run: distinct row
    ledger.write_text(
        json.dumps({"cell_key": "c1", "run_id": "r1", "status": "PASS", "app_id": "", "spend_usd": 0.4}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="no real App ID"):
        rec.check_trial_ledger(ledger)
    report = rec.write_demo(tmp_path / "demo")
    assert report["bundles"]["pass_bundle"]["verdict"] == "PASS"
    assert report["bundles"]["fail_bundle"]["all_three_gates_blocked"] is True


def test_resume_manifest_rejects_placeholders(tmp_path):
    good = tmp_path / "m.json"
    good.write_text(
        json.dumps(
            {"cells": [{"cell_key": "a", "source_sha256": "s" * 64, "output_sha256": "o" * 64, "cache_key": "k" * 64}]}
        )
        + "\n",
        encoding="utf-8",
    )
    assert _check_manifest(str(good))["cells"][0]["cell_key"] == "a"
    bad = tmp_path / "b.json"
    bad.write_text(
        json.dumps(
            {
                "cells": [
                    {
                        "cell_key": "a",
                        "source_sha256": "pending_source_sha",
                        "output_sha256": "o" * 64,
                        "cache_key": "k" * 64,
                    }
                ]
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="placeholder"):
        _check_manifest(str(bad))
