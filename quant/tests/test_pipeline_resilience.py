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
        self.ranges = []

    def fetch_pair(self, symbols, start, end, *, on_progress=None, progress_every=200):
        self.calls.append(sorted(symbols))
        self.ranges.append((start, end))
        batch = FetchBatch()
        available = (*BASE_DATES, *self.dates) if self.with_base else self.dates
        dates = tuple(dict.fromkeys(date for date in available if start <= date <= end))
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


def default_history():
    return {day: memberships(day) for day in TARGET_DATES}


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
    kwargs.setdefault("membership_history", default_history())
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
        membership_history=default_history(),
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
        membership_history={day: memberships(day, membership) for day in TARGET_DATES},
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
    pipeline.build(
        base,
        tmp_path / "overlay",
        asof=TARGET_DATES[-1],
        dates=list(TARGET_DATES),
        markets=tuple(MARKETS),
        membership_history={day: memberships(day, membership) for day in TARGET_DATES},
    )
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
    pipeline2.build(
        base,
        tmp_path / "overlay",
        asof=TARGET_DATES[-1],
        dates=list(TARGET_DATES),
        markets=tuple(MARKETS),
        membership_history={day: memberships(day, membership) for day in TARGET_DATES},
    )
    assert baostock.history_calls == [[new_member]]


def test_cached_sina_requires_finite_hfq_for_all_required_dates(tmp_path):
    from data_ingest.cache import IngestCache
    from data_ingest.model import Bar, FetchBatch, SymbolSeries

    cache = IngestCache(tmp_path / "vol", TARGET_DATES[-1])
    bad = FetchBatch()
    raw = _bars("SH600000", [BASE_DATES[-1], *TARGET_DATES])
    hfq = {date: Bar(date=date, open=1, high=1, low=1, close=float("nan"), volume=0, amount=0) for date in raw}
    bad.series["SH600000"] = SymbolSeries(raw=raw, hfq=hfq, source="bad")
    cache.append_sina(bad, batch_index=1)
    pipeline = OverlayPipeline(sina=FakeSina(), baostock=FakeBaoStock(), cache=cache, crosscheck_count=2)
    cached, complete = pipeline._cached_sina([BASE_DATES[-1], *TARGET_DATES], {"SH600000"})
    assert "SH600000" in cached.series
    assert complete == set()


def test_bad_hfq_cache_is_refetched_and_then_reused(tmp_path):
    from data_ingest.cache import IngestCache
    from data_ingest.model import Bar, FetchBatch, SymbolSeries

    cache = IngestCache(tmp_path / "vol", TARGET_DATES[-1])
    bad = FetchBatch()
    raw = _bars("SH600000", [BASE_DATES[-1], *TARGET_DATES])
    hfq = {date: Bar(date=date, open=1, high=1, low=1, close=float("nan"), volume=0, amount=0) for date in raw}
    bad.series["SH600000"] = SymbolSeries(raw=raw, hfq=hfq, source="bad")
    cache.append_sina(bad, batch_index=1)
    first = FakeSina()
    pipeline = OverlayPipeline(sina=first, baostock=FakeBaoStock(), cache=cache, crosscheck_count=2)
    symbols = {"SH600000"}
    required = [BASE_DATES[-1], *TARGET_DATES]
    result = pipeline._fetch_sina(symbols, required_dates=required, base_end=BASE_DATES[-1], asof=TARGET_DATES[-1])
    assert first.calls == [["SH600000"]]
    assert result.series["SH600000"].hfq[TARGET_DATES[-1]].close > 0
    second = FakeSina()
    pipeline2 = OverlayPipeline(sina=second, baostock=FakeBaoStock(), cache=cache, crosscheck_count=2)
    result2 = pipeline2._fetch_sina(symbols, required_dates=required, base_end=BASE_DATES[-1], asof=TARGET_DATES[-1])
    assert second.calls == []
    assert result2.series["SH600000"].hfq[TARGET_DATES[-1]].close > 0


