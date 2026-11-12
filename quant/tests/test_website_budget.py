"""Website publication receipts and non-blocking billing telemetry.

A receipt is not a publication merely because it contains a status and two
identities: it must bind the semantic/account contract, latest session and the
three immutable market result/series/ledger hashes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from website_budget import (
    PUBLISHED_STATUSES,
    billing_observation,
    checked_publication_receipt,
    receipt_path,
    validate_publication_receipt,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _valid_series(market, *, source, semantic, account, provider):
    from csi1000_production_config import (
        CANONICAL_ND_BY_MARKET,
        CANONICAL_PROFILES_BY_MARKET,
        CANONICAL_TOPK_BY_MARKET,
        profile_manifest,
    )
    from test_website_simulation import example_series

    series, _ = example_series(market)
    profile = CANONICAL_PROFILES_BY_MARKET[market]
    identity = series["account_identity"]
    series["market"] = identity["market"] = market
    identity.update(
        profile=profile,
        paper_lineage=f"{profile}_website_oct08_v2",
        topk=CANONICAL_TOPK_BY_MARKET[market],
        nd=CANONICAL_ND_BY_MARKET[market],
        horizon=profile_manifest(profile)["label_horizon"],
    )
    series["fit_receipts"][0]["profile"] = profile
    series["fit_receipts"][0]["training_contract"]["horizon"] = identity["horizon"]
    series["source_manifest_sha256"] = source
    series["release_source_sha256"] = source
    series["website_semantic_sha256"] = semantic
    series["account_contract_sha256"] = account
    series["provider_archive_sha256"] = provider
    return series


def _valid_receipt(tmp_path):
    source = "a" * 64
    semantic = "b" * 64
    asset = "sha256:" + "c" * 64
    latest = "2026-10-09"
    accounts = {market: _sha(market.encode()) for market in ("csi300", "csi500", "csi1000")}
    markets = {}
    for market, account in accounts.items():
        cache = _sha(("cache-" + market).encode())
        result_dir = tmp_path / cache / asset.split(":")[1] / market / "full"
        result_dir.mkdir(parents=True)
        series = _valid_series(
            market,
            source=source,
            semantic=semantic,
            account=account,
            provider=asset.split(":")[1],
        )
        final_state = b'{"state": "fixed"}\n'
        (result_dir / "final_state.json").write_bytes(final_state)
        ledger = {"final_state.json": _sha(final_state)}
        result = {
            "status": "COMPLETED",
            "market": market,
            "source_sha256": source,
            "website_semantic_sha256": semantic,
            "account_contract_sha256": account,
            "asset_digest": asset,
            "cache_sha256": cache,
            "series": series,
            "ledger_file_sha256": ledger,
        }
        result_bytes = (json.dumps(result, indent=2, allow_nan=False) + "\n").encode()
        (result_dir / "result.json").write_bytes(result_bytes)
        series_bytes = (json.dumps(series, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()
        markets[market] = {
            "cache_sha256": cache,
            "series_sha256": _sha(series_bytes),
            "result_sha256": _sha(result_bytes),
            "ledger_file_sha256": ledger,
            "final_state_sha256": ledger["final_state.json"],
        }
    receipt = {
        "schema_version": 2,
        "status": "PUBLISHED",
        "commit": "d" * 40,
        "source_sha256": source,
        "market_release_sha256": {market: source for market in accounts},
        "website_semantic_sha256": semantic,
        "account_contract_sha256": accounts,
        "asset_digest": asset,
        "latest_date": latest,
        "files": 12,
        "markets": markets,
    }
    path = receipt_path(tmp_path, source, asset)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(receipt, indent=2) + "\n")
    expected = {
        "simulation_root": tmp_path,
        "expected_source_sha256": source,
        "expected_semantic_sha256": semantic,
        "expected_accounts": accounts,
        "expected_asset_digest": asset,
        "expected_latest_date": latest,
    }
    return receipt, path, expected


def _rewrite_result(tmp_path, receipt, market, mutate):
    binding = receipt["markets"][market]
    provider = receipt["asset_digest"].split(":", 1)[1]
    result_path = tmp_path / binding["cache_sha256"] / provider / market / "full" / "result.json"
    result = json.loads(result_path.read_text())
    mutate(result)
    result_bytes = (json.dumps(result, indent=2, allow_nan=False) + "\n").encode()
    result_path.write_bytes(result_bytes)
    series_bytes = (json.dumps(result["series"], indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()
    binding["result_sha256"] = _sha(result_bytes)
    binding["series_sha256"] = _sha(series_bytes)


def test_valid_receipt_binds_all_three_immutable_markets(tmp_path):
    receipt, _path, expected = _valid_receipt(tmp_path)
    assert validate_publication_receipt(receipt, **expected)["markets"].keys() == {
        "csi300",
        "csi500",
        "csi1000",
    }
    assert checked_publication_receipt(**expected)["status"] == "PUBLISHED"


def test_legacy_receipt_without_per_market_release_map_remains_valid(tmp_path):
    receipt, _path, expected = _valid_receipt(tmp_path)
    receipt.pop("market_release_sha256")
    assert validate_publication_receipt(receipt, **expected)["status"] == "PUBLISHED"


def test_minimal_receipt_is_never_treated_as_published(tmp_path):
    source = "a" * 64
    asset = "sha256:" + "c" * 64
    path = receipt_path(tmp_path, source, asset)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"status": "PUBLISHED", "source_sha256": source, "asset_digest": asset}))
    with pytest.raises(ValueError, match="schema"):
        checked_publication_receipt(
            simulation_root=tmp_path,
            expected_source_sha256=source,
            expected_semantic_sha256="b" * 64,
            expected_accounts={m: "e" * 64 for m in ("csi300", "csi500", "csi1000")},
            expected_asset_digest=asset,
            expected_latest_date="2026-11-10",
        )


@pytest.mark.parametrize(
    "damage",
    [
        "semantic",
        "account_missing",
        "account_changed",
        "latest",
        "series_hash",
        "result_hash",
        "ledger_hash",
        "market_missing",
        "provider",
        "files",
        "series_latest",
        "result_content",
        "ledger_content",
        "release_map_empty",
        "release_map_missing_market",
        "release_map_invalid_digest",
        "series_release",
        "series_semantic",
        "series_account",
        "series_provider",
    ],
)
def test_tampered_receipt_or_immutable_payload_is_rejected(tmp_path, damage):
    receipt, path, expected = _valid_receipt(tmp_path)
    if damage == "semantic":
        receipt["website_semantic_sha256"] = "f" * 64
    elif damage == "account_missing":
        receipt["account_contract_sha256"].pop("csi500")
    elif damage == "account_changed":
        receipt["account_contract_sha256"]["csi500"] = "f" * 64
    elif damage == "latest":
        receipt["latest_date"] = "2026-11-09"
    elif damage == "series_hash":
        receipt["markets"]["csi300"]["series_sha256"] = "f" * 64
    elif damage == "result_hash":
        receipt["markets"]["csi300"]["result_sha256"] = "f" * 64
    elif damage == "ledger_hash":
        receipt["markets"]["csi300"]["ledger_file_sha256"]["final_state.json"] = "f" * 64
    elif damage == "market_missing":
        receipt["markets"].pop("csi1000")
    elif damage == "provider":
        receipt["asset_digest"] = "sha256:" + "f" * 64
    elif damage == "files":
        receipt["files"] = 9
    elif damage == "series_latest":
        binding = receipt["markets"]["csi300"]
        result_path = tmp_path / binding["cache_sha256"] / ("c" * 64) / "csi300" / "full" / "result.json"
        result = json.loads(result_path.read_text())
        result["series"]["latest_date"] = "2026-11-09"
        result_bytes = (json.dumps(result, indent=2, allow_nan=False) + "\n").encode()
        result_path.write_bytes(result_bytes)
        series_bytes = (json.dumps(result["series"], indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()
        binding["result_sha256"] = _sha(result_bytes)
        binding["series_sha256"] = _sha(series_bytes)
    elif damage == "result_content":
        result = (
            tmp_path / receipt["markets"]["csi300"]["cache_sha256"] / ("c" * 64) / "csi300" / "full" / "result.json"
        )
        result.write_text(result.read_text() + " ")
    elif damage == "ledger_content":
        state = (
            tmp_path
            / receipt["markets"]["csi300"]["cache_sha256"]
            / ("c" * 64)
            / "csi300"
            / "full"
            / "final_state.json"
        )
        state.write_text('{"state": "changed"}\n')
    elif damage == "release_map_empty":
        receipt["market_release_sha256"] = {}
    elif damage == "release_map_missing_market":
        receipt["market_release_sha256"].pop("csi1000")
    elif damage == "release_map_invalid_digest":
        receipt["market_release_sha256"]["csi500"] = "NOT-A-SHA"
    elif damage == "series_release":
        receipt["market_release_sha256"]["csi300"] = "e" * 64
        _rewrite_result(tmp_path, receipt, "csi300", lambda result: result.update(source_sha256="e" * 64))
    elif damage == "series_semantic":
        _rewrite_result(
            tmp_path,
            receipt,
            "csi300",
            lambda result: result["series"].update(website_semantic_sha256="e" * 64),
        )
    elif damage == "series_account":
        _rewrite_result(
            tmp_path,
            receipt,
            "csi300",
            lambda result: result["series"].update(account_contract_sha256="e" * 64),
        )
    elif damage == "series_provider":
        _rewrite_result(
            tmp_path,
            receipt,
            "csi300",
            lambda result: result["series"].update(provider_archive_sha256="e" * 64),
        )
    path.write_text(json.dumps(receipt, indent=2) + "\n")
    with pytest.raises(ValueError):
        checked_publication_receipt(**expected)


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
    assert set(PUBLISHED_STATUSES) == {"PUBLISHED", "ALREADY_PUBLISHED", "RECOVERED_AFTER_PUBLICATION"}
