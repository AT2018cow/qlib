"""Independent forensic paper NAV regression fixtures; never modify production state."""
from copy import deepcopy

from scripts.audit_paper_nav import reconcile


def fixture():
    initial = {
        "market": "csi1000",
        "paper_lineage": "stage_b_winner_canonical",
        "production_lineage": {"profile": "stage_b_winner"},
        "usage_date": "2026-10-09",
        "cash": 2000.0,
        "positions": {},
        "execution_report": None,
    }
    first = deepcopy(initial)
    first.update({
        "usage_date": "2026-10-13",
        "cash": 995.0,
        "positions": {"A": {"shares": 100, "last_price": 11}},
        "execution_report": {
            "date": "2026-10-12",
            "signal_date": "2026-10-09",
            "cash": 995,
            "n_positions": 1,
            "positions": ["A"],
            "planned_buy": ["A"],
            "executed_buy": [{"instrument": "A", "shares": 100, "price": 10, "cost": 5}],
            "executed_sell": [],
            "cost": 5,
            "portfolio_value_pre_trade_open": 2000,
            "portfolio_value_close": 2095,
        },
    })
    second = deepcopy(initial)
    second.update({
        "usage_date": "2026-10-14",
        "cash": 2190,
        "positions": {},
        "execution_report": {
            "date": "2026-10-13",
            "signal_date": "2026-10-12",
            "cash": 2190,
            "n_positions": 0,
            "positions": [],
            "planned_buy": [],
            "executed_buy": [],
            "executed_sell": [{"instrument": "A", "shares": 100, "price": 12, "cost": 5}],
            "cost": 5,
            "portfolio_value_pre_trade_open": 2195,
            "portfolio_value_close": 2190,
        },
    })
    quotes = {
        ("2026-10-12", "A"): {"open": "10", "close": "11", "factor": "1"},
        ("2026-10-13", "A"): {"open": "12", "close": "12", "factor": "1"},
    }
    forward = {
        "start_date": "2026-10-12",
        "status": "active",
        "latest_date": "2026-10-13",
        "cumulative_return": 2190 / 2000 - 1,
        "points": [
            {"date": "2026-10-12", "nav": 2095 / 2000, "daily_return": 2095 / 2000 - 1,
             "cumulative_return": 2095 / 2000 - 1},
            {"date": "2026-10-13", "nav": 2190 / 2000, "daily_return": 2190 / 2095 - 1,
             "cumulative_return": 2190 / 2000 - 1},
        ],
    }
    return [initial, first, second], quotes, ["2026-10-12", "2026-10-13"], forward


def codes(result):
    return {row["code"] for row in result["issues"]}


def test_double_entry_cash_shares_fees_quotes_and_geometric_returns_pass():
    artifacts, quotes, calendar, forward = fixture()
    result = reconcile(artifacts, forward=forward, calendar=calendar, quotes=quotes)
    assert result["status"] == "PASS", result
    assert result["audited_reports"] == 2
    assert result["quoted_closes"] == 1
    assert result["rows"][0]["ledger_close"] == "2095.0"


def test_current_public_pre_inception_is_not_called_pass():
    old = {"market": "csi1000", "paper_lineage": "canonical", "usage_date": "2026-10-08"}
    result = reconcile([old], forward={"start_date": "2026-10-12",
        "status": "awaiting_first_valuation", "latest_date": None,
        "cumulative_return": None, "points": []})
    assert result["status"] == "NOT_STARTED"
    assert not result["issues"]
    assert result["audited_reports"] == 0


def test_no_external_quotes_cannot_be_certified():
    artifacts, _, calendar, forward = fixture()
    result = reconcile(artifacts, calendar=calendar, forward=forward)
    assert result["status"] == "PARTIAL_NOT_CERTIFIED"