@pytest.mark.parametrize("seed_bad_cache", [False, True])
def test_final_inputs_fail_when_hfq_and_baostock_adjustment_evidence_are_both_missing(
    tmp_path, monkeypatch, seed_bad_cache
):
    """S03: fresh-but-still-bad data cannot bypass adjustment validation."""
    from data_ingest.manifest import sha256_text

    symbols = sorted({symbol for values in MARKETS.values() for symbol in values})
    sample = set(sorted(symbols, key=sha256_text)[:3])
    victim = next(symbol for symbol in symbols if symbol not in sample)

    class MissingHfqSina(FakeSina):
        def fetch_pair(self, symbols, start, end, *, on_progress=None, progress_every=200):
            batch = super().fetch_pair(symbols, start, end, on_progress=None, progress_every=progress_every)
            if victim in batch.series:
                series = batch.series[victim]
                batch.series[victim] = SymbolSeries(raw=series.raw, hfq={}, source="fresh-missing-hfq")
            return batch

    cache = IngestCache(tmp_path / "vol", TARGET_DATES[-1]) if seed_bad_cache else None
    if cache is not None:
        bad = FetchBatch()
        raw = _bars(victim, [BASE_DATES[-1], *TARGET_DATES])
        bad.series[victim] = SymbolSeries(raw=raw, hfq={}, source="cached-missing-hfq")
        cache.append_sina(bad, batch_index=1)

    with pytest.raises(PipelineError, match="adjustment evidence"):
        build(
            tmp_path,
            monkeypatch,
            sina=MissingHfqSina(),
            baostock=FakeBaoStock(fail={victim}),
            cache=cache,
        )


def test_baostock_preclose_is_recorded_when_hfq_is_missing(tmp_path, monkeypatch):
    """S03: a valid dated fallback may pass, but its source stays auditable."""
    from data_ingest.manifest import sha256_text

    symbols = sorted({symbol for values in MARKETS.values() for symbol in values})
    sample = set(sorted(symbols, key=sha256_text)[:3])
    victim = next(symbol for symbol in symbols if symbol not in sample)

    class MissingHfqSina(FakeSina):
        def fetch_pair(self, symbols, start, end, *, on_progress=None, progress_every=200):
            batch = super().fetch_pair(symbols, start, end, on_progress=None, progress_every=progress_every)
            if victim in batch.series:
                series = batch.series[victim]
                batch.series[victim] = SymbolSeries(raw=series.raw, hfq={}, source="fresh-missing-hfq")
            return batch

    _, _, manifest = build(tmp_path, monkeypatch, sina=MissingHfqSina(), baostock=FakeBaoStock())
    evidence = manifest["sources"]["adjustment_evidence"][victim]
    assert set(evidence) == set(TARGET_DATES)
    assert set(evidence.values()) == {"baostock_preclose"}


def test_partial_primary_raw_is_merged_by_symbol_and_date_from_fallback(tmp_path, monkeypatch):
    """S03: a symbol-level hit cannot hide one missing target-date raw bar."""
    victim = "SH600000"

    class PartialRawSina(FakeSina):
        def fetch_pair(self, symbols, start, end, *, on_progress=None, progress_every=200):
            batch = super().fetch_pair(symbols, start, end, on_progress=None, progress_every=progress_every)
            series = batch.series[victim]
            raw = dict(series.raw)
            raw.pop(TARGET_DATES[-1])
            batch.series[victim] = SymbolSeries(raw=raw, hfq=series.hfq, source="partial-primary")
            return batch

    _, overlay, manifest = build(tmp_path, monkeypatch, sina=PartialRawSina(), baostock=FakeBaoStock())
    assert manifest["gate"]["status"] == "PASS"
    assert math.isfinite(read_bin_value(overlay, victim, "close", TARGET_DATES[-1]))


def test_cache_contract_was_bumped_for_hfq_completeness():
    from data_ingest.cache import CACHE_CONTRACT, IngestCache

    assert CACHE_CONTRACT == "ingest-cache-v2"
    cache = IngestCache("/tmp/ignored", "2026-11-10")
    assert cache.directory.name == "ingest-cache-v2"


def _set_base_row_nan(
    base,
    symbol,
    *,
    all_rows=False,
    fields=("open", "high", "low", "close", "volume", "amount", "factor", "vwap", "change", "adjclose"),
):
    import numpy as np

    for field in fields:
        path = base / "features" / symbol.lower() / f"{field}.day.bin"
        data = np.fromfile(path, dtype="<f")
        if all_rows:
            data[1:] = np.nan
        else:
            data[-1] = np.nan
        data.tofile(path)


