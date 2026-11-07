"""Trading-calendar helpers for the overlay path."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class CalendarError(ValueError):
    """Raised when calendar inputs cannot support a fail-closed overlay."""


def read_calendar(provider_dir: str | Path) -> list[str]:
    path = Path(provider_dir) / "calendars" / "day.txt"
    if not path.is_file():
        raise CalendarError(f"missing provider calendar: {path}")
    dates = [line.strip()[:10] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not dates:
        raise CalendarError(f"empty provider calendar: {path}")
    if dates != sorted(set(dates)):
        raise CalendarError(f"calendar is not strictly ordered and unique: {path}")
    if any(not _DATE_RE.match(day) for day in dates):
        raise CalendarError(f"calendar contains a non-ISO date: {path}")
    return dates


def fetch_sina_calendar() -> list[str]:
    """Fetch the broad A-share trading calendar from Sina via AkShare.

    The endpoint returns a long history and future dates.  Callers must clip
    to the requested as-of date; a future date must never become a signal date.
    """
    import akshare as ak

    frame = ak.tool_trade_date_hist_sina()
    if "trade_date" not in frame.columns:
        raise CalendarError("Sina calendar response missing trade_date column")
    values = sorted({str(value)[:10] for value in frame["trade_date"].tolist()})
    if not values or values != sorted(set(values)):
        raise CalendarError("Sina calendar is empty or not ordered")
    return values


def resolve_target_dates(
    base_calendar: Iterable[str],
    asof: str,
    calendar_dates: Iterable[str] | None = None,
) -> list[str]:
    """Return the trading dates strictly after the frozen base and through asof."""
    base = list(base_calendar)
    if not base:
        raise CalendarError("base calendar is empty")
    asof = str(asof)[:10]
    if not _DATE_RE.match(asof):
        raise CalendarError(f"as-of date is not ISO: {asof!r}")
    calendar = list(calendar_dates) if calendar_dates is not None else fetch_sina_calendar()
    if not calendar or calendar != sorted(set(calendar)):
        raise CalendarError("target calendar is empty or not strictly ordered")
    if asof not in calendar:
        raise CalendarError(f"as-of date {asof} is not a trading day in the target calendar")
    if asof <= base[-1]:
        raise CalendarError(f"as-of date {asof} is not after base end {base[-1]}")
    dates = [day for day in calendar if base[-1] < day <= asof]
    if not dates or dates[-1] != asof:
        raise CalendarError(f"could not resolve target dates through {asof}")
    return dates


def assert_no_calendar_gaps(base_calendar: Iterable[str], new_dates: Iterable[str]) -> None:
    """Validate the structural shape of an append, not exchange holidays."""
    base = list(base_calendar)
    dates = list(new_dates)
    if not dates:
        raise CalendarError("new date list is empty")
    if dates != sorted(set(dates)):
        raise CalendarError("new dates are not strictly ordered and unique")
    if dates[0] <= base[-1]:
        raise CalendarError("new dates overlap the frozen base calendar")
    return
