"""Board-aware execution layer for research backtests (R24 + R25, decisions 2026-10-05).

Subclasses qlib's Exchange without modifying the qlib package:
- R24: per-instrument/date price-limit blocks from board_rules (board type, ChiNext
  reform date, first-5-sessions listing exemption). Replaces the scalar
  limit_threshold that misprices mixed-board universes (csi1000 contains
  ChiNext/STAR members).
- R25: T+1 open fill convention (deal_price="$open"), matching the production
  protocol "T close data -> T score -> publish 07:00 -> execute at T+1 open".
- Optional execution-day buy protection can be requested with `high_open_block`
  for attribution diagnostics, but the standard research/live paper baseline
  leaves it disabled.
- Statutory 0.01 CNY limit-price rounding is performed on the reconstructed
  original-RMB price scale, never on Qlib's normalized adjusted prices.

Limit semantics (documented approximation, daily bars):
- limit_up / limit_down are evaluated on the OPEN gap versus the previous close:
  a stock that opens at/through its board's limit cannot be meaningfully filled
  that day, so the order is blocked for the whole day (same coarse daily
  granularity as qlib's scalar rule, but with the correct board threshold).
- Listing exemption: the first 5 sessions of a STAR/ChiNext listing receive no
  statutory limit block when listing metadata is available.
- Suspension: qlib's own rule ($close NaN) is preserved.

Board-aware execution also applies the shared legal quantity rules. The
uniform attribution control explicitly retains native Qlib quantities so its
comparison with the legacy control isolates the information timing change.
"""

from __future__ import annotations

from bisect import bisect_left
from contextvars import ContextVar
from typing import Dict, Optional

import numpy as np
import pandas as pd

from board_rules import (
    CHINEXT_REFORM,
    MAIN_REGISTRATION_FIRST_LISTING,
    MAIN_ST_10_START,
)
from qlib.backtest.exchange import Exchange

# Backwards-compatible exports. Both Exchange and paper replay now call the
# same pure executable statutory-limit implementation.
from execution_limits import _board_quick, board_thresholds, compute_limit_masks
from order_quantity import round_quantity, sell_quantity

_ACTIVE_ORDER = ContextVar("board_execution_order", default=None)


class _OrderFactor(float):
    """Numeric factor retaining identity for Qlib's legacy two-argument API."""

    def __new__(cls, value, stock_id, start_time, end_time):
        obj = super().__new__(cls, value)
        obj.stock_id, obj.start_time, obj.end_time = stock_id, start_time, end_time
        return obj

    def __reduce__(self):
        return type(self), (float(self), self.stock_id, self.start_time, self.end_time)


class BoardAwareExchange(Exchange):
    """Exchange with per-instrument/date board-aware limits and T+1-open-era
    execution-day protection. See module docstring."""

    def get_factor(self, stock_id, start_time, end_time):
        value = super().get_factor(stock_id, start_time, end_time)
        if value is None:
            return None
        return _OrderFactor(value, stock_id, start_time, end_time)

    def _calc_trade_info_by_order(self, order, position, dealt_order_amount):
        if not getattr(self, "_enforce_board_quantities", True):
            return super()._calc_trade_info_by_order(order, position, dealt_order_amount)
        # Upstream calls rounding with only amount/factor. Preserve the order
        # identity without mutable process-global or thread-shared state.
        token = _ACTIVE_ORDER.set(order)
        requested_amount = order.amount
        try:
            from qlib.backtest.decision import Order

            if order.direction == Order.SELL:
                factor = self.get_factor(order.stock_id, order.start_time, order.end_time)
                order.amount = sell_quantity(order.amount, factor, order.stock_id, str(order.start_time)[:10])
            return super()._calc_trade_info_by_order(order, position, dealt_order_amount)
        finally:
            order.amount = requested_amount
            _ACTIVE_ORDER.reset(token)

    def round_amount_by_trade_unit(self, deal_amount, factor=None, stock_id=None, start_time=None, end_time=None):
        if not getattr(self, "_enforce_board_quantities", True):
            return super().round_amount_by_trade_unit(deal_amount, factor)
        order = _ACTIVE_ORDER.get()
        if stock_id is None and order is not None:
            stock_id, start_time, end_time = order.stock_id, order.start_time, order.end_time
        elif stock_id is None and isinstance(factor, _OrderFactor):
            stock_id, start_time, end_time = factor.stock_id, factor.start_time, factor.end_time
        if stock_id is None:
            raise ValueError("board-aware rounding requires an order identity")
        if factor is None:
            factor = self.get_factor(stock_id, start_time, end_time)
        return round_quantity(deal_amount, factor, stock_id, str(start_time)[:10])

    def __init__(
        self,
        *args,
        high_open_block: Optional[float] = None,
        enforce_board_limits: bool = True,
        enforce_board_quantities: bool = True,
        st_symbols: Optional[list[str]] = None,
        uniform_limit_ratio: Optional[float] = None,
        st_history=None,
        require_verified_st=False,
        **kwargs,
    ):
        self._high_open_block = None if high_open_block is None else float(high_open_block)
        self._enforce = bool(enforce_board_limits)
        self._enforce_board_quantities = bool(enforce_board_quantities)
        self._st_symbols = {str(x).upper() for x in (st_symbols or [])}
        self._st_history = st_history
        if require_verified_st and st_history is None:
            raise ValueError("dated ST history required for verified research execution")
        if st_history is not None and st_symbols:
            raise ValueError("static ST symbols cannot be combined with dated history")
        self._uniform_limit_ratio = None if uniform_limit_ratio is None else float(uniform_limit_ratio)
        # $open is the fill price (R25); Ref($close,1) is the open-gap reference.
        # $factor is required to reconstruct original CNY prices for 0.01-tick
        # statutory limit rounding.
        extra = ["$open", "Ref($close,1)", "$factor"]
        kwargs["subscribe_fields"] = list(kwargs.get("subscribe_fields") or []) + extra
        super().__init__(*args, **kwargs)
        # super().__init__ already ran get_quote_from_qlib -> _update_limit.

    def _update_limit(self, limit_threshold) -> None:
        if not self._enforce:
            super()._update_limit(limit_threshold)
            return
        df = self.quote_df
        suspended = df["$close"].isna().to_numpy()
        idx = df.index
        listing_dates = self._listing_dates(idx.get_level_values(0).unique())
        calendar = self._full_calendar(idx.get_level_values(1))
        flags = None
        if self._st_history is not None:
            from st_history import st_flags

            flags = st_flags(self._st_history, idx.get_level_values(0), idx.get_level_values(1))
        limit_buy, limit_sell = compute_limit_masks(
            idx.get_level_values(0),
            idx.get_level_values(1),
            df["$open"].to_numpy(dtype=float),
            pd.to_numeric(df["Ref($close,1)"], errors="coerce").to_numpy(dtype=float),
            suspended,
            high_open_block=self._high_open_block,
            listing_dates=listing_dates,
            calendar=calendar,
            st_symbols=self._st_symbols,
            st_flags=flags,
            uniform_limit_ratio=self._uniform_limit_ratio,
            factors=pd.to_numeric(df["$factor"], errors="coerce").to_numpy(dtype=float),
        )
        df["limit_buy"] = limit_buy
        df["limit_sell"] = limit_sell

    def _listing_dates(self, insts) -> Dict[str, str]:
        """Listing date (first span start) for stock instruments in the universe."""
        from qlib.data import D

        wanted = [s for s in insts if _board_quick(str(s)) != "index"]
        if not wanted:
            return {}
        spans = D.list_instruments(D.instruments("all"), as_list=False)
        out = {}
        for inst in wanted:
            ss = spans.get(inst)
            if ss:
                out[inst] = str(min(s for s, _ in ss))[:10]
        return out

    def _full_calendar(self, dates) -> list:
        from qlib.data import D

        end = max(str(d)[:10] for d in dates)
        return [str(x)[:10] for x in D.calendar(start_time="2000-01-01", end_time=end)]


