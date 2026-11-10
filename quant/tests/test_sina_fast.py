"""Fast Sina raw/hfq pair adapter tests (no network by default)."""

from __future__ import annotations

import os
import time

import pandas as pd
import pytest

from data_ingest.model import Bar
from data_ingest.sina import SinaSource, _factor_series, _hfq_frame, _raw_frame


def test_factor_payload_ignores_trailing_js_comment():
    text = 'var sh600000hfq={"total":2,"data":[{"d":"2026-07-16","f":"2.0"},{"d":"2020-01-01","f":"1.5"}]}\n/* challenge */'
    series = _factor_series(text)
    assert series.loc[pd.Timestamp("2026-07-16").date()] == 2.0
    assert series.loc[pd.Timestamp("2020-01-01").date()] == 1.5


def _full_frame():
    index = pd.to_datetime(["2026-11-03", "2026-11-04", "2026-11-05", "2026-11-05"]).date
    return pd.DataFrame(
        {
            "open": [10.0, 10.1, 10.2, 10.2],
            "high": [10.2, 10.3, 10.4, 10.4],
            "low": [9.9, 10.0, 10.1, 10.1],
            "close": [10.1, 10.2, 10.3, 10.3],
            "volume": [100.0, 110.0, 120.0, 120.0],
            "amount": [1010.0, 1122.0, 1236.0, 1236.0],
        },
        index=index,
    )


def test_raw_frame_rounds_and_deduplicates_like_akshare():
    frame = _raw_frame(_full_frame(), "2026-11-04", "2026-11-05")
    assert list(frame.index) == [pd.Timestamp("2026-11-04").date(), pd.Timestamp("2026-11-05").date()]
    assert frame.loc[pd.Timestamp("2026-11-04").date(), "close"] == 10.2
    # The duplicate 11-05 row is removed before rounding.
    assert len(frame) == 2


def test_hfq_frame_applies_factor_and_keeps_amount():
    full = _full_frame()
    factor = pd.Series({pd.Timestamp("2026-11-04").date(): 2.0})
    frame = _hfq_frame(full, factor, "2026-11-04", "2026-11-05")
    # 11-04 factor 2.0 -> 20.4; 11-05 forward-filled 2.0 -> 20.6
    assert frame.loc[pd.Timestamp("2026-11-04").date(), "close"] == 20.4
    assert frame.loc[pd.Timestamp("2026-11-05").date(), "close"] == 20.6
    assert frame.loc[pd.Timestamp("2026-11-05").date(), "amount"] == 1236.0


def test_fetch_pair_uses_one_pair_attempt_per_symbol(monkeypatch):
    source = SinaSource(workers=2, retries=1)
    calls = []

    def fake(symbol, start, end):
        calls.append(symbol)
        bar = Bar(date="2026-11-10", open=1, high=1, low=1, close=1, volume=1, amount=1)
        return {"2026-11-10": bar}, {"2026-11-10": bar}

    monkeypatch.setattr(source, "_fetch_pair_one", fake)
    batch = source.fetch_pair(["SH600000", "SZ000001"], "2026-11-09", "2026-11-10")
    assert sorted(calls) == ["SH600000", "SZ000001"]
    assert set(batch.series) == {"SH600000", "SZ000001"}
    assert batch.stats["completed"] == 2


def test_fetch_pair_keeps_partial_results_and_records_errors(monkeypatch):
    source = SinaSource(workers=2, retries=1)
    bar = Bar(date="2026-11-10", open=1, high=1, low=1, close=1, volume=1, amount=1)

    def fake(symbol, start, end):
        if symbol == "SH600000":
            return {"2026-11-10": bar}, {"2026-11-10": bar}
        raise RuntimeError("provider down")

    monkeypatch.setattr(source, "_fetch_pair_one", fake)
    batch = source.fetch_pair(["SH600000", "SZ000001"], "2026-11-09", "2026-11-10")
    assert set(batch.series) == {"SH600000"}
    assert "SZ000001" in batch.errors
    assert batch.stats["completed"] == 1
    assert batch.stats["errors"] == 1


def test_fetch_pair_batch_deadline_returns_partial(monkeypatch):
    source = SinaSource(workers=2, retries=1, batch_deadline=0.05)
    bar = Bar(date="2026-11-10", open=1, high=1, low=1, close=1, volume=1, amount=1)

    def fake(symbol, start, end):
        if symbol == "SH600000":
            return {"2026-11-10": bar}, {"2026-11-10": bar}
        time.sleep(0.4)
        return {"2026-11-10": bar}, {"2026-11-10": bar}

    monkeypatch.setattr(source, "_fetch_pair_one", fake)
    batch = source.fetch_pair(["SH600000", "SZ000001"], "2026-11-09", "2026-11-10")
    assert set(batch.series) == {"SH600000"}
    assert "SZ000001" in batch.errors


@pytest.mark.skipif(os.environ.get("QLIB_NETWORK_TESTS") != "1", reason="network test disabled")
def test_fast_path_matches_public_akshare_exactly():
    import akshare as ak

    from data_ingest.symbols import to_sina_symbol

    source = SinaSource(workers=1, retries=1, prefer_fast=True)
    for symbol in ("SH600000", "SZ000001"):
        raw, hfq = source._fast_pair(symbol, "2026-11-01", "2026-11-10")
        public_raw = ak.stock_zh_a_daily(
            symbol=to_sina_symbol(symbol), start_date="20261101", end_date="20261110", adjust=""
        )
        public_hfq = ak.stock_zh_a_daily(
            symbol=to_sina_symbol(symbol), start_date="20261101", end_date="20261110", adjust="hfq"
        )
        for ours, public in ((raw, public_raw), (hfq, public_hfq)):
            expected = {
                str(row["date"])[:10]: (
                    float(row["open"]),
                    float(row["high"]),
                    float(row["low"]),
                    float(row["close"]),
                    float(row["volume"]),
                    float(row["amount"]),
                )
                for _, row in public.iterrows()
            }
            actual = {
                date: (bar.open, bar.high, bar.low, bar.close, bar.volume, bar.amount) for date, bar in ours.items()
            }
            assert actual == expected
