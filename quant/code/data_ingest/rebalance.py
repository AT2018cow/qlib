"""Dates on which the production overlay requires same-session CSIndex evidence."""

from __future__ import annotations

from bisect import bisect_right
from datetime import date, timedelta
from typing import TYPE_CHECKING

from data_ingest.csindex import MARKETS

if TYPE_CHECKING:
    from collections.abc import Callable

    from data_ingest.store import ProviderSnapshot

# Additional officially announced effective sessions for any of the three markets.
# Enter the YYYY-MM-DD effective session (not the announcement date) and the
# official notice URL. Regular June/December rebalances are resolved below.
ANNOUNCED_EFFECTIVE_SESSIONS: dict[str, str] = {}
FROZEN_OVERLAY_END = "2026-11-06"


def rebalance_reason(session_date: str, previous_session: str | None) -> str | None:
    """Identify the first exchange session after the second Friday in June/December."""
    notice = ANNOUNCED_EFFECTIVE_SESSIONS.get(session_date)
    if notice is not None:
        return f"CSIndex notice {notice}"

    session = date.fromisoformat(session_date)
    if previous_session is None and session.month not in (6, 12):
        return None
    if session.month < 6:
        month_start = date(session.year - 1, 12, 1)
    else:
        month_start = session.replace(month=12 if session.month == 12 else 6, day=1)
    second_friday = month_start + timedelta(days=(4 - month_start.weekday()) % 7 + 7)
    if session > second_friday and (previous_session is None or date.fromisoformat(previous_session) <= second_friday):
        return "regular CSIndex schedule"
    return None


def _has_verified_lists(manifest: dict, appended: tuple[str, ...]) -> bool:
    policy = manifest.get("membership_policy")
    memberships = manifest.get("memberships")
    if not isinstance(policy, dict) or not isinstance(memberships, dict) or policy.get("official_asof") != appended[-1]:
        return False
    for market in MARKETS:
        evidence = memberships.get(market)
        if (
            not isinstance(evidence, dict)
            or evidence.get("asof") != appended[-1]
            or str(evidence.get("source_url", "")).startswith("base_pit:")
        ):
            return False
    if policy.get("mode") == "OFFICIAL":
        return len(appended) == 1
    if policy.get("mode") != "MULTI_DAY_EVIDENCE" or policy.get("dates") != list(appended):
        return False
    gate = manifest.get("gate")
    if not isinstance(gate, dict) or gate.get("status") != "PASS" or not isinstance(gate.get("checks"), list):
        return False
    required = {
        f"membership.{check}.{market}.{day}" for day in appended for market in MARKETS for check in ("date", "members")
    }
    checks = [item for item in gate["checks"] if isinstance(item, dict) and item.get("name") in required]
    return (
        len(checks) == len(required) and {item["name"] for item in checks if item.get("status") == "PASS"} == required
    )


def snapshot_rebalance_error(
    snapshot: ProviderSnapshot, load_parent: Callable[[str], ProviderSnapshot | None]
) -> str | None:
    """Check protected sessions in this append and its exact, immutable ancestry."""
    end = snapshot.calendar_end
    try:
        sessions = snapshot.read_sessions()
    except (OSError, RuntimeError, ValueError) as exc:
        return f"rebalance lineage calendar unavailable for {end}: {exc}"
    pending = {}
    for index in range(bisect_right(sessions, FROZEN_OVERLAY_END), len(sessions)):
        day = sessions[index]
        reason = rebalance_reason(day, sessions[index - 1] if index else None)
        if reason is not None:
            pending[day] = reason

    span_end = len(sessions)
    while pending:
        manifest = snapshot.manifest
        base = manifest.get("base") or {}
        target = manifest.get("target") or {}
        base_end = base.get("calendar_end")
        declared = target.get("dates")
        if base_end:
            start = bisect_right(sessions, base_end, hi=span_end)
            if not 0 < start < span_end or sessions[start - 1] != base_end:
                return f"rebalance lineage has an invalid base date for {snapshot.calendar_end}"
        else:
            start = span_end - (len(declared) if declared else 1)
        appended = sessions[start:span_end]
        if start < 0 or (declared is not None and declared != list(appended)):
            return f"rebalance lineage target dates differ from the calendar for {snapshot.calendar_end}"

        protected = [day for day in appended if day in pending]
        if protected and not _has_verified_lists(manifest, appended):
            day = protected[0]
            return (
                f"rebalance {day} ({pending[day]}) requires current official constituent evidence for all three markets"
            )
        for day in protected:
            del pending[day]
        if not pending:
            return None

        if not base_end:
            return f"rebalance lineage missing base for {snapshot.calendar_end}; cannot verify {min(pending)}"
        parent = load_parent(base_end)
        expected = str(base.get("archive_sha256") or base.get("digest") or base.get("manifest_sha256") or "")
        expected = expected.removeprefix("sha256:")
        # A sealed directory keeps its canonical manifest identity, while the
        # encoded archive gets its own digest. Never resolve a parent by date alone.
        if (
            parent is None
            or parent.calendar_end != base_end
            or not expected
            or (parent.digest != expected and (base.get("kind") == "archive" or parent.manifest_sha256 != expected))
            or (base.get("calendar_sha256") and parent.calendar_sha256 != base["calendar_sha256"])
            or (base.get("manifest_sha256") and parent.manifest_sha256 != base["manifest_sha256"])
        ):
            return f"rebalance lineage missing or mismatched base {base_end}; cannot verify {min(pending)}"
        try:
            if parent.read_sessions() != sessions[:start]:
                return f"rebalance lineage calendar differs from base {base_end}; cannot verify {min(pending)}"
        except (OSError, RuntimeError, ValueError) as exc:
            return f"rebalance lineage calendar unavailable for {base_end}: {exc}"
        snapshot, span_end = parent, start
    return None
