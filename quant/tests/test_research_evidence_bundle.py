"""IR04: semantic research evidence bundles and legacy inventory."""

from __future__ import annotations

import json
import ast
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code/execution"))

import research_bundle as bundle


def _identity(bundle_id="fixture", market="csi1000", profile="c98856b4_top10_drop1"):
    return {
        "bundle_id": bundle_id,
        "market": market,
        "profile": profile,
        "candidate_id": "f" * 64,
        "window_start": "2026-10-16",
        "window_end": "2026-10-20",
        "source_sha256": "a" * 64,
        "environment_sha256": "b" * 64,
        "provider_fingerprint": "c" * 64,
        "model_sha256": "d" * 64,
        "strategy_contract_sha256": "e" * 64,
        "calendar_sha256": "1" * 64,
        "signal_sha256": hashlib.sha256(b"fixed signal artifact").hexdigest(),
        "assembly_receipt_sha256": "3" * 64,
    }


def _attachments(tmp_path):
    signal = tmp_path / "fixed-signal.parquet"
    signal.write_bytes(b"fixed signal artifact")
    return {"signal": signal}


def _role(tmp_path, role, identity, payload=None, *, status="PASS", kind=None):
    path = tmp_path / "roles" / f"{role}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": bundle.SCHEMA_VERSION,
                "kind": kind or bundle.ROLE_KIND[role],
                "status": status,
                "identity": identity,
                "payload": payload or {},
            },
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    )
    return path


def _artifacts(tmp_path, kind, identity):
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
        "calendar": {
            "calendar_sha256": identity["calendar_sha256"],
            "dates": ["2026-10-16", "2026-10-20"],
        },
        "model": {"model_sha256": identity["model_sha256"]},
        "strategy": {
            "strategy_contract_sha256": identity["strategy_contract_sha256"],
            "candidate_id": identity["candidate_id"],
        },
        "slice_receipts": {"receipt_sha256": ["4" * 64]},
        "assembly_receipt": {"receipt_sha256": identity["assembly_receipt_sha256"]},
        "signal": {"signal_sha256": identity["signal_sha256"]},
    }
    if kind == "G1_SIGNAL":
        roles = bundle.G1_SIGNAL_ROLES
    else:
        roles = bundle.G2_EVALUATION_ROLES
        report = {
            "dates": ["2026-10-16", "2026-10-19"],
            "gross_return": [0.02, -0.01],
            "cost": [0.001, 0.002],
            "benchmark_return": [0.01, -0.005],
            "account": [101_900_000.0, 100_677_200.0],
            "turnover": [0.1, 0.2],
        }
        ledger = {"initial_cash": 100_000_000.0}
        summary = bundle._summary_from_report({"payload": report}, {"payload": ledger}, identity)
        payloads.update(
            {
                "execution": {"execution_contract_sha256": "5" * 64},
                "report": report,
                "ledger": ledger,
                "summary": summary,
                "cost": {"total_cost": summary["total_cost"]},
                "account": {
                    "initial_cash": ledger["initial_cash"],
                    "final_account": summary["final_account"],
                    "account_return_max_error": summary["account_return_max_error"],
                },
            }
        )
        if kind == "PROMOTION":
            roles = bundle.PROMOTION_ROLES
            payloads.update(
                {
                    "preregistration": {
                        "contract_sha256": "6" * 64,
                        "registration_receipt_sha256": "8" * 64,
                        "candidate_id": identity["candidate_id"],
                        "registered_at": "2025-01-02T00:00:00+00:00",
                    },
                    "promotion_evidence": {
                        "evidence_sha256": "7" * 64,
                        "candidate_id": identity["candidate_id"],
                        "forward_dates": ["2026-10-16", "2026-10-20"],
                    },
                    "gate": {
                        "promotion_eligible": True,
                        "failed_gates": [],
                        "status": "READY_FOR_INDEPENDENT_REVIEW",
                        "contract_sha256": "6" * 64,
                        "evidence_sha256": "7" * 64,
                    },
                    "release_candidate": {
                        "market": identity["market"],
                        "profile": identity["profile"],
                        "candidate_id": identity["candidate_id"],
                        "strategy_contract_sha256": identity["strategy_contract_sha256"],
                    },
                }
            )
    return {role: _role(tmp_path, role, identity, payloads.get(role)) for role in roles}


