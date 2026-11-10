"""End-to-end provider overlay pipeline (no publication)."""

from __future__ import annotations

import math
from datetime import date as date_cls
from pathlib import Path
from typing import Iterable, Mapping

from data_ingest.adjust import FIELDS, build_rows
from data_ingest.baostock_source import BaoStockSource
from data_ingest.cache import IngestCache
from data_ingest.calendar import read_calendar, resolve_target_dates
from data_ingest.csindex import fetch_all_memberships
from data_ingest.gate import run_gate
from data_ingest.history import BackfillError, build_backfill
from data_ingest.manifest import canonical_json, sha256_text, write_json
from data_ingest.model import Bar, CrossCheck, FetchBatch, Membership, SymbolSeries
from data_ingest.overlay import (
    BENCHMARK_SYMBOLS,
    active_members,
    build_overlay,
    read_base_features,
    read_ranges,
)
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
            "raw_bytes": item.raw_bytes,
            "weights_date": item.weights_date,
            "weights_sha256": item.weights_sha256,
            "weights_bytes": item.weights_bytes,
            "source_url": item.source_url,
            "weights_source_url": item.weights_source_url,
            "fetched_at_utc": item.fetched_at_utc,
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


def _merge_batches(target: FetchBatch, incoming: FetchBatch) -> None:
    for symbol, series in incoming.series.items():
        if symbol in target.series:
            raw = dict(target.series[symbol].raw)
            raw.update(series.raw)
            hfq = dict(target.series[symbol].hfq)
            hfq.update(series.hfq)
            target.series[symbol] = SymbolSeries(raw=raw, hfq=hfq, source=series.source)
        else:
            target.series[symbol] = series
    target.errors.update(incoming.errors)


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
        cache: IngestCache | None = None,
        min_primary_coverage: float = 0.8,
        max_baostock_symbols: int = 300,
        progress_every: int = 200,
        metrics: dict | None = None,
        membership_provider=None,
    ):
        self.sina = sina or SinaSource()
        self.baostock = baostock or BaoStockSource()
        self.csindex_fetch = csindex_fetch
        self.calendar_dates = list(calendar_dates) if calendar_dates is not None else None
        self.crosscheck_count = crosscheck_count
        self.require_weights = require_weights
        self.benchmark_fetch = benchmark_fetch
        self.cache = cache
        self.min_primary_coverage = float(min_primary_coverage)
        self.max_baostock_symbols = int(max_baostock_symbols)
        self.progress_every = int(progress_every)
        self.metrics = dict(metrics or {})
        self._memberships: dict[str, Membership] | None = None
        self.membership_provider = membership_provider

    def _load_memberships(self, markets: tuple[str, ...], asof: str, refresh: bool) -> dict[str, Membership]:
        if self.membership_provider is not None:
            return self.membership_provider(markets)
        if self._memberships is not None and not refresh:
            return {market: self._memberships[market] for market in markets}
        cached = None if self.cache is None or refresh else self.cache.load_memberships()
        if cached is not None and all(market in cached["parsed"] for market in markets):
            self._memberships = cached["parsed"]
            print(f"[overlay] memberships reused from cache: {cached.get('fetched_at_utc')}", flush=True)
            return {market: self._memberships[market] for market in markets}
        memberships = fetch_all_memberships(markets, fetch=self.csindex_fetch, require_weights=self.require_weights)
        self._memberships = dict(memberships)
        if self.cache is not None:
            evidence = {
                market: {
                    "raw_sha256": item.raw_sha256,
                    "raw_bytes": item.raw_bytes,
                    "weights_sha256": item.weights_sha256,
                    "weights_bytes": item.weights_bytes,
                    "source_url": item.source_url,
                    "weights_source_url": item.weights_source_url,
                }
                for market, item in memberships.items()
            }
            self.cache.save_memberships(memberships, evidence)
        return memberships

    def _membership_fallback(
        self,
        base: Path,
        base_end: str,
        asof: str,
        memberships: Mapping[str, Membership],
        target_dates: list[str],
        markets: tuple[str, ...],
    ) -> tuple[dict[str, Membership], dict]:
        """Build base-PIT memberships only when the official snapshot matches.

        A lagged CSIndex file may hide a rebalance.  Reusing base PIT is allowed
        only when the official snapshot's members are exactly the base's active
        members; otherwise the build fails closed.  Multi-day gaps have no
        historical membership evidence, so they also fail closed.
        """
        if len(target_dates) > 1:
            raise PipelineError(
                "membership evidence is stale for a multi-day gap; refusing to apply current/base "
                f"membership to historical dates {target_dates}"
            )
        base_memberships: dict[str, Membership] = {}
        for market in markets:
            ranges = read_ranges(base / "instruments" / f"{market}.txt")
            members = tuple(sorted(active_members(ranges, base_end)))
            official = memberships[market]
            if set(official.members) != set(members):
                raise PipelineError(
                    f"{market} official snapshot asof {official.asof} differs from base PIT members at "
                    f"{base_end}; refusing lagged membership that may hide a rebalance"
                )
            base_memberships[market] = Membership(
                market=market,
                asof=asof,
                members=members,
                raw_sha256=sha256_text("\n".join(members)),
                raw_bytes=official.raw_bytes,
                weights_date=official.weights_date,
                weights_sha256=official.weights_sha256,
                weights_bytes=official.weights_bytes,
                source_url=f"base_pit:{base_end}",
                weights_source_url=official.weights_source_url,
                fetched_at_utc=official.fetched_at_utc,
            )
        policy = {
            "mode": "BASE_PIT_LAG",
            "official_asof": memberships[markets[0]].asof,
            "base_end": base_end,
            "lag_days": None,
            "equality_verified": True,
        }
        print(f"[overlay] historical/one-day backfill uses verified base PIT membership at {base_end}", flush=True)
        return base_memberships, policy

    def _cached_sina(self, required_dates: list[str], current: set[str]) -> tuple[FetchBatch, set[str]]:
        batch = FetchBatch()
        complete: set[str] = set()
        if self.cache is None:
            return batch, complete
        cached = self.cache.load_sina()
        if cached is None:
            return batch, complete
        for symbol in sorted(current & set(cached.batch.series)):
            series = cached.batch.series[symbol]
            available = set(series.raw)
            if all(date in available for date in required_dates):
                batch.series[symbol] = series
                complete.add(symbol)
            else:
                batch.series[symbol] = series
        batch.errors.update({key: value for key, value in cached.batch.errors.items() if key in current})
        return batch, complete

    def _fetch_sina(
        self,
        current: set[str],
        *,
        required_dates: list[str],
        base_end: str,
        asof: str,
    ) -> FetchBatch:
        cached, complete = self._cached_sina(required_dates, current)
        refetch = sorted(current - complete)
        if self.cache is not None:
            self.cache.prepare_request(required_dates[1:], base_end, asof, current)
        print(
            f"[overlay] sina cache complete={len(complete)} refetch={len(refetch)}",
            flush=True,
        )
        if not refetch:
            cached.stats = {"cache_hit": True, "completed": len(complete), "refetched": 0}
            return cached
        progress = None
        if self.cache is not None:

            def on_progress(batch: FetchBatch, index: int, stats: dict) -> None:
                self.cache.append_sina(batch, batch_index=index, stats=stats)

            progress = on_progress

        fetched = self.sina.fetch_pair(
            refetch,
            base_end,
            asof,
            on_progress=progress,
            progress_every=self.progress_every,
        )
        _merge_batches(cached, fetched)
        cached.stats = {
            "cache_hit": bool(complete),
            "cache_complete": len(complete),
            "refetched": len(refetch),
            **(fetched.stats or {}),
        }
        return cached

    def _cached_baostock(self, needed: set[str], target_dates: list[str]) -> tuple[FetchBatch, set[str]]:
        batch = FetchBatch()
        complete: set[str] = set()
        if self.cache is None:
            return batch, complete
        cached = self.cache.load_baostock()
        if cached is None:
            return batch, complete
        for symbol in sorted(needed & set(cached.series)):
            if all(date in cached.series[symbol].raw for date in target_dates):
                batch.series[symbol] = cached.series[symbol]
                complete.add(symbol)
        return batch, complete

    def _history_backfill(
        self,
        missing_base: list[str],
        *,
        base_calendar: list[str],
        target_dates: list[str],
        allow_missing_base_suspensions: bool,
        suspensions: set[str],
    ) -> tuple[
        dict[str, dict[str, dict[str, float]]],
        dict[str, dict[str, dict[str, float]]],
        dict[str, int],
        dict[str, dict],
        list[str],
    ]:
        history_rows: dict[str, dict[str, dict[str, float]]] = {}
        target_rows: dict[str, dict[str, dict[str, float]]] = {}
        history_start: dict[str, int] = {}
        provenance: dict[str, dict] = {}
        unresolved: list[str] = []
        if not missing_base:
            return history_rows, target_rows, history_start, provenance, unresolved
        cached = self.cache.load_history() if self.cache is not None else None
        cached_symbols = (cached or {}).get("symbols") or {}
        to_fetch = [symbol for symbol in missing_base if symbol not in cached_symbols]
        fetched_history: dict[str, dict] = {}
        if to_fetch:
            print(f"[overlay] fetching BaoStock history for {len(to_fetch)} new members", flush=True)
            fetched_history = self.baostock.fetch_history(
                to_fetch,
                base_calendar[0],
                target_dates[-1],
            )
            if self.cache is not None:
                merged = dict(cached_symbols)
                for symbol, payload in fetched_history.items():
                    if payload.get("error"):
                        continue
                    merged[symbol] = {
                        "raw": {date: _sanitize(_bar_payload(bar)) for date, bar in payload["raw"].items()},
                        "hfq_close": payload.get("hfq_close") or {},
                    }
                self.cache.save_history(
                    {
                        "schema_version": 1,
                        "source": "baostock:adjustflag=3 + preclose",
                        "symbols": merged,
                    }
                )
                cached_symbols = merged
        for symbol in missing_base:
            payload = fetched_history.get(symbol) or cached_symbols.get(symbol)
            if not payload or payload.get("error"):
                error = (payload or {}).get("error", "no cached history")
                if allow_missing_base_suspensions and symbol in suspensions:
                    continue
                unresolved.append(f"{symbol}: {error}")
                continue
            raw_bars = {
                date: (item if isinstance(item, Bar) else _bar_from_payload(item))
                for date, item in (payload.get("raw") or {}).items()
            }
            try:
                result = build_backfill(
                    symbol,
                    raw_bars,
                    hfq_close={date: float(value) for date, value in (payload.get("hfq_close") or {}).items()},
                    base_calendar=base_calendar,
                    target_dates=target_dates,
                )
            except BackfillError as exc:
                if allow_missing_base_suspensions and symbol in suspensions:
                    continue
                unresolved.append(str(exc))
                continue
            history_rows[symbol] = result["history"]
            target_rows[symbol] = result["target"]
            history_start[symbol] = result["start_index"]
            provenance[symbol] = result["provenance"]
        return history_rows, target_rows, history_start, provenance, unresolved

    def build(
        self,
        base_dir: str | Path,
        out_dir: str | Path,
        *,
        asof: str,
        dates: Iterable[str] | None = None,
        markets: tuple[str, ...] = ("csi300", "csi500", "csi1000"),
        extra_symbols: Iterable[str] = (),
        required_extra_symbols: Iterable[str] = (),
        base_archive_sha256: str | None = None,
        base_identity: Mapping | None = None,
        require_qlib_smoke: bool = False,
        allow_base_membership: bool = False,
        allow_missing_base_suspensions: bool = False,
        refresh_memberships: bool = False,
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

        memberships = self._load_memberships(markets, asof, refresh_memberships)
        membership_policy = {"mode": "OFFICIAL", "official_asof": memberships[markets[0]].asof}
        mismatched = [market for market, membership in memberships.items() if membership.asof != asof]
        if mismatched:
            if not allow_base_membership:
                first = memberships[mismatched[0]]
                raise PipelineError(f"{mismatched[0]} constituent date {first.asof} != asof {asof}")
            memberships, membership_policy = self._membership_fallback(
                base, base_end, asof, memberships, target_dates, markets
            )
            membership_policy["lag_days"] = (
                date_cls.fromisoformat(asof) - date_cls.fromisoformat(membership_policy["official_asof"])
            ).days

        extras = {str(symbol).upper() for symbol in extra_symbols}
        required_extras = {str(symbol).upper() for symbol in required_extra_symbols}
        current = sorted({symbol for membership in memberships.values() for symbol in membership.members} | extras)
        members = {symbol for membership in memberships.values() for symbol in membership.members}
        print(f"[overlay] current symbols={len(current)} members={len(members)} dates={target_dates}", flush=True)

        # Include the frozen base date so hfq/raw ratio changes can detect
        # corporate actions on the first target day.
        required_dates = [base_end] + list(target_dates)
        primary = self._fetch_sina(current=set(current), required_dates=required_dates, base_end=base_end, asof=asof)
        with_asof = {symbol for symbol in members if symbol in primary.series and asof in primary.series[symbol].raw}
        coverage = len(with_asof) / max(1, len(members))
        print(
            f"[overlay] sina ready={len(primary.series)} errors={len(primary.errors)} " f"asof_coverage={coverage:.1%}",
            flush=True,
        )
        if coverage < self.min_primary_coverage:
            raise PipelineError(
                f"primary source not ready for {asof}: only {len(with_asof)}/{len(members)} "
                f"current members have a bar; refusing whole-market BaoStock fallback"
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
        if len(baostock_symbols) > self.max_baostock_symbols:
            raise PipelineError(
                f"BaoStock fallback requested {len(baostock_symbols)} symbols "
                f"(limit {self.max_baostock_symbols}); provider likely not ready"
            )
        cached_bs, complete_bs = self._cached_baostock(baostock_symbols, target_dates)
        refetch_bs = sorted(baostock_symbols - complete_bs)
        print(
            f"[overlay] baostock needed={len(baostock_symbols)} cached={len(complete_bs)} refetch={len(refetch_bs)}",
            flush=True,
        )
        fetched_bs = (
            self.baostock.fetch(refetch_bs, target_dates[0], target_dates[-1], adjust="3")
            if refetch_bs
            else FetchBatch()
        )
        _merge_batches(cached_bs, fetched_bs)
        fallback = cached_bs
        if self.cache is not None and (fetched_bs.series or fetched_bs.errors):
            self.cache.save_baostock(fallback)
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

        rows = build_rows(
            base_features,
            merged,
            target_dates,
            base_end=base_end,
            preclose=preclose,
        )
        history_rows, history_target, history_start, history_provenance, unresolved = self._history_backfill(
            missing_base,
            base_calendar=base_calendar,
            target_dates=target_dates,
            allow_missing_base_suspensions=allow_missing_base_suspensions,
            suspensions=suspensions,
        )
        if unresolved:
            raise PipelineError(
                "no frozen base features and no usable controlled history: " + "; ".join(unresolved[:10])
            )
        for symbol, target_rows in history_target.items():
            rows[symbol] = {
                date: target_rows.get(date, {field: float("nan") for field in FIELDS}) for date in target_dates
            }
        benchmark_base = read_base_features(base, BENCHMARK_SYMBOLS, base_end)
        rows.update(self._benchmark_rows(benchmark_base, target_dates))
        print(
            f"[overlay] adjusted rows={len(rows)} history_backfill={len(history_rows)}",
            flush=True,
        )

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
            history_rows=history_rows,
            history_start=history_start,
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
            required_symbols=required_extras,
            base_symbols=set(base_features),
        )
        base_payload = (
            dict(base_identity)
            if base_identity is not None
            else {
                "path": str(base),
                "calendar_end": base_end,
                "calendar_sha256": sha256_text("\n".join(base_calendar) + "\n"),
                "archive_sha256": base_archive_sha256,
            }
        )
        manifest = {
            "schema_version": 1,
            "status": report.status,
            "base": base_payload,
            "target": {"asof": asof, "dates": target_dates},
            "membership_policy": membership_policy,
            "memberships": _membership_payload(memberships),
            "sources": {
                "primary": "sina:stock_zh_a_daily(raw,hfq) fast-pair adapter",
                "fallback": "baostock:query_history_k_data_plus(raw,preclose,tradestatus,isST)",
                "universe": "csindex official cons.xls / closeweight.xls",
                "sina_errors": primary.errors,
                "baostock_errors": fallback.errors,
                "sina_stats": primary.stats,
                "cache": self.cache.status() if self.cache is not None else None,
            },
            "universe": {
                "members": len(members),
                "extras": sorted(extras),
                "required_extras": sorted(required_extras),
            },
            "history_backfill": {
                "symbols": sorted(history_rows),
                "provenance": history_provenance,
            },
            "overlay": overlay_meta,
            "rows": {
                "symbols": len(rows),
                "dates": len(target_dates),
                "sha256": sha256_text(canonical_json(_sanitize(rows))),
            },
            "gate": report.as_dict(),
            "metrics": self.metrics,
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


def _bar_payload(bar: Bar) -> dict:
    return {
        "date": bar.date,
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
        "amount": bar.amount,
        "preclose": bar.preclose,
        "tradestatus": bar.tradestatus,
        "is_st": bar.is_st,
    }


def _bar_from_payload(payload: dict) -> Bar:
    return Bar(
        date=payload["date"],
        open=payload["open"],
        high=payload["high"],
        low=payload["low"],
        close=payload["close"],
        volume=payload["volume"],
        amount=payload["amount"],
        preclose=payload.get("preclose"),
        tradestatus=payload.get("tradestatus"),
        is_st=payload.get("is_st"),
    )


def _sha(path: Path) -> str:
    from data_ingest.manifest import sha256_file

    return sha256_file(path)
