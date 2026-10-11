"""Common, dependency-free paper-account accounting and replay valuation rules.

Prices and quantities are always Qlib-adjusted coordinates.  This module is
used by both historical paper replays and forward paper settlement.  It does
not model queue position, book depth or partial fills (small account policy).
"""
from __future__ import annotations

import math


def _positive(value, label):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"invalid {label}: {value!r}")
    return number


def close_valuation(cash, positions, close_prices, *, confirmed_halts=()):
    """Mark every held name or fail; a confirmed halt may carry its last close.

    Missing provider data is NOT equivalent to a certified suspension.
    """
    amount = float(cash)
    if not math.isfinite(amount) or amount < -0.01:
        raise ValueError("invalid cash")
    halts = set(confirmed_halts)
    marks = {}
    for symbol, position in sorted(positions.items()):
        shares = _positive(position["shares"], f"{symbol} shares")
        quote = close_prices.get(symbol)
        if quote is None:
            if symbol not in halts:
                raise ValueError(f"unverified missing close for held {symbol}")
            price = _positive(position.get("last_price"), f"{symbol} certified halt carry")
            reason = "CERTIFIED_HALT_CARRY"
        else:
            price = _positive(quote, f"{symbol} close")
            reason = "EXCHANGE_CLOSE"
        marks[symbol] = {"shares": shares, "price": price, "reason": reason}
        amount += shares * price
    if not math.isfinite(amount) or amount <= 0:
        raise ValueError("invalid end-of-day NAV")
    return amount, marks


def verify_ledger(report, *, tolerance=0.011):
    """Independently recompute cash, share inventory and close NAV from fills.

    This validator deliberately does not call PaperPortfolio accounting.
    """
    opening_cash = float(report["opening_cash"])
    end_cash = float(report["cash"])
    opening = report["opening_positions"]
    closing = report["position_marks"]
    expected_cash = opening_cash
    quantities = {symbol: float(p["shares"]) for symbol, p in opening.items()}
    cost_sum = 0.0
    for side, sign in (("executed_sell", -1), ("executed_buy", 1)):
        for fill in report[side]:
            symbol = str(fill["instrument"])
            shares = _positive(fill["shares"], "fill shares")
            price = _positive(fill["price"], "fill price")
            cost = float(fill["cost"])
            if not math.isfinite(cost) or cost < 0:
                raise ValueError("invalid fill cost")
            expected_cash += (-sign) * shares * price - cost
            quantities[symbol] = quantities.get(symbol, 0.0) + sign * shares
            cost_sum += cost
    if abs(expected_cash - end_cash) > tolerance:
        raise ValueError(f"cash not conserved: expected {expected_cash:.6f}, got {end_cash:.6f}")
    if abs(cost_sum - float(report["cost"])) > tolerance:
        raise ValueError("fee ledger mismatch")
    for symbol, shares in quantities.items():
        actual = float(closing.get(symbol, {}).get("shares", 0.0))
        if not math.isclose(actual, shares, rel_tol=1e-9, abs_tol=1e-7):
            raise ValueError(f"share inventory mismatch: {symbol}")
    if any(symbol not in quantities for symbol in closing):
        raise ValueError("unexplained added holding")
    expected_nav = end_cash
    for symbol, mark in closing.items():
        expected_nav += _positive(mark["shares"], f"{symbol} shares") * _positive(mark["price"], f"{symbol} price")
    if abs(expected_nav - float(report["portfolio_value_close"])) > tolerance:
        raise ValueError("close NAV does not match cash plus marked holdings")
    return True
