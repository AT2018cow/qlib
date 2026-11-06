"""Assess a concrete limited-use package without changing research promotion.

This is a review-preparation command. It never grants live trading approval,
deploys an app, modifies an account, or sends an order.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quant_bootstrap import ensure_quant_import_paths

ensure_quant_import_paths()
import artifact_io as json
from csi1000_production_config import CANONICAL_PROFILES_BY_MARKET

REQUIRED_CHECKS = (
    "corrected_replay",
    "container_runtime",
    "account_reconciliation",
    "order_legality",
    "coordinate_consistency",
    "dated_preopen_st",
    "current_quotes",
    "duplicate_run",
    "failure_recovery",
)


def assess_package(package, *, evidence_root):
    reasons = []
    if package.get("schema_version") != 1:
        reasons.append("UNSUPPORTED_SCHEMA")
    market = package.get("market")
    if market not in CANONICAL_PROFILES_BY_MARKET:
        reasons.append("UNKNOWN_MARKET")
    if package.get("profile") != CANONICAL_PROFILES_BY_MARKET.get(market):
        reasons.append("PROFILE_CHANGE_REQUIRES_SEPARATE_REVIEW")
    for field in ("budget_cny",):
        value = package.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            reasons.append(f"MISSING_OR_INVALID_{field.upper()}")
    for field in ("max_single_weight", "max_drawdown_fraction"):
        enabled_key = "single_weight_limit_enabled" if field == "max_single_weight" else "drawdown_limit_enabled"
        if package.get(enabled_key) is False and package.get(field) is None:
            continue
        value = package.get(field)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0 < value <= 1
        ):
            reasons.append(f"MISSING_OR_INVALID_{field.upper()}")
    for field in ("max_single_order_cny", "max_daily_order_cny"):
        value = package.get(field)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0
        ):
            reasons.append(f"INVALID_{field.upper()}")
    if package.get("execution_mode") != "MANUAL_CONFIRMATION":
        reasons.append("AUTOMATIC_EXECUTION_NOT_REVIEWED")
    if package.get("shared_account") is not False:
        reasons.append("SHARED_ACCOUNT_REQUIRES_CROSS_MARKET_NETTING_REVIEW")
    calendar = package.get("verified_calendar", [])
    usage = package.get("usage_date")
    if not calendar or calendar != sorted(set(calendar)) or usage not in calendar:
        reasons.append("VERIFIED_USAGE_CALENDAR_REQUIRED")
    else:
        i = calendar.index(usage)
        if i == 0 or package.get("data_date") != calendar[i - 1]:
            reasons.append("DATA_MUST_BE_PREVIOUS_VERIFIED_SESSION")
    code_sha = package.get("source_manifest_sha256", "")
    if not isinstance(code_sha, str) or len(code_sha) != 64 or any(c not in "0123456789abcdef" for c in code_sha):
        reasons.append("SOURCE_MANIFEST_IDENTITY_REQUIRED")
    root = Path(evidence_root).resolve()
    checks = package.get("checks", {})
    for name in REQUIRED_CHECKS:
        check = checks.get(name, {})
        if not isinstance(check, dict) or check.get("status") != "PASS":
            reasons.append(f"CHECK_NOT_PASS:{name}")
            continue
        if check.get("market") != market or check.get("source_manifest_sha256") != code_sha:
            reasons.append(f"CHECK_IDENTITY_MISMATCH:{name}")
            continue
        if (
            name in ("dated_preopen_st", "current_quotes", "account_reconciliation", "coordinate_consistency")
            and check.get("usage_date") != usage
        ):
            reasons.append(f"CHECK_DATE_MISMATCH:{name}")
            continue
        rel = check.get("path")
        if not isinstance(rel, str) or not rel or Path(rel).is_absolute():
            reasons.append(f"RELATIVE_RECEIPT_PATH_REQUIRED:{name}")
            continue
        receipt = (root / rel).resolve()
        if root not in receipt.parents or not receipt.is_file():
            reasons.append(f"MISSING_OR_OUTSIDE_RECEIPT:{name}")
            continue
        if hashlib.sha256(receipt.read_bytes()).hexdigest() != check.get("sha256"):
            reasons.append(f"RECEIPT_HASH_MISMATCH:{name}")
    return {
        "schema_version": 1,
        "market": market,
        "profile": package.get("profile"),
        "usage_date": usage,
        "status": "BLOCKED" if reasons else "READY_FOR_LIMITED_REVIEW",
        "reasons": reasons,
        "live_trading_approved": False,
        "research_promotion_eligible": False,
        "external_receipts_require_independent_review": True,
        "scope": "CONCRETE_PACKAGE_REVIEW_PREPARATION_ONLY",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = assess_package(json.loads(args.package.read_text()), evidence_root=args.package.parent)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(result, ensure_ascii=False))
    return 2 if result["status"] == "BLOCKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