def research_exchange(
    start_time: str,
    end_time: str,
    codes="all",
    st_symbols: Optional[list[str]] = None,
    high_open_block: Optional[float] = None,
    *,
    st_history=None,
    require_verified_st=False,
) -> dict:
    """Backtest kwargs that actually instantiate :class:`BoardAwareExchange`.

    Qlib's `backtest(..., exchange_kwargs=...)` splats the dictionary into
    `get_exchange`.  Therefore the custom exchange must be supplied through
    the dedicated `exchange` argument; placing class/module_path beside the
    ordinary kwargs only creates a normal Exchange.  Start/end are embedded in
    the class config because get_exchange does not merge its outer dates into
    a supplied exchange config.
    """
    if not start_time or not end_time:
        raise ValueError("research_exchange requires explicit start_time/end_time")
    return {
        "exchange": {
            "class": "BoardAwareExchange",
            "module_path": "board_execution",
            "kwargs": {
                "freq": "day",
                "start_time": str(start_time),
                "end_time": str(end_time),
                "codes": codes,
                "deal_price": "open",
                "limit_threshold": None,
                "high_open_block": high_open_block,
                "enforce_board_quantities": True,
                "st_symbols": list(st_symbols or []),
                "st_history": st_history,
                "require_verified_st": require_verified_st,
                "open_cost": 0.0005,
                "close_cost": 0.0015,
                "min_cost": 5,
            },
        }
    }


def legacy_scalar_exchange() -> dict:
    """Exact legacy v2 execution control (A).

    This intentionally preserves the old Qlib scalar-limit behavior:
    limit_threshold=0.095 is evaluated from the execution day's full-day
    $change even though the fill price is the open. It is therefore a
    look-ahead diagnostic only and must never be used as production logic.
    """
    return {
        "deal_price": "open",
        "limit_threshold": 0.095,
        "open_cost": 0.0005,
        "close_cost": 0.0015,
        "min_cost": 5,
    }


def uniform_open_exchange(start_time: str, end_time: str, codes="all", ratio: float = 0.095) -> dict:
    """Uniform limit control evaluated only from open / previous close (B).

    The default 9.5% exactly preserves the legacy scalar threshold while
    removing the same-day-close oracle. Keeping threshold and strategy
    semantics fixed makes A->B an attribution of information timing rather
    than a simultaneous market-rule change.

    Native legacy quantities are retained only for this diagnostic control;
    it is not a production execution configuration.
    """
    if not start_time or not end_time:
        raise ValueError("uniform_open_exchange requires explicit start_time/end_time")
    return {
        "exchange": {
            "class": "BoardAwareExchange",
            "module_path": "board_execution",
            "kwargs": {
                "freq": "day",
                "start_time": str(start_time),
                "end_time": str(end_time),
                "codes": codes,
                "deal_price": "open",
                "limit_threshold": None,
                "uniform_limit_ratio": float(ratio),
                "enforce_board_quantities": False,
                "high_open_block": None,
                "st_symbols": [],
                "open_cost": 0.0005,
                "close_cost": 0.0015,
                "min_cost": 5,
            },
        }
    }
