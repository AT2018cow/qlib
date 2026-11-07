"""BaoStock fallback and exact corporate-action/status source."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterable

from data_ingest.model import Bar, FetchBatch, SymbolSeries
from data_ingest.symbols import normalize_code, to_baostock_symbol


class BaoStockError(RuntimeError):
    """Raised when BaoStock rejects a session or query."""


FIELDS = "date,code,open,high,low,close,preclose,volume,amount,tradestatus,isST"


class BaoStockSource:
    """Serial BaoStock adapter; intentionally not used for full-market pulls."""

    def __init__(self, retries: int = 2, retry_sleep: float = 1.0):
        if retries < 1:
            raise ValueError("retries must be positive")
        self.retries = retries
        self.retry_sleep = retry_sleep

    @contextmanager
    def _session(self):
        import baostock as bs

        login = bs.login()
        if login.error_code != "0":
            raise BaoStockError(f"login failed: {login.error_code} {login.error_msg}")
        try:
            yield bs
        finally:
            bs.logout()

    def _query(self, bs, symbol: str, start: str, end: str, adjust: str) -> dict[str, Bar]:
        result = bs.query_history_k_data_plus(
            to_baostock_symbol(symbol),
            FIELDS,
            start_date=start,
            end_date=end,
            frequency="d",
            adjustflag=adjust,
        )
        if result.error_code != "0":
            raise BaoStockError(f"{symbol}: {result.error_code} {result.error_msg}")
        bars: dict[str, Bar] = {}
        while result.next():
            row = result.get_row_data()
            values = dict(zip(result.fields, row))
            bars[values["date"]] = Bar(
                date=values["date"],
                open=float(values["open"]),
                high=float(values["high"]),
                low=float(values["low"]),
                close=float(values["close"]),
                volume=float(values["volume"] or 0),
                amount=float(values["amount"] or 0),
                preclose=float(values["preclose"]) if values["preclose"] else None,
                tradestatus=int(values["tradestatus"]) if values["tradestatus"] else None,
                is_st=int(values["isST"]) if values["isST"] else None,
            )
        return bars

    def fetch(self, symbols: Iterable[str], start: str, end: str, adjust: str = "3") -> FetchBatch:
        codes = sorted({normalize_code(symbol) for symbol in symbols})
        batch = FetchBatch()
        with self._session() as bs:
            for code in codes:
                if code.startswith("BJ"):
                    batch.errors[code] = "BaoStock does not support Beijing Stock Exchange codes"
                    continue
                last_error: Exception | None = None
                for attempt in range(self.retries):
                    try:
                        bars = self._query(bs, code, start, end, adjust)
                        batch.series[code] = SymbolSeries(raw=bars, hfq={}, source=f"baostock:{adjust}")
                        break
                    except Exception as exc:  # noqa: BLE001 - provider boundary
                        last_error = exc
                        if attempt + 1 < self.retries:
                            time.sleep(self.retry_sleep * (attempt + 1))
                if code not in batch.series:
                    batch.errors[code] = f"{type(last_error).__name__}: {last_error}"
        return batch

    def fetch_pair(self, symbols: Iterable[str], start: str, end: str) -> FetchBatch:
        codes = sorted({normalize_code(symbol) for symbol in symbols})
        raw = self.fetch(codes, start, end, adjust="3")
        hfq = self.fetch(codes, start, end, adjust="1")
        merged = FetchBatch()
        for code in codes:
            raw_bars = raw.series.get(code)
            hfq_bars = hfq.series.get(code)
            if raw_bars is None or hfq_bars is None:
                merged.errors[code] = raw.errors.get(code) or hfq.errors.get(code) or "missing raw/hfq series"
                continue
            merged.series[code] = SymbolSeries(raw=raw_bars.raw, hfq=hfq_bars.raw, source="baostock")
        return merged
