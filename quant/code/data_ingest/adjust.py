"""Qlib-compatible adjustment of raw daily bars.

The frozen chenditc history stores ``close = raw_close * factor`` and
``volume = raw_shares / 100 / factor``.  New rows must continue that scale.

Normal days use the raw return and carry the previous factor exactly.  On an
ex-date, BaoStock ``preclose`` gives the exact adjusted previous close; if it
is unavailable, the Sina hfq ratio is used as a conservative fallback.
"""

from __future__ import annotations

import math
from typing import Mapping

FIELDS = ("open", "high", "low", "close", "volume", "amount", "factor", "vwap", "change", "adjclose")
_NAN = float("nan")


class AdjustmentError(ValueError):
    """Raised when a base row cannot anchor the new series."""


def _finite(value: float) -> bool:
    return math.isfinite(value)


def build_rows(
    base_features: Mapping[str, Mapping[str, float]],
    series: Mapping[str, object],
    dates: list[str],
    *,
    base_end: str,
    preclose: Mapping[tuple[str, str], float] | None = None,
    hfq_change_threshold: float = 1e-4,
) -> dict[str, dict[str, dict[str, float]]]:
    """Build adjusted rows for every symbol/date.

    ``series`` values must expose ``raw`` and ``hfq`` mappings of
    :class:`data_ingest.model.Bar`.  Missing bars become all-NaN rows; a
    missing base anchor is a hard error.
    """
    preclose = preclose or {}
    output: dict[str, dict[str, dict[str, float]]] = {}
    for symbol, base in base_features.items():
        source = series.get(symbol)
        if source is None:
            continue
        raw_bars = getattr(source, "raw", {})
        hfq_bars = getattr(source, "hfq", {})
        base_close = float(base["close"])
        base_adj = float(base["adjclose"])
        base_factor = float(base["factor"])
        if not all(_finite(value) for value in (base_close, base_adj, base_factor)) or base_factor <= 0:
            raise AdjustmentError(f"{symbol}: invalid base features at {base_end}")
        prev_raw = base_close / base_factor
        prev_close = base_close
        prev_adj = base_adj
        hfq_base = hfq_bars.get(base_end)
        hfq_base_close = float(hfq_base.close) if hfq_base is not None else None
        rows: dict[str, dict[str, float]] = {}
        for date in dates:
            raw = raw_bars.get(date)
            hfq = hfq_bars.get(date)
            if raw is None or raw.volume <= 0 or raw.amount <= 0:
                rows[date] = {field: _NAN for field in FIELDS}
                continue
            pre = preclose.get((symbol, date))
            ex_date = pre is not None and abs(float(pre) - prev_raw) / max(abs(prev_raw), 1.0) > 1e-6
            if ex_date and pre and pre > 0:
                close = prev_close * raw.close / float(pre)
                adjclose = prev_adj * raw.close / float(pre)
            elif hfq is not None and hfq_base_close and hfq_base_close > 0:
                # A hfq ratio jump is a fallback corporate-action detector when
                # BaoStock preclose is unavailable.
                expected = prev_close * raw.close / prev_raw if prev_raw > 0 else _NAN
                hfq_close = base_close * (float(hfq.close) / hfq_base_close)
                if _finite(expected) and abs(hfq_close - expected) / max(abs(expected), 1.0) > hfq_change_threshold:
                    close = hfq_close
                    adjclose = base_adj * (float(hfq.close) / hfq_base_close)
                else:
                    close = expected
                    adjclose = prev_adj * raw.close / prev_raw if prev_raw > 0 else _NAN
            else:
                close = prev_close * raw.close / prev_raw if prev_raw > 0 else _NAN
                adjclose = prev_adj * raw.close / prev_raw if prev_raw > 0 else _NAN
            if not _finite(close) or close <= 0 or not _finite(adjclose):
                rows[date] = {field: _NAN for field in FIELDS}
                continue
            factor = close / raw.close
            volume = raw.volume / 100.0 / factor
            amount = raw.amount / 1000.0
            row = {
                "open": raw.open * factor,
                "high": raw.high * factor,
                "low": raw.low * factor,
                "close": close,
                "volume": volume,
                "amount": amount,
                "factor": factor,
                "vwap": amount * 1000.0 / (volume * 100.0) if volume > 0 else _NAN,
                "change": close / prev_close - 1.0 if prev_close > 0 else _NAN,
                "adjclose": adjclose,
            }
            rows[date] = row
            prev_raw = raw.close
            prev_close = close
            prev_adj = adjclose
        output[symbol] = rows
    return output
