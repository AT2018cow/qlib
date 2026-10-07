"""Deterministic TopK-dropout strategy for audited CSI1000 research.

Qlib's stock Position exposes holdings through a set-derived list and the
upstream TopkDropoutStrategy does not specify a secondary key for equal scores.
Across Python processes this can make exact-tie boundaries choose different
stocks even when the prediction vector is identical.

This strategy keeps the standard top/bottom TopkDropout semantics but freezes:
- current holdings: instrument ascending;
- score ranking: score descending, instrument ascending for exact ties;
- sell iteration: instrument ascending;
- buy iteration: deterministic score rank.

Random buy/sell modes are intentionally unsupported by this audited strategy.
"""
from __future__ import annotations

import copy

import pandas as pd

from csi1000_tuner_core import deterministic_score_order
from qlib.backtest.decision import Order, OrderDir, TradeDecisionWO
from qlib.backtest.position import Position
from qlib.contrib.strategy.signal_strategy import TopkDropoutStrategy


class DeterministicTopkDropoutStrategy(TopkDropoutStrategy):
    """TopkDropoutStrategy with explicit deterministic ordering semantics."""

    def __init__(self, *args, method_sell="bottom", method_buy="top", **kwargs):
        if method_sell != "bottom" or method_buy != "top":
            raise ValueError(
                "DeterministicTopkDropoutStrategy supports only "
                "method_sell='bottom', method_buy='top'"
            )
        super().__init__(
            *args,
            method_sell=method_sell,
            method_buy=method_buy,
            **kwargs,
        )

    @staticmethod
    def _rank(score: pd.Series) -> pd.Index:
        ordered = deterministic_score_order(score.items())
        return pd.Index(ordered, name=score.index.name)

    def generate_trade_decision(self, execute_result=None):
        trade_step = self.trade_calendar.get_trade_step()
        trade_start_time, trade_end_time = self.trade_calendar.get_step_time(trade_step)
        pred_start_time, pred_end_time = self.trade_calendar.get_step_time(trade_step, shift=1)
        pred_score = self.signal.get_signal(start_time=pred_start_time, end_time=pred_end_time)

        if isinstance(pred_score, pd.DataFrame):
            pred_score = pred_score.iloc[:, 0]
        if pred_score is None:
            return TradeDecisionWO([], self)
        if pred_score.index.has_duplicates:
            raise ValueError("prediction signal contains duplicate instruments")

        if self.only_tradable:
            def get_first_n(items, n, reverse=False):
                result = []
                iterator = reversed(items) if reverse else items
                for stock_id in iterator:
                    if self.trade_exchange.is_stock_tradable(
                        stock_id=stock_id,
                        start_time=trade_start_time,
                        end_time=trade_end_time,
                    ):
                        result.append(stock_id)
                        if len(result) >= n:
                            break
                return result[::-1] if reverse else result
        else:
            def get_first_n(items, n, reverse=False):
                values = list(items)
                if reverse:
                    return values[-n:] if n > 0 else []
                return values[:n]

        def get_last_n(items, n):
            return get_first_n(items, n, reverse=True)

        current_temp: Position = copy.deepcopy(self.trade_position)
        cash = current_temp.get_cash()

        # Qlib Position.get_stock_list() is set-derived; freeze it explicitly.
        current_stock_list = sorted(current_temp.get_stock_list(), key=str)

        last = self._rank(pred_score.reindex(current_stock_list))
        nonheld = pred_score[~pred_score.index.isin(last)]
        today = get_first_n(
            self._rank(nonheld),
            self.n_drop + self.topk - len(last),
        )

        # Avoid Index.union ordering semantics by defining the union order first.
        comb_codes = sorted(
            set(last.tolist()).union(today),
            key=str,
        )
        comb = self._rank(pred_score.reindex(comb_codes))
        sell = last[last.isin(get_last_n(comb, self.n_drop))]
        buy = list(today[: len(sell) + self.topk - len(last)])

        sell_order_list = []
        buy_order_list = []

        for code in current_stock_list:
            if not self.trade_exchange.is_stock_tradable(
                stock_id=code,
                start_time=trade_start_time,
                end_time=trade_end_time,
                direction=None if self.forbid_all_trade_at_limit else OrderDir.SELL,
            ):
                continue
            if code not in sell:
                continue
            time_per_step = self.trade_calendar.get_freq()
            if current_temp.get_stock_count(code, bar=time_per_step) < self.hold_thresh:
                continue

            sell_amount = current_temp.get_stock_amount(code=code)
            sell_order = Order(
                stock_id=code,
                amount=sell_amount,
                start_time=trade_start_time,
                end_time=trade_end_time,
                direction=Order.SELL,
            )
            if self.trade_exchange.check_order(sell_order):
                sell_order_list.append(sell_order)
                trade_val, trade_cost, _trade_price = self.trade_exchange.deal_order(
                    sell_order,
                    position=current_temp,
                )
                cash += trade_val - trade_cost

        value = cash * self.risk_degree / len(buy) if buy else 0.0
        for code in buy:
            if not self.trade_exchange.is_stock_tradable(
                stock_id=code,
                start_time=trade_start_time,
                end_time=trade_end_time,
                direction=None if self.forbid_all_trade_at_limit else OrderDir.BUY,
            ):
                continue
            buy_price = self.trade_exchange.get_deal_price(
                stock_id=code,
                start_time=trade_start_time,
                end_time=trade_end_time,
                direction=OrderDir.BUY,
            )
            buy_amount = value / buy_price
            factor = self.trade_exchange.get_factor(
                stock_id=code,
                start_time=trade_start_time,
                end_time=trade_end_time,
            )
            buy_amount = self.trade_exchange.round_amount_by_trade_unit(
                buy_amount,
                factor,
            )
            buy_order_list.append(
                Order(
                    stock_id=code,
                    amount=buy_amount,
                    start_time=trade_start_time,
                    end_time=trade_end_time,
                    direction=Order.BUY,
                )
            )

        return TradeDecisionWO(sell_order_list + buy_order_list, self)