def test_g1_bundle_verifies_and_g2_summary_rebuilds(tmp_path):
    identity = _identity()
    g1 = _artifacts(tmp_path, "G1_SIGNAL", identity)
    root = tmp_path / "g1"
    manifest = bundle.build_bundle(
        root,
        bundle_kind="G1_SIGNAL",
        bundle_id="fixture",
        artifacts=g1,
        identity=identity,
        attachments=_attachments(tmp_path),
    )
    assert manifest["bundle_kind"] == "G1_SIGNAL"
    assert bundle.verify_bundle(root)["manifest_sha256"] == manifest["manifest_sha256"]
    with pytest.raises(ValueError, match="G2/PROMOTION"):
        bundle.rebuild_summary(root, tmp_path / "g1-out")

    g2 = _artifacts(tmp_path, "G2_EVALUATION", identity)
    root2 = tmp_path / "g2"
    bundle.build_bundle(
        root2,
        bundle_kind="G2_EVALUATION",
        bundle_id="fixture",
        artifacts=g2,
        identity=identity,
        attachments=_attachments(tmp_path),
    )
    rebuilt = bundle.rebuild_summary(root2, tmp_path / "g2-out")
    assert rebuilt["status"] == "REBUILT"


def test_verified_g1_promotes_to_g2_and_rebuilds_in_an_empty_directory(tmp_path):
    identity = _identity()
    g1_root = tmp_path / "g1-source"
    bundle.build_bundle(
        g1_root,
        bundle_kind="G1_SIGNAL",
        bundle_id="fixture",
        artifacts=_artifacts(tmp_path, "G1_SIGNAL", identity),
        identity=identity,
        attachments=_attachments(tmp_path),
    )
    report = {
        "dates": ["2026-10-16", "2026-10-19"],
        "gross_return": [0.02, -0.01],
        "cost": [0.001, 0.002],
        "benchmark_return": [0.01, -0.005],
        "account": [101_900_000.0, 100_677_200.0],
        "turnover": [0.1, 0.2],
    }
    selection_inputs = {
        "baseline": [{"market": "csi1000", "spine": "A", "cell": "S01"}],
        "overlay_s01": [],
        "overlay_best": [],
        "periodic": [],
    }
    g2_root = tmp_path / "g2-promoted"
    promoted = bundle.build_evaluation_bundle(
        g2_root,
        g1_bundle_root=g1_root,
        report_payload=report,
        ledger_payload={"initial_cash": 100_000_000.0},
        execution_contract_sha256="5" * 64,
        selection_inputs=selection_inputs,
    )
    assert promoted["bundle_kind"] == "G2_EVALUATION"
    summary, verified = bundle.load_verified_summary(g2_root)
    assert summary["selection_inputs"]["baseline"][0]["market"] == "csi1000"
    assert summary["selection_inputs"]["baseline"][0]["strategy_cagr"] != 99.0
    assert verified["identity"]["signal_sha256"] == identity["signal_sha256"]
    assert verified["identity"]["assembly_receipt_sha256"] == identity["assembly_receipt_sha256"]
    assert verified["identity"]["candidate_id"] == "5" * 64
    assert verified["identity"]["strategy_contract_sha256"] == "5" * 64
    assert verified["identity"]["bundle_id"] != identity["bundle_id"]
    empty_output = tmp_path / "empty-output"
    assert bundle.rebuild_summary(g2_root, empty_output)["status"] == "REBUILT"
    assert (empty_output / "summary.json").is_file()


