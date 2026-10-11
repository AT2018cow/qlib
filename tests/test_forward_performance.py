import pytest

from forward_performance import FORWARD_PERFORMANCE_START, build_forward_performance


def _artifact(date, open_value, close_value, lineage="stage_b_winner_canonical", pending=None):
    report = {
        "date": date,
        "portfolio_value_pre_trade_open": open_value,
        "portfolio_value_close": close_value,
        "valuation_verified": True,
        "opening_cash": 0.0,
        "cash": 0.0,
        "opening_positions": {"A": {"shares": 10.0}},
        "position_marks": {"A": {"shares": 10.0, "price": close_value / 10.0, "reason": "EXCHANGE_CLOSE"}},
        "executed_buy": [], "executed_sell": [], "cost": 0.0,
    }
    return {
        "market": "csi1000",
        "paper_lineage": lineage,
        "execution_report": report,
        **({"pending_orders": {"execution_date": pending}} if pending else {}),
    }


def test_forward_series_starts_exactly_on_20261012():
    result = build_forward_performance([
        _artifact("2026-10-09", 100.0, 110.0),
        _artifact("2026-10-12", 200.0, 210.0),
        _artifact("2026-10-13", 211.0, 220.0),
    ])
    assert result["start_date"] == FORWARD_PERFORMANCE_START == "2026-10-12"
    assert result["inception_date"] == "2026-10-12"
    assert [p["date"] for p in result["points"]] == ["2026-10-12", "2026-10-13"]
    assert abs(result["points"][0]["cumulative_return"] - 0.05) < 1e-12
    assert abs(result["cumulative_return"] - 0.10) < 1e-12


def test_first_day_includes_open_to_close_return_and_cost_effects():
    result = build_forward_performance([_artifact("2026-10-12", 100.0, 98.0)])
    assert result["points"][0]["nav"] == 0.98
    assert abs(result["cumulative_return"] + 0.02) < 1e-12


def test_only_current_winner_lineage_is_public_performance():
    result = build_forward_performance([
        _artifact("2026-10-12", 100.0, 150.0, lineage="canonical"),
        _artifact("2026-10-12", 100.0, 101.0),
    ])
    assert len(result["points"]) == 1
    assert abs(result["cumulative_return"] - 0.01) < 1e-12


def test_no_report_waits_for_actual_settlement():
    result = build_forward_performance([{
        "market": "csi1000",
        "paper_lineage": "stage_b_winner_canonical",
        "pending_orders": {"execution_date": "2026-10-12"},
        "execution_report": None,
    }])
    assert result["status"] == "awaiting_first_valuation"
    assert result["points"] == []


def test_missing_or_unverified_close_fails_closed():
    broken = _artifact("2026-10-12", 100, 101)
    broken["execution_report"]["valuation_verified"] = False
    with pytest.raises(ValueError, match="unverified"):
        build_forward_performance([broken])


def test_changed_cash_and_nav_fail_independent_reconciliation():
    broken = _artifact("2026-10-12", 100, 101)
    broken["execution_report"]["cash"] = 100
    with pytest.raises(ValueError, match="cash not conserved"):
        build_forward_performance([broken])


def test_conflicting_same_date_reports_fail_closed():
    with pytest.raises(ValueError, match="conflicting performance reports"):
        build_forward_performance([
            _artifact("2026-10-12", 100.0, 101.0),
            _artifact("2026-10-12", 100.0, 102.0),
        ])


def test_scheduled_gap_must_not_be_silently_skipped():
    with pytest.raises(ValueError, match="missing scheduled execution"):
        build_forward_performance([
            _artifact("2026-10-12", 100, 101, pending="2026-10-13"),
            _artifact("2026-10-14", 100, 102),
        ])


def test_later_verified_first_valuation_preserves_real_inception():
    result = build_forward_performance([_artifact("2026-10-13", 100, 101)])
    assert result["start_date"] == FORWARD_PERFORMANCE_START
    assert result["inception_date"] == "2026-10-13"
