"""Resilience tests for the primary self-hosted provider pipeline."""

from __future__ import annotations

import math

import numpy as np
import pytest

from data_ingest.cache import IngestCache
from data_ingest.model import Bar, FetchBatch, Membership, SymbolSeries
from data_ingest.overlay import read_bin_value
from data_ingest.pipeline import OverlayPipeline, PipelineError

BASE_DATES = ("2026-11-03", "2026-11-04")
TARGET_DATES = ("2026-11-05", "2026-11-06")
MARKETS = {
    "csi300": ("SH600000", "SH600001"),
    "csi500": ("SZ000001", "SZ000002"),
    "csi1000": ("SZ300001", "SZ300002"),
}


def _write_bin(path, values, start=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    np.hstack([[float(start)], values]).astype("<f").tofile(path)


def make_provider(root, symbols, calendar=BASE_DATES):
    root.mkdir(parents=True)
    (root / "calendars").mkdir()
    (root / "calendars" / "day.txt").write_text("\n".join(calendar) + "\n")
    (root / "instruments").mkdir()
    for market, members in MARKETS.items():
        (root / "instruments" / f"{market}.txt").write_text(
            "\n".join(f"{symbol}\t{calendar[0]}\t{calendar[-1]}" for symbol in members) + "\n"
        )
    (root / "instruments" / "csi800.txt").write_text(
        "\n".join(f"{symbol}\t{calendar[0]}\t{calendar[-1]}" for symbol in MARKETS["csi300"] + MARKETS["csi500"]) + "\n"
    )
    (root / "instruments" / "all.txt").write_text(
        "\n".join(f"{symbol}\t{calendar[0]}\t{calendar[-1]}" for symbol in symbols) + "\n"
    )
    for symbol in symbols:
        base = 10.0 + symbols.index(symbol)
        feature = root / "features" / symbol.lower()
        values = {
            "open": base,
            "high": base,
            "low": base,
            "close": base,
            "volume": 1000.0,
            "amount": base * 1000.0,
            "factor": 1.0,
            "vwap": base,
            "change": 0.0,
            "adjclose": base,
        }
        for field, value in values.items():
            _write_bin(feature / f"{field}.day.bin", [value, value])
    return root


ALL_DATES = BASE_DATES + TARGET_DATES


def _bars(symbol, dates):
    base = 10.0 + (sum(ord(char) for char in symbol) % 100) / 10.0
    output = {}
    for date in dates:
        close = base + ALL_DATES.index(date) * 0.1
        previous = base + max(0, ALL_DATES.index(date) - 1) * 0.1
        output[date] = Bar(
            date=date,
            open=close,
            high=close + 0.2,
            low=close - 0.2,
            close=close,
            volume=1000.0,
            amount=close * 1000.0,
            preclose=previous,
            tradestatus=1,
            is_st=0,
        )
    return output


class FakeSina:
    def __init__(self, *, fail=(), dates=TARGET_DATES, with_base=True):
        self.fail = set(fail)
        self.dates = tuple(dates)
        self.with_base = with_base
        self.calls = []

    def fetch_pair(self, symbols, start, end, *, on_progress=None, progress_every=200):
        self.calls.append(sorted(symbols))
        batch = FetchBatch()
        dates = (BASE_DATES[-1],) + self.dates if self.with_base else self.dates
        for symbol in sorted(symbols):
            if symbol in self.fail:
                batch.errors[symbol] = "fake sina failure"
                continue
            raw = _bars(symbol, dates)
            batch.series[symbol] = SymbolSeries(raw=raw, hfq=raw, source="fake-sina")
        if on_progress is not None and (batch.series or batch.errors):
            on_progress(batch, 1, {"completed": len(batch.series), "errors": len(batch.errors)})
        return batch


class FakeBaoStock:
    def __init__(self, *, fail=(), mismatch=(), history_fail=()):
        self.fail = set(fail)
        self.mismatch = set(mismatch)
        self.history_fail = set(history_fail)
        self.fetch_calls = []
        self.history_calls = []

    def fetch(self, symbols, start, end, adjust="3", **kwargs):
        self.fetch_calls.append(sorted(symbols))
        batch = FetchBatch()
        for symbol in sorted(symbols):
            if symbol in self.fail:
                batch.errors[symbol] = "fake baostock failure"
                continue
            raw = _bars(symbol, TARGET_DATES)
            if symbol in self.mismatch:
                raw = {
                    date: Bar(
                        date=bar.date,
                        open=bar.open,
                        high=bar.high,
                        low=bar.low,
                        close=bar.close + 1.0,
                        volume=bar.volume,
                        amount=bar.amount,
                        preclose=bar.preclose,
                        tradestatus=bar.tradestatus,
                        is_st=bar.is_st,
                    )
                    for date, bar in raw.items()
                }
            batch.series[symbol] = SymbolSeries(raw=raw, hfq={}, source="fake-baostock")
        return batch

    def fetch_history(self, symbols, start, end, **kwargs):
        self.history_calls.append(sorted(symbols))
        output = {}
        for symbol in sorted(symbols):
            if symbol in self.history_fail:
                output[symbol] = {"error": "no history"}
                continue
            raw = _bars(symbol, BASE_DATES + TARGET_DATES)
            output[symbol] = {"raw": raw, "hfq_close": {date: bar.close for date, bar in raw.items()}}
        return output


def memberships(asof, members=None, csi800=True):
    members = members or {market: values for market, values in MARKETS.items()}
    return {
        market: Membership(
            market=market,
            asof=asof,
            members=tuple(values),
            raw_sha256="a" * 64,
            weights_date=asof,
            weights_sha256="b" * 64,
            source_url=f"https://example.invalid/{market}",
        )
        for market, values in members.items()
    }


def build(tmp_path, monkeypatch, *, sina=None, baostock=None, cache=None, **kwargs):
    symbols = sorted({symbol for values in MARKETS.values() for symbol in values} | set(kwargs.pop("extra_base", ())))
    base = make_provider(tmp_path / "base", symbols)
    import data_ingest.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "fetch_all_memberships", lambda *args, **kw: memberships(TARGET_DATES[-1]))
    market = FakeSina()
    pipeline = OverlayPipeline(
        sina=sina or market,
        baostock=baostock or FakeBaoStock(),
        calendar_dates=list(BASE_DATES) + list(TARGET_DATES),
        crosscheck_count=3,
        cache=cache,
    )
    manifest = pipeline.build(
        base,
        tmp_path / "overlay",
        asof=TARGET_DATES[-1],
        dates=list(TARGET_DATES),
        markets=tuple(MARKETS),
        **kwargs,
    )
    return base, tmp_path / "overlay", manifest