def test_tampered_cash_is_caught():
    artifacts, quotes, calendar, forward = fixture()
    artifacts[1]["cash"] -= 100
    result = reconcile(artifacts, quotes=quotes, calendar=calendar, forward=forward)
    assert "CASH_SNAPSHOT_DIFF" in codes(result)
    assert "CASH_LEDGER_DIFF" in codes(result)
    assert result["status"] == "FAIL"


def test_tampered_position_price_and_snapshot_nav_are_caught():
    artifacts, quotes, calendar, forward = fixture()
    artifacts[1]["positions"]["A"]["last_price"] = 15
    result = reconcile(artifacts, quotes=quotes, calendar=calendar, forward=forward)
    assert "CLOSE_NAV_DIFF" in codes(result)
    assert "STALE_OR_DIFFERENT_CLOSE" in codes(result)
    assert result["status"] == "FAIL"


def test_wrong_fee_and_cash_effect_are_caught():
    artifacts, quotes, calendar, forward = fixture()
    artifacts[1]["execution_report"]["executed_buy"][0]["cost"] = 8
    result = reconcile(artifacts, quotes=quotes, calendar=calendar, forward=forward)
    assert "FEE_DIFF" in codes(result)
    assert "TOTAL_FEE_DIFF" in codes(result)


def test_production_adjusted_share_lot_parity_flag_is_exposed():
    artifacts, quotes, calendar, forward = fixture()
    quotes[("2026-10-12", "A")]["factor"] = "0.5"
    result = reconcile(artifacts, quotes=quotes, calendar=calendar, forward=forward)
    assert "FACTOR_LOT_DIVERGENCE" in codes(result)
    assert result["status"] == "FAIL"


def test_missing_authoritative_trading_session_is_caught():
    artifacts, quotes, _, _ = fixture()
    artifacts[2]["execution_report"]["date"] = "2026-10-14"
    artifacts[2]["usage_date"] = "2026-10-15"
    result = reconcile(artifacts, quotes=quotes,
                       calendar=["2026-10-12", "2026-10-13", "2026-10-14"])
    assert "MISSING_TRADING_DAY" in codes(result)


def test_first_observation_cannot_move_inception_baseline():
    artifacts, quotes, calendar, _ = fixture()
    artifacts = [artifacts[0], artifacts[2]]
    result = reconcile(artifacts, quotes=quotes, calendar=calendar)
    assert "MISSING_INCEPTION_REPORT" in codes(result)


def test_false_forward_percentage_and_date_are_caught():
    artifacts, quotes, calendar, forward = fixture()
    forward["points"][1]["daily_return"] = 0.20
    forward["points"][1]["date"] = "2026-10-14"
    result = reconcile(artifacts, quotes=quotes, calendar=calendar, forward=forward)
    assert "FORWARD_VALUE_DIFF" in codes(result)
    assert "FORWARD_DATE_DIFF" in codes(result)


def test_nonfinite_inputs_fail_without_being_counted_as_valid_nav():
    artifacts, quotes, calendar, forward = fixture()
    artifacts[1]["execution_report"]["portfolio_value_close"] = float("nan")
    result = reconcile(artifacts, quotes=quotes, calendar=calendar, forward=forward)
    assert "BAD_ACCOUNTING_FIELDS" in codes(result)


def test_missing_quote_is_not_mistaken_for_real_zero_return():
    artifacts, quotes, calendar, forward = fixture()
    quotes[("2026-10-12", "A")]["close"] = ""
    result = reconcile(artifacts, quotes=quotes, calendar=calendar, forward=forward)
    assert "MISSING_INDEPENDENT_CLOSE" in codes(result)


def test_without_opening_state_trade_replay_is_not_certified():
    artifacts, quotes, calendar, forward = fixture()
    result = reconcile(artifacts[1:], quotes=quotes, calendar=calendar, forward=forward)
    assert result["status"] == "PARTIAL_NOT_CERTIFIED"
    assert any(w["code"] == "MISSING_OPENING_SNAPSHOT" for w in result["warnings"])