def _assert_all_prefixes_preserved(base, overlay, base_end):
    import numpy as np

    base_calendar = [line.strip() for line in (base / "calendars" / "day.txt").read_text().splitlines() if line.strip()]
    end_index = base_calendar.index(base_end)
    for feature_dir in (base / "features").iterdir():
        if not feature_dir.is_dir():
            continue
        for path in feature_dir.glob("*.day.bin"):
            left = np.fromfile(path, dtype="<f")
            right = np.fromfile(overlay / "features" / feature_dir.name / path.name, dtype="<f")
            assert left.size and right.size
            start = int(left[0])
            assert int(right[0]) == start
            count = end_index - start + 1
            assert count + 1 <= left.size and count + 1 <= right.size
            assert np.array_equal(left[1 : count + 1], right[1 : count + 1], equal_nan=True), (
                feature_dir.name,
                path.name,
            )


class SuspendedBaoStock(FakeBaoStock):
    """Target bars are zero-volume suspension evidence; history stops at base."""

    def __init__(self, suspend=("SH600000",), **kwargs):
        super().__init__(**kwargs)
        self.suspend = set(suspend)

    def fetch(self, symbols, start, end, adjust="3", **kwargs):
        self.fetch_calls.append(sorted(symbols))
        batch = FetchBatch()
        for symbol in sorted(symbols):
            raw = _bars(symbol, [date for date in (BASE_DATES[-1], *TARGET_DATES) if start <= date <= end])
            if symbol in self.suspend:
                raw = {
                    date: Bar(
                        date=bar.date,
                        open=bar.open,
                        high=bar.high,
                        low=bar.low,
                        close=bar.close,
                        volume=0.0,
                        amount=0.0,
                        preclose=bar.preclose,
                        tradestatus=0,
                        is_st=0,
                    )
                    for date, bar in raw.items()
                }
            batch.series[symbol] = SymbolSeries(raw=raw, hfq={}, source="fake-baostock-suspended")
        return batch

    def fetch_history(self, symbols, start, end, **kwargs):
        self.history_calls.append(sorted(symbols))
        output = {}
        for symbol in sorted(symbols):
            raw = _bars(symbol, BASE_DATES)
            output[symbol] = {"raw": raw, "hfq_close": {date: bar.close for date, bar in raw.items()}}
        return output


def test_existing_stock_with_nan_base_row_resumes_from_last_anchor(tmp_path, monkeypatch):
    symbol = "SH600000"
    import data_ingest.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "fetch_all_memberships", lambda *args, **kw: memberships(TARGET_DATES[-1]))
    base = make_provider(tmp_path / "base", sorted({s for v in MARKETS.values() for s in v}))
    _set_base_row_nan(base, symbol)
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
        membership_history=default_history(),
    )
    assert symbol in manifest["history_backfill"]["existing_anchor"]
    assert symbol not in manifest["history_backfill"]["symbols"]
    assert sina.ranges == [(BASE_DATES[0], TARGET_DATES[-1])]
    assert math.isfinite(read_bin_value(tmp_path / "overlay", symbol, "close", TARGET_DATES[-1]))
    _assert_all_prefixes_preserved(base, tmp_path / "overlay", BASE_DATES[-1])


def test_adjustment_evidence_handles_multiple_actions_and_suspension_bridge():
    symbol = "SH600000"
    anchor = {symbol: {"date": "2026-11-03", "close": 1.0, "adjclose": 1.0, "factor": 0.1}}

    def bar(date, close, *, volume=1000.0):
        return Bar(date, close, close, close, close, volume, close * volume, tradestatus=1)

    actions = SymbolSeries(
        raw={"2026-11-04": bar("2026-11-04", 5.0), "2026-11-05": bar("2026-11-05", 3.0)},
        hfq={},
        source="raw-only",
    )
    factors, sources, failures = OverlayPipeline._adjustment_evidence(
        anchor,
        {symbol: actions},
        {symbol: ["2026-11-03", "2026-11-04", "2026-11-05"]},
        {(symbol, "2026-11-04"): 5.0, (symbol, "2026-11-05"): 2.5},
        {},
    )
    assert failures == []
    assert factors[(symbol, "2026-11-04")] == pytest.approx(0.2)
    assert factors[(symbol, "2026-11-05")] == pytest.approx(0.4)
    assert set(sources[symbol].values()) == {"baostock_preclose"}

    resumed = SymbolSeries(raw={"2026-11-05": bar("2026-11-05", 11.0)}, hfq={}, source="raw-only")
    factors, sources, failures = OverlayPipeline._adjustment_evidence(
        anchor,
        {symbol: resumed},
        {symbol: ["2026-11-03", "2026-11-04", "2026-11-05"]},
        {(symbol, "2026-11-05"): 10.0},
        {symbol: {"2026-11-04"}},
    )
    assert failures == []
    assert factors[(symbol, "2026-11-05")] == pytest.approx(0.1)
    assert sources[symbol]["2026-11-04"] == "baostock_suspension"


