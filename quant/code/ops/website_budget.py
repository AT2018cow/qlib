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


MARKETS = ("csi300", "csi500", "csi1000")
RECEIPT_SCHEMA_VERSION = 2
_HEX = set("0123456789abcdef")


def _sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _digest(value, label):
    if not isinstance(value, str) or len(value) != 64 or any(char not in _HEX for char in value):
        raise ValueError(f"publication receipt {label} must be a lowercase SHA256")
    return value


def _asset_hex(value):
    text = str(value or "")
    if not text.startswith("sha256:") or len(text) != 71:
        raise ValueError("publication receipt asset digest must be sha256:<64hex>")
    _digest(text.split(":", 1)[1], "asset_digest")
    return text.split(":", 1)[1]


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(f"publication receipt payload unreadable: {path}") from exc


def validate_publication_receipt(
    receipt,
    *,
    simulation_root,
    expected_source_sha256,
    expected_semantic_sha256,
    expected_accounts,
    expected_asset_digest,
    expected_latest_date=None,
):
    """Validate the receipt and every immutable market payload it binds."""
    from datetime import date

    if not isinstance(receipt, dict) or receipt.get("schema_version") != RECEIPT_SCHEMA_VERSION:
        raise ValueError("publication receipt schema is missing or unsupported")
    if receipt.get("status") not in PUBLISHED_STATUSES:
        raise ValueError("publication receipt is not published")
    source = _digest(receipt.get("source_sha256"), "source_sha256")
    semantic = _digest(receipt.get("website_semantic_sha256"), "website_semantic_sha256")
    if source != expected_source_sha256:
        raise ValueError("publication receipt release identity changed")
    if semantic != expected_semantic_sha256:
        raise ValueError("publication receipt semantic identity changed")
    if receipt.get("asset_digest") != expected_asset_digest:
        raise ValueError("publication receipt provider digest changed")
    provider_hex = _asset_hex(receipt.get("asset_digest"))
    accounts = receipt.get("account_contract_sha256")
    if not isinstance(accounts, dict) or set(accounts) != set(MARKETS):
        raise ValueError("publication receipt must bind exactly three account contracts")
    for market in MARKETS:
        _digest(accounts.get(market), f"{market} account contract")
        if accounts[market] != expected_accounts.get(market):
            raise ValueError(f"publication receipt {market} account contract changed")
    latest = receipt.get("latest_date")
    if not isinstance(latest, str) or date.fromisoformat(latest).isoformat() != latest:
        raise ValueError("publication receipt latest_date is missing or invalid")
    if expected_latest_date is not None and latest != expected_latest_date:
        raise ValueError("publication receipt latest_date is not the session date")
    if receipt.get("files") != 12:
        raise ValueError("publication receipt must bind twelve published files")
    markets = receipt.get("markets")
    if not isinstance(markets, dict) or set(markets) != set(MARKETS):
        raise ValueError("publication receipt must bind exactly three market results")
    root = Path(simulation_root)
    for market in MARKETS:
        binding = markets.get(market)
        if not isinstance(binding, dict):
            raise ValueError(f"publication receipt {market} binding is missing")
        cache = _digest(binding.get("cache_sha256"), f"{market} cache_sha256")
        series_sha = _digest(binding.get("series_sha256"), f"{market} series_sha256")
        result_sha = _digest(binding.get("result_sha256"), f"{market} result_sha256")
        final_sha = _digest(binding.get("final_state_sha256"), f"{market} final_state_sha256")
        ledger = binding.get("ledger_file_sha256")
        if not isinstance(ledger, dict) or not ledger:
            raise ValueError(f"publication receipt {market} ledger hashes are missing")
        for relative, digest in ledger.items():
            _digest(digest, f"{market} ledger {relative}")
            path = Path(relative)
            if path.is_absolute() or ".." in path.parts or not relative:
                raise ValueError(f"publication receipt {market} ledger path is invalid")
        if ledger.get("final_state.json") != final_sha:
            raise ValueError(f"publication receipt {market} final state hash disagrees")
        result_dir = root / cache / provider_hex / market / "full"
        result_path = result_dir / "result.json"
        if not result_path.is_file() or _sha256(result_path.read_bytes()) != result_sha:
            raise ValueError(f"publication receipt {market} immutable result hash mismatch")
        result = _read_json(result_path)
        if (
            result.get("status") != "COMPLETED"
            or result.get("market") != market
            or result.get("source_sha256") != source
            or result.get("website_semantic_sha256") != semantic
            or result.get("account_contract_sha256") != accounts[market]
            or result.get("asset_digest") != expected_asset_digest
            or result.get("cache_sha256") != cache
        ):
            raise ValueError(f"publication receipt {market} immutable result identity mismatch")
        series_bytes = (json.dumps(result.get("series"), indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()
        if _sha256(series_bytes) != series_sha:
            raise ValueError(f"publication receipt {market} immutable series hash mismatch")
        if result.get("ledger_file_sha256") != ledger:
            raise ValueError(f"publication receipt {market} immutable ledger map mismatch")
        for relative, digest in ledger.items():
            path = result_dir / relative
            if not path.is_file() or _sha256(path.read_bytes()) != digest:
                raise ValueError(f"publication receipt {market} immutable ledger hash mismatch: {relative}")
    return receipt


def checked_publication_receipt(
    *,
    simulation_root,
    expected_source_sha256,
    expected_semantic_sha256,
    expected_accounts,
    expected_asset_digest,
    expected_latest_date=None,
):
    """Return a validated receipt, None if absent, or fail closed if corrupt."""
    path = receipt_path(simulation_root, expected_source_sha256, expected_asset_digest)
    if not path.is_file():
        return None
    receipt = _read_json(path)
    return validate_publication_receipt(
        receipt,
        simulation_root=simulation_root,
        expected_source_sha256=expected_source_sha256,
        expected_semantic_sha256=expected_semantic_sha256,
        expected_accounts=expected_accounts,
        expected_asset_digest=expected_asset_digest,
        expected_latest_date=expected_latest_date,
    )


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