def test_verified_g2_can_spawn_an_overlay_g2_with_distinct_evaluation_identity(tmp_path):
    identity = _identity()
    g1_root = tmp_path / "g1-source"
    bundle.build_bundle(
        g1_root,
        bundle_kind="G1_SIGNAL",
        bundle_id=identity["bundle_id"],
        artifacts=_artifacts(tmp_path, "G1_SIGNAL", identity),
        identity=identity,
        attachments=_attachments(tmp_path),
    )
    report = {
        "dates": ["2026-10-16", "2026-10-19"],
        "gross_return": [0.02, -0.01],
        "cost": [0.001, 0.002],
        "benchmark_return": [0.01, -0.005],
        "account": [101_900_000.0, 100_677_200.0],
        "turnover": [0.1, 0.2],
    }
    baseline_root = tmp_path / "baseline"
    bundle.build_evaluation_bundle(
        baseline_root,
        g1_bundle_root=g1_root,
        report_payload=report,
        ledger_payload={"initial_cash": 100_000_000.0},
        execution_contract_sha256="5" * 64,
        selection_inputs={
            "baseline": [{"market": "csi1000", "spine": "A", "cell": "S01"}],
            "overlay_s01": [],
            "overlay_best": [],
            "periodic": [],
        },
    )
    overlay_root = tmp_path / "overlay"
    overlay = bundle.build_evaluation_bundle(
        overlay_root,
        g1_bundle_root=baseline_root,
        report_payload=report,
        ledger_payload={"initial_cash": 100_000_000.0},
        execution_contract_sha256="6" * 64,
        selection_inputs={
            "baseline": [],
            "overlay_s01": [{"market": "csi1000", "overlay": "S16", "base": "S01/A"}],
            "overlay_best": [],
            "periodic": [],
        },
    )
    assert overlay["identity"]["candidate_id"] == "6" * 64
    assert overlay["identity"]["strategy_contract_sha256"] == "6" * 64
    assert overlay["identity"]["signal_sha256"] == identity["signal_sha256"]
    assert overlay["verified_roles"]["strategy"]["payload"]["candidate_id"] == "6" * 64


def test_signal_attachment_bytes_are_verified(tmp_path):
    identity = _identity()
    root = tmp_path / "g1-attached"
    bundle.build_bundle(
        root,
        bundle_kind="G1_SIGNAL",
        bundle_id="fixture",
        artifacts=_artifacts(tmp_path, "G1_SIGNAL", identity),
        identity=identity,
        attachments=_attachments(tmp_path),
    )
    manifest = json.loads((root / "bundle.json").read_text())
    signal_path = root / manifest["artifacts"]["signal"]["attachment"]["path"]
    signal_path.write_bytes(b"changed signal")
    with pytest.raises(ValueError, match="signal attachment"):
        bundle.verify_bundle(root)


def test_semantically_empty_role_payloads_are_rejected(tmp_path):
    """AR02: a valid wrapper must not turn an empty business payload into evidence."""
    identity = _identity()
    artifacts = _artifacts(tmp_path, "G1_SIGNAL", identity)
    for path in artifacts.values():
        wrapper = json.loads(path.read_text())
        wrapper["payload"] = {}
        path.write_text(json.dumps(wrapper, sort_keys=True, allow_nan=False))
    with pytest.raises(ValueError, match="payload"):
        bundle.build_bundle(
            tmp_path / "empty-payload-bundle",
            bundle_kind="G1_SIGNAL",
            bundle_id="fixture",
            artifacts=artifacts,
            identity=identity,
            attachments=_attachments(tmp_path),
        )


def test_rebuild_rejects_false_dates_and_daily_net_even_when_totals_match(tmp_path):
    """AR02: the declared daily series is part of the canonical summary."""
    identity = _identity()
    artifacts = _artifacts(tmp_path, "G2_EVALUATION", identity)
    root = tmp_path / "g2-daily-tamper"
    bundle.build_bundle(
        root,
        bundle_kind="G2_EVALUATION",
        bundle_id="fixture",
        artifacts=artifacts,
        identity=identity,
        attachments=_attachments(tmp_path),
    )
    summary_path = root / "artifacts" / "summary" / "summary.json"
    summary = json.loads(summary_path.read_text())
    summary["payload"]["dates"] = ["2099-01-01"]
    summary["payload"]["daily_net_return"] = [999.0]
    summary_path.write_text(json.dumps(summary, sort_keys=True, allow_nan=False))
    manifest_path = root / "bundle.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["artifacts"]["summary"]["sha256"] = bundle._sha256(summary_path)
    manifest = bundle._seal(manifest)
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, allow_nan=False))
    with pytest.raises(ValueError, match="summary does not match"):
        bundle.rebuild_summary(root, tmp_path / "rebuilt")


