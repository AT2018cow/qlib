"""Independent hand-calculated and boundary cases for the November review."""

import copy
from decimal import Decimal, ROUND_HALF_UP

import numpy as np
import pandas as pd
import pytest

import artifact_io
from account_boundary import account_identity
from coordinate_contract import validate_coordinate_quotes
from execution_limits import compute_limit_masks
from order_quantity import round_quantity
from paper_accounting import verify_ledger
from paper_portfolio import PaperPortfolio
from portfolio_performance import portfolio_performance
from portfolio_overlays import apply_trend_gate, apply_drawdown_breaker, combine_sleeves
from st_history import st_flags
from stage0_p0_core import apply_cost_stress
from research_governance import trial_record, promotion_gate, digest


@pytest.mark.parametrize("field", ["return", "cost", "bench", "account"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_metrics_never_remove_a_bad_session(field, bad):
    r = pd.DataFrame(
        {"return": [0.0, -0.5, 1.0], "cost": 0.0, "bench": 0.0, "account": [100.0, 50.0, 100.0]},
        index=pd.date_range("2026-01-01", periods=3),
    )
    assert portfolio_performance(r, initial_cash=100)["strategy_max_drawdown"] == -0.5
    r.loc[r.index[1], field] = bad
    with pytest.raises(ValueError):
        portfolio_performance(r, initial_cash=100)


def _ledger():
    pp = PaperPortfolio(topk=1, nd=1, initial_cash=10000)
    pp.plan_signal("2026-11-02", "2026-11-03", [("SZ000001", 1.0)])
    return pp.execute_pending(
        "2026-11-03", {"SZ000001": 10}, factors={"SZ000001": 1}, close_prices={"SZ000001": 10}, strict_valuation=True
    )


@pytest.mark.parametrize(
    "field", ["cash", "opening_cash", "cost", "portfolio_value_pre_trade_open", "portfolio_value_close"]
)
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_ledger_rejects_nonfinite_summaries(field, bad):
    ledger = _ledger()
    ledger[field] = bad
    with pytest.raises(ValueError):
        verify_ledger(ledger)


def test_strict_json_rejects_read_and_write_nonfinite():
    with pytest.raises(ValueError):
        artifact_io.loads('{"cash":NaN}')
    with pytest.raises(ValueError):
        artifact_io.dumps({"cash": float("inf")})
    with pytest.raises(ValueError):
        artifact_io.loads('{"cash": 1e999}')


@pytest.mark.parametrize("prior", [float("nan"), float("inf"), 0.0, -1.0])
def test_unknown_reference_is_not_limit_exemption(prior):
    buy, sell = compute_limit_masks(["SZ000001"], ["2026-11-03"], [11], [prior], [False], factors=[1.0])
    assert buy[0] and sell[0]


def test_verified_ipo_exemption_can_have_no_previous_quote():
    buy, sell = compute_limit_masks(
        ["SH688001"],
        ["2026-11-03"],
        [11],
        [float("nan")],
        [False],
        listing_dates={"SH688001": "2026-11-03"},
        calendar=["2026-11-03"],
        factors=[1.0],
    )
    assert not buy[0] and not sell[0]


@pytest.mark.parametrize("factor", [1.0, 0.01, 0.137])
def test_tick_half_boundaries_against_independent_decimal(factor):
    # Include float32 provider encoding of the adjusted price/factor.
    for cents in range(105, 1000, 10):
        ref = Decimal(cents) / 100
        low = float((ref * Decimal(".9")).quantize(Decimal(".01"), rounding=ROUND_HALF_UP))
        high = float((ref * Decimal("1.1")).quantize(Decimal(".01"), rounding=ROUND_HALF_UP))
        for raw, expected_buy, expected_sell in ((low, False, True), (high, True, False)):
            buy, sell = compute_limit_masks(
                ["SZ000001"],
                ["2026-11-03"],
                [np.float32(raw * factor)],
                [np.float32(float(ref) * factor)],
                [False],
                factors=[np.float32(factor)],
            )
            assert bool(buy[0]) == expected_buy
            assert bool(sell[0]) == expected_sell


def test_star_quantity_and_factor_invariance():
    assert round_quantity(199, 1.0, "SH688001", "2026-11-03") == 0
    assert round_quantity(201.9, 1.0, "SH688001", "2026-11-03") == 201
    assert round_quantity(402, 0.5, "SH688001", "2026-11-03") == 402
    assert round_quantity(199, 1.0, "SZ000001", "2026-11-03") == 100
    assert round_quantity(101, 1.0, "BJ430047", "2026-11-03") == 101


def test_real_qlib_exchange_adapter_uses_star_quantity():
    import logging
    from board_execution import BoardAwareExchange
    from qlib.backtest.decision import Order

    exchange = object.__new__(BoardAwareExchange)
    exchange.open_cost, exchange.close_cost, exchange.min_cost, exchange.impact_cost = 0.0005, 0.0015, 5.0, 0.0
    exchange.logger = logging.getLogger("quantity-test")
    exchange.get_deal_price = lambda *a, **kw: 150.0
    exchange.get_volume = lambda *a, **kw: 1_000_000.0
    exchange.get_factor = lambda *a, **kw: 1.0
    exchange._clip_amount_by_volume = lambda *a, **kw: None
    order = Order(
        stock_id="SH688001",
        amount=201,
        start_time=pd.Timestamp("2026-11-03"),
        end_time=pd.Timestamp("2026-11-03"),
        direction=Order.BUY,
    )
    price, value, cost = exchange._calc_trade_info_by_order(order, None, {})
    assert order.deal_amount == 201 and value == 30150
    order.amount = 199
    exchange._calc_trade_info_by_order(order, None, {})
    assert order.deal_amount == 0
    order.amount, order.direction = 200000, Order.SELL
    exchange._calc_trade_info_by_order(order, None, {})
    assert order.deal_amount == 100000 and order.amount == 200000


def test_legacy_strategy_factor_rounding_keeps_instrument_identity(monkeypatch):
    from board_execution import BoardAwareExchange
    from qlib.backtest.exchange import Exchange

    monkeypatch.setattr(Exchange, "get_factor", lambda *args: 1.0)
    exchange = object.__new__(BoardAwareExchange)
    day = pd.Timestamp("2026-11-03")
    star = exchange.get_factor("SH688001", day, day)
    main = exchange.get_factor("SZ000001", day, day)
    # Equal numeric factors cannot be used to guess the board. Each factor
    # carries its own identity even when another factor has since been read.
    assert star == main == 1.0
    assert exchange.round_amount_by_trade_unit(201, star) == 201
    assert exchange.round_amount_by_trade_unit(201, main) == 200
    assert exchange.round_amount_by_trade_unit(201, copy.deepcopy(star)) == 201


def _report(n=100):
    return pd.DataFrame(
        {"return": 0.0, "cost": 0.0, "bench": 0.0, "turnover": 0.0}, index=pd.bdate_range("2026-01-01", periods=n)
    )


def test_trend_signal_does_not_depend_on_evaluation_start():
    full = _report(400)
    close = pd.Series(np.arange(500.0, 100.0, -1), index=full.index)
    _, all_weight = apply_trend_gate(full, close)
    _, tail = apply_trend_gate(full.iloc[300:], close)
    assert (tail == 0.3).all()
    pd.testing.assert_series_equal(tail, all_weight.iloc[300:])


def test_breaker_recovers_from_cash_without_future_information():
    r = _report(80)
    r["return"] = [-0.2] + [0.01] * 79
    _, weights = apply_drawdown_breaker(r)
    assert weights.iloc[0] == 1 and weights.iloc[1] == 0
    assert weights.iloc[-1] == 1
    _, prefix = apply_drawdown_breaker(r.iloc[:30])
    pd.testing.assert_series_equal(prefix, weights.iloc[:30])
    r["return"] = -0.02
    _, falling = apply_drawdown_breaker(r)
    assert falling.iloc[-1] == 0


def test_two_sleeves_reconcile_actual_net_asset_values():
    a, b = _report(2), _report(2)
    a["return"] = [0.0, 0.1]
    a["cost"] = [0.1, 0.0]
    out = combine_sleeves({"a": a, "b": b}, "equal", 20, 0)
    assert out["account"].iloc[-1] == pytest.approx(45_000_000 * 1.1 + 50_000_000)
    with pytest.raises(ValueError, match="align"):
        combine_sleeves({"a": a, "b": b.iloc[1:]}, "equal", 20, 0)


def test_stress_risk_statistics_reconcile_to_the_stressed_nav():
    r = _report(2)
    r["account"], r["cash"], r["total_cost"] = [100.0, 110.0], [100.0, 110.0], 0.0
    r["return"], r["turnover"] = [0.0, 0.1], [1.0, 0.0]
    stressed = apply_cost_stress(r, 100, initial_cash=100)
    assert stressed["account"].tolist() == [99.0, 109.0]
    assert (stressed["return"] - stressed["cost"]).iloc[1] == pytest.approx(109 / 99 - 1)
    assert portfolio_performance(stressed, initial_cash=100)["account_return_max_error"] == 0


def test_same_stock_st_entry_and_exit_are_dated():
    history = {
        "schema_version": 1,
        "source": "fixture",
        "dates": {"2025-01-02": [], "2025-01-03": ["SZ000001"], "2025-01-06": []},
    }
    flags = st_flags(history, ["SZ000001"] * 3, list(history["dates"]))
    buy, _ = compute_limit_masks(
        ["SZ000001"] * 3, list(history["dates"]), [10.5] * 3, [10] * 3, [False] * 3, st_flags=flags
    )
    assert buy.tolist() == [False, True, False]
    with pytest.raises(ValueError, match="missing"):
        st_flags(history, ["SZ000001"], ["2025-01-07"])


def test_coordinate_revision_never_silently_revalues_an_existing_position():
    positions = {"A": {"coordinate_anchor": {"date": "2026-11-02", "close": 10.0, "factor": 0.5}}}
    quotes = {"A": {"date": "2026-11-02", "close": 10.0, "factor": 0.5}}
    validate_coordinate_quotes(positions, quotes)
    quotes["A"]["close"] = 20.0
    with pytest.raises(ValueError, match="revision"):
        validate_coordinate_quotes(positions, quotes)


def test_liquidity_partial_fill_and_failure_are_cash_inventory_safe():
    pp = PaperPortfolio(topk=1, nd=1, initial_cash=10000)
    pp.plan_signal("2026-11-02", "2026-11-03", [("SZ000001", 1.0)])
    before = copy.deepcopy(pp.to_dict())
    with pytest.raises(ValueError, match="notional"):
        pp.execute_pending(
            "2026-11-03",
            {"SZ000001": 10},
            factors={"SZ000001": 1},
            close_prices={"SZ000001": 10},
            strict_valuation=True,
            max_participation=0.1,
            liquidity_evidence={"date": "2026-11-03", "source": "fixture", "open_auction_notional": {}},
        )
    assert pp.to_dict() == before
    report = pp.execute_pending(
        "2026-11-03",
        {"SZ000001": 10},
        factors={"SZ000001": 1},
        close_prices={"SZ000001": 10},
        strict_valuation=True,
        max_participation=0.1,
        liquidity_evidence={"date": "2026-11-03", "source": "fixture", "open_auction_notional": {"SZ000001": 20000}},
    )
    assert report["executed_buy"][0]["physical_shares"] == 200
    assert verify_ledger(report)
    pp.plan_signal("2026-11-03", "2026-11-04", [("SZ000002", 2.0), ("SZ000001", 0.0)])
    second = pp.execute_pending(
        "2026-11-04",
        {"SZ000001": 10.0, "SZ000002": 10.0},
        factors={"SZ000001": 1.0, "SZ000002": 1.0},
        close_prices={"SZ000001": 10.0, "SZ000002": 10.0},
        strict_valuation=True,
        max_participation=0.1,
        liquidity_evidence={
            "date": "2026-11-04",
            "source": "fixture",
            "open_auction_notional": {"SZ000001": 10000.0, "SZ000002": 10000.0},
        },
    )
    assert pp.positions["SZ000001"]["shares"] == 100
    assert verify_ledger(second)


def test_trial_registry_keeps_failed_attempts_and_prevents_rewrite(tmp_path):
    identity = {"candidate": "fixture", "signal_sha256": "a" * 64}
    with pytest.raises(RuntimeError):
        with trial_record(tmp_path, identity, attempt_id="fixture"):
            raise RuntimeError("failure fixture")
    outcome = artifact_io.loads((tmp_path / digest(identity) / "fixture" / "outcome.json").read_text())
    assert outcome["status"] == "FAILED"
    with pytest.raises(FileExistsError):
        with trial_record(tmp_path, identity, attempt_id="fixture"):
            pass


def test_promotion_cannot_use_consumed_dates_or_omit_execution_evidence():
    from datetime import date

    contract = {
        "schema_version": 1,
        "registered_at": "2026-11-06T00:00:00+00:00",
        "forward_start": "2026-11-09",
        "registration_receipt_sha256": "a" * 64,
        "label_horizon": 20,
        "purge_sessions": 20,
        "embargo_sessions": 20,
        "min_forward_sessions": 120,
        "candidate_ids": ["fixture"],
    }
    dates = [str(d.date()) for d in pd.bdate_range("2026-11-09", periods=120)]
    evidence = {"candidate_id": "fixture", "contract_sha256": digest(contract), "forward_dates": dates}
    assert not promotion_gate(contract, evidence)["promotion_eligible"]
    for kind in ("frozen_predictions", "order_replay", "dated_st", "liquidity", "cost_stress", "multiple_testing"):
        evidence[kind] = {"status": "PASS", "sha256": "b" * 64}
    assert promotion_gate(contract, evidence, asof=date(2027, 12, 31))["promotion_eligible"]
    assert not promotion_gate(contract, evidence, asof=date(2026, 11, 6))["promotion_eligible"]
    evidence["forward_dates"][0] = "2026-01-01"
    assert not promotion_gate(contract, evidence)["promotion_eligible"]


def test_production_context_checks_old_coordinate_before_settling(tmp_path, monkeypatch):
    from paper_execution_context import build_paper_execution_context
    import qlib.data as qdata
    from types import SimpleNamespace

    state = {
        "positions": {"SZ000001": {"shares": 100.0, "last_price": 10.0}},
        "last_execution_date": "2026-11-02",
        "pending_signal": {"execution_date": "2026-11-03"},
    }
    path = tmp_path / "state.json"
    path.write_text(artifact_io.dumps(state))

    def revised_quote(*args, **kwargs):
        return pd.DataFrame(
            {"$close": [20.0], "$factor": [1.0]},
            index=pd.MultiIndex.from_tuples([("SZ000001", pd.Timestamp("2026-11-02"))]),
        )

    monkeypatch.setattr(qdata, "D", SimpleNamespace(features=revised_quote))
    with pytest.raises(ValueError, match="coordinate revision"):
        build_paper_execution_context(
            tmp_path,
            "csi1000",
            ["2026-11-02", "2026-11-03"],
            "2026-11-03",
            state_path=path,
            listing_dates_fn=lambda *a: {},
        )


def test_physical_portfolio_is_invariant_to_provider_normalization():
    rng = np.random.default_rng(20261106)
    for _ in range(25):
        cash, raw_price = float(rng.integers(20000, 1000000)), float(rng.integers(1, 500))
        accounts = []
        for factor in (1.0, 0.01, 0.37):
            pp = PaperPortfolio(topk=1, nd=1, initial_cash=cash)
            pp.plan_signal("2026-11-02", "2026-11-03", [("SZ000001", 1.0)])
            report = pp.execute_pending(
                "2026-11-03",
                {"SZ000001": raw_price * factor},
                factors={"SZ000001": factor},
                close_prices={"SZ000001": raw_price * factor},
                strict_valuation=True,
            )
            assert verify_ledger(report)
            accounts.append(report["portfolio_value_close"])
        assert max(accounts) - min(accounts) < 0.011


def test_inverse_vol_fallback_preserves_total_budget():
    from portfolio_overlays import _target_weights

    net = pd.DataFrame({"flat": [0.0] * 100, "variable": [0.01, -0.01] * 50})
    weights = _target_weights(net, "riskparity")
    assert np.allclose(weights.sum(axis=1), 1.0)


def test_portfolio_diagnostics_report_exposure_and_missing_classification():
    from portfolio_diagnostics import portfolio_diagnostics

    result = portfolio_diagnostics(100, {"A": {"shares": 20, "last_price": 20}})
    assert result["cash_ratio"] == 0.2
    assert result["largest_position_weight"] == 0.8
    assert result["effective_names"] == 1
    assert result["industry_exposure_status"] == "REQUIRES_DATED_CLASSIFICATION"


def test_capacity_grid_requires_real_evidence_and_runs_multiple_account_sizes():
    from paper_replay import replay_capacity_scenarios

    row = {
        "signal_date": "2026-11-02",
        "execution_date": "2026-11-03",
        "ranking": [["SZ000001", 1.0]],
        "open_prices": {"SZ000001": 10.0},
        "close_prices": {"SZ000001": 10.0},
        "previous_close": {"SZ000001": 10.0},
        "factors": {"SZ000001": 1.0},
        "execution_st_snapshot": {"date": "2026-11-03", "source": "fixture", "st_symbols": []},
    }
    with pytest.raises(ValueError, match="evidence"):
        replay_capacity_scenarios([row], account_sizes=[10000], participation_rates=[0.1], slippage_grid=[0.0])
    row["liquidity_evidence"] = {
        "date": "2026-11-03",
        "source": "fixture",
        "open_auction_notional": {"SZ000001": 20000},
    }
    results = replay_capacity_scenarios(
        [row],
        account_sizes=[10000, 100000],
        participation_rates=[0.1],
        slippage_grid=[0.0, 10.0],
        config={"topk": 1, "nd": 1},
    )
    assert len(results) == 4
    assert all(r["execution_mode"] == "OPEN_AUCTION_CONSTRAINED" for r in results)
    assert results[1]["cost"] > results[0]["cost"]
