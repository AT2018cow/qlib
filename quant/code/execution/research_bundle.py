"""Semantic, content-addressed research evidence bundles."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from datetime import date
from pathlib import Path

SCHEMA_VERSION = 2
GENERATOR_VERSION = "research_bundle_v2"
COMMON_IDENTITY_KEYS = (
    "bundle_id",
    "market",
    "profile",
    "candidate_id",
    "window_start",
    "window_end",
    "source_sha256",
    "environment_sha256",
    "provider_fingerprint",
    "model_sha256",
    "strategy_contract_sha256",
    "calendar_sha256",
    "signal_sha256",
    "assembly_receipt_sha256",
)
G1_SIGNAL_ROLES = (
    "research_contract",
    "source_environment",
    "provider",
    "calendar",
    "model",
    "strategy",
    "slice_receipts",
    "assembly_receipt",
    "signal",
)
G2_EVALUATION_ROLES = G1_SIGNAL_ROLES + ("execution", "report", "ledger", "summary", "cost", "account")
PROMOTION_ROLES = G2_EVALUATION_ROLES + ("preregistration", "promotion_evidence", "gate", "release_candidate")
BUNDLE_ROLES = {
    "G1_SIGNAL": G1_SIGNAL_ROLES,
    "G2_EVALUATION": G2_EVALUATION_ROLES,
    "PROMOTION": PROMOTION_ROLES,
}
ROLE_KIND = {role: role.upper() for roles in BUNDLE_ROLES.values() for role in roles}
LEGACY_STATUSES = (
    "RETAINED",
    "WITHDRAWN",
    "CORRECTED_DERIVATIVE",
    "LEGACY_UNCERTIFIED",
    "LEGACY_MIXED_SOURCE_UNCERTIFIED",
    "LEGACY_INCOMPLETE_EVIDENCE",
    "GROSS_ONLY_WITHDRAWN",
)
SHA_IDENTITY_KEYS = (
    "candidate_id",
    "source_sha256",
    "environment_sha256",
    "provider_fingerprint",
    "model_sha256",
    "strategy_contract_sha256",
    "calendar_sha256",
    "signal_sha256",
    "assembly_receipt_sha256",
)


def _require_sha(value, label):
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{label} must be a lowercase SHA256")
    return value


def _require_string(value, label):
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _require_number(value, label, *, positive=False, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{label} must be a finite number")
    number = float(value)
    if positive and number <= 0:
        raise ValueError(f"{label} must be positive")
    if nonnegative and number < 0:
        raise ValueError(f"{label} must be non-negative")
    return number


def _require_dates(values, label):
    if not isinstance(values, list) or not values:
        raise ValueError(f"{label} must be a non-empty date list")
    try:
        normalized = [date.fromisoformat(value).isoformat() for value in values]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} contains an invalid date") from exc
    if normalized != values or normalized != sorted(set(normalized)):
        raise ValueError(f"{label} must be sorted and unique")
    return normalized


def _validate_identity(identity):
    if not isinstance(identity, dict) or set(identity) != set(COMMON_IDENTITY_KEYS):
        raise ValueError("bundle identity fields differ from the canonical schema")
    for key in ("bundle_id", "market", "profile"):
        _require_string(identity.get(key), f"bundle identity {key}")
    for key in SHA_IDENTITY_KEYS:
        _require_sha(identity.get(key), f"bundle identity {key}")
    start = date.fromisoformat(_require_string(identity.get("window_start"), "bundle identity window_start"))
    end = date.fromisoformat(_require_string(identity.get("window_end"), "bundle identity window_end"))
    if start > end:
        raise ValueError("bundle identity window is reversed")
    return identity


def _validate_report_payload(payload, identity):
    dates = _require_dates(payload.get("dates"), "report payload dates")
    if dates[0] < identity["window_start"] or dates[-1] > identity["window_end"]:
        raise ValueError("report payload dates escape the bundle window")
    for key in ("gross_return", "cost", "benchmark_return", "account", "turnover"):
        values = payload.get(key)
        if not isinstance(values, list) or len(values) != len(dates):
            raise ValueError(f"report payload {key} length differs from dates")
        for value in values:
            _require_number(
                value,
                f"report payload {key}",
                positive=key == "account",
                nonnegative=key in ("cost", "turnover"),
            )
    inputs = payload.get("selection_inputs")
    if inputs is not None:
        if not isinstance(inputs, dict) or set(inputs) != {"baseline", "overlay_s01", "overlay_best", "periodic"}:
            raise ValueError("report payload selection_inputs schema is invalid")
        if any(not isinstance(rows, list) for rows in inputs.values()):
            raise ValueError("report payload selection_inputs groups must be lists")
        rows = [row for group in inputs.values() for row in group]
        if len(rows) > 1 or any(not isinstance(row, dict) or not row for row in rows):
            raise ValueError("one evaluation bundle may contain at most one non-empty selection candidate")


def _canonical_selection_inputs(report, ledger, identity, performance):
    inputs = report.get("selection_inputs")
    if inputs is None:
        return None
    groups = {"baseline": [], "overlay_s01": [], "overlay_best": [], "periodic": []}
    rows = [(group, row) for group, values in inputs.items() for row in values]
    if not rows:
        return groups
    group, row = rows[0]
    required = {
        "baseline": ("market", "spine", "cell"),
        "periodic": ("market", "spine", "cell", "cadence"),
        "overlay_s01": ("market", "overlay", "base"),
        "overlay_best": ("market", "overlay", "base"),
    }[group]
    metadata = {}
    for key in required:
        metadata[key] = _require_string(row.get(key), f"selection candidate {key}")
    if metadata["market"] != identity["market"]:
        raise ValueError("selection candidate market differs from the bundle identity")
    if group.startswith("overlay_") and row.get("policy_base") is not None:
        metadata["policy_base"] = _require_string(row["policy_base"], "selection candidate policy_base")
    candidate = {
        **metadata,
        "strategy_cagr": performance["strategy_cagr"],
        "strategy_max_drawdown": performance["strategy_max_drawdown"],
        "sharpe": performance["sharpe"],
        "benchmark_cagr": performance["benchmark_cagr"],
        "benchmark_max_drawdown": performance["benchmark_max_drawdown"],
        "mean_turnover": round(sum(report["turnover"]) / len(report["turnover"]), 6),
    }
    initial_cash = float(ledger["initial_cash"])
    prior = [initial_cash] + [float(value) for value in report["account"][:-1]]
    for bps in (10, 20):
        adjustment = 0.0
        stressed_account = []
        for account, turnover, previous in zip(report["account"], report["turnover"], prior):
            adjustment += float(turnover) * previous * bps / 10_000.0
            stressed_account.append(float(account) - adjustment)
        if min(stressed_account) <= 0:
            stressed = None
        else:
            stressed_net = []
            for index, account in enumerate(stressed_account):
                previous = initial_cash if index == 0 else stressed_account[index - 1]
                stressed_net.append(account / previous - 1.0)
            import pandas as pd

            stressed_frame = pd.DataFrame(
                {
                    "return": stressed_net,
                    "cost": [0.0] * len(stressed_net),
                    "bench": report["benchmark_return"],
                    "account": stressed_account,
                },
                index=pd.to_datetime(report["dates"]),
            )
            from portfolio_performance import portfolio_performance

            stressed = portfolio_performance(
                stressed_frame,
                initial_cash=initial_cash,
                backtest_start=report["dates"][0],
            )
        candidate[f"cagr_cost_plus_{bps}bp"] = None if stressed is None else stressed["strategy_cagr"]
        candidate[f"maxdd_cost_plus_{bps}bp"] = None if stressed is None else stressed["strategy_max_drawdown"]
        candidate[f"sharpe_cost_plus_{bps}bp"] = None if stressed is None else stressed["sharpe"]
    groups[group].append(candidate)
    return groups


def _validate_role_payload(role, payload, identity):
    if not isinstance(payload, dict) or not payload:
        raise ValueError(f"bundle role payload is empty or invalid: {role}")
    if role == "research_contract":
        _require_string(payload.get("contract_version"), "research contract version")
        if (
            _require_sha(payload.get("assembly_receipt_sha256"), "research contract assembly receipt")
            != identity["assembly_receipt_sha256"]
        ):
            raise ValueError("research contract assembly receipt differs from bundle identity")
    elif role == "source_environment":
        for key in ("source_sha256", "environment_sha256"):
            if _require_sha(payload.get(key), key) != identity[key]:
                raise ValueError(f"source/environment payload differs: {key}")
    elif role == "provider":
        _require_string(payload.get("provider_kind"), "provider kind")
        if _require_sha(payload.get("fingerprint"), "provider fingerprint") != identity["provider_fingerprint"]:
            raise ValueError("provider payload differs from bundle identity")
    elif role == "calendar":
        if _require_sha(payload.get("calendar_sha256"), "calendar SHA") != identity["calendar_sha256"]:
            raise ValueError("calendar payload differs from bundle identity")
        dates = _require_dates(payload.get("dates"), "calendar payload dates")
        if dates[0] != identity["window_start"] or dates[-1] != identity["window_end"]:
            raise ValueError("calendar payload window differs from bundle identity")
    elif role == "model":
        if _require_sha(payload.get("model_sha256"), "model SHA") != identity["model_sha256"]:
            raise ValueError("model payload differs from bundle identity")
    elif role == "strategy":
        if (
            _require_sha(payload.get("strategy_contract_sha256"), "strategy contract SHA")
            != identity["strategy_contract_sha256"]
        ):
            raise ValueError("strategy payload differs from bundle identity")
        if _require_sha(payload.get("candidate_id"), "strategy candidate") != identity["candidate_id"]:
            raise ValueError("strategy candidate differs from bundle identity")
    elif role == "slice_receipts":
        receipts = payload.get("receipt_sha256")
        if not isinstance(receipts, list) or not receipts:
            raise ValueError("slice receipt payload is empty")
        for value in receipts:
            _require_sha(value, "slice receipt SHA")
    elif role == "assembly_receipt":
        if _require_sha(payload.get("receipt_sha256"), "assembly receipt SHA") != identity["assembly_receipt_sha256"]:
            raise ValueError("assembly receipt payload differs from bundle identity")
    elif role == "signal":
        if _require_sha(payload.get("signal_sha256"), "signal SHA") != identity["signal_sha256"]:
            raise ValueError("signal payload differs from bundle identity")
    elif role == "execution":
        _require_sha(payload.get("execution_contract_sha256"), "execution contract SHA")
    elif role == "report":
        _validate_report_payload(payload, identity)
    elif role == "ledger":
        _require_number(payload.get("initial_cash"), "ledger initial_cash", positive=True)
    elif role == "summary":
        for key in (
            "dates",
            "daily_net_return",
            "strategy_total_return_net",
            "total_cost",
            "final_account",
            "account_return_max_error",
            "source_sha256",
            "environment_sha256",
            "provider_fingerprint",
            "model_sha256",
            "strategy_contract_sha256",
        ):
            if key not in payload:
                raise ValueError(f"summary payload is missing: {key}")
        _require_dates(payload["dates"], "summary dates")
        if not isinstance(payload["daily_net_return"], list) or len(payload["daily_net_return"]) != len(
            payload["dates"]
        ):
            raise ValueError("summary daily_net_return length differs from dates")
        for value in payload["daily_net_return"]:
            _require_number(value, "summary daily net return")
    elif role == "cost":
        _require_number(payload.get("total_cost"), "cost total", nonnegative=True)
    elif role == "account":
        _require_number(payload.get("initial_cash"), "account initial_cash", positive=True)
        _require_number(payload.get("final_account"), "account final_account", positive=True)
        _require_number(payload.get("account_return_max_error"), "account return error", nonnegative=True)
    elif role == "preregistration":
        _require_sha(payload.get("contract_sha256"), "preregistration contract SHA")
        _require_sha(payload.get("registration_receipt_sha256"), "preregistration receipt SHA")
        if _require_sha(payload.get("candidate_id"), "preregistration candidate") != identity["candidate_id"]:
            raise ValueError("preregistration candidate differs from bundle identity")
        _require_string(payload.get("registered_at"), "preregistration timestamp")
    elif role == "promotion_evidence":
        _require_sha(payload.get("evidence_sha256"), "promotion evidence SHA")
        if _require_sha(payload.get("candidate_id"), "promotion evidence candidate") != identity["candidate_id"]:
            raise ValueError("promotion evidence candidate differs from bundle identity")
        _require_dates(payload.get("forward_dates"), "promotion forward dates")
    elif role == "gate":
        if payload.get("promotion_eligible") is not True or payload.get("failed_gates") != []:
            raise ValueError("promotion gate payload is not eligible")
        if payload.get("status") != "READY_FOR_INDEPENDENT_REVIEW":
            raise ValueError("promotion gate payload status is invalid")
        _require_sha(payload.get("contract_sha256"), "gate contract SHA")
        _require_sha(payload.get("evidence_sha256"), "gate evidence SHA")
    elif role == "release_candidate":
        for key in ("market", "profile"):
            if payload.get(key) != identity[key]:
                raise ValueError(f"release candidate differs from bundle identity: {key}")
        for key in ("candidate_id", "strategy_contract_sha256"):
            if _require_sha(payload.get(key), f"release candidate {key}") != identity[key]:
                raise ValueError(f"release candidate differs from bundle identity: {key}")
    else:  # pragma: no cover - BUNDLE_ROLES is the closed role registry
        raise ValueError(f"unknown bundle role: {role}")
    return payload


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _content_sha(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _safe_file(root: Path, relative):
    path = Path(str(relative))
    if path.is_absolute() or ".." in path.parts or not str(relative):
        raise ValueError("bundle path must be relative and stay inside the bundle")
    full = (root / path).resolve()
    root_resolved = root.resolve()
    if root_resolved not in full.parents and full != root_resolved:
        raise ValueError("bundle path escapes the bundle root")
    if not full.is_file():
        raise ValueError(f"bundle artifact is missing: {relative}")
    return full


def _seal(manifest):
    body = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    return {**body, "manifest_sha256": _content_sha(body)}


def _load_role(root: Path, relative, role, *, bundle_identity=None):
    path = _safe_file(root, relative)
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(f"bundle role is unreadable: {role}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"bundle role schema is invalid: {role}")
    if payload.get("kind") != ROLE_KIND[role]:
        raise ValueError(f"bundle role kind is invalid: {role}")
    if payload.get("status") != "PASS":
        raise ValueError(f"bundle role is not PASS: {role}")
    identity = payload.get("identity")
    _validate_identity(identity)
    if bundle_identity is not None and identity != bundle_identity:
        raise ValueError(f"bundle role identity mismatch: {role}")
    _validate_role_payload(role, payload.get("payload"), identity)
    return payload


def build_bundle(
    bundle_root,
    *,
    bundle_kind,
    bundle_id,
    artifacts,
    identity,
    attachments=None,
    generator_version=GENERATOR_VERSION,
):
    if bundle_kind not in BUNDLE_ROLES:
        raise ValueError("bundle kind is invalid")
    required = set(BUNDLE_ROLES[bundle_kind])
    if set(artifacts) != required:
        raise ValueError(f"{bundle_kind} bundle artifacts must cover exactly: {sorted(required)}")
    _validate_identity(identity)
    if identity.get("bundle_id") != bundle_id:
        raise ValueError("bundle id differs from identity")
    attachments = dict(attachments or {})
    if set(attachments) != {"signal"}:
        raise ValueError("bundle must attach exactly the immutable signal artifact")
    root = Path(bundle_root)
    root.mkdir(parents=True, exist_ok=True)
    entries = {}
    for role, source in artifacts.items():
        source = Path(source)
        payload = _load_role(source.parent, source.name, role, bundle_identity=identity)
        relative = Path("artifacts") / role / source.name
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        entry = {"path": relative.as_posix(), "sha256": _sha256(target)}
        if role == "signal":
            attachment_source = Path(attachments[role])
            if not attachment_source.is_file():
                raise ValueError("signal attachment is missing")
            attachment_relative = Path("attachments") / "signal" / attachment_source.name
            attachment_target = root / attachment_relative
            attachment_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(attachment_source, attachment_target)
            attachment_sha = _sha256(attachment_target)
            if attachment_sha != payload["payload"]["signal_sha256"]:
                raise ValueError("signal attachment differs from the signal role")
            entry["attachment"] = {"path": attachment_relative.as_posix(), "sha256": attachment_sha}
        entries[role] = entry
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generator_version": generator_version,
        "bundle_kind": bundle_kind,
        "bundle_id": bundle_id,
        "identity": identity,
        "artifacts": entries,
    }
    manifest = _seal(manifest)
    target = root / "bundle.json"
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n")
    temporary.replace(target)
    return verify_bundle(root)


def verify_bundle(bundle_root):
    root = Path(bundle_root)
    path = root / "bundle.json"
    try:
        manifest = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError("bundle manifest is unreadable") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("bundle schema is missing or unsupported")
    if manifest.get("bundle_kind") not in BUNDLE_ROLES:
        raise ValueError("bundle kind is invalid")
    if manifest.get("manifest_sha256") != _seal(manifest)["manifest_sha256"]:
        raise ValueError("bundle manifest self hash changed")
    identity = manifest.get("identity")
    _validate_identity(identity)
    if identity.get("bundle_id") != manifest.get("bundle_id"):
        raise ValueError("bundle id differs from identity")
    required = set(BUNDLE_ROLES[manifest["bundle_kind"]])
    entries = manifest.get("artifacts")
    if not isinstance(entries, dict) or set(entries) != required:
        raise ValueError("bundle artifacts do not match the bundle kind")
    seen_paths = set()
    roles = {}
    for role, entry in entries.items():
        if not isinstance(entry, dict):
            raise ValueError(f"bundle artifact entry is invalid: {role}")
        relative = entry.get("path")
        full = _safe_file(root, relative)
        if relative in seen_paths:
            raise ValueError("bundle artifact path is duplicated")
        seen_paths.add(relative)
        expected = entry.get("sha256")
        _require_sha(expected, f"bundle artifact hash: {role}")
        if _sha256(full) != expected:
            raise ValueError(f"bundle artifact changed: {role}")
        payload = _load_role(root, relative, role, bundle_identity=identity)
        attachment = entry.get("attachment")
        if role == "signal":
            if not isinstance(attachment, dict):
                raise ValueError("signal attachment is missing from the bundle")
            attached_path = _safe_file(root, attachment.get("path"))
            attached_sha = _require_sha(attachment.get("sha256"), "signal attachment hash")
            if _sha256(attached_path) != attached_sha or attached_sha != payload["payload"]["signal_sha256"]:
                raise ValueError("signal attachment changed or differs from the signal role")
        elif attachment is not None:
            raise ValueError(f"unexpected bundle attachment: {role}")
        roles[role] = payload
    if manifest["bundle_kind"] in ("G2_EVALUATION", "PROMOTION"):
        rebuilt = _summary_from_report(roles["report"], roles["ledger"], identity)
        declared = roles["summary"]["payload"]
        if declared != rebuilt:
            changed = sorted(key for key in set(declared) | set(rebuilt) if declared.get(key) != rebuilt.get(key))
            raise ValueError(f"summary does not match rebuilt report/ledger: {changed[:5]}")
        cost = roles["cost"]["payload"]
        account = roles["account"]["payload"]
        ledger = roles["ledger"]["payload"]
        if cost["total_cost"] != rebuilt["total_cost"]:
            raise ValueError("cost role differs from rebuilt summary")
        if (
            account["initial_cash"] != ledger["initial_cash"]
            or account["final_account"] != rebuilt["final_account"]
            or account["account_return_max_error"] != rebuilt["account_return_max_error"]
        ):
            raise ValueError("account role differs from ledger/rebuilt summary")
    if manifest["bundle_kind"] == "PROMOTION":
        preregistration = roles["preregistration"]["payload"]
        evidence = roles["promotion_evidence"]["payload"]
        gate = roles["gate"]["payload"]
        if gate["contract_sha256"] != preregistration["contract_sha256"]:
            raise ValueError("promotion gate/preregistration contract identity differs")
        if gate["evidence_sha256"] != evidence["evidence_sha256"]:
            raise ValueError("promotion gate/evidence identity differs")
        if (
            evidence["forward_dates"][0] != identity["window_start"]
            or evidence["forward_dates"][-1] != identity["window_end"]
        ):
            raise ValueError("promotion evidence window differs from bundle identity")
    return {**manifest, "verified_roles": roles}


def _summary_from_report(report_payload, ledger_payload, identity):
    import pandas as pd

    from portfolio_performance import portfolio_performance

    report = report_payload.get("payload") or {}
    ledger = ledger_payload.get("payload") or {}
    dates = report.get("dates")
    gross = report.get("gross_return")
    cost = report.get("cost")
    bench = report.get("benchmark_return")
    account = report.get("account")
    turnover = report.get("turnover")
    if not isinstance(dates, list) or not dates:
        raise ValueError("report payload dates are incomplete")
    if not all(isinstance(item, list) and len(item) == len(dates) for item in (gross, cost, bench, account, turnover)):
        raise ValueError("report payload arrays are incomplete")
    initial_cash = ledger.get("initial_cash")
    if not isinstance(initial_cash, (int, float)) or isinstance(initial_cash, bool) or initial_cash <= 0:
        raise ValueError("ledger initial_cash is invalid")
    frame = pd.DataFrame(
        {"return": gross, "cost": cost, "bench": bench, "account": account, "turnover": turnover},
        index=pd.to_datetime(dates),
    )
    performance = portfolio_performance(frame, initial_cash=float(initial_cash), backtest_start=dates[0])
    daily_net = [g - c for g, c in zip(gross, cost)]
    summary = {
        "metric_schema_version": "current_window_net_v1",
        "dates": [str(pd.Timestamp(day).date()) for day in dates],
        "daily_net_return": daily_net,
        "strategy_total_return_net": performance["strategy_total_return"],
        "total_cost": round(sum(cost), 8),
        "final_account": float(account[-1]),
        "account_return_max_error": performance["account_return_max_error"],
        "source_sha256": identity["source_sha256"],
        "environment_sha256": identity["environment_sha256"],
        "provider_fingerprint": identity["provider_fingerprint"],
        "model_sha256": identity["model_sha256"],
        "strategy_contract_sha256": identity["strategy_contract_sha256"],
    }
    if report.get("selection_inputs") is not None:
        canonical_inputs = _canonical_selection_inputs(report, ledger, identity, performance)
        if report["selection_inputs"] != canonical_inputs:
            raise ValueError("selection inputs do not match metrics rebuilt from the verified report")
        summary["selection_inputs"] = canonical_inputs
    return summary


def rebuild_summary(bundle_root, output_dir):
    root = Path(bundle_root)
    manifest = verify_bundle(root)
    if manifest["bundle_kind"] not in ("G2_EVALUATION", "PROMOTION"):
        raise ValueError("only G2/PROMOTION bundles can rebuild summary")
    report = manifest["verified_roles"]["report"]
    ledger = manifest["verified_roles"]["ledger"]
    summary = manifest["verified_roles"]["summary"]
    rebuilt = _summary_from_report(report, ledger, manifest["identity"])
    declared = summary.get("payload") or {}
    if declared != rebuilt:
        changed = sorted(key for key in set(declared) | set(rebuilt) if declared.get(key) != rebuilt.get(key))
        raise ValueError(f"summary does not match rebuilt report/ledger: {changed[:5]}")
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    target = output / "summary.json"
    target.write_text(json.dumps(rebuilt, indent=2, sort_keys=True, allow_nan=False) + "\n")
    return {"status": "REBUILT", "summary_sha256": _sha256(target), "manifest_sha256": manifest["manifest_sha256"]}


def load_verified_summary(bundle_root):
    """Return a summary only after full G2/PROMOTION verification and rebuild comparison."""
    root = Path(bundle_root)
    manifest = verify_bundle(root)
    if manifest["bundle_kind"] not in ("G2_EVALUATION", "PROMOTION"):
        raise ValueError("selection requires a G2/PROMOTION evidence bundle")
    rebuilt = _summary_from_report(
        manifest["verified_roles"]["report"], manifest["verified_roles"]["ledger"], manifest["identity"]
    )
    declared = manifest["verified_roles"]["summary"]["payload"]
    if declared != rebuilt:
        raise ValueError("verified summary differs from its report/ledger")
    return declared, manifest


def _write_role(path, role, identity, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "kind": ROLE_KIND[role],
                "status": "PASS",
                "identity": identity,
                "payload": payload,
            },
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    )
    return path


def build_evaluation_bundle(
    bundle_root,
    *,
    g1_bundle_root,
    report_payload,
    ledger_payload,
    execution_contract_sha256,
    selection_inputs=None,
    evaluation_candidate_id=None,
    strategy_contract_sha256=None,
    bundle_id=None,
):
    """Build a G2 evaluation while preserving signal provenance.

    The evaluation candidate and strategy are derived from the executable
    replay contract, not inherited from the upstream signal bundle.  A
    verified G2 bundle may be used as the source for a downstream overlay;
    its immutable signal/assembly lineage is retained while the evaluation
    identity changes.
    """
    source_root = Path(g1_bundle_root)
    source = verify_bundle(source_root)
    execution_contract_sha256 = _require_sha(execution_contract_sha256, "execution contract SHA")
    evaluation_candidate_id = _require_sha(evaluation_candidate_id or execution_contract_sha256, "evaluation candidate")
    strategy_contract_sha256 = _require_sha(
        strategy_contract_sha256 or execution_contract_sha256, "evaluation strategy contract"
    )
    bundle_id = bundle_id or f"{source['bundle_id']}:g2:{execution_contract_sha256[:16]}"
    _require_string(bundle_id, "evaluation bundle id")
    identity = {
        **source["identity"],
        "bundle_id": bundle_id,
        "candidate_id": evaluation_candidate_id,
        "strategy_contract_sha256": strategy_contract_sha256,
    }
    _validate_identity(identity)
    report_payload = dict(report_payload)
    if selection_inputs is not None:
        report_payload["selection_inputs"] = selection_inputs
    initial_cash = _require_number(ledger_payload.get("initial_cash"), "ledger initial_cash", positive=True)
    _validate_report_payload(report_payload, identity)
    if selection_inputs is not None:
        import pandas as pd

        from portfolio_performance import portfolio_performance

        frame = pd.DataFrame(
            {
                "return": report_payload["gross_return"],
                "cost": report_payload["cost"],
                "bench": report_payload["benchmark_return"],
                "account": report_payload["account"],
                "turnover": report_payload["turnover"],
            },
            index=pd.to_datetime(report_payload["dates"]),
        )
        performance = portfolio_performance(
            frame,
            initial_cash=initial_cash,
            backtest_start=report_payload["dates"][0],
        )
        report_payload["selection_inputs"] = _canonical_selection_inputs(
            report_payload, ledger_payload, identity, performance
        )
    summary_payload = _summary_from_report({"payload": report_payload}, {"payload": ledger_payload}, identity)
    extra_payloads = {
        "execution": {"execution_contract_sha256": execution_contract_sha256},
        "report": report_payload,
        "ledger": ledger_payload,
        "summary": summary_payload,
        "cost": {"total_cost": summary_payload["total_cost"]},
        "account": {
            "initial_cash": initial_cash,
            "final_account": summary_payload["final_account"],
            "account_return_max_error": summary_payload["account_return_max_error"],
        },
    }
    root = Path(bundle_root)
    role_source = root / "role_sources"
    artifacts = {}
    for role in G1_SIGNAL_ROLES:
        payload = dict(source["verified_roles"][role]["payload"])
        if role == "strategy":
            payload.update(
                {
                    "candidate_id": evaluation_candidate_id,
                    "strategy_contract_sha256": strategy_contract_sha256,
                }
            )
        artifacts[role] = _write_role(role_source / f"{role}.json", role, identity, payload)
    signal_attachment = source["artifacts"]["signal"].get("attachment")
    if not isinstance(signal_attachment, dict):
        raise ValueError("verified source bundle has no signal attachment")
    for role, payload in extra_payloads.items():
        artifacts[role] = _write_role(role_source / f"{role}.json", role, identity, payload)
    return build_bundle(
        root,
        bundle_kind="G2_EVALUATION",
        bundle_id=bundle_id,
        artifacts=artifacts,
        identity=identity,
        attachments={"signal": _safe_file(source_root, signal_attachment["path"])},
    )


def verify_legacy_inventory(inventory_path, *, repo_root):
    repo_root = Path(repo_root)
    payload = json.loads(Path(inventory_path).read_text())
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("legacy inventory schema is missing")
    for item in payload.get("artifacts") or ():
        if not isinstance(item, dict) or item.get("status") not in LEGACY_STATUSES:
            raise ValueError("legacy inventory status is invalid")
        path = _safe_file(repo_root, item.get("path"))
        if _sha256(path) != item.get("sha256"):
            raise ValueError(f"legacy artifact bytes changed: {item.get('path')}")
        if item.get("certified") is not False and item["status"].startswith("LEGACY"):
            raise ValueError("legacy artifacts cannot be certified")
    return payload