def test_selection_metrics_must_be_rebuilt_from_the_verified_report(tmp_path):
    identity = _identity()
    g1_root = tmp_path / "selection-metrics-g1"
    bundle.build_bundle(
        g1_root,
        bundle_kind="G1_SIGNAL",
        bundle_id=identity["bundle_id"],
        artifacts=_artifacts(tmp_path, "G1_SIGNAL", identity),
        identity=identity,
        attachments=_attachments(tmp_path),
    )
    g2_root = tmp_path / "selection-metrics-g2"
    bundle.build_evaluation_bundle(
        g2_root,
        g1_bundle_root=g1_root,
        report_payload={
            "dates": ["2026-10-16", "2026-10-19"],
            "gross_return": [0.02, -0.01],
            "cost": [0.001, 0.002],
            "benchmark_return": [0.01, -0.005],
            "account": [101_900_000.0, 100_677_200.0],
            "turnover": [0.1, 0.2],
        },
        ledger_payload={"initial_cash": 100_000_000.0},
        execution_contract_sha256="5" * 64,
        selection_inputs={
            "baseline": [{"market": "csi1000", "spine": "A", "cell": "S01"}],
            "overlay_s01": [],
            "overlay_best": [],
            "periodic": [],
        },
    )
    manifest_path = g2_root / "bundle.json"
    manifest = json.loads(manifest_path.read_text())
    for role in ("report", "summary"):
        role_path = g2_root / manifest["artifacts"][role]["path"]
        payload = json.loads(role_path.read_text())
        payload["payload"]["selection_inputs"]["baseline"][0]["strategy_cagr"] = 99.0
        role_path.write_text(json.dumps(payload, sort_keys=True, allow_nan=False))
        manifest["artifacts"][role]["sha256"] = bundle._sha256(role_path)
    manifest = bundle._seal(manifest)
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, allow_nan=False))
    with pytest.raises(ValueError, match="selection inputs"):
        bundle.verify_bundle(g2_root)


def test_selection_entry_requires_and_consumes_a_verified_g2_bundle():
    """AR03: selection must not silently fall back to fixed unverified summaries."""
    source = (Path(__file__).resolve().parents[1] / "code/research/regime_reselection/select_per_market.py").read_text()
    tree = ast.parse(source)
    option = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_argument"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "--g2-bundle"
    )
    required = next(keyword.value for keyword in option.keywords if keyword.arg == "required")
    assert isinstance(required, ast.Constant) and required.value is True
    assert 'root / "g1_baseline_summary.json"' not in source
    assert 'root / "g1_overlay_summary_s01.json"' not in source
    baseline_source = (
        Path(__file__).resolve().parents[1] / "code/research/regime_reselection/run_g1_baseline.py"
    ).read_text()
    assert "build_evaluation_bundle(" in baseline_source
    overlay_source = (
        Path(__file__).resolve().parents[1] / "code/research/regime_reselection/run_g1_overlays.py"
    ).read_text()
    assert "build_evaluation_bundle(" in overlay_source


