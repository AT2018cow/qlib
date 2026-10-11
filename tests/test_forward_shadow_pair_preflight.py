"""Contract-only prospective shadow tests. No private Modal credentials or returns."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import pytest

from audit.forward_shadow_pair_preflight import CANDIDATES, canonical_hash, validate_pair


def sha(symbol):
    return symbol * 64


def sample():
    common = {
        "usage_date": "2026-10-12", "signal_data_date": "2026-10-09",
        "calendar_sha256": sha("a"), "provider_fingerprint": sha("b"),
        "pit_universe_sha256": sha("c"), "feature_asof": "2026-10-09",
    }
    legs = {}
    for key in CANDIDATES:
        legs[key] = {
            **CANDIDATES[key], **common, "checkpoint_sha256": sha("d"),
            "signal_file_sha256": sha("e"), "full_ranking_sha256": sha("f"),
            "top20_sha256": sha("1"), "pending_order_sha256": sha("2"),
            "train_end": "2026-08-01", "model_fit_asof_utc": "2026-10-10T00:00:00Z",
            "published_utc": "2026-10-11T09:00:00Z",
            "label_horizon_sessions": 20, "topk": 20, "n_drop": 2,
            "paper_account_id": "account-" + key, "initial_cash_cny": 100_000_000,
        }
    return {
        "protocol": "47-forward-shadow-v1", "usage_date": "2026-10-12",
        "created_utc": "2026-10-11T10:00:00Z", "sequence": 1,
        "previous_pair_hash": None, "legs": legs,
    }


def check(packet):
    return validate_pair(packet, independently_observed_outcome_utc="2026-10-12T08:00:00Z")


def test_structural_preflight_not_certification():
    out = check(sample())
    assert out["status"].startswith("STRUCTURAL_PREFLIGHT_ONLY")
    assert not out["training_performed"] and not out["promotion_authorized"]
    assert out["pair_sha256"] == canonical_hash(sample())


def test_exactly_two_registered_legs():
    p = sample()
    del p["legs"]["baseline"]
    with pytest.raises(ValueError, match="exactly winner"):
        check(p)


def test_identity_config_immutable():
    p = sample()
    p["legs"]["winner"]["model_config_sha256"] = sha("3")
    with pytest.raises(ValueError, match="config drift"):
        check(p)


def test_no_backfilled_or_late_predictions():
    p = sample()
    p["usage_date"] = "2026-10-09"
    with pytest.raises(ValueError, match="backfilled"):
        check(p)
    p = sample()
    p["created_utc"] = "2026-10-13T10:00:00Z"
    with pytest.raises(ValueError, match="post-outcome"):
        check(p)


def test_common_asof_provider_and_pit_must_match():
    for field in ("provider_fingerprint", "pit_universe_sha256", "signal_data_date", "feature_asof", "calendar_sha256"):
        p = sample()
        p["legs"]["baseline"][field] = sha("3")
        with pytest.raises(ValueError, match="unequal as-of"):
            check(p)


def test_cross_account_and_future_fit_rejected():
    p = sample()
    p["legs"]["baseline"]["paper_account_id"] = p["legs"]["winner"]["paper_account_id"]
    with pytest.raises(ValueError, match="shared/invalid paper"):
        check(p)
    p = sample()
    p["legs"]["winner"]["model_fit_asof_utc"] = "2026-10-13T10:00:00Z"
    with pytest.raises(ValueError, match="model fit from the future"):
        check(p)


def test_dangling_chain_rejected():
    p = sample()
    p["sequence"] = 2
    with pytest.raises(ValueError, match="chain"):
        check(p)
    p["previous_pair_hash"] = sha("9")
    assert validate_pair(p, previous_pair_hash=sha("9"), independently_observed_outcome_utc="2026-10-12T08:00:00Z")["pair_sha256"]


def test_no_realized_data_in_prediction():
    p = sample()
    p["realized_return"] = 0.10
    with pytest.raises(ValueError, match="realized"):
        check(p)


def test_no_external_outcome_receipt_must_fail():
    with pytest.raises(ValueError, match="independently observed"):
        validate_pair(sample())