def test_adjustment_evidence_rejects_unexplained_anchor_bridge_gap():
    symbol = "SH600000"
    anchor = {symbol: {"date": "2026-11-03", "close": 1.0, "adjclose": 1.0, "factor": 0.1}}
    target = Bar("2026-11-05", 11.0, 11.0, 11.0, 11.0, 1000.0, 11000.0)
    _, _, failures = OverlayPipeline._adjustment_evidence(
        anchor,
        {symbol: SymbolSeries(raw={target.date: target}, hfq={}, source="incomplete")},
        {symbol: ["2026-11-03", "2026-11-04", "2026-11-05"]},
        {(symbol, "2026-11-05"): 10.0},
        {},
    )
    assert any("2026-11-04" in failure for failure in failures)


def _dividend_evidence(coordinate_factor, *, target_hfq=200.0, preclose=None):
    symbol = "SH600000"
    base_day, target_day = BASE_DATES[-1], TARGET_DATES[0]
    anchor = {
        symbol: {
            "date": base_day,
            "close": 100.0 * coordinate_factor,
            "adjclose": 200.0,
            "factor": coordinate_factor,
        }
    }

    def bar(day, price):
        return Bar(day, price, price, price, price, 1000.0, price * 1000.0)

    source = SymbolSeries(
        raw={base_day: bar(base_day, 100.0), target_day: bar(target_day, 99.5)},
        hfq={base_day: bar(base_day, 200.0), target_day: bar(target_day, target_hfq)},
        source="dividend-test",
    )
    return OverlayPipeline._adjustment_evidence(
        anchor,
        {symbol: source},
        {symbol: [base_day, target_day]},
        {} if preclose is None else {(symbol, target_day): preclose},
        {},
    )


@pytest.mark.parametrize("coordinate_factor", [1e-6, 0.001, 0.01, 1.0, 100.0])
def test_hfq_cash_dividend_adjustment_is_coordinate_scale_invariant(coordinate_factor):
    factors, sources, failures = _dividend_evidence(coordinate_factor)
    key = ("SH600000", TARGET_DATES[0])
    assert failures == []
    assert factors[key] == pytest.approx(coordinate_factor * 100.0 / 99.5, rel=1e-12, abs=0.0)
    assert 99.5 * factors[key] / (100.0 * coordinate_factor) == pytest.approx(1.0, rel=1e-12)
    assert sources[key[0]][key[1]] == "sina_hfq"


@pytest.mark.parametrize("coordinate_factor", [1e-6, 0.01, 1.0])
@pytest.mark.parametrize("target_hfq", [199.0, 200.0, 201.0])
def test_precise_preclose_takes_priority_over_present_hfq(coordinate_factor, target_hfq):
    factors, sources, failures = _dividend_evidence(coordinate_factor, target_hfq=target_hfq, preclose=99.5)
    key = ("SH600000", TARGET_DATES[0])
    assert failures == []
    assert factors[key] == pytest.approx(coordinate_factor * 100.0 / 99.5, rel=1e-12, abs=0.0)
    assert sources[key[0]][key[1]] == "baostock_preclose"


