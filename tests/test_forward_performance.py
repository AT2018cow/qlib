from forward_performance import FORWARD_PERFORMANCE_START, build_forward_performance


def _artifact(date, open_value, close_value, lineage="stage_b_winner_canonical"):
    return {
        "market": "csi1000",
        "paper_lineage": lineage,
        "execution_report": {
            "date": date,
            "portfolio_value_pre_trade_open": open_value,
            "portfolio_value_close": close_value,
        },
    }


def test_forward_series_starts_exactly_on_20261012():
    result = build_forward_performance(
        [
            _artifact("2026-10-09", 100.0, 110.0),
            _artifact("2026-10-12", 200.0, 210.0),
            _artifact("2026-10-13", 211.0, 220.0),
        ]
    )
    assert result["start_date"] == FORWARD_PERFORMANCE_START == "2026-10-12"
    assert [p["date"] for p in result["points"]] == ["2026-10-12", "2026-10-13"]
    assert abs(result["points"][0]["cumulative_return"] - 0.05) < 1e-12
    assert abs(result["points"][-1]["cumulative_return"] - 0.10) < 1e-12


def test_first_day_includes_open_to_close_return_and_cost_effects():
    result = build_forward_performance([_artifact("2026-10-12", 100.0, 98.0)])
    assert result["points"][0]["nav"] == 0.98
    assert abs(result["points"][0]["daily_return"] + 0.02) < 1e-12
    assert abs(result["cumulative_return"] + 0.02) < 1e-12


def test_only_current_winner_lineage_is_public_performance():
    result = build_forward_performance(
        [
            _artifact("2026-10-12", 100.0, 150.0, lineage="canonical"),
            _artifact("2026-10-12", 100.0, 101.0),
        ]
    )
    assert len(result["points"]) == 1
    assert abs(result["cumulative_return"] - 0.01) < 1e-12


def test_missing_close_valuation_waits_for_valid_execution_report():
    result = build_forward_performance(
        [
            {
                "market": "csi1000",
                "paper_lineage": "stage_b_winner_canonical",
                "execution_report": {
                    "date": "2026-10-12",
                    "portfolio_value": 100.0,
                },
            }
        ]
    )
    assert result["status"] == "awaiting_first_valuation"
    assert result["points"] == []


def test_conflicting_same_date_reports_fail_closed():
    artifacts = [
        _artifact("2026-10-12", 100.0, 101.0),
        _artifact("2026-10-12", 100.0, 102.0),
    ]
    try:
        build_forward_performance(artifacts)
    except ValueError as exc:
        assert "conflicting performance reports" in str(exc)
    else:
        raise AssertionError("conflicting same-date performance must fail")
