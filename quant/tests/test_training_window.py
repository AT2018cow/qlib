"""RR06: fit_start must control both handler fit and model train segment."""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code/research/regime_reselection"))

from training_window import training_segments


def _calendar(n=2500):
    days = []
    day = date(2015, 1, 1)
    while len(days) < n:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    return days


def test_nondefault_fit_start_enters_train_segment_and_handler_window():
    calendar = _calendar()
    segments = training_segments(
        calendar,
        fit_start="2018-01-02",
        train_end="2022-12-30",
        valid_start="2023-01-03",
        valid_end="2023-12-29",
        asof="2024-01-02",
        signal_end="2024-01-30",
    )
    assert segments["train"] == ["2018-01-02", "2022-12-30"]
    assert segments["fit_start"] == "2018-01-02"
    assert segments["valid"] == ["2023-01-03", "2023-12-29"]
    assert segments["test"] == ["2024-01-02", "2024-01-30"]


def test_fit_start_must_be_a_calendar_day_before_train_end_with_enough_samples():
    calendar = _calendar()
    with pytest.raises(ValueError, match="not in the provider calendar"):
        training_segments(
            calendar,
            fit_start="2018-01-06",
            train_end="2022-12-30",
            valid_start="2023-01-03",
            valid_end="2023-12-29",
            asof="2024-01-02",
            signal_end="2024-01-30",
        )
    with pytest.raises(ValueError, match="after train_end"):
        training_segments(
            calendar,
            fit_start="2023-01-03",
            train_end="2022-12-30",
            valid_start="2023-01-03",
            valid_end="2023-12-29",
            asof="2024-01-02",
            signal_end="2024-01-30",
        )
    with pytest.raises(ValueError, match="requires at least"):
        training_segments(
            calendar,
            fit_start="2022-12-01",
            train_end="2022-12-30",
            valid_start="2023-01-03",
            valid_end="2023-12-29",
            asof="2024-01-02",
            signal_end="2024-01-30",
        )


def test_spine_runner_wires_the_same_fit_start_to_both_windows():
    source = (
        Path(__file__).resolve().parents[1] / "code/research/regime_reselection/spine_and_replay_modal.py"
    ).read_text()
    assert "training_segments(" in source
    assert 'handler["fit_start_time"] = fit_start' in source
    assert 'dk["segments"] = {key: segments[key] for key in ("train", "valid", "test")}' in source
    assert '"train": ["2016-01-01", train_end]' not in source
