"""Time-series data boundary guards for the AT2018cow Qlib application.

No Qlib import is required, so the functions can be unit-tested independently.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from pathlib import Path
import re


def read_trading_calendar(provider_dir: str | Path) -> list[str]:
    path = Path(provider_dir) / "calendars" / "day.txt"
    if not path.is_file():
        raise FileNotFoundError(f"Cannot verify point-in-time split without calendar: {path}")
    days = [line.strip()[:10] for line in path.read_text().splitlines() if line.strip()]
    if not days or days != sorted(set(days)):
        raise ValueError(f"Missing, unordered or duplicate trading dates in {path}")
    if any(not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) for day in days):
        raise ValueError(f"Invalid calendar date in {path}")
    return days


def last_matured_sample(calendar: list[str], next_start: str, horizon: int) -> str:
    """Last t for which t+horizon is strictly before first bar >= next_start.

    This assumes Qlib Ref($close,-h) resolves h *trading* bars forward.
    """
    if horizon < 0:
        raise ValueError("horizon must be nonnegative")
    first_next = bisect_left(calendar, str(next_start)[:10])
    candidate = first_next - horizon - 1
    if candidate < 0 or first_next >= len(calendar):
        raise ValueError(f"Insufficient calendar for next_start={next_start}, h={horizon}")
    assert candidate + horizon < first_next
    return calendar[candidate]


def infer_label_horizon(cfg: dict) -> int:
    """Max lookahead for labels using Ref(...,-N); fail closed for unknown syntax."""
    dh = cfg["task"]["dataset"]["kwargs"]["handler"]["kwargs"]
    labels = dh.get("label", ["Ref($close, -2)/Ref($close, -1) - 1"])
    if not isinstance(labels, (list, tuple)) or not labels:
        raise ValueError("Cannot determine lookahead from label configuration")
    lookaheads = []
    for expr in labels:
        refs = re.findall(r"Ref\([^()]+,\s*(-?\d+)\s*\)", expr, flags=re.I)
        if not refs:
            raise ValueError(f"Unsupported label: {expr!r}; supply an explicit lookahead")
        lookaheads.append(max((max(0, -int(n)) for n in refs), default=0))
    return max(lookaheads)


def purge_cfg_splits(cfg: dict, calendar: list[str], horizon: int | None = None) -> dict:
    """Remove labels that would not exist before the NEXT stage starts.

    Alters training and validation segment ends and processor fitting end.
    Keep the test segment unchanged: its future labels are *not* used for fitting.
    """
    if horizon is None:
        horizon = infer_label_horizon(cfg)
    segments = cfg["task"]["dataset"]["kwargs"]["segments"]
    handler = cfg["task"]["dataset"]["kwargs"]["handler"]["kwargs"]
    for prev, nxt in (("train", "valid"), ("valid", "test")):
        if prev not in segments or nxt not in segments:
            raise ValueError("Expected chronological train/valid/test splits")
        prev_start, prev_end = map(str, segments[prev])
        nxt_start = str(segments[nxt][0])
        safe_end = last_matured_sample(calendar, nxt_start, horizon)
        actual_end = min(prev_end[:10], safe_end)
        if bisect_right(calendar, actual_end) <= bisect_left(calendar, prev_start[:10]):
            raise ValueError(f"Purging empties {prev}: {segments[prev]}")
        segments[prev][1] = actual_end
        if prev == "train":
            handler["fit_end_time"] = min(str(handler["fit_end_time"])[:10], actual_end)
        # Hard assertion on actual label observation, not calendar-day approximations.
        first_next = bisect_left(calendar, nxt_start[:10])
        last_prev = bisect_right(calendar, actual_end) - 1
        if last_prev + horizon >= first_next:
            raise AssertionError(f"{prev} label leaks into {nxt}")
    return cfg


def fixed_horizon_return(close, horizon: int):
    """For pd.Series with MultiIndex (datetime,instrument); output same index.

    Uses calendar shifts, not per-stock shifts that silently skip suspensions.
    The caller must supply calendar-aligned prices including NaN for suspended bars.
    """
    if horizon < 1:
        raise ValueError("horizon must be positive")
    return close.groupby(level="instrument").shift(-horizon) / close - 1
