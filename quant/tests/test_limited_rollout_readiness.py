"""Limited review must bind fresh, untampered evidence and user constraints."""

import hashlib

from limited_rollout_readiness import REQUIRED_CHECKS, assess_package


def valid_package(tmp_path):
    receipt = tmp_path / "receipt.json"
    receipt.write_text('{"synthetic_fixture":true}')
    source_sha = "a" * 64
    return {
        "schema_version": 1,
        "market": "csi300",
        "profile": "59b93929_top10_drop1_10d",
        "budget_cny": 1000000,
        "max_single_order_cny": 100000,
        "max_daily_order_cny": 200000,
        "max_single_weight": 0.2,
        "max_drawdown_fraction": 0.1,
        "shared_account": False,
        "execution_mode": "MANUAL_CONFIRMATION",
        "source_manifest_sha256": source_sha,
        "usage_date": "2026-11-05",
        "data_date": "2026-11-04",
        "verified_calendar": ["2026-11-03", "2026-11-04", "2026-11-05"],
        "checks": {
            name: {
                "status": "PASS",
                "market": "csi300",
                "source_manifest_sha256": source_sha,
                "usage_date": "2026-11-05",
                "path": "receipt.json",
                "sha256": hashlib.sha256(receipt.read_bytes()).hexdigest(),
            }
            for name in REQUIRED_CHECKS
        },
    }


def test_ready_package_never_grants_live_or_research_approval(tmp_path):
    result = assess_package(valid_package(tmp_path), evidence_root=tmp_path)
    assert result["status"] == "READY_FOR_LIMITED_REVIEW"
    assert result["live_trading_approved"] is False
    assert result["research_promotion_eligible"] is False


def test_stale_or_modified_receipt_blocks(tmp_path):
    package = valid_package(tmp_path)
    package["checks"]["dated_preopen_st"]["usage_date"] = "2026-11-04"
    (tmp_path / "receipt.json").write_text("changed")
    result = assess_package(package, evidence_root=tmp_path)
    assert result["status"] == "BLOCKED"
    assert "CHECK_DATE_MISMATCH:dated_preopen_st" in result["reasons"]
    assert "RECEIPT_HASH_MISMATCH:order_legality" in result["reasons"]


def test_missing_user_constraints_and_shared_account_block(tmp_path):
    package = valid_package(tmp_path)
    package["budget_cny"] = None
    package["shared_account"] = True
    result = assess_package(package, evidence_root=tmp_path)
    assert result["status"] == "BLOCKED"
    assert "MISSING_OR_INVALID_BUDGET_CNY" in result["reasons"]
    assert "SHARED_ACCOUNT_REQUIRES_CROSS_MARKET_NETTING_REVIEW" in result["reasons"]


def test_explicit_user_choice_to_disable_risk_caps_is_respected(tmp_path):
    package = valid_package(tmp_path)
    package.update(
        max_single_weight=None,
        max_drawdown_fraction=None,
        single_weight_limit_enabled=False,
        drawdown_limit_enabled=False,
        max_single_order_cny=None,
        max_daily_order_cny=None,
    )
    result = assess_package(package, evidence_root=tmp_path)
    assert result["status"] == "READY_FOR_LIMITED_REVIEW"
    assert result["live_trading_approved"] is False
