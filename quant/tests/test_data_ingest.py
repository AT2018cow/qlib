"""Unit tests for the fail-closed provider overlay."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from data_ingest.adjust import build_rows
from data_ingest.gate import run_gate
from data_ingest.manifest import canonical_json, sha256_text
from data_ingest.model import Bar, CrossCheck, FetchBatch, Membership, SymbolSeries
from data_ingest.overlay import read_bin_value, read_calendar
from data_ingest.pipeline import OverlayPipeline, PipelineError

BASE_DATES = ("2026-11-03", "2026-11-04")
TARGET_DATES = ("2026-11-05", "2026-11-06")
MARKETS = {
    "csi300": ("SH600000", "SH600001"),
    "csi500": ("SZ000001", "SZ000002"),
    "csi1000": ("SZ300001", "SZ300002"),
}
NEW_MEMBERS = {
    "csi300": ("SH600000", "SH600002"),
    "csi500": ("SZ000001", "SZ000002"),
    "csi1000": ("SZ300001", "SZ300002"),
}
ALL_SYMBOLS = sorted({symbol for values in MARKETS.values() for symbol in values} | {"SH600002"})


def _write_bin(path, values, start=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    np.hstack([[float(start)], values]).astype("<f").tofile(path)


def _make_provider(root):
    root.mkdir(parents=True)
    (root / "calendars").mkdir()
    (root / "calendars" / "day.txt").write_text("\n".join(BASE_DATES) + "\n")
    (root / "calendars" / "day_future.txt").write_text("\n".join(BASE_DATES) + "\n")
    (root / "instruments").mkdir()
    for market, members in MARKETS.items():
        (root / "instruments" / f"{market}.txt").write_text(
            "\n".join(f"{symbol}\t{BASE_DATES[0]}\t{BASE_DATES[-1]}" for symbol in members) + "\n"
        )
    (root / "instruments" / "csi800.txt").write_text(
        "\n".join(f"{symbol}\t{BASE_DATES[0]}\t{BASE_DATES[-1]}" for symbol in MARKETS["csi300"] + MARKETS["csi500"])
        + "\n"
    )
    (root / "instruments" / "all.txt").write_text(
        "\n".join(f"{symbol}\t{BASE_DATES[0]}\t{BASE_DATES[-1]}" for symbol in ALL_SYMBOLS) + "\n"
    )
    for symbol in ALL_SYMBOLS:
        base = 10.0 + ALL_SYMBOLS.index(symbol)
        feature_dir = root / "features" / symbol.lower()
        for date_index, date in enumerate(BASE_DATES):
            values = {
                "open": base,
                "high": base,
                "low": base,
                "close": base,
                "volume": 100.0,
                "amount": base * 100.0,
                "factor": 1.0,
                "vwap": base,
                "change": 0.0 if date_index == 0 else 0.01,
                "adjclose": base,
            }
            if date_index == 0:
                for field, value in values.items():
                    _write_bin(feature_dir / f"{field}.day.bin", [value, value])
            else:
                # First date already wrote both values; keep the same base row.
                pass
    return root


class SyntheticMarket:
    def __init__(self):
        self.raw = {}
        for symbol in ALL_SYMBOLS:
            values = {}
            previous = 10.0 + ALL_SYMBOLS.index(symbol)
            for index, date in enumerate(TARGET_DATES):
                close = previous + 1.0
                values[date] = Bar(
                    date=date,
                    open=close,
                    high=close + 0.5,
                    low=close - 0.5,
                    close=close,
                    volume=1000.0,
                    amount=close * 1000.0,
                    preclose=previous,
                    tradestatus=1,
                    is_st=0,
                )
                previous = close
            self.raw[symbol] = values

    def bad_high(self, symbol):
        self.raw[symbol][TARGET_DATES[0]] = Bar(
            date=TARGET_DATES[0],
            open=1.0,
            high=0.5,
            low=0.1,
            close=1.0,
            volume=100.0,
            amount=100.0,
            preclose=1.0,
            tradestatus=1,
            is_st=0,
        )


class FakeSina:
    def __init__(self, market: SyntheticMarket, fail=(), bad=()):
        self.market = market
        self.fail = set(fail)
        self.bad = set(bad)

    def fetch_pair(self, symbols, start, end, *, on_progress=None, progress_every=200):
        batch = FetchBatch()
        for symbol in symbols:
            if symbol in self.fail:
                batch.errors[symbol] = "fake sina failure"
                continue
            raw = {date: self.market.raw[symbol][date] for date in TARGET_DATES}
            hfq = {date: self.market.raw[symbol][date] for date in TARGET_DATES}
            batch.series[symbol] = SymbolSeries(raw=raw, hfq=hfq, source="fake-sina")
        return batch


class FakeBaoStock:
    def __init__(self, market: SyntheticMarket, mismatch=(), fail=()):
        self.market = market
        self.mismatch = set(mismatch)
        self.fail = set(fail)

    def fetch(self, symbols, start, end, adjust="3"):
        batch = FetchBatch()
        for symbol in symbols:
            if symbol in self.fail:
                batch.errors[symbol] = "fake baostock failure"
                continue
            raw = {}
            for date in TARGET_DATES:
                bar = self.market.raw[symbol][date]
                if symbol in self.mismatch:
                    bar = Bar(
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
                raw[date] = bar
            batch.series[symbol] = SymbolSeries(raw=raw, hfq={}, source="fake-baostock")
        return batch


def _memberships(asof=None):
    return {
        market: Membership(
            market=market,
            asof=asof or TARGET_DATES[-1],
            members=tuple(NEW_MEMBERS[market]),
            raw_sha256="a" * 64,
            weights_date=TARGET_DATES[-1],
            weights_sha256="b" * 64,
            source_url=f"https://example.invalid/{market}",
        )
        for market in MARKETS
    }


def _build(tmp_path, monkeypatch, *, sina=None, baostock=None, **kwargs):
    base = _make_provider(tmp_path / "base")
    import data_ingest.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "fetch_all_memberships", lambda *args, **kw: _memberships())
    market = SyntheticMarket()
    pipeline = OverlayPipeline(
        sina=sina or FakeSina(market),
        baostock=baostock or FakeBaoStock(market),
        calendar_dates=list(BASE_DATES) + list(TARGET_DATES),
        crosscheck_count=3,
    )
    kwargs.setdefault(
        "membership_history",
        {day: _memberships(asof=day) for day in TARGET_DATES},
    )
    manifest = pipeline.build(
        base,
        tmp_path / "overlay",
        asof=TARGET_DATES[-1],
        dates=list(TARGET_DATES),
        markets=tuple(MARKETS),
        **kwargs,
    )
    return base, tmp_path / "overlay", manifest, market


def test_pipeline_builds_overlay_and_passes(tmp_path, monkeypatch):
    base, overlay, manifest, market = _build(tmp_path, monkeypatch)
    assert manifest["status"] == "PASS"
    assert read_calendar(overlay)[-2:] == list(TARGET_DATES)
    assert read_bin_value(base, "SH600000", "close", BASE_DATES[-1]) == pytest.approx(
        read_bin_value(overlay, "SH600000", "close", BASE_DATES[-1]), abs=1e-6
    )
    expected = market.raw["SH600000"][TARGET_DATES[-1]].close
    assert read_bin_value(overlay, "SH600000", "close", TARGET_DATES[-1]) == pytest.approx(expected, rel=1e-6)
    assert "SH600002" in (overlay / "instruments" / "csi300.txt").read_text()
    assert "SH600001\t2026-11-03\t2026-11-04" in (overlay / "instruments" / "csi300.txt").read_text()


def test_pipeline_rejects_missing_coverage(tmp_path, monkeypatch):
    market = SyntheticMarket()
    sina = FakeSina(market, fail={"SH600000"})
    baostock = FakeBaoStock(market, fail={"SH600000"})
    with pytest.raises(PipelineError, match="coverage"):
        _build(tmp_path, monkeypatch, sina=sina, baostock=baostock)


def test_pipeline_rejects_bad_ohlc(tmp_path, monkeypatch):
    market = SyntheticMarket()
    market.bad_high("SH600000")
    with pytest.raises(PipelineError, match="ohlc"):
        _build(tmp_path, monkeypatch, sina=FakeSina(market), baostock=FakeBaoStock(market))


def test_pipeline_rejects_crosscheck_mismatch(tmp_path, monkeypatch):
    market = SyntheticMarket()
    with pytest.raises(PipelineError, match="crosscheck"):
        _build(
            tmp_path,
            monkeypatch,
            sina=FakeSina(market),
            baostock=FakeBaoStock(market, mismatch={"SH600000"}),
        )


def test_corporate_action_gate_reads_materialized_factor(tmp_path, monkeypatch):
    """S03: the gate must reject a bad output bin even when the in-memory row was correct."""
    base, overlay, _, _ = _build(tmp_path, monkeypatch)
    factor_bin = overlay / "features" / "sh600000" / "factor.day.bin"
    payload = np.fromfile(factor_bin, dtype="<f")
    start = int(payload[0])
    target_index = read_calendar(overlay).index(TARGET_DATES[-1])
    payload[target_index - start + 1] = 2.0
    payload.astype("<f").tofile(factor_bin)

    rows = json.loads((overlay / "new_rows.json").read_text())
    report = run_gate(
        base,
        overlay,
        base_end=BASE_DATES[-1],
        asof=TARGET_DATES[-1],
        dates=list(TARGET_DATES),
        memberships=_memberships(),
        rows=rows,
        corporate_actions=[
            {
                "symbol": "SH600000",
                "date": TARGET_DATES[-1],
                "expected_factor": 1.0,
                "actual_factor": 1.0,
            }
        ],
        require_crosscheck=False,
        membership_history={day: _memberships(asof=day) for day in TARGET_DATES},
    )

    assert report.status == "FAIL"
    assert any("corporate_actions" in failure for failure in report.failures)


def test_build_rows_normal_day_and_ex_date():
    base = {"SH600000": {"close": 10.0, "adjclose": 10.0, "factor": 1.0}}
    normal = Bar("2026-11-05", 10.0, 10.5, 9.5, 10.0, 1000.0, 10000.0)
    ex_date = Bar("2026-11-06", 9.0, 9.5, 8.5, 9.0, 1000.0, 9000.0)
    series = {
        "SH600000": SymbolSeries(
            raw={"2026-11-05": normal, "2026-11-06": ex_date},
            hfq={"2026-11-05": normal, "2026-11-06": ex_date},
        )
    }
    rows = build_rows(
        base,
        series,
        ["2026-11-05", "2026-11-06"],
        base_end="2026-11-04",
        preclose={("SH600000", "2026-11-06"): 10.0},
    )["SH600000"]
    assert rows["2026-11-05"]["close"] == pytest.approx(10.0)
    assert rows["2026-11-05"]["factor"] == pytest.approx(1.0)
    assert rows["2026-11-06"]["close"] == pytest.approx(9.0)
    assert rows["2026-11-06"]["factor"] == pytest.approx(1.0)
    assert rows["2026-11-06"]["change"] == pytest.approx(9.0 / 10.0 - 1.0)
    assert rows["2026-11-05"]["volume"] == pytest.approx(10.0)


def test_build_rows_uses_dated_anchor_and_bridges_missing_frozen_session():
    """S02: an old anchor cannot treat a multi-session rise as an ex-date."""
    anchor = {
        "SH600000": {
            "date": "2026-11-03",
            "close": 1.0,
            "adjclose": 1.0,
            "factor": 0.1,
        }
    }
    bridge = Bar("2026-11-04", 11.0, 11.0, 11.0, 11.0, 1000.0, 11000.0)
    target = Bar("2026-11-05", 12.0, 12.0, 12.0, 12.0, 1000.0, 12000.0)
    anchor_raw = Bar("2026-11-03", 10.0, 10.0, 10.0, 10.0, 1000.0, 10000.0)
    source = SymbolSeries(
        raw={bar.date: bar for bar in (anchor_raw, bridge, target)},
        hfq={bar.date: bar for bar in (anchor_raw, bridge, target)},
    )
    rows = build_rows(
        {},
        {"SH600000": source},
        ["2026-11-05"],
        base_end="2026-11-04",
        base_anchors=anchor,
        preclose={
            ("SH600000", "2026-11-04"): 10.0,
            ("SH600000", "2026-11-05"): 11.0,
        },
    )["SH600000"]
    assert rows["2026-11-05"]["close"] == pytest.approx(1.2)
    assert rows["2026-11-05"]["adjclose"] == pytest.approx(1.2)
    assert rows["2026-11-05"]["factor"] == pytest.approx(0.1)


@pytest.mark.parametrize("coordinate_factor", [1e-6, 0.01, 1.0])
def test_build_rows_hfq_cash_dividend_is_coordinate_scale_invariant(coordinate_factor):
    symbol = "SH600000"
    base_day, target_day = "2026-11-04", "2026-11-05"

    def bar(day, price):
        return Bar(day, price, price, price, price, 1000.0, price * 1000.0)

    rows = build_rows(
        {symbol: {"close": 100.0 * coordinate_factor, "adjclose": 200.0, "factor": coordinate_factor}},
        {
            symbol: SymbolSeries(
                raw={base_day: bar(base_day, 100.0), target_day: bar(target_day, 99.5)},
                hfq={base_day: bar(base_day, 200.0), target_day: bar(target_day, 200.0)},
            )
        },
        [target_day],
        base_end=base_day,
    )[symbol]
    assert rows[target_day]["close"] == pytest.approx(100.0 * coordinate_factor, rel=1e-12, abs=0.0)
    assert rows[target_day]["change"] == pytest.approx(0.0, abs=1e-12)


@pytest.mark.parametrize("expected_factor", [1e-6, 0.01, 1.0])
def test_corporate_action_gate_uses_relative_factor_tolerance(tmp_path, monkeypatch, expected_factor):
    base, overlay, _, _ = _build(tmp_path, monkeypatch)
    factor_bin = overlay / "features" / "sh600000" / "factor.day.bin"
    payload = np.fromfile(factor_bin, dtype="<f")
    target_index = read_calendar(overlay).index(TARGET_DATES[-1])
    payload[target_index - int(payload[0]) + 1] = expected_factor * 0.995
    payload.astype("<f").tofile(factor_bin)
    rows = json.loads((overlay / "new_rows.json").read_text())
    report = run_gate(
        base,
        overlay,
        base_end=BASE_DATES[-1],
        asof=TARGET_DATES[-1],
        dates=list(TARGET_DATES),
        memberships=_memberships(),
        rows=rows,
        corporate_actions=[{"symbol": "SH600000", "date": TARGET_DATES[-1], "expected_factor": expected_factor}],
        require_crosscheck=False,
        membership_history={day: _memberships(asof=day) for day in TARGET_DATES},
    )
    assert report.status == "FAIL"
    assert any("corporate_actions" in failure for failure in report.failures)


def test_canonical_json_and_crosscheck_status():
    assert sha256_text(canonical_json({"b": 1, "a": 2})) == sha256_text(canonical_json({"a": 2, "b": 1}))
    assert CrossCheck("SH600000", "2026-11-06", {"close": (1.0, 1.0)}).status == "PASS"
    assert CrossCheck("SH600000", "2026-11-06", {"close": (1.0, 1.1)}).status == "FAIL"
    assert math.isfinite(1.0)
