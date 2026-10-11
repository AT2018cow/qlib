"""Pure execution-day statutory limit rules for research and forward paper.

No dependency on Qlib or the broker. The same masks are used in
BoardAwareExchange and historical PaperPortfolio replay.
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np

from board_rules import (
    CHINEXT_REFORM,
    MAIN_REGISTRATION_FIRST_LISTING,
    MAIN_ST_10_START,
)

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