def test_external_quote_factor_is_required_for_certification():
    artifacts, quotes, calendar, forward = fixture()
    del quotes[("2026-10-12", "A")]["factor"]
    result = reconcile(artifacts, quotes=quotes, calendar=calendar, forward=forward)
    assert "MISSING_INDEPENDENT_FACTOR" in codes(result)
    assert result["status"] == "FAIL"


def test_malformed_winner_profile_is_a_reported_failure():
    artifacts, quotes, calendar, forward = fixture()
    artifacts[0]["production_lineage"] = "not-an-object"
    result = reconcile(artifacts, quotes=quotes, calendar=calendar, forward=forward)
    assert "LINEAGE_MISMATCH" in codes(result)


def test_invalid_signal_day_cannot_certify_execution():
    artifacts, quotes, calendar, forward = fixture()
    artifacts[1]["execution_report"]["signal_date"] = "2026-10-13"
    result = reconcile(artifacts, quotes=quotes, calendar=calendar, forward=forward)
    assert "BAD_SIGNAL_DATE" in codes(result)


def test_missing_public_forward_json_does_not_allow_full_certification():
    artifacts, quotes, calendar, _ = fixture()
    result = reconcile(artifacts, quotes=quotes, calendar=calendar)
    assert result["status"] == "PARTIAL_NOT_CERTIFIED"
    assert any(w["code"] == "PUBLIC_FORWARD_NOT_PROVIDED" for w in result["warnings"])


def test_legal_physical_lot_still_detects_wrong_buy_budget_at_factor_two():
    """Old Paper 100-adjusted-share fill can form a legal physical lot, yet be undersized."""
    artifacts, quotes, calendar, forward = fixture()
    quotes[("2026-10-12", "A")]["factor"] = "2"
    result = reconcile(artifacts, quotes=quotes, calendar=calendar, forward=forward)
    assert "FACTOR_LOT_DIVERGENCE" not in codes(result)
    assert "BUY_SIZE_DIFF" in codes(result)


def test_factor_aware_fractional_share_ledger_reconciles():
    """A correct Qlib-adjusted buy at factor 2 should pass the full ledger audit."""
    artifacts, quotes, calendar, _ = fixture()
    original, first, second = artifacts
    quotes[("2026-10-12", "A")]["factor"] = "2"
    quotes[("2026-10-13", "A")]["factor"] = "2"
    first["positions"]["A"]["shares"] = 150
    first["cash"] = 495
    fill = first["execution_report"]["executed_buy"][0]
    fill.update({"shares": 150, "factor": 2, "physical_shares": 300})
    first["execution_report"].update({"cash": 495, "portfolio_value_close": 2145})
    sold = second["execution_report"]["executed_sell"][0]
    sold["shares"] = 150
    sold["cost"] = 5
    second["cash"] = 2290
    second["execution_report"].update({
        "cash": 2290, "portfolio_value_pre_trade_open": 2295,
        "portfolio_value_close": 2290,
    })
    result = reconcile(artifacts, quotes=quotes, calendar=calendar)
    assert result["status"] == "PARTIAL_NOT_CERTIFIED"
    assert not result["issues"], result


def test_mismatched_reported_factor_and_physical_share_count_are_detected():
    artifacts, quotes, calendar, forward = fixture()
    fill = artifacts[1]["execution_report"]["executed_buy"][0]
    fill["factor"] = 2
    fill["physical_shares"] = 300
    result = reconcile(artifacts, quotes=quotes, calendar=calendar, forward=forward)
    assert "REPORTED_FACTOR_DIFF" in codes(result)
    assert "PHYSICAL_SHARES_DIFF" in codes(result)


def test_missing_buy_plan_is_not_independently_certified():
    artifacts, quotes, calendar, forward = fixture()
    del artifacts[1]["execution_report"]["planned_buy"]
    result = reconcile(artifacts, quotes=quotes, calendar=calendar, forward=forward)
    assert "MISSING_BUY_PLAN" in codes(result)
