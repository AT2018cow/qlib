"""End-to-end provider overlay pipeline (no publication)."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable, Mapping

from data_ingest.adjust import build_rows
from data_ingest.baostock_source import BaoStockSource
from data_ingest.calendar import read_calendar, resolve_target_dates
from data_ingest.csindex import fetch_all_memberships
from data_ingest.gate import run_gate
from data_ingest.manifest import canonical_json, sha256_text, write_json
from data_ingest.model import Bar, CrossCheck, Membership, SymbolSeries
from data_ingest.overlay import BENCHMARK_SYMBOLS, active_members, build_overlay, read_base_features, read_ranges
from data_ingest.sina import SinaSource


class PipelineError(RuntimeError):
    """Raised when the overlay cannot pass its fail-closed gate."""


def _sanitize(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _sanitize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    return value


def _membership_payload(memberships: Mapping[str, Membership]) -> dict:
    return {
        market: {
            "asof": item.asof,
            "count": len(item.members),
            "raw_sha256": item.raw_sha256,
            "weights_date": item.weights_date,
            "weights_sha256": item.weights_sha256,
            "source_url": item.source_url,
        }
        for market, item in sorted(memberships.items())
    }


def fetch_benchmark_bars(symbols: Iterable[str], start: str, end: str) -> dict[str, dict[str, Bar]]:
    """Fetch raw index bars for website backtest benchmarks from Sina."""
    import akshare as ak

    output: dict[str, dict[str, Bar]] = {}
    for symbol in symbols:
        frame = ak.stock_zh_index_daily(symbol=symbol[:2].lower() + symbol[2:])
        bars: dict[str, Bar] = {}
        for _, row in frame.iterrows():
            date = str(row["date"])[:10]
            if start <= date <= end:
                bars[date] = Bar(
                    date=date,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                    amount=0.0,
                )
        output[symbol] = bars
    return output


class OverlayPipeline:
    """Build a versioned overlay and refuse to mark it PASS unless all gates pass."""

    def __init__(
        self,
        *,
        sina: SinaSource | None = None,
        baostock: BaoStockSource | None = None,
        csindex_fetch=None,
        calendar_dates: Iterable[str] | None = None,
        crosscheck_count: int = 20,
        require_weights: bool = True,
        benchmark_fetch=fetch_benchmark_bars,
    ):
        self.sina = sina or SinaSource()
        self.baostock = baostock or BaoStockSource()
        self.csindex_fetch = csindex_fetch
        self.calendar_dates = list(calendar_dates) if calendar_dates is not None else None
        self.crosscheck_count = crosscheck_count
        self.require_weights = require_weights
        self.benchmark_fetch = benchmark_fetch

    def build(
        self,
        base_dir: str | Path,
        out_dir: str | Path,
        *,
        asof: str,
        dates: Iterable[str] | None = None,
        markets: tuple[str, ...] = ("csi300", "csi500", "csi1000"),
        extra_symbols: Iterable[str] = (),
        base_archive_sha256: str | None = None,
        require_qlib_smoke: bool = False,
        allow_base_membership: bool = False,
        allow_missing_base_suspensions: bool = False,
    ) -> dict:
        base = Path(base_dir)
        out = Path(out_dir)
        asof = str(asof)[:10]
        base_calendar = read_calendar(base)
        base_end = base_calendar[-1]
        target_dates = (
            list(dates) if dates is not None else resolve_target_dates(base_calendar, asof, self.calendar_dates)
        )
        if not target_dates or target_dates[-1] != asof:
            raise PipelineError(f"target dates must end at {asof}: {target_dates}")
        if target_dates != sorted(set(target_dates)):
            raise PipelineError("target dates must be ordered and unique")

        memberships = fetch_all_memberships(markets, fetch=self.csindex_fetch, require_weights=self.require_weights)
        mismatched = [market for market, membership in memberships.items() if membership.asof != asof]
        if mismatched:
            if not allow_base_membership:
                first = memberships[mismatched[0]]
                raise PipelineError(f"{mismatched[0]} constituent date {first.asof} != asof {asof}")
            # Historical backfill: CSIndex exposes only the current snapshot.
            # Use the frozen base ranges active on base_end (no look-ahead).
            base_memberships: dict[str, Membership] = {}
            for market in markets:
                ranges = read_ranges(base / "instruments" / f"{market}.txt")
                members = tuple(sorted(active_members(ranges, base_end)))
                base_memberships[market] = Membership(
                    market=market,
                    asof=asof,
                    members=members,
                    raw_sha256=sha256_text("\n".join(members)),
                    source_url=f"base_pit:{base_end}",
                )
            memberships = base_memberships
            print(f"[overlay] historical backfill uses base PIT membership at {base_end}", flush=True)

        current = sorted(
            {symbol for membership in memberships.values() for symbol in membership.members}
            | {str(symbol).upper() for symbol in extra_symbols}
        )
        print(f"[overlay] current symbols={len(current)} dates={target_dates}", flush=True)
        # Include the frozen base date so hfq/raw ratio changes can detect
        # corporate actions on the first target day.
        primary = self.sina.fetch_pair(current, base_end, target_dates[-1])
        print(
            f"[overlay] sina ready={len(primary.series)} errors={len(primary.errors)}",
            flush=True,
        )

        base_features = read_base_features(base, current, base_end)
        missing_base = sorted(set(current) - set(base_features))
        missing = [symbol for symbol in current if symbol not in primary.series]
        suspect = {
            symbol
            for symbol, series in primary.series.items()
            if any(date not in series.raw or series.raw[date].volume <= 0 for date in target_dates)
        }
        baostock_symbols = set(missing) | suspect | set(missing_base)
        sample = sorted(current, key=sha256_text)[: self.crosscheck_count]
        baostock_symbols |= set(sample)
        # Corporate-action candidates are detected from the Sina hfq/raw ratio;
        # BaoStock is then queried for exact preclose.
        candidates: set[str] = set()
        for symbol in current:
            series = primary.series.get(symbol)
            if series is None:
                continue
            base_raw = series.raw.get(base_end)
            base_hfq = series.hfq.get(base_end)
            if base_raw is None or base_hfq is None or base_raw.close <= 0:
                continue
            base_ratio = base_hfq.close / base_raw.close
            for date in target_dates:
                raw = series.raw.get(date)
                hfq = series.hfq.get(date)
                if raw is None or hfq is None or raw.close <= 0:
                    continue
                if abs((hfq.close / raw.close) / base_ratio - 1.0) > 1e-3:
                    candidates.add(symbol)
                    break
        baostock_symbols |= candidates
        fallback = self.baostock.fetch(sorted(baostock_symbols), target_dates[0], target_dates[-1], adjust="3")
        print(
            f"[overlay] baostock checked={len(baostock_symbols)} ready={len(fallback.series)} errors={len(fallback.errors)}",
            flush=True,
        )

        merged: dict[str, SymbolSeries] = dict(primary.series)
        for symbol, series in fallback.series.items():
            if symbol not in merged:
                merged[symbol] = SymbolSeries(raw=series.raw, hfq={}, source="baostock")
        preclose: dict[tuple[str, str], float] = {}
        suspensions: set[str] = set()
        for symbol, series in fallback.series.items():
            for date, bar in series.raw.items():
                if bar.preclose is not None and bar.preclose > 0:
                    preclose[(symbol, date)] = float(bar.preclose)
                if bar.tradestatus == 0:
                    suspensions.add(symbol)

        unresolved_base = sorted(set(missing_base) - suspensions)
        if unresolved_base and allow_missing_base_suspensions:
            # Historical PIT membership can still contain a name that was
            # suspended at base_end and has no feature history.  The base
            # snapshot itself has no close for it, so the only safe historical
            # interpretation is a suspended member, not a fabricated price.
            print(f"[overlay] treating base-missing members as suspended: {unresolved_base[:20]}", flush=True)
            suspensions.update(unresolved_base)
            unresolved_base = []
        if unresolved_base:
            raise PipelineError(f"no frozen base features and no confirmed suspension: {unresolved_base[:20]}")

        rows = build_rows(
            base_features,
            merged,
            target_dates,
            base_end=base_end,
            preclose=preclose,
        )
        benchmark_base = read_base_features(base, BENCHMARK_SYMBOLS, base_end)
        rows.update(self._benchmark_rows(benchmark_base, target_dates))
        print(f"[overlay] adjusted rows={len(rows)}", flush=True)

        crosschecks = self._crosschecks(sample, target_dates, primary, fallback)
        corporate_actions = self._corporate_actions(rows, base_features, target_dates, base_end, preclose)
        overlay_meta = build_overlay(
            base,
            out,
            dates=target_dates,
            rows=rows,
            memberships=memberships,
            base_end=base_end,
            asof=asof,
        )
        report = run_gate(
            base,
            out,
            base_end=base_end,
            asof=asof,
            dates=target_dates,
            memberships=memberships,
            rows=rows,
            crosschecks=crosschecks,
            suspensions=suspensions,
            corporate_actions=corporate_actions,
            require_crosscheck=True,
            min_crosschecks=max(1, int(self.crosscheck_count * len(target_dates) * 0.8)),
            require_qlib=require_qlib_smoke,
        )
        manifest = {
            "schema_version": 1,
            "status": report.status,
            "base": {
                "path": str(base),
                "calendar_end": base_end,
                "calendar_sha256": sha256_text("\n".join(base_calendar) + "\n"),
                "archive_sha256": base_archive_sha256,
            },
            "target": {"asof": asof, "dates": target_dates},
            "memberships": _membership_payload(memberships),
            "sources": {
                "primary": "sina:stock_zh_a_daily(raw,hfq)",
                "fallback": "baostock:query_history_k_data_plus(raw,preclose,tradestatus,isST)",
                "universe": "csindex official cons.xls / closeweight.xls",
                "sina_errors": primary.errors,
                "baostock_errors": fallback.errors,
            },
            "overlay": overlay_meta,
            "rows": {
                "symbols": len(rows),
                "dates": len(target_dates),
                "sha256": sha256_text(canonical_json(_sanitize(rows))),
            },
            "gate": report.as_dict(),
        }
        write_json(out / "new_rows.json", _sanitize(rows))
        write_json(out / "manifest.json", manifest)
        sums = {
            "calendars/day.txt": _sha(out / "calendars" / "day.txt"),
            "instruments/csi300.txt": _sha(out / "instruments" / "csi300.txt"),
            "instruments/csi500.txt": _sha(out / "instruments" / "csi500.txt"),
            "instruments/csi1000.txt": _sha(out / "instruments" / "csi1000.txt"),
            "new_rows.json": _sha(out / "new_rows.json"),
            "manifest.json": _sha(out / "manifest.json"),
        }
        write_json(out / "SHA256SUMS.json", sums)
        if report.status != "PASS":
            raise PipelineError("overlay gate failed: " + "; ".join(report.failures[:5]))
        return manifest

    def _benchmark_rows(
        self,
        base_features: Mapping[str, Mapping[str, float]],
        dates: list[str],
    ) -> dict[str, dict[str, dict[str, float]]]:
        if not base_features:
            return {}
        bars = self.benchmark_fetch(sorted(base_features), dates[0], dates[-1])
        output: dict[str, dict[str, dict[str, float]]] = {}
        for symbol, base in base_features.items():
            factor = float(base["factor"])
            previous = float(base["close"])
            rows: dict[str, dict[str, float]] = {}
            for date in dates:
                bar = bars.get(symbol, {}).get(date)
                if bar is None or bar.close <= 0:
                    raise PipelineError(f"missing benchmark bar: {symbol}/{date}")
                close = float(bar.close) * factor
                rows[date] = {
                    "open": float(bar.open) * factor,
                    "high": float(bar.high) * factor,
                    "low": float(bar.low) * factor,
                    "close": close,
                    "volume": 0.0,
                    "amount": 0.0,
                    "factor": factor,
                    "vwap": close,
                    "change": close / previous - 1.0 if previous > 0 else float("nan"),
                    "adjclose": float(bar.close),
                }
                previous = close
            output[symbol] = rows
        return output

    def _crosschecks(
        self,
        sample: list[str],
        dates: list[str],
        primary,
        fallback,
    ) -> list[CrossCheck]:
        checks: list[CrossCheck] = []
        for symbol in sample:
            sina = primary.series.get(symbol)
            bao = fallback.series.get(symbol)
            if sina is None or bao is None:
                continue
            for date in dates:
                left = sina.raw.get(date)
                right = bao.raw.get(date)
                if left is None or right is None:
                    continue
                checks.append(
                    CrossCheck(
                        symbol=symbol,
                        date=date,
                        fields={
                            "open": (left.open, right.open),
                            "high": (left.high, right.high),
                            "low": (left.low, right.low),
                            "close": (left.close, right.close),
                            "volume": (left.volume, right.volume),
                            "amount": (left.amount, right.amount),
                        },
                    )
                )
        return checks

    def _corporate_actions(
        self,
        rows: Mapping[str, Mapping[str, Mapping[str, float]]],
        base_features: Mapping[str, Mapping[str, float]],
        dates: list[str],
        base_end: str,
        preclose: Mapping[tuple[str, str], float],
    ) -> list[dict]:
        actions: list[dict] = []
        for symbol, per_date in rows.items():
            if symbol not in base_features:
                continue
            previous_close = float(base_features[symbol]["close"])
            previous_factor = float(base_features[symbol]["factor"])
            previous_raw = previous_close / previous_factor
            for date in dates:
                row = per_date.get(date, {})
                pre = preclose.get((symbol, date))
                actual = row.get("factor")
                if (
                    pre is not None
                    and pre > 0
                    and actual is not None
                    and math.isfinite(actual)
                    and previous_raw > 0
                    and abs(float(pre) - previous_raw) / previous_raw > 1e-6
                ):
                    actions.append(
                        {
                            "symbol": symbol,
                            "date": date,
                            "preclose": float(pre),
                            "expected_factor": previous_close / float(pre),
                            "actual_factor": float(actual),
                        }
                    )
                close = row.get("close")
                factor = row.get("factor")
                if (
                    close is not None
                    and factor is not None
                    and math.isfinite(close)
                    and math.isfinite(factor)
                    and factor > 0
                ):
                    previous_close = float(close)
                    previous_factor = float(factor)
                    previous_raw = previous_close / previous_factor
        return actions


def _sha(path: Path) -> str:
    from data_ingest.manifest import sha256_file

    return sha256_file(path)
