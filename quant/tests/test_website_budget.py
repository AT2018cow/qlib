"""Website idempotency plus non-blocking billing telemetry.

The former USD budget guard is intentionally gone: billing observations may be
recorded, but they must never allow, block or change a replay.
"""

from __future__ import annotations

import json
from pathlib import Path

from website_budget import (
    PUBLISHED_STATUSES,
    already_published,
    billing_observation,
    receipt_path,
)


def test_receipt_path_and_already_published(tmp_path):
    source = "a" * 64
    digest = "sha256:" + "b" * 64
    path = receipt_path(tmp_path, source, digest)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "status": "PUBLISHED",
                "source_sha256": source,
                "asset_digest": digest,
                "commit": "c" * 40,
                "files": 12,
            }
        )
    )
    assert already_published(tmp_path, source, digest)["commit"] == "c" * 40
    assert already_published(tmp_path, source, "sha256:" + "d" * 64) is None
    assert already_published(tmp_path, "e" * 64, digest) is None


def test_receipt_rejects_non_published_or_wrong_identity(tmp_path):
    source = "a" * 64
    digest = "sha256:" + "b" * 64
    path = receipt_path(tmp_path, source, digest)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"status": "FAILED", "source_sha256": source, "asset_digest": digest}))
    assert already_published(tmp_path, source, digest) is None
    assert set(PUBLISHED_STATUSES) == {"PUBLISHED", "ALREADY_PUBLISHED", "RECOVERED_AFTER_PUBLICATION"}


def test_billing_observation_never_returns_a_business_decision():
    def report():
        return [{"cost": 999.0}, {"cost": 1.0}]

    def summary():
        return {"metered_cost": 12345.0}

    observation = billing_observation(report=report, summary=summary)
    assert observation["status"] == "available"
    assert observation["today_cost_usd"] == 1000.0
    assert observation["monthly_metered_usd"] == 12345.0
    assert "allowed" not in observation
    assert "reason" not in observation


def test_billing_failure_is_unavailable_and_non_blocking():
    def broken():
        raise RuntimeError("billing API down")

    observation = billing_observation(report=broken, summary=broken)
    assert observation["status"] == "unavailable"
    assert observation["error_type"] == "RuntimeError"
    assert "allowed" not in observation


def test_no_budget_blocking_state_remains_in_website_code():
    code = Path(__file__).resolve().parents[1] / "code"
    text = "\n".join(path.read_text() for path in code.rglob("*.py") if "__pycache__" not in path.parts)
    assert "SKIPPED_BUDGET" not in text
    assert "budget_decision" not in text
    assert "QLIB_DAILY_BUDGET_USD" not in text
    assert "QLIB_BUDGET_STRICT" not in text
