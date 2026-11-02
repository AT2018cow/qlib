"""Periodic wrapper around the audited deterministic TopK/DropN strategy.

The signal remains daily.  On non-rebalance sessions the strategy emits an
empty decision, so existing holdings are marked to market but not traded.
This isolates the effect of execution cadence from the signal itself.
"""

from __future__ import annotations

from qlib.backtest.decision import TradeDecisionWO

from deterministic_strategy import DeterministicTopkDropoutStrategy


class PeriodicTopkDropoutStrategy(DeterministicTopkDropoutStrategy):
    """Rebalance every N trading sessions; hold otherwise."""

    def __init__(self, *args, rebalance_every: int = 5, **kwargs):
        super().__init__(*args, **kwargs)
        self.rebalance_every = int(rebalance_every)
        if self.rebalance_every < 1:
            raise ValueError("rebalance_every must be >= 1")

    def generate_trade_decision(self, execute_result=None):
        trade_step = self.trade_calendar.get_trade_step()
        if trade_step % self.rebalance_every != 0:
            return TradeDecisionWO([], self)
        return super().generate_trade_decision(execute_result)