def test_selection_runs_only_from_verified_g2_summaries(tmp_path, monkeypatch):
    import importlib

    selector_dir = Path(__file__).resolve().parents[1] / "code/research/regime_reselection"
    if str(selector_dir) not in sys.path:
        sys.path.insert(0, str(selector_dir))
    selector = importlib.import_module("select_per_market")
    bundle_roots = []
    for offset, market in enumerate(("csi300", "csi500", "csi1000"), start=1):
        identity = _identity(bundle_id=f"fixture-{market}", market=market, profile=f"profile-{market}")
        g1_root = tmp_path / market / "g1"
        bundle.build_bundle(
            g1_root,
            bundle_kind="G1_SIGNAL",
            bundle_id=identity["bundle_id"],
            artifacts=_artifacts(tmp_path / market, "G1_SIGNAL", identity),
            identity=identity,
            attachments=_attachments(tmp_path / market),
        )
        row = {
            "market": market,
            "spine": "A",
            "cell": "S01",
            "benchmark_cagr": 0.02,
            "benchmark_max_drawdown": -0.30,
            "strategy_cagr": 0.12 + offset / 100,
            "strategy_max_drawdown": -0.15,
            "sharpe": 1.1,
            "mean_turnover": 0.05,
            "cagr_cost_plus_10bp": 0.11,
            "maxdd_cost_plus_10bp": -0.16,
            "sharpe_cost_plus_10bp": 1.0,
            "cagr_cost_plus_20bp": 0.10,
        }
        selection_inputs = {"baseline": [row], "overlay_s01": [], "overlay_best": [], "periodic": []}
        g2_root = tmp_path / market / "g2"
        bundle.build_evaluation_bundle(
            g2_root,
            g1_bundle_root=g1_root,
            report_payload={
                "dates": ["2026-10-16", "2026-10-19"],
                "gross_return": [0.02, -0.01],
                "cost": [0.001, 0.002],
                "benchmark_return": [0.01, -0.005],
                "account": [101_900_000.0, 100_677_200.0],
                "turnover": [0.1, 0.2],
            },
            ledger_payload={"initial_cash": 100_000_000.0},
            execution_contract_sha256=str(offset) * 64,
            selection_inputs=selection_inputs,
        )
        bundle_roots.append(g2_root)
    output = tmp_path / "selection.json"
    argv = ["select_per_market.py", "--out", str(output)]
    for root in bundle_roots:
        argv.extend(("--g2-bundle", str(root)))
    monkeypatch.setattr(sys, "argv", argv)
    selector.main()
    result = json.loads(output.read_text())
    assert set(result["markets"]) == {"csi300", "csi500", "csi1000"}
    assert all(result["markets"][market]["champion"] == "S01/A" for market in result["markets"])


@pytest.mark.parametrize("role", list(bundle.G1_SIGNAL_ROLES))
def test_arbitrary_role_payload_is_rejected(tmp_path, role):
    identity = _identity()
    artifacts = _artifacts(tmp_path, "G1_SIGNAL", identity)
    path = tmp_path / "roles" / f"{role}.json"
    path.write_text("{}")
    with pytest.raises(ValueError, match="schema"):
        bundle.build_bundle(
            tmp_path / "bundle",
            bundle_kind="G1_SIGNAL",
            bundle_id="fixture",
            artifacts=artifacts,
            identity=identity,
            attachments=_attachments(tmp_path),
        )


@pytest.mark.parametrize("field", bundle.COMMON_IDENTITY_KEYS)
def test_cross_role_identity_mismatch_is_rejected(tmp_path, field):
    identity = _identity()
    artifacts = _artifacts(tmp_path, "G1_SIGNAL", identity)
    wrong = dict(identity)
    if field == "window_start":
        wrong[field] = "2026-10-15"
    elif field == "window_end":
        wrong[field] = "2026-10-21"
    else:
        wrong[field] = "9" * 64 if field in bundle.SHA_IDENTITY_KEYS else "other"
    signal_payload = json.loads(artifacts["signal"].read_text())["payload"]
    artifacts["signal"] = _role(tmp_path, "signal", wrong, signal_payload)
    with pytest.raises(ValueError, match="identity mismatch"):
        bundle.build_bundle(
            tmp_path / "bundle",
            bundle_kind="G1_SIGNAL",
            bundle_id="fixture",
            artifacts=artifacts,
            identity=identity,
            attachments=_attachments(tmp_path),
        )


