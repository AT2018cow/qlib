"""RR02: current-window and bear-block metrics must be after-cost net returns."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code/research/current_window"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code/execution"))

from current_window_net import METRIC_SCHEMA_VERSION, combine_bear_blocks, performance_from_report


def _report(initial_cash=100_000_000.0):
    gross = [0.0, 0.02, -0.01]
    cost = [0.0, 0.001, 0.002]
    bench = [0.0, 0.01, -0.005]
    account = [initial_cash]
    for g, c in zip(gross[1:], cost[1:]):
        account.append(account[-1] * (1.0 + g - c))
    return pd.DataFrame(
        {"return": gross, "cost": cost, "bench": bench, "account": account},
        index=pd.to_datetime(["2026-10-15", "2026-10-16", "2026-10-19"]),
    )


def test_nonzero_cost_net_is_below_gross_and_matches_account():
    result = performance_from_report(_report(), initial_cash=100_000_000.0, backtest_start="2026-10-15")
    gross_total = (1.02 * 0.99) - 1.0
    net_total = (1.019 * 0.988) - 1.0
    assert result["metric_schema_version"] == METRIC_SCHEMA_VERSION
    assert result["strategy_total_return_net"] == pytest.approx(net_total, abs=1e-12)
    assert result["cumulative_return"] == result["strategy_total_return_net"]
    assert result["strategy_total_return_net"] < gross_total
    assert result["total_cost"] == pytest.approx(0.003, abs=1e-12)
    assert result["final_account"] == pytest.approx(100_000_000.0 * (1.019 * 0.988), abs=1e-6)
    assert result["account_return_max_error"] is not None
    assert result["account_return_max_error"] <= 1e-6
    assert result["daily_net_return"] == [0.019, -0.012]
    assert result["dates"] == ["2026-10-16", "2026-10-19"]


@pytest.mark.parametrize(
    "damage",
    ["nan_gross", "inf_cost", "negative_cost", "bool_return", "bad_account", "duplicate_date"],
)
def test_nonfinite_or_inconsistent_reports_fail_closed(damage):
    report = _report()
    if damage == "nan_gross":
        report.loc[report.index[1], "return"] = float("nan")
    elif damage == "inf_cost":
        report.loc[report.index[1], "cost"] = float("inf")
    elif damage == "negative_cost":
        report.loc[report.index[1], "cost"] = -0.001
    elif damage == "bool_return":
        report["return"] = report["return"].astype(object)
        report.loc[report.index[1], "return"] = True
    elif damage == "bad_account":
        report.loc[report.index[1], "account"] = 0.0
    elif damage == "duplicate_date":
        report.index = pd.to_datetime(["2026-10-15", "2026-10-15", "2026-10-19"])
    with pytest.raises(ValueError):
        performance_from_report(report, initial_cash=100_000_000.0, backtest_start="2026-10-15")


def _block(dates, gross, cost, bench):
    net = [g - c for g, c in zip(gross, cost)]
    account = 100_000_000.0
    accounts = []
    for value in net:
        account *= 1.0 + value
        accounts.append(account)
    return {
        "verified_net": True,
        "initial_cash": 100_000_000.0,
        "account_return_max_error": 0.0,
        "dates": dates,
        "daily_gross_return": gross,
        "daily_cost": cost,
        "daily_net_return": net,
        "daily_benchmark": bench,
        "daily_account": accounts,
        "final_account": accounts[-1],
        "identity": {
            "market": "csi300",
            "profile": "fixture-profile",
            "provider_fingerprint": "a" * 64,
            "calendar_sha256": "b" * 64,
            "strategy_contract_sha256": "c" * 64,
        },
        "retrain_asof": "2026-10-15" if dates[0] == "2026-10-16" else "2026-10-19",
        "eval_end": dates[-1],
    }


def test_bear_blocks_combine_only_verified_net_and_reject_gaps_or_duplicates():
    first = _block(["2026-10-16", "2026-10-19"], [0.02, -0.01], [0.001, 0.002], [0.01, -0.005])
    second = _block(["2026-10-20"], [0.015], [0.001], [0.004])
    combined = combine_bear_blocks(
        [first, second],
        expected_dates=["2026-10-16", "2026-10-19", "2026-10-20"],
        expected_identity=first["identity"],
    )
    assert combined["metric_schema_version"] == METRIC_SCHEMA_VERSION
    assert combined["strategy_total_return_net"] == pytest.approx((1.019 * 0.988 * 1.014) - 1.0, abs=1e-6)
    assert combined["total_cost"] == pytest.approx(0.004, abs=1e-12)
    assert combined["final_account"] > 0
    with pytest.raises(ValueError, match="overlap|expected|contiguous"):
        combine_bear_blocks(
            [first, first], expected_dates=["2026-10-16", "2026-10-19"], expected_identity=first["identity"]
        )
    with pytest.raises(ValueError, match="expected"):
        combine_bear_blocks(
            [first, second],
            expected_dates=["2026-10-16", "2026-10-19", "2026-10-21"],
            expected_identity=first["identity"],
        )
    with pytest.raises(ValueError, match="verified net"):
        combine_bear_blocks(
            [{**first, "verified_net": False}], expected_dates=first["dates"], expected_identity=first["identity"]
        )
    broken = {**first, "daily_net_return": [0.02, -0.01]}
    with pytest.raises(ValueError, match="final_account|gross/cost/net|daily account"):
        combine_bear_blocks([broken], expected_dates=first["dates"], expected_identity=first["identity"])


def test_current_window_correction_inventory_is_reproducible_and_keeps_original_bytes():
    import hashlib
    import json

    root = Path(__file__).resolve().parents[2]
    source = root / "quant/data/current_window_backtest/2026-10-16_2026-10-29_candidate_results.json"
    inventory = root / "quant/data/current_window_backtest/2026-10-16_2026-10-29_correction_inventory.json"
    original = source.read_bytes()
    payload = json.loads(inventory.read_text())
    assert payload["source_sha256"] == hashlib.sha256(original).hexdigest()
    assert len(payload["units"]) == 13
    assert payload["ranking_changed"] is False
    assert all(unit["account_net_return"] < unit["reported_cumulative_return"] for unit in payload["units"])
    assert source.read_bytes() == original
    rebuilt = __import__("current_window_net").build_correction_inventory(
        source, source_label=str(source.relative_to(root))
    )
    assert rebuilt == payload


@pytest.mark.parametrize(
    "damage",
    ["final_account", "account_error", "initial_cash", "daily_cost", "daily_gross", "daily_account"],
)
def test_bear_block_account_and_cost_tampering_is_rejected(damage):
    first = _block(["2026-10-16", "2026-10-19"], [0.02, -0.01], [0.001, 0.002], [0.01, -0.005])
    second = _block(["2026-10-20"], [0.015], [0.001], [0.004])
    expected = ["2026-10-16", "2026-10-19", "2026-10-20"]
    if damage == "final_account":
        first["final_account"] = 1.0
    elif damage == "account_error":
        first["account_return_max_error"] = 0.1
    elif damage == "initial_cash":
        first["initial_cash"] = 1.0
    elif damage == "daily_cost":
        first["daily_cost"] = [0.0, 0.002]
    elif damage == "daily_gross":
        first["daily_gross_return"] = [0.03, -0.01]
    elif damage == "daily_account":
        first["daily_account"][0] = 1.0
    with pytest.raises(ValueError):
        combine_bear_blocks([first, second], expected_dates=expected, expected_identity=first["identity"])


def test_bear_blocks_reject_mixed_market_profile_provider_and_contract():
    first = _block(["2026-10-16"], [0.02], [0.001], [0.01])
    second = _block(["2026-10-19"], [0.015], [0.001], [0.004])
    second["identity"] = {
        "market": "csi500",
        "profile": "other-profile",
        "provider_fingerprint": "d" * 64,
        "calendar_sha256": "e" * 64,
        "strategy_contract_sha256": "f" * 64,
    }
    with pytest.raises(ValueError, match="identity"):
        combine_bear_blocks(
            [first, second],
            expected_dates=["2026-10-16", "2026-10-19"],
            expected_identity=first["identity"],
        )


def test_correction_inventory_rejects_a_missing_registered_unit(tmp_path):
    import json

    root = Path(__file__).resolve().parents[2]
    source = root / "quant/data/current_window_backtest/2026-10-16_2026-10-29_candidate_results.json"
    rows = json.loads(source.read_text())
    reduced = tmp_path / "reduced.json"
    reduced.write_text(json.dumps(rows[:-1], allow_nan=False))
    with pytest.raises(ValueError, match="unit|registered|expected"):
        __import__("current_window_net").build_correction_inventory(reduced)
