"""Run independently recoverable signal and website publication stages."""

from __future__ import annotations

import json
import re
from datetime import date

SIGNAL_WORKER_TIMEOUT_SECONDS = 90 * 60
# CSI1000 full replay can exceed 60 minutes on a cold source identity.
WEBSITE_WORKER_TIMEOUT_SECONDS = 90 * 60
WEBSITE_STAGE_TIMEOUT_SECONDS = 4 * WEBSITE_WORKER_TIMEOUT_SECONDS + 30 * 60
MAIN_TIMEOUT_SECONDS = 3 * SIGNAL_WORKER_TIMEOUT_SECONDS + WEBSITE_STAGE_TIMEOUT_SECONDS + 30 * 60
BACKUP_TIMEOUT_SECONDS = 2 * MAIN_TIMEOUT_SECONDS + 30 * 60


def publication_context(files, usage_date):
    """Recover publication provenance from hash-verified frozen metadata."""
    publications = [
        json.loads(content)["publication"]
        for path, content in files.items()
        if "_mvp_metadata" in path and path.endswith(".json")
    ]
    if not publications:
        return {
            "publication_mode": "UNKNOWN_LEGACY",
            "usage_date": usage_date,
            "signal_data_date": None,
            "lag_sessions": None,
            "same_day_revision_policy": "EXPLICIT_VERSIONED_AMENDMENT_ONLY",
        }
    dates = {p.get("signal_data_date") for p in publications}
    modes = {
        (
            "OPERATOR_OVERRIDE"
            if p.get("status") == "PASS_OPERATOR_OVERRIDE"
            or p.get("publication_mode") == "OPERATOR_OVERRIDE"
            or "OPERATOR_OVERRIDE" in str(p.get("canonical_freshness_gate", ""))
            else "CANONICAL_T1"
        )
        for p in publications
    }
    if len(dates) != 1 or len(modes) != 1 or any(p.get("usage_date") != usage_date for p in publications):
        raise ValueError("frozen market publication provenance disagrees")
    for day in (usage_date, *dates):
        if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
            raise ValueError("frozen publication date missing/invalid")
    if next(iter(dates)) >= usage_date:
        raise ValueError("frozen publication cannot use same-day/future data")
    mode = modes.pop()
    lags = set()
    for p in publications:
        lag = p.get("lag_sessions")
        if lag is None:
            match = re.search(r"lag\s*=\s*(\d+)", str(p.get("canonical_freshness_gate", "")))
            lag = int(match.group(1)) if match else (1 if mode == "OPERATOR_OVERRIDE" else 0)
        if type(lag) is not int or lag != (1 if mode == "OPERATOR_OVERRIDE" else 0):
            raise ValueError("frozen publication lag/mode mismatch")
        lags.add(lag)
    return {
        "publication_mode": mode,
        "usage_date": usage_date,
        "signal_data_date": dates.pop(),
        "lag_sessions": lags.pop(),
        "same_day_revision_policy": "EXPLICIT_VERSIONED_AMENDMENT_ONLY",
    }


def existing_publication_result(record, journal, *, recovered=False):
    files, _ = journal.read_payloads(record)
    context = publication_context(files, record["signal_date"])
    if record.get("publication_context") is not None and record["publication_context"] != context:
        raise ValueError("journal provenance differs from immutable metadata")
    status = "RECOVERED" if recovered else "ALREADY_PUBLISHED"
    if context["publication_mode"] != "CANONICAL_T1":
        status += "_OPERATOR_OVERRIDE" if context["publication_mode"] == "OPERATOR_OVERRIDE" else "_UNVERIFIED_LEGACY"
    return {"status": status, "signal_date": record["signal_date"], **context}


class DailyPipelineError(RuntimeError):
    """Expose partial completion without hiding a failed daily stage."""

    def __init__(self, stages):
        self.stages = stages
        failed = ", ".join(
            name for name, result in stages.items() if result["status"] not in ("SUCCEEDED", "SKIPPED_NON_TRADING_DAY")
        )
        super().__init__(f"daily publication stages failed: {failed}")

    def __reduce__(self):
        return type(self), (self.stages,)


def run_daily_pipeline(signal_runner, website_runner):
    """Attempt both stages; their own journals make subsequent runs idempotent."""
    stages = {}
    first_error = None
    for name, runner in (("signals", signal_runner), ("website", website_runner)):
        try:
            result = runner()
            outcome = result.get("status") if isinstance(result, dict) else None
            incomplete = outcome == "SKIPPED_STALE_DATA" or any(
                marker in str(outcome) for marker in ("OPERATOR_OVERRIDE", "UNVERIFIED_LEGACY")
            )
            if not incomplete and outcome not in (
                "PUBLISHED",
                "ALREADY_PUBLISHED",
                "RECOVERED",
                "SKIPPED_NON_TRADING_DAY",
            ):
                raise ValueError(f"{name} returned no recognized successful publication receipt")
            status = (
                "INCOMPLETE_STALE_DATA"
                if incomplete
                else ("SKIPPED_NON_TRADING_DAY" if outcome == "SKIPPED_NON_TRADING_DAY" else "SUCCEEDED")
            )
            stages[name] = {"status": status, "result": result}
            if incomplete and first_error is None:
                first_error = RuntimeError(f"{name} has no fresh canonical publication: {outcome}")
            print(f"[daily] {name}: {status}", flush=True)
        except Exception as exc:
            first_error = first_error or exc
            stages[name] = {"status": "FAILED", "error_type": type(exc).__name__}
            print(f"[daily] {name}: FAILED ({type(exc).__name__})", flush=True)
    if first_error is not None:
        raise DailyPipelineError(stages) from first_error
    return {"status": "COMPLETED", "stages": stages}
