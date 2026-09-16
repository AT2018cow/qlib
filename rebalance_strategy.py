# 低频调仓版 TopkDropout：每 rebalance_days 个交易日调仓一次，其余交易日不交易。
# 用于匹配 20 日标签（月度调仓）等低频信号的回测。
from qlib.backtest.decision import TradeDecisionWO
from qlib.contrib.strategy import TopkDropoutStrategy


class PeriodicTopkStrategy(TopkDropoutStrategy):
    def __init__(self, rebalance_days: int = 20, **kwargs):
        super().__init__(**kwargs)
        self._rebalance_days = max(1, int(rebalance_days))

    def generate_trade_decision(self, execute_result=None):
        trade_step = self.trade_calendar.get_trade_step()
        if trade_step % self._rebalance_days != 0:
            # 非调仓日：不产生任何交易
            return TradeDecisionWO([], self)
        return super().generate_trade_decision(execute_result=execute_result)