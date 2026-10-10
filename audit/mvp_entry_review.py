"""Read-only CSI1000 MVP artifact-contract review.

Validates identity and T-close -> T+1 intent on an existing paper artifact,
optionally against a *supplied authoritative trading calendar*. Does not
verify deployed cron, today's provider, label maturity, current ST state,
execution, or research returns. Never promotes any model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

# Direct-file invocation from the repository's audit/ subdirectory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from csi1000_production_config import (  # noqa: E402
    CANONICAL_PAPER_LINEAGE, CANONICAL_PROFILE, profile_manifest,
)
from signal_publication_gate import _csi1000_canonical_publication_decision  # noqa: E402


def evaluate_paper_artifact(paper, calendar=None):
    errors = []
    manifest = profile_manifest(CANONICAL_PROFILE)
    lineage = paper.get("production_lineage")
    if not isinstance(lineage, dict):
        lineage = {}
    expected = {
        "market": "csi1000",
        "paper_lineage": CANONICAL_PAPER_LINEAGE,
    }
    for key, value in expected.items():
        if paper.get(key) != value:
            errors.append(f"{key}_MISMATCH")
    for key in ("candidate_id", "profile", "model_config_sha256",
                "stage_b_snapshot_token", "contract_version"):
        if lineage.get(key) != manifest.get(key):
            errors.append(f"production_lineage_{key}_MISMATCH")

    data_date = paper.get("signal_data_date")
    use_date = paper.get("usage_date")
    fit_asof = paper.get("model_fit_asof")
    try:
        for value in (data_date, use_date, fit_asof):
            if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
                raise ValueError("invalid date")
        valid_dates = True
    except ValueError:
        valid_dates = False
    if not valid_dates:
        errors.append("INVALID_OR_MISSING_SIGNAL_FIT_USAGE_DATE")
    elif not (fit_asof <= data_date < use_date):
        errors.append("FUTURE_OR_OUT_OF_ORDER_MODEL_SIGNAL_DATE")

    pending = paper.get("pending_orders")
    if not isinstance(pending, dict):
        errors.append("PENDING_ORDERS_MISSING")
    else:
        if pending.get("signal_date") != data_date or pending.get("execution_date") != use_date:
            errors.append("PENDING_ORDER_DATES_MISMATCH")
        buys, sells = pending.get("buy"), pending.get("sell")
        if not isinstance(buys, list) or not isinstance(sells, list):
            errors.append("PENDING_ORDER_LISTS_MISSING")
        elif (not all(isinstance(x, str) and x for x in buys + sells)
              or len(buys) > 20 or len(set(buys)) != len(buys)
              or len(set(sells)) != len(sells) or set(buys) & set(sells)):
            errors.append("PENDING_ORDER_SET_INVALID")
    if calendar is not None:
        # The caller must document/verify the calendar source. Do NOT infer a
        # trading session from consecutive calendar dates or a chart series.
        try:
            valid_calendar = (isinstance(calendar, list)
                              and calendar == sorted(set(calendar))
                              and all(isinstance(x, str) and
                                      date.fromisoformat(x).isoformat() == x for x in calendar))
        except (TypeError, ValueError):
            valid_calendar = False
        if not valid_calendar:
            errors.append("INVALID_SUPPLIED_TRADING_CALENDAR")
        elif isinstance(data_date, str) and isinstance(use_date, str):
            action, detail = _csi1000_canonical_publication_decision(
                calendar, data_date, use_date)
            if action != "publish":
                errors.append("PUBLICATION_NOT_APPROVED: " + detail)
    return {
        "static_artifact_contract": "FAIL" if errors else "PASS_STATIC_ONLY",
        "errors": errors,
        "active_candidate_id": manifest["candidate_id"],
        "active_profile": CANONICAL_PROFILE,
        "artifact_signal_data_date": data_date,
        "artifact_usage_date": use_date,
        "supplied_calendar_gate": ("FAIL" if errors and calendar is not None
                                   else "PASS_SUPPLIED_CALENDAR_ONLY" if calendar is not None
                                   else "NOT_CHECKED"),
        "historical_accounting": "BLOCKED_UNLESS_SEPARATE_REAL_PROVIDER_AUDIT_PASS",
        "historical_strategy_selection": "CONSUMED_TAIL_DIAGNOSTIC_ONLY",
        "deployed_runtime": "NOT_VERIFIED",
        "training_label_maturity": "NOT_VERIFIED_FROM_THIS_ARTIFACT",
        "current_st_suspension_status": "NOT_VERIFIED",
        "real_fill_execution": "NOT_CERTIFIED",
        "mvp_activation_approved": False,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--paper-artifact", required=True, type=Path)
    p.add_argument("--calendar-file", type=Path, help="optional VERIFIED Qlib day.txt")
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    root = Path(__file__).resolve().parents[1]
    dest = args.output.resolve()
    if dest.exists() or dest.is_relative_to(root) or not dest.parent.is_dir():
        raise ValueError("output must be a new file outside repository")
    paper = json.loads(args.paper_artifact.read_text())
    calendar = None
    if args.calendar_file:
        calendar = [x.strip()[:10] for x in args.calendar_file.read_text().splitlines() if x.strip()]
    result = evaluate_paper_artifact(paper, calendar=calendar)
    result["source_paper_sha256"] = hashlib.sha256(args.paper_artifact.read_bytes()).hexdigest()
    result["calendar_sha256"] = (hashlib.sha256(args.calendar_file.read_bytes()).hexdigest()
                                 if args.calendar_file else None)
    dest.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"output": str(dest), "contract": result["static_artifact_contract"]}))
    if result["static_artifact_contract"] != "PASS_STATIC_ONLY":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
