"""Shared finite-number and date-sequence validation for research artifacts."""

from __future__ import annotations

import json
import math
from datetime import date


def finite_number(value, label, *, positive=False, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    if positive and number <= 0:
        raise ValueError(f"{label} must be positive")
    if nonnegative and number < 0:
        raise ValueError(f"{label} must be non-negative")
    return number


def finite_optional(value, label):
    if value is None:
        return None
    return finite_number(value, label)


def validate_dates(dates, label="dates", expected=None):
    if not isinstance(dates, (list, tuple)) or not dates:
        raise ValueError(f"{label} must be a non-empty sequence")
    normalized = []
    for value in dates:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError(f"{label} contains an invalid date: {value!r}")
        normalized.append(value)
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{label} contains duplicate dates")
    if normalized != sorted(normalized):
        raise ValueError(f"{label} must be sorted")
    if expected is not None:
        if list(normalized) != list(expected):
            raise ValueError(f"{label} does not match the expected sessions")
    return normalized


def finite_json_dumps(value, **kwargs):
    """Serialize with standard JSON only; never emit NaN/Infinity tokens."""
    return json.dumps(value, allow_nan=False, **kwargs)
