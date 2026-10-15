"""Pure frozen-decision holding/quote inventory; no Qlib/Exchange/backtest.

A *complete* trading-calendar sweep runs before independent NAV accounting.
It never infers a suspension from a missing quote or forward-fills a price.
Rows contain metadata and issue categories only, not proprietary market bars.
"""
from __future__ import annotations

import math


def _positive(value) -> bool:
    try:
        return value is not None and math.isfinite(float(value)) and float(value) > 0
    except (TypeError, ValueError, OverflowError):
        return False


def _conflicting_suspension_close(value) -> bool:
    """Match verifier: on a documented halt only None / nonfinite is absent."""
    if value is None:
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return True


def scan_holding_quotes(decisions, quotes, dates, documented_suspensions,
                        known_st_execution=None):
    """Scan held stocks *after* the day's frozen fills, across every date.

    Documented suspensions map (date ISO, stock) -> announcement URL.  Only
    this map permits carry-forward accounting; unresolved gaps block it.
    This is an inventory scan, NOT historical execution validation.
    """
    if len(decisions) != len(dates) or len({str(d) for d in dates}) != len(dates):
        raise ValueError("inventory decision/calendar coverage drift")
    known_st_execution = known_st_execution or {}
    held = {}
    previous_mark_date = {}
    issues = []
    count_orders = 0
    for i, (day, decision) in enumerate(zip(dates, decisions)):
        today = day.isoformat() if hasattr(day, "isoformat") else str(day)[:10]
        if str(decision.get("start_time", ""))[:10] != today or decision.get("decision_index") != i:
            raise ValueError(f"inventory decision clock/index {today}")
        for pos, order in enumerate(decision["orders"]):
            symbol = str(order["stock_id"])
            if (order["order_index"] != pos or str(order["start_time"])[:10] != today
                    or order["direction"] not in (0, 1)):
                raise ValueError(f"inventory invalid order {today}/{symbol}")
            qty, requested = float(order["deal_amount"]), float(order["amount"])
            if not (_positive(qty) and _positive(requested)
                    and math.isclose(qty, requested, rel_tol=1e-10, abs_tol=1e-6)):
                raise ValueError(f"inventory partial/invalid fill {today}/{symbol}")
            q = quotes.get((today, symbol))
            if not (q is not None and len(q) == 4 and all(_positive(q[j]) for j in (0, 1, 2))):
                issues.append({"date": today, "stock_id": symbol,
                               "issue": "INVALID_FROZEN_FILL_QUOTE", "blocking": True})
            if (today, symbol) in documented_suspensions:
                issues.append({"date": today, "stock_id": symbol,
                               "issue": "ORDER_ON_DOCUMENTED_SUSPENSION", "blocking": True})
            if order["direction"] == 1:
                held[symbol] = held.get(symbol, 0.0) + qty
            else:
                before = held.get(symbol, 0.0)
                if not (before > 0 and math.isclose(before, qty, rel_tol=1e-9, abs_tol=1e-5)):
                    raise ValueError(f"inventory impossible frozen sell {today}/{symbol}")
                del held[symbol]
                previous_mark_date.pop(symbol, None)
            if (today, symbol) in known_st_execution:
                issues.append({"date": today, "stock_id": symbol,
                               "issue": "HISTORICAL_ST_FILL_NOT_CERTIFIED", "blocking": False,
                               "evidence": known_st_execution[(today, symbol)]["notice"]})
            count_orders += 1
        for symbol in sorted(held):
            q = quotes.get((today, symbol))
            close = q[1] if q is not None else None
            evidence = documented_suspensions.get((today, symbol))
            if evidence:
                if _conflicting_suspension_close(close):
                    issues.append({"date": today, "stock_id": symbol,
                                   "issue": "DOCUMENTED_SUSPENSION_HAS_CLOSE", "blocking": True,
                                   "evidence": evidence})
                elif symbol not in previous_mark_date:
                    issues.append({"date": today, "stock_id": symbol,
                                   "issue": "DOCUMENTED_SUSPENSION_NO_PRIOR_HELD_MARK",
                                   "blocking": True, "evidence": evidence})
                else:
                    issues.append({"date": today, "stock_id": symbol,
                                   "issue": "DOCUMENTED_SUSPENSION_CARRY_MARK",
                                   "prior_mark_date": previous_mark_date[symbol],
                                   "blocking": False, "evidence": evidence})
            elif not _positive(close):
                issues.append({"date": today, "stock_id": symbol,
                               "issue": "UNKNOWN_HELD_CLOSE", "blocking": True,
                               "last_valid_mark_date": previous_mark_date.get(symbol)})
            else:
                previous_mark_date[symbol] = today
    blockers = [x for x in issues if x["blocking"]]
    return {
        "status": "BLOCKED_INPUT_QUOTES" if blockers else "READY_FOR_ACCOUNTING_ONLY",
        "complete_inventory_calendar_days": len(dates),
        "frozen_orders_scanned": count_orders,
        "anomaly_count": len(issues),
        "blocking_count": len(blockers),
        "unknown_held_close_count": sum(x["issue"] == "UNKNOWN_HELD_CLOSE" for x in issues),
        "documented_suspension_carry_count":
            sum(x["issue"] == "DOCUMENTED_SUSPENSION_CARRY_MARK" for x in issues),
        "st_execution_warning_count":
            sum(x["issue"] == "HISTORICAL_ST_FILL_NOT_CERTIFIED" for x in issues),
        "issues": issues,  # Full, date-sorted inventory; never return only first gap.
        "market_execution": "NOT_CERTIFIED",
        "model_training": False,
    }
