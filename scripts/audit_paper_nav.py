#!/usr/bin/env python3
"""Independent read-only forensic reconciliation of CSI1000 paper NAV.

No production portfolio/performance modules are imported. All monetary math
uses Decimal, not the production's float formulas. The public artifacts alone
cannot prove that the open/close quotes were correct: provide an independently
versioned quote CSV and a trading calendar for full certification.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

START = "2026-10-12"
LINEAGE = "stage_b_winner_canonical"
CENT_TOL = Decimal("0.035")
RET_TOL = Decimal("0.00000001")
FILE_PATTERN = re.compile(r"(\d{4}-\d{2}-\d{2})_paper_portfolio\.json$")


def number(value, name):
    if value is None or isinstance(value, bool):
        raise ValueError(f"{name}: missing or nonnumeric value")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name}: invalid number") from exc
    if not result.is_finite():
        raise ValueError(f"{name}: nonfinite number")
    return result


def valid_date(value):
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def reconcile(artifacts, *, forward=None, calendar=None, quotes=None):
    """Return a JSON-compatible ledger audit without changing its inputs.

    artifacts: public *_paper_portfolio.json dicts (usage date in artifact).
    forward: optional generated public JSON to independently compare.
    calendar: optional list of authoritative trading dates.
    quotes: optional dict keyed by (execution_date, instrument) with adjusted
            'open', 'close', 'factor', from an independent provider snapshot.
    """
    issues, warnings = [], []
    rows = []
    audited = []
    previous = None
    seen_execution = {}
    quote_count = 0

    def issue(code, day, detail):
        issues.append({"code": code, "date": day, "detail": detail})

    def warn(code, day, detail):
        warnings.append({"code": code, "date": day, "detail": detail})

    for artifact in artifacts:
        if not isinstance(artifact, dict):
            issue("MALFORMED_ARTIFACT", None, "artifact is not an object")
            continue
        if artifact.get("market") != "csi1000" or artifact.get("paper_lineage") != LINEAGE:
            continue
        day = artifact.get("usage_date")
        if not valid_date(day):
            issue("BAD_USAGE_DATE", day, "missing/invalid published usage_date")
            continue
        profile = artifact.get("production_lineage")
        if not isinstance(profile, dict) or profile.get("profile") != "stage_b_winner":
            issue("LINEAGE_MISMATCH", day, "winner paper requires Stage-B winner production profile")
            continue
        audited.append((day, artifact))

    audited.sort(key=lambda pair: pair[0])
    if len({day for day, _ in audited}) != len(audited):
        issue("DUPLICATE_USAGE_DATE", None, "more than one winner snapshot has the same usage date")

    for day, artifact in audited:
        try:
            cash = number(artifact["cash"], "cash")
            if cash < 0:
                raise ValueError("negative cash")
            raw_positions = artifact["positions"]
            if not isinstance(raw_positions, dict):
                raise ValueError("positions is not an object")
            positions = {}
            for sym, pos in raw_positions.items():
                if not isinstance(pos, dict):
                    raise ValueError("position must be an object")
                shares = number(pos["shares"], "shares")
                mark = number(pos["last_price"], "last_price")
                if shares <= 0 or mark <= 0:
                    raise ValueError("position shares/mark must be positive")
                positions[sym] = {"shares": shares, "last_price": mark}
        except (KeyError, TypeError, ValueError) as exc:
            issue("INVALID_SNAPSHOT", day, str(exc))
            previous = None
            continue

        report = artifact.get("execution_report")
        if report is None:
            if previous:
                if cash != previous["cash"] or {
                    k: v["shares"] for k, v in positions.items()
                } != {k: v["shares"] for k, v in previous["positions"].items()}:
                    issue("UNREPORTED_POSITION_CHANGE", day, "cash/positions changed without an execution report")
            previous = {"date": day, "cash": cash, "positions": positions}
            continue
        if not isinstance(report, dict):
            issue("BAD_EXECUTION_REPORT", day, "execution report is not an object")
            previous = None
            continue

        execution_day = report.get("date")
        signal_day = report.get("signal_date")
        if signal_day is not None and (not valid_date(signal_day) or
                                      (valid_date(execution_day) and signal_day >= execution_day)):
            issue("BAD_SIGNAL_DATE", day, f"report signal day {signal_day!r} must precede execution")
        if not valid_date(execution_day) or execution_day > day or execution_day < START:
            issue("BAD_EXECUTION_DATE", day, f"invalid or out-of-range execution day: {execution_day!r}")
            previous = {"date": day, "cash": cash, "positions": positions}
            continue
        try:
            pre = number(report["portfolio_value_pre_trade_open"], "portfolio_value_pre_trade_open")
            close = number(report["portfolio_value_close"], "portfolio_value_close")
            reported_cash = number(report["cash"], "report cash")
            reported_cost = number(report["cost"], "report cost")
            if min(pre, close) <= 0:
                raise ValueError("nonpositive pre/close NAV")
            if not isinstance(report["executed_buy"], list) or not isinstance(report["executed_sell"], list):
                raise ValueError("executed orders must be arrays")
        except (KeyError, TypeError, ValueError) as exc:
            issue("BAD_ACCOUNTING_FIELDS", execution_day, str(exc))
            previous = {"date": day, "cash": cash, "positions": positions}
            continue

        signature = (pre, close, reported_cash, reported_cost, json.dumps(report, sort_keys=True))
        if execution_day in seen_execution:
            if seen_execution[execution_day] != signature:
                issue("CONFLICTING_EXECUTION_REPORT", execution_day, "conflicting repeated execution-day report")
            previous = {"date": day, "cash": cash, "positions": positions}
            continue
        seen_execution[execution_day] = signature

        if abs(cash - reported_cash) > CENT_TOL:
            issue("CASH_SNAPSHOT_DIFF", execution_day, f"artifact cash={cash}, report cash={reported_cash}")
        if report.get("n_positions") != len(positions):
            issue("POSITION_COUNT_DIFF", execution_day, "reported count differs from snapshot")
        if sorted(report.get("positions", [])) != sorted(positions):
            issue("POSITION_SYMBOL_DIFF", execution_day, "reported symbols differ from snapshot")

        expected_close = cash + sum(p["shares"] * p["last_price"] for p in positions.values())
        if abs(expected_close - close) > CENT_TOL:
            issue("CLOSE_NAV_DIFF", execution_day, f"ledger close={expected_close}, report close={close}")
        buys, sells, fees = [], [], Decimal(0)
        for action, rate in (("executed_sell", Decimal("0.0015")), ("executed_buy", Decimal("0.0005"))):
            for fill in report[action]:
                try:
                    sym = str(fill["instrument"])
                    shares = number(fill["shares"], "fill shares")
                    px = number(fill["price"], "fill price")
                    fee = number(fill["cost"], "fill fee")
                    if shares <= 0 or px <= 0 or fee < 0:
                        raise ValueError("invalid fill")
                    fee_expected = max(shares * px * rate, Decimal("5"))
                    if abs(fee - fee_expected) > CENT_TOL:
                        issue("FEE_DIFF", execution_day, f"{sym} {action}: fee={fee}, expected={fee_expected}")
                    fees += fee
                    (sells if action == "executed_sell" else buys).append((sym, shares, px, fee))
                    if quotes is not None:
                        q = quotes.get((execution_day, sym))
                        if q is None or q.get("open") in (None, ""):
                            issue("MISSING_INDEPENDENT_OPEN", execution_day, sym)
                        else:
                            if abs(number(q["open"], "quote open") - px) > Decimal("0.0001"):
                                issue("OPEN_PRICE_DIFF", execution_day, f"{sym}: fill={px} quote={q['open']}")
                        if action == "executed_buy":
                            if not q or q.get("factor") in (None, ""):
                                issue("MISSING_INDEPENDENT_FACTOR", execution_day, sym)
                            else:
                                factor = number(q["factor"], "quote factor")
                                if factor <= 0:
                                    issue("BAD_FACTOR", execution_day, sym)
                                elif (shares * factor / Decimal(100)) % 1 != 0:
                                    issue("FACTOR_LOT_DIVERGENCE", execution_day,
                                          f"{sym}: adjusted shares={shares}, factor={factor}; "
                                          "not a multiple of 100 original shares")
                except (KeyError, TypeError, ValueError) as exc:
                    issue("INVALID_FILL", execution_day, str(exc))
        if abs(fees - reported_cost) > CENT_TOL:
            issue("TOTAL_FEE_DIFF", execution_day, f"fills fees={fees}, report cost={reported_cost}")

        if previous is None:
            warn("MISSING_OPENING_SNAPSHOT", execution_day,
                 "no prior winner state snapshot; cannot independently replay opening holdings/cash")
        else:
            expected_cash = previous["cash"] + sum(n * px - fee for _, n, px, fee in sells)
            expected_cash -= sum(n * px + fee for _, n, px, fee in buys)
            if abs(expected_cash - cash) > CENT_TOL:
                issue("CASH_LEDGER_DIFF", execution_day, f"replayed cash={expected_cash}, artifact cash={cash}")
            share_balance = {sym: p["shares"] for sym, p in previous["positions"].items()}
            for sym, n, _, _ in sells:
                if share_balance.get(sym, Decimal(0)) < n:
                    issue("OVERSOLD_POSITION", execution_day, f"{sym}: sold {n}, previously {share_balance.get(sym, 0)}")
                share_balance[sym] = share_balance.get(sym, Decimal(0)) - n
            for sym, n, _, _ in buys:
                share_balance[sym] = share_balance.get(sym, Decimal(0)) + n
            share_balance = {sym: n for sym, n in share_balance.items() if n}
            if share_balance != {sym: p["shares"] for sym, p in positions.items()}:
                issue("POSITION_LEDGER_DIFF", execution_day, "replayed shares differ from snapshot")

            if quotes is not None:
                expected_pre = previous["cash"]
                for sym, pos in previous["positions"].items():
                    q = quotes.get((execution_day, sym))
                    if not q or q.get("open") in (None, ""):
                        issue("MISSING_INDEPENDENT_OPEN", execution_day, sym)
                    else:
                        expected_pre += pos["shares"] * number(q["open"], "quote open")
                if abs(expected_pre - pre) > CENT_TOL and not any(
                    x["code"] == "MISSING_INDEPENDENT_OPEN" and x["date"] == execution_day for x in issues
                ):
                    issue("PRETRADE_NAV_DIFF", execution_day, f"quote NAV={expected_pre}, report pre={pre}")

        if quotes is not None:
            expected_quote_close = cash
            for sym, pos in positions.items():
                q = quotes.get((execution_day, sym))
                if not q or q.get("close") in (None, ""):
                    issue("MISSING_INDEPENDENT_CLOSE", execution_day, sym)
                else:
                    if q.get("factor") in (None, ""):
                        issue("MISSING_INDEPENDENT_FACTOR", execution_day, sym)
                    else:
                        factor = number(q["factor"], "quote factor")
                        if factor <= 0:
                            issue("BAD_FACTOR", execution_day, sym)
                    actual_mark = number(q["close"], "quote close")
                    expected_quote_close += pos["shares"] * actual_mark
                    quote_count += 1
                    if abs(actual_mark - pos["last_price"]) > Decimal("0.0001"):
                        issue("STALE_OR_DIFFERENT_CLOSE", execution_day,
                              f"{sym}: snapshot mark={pos['last_price']}, independent close={actual_mark}")
            if not any(x["code"] == "MISSING_INDEPENDENT_CLOSE" and x["date"] == execution_day for x in issues):
                if abs(expected_quote_close - close) > CENT_TOL:
                    issue("QUOTE_NAV_DIFF", execution_day, f"independent close NAV={expected_quote_close}, report NAV={close}")

        rows.append({"date": execution_day, "baseline_open": str(pre),
                     "reported_close": str(close), "ledger_close": str(expected_close),
                     "fee": str(fees), "usage_date": day})
        previous = {"date": day, "cash": cash, "positions": positions}

    rows.sort(key=lambda row: row["date"])
    if rows and rows[0]["date"] != START:
        issue("MISSING_INCEPTION_REPORT", START, f"first report date {rows[0]['date']}, expected {START}")
    if calendar is not None:
        if len(calendar) != len(set(calendar)) or any(not valid_date(day) for day in calendar):
            issue("INVALID_CALENDAR", None, "calendar dates must be valid and unique")
        elif rows:
            expected = {day for day in calendar if START <= day <= rows[-1]["date"]}
            actual = {row["date"] for row in rows}
            for day in sorted(expected - actual):
                issue("MISSING_TRADING_DAY", day, "no execution report for an expected session")
            for day in sorted(actual - set(calendar)):
                issue("OFF_CALENDAR_REPORT", day, "execution not in authoritative calendar")
    elif rows:
        warn("TRADING_CALENDAR_NOT_PROVIDED", None, "cannot certify missing trading sessions")

    if forward is not None:
        if not isinstance(forward, dict):
            issue("INVALID_FORWARD_JSON", None, "forward JSON is not an object")
        elif not rows:
            if not (forward.get("status") == "awaiting_first_valuation"
                    and forward.get("start_date") == START
                    and forward.get("points") == []
                    and forward.get("cumulative_return") is None
                    and forward.get("latest_date") is None):
                issue("FORWARD_BEFORE_INCEPTION", START, "expected an empty awaiting series")
        else:
            points = forward.get("points")
            if (forward.get("status") != "active" or forward.get("start_date") != START
                    or not isinstance(points, list) or len(points) != len(rows)):
                issue("FORWARD_SCHEMA_DIFF", None, "status/start date/point count differs from paper ledger")
            else:
                baseline, prev_close = number(rows[0]["baseline_open"], "baseline"), None
                for row, point in zip(rows, points):
                    close = number(row["reported_close"], "report close")
                    nav = close / baseline
                    daily = close / (prev_close if prev_close is not None else baseline) - 1
                    expected = {"nav": nav, "daily_return": daily, "cumulative_return": nav - 1}
                    if point.get("date") != row["date"]:
                        issue("FORWARD_DATE_DIFF", row["date"], f"point date={point.get('date')}")
                    for field, value in expected.items():
                        try:
                            actual = number(point.get(field), field)
                            if abs(actual - value) > RET_TOL:
                                issue("FORWARD_VALUE_DIFF", row["date"], f"{field}: reported={actual}, independent={value}")
                        except ValueError as exc:
                            issue("FORWARD_INVALID_VALUE", row["date"], str(exc))
                    prev_close = close
                try:
                    if (forward.get("latest_date") != rows[-1]["date"] or
                        abs(number(forward.get("cumulative_return"), "total return") -
                            (number(rows[-1]["reported_close"], "close") / baseline - 1)) > RET_TOL):
                        issue("FORWARD_TOTAL_DIFF", None, "latest date or final cumulative return differs")
                except ValueError:
                    issue("FORWARD_TOTAL_DIFF", None, "final return is not numeric")

    if not rows:
        status = "FAIL" if issues else "NOT_STARTED"
    elif issues:
        status = "FAIL"
    elif quotes is None or calendar is None or forward is None or any(
        w["code"] == "MISSING_OPENING_SNAPSHOT" for w in warnings
    ):
        status = "PARTIAL_NOT_CERTIFIED"
    else:
        status = "PASS"
    if rows and forward is None:
        warn("PUBLIC_FORWARD_NOT_PROVIDED", None, "cannot certify that webpage return JSON equals the account ledger")
    if rows and quotes is None:
        warn("PRICE_PROVENANCE_NOT_VERIFIED", None, "no independent open/close/factor quotes; original market marks unverified")
    return {"status": status, "start_date": START, "winner_artifacts": len(audited),
            "audited_reports": len(rows), "quoted_closes": quote_count,
            "issues": issues, "warnings": warnings, "rows": rows}


def load_quote_csv(path):
    quotes = {}
    with path.open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            key = (row["date"], row["instrument"])
            if key in quotes:
                raise ValueError(f"duplicate quote {key}")
            quotes[key] = row
    return quotes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signals", type=Path, required=True)
    parser.add_argument("--forward", type=Path)
    parser.add_argument("--calendar", type=Path, help="authoritative dates, one YYYY-MM-DD per line")
    parser.add_argument("--quotes", type=Path, help="independent adjusted open/close/factor CSV")
    parser.add_argument("--output", type=Path, help="optional JSON audit report output")
    args = parser.parse_args()
    artifacts = []
    for path in sorted(args.signals.glob("*_paper_portfolio.json")):
        if not FILE_PATTERN.fullmatch(path.name):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and payload.get("usage_date") != FILE_PATTERN.fullmatch(path.name).group(1):
                raise ValueError("filename and usage date mismatch")
            artifacts.append(payload)
        except (OSError, ValueError) as exc:
            print(f"Invalid artifact {path.name}: {exc}")
            raise SystemExit(2) from exc
    forward = json.loads(args.forward.read_text()) if args.forward else None
    calendar = [x.strip() for x in args.calendar.read_text().splitlines() if x.strip()] if args.calendar else None
    quotes = load_quote_csv(args.quotes) if args.quotes else None
    outcome = reconcile(artifacts, forward=forward, calendar=calendar, quotes=quotes)
    formatted = json.dumps(outcome, ensure_ascii=False, indent=2)
    print(formatted)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(formatted + "\n", encoding="utf-8")
    if outcome["status"] == "FAIL":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
