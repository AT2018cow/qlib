"""Validated training/validation/test windows shared by research runners."""

from __future__ import annotations

from finite_values import validate_dates


def training_segments(
    calendar,
    *,
    fit_start: str,
    train_end: str,
    valid_start: str,
    valid_end: str,
    asof: str,
    signal_end: str,
    min_train_sessions: int = 252,
):
    """Build segments and require fit_start to control the model train start."""
    sessions = validate_dates([str(day)[:10] for day in calendar], "calendar")
    for label, value in (
        ("fit_start", fit_start),
        ("train_end", train_end),
        ("valid_start", valid_start),
        ("valid_end", valid_end),
        ("asof", asof),
        ("signal_end", signal_end),
    ):
        if value not in sessions:
            raise ValueError(f"{label} {value} is not in the provider calendar")
    if fit_start > train_end:
        raise ValueError("fit_start must not be after train_end")
    train_sessions = [day for day in sessions if fit_start <= day <= train_end]
    if len(train_sessions) < int(min_train_sessions):
        raise ValueError(f"training window has {len(train_sessions)} sessions; requires at least {min_train_sessions}")
    if not train_end < valid_start <= valid_end < asof <= signal_end:
        raise ValueError("train/valid/test windows are not strictly ordered")
    return {
        "train": [fit_start, train_end],
        "valid": [valid_start, valid_end],
        "test": [asof, signal_end],
        "fit_start": fit_start,
        "train_sessions": len(train_sessions),
    }
