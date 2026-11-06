"""Independent contract checks for the limited prelive evaluation runner."""

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

SPEC = importlib.util.spec_from_file_location(
    "prelive_revalidation",
    Path(__file__).resolve().parents[1] / "code/research/regime_reselection/prelive_revalidation.py",
)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def signal(days, scores=None):
    return pd.DataFrame(
        {"score": scores or [1.0] * len(days)},
        index=pd.MultiIndex.from_product([pd.to_datetime(days), ["SH600000"]], names=["datetime", "instrument"]),
    )


def test_signal_join_rejects_different_overlap_and_missing_day(tmp_path):
    one, two = tmp_path / "one.parquet", tmp_path / "two.parquet"
    signal(["2026-01-05", "2026-01-06"]).to_parquet(one)
    signal(["2026-01-06", "2026-01-07"], [2.0, 1.0]).to_parquet(two)
    days = ["2026-01-05", "2026-01-06", "2026-01-07"]
    with pytest.raises(ValueError, match="overlap"):
        runner.load_signal([one, two], days, days[0], days[-1])
    with pytest.raises(ValueError, match="coverage"):
        runner.load_signal([one], days, days[0], days[-1])
    signal(["2026-01-06", "2026-01-07"]).to_parquet(two)
    joined, receipt = runner.load_signal([one, two], days, days[0], days[-1])
    assert len(joined) == 3
    assert receipt["overlap_rows_equal"] == 1


def synthetic_data(open_price=10, close_price=11):
    days = ["2026-01-05", "2026-01-06"]
    return {
        "days": days,
        "calendar": days,
        "signal": signal(days)["score"],
        "quotes": {
            days[1]: {
                "$open": {"SH600000": open_price},
                "$close": {"SH600000": close_price},
                "$factor": {"SH600000": 1},
                "Ref($close,1)": {"SH600000": 10},
                "$volume": {"SH600000": 100000},
            }
        },
        "bench": pd.Series([0.0], index=pd.to_datetime(days[1:])),
        "listing": {},
    }


def test_actual_engine_ledger_matches_independent_cash_and_nav():
    candidate = {"id": "synthetic", "topk": 1, "dropn": 1}
    result = runner.replay(synthetic_data(), candidate, account=10000, progress_every=0)
    # 95% target -> 900 physical shares at 10, minimum fee 5; close at 11.
    assert result["report"].iloc[0]["account"] == 10895
    assert result["report"].iloc[0]["cash"] == 995
    assert result["orders"][0]["physical_shares"] == 900
    assert result["metrics"]["valuation_verified_sessions"] == 1
    assert result["metrics"]["evidence_status"] == "REFERENCE_ONLY"
    assert not result["metrics"]["live_approved"]


def test_numpy_limit_mask_blocks_open_at_limit():
    result = runner.replay(
        synthetic_data(open_price=11), {"id": "limit", "topk": 1, "dropn": 1}, account=10000, progress_every=0
    )
    assert not result["orders"]
    assert result["report"].iloc[0]["account"] == 10000
    assert result["metrics"]["blocked_buy"] == 1


def test_independent_ledger_rejects_hidden_cash_change():
    result = runner.replay(synthetic_data(), {"id": "ledger", "topk": 1, "dropn": 1}, account=10000, progress_every=0)
    report = result["sessions"][0]
    report["cash"] += 1
    with pytest.raises(ValueError, match="conservation"):
        runner.numerical_ledger(report)
    report["cash"] = float("nan")
    with pytest.raises(ValueError, match="summary"):
        runner.numerical_ledger(report)


def test_replay_log_compaction_preserves_every_nav_fill_and_fee():
    data = synthetic_data()
    days = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]
    data["days"], data["calendar"] = days, days
    data["signal"] = signal(days)["score"]
    q = data["quotes"][days[1]]
    data["quotes"] = {d: q for d in days[1:]}
    data["bench"] = pd.Series(0.0, index=pd.to_datetime(days[1:]))
    candidate = {"id": "history", "topk": 1, "dropn": 1}
    full = runner.replay(data, candidate, account=10000, progress_every=0, engine_history_limit=120)
    compact = runner.replay(data, candidate, account=10000, progress_every=0, engine_history_limit=0)
    pd.testing.assert_frame_equal(full["report"], compact["report"], check_exact=True)
    assert full["sessions"] == compact["sessions"]
    assert full["orders"] == compact["orders"]
