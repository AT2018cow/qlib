"""PR-B preflight ONLY: fail-closed validation of a *proposed* forward shadow pair.

Structural/clock checks cannot attest market data arrival, checkpoint contents,
an externally immutable publication receipt, or actual PaperPortfolio settlement.
Do NOT import historical Stage-A/B results, perform strategy selection, trade,
or modify stage_b_winner_canonical. See protocol 47.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone

START = "2026-10-12"
CAPITAL = 100_000_000
CANDIDATES = {
    "winner": {
        "profile": "stage_b_winner",
        "lineage": "stage_b_winner_canonical",
        "candidate_id": "4e908173705c76fee3782be37068672a3a845bd61894202f3152afe3b9d81ef2",
        "model_config_sha256": "0f2cf94d179e7b9982f873588a3afe25fa02991c873954fea11d2db0e025cb88",
    },
    "baseline": {
        "profile": "baseline_control",
        "lineage": "stage_b_baseline_shadow_v1",
        "candidate_id": "23b92de05cf36c82998de684d0fbf64d81ee54490d755bd3cf96311c00286785",
        "model_config_sha256": "793021af66034ec508848ee032f6d309103291981abb63d11ae779da9c5ee7d3",
    },
}
COMMON = ("usage_date", "signal_data_date", "calendar_sha256",
          "provider_fingerprint", "pit_universe_sha256", "feature_asof")
DIGESTS = ("checkpoint_sha256", "signal_file_sha256",
           "full_ranking_sha256", "top20_sha256", "pending_order_sha256")


def _require(cond, detail):
    if not cond:
        raise ValueError("FORWARD_PAIR_INVALID: " + detail)


def _digest(text):
    return isinstance(text, str) and len(text) == 64 and all(c in "0123456789abcdef" for c in text)


def _utc(value):
    _require(isinstance(value, str), "UTC event time missing")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    _require(parsed.tzinfo is not None and parsed.utcoffset().total_seconds() == 0, "non-UTC event time")
    return parsed


def canonical_hash(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                      separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def validate_pair(packet, *, previous_pair_hash=None, independently_observed_outcome_utc=None):
    """Structural preflight only; never returns a certified-forward verdict.

    An actual operator must additionally verify the raw signal/pending-order
    bytes, checkpoint, PIT dates, 20-session label maturity, external publication
    receipt preceding any outcome and independent settled paper accounts.
    """
    _require(isinstance(packet, dict) and set(packet.get("legs", {})) == set(CANDIDATES), "exactly winner+baseline required")
    _require(packet.get("protocol") == "47-forward-shadow-v1", "wrong pre-registered protocol")
    day = packet.get("usage_date")
    _require(isinstance(day, str), "usage date missing")
    date.fromisoformat(day)
    _require(day >= START, "backfilled/pre-eligibility usage date")
    created = _utc(packet.get("created_utc"))
    _require(independently_observed_outcome_utc is not None, "independently observed outcome timestamp required")
    _require(created < _utc(independently_observed_outcome_utc), "post-outcome prediction/backfill")
    sequence = packet.get("sequence")
    _require(type(sequence) is int and sequence >= 1, "invalid sequence")
    if sequence == 1:
        _require(previous_pair_hash is None and packet.get("previous_pair_hash") is None, "invalid first-pair chain")
    else:
        _require(_digest(previous_pair_hash) and packet.get("previous_pair_hash") == previous_pair_hash,
                 "broken/missing previous-pair hash chain")
    _require("execution_report" not in packet and "realized_return" not in packet, "realized outcome embedded in prediction")
    accounts = set()
    anchor = None
    for name, frozen in CANDIDATES.items():
        leg = packet["legs"][name]
        _require(isinstance(leg, dict), "leg missing: " + name)
        for field, value in frozen.items():
            _require(leg.get(field) == value, "frozen identity/config drift: " + name + "." + field)
        _require(leg.get("usage_date") == day, "leg usage date mismatch")
        for field in COMMON:
            _require(leg.get(field) is not None, "missing shared attribute " + field)
            if field.endswith("_sha256"):
                _require(_digest(leg[field]), "bad common SHA " + field)
        comparable = tuple(leg[x] for x in COMMON)
        if anchor is None:
            anchor = comparable
        else:
            _require(anchor == comparable, "legs have unequal as-of/PIT/calendar/provider")
        for field in DIGESTS:
            _require(_digest(leg.get(field)), "missing/bad artifact hash " + name + "." + field)
        _require(isinstance(leg.get("train_end"), str) and leg["train_end"] <= leg["signal_data_date"],
                 "future training labels: " + name)
        _require(_utc(leg.get("model_fit_asof_utc")) <= created, "model fit from the future: " + name)
        _require(_utc(leg.get("published_utc")) <= created, "late individual leg: " + name)
        _require(leg.get("label_horizon_sessions") == 20 and leg.get("topk") == 20 and
                 leg.get("n_drop") == 2, "frozen training/trading protocol drift")
        account_id = leg.get("paper_account_id")
        _require(isinstance(account_id, str) and account_id and account_id not in accounts,
                 "shared/invalid paper account id")
        accounts.add(account_id)
        _require(leg.get("initial_cash_cny") == CAPITAL, "independent account not CNY100m")
    return {"status": "STRUCTURAL_PREFLIGHT_ONLY_NEEDS_IMMUTABLE_RECEIPT_AND_SETTLEMENT",
            "pair_sha256": canonical_hash(packet), "usage_date": day,
            "training_performed": False, "promotion_authorized": False,
            "production_winner_unchanged": True}
