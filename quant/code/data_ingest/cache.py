"""Resumable raw-input cache for one overlay build.

The cache stores a small number of batch files on the production Volume, keyed
by target trade date and a fetch-contract version.  A retry after a worker
preemption reuses every completed symbol/date and refetches only gaps.  An
empty or partial response is never treated as complete data: completeness is
derived from the requested dates, and callers still have to pass the gate.

Layout::

    <root>/ingest_cache/<YYYY-MM-DD>/<contract>/
        request.json
        memberships.json
        sina-<batch>.parquet
        sina_errors.json
        sina_stats.json
        baostock.parquet
        history.json.gz
"""

from __future__ import annotations

import gzip
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping

from data_ingest.manifest import canonical_json, sha256_text, write_json
from data_ingest.model import Bar, FetchBatch, Membership, SymbolSeries

CACHE_CONTRACT = "ingest-cache-v2"


class CacheError(RuntimeError):
    """Raised when a cache artifact exists but cannot be trusted."""


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


@dataclass
class CachedSina:
    batch: FetchBatch
    dates_by_symbol: dict[str, set[str]]
    requests: list[dict]

    @property
    def complete_codes(self) -> set[str]:
        return {symbol for symbol, series in self.batch.series.items() if series.raw}


class IngestCache:
    """Small, commit-bounded cache for one trade date and fetch contract."""

    def __init__(
        self,
        root: str | Path,
        trade_date: str,
        *,
        contract: str = CACHE_CONTRACT,
        commit_fn: Callable[[], None] | None = None,
    ):
        self.root = Path(root)
        self.trade_date = str(trade_date)[:10]
        self.contract = contract
        self.commit_fn = commit_fn
        self.directory = self.root / "ingest_cache" / self.trade_date / contract
        self.directory.mkdir(parents=True, exist_ok=True)

    def _commit(self) -> None:
        if self.commit_fn is not None:
            self.commit_fn()

    def _request_payload(self, dates: Iterable[str], start: str, end: str, symbols: Iterable[str]) -> dict:
        symbols = sorted({str(item).upper() for item in symbols})
        return {
            "schema_version": 1,
            "contract": self.contract,
            "trade_date": self.trade_date,
            "start": start,
            "end": end,
            "dates": list(dates),
            "symbols": symbols,
            "symbols_sha256": sha256_text("\n".join(symbols)),
        }

    def prepare_request(self, dates: Iterable[str], start: str, end: str, symbols: Iterable[str]) -> dict:
        """Write or validate the request identity before reusing cached rows."""
        payload = self._request_payload(dates, start, end, symbols)
        path = self.directory / "request.json"
        existing = _read_json(path)
        if existing is not None and existing.get("contract") != self.contract:
            raise CacheError("cached request contract mismatch")
        if existing is not None and existing.get("trade_date") != self.trade_date:
            raise CacheError("cached request trade-date mismatch")
        if existing is None or existing != payload:
            write_json(path, payload)
            self._commit()
        return payload

    def _request(self) -> dict | None:
        return _read_json(self.directory / "request.json")

    def load_memberships(self, *, max_age_seconds: float = 3600.0) -> dict | None:
        path = self.directory / "memberships.json"
        payload = _read_json(path)
        if payload is None or payload.get("contract") != self.contract:
            return None
        fetched = payload.get("fetched_at_epoch")
        if not isinstance(fetched, (int, float)) or time.time() - float(fetched) > max_age_seconds:
            return None
        memberships = {}
        try:
            for market, item in (payload.get("memberships") or {}).items():
                memberships[market] = Membership(
                    market=market,
                    asof=str(item["asof"])[:10],
                    members=tuple(item["members"]),
                    weights_date=item.get("weights_date"),
                    raw_sha256=item.get("raw_sha256", ""),
                    weights_sha256=item.get("weights_sha256"),
                    source_url=item.get("source_url", ""),
                    weights_source_url=item.get("weights_source_url"),
                )
        except (KeyError, TypeError, ValueError) as exc:
            raise CacheError(f"cached membership payload is invalid: {exc}") from exc
        payload["parsed"] = memberships
        return payload

    def save_memberships(self, memberships: Mapping[str, Membership], evidence: Mapping[str, dict]) -> dict:
        payload = {
            "schema_version": 1,
            "contract": self.contract,
            "trade_date": self.trade_date,
            "fetched_at_epoch": time.time(),
            "fetched_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "memberships": {
                market: {
                    "asof": item.asof,
                    "members": list(item.members),
                    "weights_date": item.weights_date,
                    "raw_sha256": item.raw_sha256,
                    "weights_sha256": item.weights_sha256,
                    "source_url": item.source_url,
                    "weights_source_url": item.weights_source_url,
                }
                for market, item in sorted(memberships.items())
            },
            "evidence": dict(evidence),
        }
        write_json(self.directory / "memberships.json", payload)
        self._commit()
        return payload

    def _sina_files(self) -> list[Path]:
        return sorted(self.directory.glob("sina-*.parquet"))

    def load_sina(self) -> CachedSina | None:
        import pandas as pd

        files = self._sina_files()
        if not files:
            return None
        frames = []
        for path in files:
            try:
                frames.append(pd.read_parquet(path))
            except Exception as exc:  # noqa: BLE001 - cache boundary
                raise CacheError(f"cannot read cached Sina batch {path.name}: {exc}") from exc
        frame = pd.concat(frames, ignore_index=True)
        if frame.empty:
            return None
        frame = frame.drop_duplicates(subset=["symbol", "date"], keep="last")
        errors = _read_json(self.directory / "sina_errors.json") or {}
        stats = _read_json(self.directory / "sina_stats.json") or {}
        batch = FetchBatch()
        dates_by_symbol: dict[str, set[str]] = {}
        for symbol, rows in frame.groupby("symbol", sort=True):
            raw: dict[str, Bar] = {}
            hfq: dict[str, Bar] = {}
            for _, row in rows.iterrows():
                date = str(row["date"])[:10]
                raw[date] = Bar(
                    date=date,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                    amount=float(row["amount"]),
                )
                hfq[date] = Bar(
                    date=date,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["hfq_close"]),
                    volume=0.0,
                    amount=0.0,
                )
            batch.series[str(symbol)] = SymbolSeries(raw=raw, hfq=hfq, source="sina:cache")
            dates_by_symbol[str(symbol)] = set(raw)
        batch.errors = {str(key): str(value) for key, value in errors.get("errors", {}).items()}
        return CachedSina(
            batch=batch,
            dates_by_symbol=dates_by_symbol,
            requests=list(stats.get("batches") or []),
        )

    def append_sina(
        self,
        batch: FetchBatch,
        *,
        batch_index: int,
        stats: dict | None = None,
    ) -> Path | None:
        """Persist one completed fetch batch; safe to call incrementally."""
        import pandas as pd

        rows = []
        for symbol, series in sorted(batch.series.items()):
            for date, bar in sorted(series.raw.items()):
                hfq_bar = series.hfq.get(date)
                rows.append(
                    {
                        "symbol": symbol,
                        "date": date,
                        "open": bar.open,
                        "high": bar.high,
                        "low": bar.low,
                        "close": bar.close,
                        "volume": bar.volume,
                        "amount": bar.amount,
                        "hfq_close": (hfq_bar.close if hfq_bar is not None else float("nan")),
                    }
                )
        if not rows:
            return None
        frame = pd.DataFrame(rows)
        existing = self._sina_files()
        next_sequence = 1
        for path in existing:
            parts = path.name.split("-", 2)
            if len(parts) >= 2 and parts[1].isdigit():
                next_sequence = max(next_sequence, int(parts[1]) + 1)
        path = self.directory / f"sina-{next_sequence:06d}-{uuid.uuid4().hex[:8]}.parquet"
        tmp = path.with_name(f".{path.name}.tmp")
        frame.to_parquet(tmp, index=False)
        os.replace(tmp, path)
        errors = _read_json(self.directory / "sina_errors.json") or {"errors": {}}
        errors.setdefault("errors", {}).update(batch.errors)
        write_json(self.directory / "sina_errors.json", errors)
        if stats is not None:
            payload = _read_json(self.directory / "sina_stats.json") or {"batches": []}
            payload.setdefault("batches", []).append(stats)
            write_json(self.directory / "sina_stats.json", payload)
        self._commit()
        return path

    def load_baostock(self) -> FetchBatch | None:
        import pandas as pd

        path = self.directory / "baostock.parquet"
        if not path.is_file():
            return None
        frame = pd.read_parquet(path)
        batch = FetchBatch()
        for symbol, rows in frame.groupby("symbol", sort=True):
            raw: dict[str, Bar] = {}
            for _, row in rows.iterrows():
                date = str(row["date"])[:10]
                raw[date] = Bar(
                    date=date,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                    amount=float(row["amount"]),
                    preclose=(float(row["preclose"]) if pd.notna(row["preclose"]) else None),
                    tradestatus=(int(row["tradestatus"]) if pd.notna(row["tradestatus"]) else None),
                    is_st=int(row["is_st"]) if pd.notna(row["is_st"]) else None,
                )
            batch.series[str(symbol)] = SymbolSeries(raw=raw, hfq={}, source="baostock:cache")
        return batch

    def save_baostock(self, batch: FetchBatch) -> None:
        import pandas as pd

        rows = []
        for symbol, series in sorted(batch.series.items()):
            for date, bar in sorted(series.raw.items()):
                rows.append(
                    {
                        "symbol": symbol,
                        "date": date,
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
                )
        if not rows:
            return
        path = self.directory / "baostock.parquet"
        tmp = path.with_name(f".{path.name}.tmp")
        pd.DataFrame(rows).to_parquet(tmp, index=False)
        os.replace(tmp, path)
        write_json(self.directory / "baostock_errors.json", {"errors": dict(batch.errors)})
        self._commit()

    def load_history(self) -> dict | None:
        path = self.directory / "history.json.gz"
        if not path.is_file():
            return None
        try:
            with gzip.open(path, "rt", encoding="utf-8") as stream:
                payload = json.load(stream)
        except (OSError, ValueError) as exc:
            raise CacheError(f"cannot read cached history: {exc}") from exc
        return payload if isinstance(payload, dict) else None

    def save_history(self, payload: dict) -> None:
        path = self.directory / "history.json.gz"
        tmp = path.with_name(f".{path.name}.tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as stream:
            stream.write(canonical_json(payload))
        os.replace(tmp, path)
        self._commit()

    def status(self) -> dict:
        request = self._request() or {}
        return {
            "directory": str(self.directory),
            "contract": self.contract,
            "trade_date": self.trade_date,
            "request_symbols": len(request.get("symbols") or []),
            "sina_batches": len(self._sina_files()),
            "memberships": (self.directory / "memberships.json").is_file(),
            "baostock": (self.directory / "baostock.parquet").is_file(),
            "history": (self.directory / "history.json.gz").is_file(),
        }


def sha256_symbols(symbols: Iterable[str]) -> str:
    return sha256_text("\n".join(sorted({str(item).upper() for item in symbols})))