def test_missing_kind_role_and_bad_status_are_rejected(tmp_path):
    identity = _identity()
    artifacts = _artifacts(tmp_path, "G1_SIGNAL", identity)
    missing = dict(artifacts)
    missing.pop("signal")
    with pytest.raises(ValueError, match="required roles|exactly"):
        bundle.build_bundle(
            tmp_path / "missing",
            bundle_kind="G1_SIGNAL",
            bundle_id="fixture",
            artifacts=missing,
            identity=identity,
            attachments=_attachments(tmp_path),
        )
    artifacts["signal"] = _role(tmp_path, "signal", identity, status="FAILED")
    with pytest.raises(ValueError, match="not PASS"):
        bundle.build_bundle(
            tmp_path / "failed",
            bundle_kind="G1_SIGNAL",
            bundle_id="fixture",
            artifacts=artifacts,
            identity=identity,
            attachments=_attachments(tmp_path),
        )


def test_summary_mismatch_and_symlink_escape_are_rejected(tmp_path):
    identity = _identity()
    g2 = _artifacts(tmp_path, "G2_EVALUATION", identity)
    root = tmp_path / "g2"
    bundle.build_bundle(
        root,
        bundle_kind="G2_EVALUATION",
        bundle_id="fixture",
        artifacts=g2,
        identity=identity,
        attachments=_attachments(tmp_path),
    )
    summary_path = root / "artifacts" / "summary" / "summary.json"
    summary = json.loads(summary_path.read_text())
    summary["payload"]["strategy_total_return_net"] = 0.5
    summary_path.write_text(json.dumps(summary, sort_keys=True, allow_nan=False))
    manifest = json.loads((root / "bundle.json").read_text())
    manifest["artifacts"]["summary"]["sha256"] = bundle._sha256(summary_path)
    manifest["manifest_sha256"] = bundle._seal(manifest)["manifest_sha256"]
    (root / "bundle.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="summary does not match"):
        bundle.rebuild_summary(root, tmp_path / "out")

    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    link = root / "artifacts" / "signal" / "outside.json"
    link.symlink_to(outside)
    manifest = json.loads((root / "bundle.json").read_text())
    manifest["artifacts"]["signal"] = {"path": "artifacts/signal/outside.json", "sha256": "a" * 64}
    manifest["manifest_sha256"] = bundle._seal(manifest)["manifest_sha256"]
    (root / "bundle.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="escapes"):
        bundle.verify_bundle(root)


def test_legacy_inventory_keeps_original_bytes_and_cannot_be_certified(tmp_path):
    repo = tmp_path / "repo"
    artifact = repo / "quant" / "data" / "legacy.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text('{"old": 1}\n')
    inventory = {
        "schema_version": 1,
        "artifacts": [
            {
                "path": "quant/data/legacy.json",
                "sha256": __import__("hashlib").sha256(artifact.read_bytes()).hexdigest(),
                "status": "LEGACY_MIXED_SOURCE_UNCERTIFIED",
                "certified": False,
            }
        ],
    }
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(inventory))
    assert bundle.verify_legacy_inventory(path, repo_root=repo)["artifacts"][0]["certified"] is False
    inventory["artifacts"][0]["certified"] = True
    path.write_text(json.dumps(inventory))
    with pytest.raises(ValueError, match="cannot be certified"):
        bundle.verify_legacy_inventory(path, repo_root=repo)
    inventory["artifacts"][0]["certified"] = False
    artifact.write_text('{"old": 2}\n')
    path.write_text(json.dumps(inventory))
    with pytest.raises(ValueError, match="bytes changed"):
        bundle.verify_legacy_inventory(path, repo_root=repo)


def test_repository_legacy_inventory_is_uncertified_and_bytes_stable():
    root = Path(__file__).resolve().parents[2]
    inventory = root / "quant/data/research_evidence/legacy_inventory.json"
    payload = bundle.verify_legacy_inventory(inventory, repo_root=root)
    assert {item["status"] for item in payload["artifacts"]} >= {
        "LEGACY_MIXED_SOURCE_UNCERTIFIED",
        "LEGACY_INCOMPLETE_EVIDENCE",
        "GROSS_ONLY_WITHDRAWN",
    }
    assert all(item["certified"] is False for item in payload["artifacts"])
