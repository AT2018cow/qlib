"""Capital identity is distinct from the first closing account value."""

import hashlib
import json

import pandas as pd
import pytest

from audit_repair_impact import report_initial_capital
from portfolio_performance import portfolio_performance


@pytest.mark.parametrize("initial_cash", [1_000_000, 100_000_000])
def test_declared_capital_supports_current_and_historical_scales(tmp_path, initial_cash):
    report = pd.DataFrame(
        {"account": [initial_cash * 0.99, initial_cash * 0.9999], "return": [-0.01, 0.01], "cost": 0.0, "bench": 0.0},
        index=pd.to_datetime(["2026-10-08", "2026-10-09"]),
    )
    path = tmp_path / "report.parquet"
    report.to_parquet(path)
    (tmp_path / "metrics.json").write_text(json.dumps({"account": initial_cash}))
    capital, sources = report_initial_capital(path, report, tmp_path, {})
    assert capital == initial_cash
    assert capital != report.account.iloc[0]
    assert sources == ["metrics.json:account"]
    actual = portfolio_performance(report, initial_cash=capital)
    assert actual["strategy_total_return"] == pytest.approx(-0.0001)


def test_unknown_or_conflicting_capital_is_never_guessed(tmp_path):
    path = tmp_path / "report.parquet"
    report = pd.DataFrame({"account": [990_000]})
    report.to_parquet(path)
    with pytest.raises(ValueError, match="missing declared"):
        report_initial_capital(path, report, tmp_path, {})
    report.attrs["initial_cash"] = 1_000_000
    (tmp_path / "metrics.json").write_text(json.dumps({"account": 100_000_000}))
    with pytest.raises(ValueError, match="conflicting"):
        report_initial_capital(path, report, tmp_path, {})


@pytest.mark.parametrize("value", [True, 0, -1, float("nan"), float("inf")])
def test_invalid_capital_is_rejected(tmp_path, value):
    path = tmp_path / "report.parquet"
    report = pd.DataFrame({"account": [990_000]})
    report.to_parquet(path)
    report.attrs["initial_cash"] = value
    with pytest.raises(ValueError, match="invalid declared"):
        report_initial_capital(path, report, tmp_path, {})


def test_legacy_contract_only_applies_to_exact_report_bytes(tmp_path):
    path = tmp_path / "report.parquet"
    report = pd.DataFrame({"account": [99_000_000]})
    report.to_parquet(path)
    contracts = {
        "report.parquet": {"initial_cash": 100_000_000, "report_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    }
    assert report_initial_capital(path, report, tmp_path, contracts)[0] == 100_000_000
    path.write_bytes(b"changed archive")
    with pytest.raises(ValueError, match="fingerprint"):
        report_initial_capital(path, report, tmp_path, contracts)
