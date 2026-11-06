"""Physical-share order rules shared by paper and the Qlib exchange adapter.

Supports stock auction limit orders, including STAR minimum 200/step 1 and
BSE minimum 100/step 1. Odd-lot liquidation is allowed only for the full
remaining holding. Adjusted shares are physical shares divided by factor.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

EXECUTION_RULE_VERSION = "cn_equity_execution_v2_20261106"


@dataclass(frozen=True)
class QuantityRule:
    minimum: int
    step: int
    maximum: int


def quantity_rule(symbol, day, *, fallback_unit=100):
    if str(symbol).startswith(("SH688", "SH689")):
        if str(day)[:10] < "2019-07-22":
            raise ValueError("STAR order before trading inception")
        return QuantityRule(200, 1, 100_000)
    if str(symbol).startswith("BJ"):
        return QuantityRule(100, 1, 1_000_000)
    return QuantityRule(fallback_unit, fallback_unit, 1_000_000)


def round_quantity(amount, factor, symbol, day, *, fallback_unit=100):
    if not math.isfinite(float(amount)) or float(amount) < 0:
        raise ValueError("invalid order quantity")
    if not math.isfinite(float(factor)) or float(factor) <= 0:
        raise ValueError("invalid quantity factor")
    rule = quantity_rule(symbol, day, fallback_unit=fallback_unit)
    physical = min(rule.maximum, math.floor(float(amount) * float(factor) + 1e-6))
    physical = physical // rule.step * rule.step
    return physical / factor if physical >= rule.minimum else 0.0


def sell_quantity(amount, factor, symbol, day, *, fallback_unit=100):
    """Allow full odd-lot liquidation, while respecting the single-order cap."""
    if not math.isfinite(float(factor)) or factor <= 0 or not math.isfinite(float(amount)) or amount <= 0:
        raise ValueError("invalid sell quantity or factor")
    rule = quantity_rule(symbol, day, fallback_unit=fallback_unit)
    return min(amount, rule.maximum / factor)


def affordable_quantity(
    budget, price, factor, symbol, day, *, rate=0.0, minimum_fee=0.0, extra_rate=0.0, fallback_unit=100
):
    """Maximum legal quantity whose notional plus fee fits the budget."""
    for number in (budget, price, factor, rate, minimum_fee, extra_rate):
        if not math.isfinite(float(number)):
            raise ValueError("non-finite order sizing input")
    if budget < 0 or price <= 0 or factor <= 0 or min(rate, minimum_fee, extra_rate) < 0:
        raise ValueError("invalid order sizing input")
    notional = max(0.0, min(budget / (1 + rate + extra_rate), (budget - minimum_fee) / (1 + extra_rate)))
    shares = round_quantity(notional / price, factor, symbol, day, fallback_unit=fallback_unit)
    if shares * price * (1 + extra_rate) + max(shares * price * rate, minimum_fee) > budget + 1e-7:
        rule = quantity_rule(symbol, day, fallback_unit=fallback_unit)
        shares = round_quantity(max(0.0, shares - rule.step / factor), factor, symbol, day, fallback_unit=fallback_unit)
    return shares