def test_primary_coverage_stops_before_whole_market_baostock(tmp_path, monkeypatch):
    failing = {"SH600000", "SH600001", "SZ000001"}  # 3/6 = 50%
    baostock = FakeBaoStock()
    with pytest.raises(PipelineError, match="primary source not ready"):
        build(tmp_path, monkeypatch, sina=FakeSina(fail=failing), baostock=baostock)
    assert baostock.fetch_calls == []


def test_membership_lag_fallback_requires_exact_member_equality(tmp_path, monkeypatch):
    # Official snapshot one day behind, members exactly the base members:
    # a single-session lag is safe.
    import data_ingest.pipeline as pipeline_module

    stale = memberships(BASE_DATES[-1])
    monkeypatch.setattr(pipeline_module, "fetch_all_memberships", lambda *args, **kw: stale)
    base = make_provider(tmp_path / "base", sorted({s for v in MARKETS.values() for s in v}))
    pipeline = OverlayPipeline(
        sina=FakeSina(dates=("2026-11-05",)),
        baostock=FakeBaoStock(),
        calendar_dates=list(BASE_DATES) + ["2026-11-05"],
        crosscheck_count=3,
    )
    manifest = pipeline.build(
        base,
        tmp_path / "overlay",
        asof="2026-11-05",
        dates=["2026-11-05"],
        markets=tuple(MARKETS),
        allow_base_membership=True,
    )
    assert manifest["membership_policy"]["mode"] == "BASE_PIT_LAG"
    assert manifest["membership_policy"]["equality_verified"] is True


