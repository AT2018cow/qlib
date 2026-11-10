"""Sina daily bars through AkShare, used as the primary incremental source.

The upstream ``akshare.stock_zh_a_daily`` helper downloads the full history
twice for a raw/hfq pair (and decodes the JavaScript payload twice).  The
overlay only needs one history download plus one adjustment-factor download per
symbol, so this adapter implements that pair directly:

* one ``hisdata_klc2/klc_kl.js`` request (history + amount),
* one ``hfq.js`` request (cumulative hfq factors),
* raw and hfq frames are reconstructed with AkShare's published
  rounding/deduplication semantics.

The AkShare internals are imported lazily and guarded by a version check.  If
they are unavailable, the adapter falls back to the public ``ak.stock_zh_a_daily``
API (still avoiding the duplicate hfq history/decode call by using
``adjust="hfq-factor"``).  Per-request timeouts and a whole-batch deadline are
mandatory so a slow free endpoint cannot pin a worker forever.
"""

from __future__ import annotations

import ast
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable

from data_ingest.model import Bar, FetchBatch, SymbolSeries
from data_ingest.symbols import SymbolError, normalize_code, to_sina_symbol

AKSHARE_PINNED = "1.18.94"
_UA = {"User-Agent": "Mozilla/5.0 (compatible; qlib-provider/1.0)"}


class SinaError(RuntimeError):
    """Raised when a Sina request cannot be completed after retries."""


_thread_state = threading.local()
_fast_version_checked = None


def _fast_available() -> bool:
    """The fast path depends on pinned AkShare internals; mismatch -> fallback."""
    global _fast_version_checked
    if _fast_version_checked is None:
        try:
            from importlib.metadata import version

            _fast_version_checked = version("akshare") == AKSHARE_PINNED
        except Exception:  # noqa: BLE001 - optional dependency boundary
            _fast_version_checked = False
    return _fast_version_checked


def _decoder():
    """Return a per-thread MiniRacer instance with the AkShare decoder loaded."""
    from akshare.stock.stock_zh_a_sina import hk_js_decode
    from py_mini_racer import py_mini_racer

    state = getattr(_thread_state, "decoder", None)
    if state is None:
        state = py_mini_racer.MiniRacer()
        state.eval(hk_js_decode)
        _thread_state.decoder = state
    return state


def _akshare_urls():
    from akshare.stock.stock_zh_a_sina import zh_sina_a_stock_hfq_url, zh_sina_a_stock_hist_url

    return zh_sina_a_stock_hist_url, zh_sina_a_stock_hfq_url


def _http_text(url: str, *, timeouts: tuple[int, int], retries: int, backoff: float) -> str:
    import requests

    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = requests.get(url, timeout=timeouts, headers=_UA)
            response.raise_for_status()
            if not response.text:
                raise SinaError("empty response")
            return response.text
        except Exception as exc:  # noqa: BLE001 - provider boundary
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(backoff * (attempt + 1))
    raise SinaError(f"{type(last_error).__name__}: {last_error}")


def _history_frame(text: str):
    """Decode the AkShare history payload into the pre-truncation raw frame."""
    import pandas as pd

    payload = text.split("=")[1].split(";")[0].replace('"', "")
    decoded = _decoder().call("d", payload)
    if not decoded:
        raise SinaError("history payload decoded to an empty series")
    frame = pd.DataFrame(decoded)
    frame.index = pd.to_datetime(frame["date"], errors="coerce").dt.date
    del frame["date"]
    for optional in ("prevclose", "postVol", "postAmt"):
        if optional in frame.columns:
            del frame[optional]
    frame = frame.astype("float")
    required = ["open", "high", "low", "close", "volume", "amount"]
    missing = [name for name in required if name not in frame.columns]
    if missing:
        raise SinaError(f"history payload missing fields: {missing}")
    return frame[required]


def _factor_series(text: str):
    """Parse the hfq factor payload without ``eval``."""
    import pandas as pd

    body = text.split("=", 1)[1].strip().split("\n", 1)[0].strip().rstrip(";")
    parsed = ast.literal_eval(body)
    data = parsed.get("data") if isinstance(parsed, dict) else None
    if not data:
        raise SinaError("hfq factor payload is empty")
    values = {}
    for item in data:
        if isinstance(item, dict):
            date, factor = item.get("d"), item.get("f")
        else:
            date, factor = item[0], item[1]
        if date is None or factor is None:
            continue
        values[str(date)[:10]] = float(factor)
    if not values:
        raise SinaError("hfq factor payload has no usable rows")
    series = pd.Series(values, dtype="float64")
    series.index = pd.to_datetime(series.index).date
    return series.sort_index()


