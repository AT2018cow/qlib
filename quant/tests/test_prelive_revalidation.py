"""Independent contract checks for the limited prelive evaluation runner."""

import importlib.util
import json
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


def multi_day_data():
    days = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]
    scores = [0.3, 0.2, 0.1, 0.4]
    names = ["SH600000", "SH600001"]
    signal_rows = []
    for day, score in zip(days, scores):
        signal_rows.extend([(day, names[0], score), (day, names[1], score - 0.05)])
    signal_frame = pd.DataFrame(
        {"score": [row[2] for row in signal_rows]},
        index=pd.MultiIndex.from_tuples(
            [(pd.Timestamp(row[0]), row[1]) for row in signal_rows], names=["datetime", "instrument"]
        ),
    )
    prices = {
        "2026-01-06": (10.0, 10.5),
        "2026-01-07": (10.2, 10.1),
        "2026-01-08": (9.9, 10.6),
    }
    quotes = {
        day: {
            "$open": {name: values[0] + i * 0.01 for i, name in enumerate(names)},
            "$close": {name: values[1] + i * 0.01 for i, name in enumerate(names)},
            "$factor": {name: 1.0 for name in names},
            "Ref($close,1)": {name: values[0] for name in names},
            "$volume": {name: 100000.0 for name in names},
        }
        for day, values in prices.items()
    }
    return {
        "days": days,
        "calendar": days,
        "signal": signal_frame["score"],
        "quotes": quotes,
        "bench": pd.Series([0.0, 0.01, -0.01], index=pd.to_datetime(days[1:])),
        "listing": {},
    }


def test_incremental_replay_matches_full_replay_exactly():
    data = multi_day_data()
    candidate = {"id": "incremental", "topk": 1, "dropn": 1}
    full = runner.replay(data, candidate, account=10000, progress_every=0, engine_history_limit=120)

    prefix = {**data, "days": data["days"][:3]}
    prefix["quotes"] = {d: data["quotes"][d] for d in data["days"][1:3]}
    prefix["bench"] = data["bench"].iloc[:2]
    earlier = runner.replay(prefix, candidate, account=10000, progress_every=0, engine_history_limit=120)

    suffix = {**data, "days": data["days"][2:]}
    suffix["quotes"] = {data["days"][3]: data["quotes"][data["days"][3]]}
    suffix["bench"] = data["bench"].iloc[2:]
    incremental = runner.replay(
        suffix,
        candidate,
        account=10000,
        progress_every=0,
        engine_history_limit=120,
        initial_state=earlier["final_state"],
        prior=earlier,
    )
    pd.testing.assert_frame_equal(full["report"], incremental["report"], check_exact=True)
    assert full["sessions"] == incremental["sessions"]
    assert full["orders"] == incremental["orders"]
    assert full["gaps"] == incremental["gaps"]
    assert full["final_state"] == incremental["final_state"]
    assert full["metrics"]["valuation_verified_sessions"] == incremental["metrics"]["valuation_verified_sessions"]
    assert full["metrics"]["longest_underwater_sessions"] == incremental["metrics"]["longest_underwater_sessions"]


def test_incremental_replay_rejects_state_from_another_account():
    data = multi_day_data()
    candidate = {"id": "incremental", "topk": 1, "dropn": 1}
    prefix = {**data, "days": data["days"][:3]}
    prefix["quotes"] = {d: data["quotes"][d] for d in data["days"][1:3]}
    prefix["bench"] = data["bench"].iloc[:2]
    earlier = runner.replay(prefix, candidate, account=10000, progress_every=0)
    state = json.loads(json.dumps(earlier["final_state"]))
    state["cash"] += 1
    suffix = {**data, "days": data["days"][2:]}
    suffix["quotes"] = {data["days"][3]: data["quotes"][data["days"][3]]}
    suffix["bench"] = data["bench"].iloc[2:]
    with pytest.raises(ValueError, match="checkpoint"):
        runner.replay(suffix, candidate, account=10000, initial_state=state, prior=earlier)
