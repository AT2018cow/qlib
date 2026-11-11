"""Machine-verifiable strategy release manifests and status gates."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

SCHEMA_VERSION = 1
CANONICAL_CONTRACT_VERSION = "cn_equity_strategy_contract_v1"
RELEASE_DIR = "quant/data/strategy_releases"
MARKETS = ("csi300", "csi500", "csi1000")
STATUSES = ("GATE_PASS", "RISK_ACCEPTED_OVERRIDE", "INCUMBENT_RETAINED")
REQUIRED_CONTRACT_FIELDS = (
    "contract_version",
    "market",
    "profile",
    "candidate_id",
    "model_config_sha256",
    "label_expression",
    "label_horizon",
    "pit_universe",
    "topk",
    "dropn",
    "retrain_every_sessions",
    "execution_timing",
    "board_limits",
    "listing_exemptions",
    "lot_rule",
    "liquidity_policy",
    "fees",
    "initial_cash",
    "account_start",
)


def _require_sha256(value, label):
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(f"{label} must be a lowercase SHA256")
    return value


def _execution_rule_contract():
    from board_rules import board_rule_contract
    from liquidity_contract import liquidity_rule_contract
    from order_quantity import quantity_rule_contract

    return {
        "board": board_rule_contract(),
        "quantity": quantity_rule_contract(),
        "liquidity": liquidity_rule_contract(),
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative_file(repo_root: Path, relative) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError("release evidence path must be a repository-relative string")
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("release evidence path must stay inside the repository")
    root = repo_root.resolve()
    full = (root / path).resolve()
    if full != root and root not in full.parents:
        raise ValueError("release evidence path must resolve inside the repository root")
    if not full.is_file():
        raise ValueError(f"release evidence file is missing: {relative}")
    return full


def _finite_number(value, label, *, positive=False, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"{label} must be finite")
    if positive and number <= 0:
        raise ValueError(f"{label} must be positive")
    if nonnegative and number < 0:
        raise ValueError(f"{label} must be non-negative")
    return number


def canonical_strategy_contract(manifest: dict) -> dict:
    """Canonical business contract; excludes docs, schedules and resources."""
    market = manifest.get("market")
    if market not in MARKETS:
        raise ValueError("strategy contract market is invalid")
    if manifest.get("contract_version") != CANONICAL_CONTRACT_VERSION:
        raise ValueError("strategy contract version is missing or unsupported")
    _require_sha256(manifest.get("candidate_id"), "candidate_id")
    _require_sha256(manifest.get("model_config_sha256"), "model_config_sha256")
    if not isinstance(manifest.get("profile"), str) or not manifest["profile"]:
        raise ValueError("strategy contract profile is missing")
    horizon = manifest.get("label_horizon")
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 1:
        raise ValueError("label_horizon must be a positive integer")
    topk = manifest.get("topk")
    dropn = manifest.get("dropn")
    if isinstance(topk, bool) or not isinstance(topk, int) or topk <= 0:
        raise ValueError("topk must be a positive integer")
    if isinstance(dropn, bool) or not isinstance(dropn, int) or not 0 <= dropn <= topk:
        raise ValueError("dropn must be between zero and topk")
    fees = manifest.get("fees")
    if not isinstance(fees, dict):
        raise ValueError("fees must be an object")
    open_cost = _finite_number(fees.get("open_cost"), "open_cost", nonnegative=True)
    close_cost = _finite_number(fees.get("close_cost"), "close_cost", nonnegative=True)
    min_cost = _finite_number(fees.get("min_cost"), "min_cost", nonnegative=True)
    initial_cash = _finite_number(manifest.get("initial_cash"), "initial_cash", positive=True)
    if manifest.get("execution_timing") not in ("T_CLOSE_TO_T1_OPEN",):
        raise ValueError("unknown execution timing")
    if manifest.get("retrain_every_sessions") != 20:
        raise ValueError("retrain_every_sessions must be 20")
    label_expression = manifest.get("label_expression")
    expected_label = f"Ref($close, -{horizon})/$close - 1"
    if label_expression != expected_label:
        raise ValueError("label expression does not match horizon")
    for field in ("board_limits", "listing_exemptions", "lot_rule", "liquidity_policy", "account_start"):
        if not isinstance(manifest.get(field), str) or not manifest[field]:
            raise ValueError(f"strategy contract field is missing: {field}")
    try:
        if date.fromisoformat(manifest["account_start"]).isoformat() != manifest["account_start"]:
            raise ValueError
    except ValueError as exc:
        raise ValueError("strategy contract account_start must be an ISO date") from exc
    if manifest.get("pit_universe") != market:
        raise ValueError("pit universe must match market")
    rules = _execution_rule_contract()
    expected_versions = {
        "board_limits": rules["board"]["board_limit_version"],
        "listing_exemptions": rules["board"]["listing_exemption_version"],
        "lot_rule": rules["quantity"]["lot_rule_version"],
        "liquidity_policy": rules["liquidity"]["liquidity_policy_version"],
    }
    for field, expected in expected_versions.items():
        if manifest.get(field) != expected:
            raise ValueError(f"strategy contract {field} differs from the executable rule version")
    supplied_parameters = manifest.get("board_limit_parameters")
    if supplied_parameters is not None and supplied_parameters != rules["board"]:
        raise ValueError("strategy contract board limit parameters differ from executable rules")
    return {
        "schema_version": 1,
        "contract_version": CANONICAL_CONTRACT_VERSION,
        "market": market,
        "profile": manifest.get("profile"),
        "candidate_id": manifest.get("candidate_id"),
        "model_config_sha256": manifest.get("model_config_sha256"),
        "label": {"expression": label_expression, "horizon": horizon},
        "pit_universe": manifest.get("pit_universe"),
        "selection": {"topk": topk, "dropn": dropn},
        "retrain": {"every_sessions": manifest.get("retrain_every_sessions")},
        "execution": {
            "timing": manifest.get("execution_timing"),
            "board_limits": manifest.get("board_limits"),
            "listing_exemptions": manifest.get("listing_exemptions"),
            "lot_rule": manifest.get("lot_rule"),
            "liquidity_policy": manifest.get("liquidity_policy"),
            "open_cost": open_cost,
            "close_cost": close_cost,
            "min_cost": min_cost,
            "initial_cash": initial_cash,
            "account_start": manifest.get("account_start"),
            "rules": rules,
        },
    }


def strategy_contract(manifest: dict) -> dict:
    return canonical_strategy_contract(manifest)


def strategy_contract_sha256(manifest: dict) -> str:
    return hashlib.sha256(
        json.dumps(strategy_contract(manifest), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _seal(manifest: dict) -> dict:
    body = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    return {
        **body,
        "manifest_sha256": hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest(),
    }


def _evidence(repo_root: Path, entry, label):
    if not isinstance(entry, dict):
        raise ValueError(f"{label} evidence is missing")
    path = _relative_file(repo_root, entry.get("path"))
    expected = entry.get("sha256")
    if not isinstance(expected, str) or len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
        raise ValueError(f"{label} evidence SHA256 is missing")
    if _sha256(path) != expected:
        raise ValueError(f"{label} evidence SHA256 changed")
    return entry


def validate_release(manifest, *, repo_root, expected_market=None, expected_profile=None):
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("release manifest schema is missing or unsupported")
    if manifest.get("market") not in MARKETS:
        raise ValueError("release manifest market is invalid")
    if expected_market is not None and manifest["market"] != expected_market:
        raise ValueError("release manifest market mismatch")
    if expected_profile is not None and manifest.get("profile") != expected_profile:
        raise ValueError("release manifest profile mismatch")
    if manifest.get("status") not in STATUSES:
        raise ValueError("release manifest status is invalid")
    for field in REQUIRED_CONTRACT_FIELDS:
        if manifest.get(field) is None:
            raise ValueError(f"release manifest contract field is missing: {field}")
    if strategy_contract_sha256(manifest) != manifest.get("strategy_contract_sha256"):
        raise ValueError("release strategy contract hash changed")
    _evidence(repo_root, manifest.get("research_contract"), "research contract")
    _evidence(repo_root, manifest.get("decision_artifact"), "decision artifact")
    status = manifest["status"]
    if status == "GATE_PASS":
        gate = manifest.get("promotion_gate")
        if not isinstance(gate, dict):
            raise ValueError("GATE_PASS requires a promotion gate")
        contract_path = _relative_file(repo_root, gate.get("contract_path"))
        evidence_path = _relative_file(repo_root, gate.get("evidence_path"))
        from research_promotion_gate import check_bundle

        gate_result = check_bundle(contract_path, evidence_path)
        if (
            gate_result.get("promotion_eligible") is not True
            or gate_result.get("failed_gates")
            or gate_result.get("status") != "READY_FOR_INDEPENDENT_REVIEW"
        ):
            raise ValueError("promotion gate is not eligible")
        bundle_entry = manifest.get("evidence_bundle")
        _evidence(repo_root, bundle_entry, "evidence bundle")
        from research_bundle import load_verified_summary

        bundle_root = _relative_file(repo_root, bundle_entry["path"]).parent
        _summary, bundle = load_verified_summary(bundle_root)
        if bundle.get("bundle_kind") != "PROMOTION":
            raise ValueError("GATE_PASS requires a PROMOTION bundle")
        identity = bundle.get("identity") or {}
        for key, expected in (
            ("market", manifest.get("market")),
            ("profile", manifest.get("profile")),
            ("candidate_id", manifest.get("candidate_id")),
            ("model_sha256", manifest.get("model_config_sha256")),
            ("strategy_contract_sha256", manifest.get("strategy_contract_sha256")),
        ):
            if identity.get(key) != expected:
                raise ValueError(f"promotion bundle identity differs: {key}")
        for key in (
            "candidate_id",
            "market",
            "profile",
            "source_sha256",
            "environment_sha256",
            "provider_fingerprint",
            "window_start",
            "window_end",
        ):
            if gate_result.get(key) != identity.get(key):
                raise ValueError(f"promotion gate differs from bundle identity: {key}")
        roles = bundle["verified_roles"]
        preregistration = roles["preregistration"]["payload"]
        evidence = roles["promotion_evidence"]["payload"]
        gate_payload = roles["gate"]["payload"]
        release_candidate = roles["release_candidate"]["payload"]
        if preregistration["contract_sha256"] != gate_result["contract_sha256"]:
            raise ValueError("promotion preregistration differs from the validated contract")
        if preregistration["registration_receipt_sha256"] != gate_result["registration_receipt_sha256"]:
            raise ValueError("promotion preregistration receipt differs from the validated receipt")
        if evidence["evidence_sha256"] != gate_result["evidence_sha256"]:
            raise ValueError("promotion evidence role differs from the validated evidence")
        if (
            gate_payload["contract_sha256"] != gate_result["contract_sha256"]
            or gate_payload["evidence_sha256"] != gate_result["evidence_sha256"]
        ):
            raise ValueError("promotion gate role differs from validated evidence")
        if (
            evidence["forward_dates"][0] != identity["window_start"]
            or evidence["forward_dates"][-1] != identity["window_end"]
        ):
            raise ValueError("promotion forward window differs from bundle identity")
        if release_candidate["candidate_id"] != manifest["candidate_id"]:
            raise ValueError("promotion release candidate differs from release manifest")
    elif status == "RISK_ACCEPTED_OVERRIDE":
        if not isinstance(manifest.get("reason"), str) or not manifest["reason"].strip():
            raise ValueError("override release requires a reason")
        if not isinstance(manifest.get("scope"), str) or not manifest["scope"].strip():
            raise ValueError("override release requires a scope")
        gaps = manifest.get("known_gaps")
        if not isinstance(gaps, list) or not gaps:
            raise ValueError("override release requires known gaps")
        if not isinstance(manifest.get("authority"), str) or not manifest["authority"].strip():
            raise ValueError("override release requires an authority role")
    elif status == "INCUMBENT_RETAINED":
        if not isinstance(manifest.get("incumbent_since"), str):
            raise ValueError("incumbent release requires incumbent_since")
    if manifest.get("manifest_sha256") != _seal(manifest)["manifest_sha256"]:
        raise ValueError("release manifest self hash changed")
    return manifest


def load_release_file(path, *, repo_root, expected_market=None, expected_profile=None):
    path = Path(path)
    try:
        manifest = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(f"release manifest unreadable: {path}") from exc
    return validate_release(
        manifest,
        repo_root=repo_root,
        expected_market=expected_market,
        expected_profile=expected_profile,
    )


def canonical_release_map(repo_root=None):
    repo_root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[3]
    root = repo_root / RELEASE_DIR
    if not root.is_dir():
        raise ValueError(f"strategy release directory is missing: {RELEASE_DIR}")
    releases = {}
    for path in sorted(root.glob("*.json")):
        manifest = load_release_file(path, repo_root=repo_root)
        market = manifest["market"]
        if market in releases:
            raise ValueError(f"duplicate strategy release for {market}")
        releases[market] = manifest
    if set(releases) != set(MARKETS):
        raise ValueError("strategy releases must cover exactly csi300/csi500/csi1000")
    return releases


def release_for(market, profile, repo_root=None):
    releases = canonical_release_map(repo_root)
    manifest = releases.get(market)
    if manifest is None or manifest.get("profile") != profile:
        raise ValueError(f"no registered strategy release for {market}/{profile}")
    return manifest
