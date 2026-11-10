"""BaoStock fallback and exact corporate-action/status source.

BaoStock is a serial, occasionally blocking session.  The production overlay
runs it in a killable child process with a hard deadline: a hung login/query
can never pin the provider worker, and completed symbols are written
incrementally so a retry keeps its progress.  Tests can use the in-process
path by leaving ``isolate=False``.
"""

from __future__ import annotations

import json
import multiprocessing
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

from data_ingest.model import Bar, FetchBatch, SymbolSeries
from data_ingest.symbols import normalize_code, to_baostock_symbol


class BaoStockError(RuntimeError):
    """Raised when BaoStock rejects a session or query."""


FIELDS = "date,code,open,high,low,close,preclose,volume,amount,tradestatus,isST"
HFQ_FIELDS = "date,close"


def _bar_from_values(values: dict) -> Bar:
    return Bar(
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


def _query_bars(bs, symbol: str, start: str, end: str, adjust: str) -> dict[str, Bar]:
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
        values = dict(zip(result.fields, result.get_row_data()))
        if not values.get("close"):
            continue
        bars[values["date"]] = _bar_from_values(values)
    return bars


def _query_hfq(bs, symbol: str, start: str, end: str) -> dict[str, float]:
    result = bs.query_history_k_data_plus(
        to_baostock_symbol(symbol),
        HFQ_FIELDS,
        start_date=start,
        end_date=end,
        frequency="d",
        adjustflag="1",
    )
    if result.error_code != "0":
        raise BaoStockError(f"{symbol}: hfq {result.error_code} {result.error_msg}")
    values: dict[str, float] = {}
    while result.next():
        row = dict(zip(result.fields, result.get_row_data()))
        if row.get("close"):
            values[row["date"]] = float(row["close"])
    return values


@contextmanager
def _session():
    import baostock as bs

    login = bs.login()
    if login.error_code != "0":
        raise BaoStockError(f"login failed: {login.error_code} {login.error_msg}")
    try:
        yield bs
    finally:
        bs.logout()


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


def _child_worker(payload: dict) -> None:
    """Run one BaoStock job in a separate process, writing per-symbol files."""
    output = Path(payload["output_dir"])
    output.mkdir(parents=True, exist_ok=True)
    deadline = float(payload["deadline_epoch"])
    mode = payload["mode"]
    try:
        with _session() as bs:
            for symbol in payload["symbols"]:
                target = output / f"{symbol}.json"
                if target.is_file():
                    continue
                if time.time() > deadline:
                    target.write_text(json.dumps({"error": "deadline exceeded"}))
                    break
                try:
                    raw = _query_bars(bs, symbol, payload["start"], payload["end"], payload.get("adjust", "3"))
                    body = {"bars": {date: _bar_payload(bar) for date, bar in raw.items()}}
                    if mode == "history" and payload.get("hfq"):
                        body["hfq_close"] = _query_hfq(bs, symbol, payload["start"], payload["end"])
                    target.write_text(json.dumps(body, allow_nan=False))
                except Exception as exc:  # noqa: BLE001 - provider boundary
                    target.write_text(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
    except Exception as exc:  # noqa: BLE001 - session boundary
        (output / "_session_error.json").write_text(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))


class BaoStockSource:
    """Serial BaoStock adapter; intentionally not used for full-market pulls."""

    def __init__(
        self,
        retries: int = 2,
        retry_sleep: float = 1.0,
        *,
        isolate: bool = False,
        deadline_seconds: float = 15 * 60,
    ):
        if retries < 1:
            raise ValueError("retries must be positive")
        self.retries = retries
        self.retry_sleep = retry_sleep
        self.isolate = isolate
        self.deadline_seconds = deadline_seconds

    @contextmanager
    def _session(self):
        with _session() as bs:
            yield bs

    def _query(self, bs, symbol: str, start: str, end: str, adjust: str) -> dict[str, Bar]:
        return _query_bars(bs, symbol, start, end, adjust)

    def _in_process(self, symbols: Iterable[str], start: str, end: str, adjust: str) -> FetchBatch:
        codes = sorted({normalize_code(symbol) for symbol in symbols})
        batch = FetchBatch()
        deadline = time.monotonic() + self.deadline_seconds
        with self._session() as bs:
            for code in codes:
                if code.startswith("BJ"):
                    batch.errors[code] = "BaoStock does not support Beijing Stock Exchange codes"
                    continue
                if time.monotonic() > deadline:
                    batch.errors[code] = "deadline exceeded"
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

    def _isolated(self, symbols: Iterable[str], start: str, end: str, adjust: str, mode: str = "target") -> dict:
        codes = sorted({normalize_code(symbol) for symbol in symbols})
        if not codes:
            return {"batch": FetchBatch(), "history": {}}
        output = Path(tempfile.mkdtemp(prefix="baostock-"))
        payload = {
            "symbols": codes,
            "start": start,
            "end": end,
            "adjust": adjust,
            "mode": mode,
            "hfq": mode == "history",
            "output_dir": str(output),
            "deadline_epoch": time.time() + max(1.0, self.deadline_seconds),
        }
        context = multiprocessing.get_context("spawn")
        process = context.Process(target=_child_worker, args=(payload,), daemon=True)
        process.start()
        process.join(self.deadline_seconds)
        timed_out = process.is_alive()
        if timed_out:
            process.terminate()
            process.join(5)
            if process.is_alive():
                process.kill()
                process.join(5)
        session_error = None
        session_path = output / "_session_error.json"
        if session_path.is_file():
            session_error = json.loads(session_path.read_text()).get("error")
        batch = FetchBatch()
        history: dict[str, dict] = {}
        for code in codes:
            path = output / f"{code}.json"
            if not path.is_file():
                batch.errors[code] = (
                    f"deadline exceeded{': ' + session_error if session_error else ''}"
                    if timed_out
                    else f"worker did not complete{': ' + session_error if session_error else ''}"
                )
                continue
            body = json.loads(path.read_text())
            if body.get("error"):
                batch.errors[code] = str(body["error"])
                continue
            bars = {date: _bar_from_payload(item) for date, item in (body.get("bars") or {}).items()}
            batch.series[code] = SymbolSeries(raw=bars, hfq={}, source=f"baostock:{adjust}")
            if mode == "history":
                history[code] = {
                    "raw": bars,
                    "hfq_close": {date: float(value) for date, value in (body.get("hfq_close") or {}).items()},
                }
        shutil.rmtree(output, ignore_errors=True)
        return {"batch": batch, "history": history}

    def fetch(
        self,
        symbols: Iterable[str],
        start: str,
        end: str,
        adjust: str = "3",
        *,
        deadline_seconds: float | None = None,
    ) -> FetchBatch:
        if deadline_seconds is not None:
            previous = self.deadline_seconds
            self.deadline_seconds = deadline_seconds
            try:
                return self.fetch(symbols, start, end, adjust)
            finally:
                self.deadline_seconds = previous
        if self.isolate:
            return self._isolated(symbols, start, end, adjust, mode="target")["batch"]
        return self._in_process(symbols, start, end, adjust)

    def fetch_history(
        self,
        symbols: Iterable[str],
        start: str,
        end: str,
        *,
        deadline_seconds: float | None = None,
    ) -> dict[str, dict]:
        """Return full unadjusted history plus BaoStock hfq close per symbol."""
        if deadline_seconds is not None:
            previous = self.deadline_seconds
            self.deadline_seconds = deadline_seconds
            try:
                return self.fetch_history(symbols, start, end)
            finally:
                self.deadline_seconds = previous
        if self.isolate:
            return self._isolated(symbols, start, end, "3", mode="history")["history"]
        codes = sorted({normalize_code(symbol) for symbol in symbols})
        output: dict[str, dict] = {}
        with self._session() as bs:
            for code in codes:
                try:
                    output[code] = {
                        "raw": self._query(bs, code, start, end, "3"),
                        "hfq_close": _query_hfq(bs, code, start, end),
                    }
                except Exception as exc:  # noqa: BLE001 - provider boundary
                    output[code] = {"error": f"{type(exc).__name__}: {exc}"}
        return output

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
