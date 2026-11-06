"""Trial accounting and conservative promotion gates for new strategy research.

Historical screening scores never imply forward validation. Artifact receipts
are inputs to review; this module does not certify their external provenance.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, date, timezone
import hashlib
import json
from pathlib import Path
import re
import uuid


def digest(payload):
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()
    ).hexdigest()


@contextmanager
def trial_record(root, identity, *, attempt_id=None):
    """Persist every started/completed/failed trial with immutable identity."""
    trial_id = digest(identity)
    attempt_id = attempt_id or uuid.uuid4().hex
    if not re.fullmatch(r"[A-Za-z0-9_-]+", attempt_id):
        raise ValueError("invalid trial attempt identity")
    directory = Path(root) / trial_id / attempt_id
    directory.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents unnoticed reruns from replacing prior trials.
    started = directory / "started.json"
    with started.open("x") as fh:
        json.dump({"identity": identity, "started_at": datetime.now(timezone.utc).isoformat()}, fh, allow_nan=False)
    outcome = {"trial_id": trial_id, "attempt_id": attempt_id, "status": "FAILED"}
    try:
        yield outcome
        outcome["status"] = "COMPLETED"
    except BaseException as exc:
        outcome["error_type"] = type(exc).__name__
        raise
    finally:
        with (directory / "outcome.json").open("x") as fh:
            json.dump(outcome, fh, allow_nan=False, sort_keys=True, indent=2)


def promotion_gate(contract, evidence, *, asof=None):
    """Require pre-registration, fresh forward observations and execution proof."""
    failed = []
    today = asof or datetime.now(timezone.utc).date()
    try:
        if contract.get("schema_version") != 1:
            raise ValueError("contract version")
        registered = datetime.fromisoformat(contract["registered_at"])
        first = date.fromisoformat(contract["forward_start"])
        if registered.tzinfo is None or registered.date() >= first:
            raise ValueError("registration must precede forward observation")
        if not re.fullmatch(r"[0-9a-f]{64}", contract["registration_receipt_sha256"]):
            raise ValueError("registration receipt")
        horizon = int(contract["label_horizon"])
        if horizon < 1 or int(contract["purge_sessions"]) < horizon or int(contract["embargo_sessions"]) < horizon:
            raise ValueError("label overlap")
        if int(contract["min_forward_sessions"]) < 120:
            raise ValueError("insufficient predeclared forward sample")
        if (
            evidence.get("contract_sha256") != digest(contract)
            or evidence.get("candidate_id") not in contract["candidate_ids"]
        ):
            raise ValueError("candidate/contract identity")
        dates = evidence["forward_dates"]
        if dates != sorted(set(dates)) or len(dates) < int(contract["min_forward_sessions"]):
            raise ValueError("forward sample coverage")
        if any(date.fromisoformat(d) < first for d in dates):
            raise ValueError("consumed historical sample")
        if any(date.fromisoformat(d) > today for d in dates):
            raise ValueError("unobserved future forward sample")
        # Each source receipt must bind actual immutable evidence. Multiple
        # retraining phases cannot substitute for new forward calendar dates.
        for kind in ("frozen_predictions", "order_replay", "dated_st", "liquidity", "cost_stress", "multiple_testing"):
            item = evidence.get(kind, {})
            if item.get("status") != "PASS" or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", ""))):
                failed.append(kind)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        failed.append(str(exc))
    return {"promotion_eligible": not failed, "failed_gates": failed, "external_receipts_require_review": True}