def test_membership_lag_with_changed_members_fails_closed(tmp_path, monkeypatch):
    import data_ingest.pipeline as pipeline_module

    changed = {market: list(values) for market, values in MARKETS.items()}
    changed["csi300"] = ["SH600000", "SH600999"]
    stale = memberships(BASE_DATES[-1], changed)
    monkeypatch.setattr(pipeline_module, "fetch_all_memberships", lambda *args, **kw: stale)
    base = make_provider(tmp_path / "base", sorted({s for v in MARKETS.values() for s in v}))
    pipeline = OverlayPipeline(
        sina=FakeSina(dates=("2026-11-05",)),
        baostock=FakeBaoStock(),
        calendar_dates=list(BASE_DATES) + ["2026-11-05"],
        crosscheck_count=3,
    )
    with pytest.raises(PipelineError, match="may hide a rebalance"):
        pipeline.build(
            base,
            tmp_path / "overlay",
            asof="2026-11-05",
            dates=["2026-11-05"],
            markets=tuple(MARKETS),
            allow_base_membership=True,
        )


def test_multi_day_stale_membership_fails_closed(tmp_path, monkeypatch):
    import data_ingest.pipeline as pipeline_module

    stale = memberships(BASE_DATES[-1])
    monkeypatch.setattr(pipeline_module, "fetch_all_memberships", lambda *args, **kw: stale)
    base = make_provider(tmp_path / "base", sorted({s for v in MARKETS.values() for s in v}))
    pipeline = OverlayPipeline(
        sina=FakeSina(dates=TARGET_DATES),
        baostock=FakeBaoStock(),
        calendar_dates=list(BASE_DATES) + list(TARGET_DATES),
        crosscheck_count=3,
    )
    with pytest.raises(PipelineError, match="multi-day gap"):
        pipeline.build(
            base,
            tmp_path / "overlay",
            asof=TARGET_DATES[-1],
            dates=list(TARGET_DATES),
            markets=tuple(MARKETS),
            allow_base_membership=True,
        )


def test_retired_holding_is_fetched_and_gated(tmp_path, monkeypatch):
    held = "SH600009"
    import data_ingest.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "fetch_all_memberships", lambda *args, **kw: memberships(TARGET_DATES[-1]))
    base = make_provider(tmp_path / "base", sorted({s for v in MARKETS.values() for s in v} | {held}))
    sina = FakeSina()
    pipeline = OverlayPipeline(
        sina=sina,
        baostock=FakeBaoStock(),
        calendar_dates=list(BASE_DATES) + list(TARGET_DATES),
        crosscheck_count=3,
    )
    manifest = pipeline.build(
        base,
        tmp_path / "overlay",
        asof=TARGET_DATES[-1],
        dates=list(TARGET_DATES),
        markets=tuple(MARKETS),
        extra_symbols=[held],
        required_extra_symbols=[held],
    )
    assert held in sina.calls[-1]
    assert manifest["universe"]["required_extras"] == [held]
    assert read_bin_value(tmp_path / "overlay", held, "close", TARGET_DATES[-1]) > 0


