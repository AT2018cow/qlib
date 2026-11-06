"""Independent Decimal reconciliation of the October 8 website account.

This module imports no portfolio, performance or website calculation code.
Ledger agreement does not certify historical ST, auction fills or quote origin.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation


def _number(value):
    if value is None or isinstance(value, bool):
        raise ValueError("missing or nonnumeric amount")
    try:
        value = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("invalid amount") from exc
    if not value.is_finite():
        raise ValueError("nonfinite amount")
    return value


def reconcile_website(series, sessions, *, market, calendar=None, quotes=None):
    """Audit raw session fills against their own website account, read only."""
    issues, rows = [], []
    tolerance, return_tolerance = Decimal("0.035"), Decimal("0.00000001")
    initial = Decimal(1_000_000)

    def issue(code, day, detail):
        issues.append({"code": code, "date": day, "detail": detail})

    def close_to(actual, expected, code, day, *, tol=tolerance):
        if abs(_number(actual) - expected) > tol:
            issue(code, day, f"reported={actual}, independent={expected}")

    def shares(raw):
        if not isinstance(raw, dict):
            raise ValueError("inventory is not an object")
        result = {symbol: _number(position["shares"]) for symbol, position in raw.items()}
        if any(value <= 0 for value in result.values()):
            raise ValueError("inventory must be positive")
        return result

    def same_shares(left, right, day):
        if left.keys() != right.keys() or any(abs(left[s] - right[s]) > Decimal("0.000001") for s in left):
            issue("INVENTORY_DIFF", day, "cash-account inventory is discontinuous or differs from fills")

    from csi1000_production_config import (
        CANONICAL_PROFILES_BY_MARKET,
        CANONICAL_TOPK_BY_MARKET,
        CANONICAL_ND_BY_MARKET,
        profile_manifest,
    )

    profile = CANONICAL_PROFILES_BY_MARKET[market]
    identity = {
        "market": market,
        "profile": profile,
        "paper_lineage": f"{profile}_website_oct08_v2",
        "initial_cash": 1_000_000,
        "topk": CANONICAL_TOPK_BY_MARKET[market],
        "nd": CANONICAL_ND_BY_MARKET[market],
        "horizon": profile_manifest(profile)["label_horizon"],
    }
    if not isinstance(series, dict) or (
        series.get("schema_version") != 3
        or series.get("data_mode") != "CAUSAL_SIMULATION"
        or series.get("status") != "active"
        or series.get("market") != market
        or series.get("account_identity") != identity
        or series.get("initial_cash") != 1_000_000
        or series.get("initial_nav") != 1
        or series.get("start_date") != "2026-10-08"
        or series.get("inception_date") != "2026-10-08"
    ):
        issue("ACCOUNT_SCOPE_MISMATCH", None, "expected the independent October 8 website account")
        return {"status": "FAIL", "ledger_status": "FAIL", "issues": issues, "audited_reports": 0}
    points = series.get("points")
    if not isinstance(points, list) or not points or len(points) != len(sessions):
        issue("SESSION_COUNT_DIFF", None, "website points and supplied ledger sessions differ")
        points = points if isinstance(points, list) else []
    previous_cash, previous_shares, previous_close = initial, {}, initial
    total_fees, dates = Decimal(0), []
    for report, point in zip(sessions, points):
        if not isinstance(report, dict) or not isinstance(point, dict):
            issue("INVALID_LEDGER", None, "session and point must be objects")
            break
        day = report.get("date")
        try:
            if (
                not isinstance(day, str)
                or date.fromisoformat(day).isoformat() != day
                or (dates and day <= dates[-1])
                or (not dates and day != "2026-10-08")
            ):
                raise ValueError("session dates must start October 8 and increase strictly")
            signal = report.get("signal_date")
            if signal != (dates[-1] if dates else "2026-09-30"):
                issue("SIGNAL_DATE_DIFF", day, "execution must use the previous verified session's signal")
            dates.append(day)
            opening = shares(report["opening_positions"])
            close_to(report["opening_cash"], previous_cash, "CASH_BOUNDARY_DIFF", day)
            same_shares(opening, previous_shares, day)
            opening_marks = report.get("opening_marks", {})
            if set(opening_marks) != set(opening):
                issue("OPEN_MARKS_MISSING", day, "every opening holding requires its open mark")
            independent_open = _number(report["opening_cash"])
            for symbol in opening:
                mark = opening_marks[symbol]
                close_to(mark["shares"], opening[symbol], "OPEN_SHARES_DIFF", day, tol=Decimal("0.000001"))
                px = _number(mark["price"])
                if px <= 0:
                    raise ValueError("nonpositive opening price")
                independent_open += opening[symbol] * px
            close_to(report["portfolio_value_pre_trade_open"], independent_open, "OPEN_NAV_DIFF", day)
            cash, inventory, fee_sum = _number(report["opening_cash"]), opening.copy(), Decimal(0)
            for side, rate, sign in (("executed_sell", Decimal("0.0015"), -1), ("executed_buy", Decimal("0.0005"), 1)):
                if not isinstance(report[side], list):
                    raise ValueError("fills must be arrays")
                for fill in report[side]:
                    symbol = fill["instrument"]
                    quantity, px, fee = (_number(fill[k]) for k in ("shares", "price", "cost"))
                    if min(quantity, px) <= 0 or fee < 0:
                        raise ValueError("invalid fill quantity/price/fee")
                    close_to(fee, max(quantity * px * rate, Decimal(5)), "FEE_DIFF", day)
                    fee_sum += fee
                    cash -= sign * quantity * px + fee
                    inventory[symbol] = inventory.get(symbol, Decimal(0)) + sign * quantity
                    if inventory[symbol] < -Decimal("0.000001"):
                        issue("OVERSELL", day, symbol)
                    if abs(inventory[symbol]) < Decimal("0.000001"):
                        inventory.pop(symbol)
                    if quotes is not None:
                        quote = quotes.get((day, symbol), {})
                        close_to(quote.get("open"), px, "INDEPENDENT_OPEN_DIFF", day, tol=Decimal("0.0001"))
            closing = shares(report["position_marks"])
            same_shares(inventory, closing, day)
            close_to(report["cash"], cash, "CASH_FLOW_DIFF", day)
            close_to(report["cost"], fee_sum, "TOTAL_FEE_DIFF", day)
            cash = _number(report["cash"])
            if cash < 0:
                raise ValueError("negative closing cash")
            nav = cash
            for symbol, quantity in closing.items():
                mark = report["position_marks"][symbol]
                px = _number(mark["price"])
                if px <= 0:
                    raise ValueError("nonpositive closing price")
                nav += quantity * px
                if quotes is not None:
                    quote = quotes.get((day, symbol), {})
                    close_to(quote.get("close"), px, "INDEPENDENT_CLOSE_DIFF", day, tol=Decimal("0.0001"))
            close_to(report["portfolio_value_close"], nav, "CLOSE_NAV_DIFF", day)
            independent_nav = nav
            # Public snapshots round cash and total NAV separately. First
            # reconcile absolute CNY amounts within the declared cent tolerance,
            # then independently derive returns from the verified NAV snapshot.
            # Reconstructing returns from rounded cash introduces false errors.
            nav = _number(report["portfolio_value_close"])
            fee_sum = _number(report["cost"])
            if nav <= 0:
                raise ValueError("nonpositive account NAV")
            if point.get("date") != day or point.get("data_mode") != "CAUSAL_SIMULATION":
                issue("POINT_IDENTITY_DIFF", day, "point and ledger dates/modes disagree")
            for field, value in (
                ("portfolio_value_close", nav),
                ("cash", cash),
                ("cost", fee_sum),
                ("nav", nav / initial),
                ("cumulative_return", nav / initial - 1),
                ("daily_return", nav / previous_close - 1),
            ):
                close_to(
                    point.get(field),
                    value,
                    "POINT_VALUE_DIFF",
                    day,
                    tol=return_tolerance if "return" in field or field == "nav" else tolerance,
                )
            for field, expected_cash, expected_shares in (
                ("opening_state", previous_cash, opening),
                ("closing_state", cash, closing),
            ):
                state = point[field]
                close_to(state["cash"], expected_cash, "POINT_BOUNDARY_DIFF", day)
                same_shares({s: _number(q) for s, q in state["shares"].items()}, expected_shares, day)
            total_fees += fee_sum
            previous_cash, previous_shares, previous_close = cash, closing, nav
            rows.append(
                {
                    "date": day,
                    "independent_close": str(independent_nav),
                    "reported_close": str(nav),
                    "fees": str(fee_sum),
                }
            )
        except (KeyError, TypeError, ValueError, InvalidOperation, ZeroDivisionError) as exc:
            issue("INVALID_LEDGER", day, str(exc))
            break
    if series.get("trading_dates") != dates or series.get("latest_date") != (dates[-1] if dates else None):
        issue("SESSION_COVERAGE_DIFF", None, "published session coverage differs from ledger")
    if calendar is not None:
        try:
            valid_calendar = (
                isinstance(calendar, list)
                and calendar == sorted(set(calendar))
                and all(isinstance(d, str) and date.fromisoformat(d).isoformat() == d for d in calendar)
            )
        except (ValueError, TypeError):
            valid_calendar = False
        if not valid_calendar:
            issue("INVALID_CALENDAR", None, "independent calendar must contain sorted unique ISO dates")
        elif dates and dates != [d for d in calendar if "2026-10-08" <= d <= dates[-1]]:
            issue("TRADING_CALENDAR_DIFF", None, "ledger omitted or invented a trading session")
    try:
        close_to(series.get("total_fees"), total_fees, "SERIES_FEE_DIFF", None)
        close_to(
            series.get("cumulative_return"),
            previous_close / initial - 1,
            "SERIES_RETURN_DIFF",
            None,
            tol=return_tolerance,
        )
    except ValueError as exc:
        issue("INVALID_TOTAL", None, str(exc))
    return {
        "status": "FAIL" if issues else "PARTIAL_NOT_CERTIFIED",
        "ledger_status": "FAIL" if issues else "PASS",
        "account_scope": "WEBSITE_OCT08_1M",
        "market": market,
        "audited_reports": len(rows),
        "issues": issues,
        "rows": rows,
        "total_fees": str(total_fees),
        "warnings": [
            {
                "code": "MARKET_EXECUTION_NOT_CERTIFIED",
                "detail": "Ledger reconciliation cannot certify historical ST, official ex-right reference prices or auction fills.",
            }
        ],
        "independent_quotes_provided": quotes is not None,
        "independent_calendar_provided": calendar is not None,
    }
