"""Valuation-based exposure and operational diagnostics for each paper account."""

import math


def portfolio_diagnostics(
    cash, positions, *, calendar=(), data_date=None, model_fit_asof=None, last_execution_date=None, report=None
):
    cash = float(cash)
    amounts = {s: float(p["shares"]) * float(p["last_price"]) for s, p in positions.items()}
    if cash < 0 or not all(math.isfinite(x) and x >= 0 for x in [cash, *amounts.values()]):
        raise ValueError("invalid diagnostic valuation")
    nav = cash + sum(amounts.values())
    if nav <= 0:
        raise ValueError("non-positive diagnostic NAV")
    weights = {s: value / nav for s, value in sorted(amounts.items())}
    invested = sum(weights.values())
    concentration = sum((w / invested) ** 2 for w in weights.values()) if invested else 0.0

    def age(day):
        return calendar.index(data_date) - calendar.index(day) if data_date in calendar and day in calendar else None

    return {
        "cash_ratio": cash / nav,
        "largest_position_weight": max(weights.values(), default=0.0),
        "invested_weight": invested,
        "effective_names": 1 / concentration if concentration else 0.0,
        "weights": weights,
        "model_age_sessions": age(model_fit_asof),
        "settlement_age_sessions": age(last_execution_date),
        "blocked_buys": len((report or {}).get("blocked_buy", [])),
        "blocked_sells": len((report or {}).get("blocked_sell", [])),
        "industry_exposure_status": "REQUIRES_DATED_CLASSIFICATION",
    }
