"""RR03/RR04: release status, evidence and per-market lineage."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "quant/code/execution"))
sys.path.insert(0, str(ROOT / "quant/code/production"))

import strategy_release as release
from csi1000_production_config import (
    CANONICAL_PROFILES_BY_MARKET,
    profile_manifest,
)


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest(repo, market="csi300", profile="59b93929_top10_drop1_10d", status="RISK_ACCEPTED_OVERRIDE"):
    research = repo / "research.md"
    decision = repo / "decision.md"
    research.write_text("research contract\n")
    decision.write_text("decision\n")
    manifest = {
        "schema_version": 1,
        "release_id": f"{market}_{profile}_{status.lower()}_v1",
        "market": market,
        "profile": profile,
        "candidate_id": "a" * 64,
        "model_config_sha256": "b" * 64,
        "contract_version": release.CANONICAL_CONTRACT_VERSION,
        "label_expression": "Ref($close, -10)/$close - 1",
        "label_horizon": 10,
        "pit_universe": market,
        "topk": 10,
        "dropn": 1,
        "retrain_every_sessions": 20,
        "execution_timing": "T_CLOSE_TO_T1_OPEN",
        "board_limits": "cn_equity_board_date_aware_v2",
        "listing_exemptions": "board_listing_date_rules",
        "lot_rule": "cn_equity_board_lot_v1",
        "liquidity_policy": "missing_quote_fail_closed",
        "fees": {"open_cost": 0.0005, "close_cost": 0.0015, "min_cost": 5.0},
        "initial_cash": 1_000_000,
        "account_start": "2026-09-30",
        "research_contract": {"path": "research.md", "sha256": _sha(research)},
        "decision_artifact": {"path": "decision.md", "sha256": _sha(decision)},
        "status": status,
    }
    if status == "RISK_ACCEPTED_OVERRIDE":
        manifest.update(
            {
                "reason": "risk accepted",
                "scope": "one market",
                "known_gaps": ["gate_not_passed"],
                "authority": "repository operator role",
            }
        )
    elif status == "INCUMBENT_RETAINED":
        manifest["incumbent_since"] = "2026-11-02"
    else:
        gate = repo / "gate.json"
        gate.write_text("{}\n")
        bundle = repo / "bundle.json"
        bundle.write_text("{}\n")
        manifest["gate_receipts"] = [{"path": "gate.json", "sha256": _sha(gate)}]
        manifest["evidence_bundle"] = {"path": "bundle.json", "sha256": _sha(bundle)}
    manifest["strategy_contract_sha256"] = release.strategy_contract_sha256(manifest)
    return release._seal(manifest)


def test_current_release_map_is_override_override_incumbent():
    releases = release.canonical_release_map(ROOT)
    assert releases["csi300"]["status"] == "RISK_ACCEPTED_OVERRIDE"
    assert releases["csi500"]["status"] == "RISK_ACCEPTED_OVERRIDE"
    assert releases["csi1000"]["status"] == "INCUMBENT_RETAINED"
    for market, manifest in releases.items():
        assert manifest["market"] == market
        assert manifest["profile"] == CANONICAL_PROFILES_BY_MARKET[market]
        assert manifest["initial_cash"] == 1_000_000
        assert manifest["retrain_every_sessions"] == 20
        assert manifest["execution_timing"] == "T_CLOSE_TO_T1_OPEN"
        assert manifest["fees"] == {"open_cost": 0.0005, "close_cost": 0.0015, "min_cost": 5.0}


def test_market_profile_lineage_is_not_cross_wired():
    csi300 = profile_manifest("59b93929_top10_drop1_10d")
    csi500 = profile_manifest("raw10d_c98856b4_top20_drop2_20d")
    csi1000 = profile_manifest("c98856b4_top10_drop1")
    assert csi300["market"] == "csi300" and csi300["release_status"] == "RISK_ACCEPTED_OVERRIDE"
    assert csi500["market"] == "csi500" and csi500["release_status"] == "RISK_ACCEPTED_OVERRIDE"
    assert csi1000["market"] == "csi1000" and csi1000["release_status"] == "INCUMBENT_RETAINED"
    assert "stage_b_result_path" not in csi300 and "stage_b_result_path" not in csi500
    assert csi1000["stage_b_result_path"].startswith("quant/data/round1_stage_ab/csi1000_stage_b/")
    assert csi300["topk"] == 10 and csi300["nd"] == 1
    assert csi500["topk"] == 20 and csi500["nd"] == 2
    assert csi1000["topk"] == 10 and csi1000["nd"] == 1


@pytest.mark.parametrize(
    "damage", ["missing_reason", "missing_scope", "missing_gaps", "missing_decision", "changed_hash", "fake_pass"]
)
def test_override_or_pass_evidence_is_fail_closed(tmp_path, damage):
    manifest = _manifest(tmp_path)
    if damage == "missing_reason":
        manifest.pop("reason")
    elif damage == "missing_scope":
        manifest.pop("scope")
    elif damage == "missing_gaps":
        manifest.pop("known_gaps")
    elif damage == "missing_decision":
        manifest.pop("decision_artifact")
    elif damage == "changed_hash":
        manifest["decision_artifact"]["sha256"] = "f" * 64
    elif damage == "fake_pass":
        manifest["status"] = "GATE_PASS"
        manifest["manifest_sha256"] = release._seal(manifest)["manifest_sha256"]
    with pytest.raises(ValueError):
        release.validate_release(manifest, repo_root=tmp_path)


def _gate_pass_manifest(tmp_path):
    from datetime import date, timedelta

    import research_bundle
    from research_governance import digest

    manifest = _manifest(tmp_path)
    registration_receipt = tmp_path / "registration-receipt.json"
    registration_receipt.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "preregistration",
                "status": "PASS",
                "registered_at": "2025-01-02T00:00:00+00:00",
                "forward_start": "2025-02-03",
                "candidate_ids": [manifest["candidate_id"]],
            },
            sort_keys=True,
        )
    )
    contract = {
        "schema_version": 1,
        "registered_at": "2025-01-02T00:00:00+00:00",
        "forward_start": "2025-02-03",
        "registration_receipt_path": registration_receipt.name,
        "registration_receipt_sha256": _sha(registration_receipt),
        "label_horizon": 20,
        "purge_sessions": 20,
        "embargo_sessions": 20,
        "min_forward_sessions": 120,
        "candidate_ids": [manifest["candidate_id"]],
    }
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(json.dumps(contract, sort_keys=True))
    dates = []
    day = date(2025, 2, 3)
    while len(dates) < 130:
        if day.weekday() < 5:
            dates.append(day.isoformat())
        day += timedelta(days=1)
    identity = {
        "bundle_id": "promotion-fixture",
        "market": manifest["market"],
        "profile": manifest["profile"],
        "candidate_id": manifest["candidate_id"],
        "window_start": dates[0],
        "window_end": dates[-1],
        "source_sha256": "b" * 64,
        "environment_sha256": "c" * 64,
        "provider_fingerprint": "d" * 64,
        "model_sha256": manifest["model_config_sha256"],
        "strategy_contract_sha256": manifest["strategy_contract_sha256"],
        "calendar_sha256": "e" * 64,
        "signal_sha256": hashlib.sha256(b"promotion signal artifact").hexdigest(),
        "assembly_receipt_sha256": "1" * 64,
    }
    evidence = {
        "schema_version": 1,
        "contract_sha256": digest(contract),
        "candidate_id": manifest["candidate_id"],
        "market": identity["market"],
        "profile": identity["profile"],
        "source_sha256": identity["source_sha256"],
        "environment_sha256": identity["environment_sha256"],
        "provider_fingerprint": identity["provider_fingerprint"],
        "forward_dates": dates,
    }
    for kind in ("frozen_predictions", "order_replay", "dated_st", "liquidity", "cost_stress", "multiple_testing"):
        artifact = tmp_path / f"{kind}.artifact.json"
        artifact.write_text(json.dumps({"kind": kind, "candidate_id": manifest["candidate_id"]}) + "\n")
        receipt = tmp_path / f"{kind}.receipt.json"
        receipt.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": kind,
                    "status": "PASS",
                    "identity": {
                        "candidate_id": manifest["candidate_id"],
                        "contract_sha256": digest(contract),
                        "forward_start": contract["forward_start"],
                        "forward_end": dates[-1],
                    },
                    "artifact": {"path": artifact.name, "sha256": _sha(artifact)},
                },
                sort_keys=True,
            )
            + "\n"
        )
        evidence[kind] = {"status": "PASS", "sha256": _sha(receipt), "path": receipt.name}
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(evidence, sort_keys=True))
    from research_promotion_gate import check_bundle

    gate_result = check_bundle(contract_path, evidence_path)
    assert gate_result["promotion_eligible"] is True

    report = {
        "dates": dates[:2],
        "gross_return": [0.02, -0.01],
        "cost": [0.001, 0.002],
        "benchmark_return": [0.01, -0.005],
        "account": [101_900_000.0, 100_677_200.0],
        "turnover": [0.1, 0.2],
    }
    ledger = {"initial_cash": 100_000_000.0}
    summary = research_bundle._summary_from_report({"payload": report}, {"payload": ledger}, identity)
    payloads = {
        "research_contract": {
            "contract_version": "g1_spine_v2",
            "assembly_receipt_sha256": identity["assembly_receipt_sha256"],
        },
        "source_environment": {
            "source_sha256": identity["source_sha256"],
            "environment_sha256": identity["environment_sha256"],
        },
        "provider": {"provider_kind": "chenditc", "fingerprint": identity["provider_fingerprint"]},
        "calendar": {"calendar_sha256": identity["calendar_sha256"], "dates": [dates[0], dates[-1]]},
        "model": {"model_sha256": identity["model_sha256"]},
        "strategy": {
            "strategy_contract_sha256": identity["strategy_contract_sha256"],
            "candidate_id": identity["candidate_id"],
        },
        "slice_receipts": {"receipt_sha256": ["2" * 64]},
        "assembly_receipt": {"receipt_sha256": identity["assembly_receipt_sha256"]},
        "signal": {"signal_sha256": identity["signal_sha256"]},
        "execution": {"execution_contract_sha256": "3" * 64},
        "report": report,
        "ledger": ledger,
        "summary": summary,
        "cost": {"total_cost": summary["total_cost"]},
        "account": {
            "initial_cash": ledger["initial_cash"],
            "final_account": summary["final_account"],
            "account_return_max_error": summary["account_return_max_error"],
        },
        "preregistration": {
            "contract_sha256": gate_result["contract_sha256"],
            "registration_receipt_sha256": gate_result["registration_receipt_sha256"],
            "candidate_id": identity["candidate_id"],
            "registered_at": contract["registered_at"],
        },
        "promotion_evidence": {
            "evidence_sha256": gate_result["evidence_sha256"],
            "candidate_id": identity["candidate_id"],
            "forward_dates": dates,
        },
        "gate": {
            "promotion_eligible": True,
            "failed_gates": [],
            "status": "READY_FOR_INDEPENDENT_REVIEW",
            "contract_sha256": gate_result["contract_sha256"],
            "evidence_sha256": gate_result["evidence_sha256"],
        },
        "release_candidate": {
            "market": identity["market"],
            "profile": identity["profile"],
            "candidate_id": identity["candidate_id"],
            "strategy_contract_sha256": identity["strategy_contract_sha256"],
        },
    }
    roles = {}
    for role in research_bundle.PROMOTION_ROLES:
        path = tmp_path / "roles" / f"{role}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema_version": research_bundle.SCHEMA_VERSION,
                    "kind": research_bundle.ROLE_KIND[role],
                    "status": "PASS",
                    "identity": identity,
                    "payload": payloads[role],
                },
                sort_keys=True,
            )
            + "\n"
        )
        roles[role] = path
    bundle_root = tmp_path / "promotion_bundle"
    signal_attachment = tmp_path / "promotion-signal.parquet"
    signal_attachment.write_bytes(b"promotion signal artifact")
    research_bundle.build_bundle(
        bundle_root,
        bundle_kind="PROMOTION",
        bundle_id="promotion-fixture",
        artifacts=roles,
        identity=identity,
        attachments={"signal": signal_attachment},
    )
    manifest.update(
        {
            "status": "GATE_PASS",
            "promotion_gate": {
                "contract_path": "contract.json",
                "evidence_path": "evidence.json",
            },
            "evidence_bundle": {
                "path": "promotion_bundle/bundle.json",
                "sha256": _sha(bundle_root / "bundle.json"),
            },
        }
    )
    manifest["manifest_sha256"] = release._seal(manifest)["manifest_sha256"]
    return manifest


def test_gate_pass_requires_real_gate_and_bundle(tmp_path):
    manifest = _gate_pass_manifest(tmp_path)
    assert release.validate_release(manifest, repo_root=tmp_path)["status"] == "GATE_PASS"
    for key in ("promotion_gate", "evidence_bundle"):
        broken = {**manifest}
        broken.pop(key)
        broken["manifest_sha256"] = release._seal(broken)["manifest_sha256"]
        with pytest.raises(ValueError):
            release.validate_release(broken, repo_root=tmp_path)
    empty_gate = {**manifest, "promotion_gate": {"contract_path": "empty.json", "evidence_path": "empty.json"}}
    (tmp_path / "empty.json").write_text("{}")
    empty_gate["manifest_sha256"] = release._seal(empty_gate)["manifest_sha256"]
    with pytest.raises(ValueError):
        release.validate_release(empty_gate, repo_root=tmp_path)

    underlying = tmp_path / "frozen_predictions.artifact.json"
    underlying.write_text("changed under the same receipt\n")
    with pytest.raises(ValueError, match="promotion gate"):
        release.validate_release(manifest, repo_root=tmp_path)


def test_gate_pass_rejects_semantically_empty_promotion_roles(tmp_path):
    """AR04: PASS wrappers with empty promotion payloads are not evidence."""
    manifest = _gate_pass_manifest(tmp_path)
    import research_bundle

    bundle_path = tmp_path / manifest["evidence_bundle"]["path"]
    bundle_manifest = json.loads(bundle_path.read_text())
    for entry in bundle_manifest["artifacts"].values():
        role_path = bundle_path.parent / entry["path"]
        wrapper = json.loads(role_path.read_text())
        wrapper["payload"] = {}
        role_path.write_text(json.dumps(wrapper, sort_keys=True, allow_nan=False))
        entry["sha256"] = _sha(role_path)
    bundle_manifest = research_bundle._seal(bundle_manifest)
    bundle_path.write_text(json.dumps(bundle_manifest, sort_keys=True, allow_nan=False))
    manifest["evidence_bundle"]["sha256"] = _sha(bundle_path)
    manifest = release._seal(manifest)
    with pytest.raises(ValueError, match="payload|promotion"):
        release.validate_release(manifest, repo_root=tmp_path)


def test_release_swap_or_profile_mismatch_fails(tmp_path):
    manifest = _manifest(tmp_path, market="csi300", profile="59b93929_top10_drop1_10d")
    with pytest.raises(ValueError, match="market"):
        release.validate_release(manifest, repo_root=tmp_path, expected_market="csi500")
    with pytest.raises(ValueError, match="profile"):
        release.validate_release(manifest, repo_root=tmp_path, expected_profile="other")
    swapped = _manifest(tmp_path, market="csi300", profile="raw10d_c98856b4_top20_drop2_20d")
    with pytest.raises(ValueError, match="market"):
        release.validate_release(swapped, repo_root=tmp_path, expected_market="csi500")


def test_canonical_profile_manifest_carries_release_status_into_lineage():
    from csi1000_mvp_contract import validate_production_lineage

    expected = {
        "csi300": ("59b93929_top10_drop1_10d", "RISK_ACCEPTED_OVERRIDE"),
        "csi500": ("raw10d_c98856b4_top20_drop2_20d", "RISK_ACCEPTED_OVERRIDE"),
        "csi1000": ("c98856b4_top10_drop1", "INCUMBENT_RETAINED"),
    }
    for market, (profile, status) in expected.items():
        manifest = profile_manifest(profile)
        assert manifest["market"] == market
        assert manifest["release_status"] == status
        assert manifest["strategy_contract_sha256"]
        validate_production_lineage(manifest, manifest)


def test_canonical_contract_changes_on_every_business_field(tmp_path):
    base = _manifest(tmp_path)
    original = release.strategy_contract_sha256(base)
    changes = {
        "label_expression": "Ref($close, -20)/$close - 1",
        "label_horizon": 20,
        "topk": 20,
        "dropn": 2,
        "board_limits": "changed",
        "listing_exemptions": "changed",
        "lot_rule": "changed",
        "liquidity_policy": "changed",
        "account_start": "2026-10-08",
    }
    for field, value in changes.items():
        changed = dict(base)
        changed[field] = value
        if field in ("label_expression", "label_horizon"):
            changed["label_horizon"] = 20
            changed["label_expression"] = "Ref($close, -20)/$close - 1"
        if field in ("board_limits", "listing_exemptions", "lot_rule", "liquidity_policy"):
            with pytest.raises(ValueError, match="executable rule version"):
                release.strategy_contract_sha256(changed)
        else:
            assert release.strategy_contract_sha256(changed) != original, field
    for field, value in (
        ("topk", 0),
        ("dropn", 11),
        ("initial_cash", True),
        ("execution_timing", "UNKNOWN"),
        ("fees", {"open_cost": -1, "close_cost": 0.0015, "min_cost": 5.0}),
    ):
        broken = dict(base)
        broken[field] = value
        with pytest.raises(ValueError):
            release.strategy_contract_sha256(broken)


def test_contract_version_and_actual_rule_entity_are_bound(tmp_path):
    """AR05: a label string cannot substitute for the actual executable rule entity."""
    manifest = _manifest(tmp_path)
    original = release.strategy_contract_sha256(manifest)
    changed = dict(manifest)
    changed["board_limit_parameters"] = {"main": 0.10, "star": 0.20, "bse": 0.30}
    with pytest.raises(ValueError, match="parameters"):
        release.strategy_contract_sha256(changed)
    executable_change = release._execution_rule_contract()
    executable_change["board"]["thresholds"]["main"] = 0.11
    original_builder = release._execution_rule_contract
    try:
        release._execution_rule_contract = lambda: executable_change
        assert release.strategy_contract_sha256(manifest) != original
    finally:
        release._execution_rule_contract = original_builder

    manifest["contract_version"] = "tampered-unrecognized-version"
    manifest = release._seal(manifest)
    with pytest.raises(ValueError, match="contract version"):
        release.validate_release(manifest, repo_root=tmp_path)


def test_release_evidence_symlink_cannot_escape_repo_root(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("outside\n")
    manifest = _manifest(repo)
    link = repo / "linked-research.md"
    link.symlink_to(outside)
    manifest["research_contract"] = {"path": link.name, "sha256": _sha(outside)}
    manifest = release._seal(manifest)
    with pytest.raises(ValueError, match="inside|root|repository"):
        release.validate_release(manifest, repo_root=repo)
