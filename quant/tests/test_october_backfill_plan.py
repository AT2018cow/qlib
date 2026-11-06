"""Backfill must train causally and retain the production calendar phase."""

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

SPEC = importlib.util.spec_from_file_location(
    "october_backfill_plan",
    Path(__file__).resolve().parents[1] / "code/research/regime_reselection/october_backfill_plan.py",
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_bootstrap_then_global_phase_with_no_missing_or_overlapping_signals():
    calendar = [str(x.date()) for x in pd.bdate_range("2026-01-01", periods=45)]
    blocks = module.fit_blocks(calendar, calendar[7], calendar[-1], origin=calendar[0])
    assert [b["fit_asof"] for b in blocks] == [calendar[i] for i in [6, 20, 40]]
    assert [d for b in blocks for d in b["signal_days"]] == calendar[6:]
    assert all(b["fit_asof"] <= b["signal_end"] for b in blocks)


def test_no_signal_before_first_provider_day():
    with pytest.raises(ValueError, match="previous close"):
        module.fit_blocks(["2026-09-18"], "2026-09-18", "2026-09-18")
