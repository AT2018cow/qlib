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

_EXEC_NOMINAL = {"star": 0.20, "chinext": 0.20, "main": 0.10, "bse": 0.30}


def board_thresholds(insts, dates) -> np.ndarray:
    """Vectorized statutory limit ratios for execution; NaN = no price limit.

    These are exact exchange ratios (10/20/30/5%), deliberately separate from
    board_rules.TH_* audit filters (9.5/19.5/29.5/4.5%).
    """
    inst_arr = np.asarray([str(s) for s in insts])
    boards = np.array([_EXEC_NOMINAL.get(_board_quick(s), np.nan) for s in inst_arr], dtype=float)
    date_strs = np.array([str(d)[:10] for d in dates])
    reform_mask = np.array([s.startswith("SZ3") for s in inst_arr]) & (date_strs < CHINEXT_REFORM)
    boards = np.where(boards == 0.20, np.where(reform_mask, 0.10, 0.20), boards)
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
                        high_open_block: Optional[float] = None,
                        listing_dates: Optional[Dict[str, str]] = None,
                        calendar=None,
                        st_symbols: Optional[set[str]] = None,
                        uniform_limit_ratio: Optional[float] = None,
                        factors=None) -> tuple:
    """Pure limit-mask computation (unit-testable, no qlib dependency).

    Returns (limit_buy, limit_sell) boolean arrays aligned with the inputs.
    - limit_up/down evaluated on the open gap vs previous close with the
      board-aware threshold (NaN threshold = no limit that day, e.g. index rows
      or new-listing exemption sessions).
    - high_open_block: buys blocked when the day opens more than this fraction
      above the previous close (execution-day protection, applies regardless
      of limit exemption).
    - listing_dates/calendar: optional new-listing exemption inputs for
      STAR/ChiNext first five sessions, post-registration main-board first
      five sessions, and BSE listing day.
    - factors: Qlib daily $factor aligned to each row.  Qlib stores normalized
      adjusted prices; adjusted_price / factor reconstructs the original CNY
      price scale.  Exact statutory 0.01-CNY rounding must happen there.
      Omit only in pure unit tests whose prices are already raw CNY.
    """
    inst_arr = np.asarray([str(s) for s in insts])
    date_strs = np.asarray([str(d)[:10] for d in dates])
    if len(inst_arr) != len(date_strs):
        raise ValueError("insts and dates must have the same length")
    if uniform_limit_ratio is not None:
        if not (0 < float(uniform_limit_ratio) < 1):
            raise ValueError("uniform_limit_ratio must be in (0, 1)")
        # Attribution control B: reproduce the old one-size-fits-all policy,
        # but calculate it only from execution-day open / previous close.
        # Listing/ST/board exceptions are intentionally disabled in this mode.
        thr = np.full(len(inst_arr), float(uniform_limit_ratio), dtype=float)
    else:
        thr = board_thresholds(inst_arr, date_strs)
    if st_symbols and uniform_limit_ratio is None:
        st = {str(x).upper() for x in st_symbols}
        st_mask = np.array([x.upper() in st for x in inst_arr])
        board_kind = np.array([_board_quick(x) for x in inst_arr])
        # Current/historical risk-warning limits:
        # main: 5% before 2026-07-06, 10% afterwards;
        # ChiNext: 5% before reform, 20% afterwards;
        # STAR/BSE keep their board limits.
        main_old = st_mask & (board_kind == "main") & (date_strs < MAIN_ST_10_START)
        chn_old = st_mask & (board_kind == "chinext") & (date_strs < CHINEXT_REFORM)
        thr = np.where(main_old | chn_old, 0.05, thr)
    open_arr = np.asarray(open_px, dtype=float)
    prev_arr = np.asarray(prev_close, dtype=float)
    if factors is None:
        factor_arr = np.ones(len(inst_arr), dtype=float)
    else:
        factor_arr = np.asarray(factors, dtype=float)
        if len(factor_arr) != len(inst_arr):
            raise ValueError("factors must align with insts/dates")
    with np.errstate(invalid="ignore", divide="ignore"):
        # Adjusted-price ratio is the economically meaningful open gap across
        # corporate actions and is retained for diagnostic percentage rules.
        gap = open_arr / prev_arr - 1.0
    valid_prev = np.isfinite(prev_arr) & (prev_arr > 0)
    limited = valid_prev & ~np.isnan(thr)

    if uniform_limit_ratio is not None:
        # Attribution control B must match the old scalar 9.5% threshold
        # exactly, changing only *when* the return is observed (open gap rather
        # than execution-day full-day $change).
        limit_up = np.where(limited, gap >= float(uniform_limit_ratio), False)
        limit_down = np.where(limited, gap <= -float(uniform_limit_ratio), False)
    else:
        # Qlib CN data stores normalized adjusted prices.  A 0.01 tick is a
        # rule on original RMB prices, not on those normalized values.
        # Using the current day's factor for both prices maps Ref(close, 1)
        # onto the current ex-right/ex-dividend price basis.
        factor_ok = np.isfinite(factor_arr) & (factor_arr > 0)
        if np.any(limited & ~factor_ok):
            bad = inst_arr[limited & ~factor_ok][:5].tolist()
            raise ValueError(f"missing/invalid $factor for statutory limit rows: {bad}")
        with np.errstate(invalid="ignore", divide="ignore"):
            raw_open = open_arr / factor_arr
            raw_reference = prev_arr / factor_arr
        # Positive-price decimal half-up rounding to the exchange's 0.01 CNY tick.
        upper_px = np.floor(
            raw_reference * (1.0 + np.nan_to_num(thr, nan=0.0)) * 100.0 + 0.5
        ) / 100.0
        lower_px = np.floor(
            raw_reference * (1.0 - np.nan_to_num(thr, nan=0.0)) * 100.0 + 0.5
        ) / 100.0
        eps = 1e-8
        limit_up = np.where(limited, raw_open >= upper_px - eps, False)
        limit_down = np.where(limited, raw_open <= lower_px + eps, False)
    high_open = np.where(valid_prev, gap > float(high_open_block), False) if high_open_block is not None else np.zeros_like(valid_prev, dtype=bool)
    if listing_dates and calendar and uniform_limit_ratio is None:
        cal_idx = {d: i for i, d in enumerate(calendar)}
        d_idx = np.array([cal_idx.get(str(d)[:10], -1) for d in dates])
        for inst, listing in listing_dates.items():
            listing = str(listing)[:10]
            li = cal_idx.get(listing)
            if li is None:
                continue
            board = _board_quick(inst)
            if board in ("star", "chinext"):
                n_exempt = 5
            elif board == "main" and listing >= MAIN_REGISTRATION_FIRST_LISTING:
                n_exempt = 5
            elif board == "bse":
                n_exempt = 1
            else:
                n_exempt = 0
            if not n_exempt:
                continue
            rows = inst_arr == inst
            exempt = rows & (d_idx >= li) & (d_idx < li + n_exempt)
            thr = np.where(exempt, np.nan, thr)
            limit_up = np.where(exempt, False, limit_up)
            limit_down = np.where(exempt, False, limit_down)
    limit_buy = np.asarray(close_na, bool) | limit_up | high_open
    limit_sell = np.asarray(close_na, bool) | limit_down
    return limit_buy, limit_sell


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
