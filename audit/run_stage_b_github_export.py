"""Run original SHA-gated Stage-B report audit against the new GitHub export.

Does not import Qlib, production metrics, or execute models.
Missing historical provider calendar prevents formal metric_formula PASS.
"""
import importlib.util
import json
import os
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPORT = Path(os.environ.get("AUDIT_EXPORT_ROOT", str(ROOT / "results/csi1000_stage_b/audit_reports")))
AUDIT = ROOT / "audit/independent_stage_b_metrics.py"
TARGET = ROOT / "audit/evidence/csi1000_stage_b_frozen_manifest_20261009.json"
OUTPUT = ROOT / "audit-output"

spec = importlib.util.spec_from_file_location("independent_stage_b_metrics", AUDIT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def run():
    evidence = json.loads(TARGET.read_text())
    exports = json.loads((EXPORT / "manifest.json").read_text())
    if exports["source_snapshot"] != evidence["snapshot_token"]:
        raise ValueError("wrong export snapshot")
    original = {p["expected_report"]["path"]: p for p in evidence["phases"]}
    found = {f["volume_source_path"]: f for f in exports["files"]}
    if len(found) != 10 or set(found) != set(original):
        raise ValueError("missing/duplicate extra source paths compared with frozen JSON")
    OUTPUT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        for source, entry in found.items():
            origin = EXPORT / entry["file"]
            if not origin.is_file():
                raise FileNotFoundError(origin)
            if origin.stat().st_size != entry["size_bytes"]:
                raise ValueError(f"export file size differs: {origin}")
            if entry["expected_sha256"] != original[source]["expected_report"]["sha256"]:
                raise ValueError(f"export-manifest SHA disagrees with frozen result: {source}")
            rel = Path(source).relative_to(Path("/vol/csi1000_stage_b") / evidence["snapshot_token"])
            target = base / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origin, target)
        rows = [mod.audit_one(p, evidence["snapshot_token"], base, None, OUTPUT) for p in evidence["phases"]]

    # Compare date index across all ten phase results; this is a useful internal
    # check, but cannot substitute for the authentic provider trading calendar.
    import pandas as pd
    calendars = {}
    for p in evidence["phases"]:
        e = found[p["expected_report"]["path"]]
        f = pd.read_parquet(EXPORT / e["file"])
        calendars[f"{p['cohort']}_phase{p['phase']:02d}"] = [str(d.date()) for d in pd.DatetimeIndex(f.index)]
    reference = calendars["winner_phase00"]
    same_dates = all(calendar == reference for calendar in calendars.values())
    invalid_weekday = [day for day in reference if pd.Timestamp(day).weekday() >= 5]
    summary = {
        "checked_main_sha": "5418956d9858663613d9e9eaedf614d97da019c1",
        "frozen_result_commit": evidence["frozen_commit"],
        "sha256_match_count": sum(x.get("actual_sha256") == x["expected_sha256"] for x in rows),
        "files_checked": len(rows),
        "all_phase_indexes_identical": same_dates,
        "weekend_dates": invalid_weekday,
        "claimed_start": reference[0] if reference else None,
        "claimed_end": reference[-1] if reference else None,
        "unique_days": len(set(reference)),
        "saved_metric_discrepancies": [
            {"cohort": r["cohort"], "phase": r["phase"], "keys": [
                k for k, v in (r.get("comparison") or {}).items() if not v["within_tolerance"]]}
            for r in rows if any(not v["within_tolerance"] for v in (r.get("comparison") or {}).values())
        ],
        "results": rows,
        "provider_calendar_verified": False,
        "execution_signal_orders_prices_verified": False,
    }
    (OUTPUT / "github_export_audit.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    print("AUDIT SUMMARY " + json.dumps({k: v for k, v in summary.items() if k != "results"}, sort_keys=True))
    for r in rows:
        rec = r.get("recomputed") or {}
        print("PHASE " + json.dumps({
            "cohort": r["cohort"], "phase": r["phase"], "sha_verified": r.get("actual_sha256")==r["expected_sha256"],
            "metric_formula": r["metric_formula"], "daily_account": r["daily_account"], "reason": r["reason"],
            "days": rec.get("n_days"), "sharpe": rec.get("sharpe"), "IR": rec.get("information_ratio"),
            "CAGR": rec.get("strategy_cagr"), "vol": rec.get("annual_volatility"), "max_drawdown": rec.get("strategy_max_drawdown"),
            "cost": rec.get("total_cost_sum"), "max_account_diff": rec.get("account_return_max_error"),
            "max_fee_diff": (r.get("diagnostics") or {}).get("max_cost_rate_delta"),
        }, sort_keys=True))
    if summary["sha256_match_count"] != 10 or not same_dates or invalid_weekday:
        raise SystemExit("FAIL: content hashes or common calendar")
    if summary["saved_metric_discrepancies"] or any(r["daily_account"] == "FAIL" for r in rows):
        raise SystemExit("FAIL: frozen metric or account discrepancies")
    print("PROVISIONAL_METRIC_RECONCILIATION_OK — formal PASS blocked by missing provider calendar and execution evidence")


if __name__ == "__main__":
    run()