def _date_slice(frame, start: str, end: str):
    dates = frame.index
    mask = [start <= str(day)[:10] <= end for day in dates]
    return frame.loc[mask]


def _raw_frame(full, start: str, end: str):
    """Reproduce AkShare's raw-frame pipeline (truncate, dedupe, round)."""
    frame = _date_slice(full.copy(), start, end)
    frame = frame.drop_duplicates(subset=["open", "high", "low", "close", "volume", "amount"])
    frame[["open", "high", "low", "close"]] = frame[["open", "high", "low", "close"]].round(2)
    frame = frame.dropna()
    frame = frame.drop_duplicates()
    return frame


def _hfq_frame(full, factor, start: str, end: str):
    """Reproduce AkShare's hfq-frame pipeline without a second history request."""
    values = factor.reindex(full.index)
    # AkShare merges outer, forward-fills, then drops rows without a factor.
    values = values.ffill()
    frame = full.copy()
    frame["hfq_factor"] = values
    frame = frame.dropna()
    frame = frame.drop_duplicates(subset=["open", "high", "low", "close", "volume", "amount"])
    for field in ("open", "high", "low", "close"):
        frame[field] = frame[field] * frame["hfq_factor"]
    del frame["hfq_factor"]
    frame = _date_slice(frame, start, end)
    frame[["open", "high", "low", "close"]] = frame[["open", "high", "low", "close"]].round(2)
    frame = frame.dropna()
    return frame


def _frame_bars(frame) -> dict[str, Bar]:
    bars: dict[str, Bar] = {}
    for index, row in frame.iterrows():
        date = str(index)[:10]
        bars[date] = Bar(
            date=date,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
            amount=float(row["amount"]),
        )
    return bars


