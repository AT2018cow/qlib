"""Helpers for calendar-aligned public chart series."""

from __future__ import annotations

from typing import Iterable, Optional


def align_bin_values_to_calendar_tail(
    *,
    start_index: int,
    values: Iterable[float],
    calendar_length: int,
    window: int = 60,
) -> list[Optional[float]]:
    """Align one Qlib bin series to the trailing calendar window.

    Qlib day-bin files store a global calendar start index followed by values.
    The returned list is exactly the trailing window calendar slots (or fewer
    if the calendar itself is shorter). Missing dates remain None so the
    frontend can preserve gaps instead of compressing time.
    """
    vals = list(values)
    if calendar_length < 0:
        raise ValueError("calendar_length must be non-negative")
    if window <= 0:
        raise ValueError("window must be positive")

    first_calendar_index = max(0, calendar_length - window)
    out: list[Optional[float]] = []
    for cal_i in range(first_calendar_index, calendar_length):
        local_i = cal_i - int(start_index)
        if local_i < 0 or local_i >= len(vals):
            out.append(None)
            continue
        value = vals[local_i]
        try:
            number = float(value)
        except (TypeError, ValueError):
            out.append(None)
            continue
        if number != number:  # NaN
            out.append(None)
        else:
            out.append(round(number, 4))
    return out
