"""Causal prediction blocks on the unchanged production retrain calendar."""

from __future__ import annotations

from qlib_live_retrain import RETRAIN_ORIGIN, retrain_due_calendar, should_retrain


def fit_blocks(calendar, first_execution, last_signal, *, interval=20, origin=RETRAIN_ORIGIN):
    if calendar != sorted(set(calendar)) or first_execution not in calendar or last_signal not in calendar:
        raise ValueError("backfill endpoints must be in the ordered provider calendar")
    first_i = calendar.index(first_execution) - 1
    last_i = calendar.index(last_signal)
    if first_i < 0 or first_i > last_i:
        raise ValueError("previous close signal is unavailable")
    starts = []
    previous = None
    for i in range(first_i, last_i + 1):
        day = calendar[i]
        if (
            previous is None
            or retrain_due_calendar(calendar, day, interval, origin)
            or should_retrain(calendar, day, previous, interval)
        ):
            starts.append(i)
            previous = day
    blocks = []
    for k, i in enumerate(starts):
        end_i = starts[k + 1] - 1 if k + 1 < len(starts) else last_i
        blocks.append({"fit_asof": calendar[i], "signal_end": calendar[end_i], "signal_days": calendar[i : end_i + 1]})
    return blocks
