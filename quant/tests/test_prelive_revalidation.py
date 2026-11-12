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


def test_replay_uses_dated_st_flags_to_block_five_percent_limit():
    """A 2026-01-06 ST name has a 5% limit; the same open is tradable when non-ST."""
    data = synthetic_data(open_price=10.5)
    candidate = {"id": "dated-st", "topk": 1, "dropn": 1}
    baseline = runner.replay(data, candidate, account=10000, progress_every=0)
    assert baseline["orders"]

    data["st_history"] = {
        "schema_version": 1,
        "source": "test:dated-st",
        "dates": {"2026-01-05": [], "2026-01-06": ["SH600000"]},
    }
    st_result = runner.replay(data, candidate, account=10000, progress_every=0)
    assert not st_result["orders"]
    assert st_result["metrics"]["blocked_buy"] == 1
    assert st_result["sessions"][0]["metadata"]["evidence"] == "ST_HISTORY_DATED"


def test_replay_rejects_dated_st_history_with_a_missing_execution_date():
    data = synthetic_data()
    data["st_history"] = {
        "schema_version": 1,
        "source": "test:incomplete",
        "dates": {"2026-01-05": []},
    }
    with pytest.raises(ValueError, match="missing execution dates"):
        runner.replay(data, {"id": "incomplete-st", "topk": 1, "dropn": 1}, account=10000, progress_every=0)


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


def test_prepare_data_adds_execution_quotes_without_changing_scoring_universe(tmp_path, monkeypatch):
    """S04: retired holdings need quotes but must never re-enter ranking."""
    import qlib
    import qlib.data

    days = ["2026-01-05", "2026-01-06"]
    scored, held, benchmark = "SH600000", "SH600001", "SH000300"
    frozen_signal = signal(days)["score"]
    monkeypatch.setattr(runner, "load_signal", lambda *args, **kwargs: (frozen_signal, {"inputs": []}))
    monkeypatch.setattr(qlib, "init", lambda **kwargs: None)

    class FakeD:
        requested = None

        @staticmethod
        def calendar(start_time, end_time):
            return pd.to_datetime(days)

        @staticmethod
        def features(names, fields, start_time, end_time, freq):
            FakeD.requested = list(names)
            index = pd.MultiIndex.from_product([pd.to_datetime(days), names], names=["datetime", "instrument"])
            values = []
            for _, name in index:
                close = 100.0 if name == benchmark else (10.0 if name == scored else 20.0)
                values.append([close, close, 1.0, close, 1000.0])
            return pd.DataFrame(values, index=index, columns=fields)

        @staticmethod
        def instruments(name):
            return name

        @staticmethod
        def list_instruments(_instruments, as_list=False):
            return {scored: [(days[0], days[-1])], held: [(days[0], days[-1])]}

    monkeypatch.setattr(qlib.data, "D", FakeD)
    for name in (scored, held):
        feature = tmp_path / "features" / name.lower()
        feature.mkdir(parents=True)
        for field in ("open", "close", "factor", "volume"):
            (feature / f"{field}.day.bin").write_bytes(b"fixture")
    benchmark_dir = tmp_path / "features" / benchmark.lower()
    benchmark_dir.mkdir(parents=True)
    (benchmark_dir / "close.day.bin").write_bytes(b"fixture")

    st_history = {
        "schema_version": 1,
        "source": "test:dated-st",
        "dates": {day: [] for day in days},
    }
    prepared = runner.prepare_data(
        tmp_path,
        [tmp_path / "unused.parquet"],
        "csi300",
        days[0],
        days[-1],
        extra_quote_symbols=[held],
        st_history=st_history,
    )
    assert set(FakeD.requested) == {scored, held, benchmark}
    assert set(prepared["signal"].index.get_level_values("instrument")) == {scored}
    assert held in prepared["quotes"][days[-1]]["$close"]
    assert prepared["provenance"]["n_names"] == 1
    assert prepared["provenance"]["n_quote_names"] == 2
    assert prepared["st_history"] == st_history


def test_execution_quote_symbols_include_positions_and_pending_orders():
    """S04: every restored object that may be valued or executed is quoted."""
    state = {
        "positions": {"SH600001": {"shares": 100}},
        "pending_signal": {
            "planned_buy": ["SH600002"],
            "planned_sell": ["SH600003"],
        },
    }
    assert runner.execution_quote_symbols(state) == {"SH600001", "SH600002", "SH600003"}


