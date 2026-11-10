"""Controlled historical backfill for index members without frozen features.

A new constituent can be absent from the frozen provider's feature tree (for
example a recent IPO, or a name that was never in CSI300/500/1000).  Silently
dropping it would change the PIT universe, and fabricating features is worse.
This module reconstructs the same adjusted coordinate as the overlay:

* anchor adjusted close at ``1.0`` on the symbol's first available bar inside
  the provider calendar (the provider's own convention, verified against
  frozen bins);
* on an ex-date use ``prev_close * raw_close / preclose`` (BaoStock is the
  exact corporate-action source);
* on a normal day use ``prev_close * raw_close / prev_raw``;
* ``factor = close / raw_close``; ``volume = shares / 100 / factor``;
  ``amount = CNY / 1000``; ``vwap`` follows ``adjust.build_rows``.

BaoStock hfq close is carried only as ``adjclose``; it is not used by the
canonical Alpha158 features or execution.  The provenance is recorded in the
provider manifest so an operator can see that the history was backfilled.
"""

from __future__ import annotations

import math
from typing import Mapping

from data_ingest.model import Bar

FIELDS = ("open", "high", "low", "close", "volume", "amount", "factor", "vwap", "change", "adjclose")


class BackfillError(RuntimeError):
    """Raised when a new member's history cannot be reconstructed safely."""


def _finite(value) -> bool:
    return value is not None and isinstance(value, (int, float)) and math.isfinite(float(value))


def _row_from_values(
    date: str,
    raw: Bar,
    *,
    close: float,
    previous_close: float | None,
    hfq_close: float | None,
) -> dict[str, float]:
    factor = close / raw.close
    volume = raw.volume / 100.0 / factor
    amount = raw.amount / 1000.0
    return {
        "open": raw.open * factor,
        "high": raw.high * factor,
        "low": raw.low * factor,
        "close": close,
        "volume": volume,
        "amount": amount,
        "factor": factor,
        "vwap": (amount * 1000.0 / (volume * 100.0)) if volume > 0 else float("nan"),
        "change": (close / previous_close - 1.0) if previous_close and previous_close > 0 else float("nan"),
        "adjclose": float(hfq_close) if hfq_close and hfq_close > 0 else close,
    }


def build_backfill(
    symbol: str,
    raw_bars: Mapping[str, Bar],
    *,
    hfq_close: Mapping[str, float] | None,
    base_calendar: list[str],
    target_dates: list[str],
) -> dict:
    """Build history and target rows for one symbol.

    Returns ``{"history": {date: row}, "target": {date: row}, "start_index":
    int, "anchor": date, "provenance": {...}}``.  Dates before the first valid
    bar are omitted (the bin starts at ``start_index``).
    """
    if not base_calendar:
        raise BackfillError("base calendar is empty")
    ordered = sorted((date, bar) for date, bar in raw_bars.items() if date in set(base_calendar) | set(target_dates))
    valid = [
        (date, bar)
        for date, bar in ordered
        if _finite(bar.close) and bar.close > 0 and _finite(bar.volume) and bar.volume > 0 and _finite(bar.amount)
    ]
    if not valid:
        raise BackfillError(f"{symbol}: no usable BaoStock bars in the base/target window")
    anchor = max(base_calendar[0], valid[0][0])
    if anchor not in base_calendar:
        raise BackfillError(f"{symbol}: first usable bar {anchor} is not in the base calendar")
    anchor_index = base_calendar.index(anchor)
    if anchor_index >= len(base_calendar):
        raise BackfillError(f"{symbol}: first usable bar {anchor} is after the base end")

    history: dict[str, dict[str, float]] = {}
    target: dict[str, dict[str, float]] = {}
    previous_raw: float | None = None
    previous_close: float | None = None
    first = True
    for date in base_calendar[anchor_index:] + list(target_dates):
        bar = raw_bars.get(date)
        if bar is None or not _finite(bar.close) or bar.close <= 0 or not _finite(bar.volume) or bar.volume <= 0:
            row = {field: float("nan") for field in FIELDS}
        elif first:
            close = 1.0
            row = _row_from_values(
                date,
                bar,
                close=close,
                previous_close=None,
                hfq_close=(hfq_close or {}).get(date),
            )
            previous_raw, previous_close = bar.close, close
            first = False
        else:
            pre = bar.preclose
            ex_date = (
                _finite(pre)
                and pre > 0
                and previous_raw
                and previous_raw > 0
                and abs(float(pre) - previous_raw) / max(abs(previous_raw), 1.0) > 1e-6
            )
            if ex_date:
                close = previous_close * bar.close / float(pre)
            else:
                close = previous_close * bar.close / previous_raw
            row = _row_from_values(
                date,
                bar,
                close=close,
                previous_close=previous_close,
                hfq_close=(hfq_close or {}).get(date),
            )
            previous_raw, previous_close = bar.close, close
        if date in target_dates:
            target[date] = row
        else:
            history[date] = row
    if not any(_finite(row.get("close")) for row in history.values()):
        raise BackfillError(f"{symbol}: reconstructed history has no finite close")
    if not any(_finite(row.get("close")) for row in target.values()):
        raise BackfillError(f"{symbol}: reconstructed target rows have no finite close")
    return {
        "history": history,
        "target": target,
        "start_index": anchor_index,
        "anchor": anchor,
        "provenance": {
            "symbol": symbol,
            "source": "baostock:adjustflag=3 + preclose",
            "anchor": anchor,
            "start_index": anchor_index,
            "history_rows": len(history),
            "target_rows": len(target),
            "first_bar": valid[0][0],
            "adjclose_source": "baostock:adjustflag=1" if hfq_close else "close",
        },
    }