class SinaSource:
    """Threaded AkShare/Sina daily reader.

    The source is intentionally thin: it returns provider bars, not adjusted
    Qlib rows.  The overlay layer owns the adjustment contract.
    """

    def __init__(
        self,
        workers: int = 8,
        retries: int = 3,
        retry_sleep: float = 1.5,
        *,
        connect_timeout: int = 10,
        read_timeout: int = 60,
        request_retries: int = 2,
        batch_deadline: float = 45 * 60,
        prefer_fast: bool = True,
    ):
        if workers < 1 or retries < 1 or request_retries < 1:
            raise ValueError("workers and retries must be positive")
        self.workers = workers
        self.retries = retries
        self.retry_sleep = retry_sleep
        self.timeouts = (connect_timeout, read_timeout)
        self.request_retries = request_retries
        self.batch_deadline = batch_deadline
        self.prefer_fast = prefer_fast

    def _fast_pair(self, symbol: str, start: str, end: str) -> tuple[dict[str, Bar], dict[str, Bar]]:
        if not _fast_available():
            raise ImportError(f"akshare is not pinned to {AKSHARE_PINNED}")
        hist_url, factor_url = _akshare_urls()
        sina_symbol = to_sina_symbol(symbol)
        history_text = _http_text(
            hist_url.format(sina_symbol),
            timeouts=self.timeouts,
            retries=self.request_retries,
            backoff=self.retry_sleep,
        )
        factor_text = _http_text(
            factor_url.format(sina_symbol),
            timeouts=self.timeouts,
            retries=self.request_retries,
            backoff=self.retry_sleep,
        )
        full = _history_frame(history_text)
        factor = _factor_series(factor_text)
        raw = _raw_frame(full, start, end)
        hfq = _hfq_frame(full, factor, start, end)
        return _frame_bars(raw), _frame_bars(hfq)

    def _public_pair(self, symbol: str, start: str, end: str) -> tuple[dict[str, Bar], dict[str, Bar]]:
        """Fallback using public AkShare calls (one history + one factor)."""
        import akshare as ak
        import pandas as pd

        sina_symbol = to_sina_symbol(symbol)
        raw = ak.stock_zh_a_daily(
            symbol=sina_symbol,
            start_date=start.replace("-", ""),
            end_date=end.replace("-", ""),
            adjust="",
        )
        factors = ak.stock_zh_a_daily(
            symbol=sina_symbol,
            start_date=start.replace("-", ""),
            end_date=end.replace("-", ""),
            adjust="hfq-factor",
        )
        raw = raw.set_index(pd.to_datetime(raw["date"], errors="coerce").dt.date)
        del raw["date"]
        factor = pd.Series(
            {str(row["date"])[:10]: float(row["hfq_factor"]) for _, row in factors.iterrows()},
            dtype="float64",
        )
        factor.index = pd.to_datetime(factor.index).date
        factor = factor.sort_index()
        hfq = _hfq_frame(raw[["open", "high", "low", "close", "volume", "amount"]], factor, start, end)
        return _frame_bars(raw), _frame_bars(hfq)

    def _fetch_pair_one(self, symbol: str, start: str, end: str) -> tuple[dict[str, Bar], dict[str, Bar]]:
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                if self.prefer_fast:
                    try:
                        return self._fast_pair(symbol, start, end)
                    except (ImportError, AttributeError) as exc:
                        # AkShare internals changed: keep the run alive with the
                        # public API, and surface the fallback in the batch stats.
                        setattr(_thread_state, "fast_unavailable", f"{type(exc).__name__}: {exc}")
                        return self._public_pair(symbol, start, end)
                return self._public_pair(symbol, start, end)
            except Exception as exc:  # noqa: BLE001 - provider boundary
                last_error = exc
                if attempt + 1 < self.retries:
                    time.sleep(self.retry_sleep * (attempt + 1))
        raise SinaError(f"{symbol}: {type(last_error).__name__}: {last_error}")

    def _fetch_one(self, ak_module, symbol: str, start: str, end: str, adjust: str) -> dict[str, Bar]:
        """Compatibility helper retained for callers of the single-adjust API."""
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                frame = ak_module.stock_zh_a_daily(
                    symbol=to_sina_symbol(symbol),
                    start_date=start.replace("-", ""),
                    end_date=end.replace("-", ""),
                    adjust=adjust,
                )
                bars: dict[str, Bar] = {}
                for _, row in frame.iterrows():
                    date = str(row["date"])[:10]
                    bars[date] = Bar(
                        date=date,
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row["volume"]),
                        amount=float(row["amount"]),
                    )
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

    def fetch_pair(
        self,
        symbols: Iterable[str],
        start: str,
        end: str,
        *,
        on_progress=None,
        progress_every: int = 200,
    ) -> FetchBatch:
        """Fetch raw and hfq bars with one history and one factor request per symbol.

        ``start`` is normally the frozen base date, so the response includes
        the base close used to detect the first corporate action.
        """
        codes = sorted({normalize_code(symbol) for symbol in symbols})
        merged = FetchBatch()
        if not codes:
            return merged
        started = time.monotonic()
        deadline = started + self.batch_deadline
        completed = 0
        pending = FetchBatch()
        batch_index = 0

        def flush() -> None:
            nonlocal pending, batch_index
            if on_progress is None or not (pending.series or pending.errors):
                pending = FetchBatch()
                return
            batch_index += 1
            on_progress(
                pending,
                batch_index,
                {
                    "completed": completed,
                    "errors": len(merged.errors),
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                },
            )
            pending = FetchBatch()

        executor = ThreadPoolExecutor(max_workers=self.workers)
        futures = {executor.submit(self._fetch_pair_one, code, start, end): code for code in codes}
        try:
            for future in as_completed(futures, timeout=max(0.001, deadline - time.monotonic())):
                code = futures[future]
                try:
                    raw, hfq = future.result()
                except Exception as exc:  # noqa: BLE001 - provider boundary
                    merged.errors[code] = f"{type(exc).__name__}: {exc}"
                    pending.errors[code] = merged.errors[code]
                    continue
                if not raw:
                    merged.errors[code] = "empty history response"
                    pending.errors[code] = merged.errors[code]
                    continue
                merged.series[code] = SymbolSeries(raw=raw, hfq=hfq, source="sina")
                pending.series[code] = merged.series[code]
                completed += 1
                if len(pending.series) + len(pending.errors) >= progress_every:
                    flush()
        except TimeoutError:
            for future, code in futures.items():
                if not future.done():
                    merged.errors[code] = "batch deadline exceeded"
                    pending.errors[code] = merged.errors[code]
        finally:
            flush()
            for future in futures:
                if not future.done():
                    future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
        elapsed = time.monotonic() - started
        merged.stats = {
            "symbols": len(codes),
            "completed": completed,
            "errors": len(merged.errors),
            "elapsed_seconds": round(elapsed, 3),
            "workers": self.workers,
            "retries": self.retries,
            "request_retries": self.request_retries,
            "batch_deadline_seconds": self.batch_deadline,
            "fast_unavailable": getattr(_thread_state, "fast_unavailable", None),
        }
        return merged
