"""Fail-closed validation of a generated provider overlay."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable, Mapping

from data_ingest.calendar import read_calendar
from data_ingest.model import CrossCheck, GateReport, Membership
from data_ingest.overlay import active_members, read_bin_value, read_ranges


def _finite(value: float) -> bool:
    return value is not None and math.isfinite(float(value))


def _close_enough(left: float, right: float, tolerance: float = 1e-6) -> bool:
    scale = max(abs(left), abs(right), 1.0)
    return abs(left - right) / scale <= tolerance


def run_gate(
    base_dir: str | Path,
    overlay_dir: str | Path,
    *,
    base_end: str,
    asof: str,
    dates: list[str],
    memberships: Mapping[str, Membership],
    rows: Mapping[str, Mapping[str, Mapping[str, float]]],
    crosschecks: Iterable[CrossCheck] = (),
    suspensions: Iterable[str] = (),
    corporate_actions: Iterable[Mapping[str, float | str]] = (),
    require_crosscheck: bool = True,
    min_crosschecks: int = 1,
    require_qlib: bool = False,
) -> GateReport:
    report = GateReport()
    base = Path(base_dir)
    overlay = Path(overlay_dir)
    baseline = read_calendar(base)
    extended = read_calendar(overlay)
    report.require(
        extended[: len(baseline)] == baseline,
        "calendar.base_prefix",
        f"base={len(baseline)} overlay={len(extended)}",
    )
    report.require(
        extended[len(baseline) :] == list(dates) and extended[-1] == asof,
        "calendar.appended_dates",
        f"expected={dates} got={extended[len(baseline):]}",
    )

    for market, membership in memberships.items():
        path = overlay / "instruments" / f"{market}.txt"
        if not path.is_file():
            report.fail("membership.file", f"{market}: {path} missing")
            continue
        active = active_members(read_ranges(path), asof)
        expected = set(membership.members)
        report.require(
            membership.asof == asof,
            f"membership.date.{market}",
            f"file={membership.asof} asof={asof}",
        )
        report.require(
            active == expected,
            f"membership.members.{market}",
            f"active={len(active)} expected={len(expected)} "
            f"missing={sorted(expected - active)[:5]} extra={sorted(active - expected)[:5]}",
        )

    suspension_set = {str(item).upper() for item in suspensions}
    for market, membership in memberships.items():
        missing = []
        for symbol in membership.members:
            close = read_bin_value(overlay, symbol, "close", asof)
            if not _finite(close) and symbol not in suspension_set:
                missing.append(symbol)
        report.require(
            not missing,
            f"coverage.{market}",
            f"{len(membership.members) - len(missing)}/{len(membership.members)} priced; "
            f"unexplained={missing[:10]}",
        )

    bad_rows = []
    nonempty = 0
    for symbol, per_date in rows.items():
        for date in dates:
            row = per_date.get(date, {})
            values = [
                row.get(field, float("nan")) for field in ("open", "high", "low", "close", "volume", "amount", "factor")
            ]
            if all(not _finite(value) for value in values):
                continue
            nonempty += 1
            if not all(_finite(value) for value in values):
                bad_rows.append(f"{symbol}/{date}: non-finite OHLCV/factor")
                continue
            open_, high, low, close, volume, amount, factor = values
            if close <= 0 or factor <= 0 or volume < 0 or amount < 0:
                bad_rows.append(f"{symbol}/{date}: non-positive close/factor or negative volume/amount")
            elif high + 1e-9 < max(open_, close) or low - 1e-9 > min(open_, close):
                bad_rows.append(f"{symbol}/{date}: high/low inconsistent with open/close")
    report.require(not bad_rows, "rows.ohlc", f"checked={nonempty} bad={bad_rows[:10]}")

    sample = sorted(rows)[:30]
    bad_base = []
    for symbol in sample:
        for field in ("close", "factor", "volume"):
            left = read_bin_value(base, symbol, field, base_end)
            right = read_bin_value(overlay, symbol, field, base_end)
            if _finite(left) and _finite(right) and not _close_enough(left, right, 1e-7):
                bad_base.append(f"{symbol}/{field}: {left} != {right}")
            elif _finite(left) != _finite(right):
                bad_base.append(f"{symbol}/{field}: finite mismatch")
    report.require(not bad_base, "base_integrity", f"sample={len(sample)} bad={bad_base[:10]}")

    checks = list(crosschecks)
    if len(checks) < min_crosschecks and require_crosscheck:
        report.fail("crosscheck", f"only {len(checks)} comparisons; required {min_crosschecks}")
    else:
        failed = [item for item in checks if item.status != "PASS"]
        report.require(
            not failed,
            "crosscheck",
            f"checked={len(checks)} failed={[(x.symbol, x.date) for x in failed[:10]]}",
        )

    corp = list(corporate_actions)
    bad_corp = []
    for item in corp:
        expected = float(item["expected_factor"])
        actual = float(item["actual_factor"])
        if not _close_enough(expected, actual, 1e-5):
            bad_corp.append(f"{item.get('symbol')}/{item.get('date')}: {expected} != {actual}")
    report.require(not bad_corp, "corporate_actions", f"checked={len(corp)} bad={bad_corp[:10]}")

    if require_qlib:
        try:
            import qlib
            from qlib.data import D
            from qlib.workflow import R

            try:
                R.end_exp()
            except Exception:
                pass
            qlib.init(provider_uri=str(overlay), region="cn")
            calendar_tail = [str(x)[:10] for x in D.calendar(start_time=asof, end_time=asof)]
            symbols = sorted({symbol for market in memberships.values() for symbol in market.members})[:20]
            frame = D.features(symbols, ["$close"], start_time=asof, end_time=asof)
            report.require(
                calendar_tail == [asof] and len(frame) > 0,
                "qlib.smoke",
                f"calendar={calendar_tail} rows={len(frame)}",
            )
        except Exception as exc:  # noqa: BLE001 - qlib boundary
            report.fail("qlib.smoke", f"{type(exc).__name__}: {exc}")

    return report
