"""Pure, fail-closed evidence and accounting gates for CSI1000 Stage-B F5.

No Qlib imports, training, external I/O beyond hashing the provided input.
Keep these gates separate from the ledger reconstruction so tests can inject
tampered orders, dates, fee totals, and hashes without rerunning the backtest.
"""
from __future__ import annotations

import hashlib
import math
import re
from decimal import Decimal
from pathlib import Path

_SHA256 = re.compile(r"[0-9a-f]{64}", re.ASCII)
CENT = Decimal("0.01")
RATE = Decimal("0.0000000001")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_input(path: Path, expected_sha256: str, label: str) -> dict:
    """Pin to a pre-recorded SHA, not a hash computed by the same audit run."""
    if not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256):
        raise ValueError(f"{label}: expected SHA256 must be an external 64-char lowercase hex pin")
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"{label}: input file missing")
    actual = sha256_file(path)
    if actual != expected_sha256:
        raise ValueError(f"{label}: SHA256 mismatch: expected {expected_sha256}, actual {actual}")
    return {
        "name": path.name,  # avoid leaking private absolute experiment paths
        "expected_sha256": expected_sha256,
        "actual_sha256": actual,
        "match": True,
        "size_bytes": path.stat().st_size,
    }


def check_complete_sell(held: float, requested: float, filled: float, stock: str) -> None:
    """TopK/Drop2 always requests and fills the full pre-sell position."""
    if not all(math.isfinite(float(x)) for x in (held, requested, filled)):
        raise ValueError(f"{stock}: nonfinite sell/holding quantity")
    if held <= 0 or requested <= 0 or filled <= 0:
        raise ValueError(f"{stock}: nonpositive sell/holding quantity")
    tolerance = max(1e-6, abs(float(held)) * 1e-10)
    if abs(float(requested) - float(held)) > tolerance:
        raise ValueError(f"{stock}: sell requested quantity differs from pre-trade position")
    if abs(float(filled) - float(requested)) > tolerance:
        raise ValueError(f"{stock}: sell fill differs from full requested position")


def check_full_buy(requested: float, filled: float, stock: str) -> None:
    if not math.isfinite(float(requested)) or not math.isfinite(float(filled)):
        raise ValueError(f"{stock}: nonfinite buy quantity")
    if requested <= 0 or filled <= 0:
        raise ValueError(f"{stock}: nonpositive buy quantity")
    tolerance = max(1e-6, abs(float(requested)) * 1e-10)
    if abs(float(requested) - float(filled)) > tolerance:
        raise ValueError(f"{stock}: partial buy fill")


def audit_failures(*, calendar_exact: bool, decisions_n: int, report_n: int,
                   decision_mismatches: int, tradability_violations: int,
                   lot_violations: int, factor_mismatches: int,
                   buy_amount_mismatches: int, sell_amount_mismatches: int,
                   negative_cash_events: int, max_account_diff: Decimal,
                   max_return_diff: float, max_fee_diff: Decimal,
                   max_turnover_diff: Decimal, max_fee_rate_diff: Decimal,
                   max_turnover_rate_diff: Decimal) -> list[str]:
    """One auditable gate; any nonzero mismatch is FAIL, never silent PASS."""
    issues = []
    if not calendar_exact or decisions_n != report_n or report_n != 424:
        issues.append("calendar_or_daily_coverage")
    for name, count in (
        ("decision_mismatches", decision_mismatches),
        ("tradability_violations", tradability_violations),
        ("lot_violations", lot_violations),
        ("factor_mismatches", factor_mismatches),
        ("buy_amount_mismatches", buy_amount_mismatches),
        ("sell_amount_mismatches", sell_amount_mismatches),
        ("negative_cash_events", negative_cash_events),
    ):
        if count != 0:
            issues.append(name)
    for name, value, tolerance in (
        ("account_cny", max_account_diff, CENT),
        ("fees_cny", max_fee_diff, CENT),
        ("turnover_cny", max_turnover_diff, CENT),
        ("returns", Decimal(str(max_return_diff)), RATE),
        ("fee_rate", max_fee_rate_diff, RATE),
        ("turnover_rate", max_turnover_rate_diff, RATE),
    ):
        try:
            numeric = Decimal(str(value))
            if not numeric.is_finite() or numeric > tolerance or numeric < 0:
                issues.append(name)
        except (ValueError, ArithmeticError):
            issues.append(name)
    return issues


def require_pass(**kwargs) -> None:
    problems = audit_failures(**kwargs)
    if problems:
        raise ValueError("F5 independent audit FAIL: " + ", ".join(problems))
