"""Tests for website idempotency and budget policy."""

from __future__ import annotations

import json

from website_budget import (
    DEFAULT_DAILY_CAP_USD,
    DEFAULT_MONTHLY_CREDIT_USD,
    DEFAULT_RESERVE_USD,
    already_published,
    budget_decision,
    read_budget_policy,
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


def test_budget_policy_defaults_and_override(tmp_path):
    assert read_budget_policy(tmp_path) == {
        "daily_cap_usd": DEFAULT_DAILY_CAP_USD,
        "monthly_credit_usd": DEFAULT_MONTHLY_CREDIT_USD,
        "reserve_usd": DEFAULT_RESERVE_USD,
    }
    (tmp_path / "budget_policy.json").write_text(
        json.dumps({"daily_cap_usd": 1.5, "monthly_credit_usd": 30, "reserve_usd": 2})
    )
    assert read_budget_policy(tmp_path)["monthly_credit_usd"] == 30.0


def test_budget_decision_daily_and_monthly_guards():
    assert budget_decision(
        today_cost_usd=0.5,
        monthly_metered_usd=10,
        daily_cap_usd=2,
        monthly_credit_usd=30,
        reserve_usd=2,
    )["allowed"]
    daily = budget_decision(
        today_cost_usd=2.1,
        monthly_metered_usd=10,
        daily_cap_usd=2,
        monthly_credit_usd=30,
        reserve_usd=2,
    )
    assert not daily["allowed"] and daily["reason"] == "daily_cap_reached"
    monthly = budget_decision(
        today_cost_usd=0.5,
        monthly_metered_usd=28.5,
        daily_cap_usd=2,
        monthly_credit_usd=30,
        reserve_usd=2,
    )
    assert not monthly["allowed"] and monthly["reason"] == "monthly_reserve_reached"