def test_new_member_history_is_backfilled_from_baostock(tmp_path, monkeypatch):
    new_member = "SH600010"
    membership = {market: list(values) for market, values in MARKETS.items()}
    membership["csi300"] = ["SH600000", new_member]
    import data_ingest.pipeline as pipeline_module

    monkeypatch.setattr(
        pipeline_module, "fetch_all_memberships", lambda *args, **kw: memberships(TARGET_DATES[-1], membership)
    )
    symbols = sorted({s for v in MARKETS.values() for s in v} | {new_member})
    base = make_provider(tmp_path / "base", symbols, calendar=BASE_DATES)
    # Remove the new member's frozen feature tree: it must be reconstructed.
    import shutil

    shutil.rmtree(base / "features" / new_member.lower())
    pipeline = OverlayPipeline(
        sina=FakeSina(),
        baostock=FakeBaoStock(),
        calendar_dates=list(BASE_DATES) + list(TARGET_DATES),
        crosscheck_count=3,
    )
    manifest = pipeline.build(
        base,
        tmp_path / "overlay",
        asof=TARGET_DATES[-1],
        dates=list(TARGET_DATES),
        markets=tuple(MARKETS),
    )
    assert manifest["history_backfill"]["symbols"] == [new_member]
    assert math.isnan(read_bin_value(base, new_member, "close", BASE_DATES[-1]))
    assert math.isfinite(read_bin_value(tmp_path / "overlay", new_member, "close", BASE_DATES[-1]))
    assert math.isfinite(read_bin_value(tmp_path / "overlay", new_member, "close", TARGET_DATES[-1]))


def test_sina_cache_resumes_and_only_refetches_gaps(tmp_path):
    cache = IngestCache(tmp_path / "vol", TARGET_DATES[-1])
    first = FakeSina(fail={"SZ000002"})
    pipeline = OverlayPipeline(sina=first, baostock=FakeBaoStock(), cache=cache, crosscheck_count=2)
    symbols = {"SH600000", "SH600001", "SZ000001", "SZ000002"}
    required = [BASE_DATES[-1], *TARGET_DATES]
    batch = pipeline._fetch_sina(symbols, required_dates=required, base_end=BASE_DATES[-1], asof=TARGET_DATES[-1])
    assert set(batch.series) == symbols - {"SZ000002"}
    second = FakeSina()
    pipeline2 = OverlayPipeline(sina=second, baostock=FakeBaoStock(), cache=cache, crosscheck_count=2)
    batch2 = pipeline2._fetch_sina(symbols, required_dates=required, base_end=BASE_DATES[-1], asof=TARGET_DATES[-1])
    assert second.calls == [["SZ000002"]]
    assert set(batch2.series) == symbols


def test_history_cache_prevents_second_fetch(tmp_path, monkeypatch):
    new_member = "SH600010"
    membership = {market: list(values) for market, values in MARKETS.items()}
    membership["csi300"] = ["SH600000", new_member]
    import data_ingest.pipeline as pipeline_module

    monkeypatch.setattr(
        pipeline_module, "fetch_all_memberships", lambda *args, **kw: memberships(TARGET_DATES[-1], membership)
    )
    base = make_provider(tmp_path / "base", sorted({s for v in MARKETS.values() for s in v} | {new_member}))
    import shutil

    shutil.rmtree(base / "features" / new_member.lower())
    cache = IngestCache(tmp_path / "vol", TARGET_DATES[-1])
    baostock = FakeBaoStock()
    pipeline = OverlayPipeline(
        sina=FakeSina(),
        baostock=baostock,
        cache=cache,
        calendar_dates=list(BASE_DATES) + list(TARGET_DATES),
        crosscheck_count=2,
    )
    pipeline.build(base, tmp_path / "overlay", asof=TARGET_DATES[-1], dates=list(TARGET_DATES), markets=tuple(MARKETS))
    assert baostock.history_calls == [[new_member]]
    # A second build with the same cache must not fetch history again.
    shutil.rmtree(tmp_path / "overlay")
    pipeline2 = OverlayPipeline(
        sina=FakeSina(),
        baostock=baostock,
        cache=cache,
        calendar_dates=list(BASE_DATES) + list(TARGET_DATES),
        crosscheck_count=2,
    )
    pipeline2.build(base, tmp_path / "overlay", asof=TARGET_DATES[-1], dates=list(TARGET_DATES), markets=tuple(MARKETS))
    assert baostock.history_calls == [[new_member]]
