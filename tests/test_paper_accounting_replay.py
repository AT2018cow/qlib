import pytest

from paper_accounting import close_valuation, verify_ledger
from paper_portfolio import PaperPortfolio
from paper_replay import replay_paper_sessions

A = "SZ000001"
B = "SZ000002"


def _row(day, signal, open_price, prior_price, close_price, *, st=()):
    return {
        "signal_date": signal,
        "execution_date": day,
        "ranking": [[A, 1.0], [B, 0.0]],
        "open_prices": {A: open_price},
        "close_prices": {A: close_price},
        "previous_close": {A: prior_price},
        "factors": {A: 1.0},
        "execution_st_snapshot": {
            "date": day, "source": "dated test fixture", "st_symbols": list(st),
        },
    }


def test_historical_replay_uses_forward_account_and_reconciles_two_days():
    days = [
        _row("2025-02-20", "2025-02-19", 10, 10, 11),
        _row("2025-02-21", "2025-02-20", 11, 11, 12),
    ]
    reports = replay_paper_sessions(days, config={"topk": 1, "nd": 1, "initial_cash": 10000})
    assert len(reports) == 2
    assert reports[0]["executed_buy"][0]["instrument"] == A
    assert reports[1]["position_marks"][A]["price"] == 12
    assert all(r["valuation_verified"] and verify_ledger(r) for r in reports)
    assert reports[1]["portfolio_value_close"] > reports[0]["portfolio_value_close"]


def test_pre202607_main_board_st_limit_blocks_5pct_open():
    row = _row("2025-02-21", "2025-02-20", 10.5, 10, 10.2, st=(A,))
    report = replay_paper_sessions([row], config={"topk": 1, "nd": 1, "initial_cash": 10000})[0]
    assert report["executed_buy"] == []
    assert report["blocked_buy"] == [{"instrument": A, "reason": "not_tradable"}]
    assert report["portfolio_value_close"] == 10000


def test_replay_requires_dated_preopen_st_evidence():
    row = _row("2025-02-21", "2025-02-20", 10, 10, 11)
    row.pop("execution_st_snapshot")
    with pytest.raises(ValueError, match="ST evidence"):
        replay_paper_sessions([row], config={"topk": 1, "nd": 1})


def test_missing_close_fails_before_any_paper_state_changes():
    pp = PaperPortfolio(topk=1, nd=1, initial_cash=10000)
    pp.plan_signal("2025-02-19", "2025-02-20", [(A, 1.0)])
    before = pp.to_dict()
    with pytest.raises(ValueError, match="unverified missing close"):
        pp.execute_pending("2025-02-20", {A: 10}, factors={A: 1},
                           close_prices={}, strict_valuation=True)
    assert pp.to_dict() == before


def test_only_certified_halt_may_carry_old_valid_price():
    value, marks = close_valuation(
        100, {A: {"shares": 10, "last_price": 11}},
        {}, confirmed_halts={A},
    )
    assert value == 210
    assert marks[A]["reason"] == "CERTIFIED_HALT_CARRY"
    with pytest.raises(ValueError, match="unverified missing close"):
        close_valuation(100, {A: {"shares": 10, "last_price": 11}}, {})


def test_independent_reconciliation_detects_forged_fill_or_cash():
    report = replay_paper_sessions(
        [_row("2025-02-20", "2025-02-19", 10, 10, 11)],
        config={"topk": 1, "nd": 1, "initial_cash": 10000},
    )[0]
    assert verify_ledger(report)
    report["cash"] += 100
    with pytest.raises(ValueError, match="cash not conserved"):
        verify_ledger(report)
