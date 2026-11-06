import pytest

from merge_forward_performance import merge_forward_performance
from account_boundary import account_identity


def _state():
    return {"cash": 0.0, "shares": {"A": 80000.0}}


def _prefix():
    return {
        "start": "2026-09-30",
        "initial_cash": 1_000_000.0,
        "account_identity": account_identity("csi1000"),
        "closing_state": _state(),
        "points": [
            {"date": "2026-09-30", "nav": 1.0, "daily_return": 0.0, "cumulative_return": 0.0},
            {"date": "2026-10-30", "nav": 0.8, "daily_return": -0.2, "cumulative_return": -0.2},
        ],
    }


def _live(close=840_000.0, day="2026-11-03"):
    return {
        "start_date": "2026-10-12",
        "status": "active",
        "account_identity": account_identity("csi1000"),
        "trading_dates": ["2026-10-30", "2026-11-02", "2026-11-03"],
        "points": [
            {
                "date": "2026-11-02",
                "portfolio_value_close": 820000.0,
                "opening_state": _state(),
                "closing_state": _state(),
                "execution_st_policy": "ST_VERIFIED_PREOPEN",
            },
            {
                "date": day,
                "portfolio_value_close": close,
                "opening_state": _state(),
                "closing_state": _state(),
                "execution_st_policy": "ST_VERIFIED_PREOPEN",
            },
        ],
    }


def test_live_marks_splice_onto_the_same_one_million_account():
    merged = merge_forward_performance(_prefix(), _live())
    assert merged["start_date"] == "2026-09-30"
    assert merged["inception_date"] == "2026-09-30"
    assert merged["data_mode"] == "BACKFILL_PLUS_LIVE"
    assert merged["backfill_end_date"] == "2026-10-30"
    assert merged["live_start_date"] == "2026-11-02"
    assert [p["date"] for p in merged["points"]] == ["2026-09-30", "2026-10-30", "2026-11-02", "2026-11-03"]
    last = merged["points"][-1]
    # NAV stays relative to the original 1M account, not to the splice point.
    # Rebasing here would invent a ~31% one-day return.
    assert abs(last["nav"] - 0.84) < 1e-12
    assert abs(last["cumulative_return"] + 0.16) < 1e-12
    assert abs(last["daily_return"] - (0.84 / 0.82 - 1)) < 1e-12
    assert merged["latest_date"] == "2026-11-03"


def test_no_live_marks_keeps_the_backfill_curve_active():
    merged = merge_forward_performance(_prefix(), {"status": "awaiting_first_valuation", "points": []})
    assert merged["status"] == "active"
    assert merged["data_mode"] == "BACKFILL_RESEARCH"
    assert merged["latest_date"] == "2026-10-30"
    assert merged["cumulative_return"] == -0.2


def test_live_point_without_absolute_close_fails_closed():
    with pytest.raises(ValueError, match="close value"):
        merge_forward_performance(_prefix(), _live(close=None))


def test_live_date_must_follow_the_backfill():
    with pytest.raises(ValueError, match="session"):
        merge_forward_performance(_prefix(), _live(day="2026-10-30"))


def test_account_value_discontinuity_fails_closed():
    with pytest.raises(ValueError, match="discontinuity"):
        merge_forward_performance(_prefix(), _live(close=84_000_000.0))


def test_reset_to_initial_cash_is_rejected_not_shown_as_a_gain():
    live = _live(close=1_000_000.0)
    live["points"][0]["opening_state"] = {"cash": 1_000_000.0, "shares": {}}
    with pytest.raises(ValueError, match="discontinuity"):
        merge_forward_performance(_prefix(), live)


def test_cross_market_identity_and_missing_boundary_proof_fail_closed():
    live = _live()
    live["account_identity"] = account_identity("csi300")
    with pytest.raises(ValueError, match="identity"):
        merge_forward_performance(_prefix(), live)
    live = _live()
    del live["points"][0]["opening_state"]
    with pytest.raises(ValueError, match="boundary"):
        merge_forward_performance(_prefix(), live)


def test_a_missing_boundary_session_is_not_a_daily_return():
    live = _live()
    live["points"].pop(0)
    with pytest.raises(ValueError, match="session"):
        merge_forward_performance(_prefix(), live)


def test_mixed_st_execution_policy_survives_splicing():
    live = _live()
    live["points"][-1]["execution_st_policy"] = "ST_IGNORED_REFERENCE_ONLY"
    merged = merge_forward_performance(_prefix(), live)
    assert merged["points"][-1]["execution_st_policy"] == "ST_IGNORED_REFERENCE_ONLY"
    assert merged["st_fallback_dates"] == ["2026-11-03"]
    assert merged["points"][0]["data_mode"] == "BACKFILL_RESEARCH"
