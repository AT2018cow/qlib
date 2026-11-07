"""Sina daily bars through AkShare, used as the primary incremental source."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable

from data_ingest.model import Bar, FetchBatch, SymbolSeries
from data_ingest.symbols import SymbolError, normalize_code, to_sina_symbol


class SinaError(RuntimeError):
    """Raised when a Sina request cannot be completed after retries."""


def _to_bar(row) -> Bar:
    return Bar(
        date=str(row["date"])[:10],
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row["volume"]),
        amount=float(row["amount"]),
    )


class SinaSource:
    """Threaded AkShare/Sina daily reader.

    The source is intentionally thin: it returns provider bars, not adjusted
    Qlib rows.  The overlay layer owns the adjustment contract.
    """

    def __init__(self, workers: int = 6, retries: int = 3, retry_sleep: float = 1.5):
        if workers < 1 or retries < 1:
            raise ValueError("workers and retries must be positive")
        self.workers = workers
        self.retries = retries
        self.retry_sleep = retry_sleep

    def _fetch_one(self, ak, symbol: str, start: str, end: str, adjust: str) -> dict[str, Bar]:
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                frame = ak.stock_zh_a_daily(
                    symbol=to_sina_symbol(symbol),
                    start_date=start.replace("-", ""),
                    end_date=end.replace("-", ""),
                    adjust=adjust,
                )
                bars: dict[str, Bar] = {}
                for _, row in frame.iterrows():
                    bar = _to_bar(row)
                    bars[bar.date] = bar
                return bars
            except Exception as exc:  # noqa: BLE001 - provider boundary
                last_error = exc
                if attempt + 1 < self.retries:
                    time.sleep(self.retry_sleep * (attempt + 1))
        raise SinaError(f"{symbol}: {type(last_error).__name__}: {last_error}")

    def fetch(self, symbols: Iterable[str], start: str, end: str, adjust: str = "") -> FetchBatch:
        import akshare as ak

        codes = sorted({normalize_code(symbol) for symbol in symbols})
        batch = FetchBatch()
        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            futures = {executor.submit(self._fetch_one, ak, code, start, end, adjust): code for code in codes}
            for future in as_completed(futures):
                code = futures[future]
                try:
                    batch.series[code] = SymbolSeries(
                        raw=future.result(),
                        hfq={},
                        source=f"sina:{adjust or 'raw'}",
                    )
                except (SinaError, SymbolError) as exc:
                    batch.errors[code] = str(exc)
        return batch

    def fetch_pair(self, symbols: Iterable[str], start: str, end: str) -> FetchBatch:
        """Fetch raw and hfq bars and merge them per symbol."""
        codes = sorted({normalize_code(symbol) for symbol in symbols})
        raw = self.fetch(codes, start, end, adjust="")
        hfq = self.fetch(codes, start, end, adjust="hfq")
        merged = FetchBatch()
        for code in codes:
            raw_bars = raw.series.get(code)
            hfq_bars = hfq.series.get(code)
            if raw_bars is None or hfq_bars is None:
                merged.errors[code] = raw.errors.get(code) or hfq.errors.get(code) or "missing raw/hfq series"
                continue
            merged.series[code] = SymbolSeries(raw=raw_bars.raw, hfq=hfq_bars.raw, source="sina")
        return merged
