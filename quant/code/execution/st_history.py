"""Dated ST membership evidence; absence is different from an empty ST list."""

from __future__ import annotations

from datetime import date
import re


def validate_st_history(history):
    if not isinstance(history, dict) or history.get("schema_version") != 1 or not history.get("source"):
        raise ValueError("ST history requires schema_version=1 and source")
    days = history.get("dates")
    if not isinstance(days, dict) or not days:
        raise ValueError("empty ST history coverage")
    for day, symbols in days.items():
        date.fromisoformat(day)
        if (
            not isinstance(symbols, list)
            or len(symbols) != len(set(symbols))
            or any(not isinstance(s, str) or not re.fullmatch(r"(?:SH|SZ|BJ)\d{6}", s) for s in symbols)
        ):
            raise ValueError(f"malformed ST history on {day}")
    return days


def st_flags(history, instruments, dates):
    days = validate_st_history(history)
    needed = {str(d)[:10] for d in dates}
    if not needed.issubset(days):
        raise ValueError(f"ST history missing execution dates: {sorted(needed - set(days))[:5]}")
    sets = {day: set(days[day]) for day in needed}
    return [str(s) in sets[str(d)[:10]] for s, d in zip(instruments, dates)]
