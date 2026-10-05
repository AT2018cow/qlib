"""Board-aware execution layer for research backtests (R24 + R25, decisions 2026-10-05).

Subclasses qlib's Exchange without modifying the qlib package:
- R24: per-instrument/date price-limit blocks from board_rules (board type, ChiNext
  reform date, first-5-sessions listing exemption). Replaces the scalar
  limit_threshold that misprices mixed-board universes (csi1000 contains
  ChiNext/STAR members).
- R25: T+1 open fill convention (deal_price="$open"), matching the production
  protocol "T close data -> T score -> publish 07:00 -> execute at T+1 open".
- Execution-day protection encoded in the same masks: buys are blocked when the
  day opens more than `high_open_block` above the previous close (the manual
  "T+1 高开>5% 跳过" rule, now applied consistently in research).

Limit semantics (documented approximation, daily bars):
- limit_up / limit_down are evaluated on the OPEN gap versus the previous close:
  a stock that opens at/through its board's limit cannot be meaningfully filled
  that day, so the order is blocked for the whole day (same coarse daily
  granularity as qlib's scalar rule, but with the correct board threshold).
- Listing exemption: board_rules.limit_threshold returns None for the first
  5 sessions of a STAR/ChiNext listing -> no limit block those days (the
  high-open protection still applies).
- Suspension: qlib's own rule ($close NaN) is preserved.

Only the limit columns are overridden; everything else (costs, trade unit,
volume limits, deal price handling) is qlib's standard behaviour.
"""
from __future__ import annotations

from bisect import bisect_left
from typing import Dict, Optional

import numpy as np
import pandas as pd

from board_rules import CHINEXT_REFORM, TH_5, TH_10, TH_20, TH_30
from qlib.backtest.exchange import Exchange

_NOMINAL = {"star": TH_20, "chinext": TH_20, "main": TH_10, "bse": TH_30}


def board_thresholds(insts, dates) -> np.ndarray:
    """Vectorized per-row nominal limit threshold; NaN = no limit (index/new-listing)."""
    inst_arr = np.asarray([str(s) for s in insts])
    boards = np.array([_NOMINAL.get(_board_quick(s), np.nan) for s in inst_arr], dtype=float)
    date_strs = np.array([str(d)[:10] for d in dates])
    reform_mask = (inst_arr.str if False else np.array([s.startswith("SZ3") for s in inst_arr])) & (
        date_strs < CHINEXT_REFORM
    )
    boards = np.where(boards == TH_20, np.where(reform_mask, TH_10, TH_20), boards)
    return boards


def _board_quick(symbol: str) -> str:
    """Fast board classification without full validation (execution hot path).

    Must stay consistent with board_rules.board_of (cross-checked by test).
    """
    if len(symbol) != 8:
        return "main"
    pfx, body = symbol[:2], symbol[2:]
    if pfx == "BJ":
        return "index" if body.startswith("899") else "bse"
    if pfx == "SH" and body.startswith(("688", "689")):
        return "star"
    if pfx == "SZ" and body.startswith(("300", "301", "302")):
        return "chinext"
    if (pfx == "SH" and body.startswith(("000", "880"))) or (pfx == "SZ" and body.startswith("399")):
        return "index"
    return "main"


def compute_limit_masks(insts, dates, open_px, prev_close, close_na,
                        high_open_block: float = 0.05,
                        listing_dates: Optional[Dict[str, str]] = None,
                        calendar=None,
                        st_symbols: Optional[set[str]] = None) -> tuple:
    """Pure limit-mask computation (unit-testable, no qlib dependency).

    Returns (limit_buy, limit_sell) boolean arrays aligned with the inputs.
    - limit_up/down evaluated on the open gap vs previous close with the
      board-aware threshold (NaN threshold = no limit that day, e.g. index rows
      or new-listing exemption sessions).
    - high_open_block: buys blocked when the day opens more than this fraction
      above the previous close (execution-day protection, applies regardless
      of limit exemption).
    - listing_dates/calendar: optional new-listing exemption inputs; when
      provided, star/chinext rows within the first 5 sessions get no limit.
    """
    inst_arr = np.asarray([str(s) for s in insts])
    thr = board_thresholds(inst_arr, dates)
    if st_symbols:
        st = {str(x).upper() for x in st_symbols}
        thr = np.where(np.array([x.upper() in st for x in inst_arr]), TH_5, thr)
    open_arr = np.asarray(open_px, dtype=float)
    prev_arr = np.asarray(prev_close, dtype=float)
    with np.errstate(invalid="ignore", divide="ignore"):
        gap = open_arr / prev_arr - 1.0
    valid_prev = np.isfinite(prev_arr) & (prev_arr > 0)
    limit_up = np.where(valid_prev & ~np.isnan(thr), gap >= thr, False)
    limit_down = np.where(valid_prev & ~np.isnan(thr), gap <= -thr, False)
    high_open = np.where(valid_prev, gap > float(high_open_block), False)
    if listing_dates and calendar:
        cal_idx = {d: i for i, d in enumerate(calendar)}
        d_idx = np.array([cal_idx.get(str(d)[:10], -1) for d in dates])
        for inst, listing in listing_dates.items():
            li = cal_idx.get(str(listing)[:10])
            if li is None:
                continue
            rows = inst_arr == inst
            exempt = rows & (d_idx >= 0) & (d_idx <= li + 4)
            thr = np.where(exempt, np.nan, thr)
            limit_up = np.where(exempt, False, limit_up)
            limit_down = np.where(exempt, False, limit_down)
    limit_buy = np.asarray(close_na, bool) | limit_up | high_open
    limit_sell = np.asarray(close_na, bool) | limit_down
    return limit_buy, limit_sell


class BoardAwareExchange(Exchange):
    """Exchange with per-instrument/date board-aware limits and T+1-open-era
    execution-day protection. See module docstring."""

    def __init__(self, *args, high_open_block: float = 0.05,
                 enforce_board_limits: bool = True,
                 st_symbols: Optional[list[str]] = None, **kwargs):
        self._high_open_block = float(high_open_block)
        self._enforce = bool(enforce_board_limits)
        self._st_symbols = {str(x).upper() for x in (st_symbols or [])}
        # $open is the fill price (R25); Ref($close,1) is the open-gap reference.
        extra = ["$open", "Ref($close,1)"]
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
        )
        df["limit_buy"] = limit_buy
        df["limit_sell"] = limit_sell

    def _listing_dates(self, insts) -> Dict[str, str]:
        """Listing date (first span start) for star/chinext instruments in the universe."""
        from qlib.data import D

        wanted = [s for s in insts if _board_quick(str(s)) in ("star", "chinext")]
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
                      st_symbols: Optional[list[str]] = None) -> dict:
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
                "high_open_block": 0.05,
                "st_symbols": list(st_symbols or []),
                "open_cost": 0.0005,
                "close_cost": 0.0015,
                "min_cost": 5,
            },
        }
    }