def blocked_sell_data():
    days = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]
    retired, replacement = "SH600001", "SH600000"
    rankings = {
        days[0]: [(retired, 1.0), (replacement, 0.0)],
        days[1]: [(replacement, 1.0), (retired, 0.0)],
        days[2]: [(replacement, 1.0)],
        days[3]: [(replacement, 1.0)],
    }
    tuples, scores = [], []
    for day in days:
        for instrument, score in rankings[day]:
            tuples.append((pd.Timestamp(day), instrument))
            scores.append(score)
    frozen_signal = pd.Series(
        scores,
        index=pd.MultiIndex.from_tuples(tuples, names=["datetime", "instrument"]),
        name="score",
    )
    price_rows = {
        days[1]: {retired: (10.0, 10.0, 10.0), replacement: (10.0, 10.0, 10.0)},
        # Exactly -10% at the open blocks the planned sell.
        days[2]: {retired: (9.0, 9.0, 10.0), replacement: (10.0, 10.1, 10.0)},
        days[3]: {retired: (9.1, 9.2, 9.0), replacement: (10.2, 10.3, 10.1)},
    }
    quotes = {}
    for day, instruments in price_rows.items():
        quotes[day] = {
            "$open": {name: values[0] for name, values in instruments.items()},
            "$close": {name: values[1] for name, values in instruments.items()},
            "Ref($close,1)": {name: values[2] for name, values in instruments.items()},
            "$factor": {name: 1.0 for name in instruments},
            "$volume": {name: 100000.0 for name in instruments},
        }
    return {
        "days": days,
        "calendar": days,
        "signal": frozen_signal,
        "quotes": quotes,
        "bench": pd.Series(0.0, index=pd.to_datetime(days[1:])),
        "listing": {retired: "2020-01-01", replacement: "2020-01-01"},
    }, retired


def test_incremental_blocked_sell_with_retired_holding_matches_full_replay():
    """S04: a retired holding stays valued/tradable across incremental days."""
    data, retired = blocked_sell_data()
    candidate = {"id": "blocked-sell", "topk": 1, "dropn": 1}
    full = runner.replay(data, candidate, account=10000, progress_every=0)

    prefix = {**data, "days": data["days"][:3]}
    prefix["quotes"] = {day: data["quotes"][day] for day in data["days"][1:3]}
    prefix["bench"] = data["bench"].iloc[:2]
    earlier = runner.replay(prefix, candidate, account=10000, progress_every=0)
    assert retired in earlier["final_state"]["positions"]
    assert earlier["report"].iloc[-1]["blocked_sell"] == 1

    suffix = {**data, "days": data["days"][2:]}
    suffix["signal"] = data["signal"][data["signal"].index.get_level_values(0) >= pd.Timestamp(data["days"][2])]
    suffix["quotes"] = {data["days"][3]: data["quotes"][data["days"][3]]}
    suffix["bench"] = data["bench"].iloc[2:]
    incremental = runner.replay(
        suffix,
        candidate,
        account=10000,
        progress_every=0,
        initial_state=earlier["final_state"],
        prior=earlier,
    )
    pd.testing.assert_frame_equal(full["report"], incremental["report"], check_exact=True)
    assert full["sessions"] == incremental["sessions"]
    assert full["orders"] == incremental["orders"]
    assert full["final_state"] == incremental["final_state"]


def test_incremental_retired_holding_still_reports_a_real_missing_quote():
    data, retired = blocked_sell_data()
    candidate = {"id": "blocked-sell-gap", "topk": 1, "dropn": 1}
    prefix = {**data, "days": data["days"][:3]}
    prefix["quotes"] = {day: data["quotes"][day] for day in data["days"][1:3]}
    prefix["bench"] = data["bench"].iloc[:2]
    earlier = runner.replay(prefix, candidate, account=10000, progress_every=0)

    suffix = {**data, "days": data["days"][2:]}
    suffix["signal"] = data["signal"][data["signal"].index.get_level_values(0) >= pd.Timestamp(data["days"][2])]
    last_quotes = data["quotes"][data["days"][3]]
    suffix["quotes"] = {
        data["days"][3]: {
            field: {k: v for k, v in values.items() if k != retired} for field, values in last_quotes.items()
        }
    }
    suffix["bench"] = data["bench"].iloc[2:]
    incremental = runner.replay(
        suffix,
        candidate,
        account=10000,
        progress_every=0,
        initial_state=earlier["final_state"],
        prior=earlier,
    )
    assert any(gap["instrument"] == retired for gap in incremental["gaps"])
