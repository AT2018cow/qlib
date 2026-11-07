"""Idempotency and budget policy for the website simulation App.

These helpers are import-clean so the guard can be tested without Qlib, Modal
or network access.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_DAILY_CAP_USD = 2.0
DEFAULT_MONTHLY_CREDIT_USD = 0.0
DEFAULT_RESERVE_USD = 2.0
PUBLISHED_STATUSES = ("PUBLISHED", "ALREADY_PUBLISHED", "RECOVERED_AFTER_PUBLICATION")


def receipt_path(simulation_root: str | Path, source_sha256: str, asset_digest: str) -> Path:
    """Return the content-addressed publication receipt path."""
    provider_hex = asset_digest.split(":", 1)[1] if ":" in asset_digest else asset_digest
    return Path(simulation_root) / source_sha256 / provider_hex / "publication.json"


def already_published(
    simulation_root: str | Path,
    source_sha256: str,
    asset_digest: str,
) -> dict[str, Any] | None:
    """Return the receipt if this exact source/provider was already published."""
    path = receipt_path(simulation_root, source_sha256, asset_digest)
    if not path.is_file():
        return None
    try:
        receipt = json.loads(path.read_text())
    except Exception:
        return None
    if (
        receipt.get("status") in PUBLISHED_STATUSES
        and receipt.get("source_sha256") == source_sha256
        and receipt.get("asset_digest") == asset_digest
    ):
        return receipt
    return None


def read_budget_policy(simulation_root: str | Path) -> dict[str, float]:
    """Read the optional budget policy file; malformed files fall back safely."""
    policy = {
        "daily_cap_usd": DEFAULT_DAILY_CAP_USD,
        "monthly_credit_usd": DEFAULT_MONTHLY_CREDIT_USD,
        "reserve_usd": DEFAULT_RESERVE_USD,
    }
    path = Path(simulation_root) / "budget_policy.json"
    if not path.is_file():
        return policy
    try:
        raw = json.loads(path.read_text())
    except Exception:
        return policy
    for key in policy:
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            policy[key] = float(value)
    return policy


def budget_decision(
    *,
    today_cost_usd: float,
    monthly_metered_usd: float,
    daily_cap_usd: float,
    monthly_credit_usd: float,
    reserve_usd: float,
) -> dict[str, Any]:
    """Fail-closed budget decision with a clear reason."""
    if daily_cap_usd > 0 and today_cost_usd >= daily_cap_usd:
        return {
            "allowed": False,
            "reason": "daily_cap_reached",
            "today_cost_usd": round(today_cost_usd, 6),
            "daily_cap_usd": daily_cap_usd,
        }
    if monthly_credit_usd > 0 and monthly_credit_usd - monthly_metered_usd < reserve_usd:
        return {
            "allowed": False,
            "reason": "monthly_reserve_reached",
            "monthly_metered_usd": round(monthly_metered_usd, 6),
            "monthly_credit_usd": monthly_credit_usd,
            "reserve_usd": reserve_usd,
        }
    return {
        "allowed": True,
        "reason": "within_budget",
        "today_cost_usd": round(today_cost_usd, 6),
        "monthly_metered_usd": round(monthly_metered_usd, 6),
    }