@pytest.mark.parametrize("invalid_preclose", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_preclose_does_not_displace_valid_hfq(invalid_preclose):
    factors, sources, failures = _dividend_evidence(0.01, preclose=invalid_preclose)
    key = ("SH600000", TARGET_DATES[0])
    assert failures == []
    assert factors[key] == pytest.approx(1.0 / 99.5, rel=1e-12, abs=0.0)
    assert sources[key[0]][key[1]] == "sina_hfq"


def test_hfq_cannot_undo_a_precise_adjustment_after_a_source_conflict():
    symbol = "SH600000"
    base_day, first, second = BASE_DATES[-1], *TARGET_DATES

    def bar(day, price):
        return Bar(day, price, price, price, price, 1000.0, price * 1000.0)

    factors, sources, failures = OverlayPipeline._adjustment_evidence(
        {symbol: {"date": base_day, "close": 1.0, "adjclose": 200.0, "factor": 0.01}},
        {
            symbol: SymbolSeries(
                raw={base_day: bar(base_day, 100.0), first: bar(first, 99.5), second: bar(second, 100.0)},
                hfq={base_day: bar(base_day, 200.0), first: bar(first, 199.0), second: bar(second, 200.0)},
            )
        },
        {symbol: [base_day, first, second]},
        {(symbol, first): 99.5},
        {},
    )
    assert factors[(symbol, first)] == pytest.approx(1.0 / 99.5)
    assert sources[symbol][first] == "baostock_preclose"
    assert (symbol, second) not in factors
    assert any(second in failure and "conflicting hfq" in failure for failure in failures)


@pytest.mark.parametrize("coordinate_factor", [1e-6, 0.01, 1.0])
def test_hfq_rounding_noise_keeps_the_existing_factor_at_every_scale(coordinate_factor):
    # The hfq/raw ratio has only 1 ppm noise, below the 1e-4 relative threshold.
    factors, sources, failures = _dividend_evidence(coordinate_factor, target_hfq=199.0 * (1.0 + 1e-6))
    key = ("SH600000", TARGET_DATES[0])
    assert failures == []
    assert factors[key] == coordinate_factor
    assert sources[key[0]][key[1]] == "sina_hfq"


def test_precise_row_can_reestablish_hfq_agreement_after_a_conflict():
    symbol = "SH600000"
    base_day, first, second, third = "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"

    def bar(day, price):
        return Bar(day, price, price, price, price, 1000.0, price * 1000.0)

    raw_prices = {base_day: 100.0, first: 99.5, second: 100.0, third: 101.0}
    precise_factor = 1.0 / 99.5
    hfq_prices = {
        base_day: 200.0,
        first: 199.0,  # stale on the ex-date
        second: raw_prices[second] * 200.0 / 99.5,
        third: raw_prices[third] * 200.0 / 99.5,
    }
    factors, sources, failures = OverlayPipeline._adjustment_evidence(
        {symbol: {"date": base_day, "close": 1.0, "adjclose": 200.0, "factor": 0.01}},
        {
            symbol: SymbolSeries(
                raw={day: bar(day, price) for day, price in raw_prices.items()},
                hfq={day: bar(day, price) for day, price in hfq_prices.items()},
            )
        },
        {symbol: [base_day, first, second, third]},
        {(symbol, first): 99.5, (symbol, second): 99.5},
        {},
    )
    assert failures == []
    assert [factors[(symbol, day)] for day in (first, second, third)] == pytest.approx([precise_factor] * 3)
    assert sources[symbol] == {first: "baostock_preclose", second: "baostock_preclose", third: "sina_hfq"}


@pytest.mark.parametrize("coordinate_factor", [1e-6, 0.01, 1.0])
@pytest.mark.parametrize("precise_preclose", [False, True])
def test_provider_dividend_rows_preserve_value_and_record_adjustment_source(
    tmp_path, coordinate_factor, precise_preclose
):
    from data_ingest.adjust import FIELDS
    from data_ingest.manifest import sha256_text

    symbols = sorted({s for values in MARKETS.values() for s in values})
    sample = set(sorted(symbols, key=sha256_text)[:3])
    victim = sorted(sample)[0] if precise_preclose else next(s for s in symbols if s not in sample)
    base = make_provider(tmp_path / "base", symbols)
    prices = {"open", "high", "low", "close", "vwap"}
    for field in FIELDS:
        if field in prices:
            value = 100.0 * coordinate_factor
        elif field == "factor":
            value = coordinate_factor
        elif field == "adjclose":
            value = 200.0
        elif field == "volume":
            value = 1000.0 / 100.0 / coordinate_factor
        elif field == "amount":
            value = 100.0
        else:
            value = 0.0
        _write_bin(base / "features" / victim.lower() / f"{field}.day.bin", [value, value])

    base_day, target_day = BASE_DATES[-1], TARGET_DATES[0]

    def bar(day, price, preclose=None):
        return Bar(day, price, price, price, price, 1000.0, price * 1000.0, preclose=preclose)

    class DividendSina(FakeSina):
        def fetch_pair(self, *args, **kwargs):
            batch = super().fetch_pair(*args, **kwargs)
            batch.series[victim] = SymbolSeries(
                raw={base_day: bar(base_day, 100.0), target_day: bar(target_day, 99.5)},
                hfq={
                    base_day: bar(base_day, 200.0),
                    target_day: bar(target_day, 199.0 if precise_preclose else 200.0),
                },
            )
            return batch

    class DividendBao(FakeBaoStock):
        def fetch(self, *args, **kwargs):
            batch = super().fetch(*args, **kwargs)
            if victim in batch.series:
                batch.series[victim] = SymbolSeries(raw={target_day: bar(target_day, 99.5, preclose=99.5)}, hfq={})
            return batch

    pipeline = OverlayPipeline(
        sina=DividendSina(dates=(target_day,)),
        baostock=DividendBao(fail=() if precise_preclose else (victim,)),
        crosscheck_count=3,
        membership_provider=lambda markets: memberships(target_day),
    )
    manifest = pipeline.build(base, tmp_path / "overlay", asof=target_day, dates=[target_day], markets=tuple(MARKETS))
    expected_source = "baostock_preclose" if precise_preclose else "sina_hfq"
    assert manifest["status"] == "PASS"
    assert manifest["sources"]["adjustment_evidence"][victim][target_day] == expected_source
    assert read_bin_value(tmp_path / "overlay", victim, "close", target_day) == pytest.approx(
        100.0 * coordinate_factor, rel=1e-6, abs=0.0
    )
    assert read_bin_value(tmp_path / "overlay", victim, "factor", target_day) == pytest.approx(
        coordinate_factor * 100.0 / 99.5, rel=1e-6, abs=0.0
    )
    _assert_all_prefixes_preserved(base, tmp_path / "overlay", base_day)


def test_confirmed_suspension_with_existing_history_is_not_rebuilt(tmp_path, monkeypatch):
    symbol = "SH600000"
    import data_ingest.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "fetch_all_memberships", lambda *args, **kw: memberships(TARGET_DATES[-1]))
    base = make_provider(tmp_path / "base", sorted({s for v in MARKETS.values() for s in v}))
    _set_base_row_nan(base, symbol)
    pipeline = OverlayPipeline(
        sina=FakeSina(fail={symbol}),
        baostock=SuspendedBaoStock(),
        calendar_dates=list(BASE_DATES) + list(TARGET_DATES),
        crosscheck_count=3,
    )
    manifest = pipeline.build(
        base,
        tmp_path / "overlay",
        asof=TARGET_DATES[-1],
        dates=list(TARGET_DATES),
        markets=tuple(MARKETS),
        membership_history=default_history(),
    )
    assert symbol in manifest["history_backfill"]["existing_anchor"]
    assert symbol not in manifest["history_backfill"]["symbols"]
    _assert_all_prefixes_preserved(base, tmp_path / "overlay", BASE_DATES[-1])


