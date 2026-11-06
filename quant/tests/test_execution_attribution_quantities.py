"""A-to-B timing attribution must preserve the same legacy quantity policy."""

import logging

import pandas as pd
import pytest

from board_execution import BoardAwareExchange, research_exchange, uniform_open_exchange
from qlib.backtest.decision import Order
from qlib.backtest.exchange import Exchange


def exchange(cls, *, board_quantities=False):
    instance = object.__new__(cls)
    instance.trade_unit = 100
    instance.trade_w_adj_price = False
    instance.open_cost, instance.close_cost, instance.min_cost, instance.impact_cost = 0.0005, 0.0015, 5.0, 0.0
    instance.logger = logging.getLogger("attribution-quantity-test")
    instance.get_deal_price = lambda *a, **kw: 150.0
    instance.get_volume = lambda *a, **kw: 1_000_000.0
    instance.get_factor = lambda *a, **kw: 1.0
    instance._clip_amount_by_volume = lambda *a, **kw: None
    if cls is BoardAwareExchange:
        instance._enforce_board_quantities = board_quantities
    return instance


@pytest.mark.parametrize("symbol", ["SH600000", "SH688001", "BJ430047"])
@pytest.mark.parametrize("target", [199, 201, 299])
def test_flat_price_control_has_identical_fills_and_costs(symbol, target):
    day = pd.Timestamp("2026-10-08")
    fills = []
    for instance in (exchange(Exchange), exchange(BoardAwareExchange)):
        order = Order(stock_id=symbol, amount=target, start_time=day, end_time=day, direction=Order.BUY)
        price, value, cost = instance._calc_trade_info_by_order(order, None, {})
        fills.append((order.deal_amount, price, value, cost))
    assert fills[0] == fills[1]


def test_quantity_switch_is_explicit_and_production_stays_board_aware():
    uniform = uniform_open_exchange("2026-10-08", "2026-10-09")["exchange"]["kwargs"]
    research = research_exchange("2026-10-08", "2026-10-09")["exchange"]["kwargs"]
    assert uniform["enforce_board_quantities"] is False
    assert research["enforce_board_quantities"] is True
    modern = exchange(BoardAwareExchange, board_quantities=True)
    legacy = exchange(BoardAwareExchange)
    assert legacy.round_amount_by_trade_unit(199, 1.0) == 100
    assert modern.round_amount_by_trade_unit(199, 1.0, "SH688001", "2026-10-08") == 0
    assert modern.round_amount_by_trade_unit(201, 1.0, "SH688001", "2026-10-08") == 201


def test_uniform_control_preserves_native_large_sell_quantities():
    day = pd.Timestamp("2026-10-08")
    results = []
    for instance in (exchange(Exchange), exchange(BoardAwareExchange)):
        order = Order(stock_id="SH688001", amount=200_001, start_time=day, end_time=day, direction=Order.SELL)
        instance._calc_trade_info_by_order(order, None, {})
        results.append((order.amount, order.deal_amount))
    assert results[0] == results[1] == (200_001, 200_001)
