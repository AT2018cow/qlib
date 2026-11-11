"""Tests for the resumable overlay input cache."""

from __future__ import annotations

import pytest

from data_ingest.cache import CacheError, IngestCache
from data_ingest.model import Bar, FetchBatch, Membership, SymbolSeries


def make_bar(date: str = "2026-11-10", close: float = 10.0) -> Bar:
    return Bar(date=date, open=close, high=close, low=close, close=close, volume=1.0, amount=close)


def make_batch(symbol: str, date: str = "2026-11-10", close: float = 10.0) -> FetchBatch:
    batch = FetchBatch()
    bar = make_bar(date, close)
    batch.series[symbol] = SymbolSeries(raw={date: bar}, hfq={date: bar}, source="sina")
    return batch


def test_sina_batches_resume_and_later_rows_win(tmp_path):
    cache = IngestCache(tmp_path, "2026-11-10")
    cache.prepare_request(["2026-11-10"], "2026-11-09", "2026-11-10", ["SH600000", "SZ000001"])
    cache.append_sina(make_batch("SH600000", close=10.0), batch_index=1, stats={"completed": 1})
    second = make_batch("SH600000", close=11.0)
    second.errors["SZ000001"] = "timeout"
    cache.append_sina(second, batch_index=2, stats={"completed": 1, "errors": 1})
    loaded = cache.load_sina()
    assert loaded is not None
    assert loaded.batch.series["SH600000"].raw["2026-11-10"].close == 11.0
    assert loaded.batch.errors["SZ000001"] == "timeout"
    assert loaded.dates_by_symbol["SH600000"] == {"2026-11-10"}
    assert cache.status()["sina_batches"] == 2


def test_empty_batch_is_not_persisted(tmp_path):
    cache = IngestCache(tmp_path, "2026-11-10")
    assert cache.append_sina(FetchBatch(), batch_index=1) is None
    assert cache.load_sina() is None


def test_membership_roundtrip_and_staleness(tmp_path):
    cache = IngestCache(tmp_path, "2026-11-10")
    membership = Membership(
        market="csi300",
        asof="2026-11-10",
        members=("SH600000", "SZ000001"),
        weights_date="2026-11-10",
        raw_sha256="a" * 64,
        weights_sha256="b" * 64,
        source_url="https://example/cons.xls",
        weights_source_url="https://example/weights.xls",
    )
    cache.save_memberships({"csi300": membership}, {"csi300": {"content_bytes": 10}})
    loaded = cache.load_memberships()
    assert loaded is not None
    assert loaded["parsed"]["csi300"].members == ("SH600000", "SZ000001")
    assert cache.load_memberships(max_age_seconds=0) is None


def test_baostock_roundtrip_preserves_preclose_and_status(tmp_path):
    cache = IngestCache(tmp_path, "2026-11-10")
    batch = FetchBatch()
    bar = Bar(
        date="2026-11-10",
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        volume=3.0,
        amount=4.0,
        preclose=1.4,
        tradestatus=1,
        is_st=0,
    )
    batch.series["SH600000"] = SymbolSeries(raw={"2026-11-10": bar}, hfq={}, source="baostock")
    batch.errors["SZ000001"] = "unsupported"
    cache.save_baostock(batch)
    loaded = cache.load_baostock()
    assert loaded is not None
    assert loaded.series["SH600000"].raw["2026-11-10"].preclose == 1.4
    assert loaded.series["SH600000"].raw["2026-11-10"].tradestatus == 1
    assert loaded.errors == {}  # errors are persisted in a side file


def test_history_roundtrip(tmp_path):
    cache = IngestCache(tmp_path, "2026-11-10")
    payload = {"schema_version": 1, "symbols": {"SH600000": [{"date": "2020-01-02", "close": 1.0}]}}
    cache.save_history(payload)
    assert cache.load_history() == payload


def test_corrupt_sina_batch_is_reported(tmp_path):
    cache = IngestCache(tmp_path, "2026-11-10")
    bad = cache.directory / "sina-000001-deadbeef.parquet"
    bad.write_bytes(b"not parquet")
    with pytest.raises(CacheError):
        cache.load_sina()


def test_later_retry_batches_override_earlier_rows_regardless_of_caller_index(tmp_path):
    cache = IngestCache(tmp_path, "2026-11-10")
    first = make_batch("SH600000", close=10.0)
    first.series["SH600000"].hfq["2026-11-10"] = Bar(
        date="2026-11-10", open=1, high=1, low=1, close=float("nan"), volume=0, amount=0
    )
    cache.append_sina(first, batch_index=1)
    second = make_batch("SH600000", close=11.0)
    cache.append_sina(second, batch_index=1)
    loaded = cache.load_sina()
    assert loaded is not None
    assert loaded.batch.series["SH600000"].raw["2026-11-10"].close == 11.0
    assert loaded.batch.series["SH600000"].hfq["2026-11-10"].close == 11.0