def test_existing_history_without_any_anchor_fails_closed(tmp_path, monkeypatch):
    symbol = "SH600000"
    import data_ingest.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "fetch_all_memberships", lambda *args, **kw: memberships(TARGET_DATES[-1]))
    base = make_provider(tmp_path / "base", sorted({s for v in MARKETS.values() for s in v}))
    _set_base_row_nan(base, symbol, all_rows=True)
    pipeline = OverlayPipeline(
        sina=FakeSina(),
        baostock=FakeBaoStock(),
        calendar_dates=list(BASE_DATES) + list(TARGET_DATES),
        crosscheck_count=3,
    )
    with pytest.raises(PipelineError, match="cannot be continued safely"):
        pipeline.build(
            base,
            tmp_path / "overlay",
            asof=TARGET_DATES[-1],
            dates=list(TARGET_DATES),
            markets=tuple(MARKETS),
            membership_history=default_history(),
        )


def test_true_new_member_still_uses_controlled_backfill(tmp_path, monkeypatch):
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
        membership_history={day: memberships(day, membership) for day in TARGET_DATES},
    )
    assert manifest["history_backfill"]["symbols"] == [new_member]
    assert manifest["history_backfill"]["existing_anchor"] == []
    assert math.isfinite(read_bin_value(tmp_path / "overlay", new_member, "close", BASE_DATES[-1]))
    _assert_all_prefixes_preserved(base, tmp_path / "overlay", BASE_DATES[-1])


