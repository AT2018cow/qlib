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

Only the limit columns are overridden; everything else (costs, trade unit,
volume limits, deal price handling) is qlib's standard behaviour.
"""
from __future__ import annotations

from bisect import bisect_left
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


class BoardAwareExchange(Exchange):
    """Exchange with per-instrument/date board-aware limits and T+1-open-era
    execution-day protection. See module docstring."""

    def __init__(self, *args, high_open_block: Optional[float] = None,
                 enforce_board_limits: bool = True,
                 st_symbols: Optional[list[str]] = None,
                 uniform_limit_ratio: Optional[float] = None, **kwargs):
        self._high_open_block = None if high_open_block is None else float(high_open_block)
        self._enforce = bool(enforce_board_limits)
        self._st_symbols = {str(x).upper() for x in (st_symbols or [])}
        self._uniform_limit_ratio = (
            None if uniform_limit_ratio is None else float(uniform_limit_ratio)
        )
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


def research_exchange(start_time: str, end_time: str, codes="all",
                      st_symbols: Optional[list[str]] = None,
                      high_open_block: Optional[float] = None) -> dict:
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
                "st_symbols": list(st_symbols or []),
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


def uniform_open_exchange(start_time: str, end_time: str, codes="all",
                          ratio: float = 0.095) -> dict:
    """Uniform limit control evaluated only from open / previous close (B).

    The default 9.5% exactly preserves the legacy scalar threshold while
    removing the same-day-close oracle. Keeping threshold and strategy
    semantics fixed makes A->B an attribution of information timing rather
    than a simultaneous market-rule change.
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
                "high_open_block": None,
                "st_symbols": [],
                "open_cost": 0.0005,
                "close_cost": 0.0015,
                "min_cost": 5,
            },
        }
    }
