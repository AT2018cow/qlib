"""Idempotent website receipts and non-blocking billing telemetry.

These helpers are import-clean so they can be tested without Qlib, Modal or
network access.  Billing observations are audit data only: they never decide
whether a replay may run, and an unavailable billing API is recorded as
``unavailable`` rather than treated as success or failure.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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


def billing_observation(*, report, summary) -> dict[str, Any]:
    """Return cost telemetry without any business allow/block decision."""

    def field(value, name):
        if isinstance(value, dict):
            return value[name]
        return getattr(value, name)

    try:
        items = report()
        today_cost = sum(float(field(item, "cost")) for item in items)
        monthly_metered = float(field(summary(), "metered_cost"))
        return {
            "status": "available",
            "source": "workspace.billing",
            "today_cost_usd": round(today_cost, 6),
            "monthly_metered_usd": round(monthly_metered, 6),
        }
    except Exception as exc:  # noqa: BLE001 - telemetry boundary
        return {
            "status": "unavailable",
            "source": "workspace.billing",
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
        }