def test_multi_day_gap_without_per_day_evidence_fails_closed(tmp_path, monkeypatch):
    import data_ingest.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "fetch_all_memberships", lambda *args, **kw: memberships(TARGET_DATES[-1]))
    base = make_provider(tmp_path / "base", sorted({s for v in MARKETS.values() for s in v}))
    pipeline = OverlayPipeline(
        sina=FakeSina(),
        baostock=FakeBaoStock(),
        calendar_dates=list(BASE_DATES) + list(TARGET_DATES),
        crosscheck_count=3,
    )
    with pytest.raises(PipelineError, match="multi-day gap requires per-day membership evidence"):
        pipeline.build(
            base,
            tmp_path / "overlay",
            asof=TARGET_DATES[-1],
            dates=list(TARGET_DATES),
            markets=tuple(MARKETS),
        )


def test_multi_day_with_per_day_evidence_builds_daily_ranges(tmp_path):
    day_one = {
        "csi300": ("SH600000", "SH600001"),
        "csi500": ("SZ000001", "SZ000002"),
        "csi1000": ("SZ300001", "SZ300002"),
    }
    day_two = {
        "csi300": ("SH600000", "SZ000001"),
        "csi500": ("SZ000002", "SZ300001"),
        "csi1000": ("SZ300001", "SZ300002"),
    }
    history = {
        TARGET_DATES[0]: memberships(TARGET_DATES[0], day_one),
        TARGET_DATES[1]: memberships(TARGET_DATES[1], day_two),
    }
    base = make_provider(tmp_path / "base", sorted({s for v in MARKETS.values() for s in v}))
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
        membership_history=history,
    )
    assert manifest["membership_policy"]["mode"] == "MULTI_DAY_EVIDENCE"
    csi300 = (tmp_path / "overlay" / "instruments" / "csi300.txt").read_text()
    assert "SH600001\t2026-11-03\t2026-11-05" in csi300
    assert "SZ000001\t2026-11-06\t2026-11-06" in csi300
    csi500 = (tmp_path / "overlay" / "instruments" / "csi500.txt").read_text()
    assert "SZ000001\t2026-11-03\t2026-11-05" in csi500
    assert "SZ300001\t2026-11-06\t2026-11-06" in csi500
    assert manifest["gate"]["status"] == "PASS"


def test_multi_day_evidence_without_change_extends_ranges(tmp_path):
    same = {market: tuple(values) for market, values in MARKETS.items()}
    history = {
        TARGET_DATES[0]: memberships(TARGET_DATES[0], same),
        TARGET_DATES[1]: memberships(TARGET_DATES[1], same),
    }
    base = make_provider(tmp_path / "base", sorted({s for v in MARKETS.values() for s in v}))
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
        membership_history=history,
    )
    csi300 = (tmp_path / "overlay" / "instruments" / "csi300.txt").read_text()
    assert "SH600000\t2026-11-03\t2026-11-06" in csi300
    assert manifest["gate"]["status"] == "PASS"


@pytest.mark.parametrize(
    "history_mutator",
    [
        lambda history: {TARGET_DATES[0]: history[TARGET_DATES[0]]},
        lambda history: {
            TARGET_DATES[0]: history[TARGET_DATES[0]],
            TARGET_DATES[1]: memberships(TARGET_DATES[0], {m: tuple(v) for m, v in MARKETS.items()}),
        },
        lambda history: {
            TARGET_DATES[0]: {k: v for k, v in history[TARGET_DATES[0]].items() if k != "csi300"},
            TARGET_DATES[1]: history[TARGET_DATES[1]],
        },
    ],
)
def test_malformed_multi_day_evidence_fails_closed(tmp_path, history_mutator):
    history = {
        TARGET_DATES[0]: memberships(TARGET_DATES[0]),
        TARGET_DATES[1]: memberships(TARGET_DATES[1]),
    }
    base = make_provider(tmp_path / "base", sorted({s for v in MARKETS.values() for s in v}))
    pipeline = OverlayPipeline(
        sina=FakeSina(),
        baostock=FakeBaoStock(),
        calendar_dates=list(BASE_DATES) + list(TARGET_DATES),
        crosscheck_count=3,
    )
    with pytest.raises(PipelineError, match="membership history"):
        pipeline.build(
            base,
            tmp_path / "overlay",
            asof=TARGET_DATES[-1],
            dates=list(TARGET_DATES),
            markets=tuple(MARKETS),
            membership_history=history_mutator(history),
        )
