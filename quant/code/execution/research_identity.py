"""Content identities for frozen research inputs and executable dependencies."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_IDENTITY_VERSION = 2
WEBSITE_IDENTITY_VERSION = 3


def file_sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def content_sha(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _relative_file(root, name):
    if not isinstance(name, str) or not name:
        raise ValueError("identity paths must be relative and remain inside the declared root")
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("identity paths must be relative and remain inside the declared root")
    path = root / relative
    if not path.is_file():
        raise ValueError(f"missing identity file: {name}")
    return path


def production_model_config_paths():
    """Return the repo-relative YAML files actually referenced by MODEL_CFG.

    ``qlib_runtime.MODEL_CFG`` is the single production source of truth.  Its
    values use the Modal container root (``/root/qlib``); convert them back to
    repository-relative paths instead of copying a possibly stale filename.
    """
    from qlib_runtime import MODEL_CFG

    prefix = "/root/qlib/"
    paths = set()
    for value in MODEL_CFG.values():
        text = str(value)
        if text.startswith(prefix):
            text = text[len(prefix) :]
        elif Path(text).is_absolute():
            raise ValueError(f"MODEL_CFG path is outside the audited repository root: {text}")
        paths.add(text)
    if not paths:
        raise ValueError("MODEL_CFG contains no auditable configuration paths")
    return tuple(sorted(paths))


_WEBSITE_CODE_DEPENDENCIES = (
    "quant/code/quant_bootstrap.py",
    "quant/code/execution/board_execution.py",
    "quant/code/execution/board_rules.py",
    "quant/code/execution/execution_limits.py",
    "quant/code/execution/liquidity_contract.py",
    "quant/code/execution/order_quantity.py",
    "quant/code/execution/portfolio_performance.py",
    "quant/code/execution/qlib_audit_fixes.py",
    "quant/code/execution/qlib_live_retrain.py",
    "quant/code/execution/research_identity.py",
    "quant/code/execution/st_history.py",
    "quant/code/ops/website_budget.py",
    "quant/code/production/account_boundary.py",
    "quant/code/production/artifact_io.py",
    "quant/code/production/csi1000_production_config.py",
    "quant/code/production/github_commit.py",
    "quant/code/production/github_config.py",
    "quant/code/production/paper_accounting.py",
    "quant/code/production/paper_portfolio.py",
    "quant/code/production/provider_archive.py",
    "quant/code/production/provider_asset.py",
    "quant/code/production/qlib_runtime.py",
    "quant/code/production/website_replay_core.py",
    "quant/code/production/website_simulation.py",
    "quant/code/research/regime_reselection/october_backfill_plan.py",
    "quant/code/research/regime_reselection/prelive_revalidation.py",
    "quant/code/research/stage_ab/csi1000_tuner_core.py",
)
WEBSITE_SEMANTIC_DEPENDENCIES = tuple(sorted(set(_WEBSITE_CODE_DEPENDENCIES) | set(production_model_config_paths())))


def _manifest_body(kind, root, paths, version=SOURCE_IDENTITY_VERSION):
    body = {
        "schema_version": version,
        "kind": kind,
        "files": {str(path.relative_to(root)): file_sha(path) for path in sorted(paths)},
    }
    return {**body, "sha256": content_sha(body)}


def website_semantic_manifest(*, repo_root=None, dependencies=None):
    """Bind only the business closure that can change website results.

    Resource decorators, schedules, documentation and unrelated tools are
    deliberately excluded; ``source_manifest("training")`` records every
    executable change for deployment and receipt audit.
    """
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    names = WEBSITE_SEMANTIC_DEPENDENCIES if dependencies is None else tuple(dependencies)
    if not names or len(set(names)) != len(names):
        raise ValueError("website semantic dependency list is empty or duplicated")
    paths = [_relative_file(root, name) for name in names]
    return _manifest_body("website-semantic", root, paths, version=WEBSITE_IDENTITY_VERSION)


def verify_website_semantic_manifest(expected, *, repo_root=None, dependencies=None):
    if not isinstance(expected, dict) or expected != website_semantic_manifest(
        repo_root=repo_root, dependencies=dependencies
    ):
        raise ValueError("website semantic manifest changed or is missing")
    return expected["sha256"]


def account_contract_manifest(contract):
    """Hash the business contract that must not drift under a semantic reuse."""
    if not isinstance(contract, dict):
        raise ValueError("missing account contract")
    fees = contract.get("fees") if isinstance(contract.get("fees"), dict) else {}
    normalized = dict(contract)
    for key in ("open_cost", "close_cost"):
        if normalized.get(key) is None:
            normalized[key] = fees.get(key)
    required = ("market", "profile", "topk", "dropn", "open_cost", "close_cost")
    missing = [key for key in required if normalized.get(key) is None]
    if missing:
        raise ValueError(f"missing account contract fields: {missing}")
    body = {
        "schema_version": WEBSITE_IDENTITY_VERSION,
        "kind": "account-contract",
        "contract": normalized,
    }
    return {**body, "sha256": content_sha(body)}


def verify_account_contract(expected, current):
    if not isinstance(expected, dict) or expected != account_contract_manifest(current):
        raise ValueError("website account contract changed or is missing")
    return expected["sha256"]


def website_cache_key(semantic_sha256, account_contract_sha256):
    for label, value in (
        ("website semantic", semantic_sha256),
        ("account contract", account_contract_sha256),
    ):
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError(f"{label} identity must be a lowercase SHA256")
    return content_sha(
        {
            "website_semantic_sha256": semantic_sha256,
            "account_contract_sha256": account_contract_sha256,
        }
    )


def verify_loaded_semantic_closure(loaded_paths, *, repo_root=None, dependencies=None):
    """Fail closed when the worker imported an undeclared business module.

    Only files under ``quant/code`` are checked; the Qlib package and the
    Python standard library are not part of the website semantic closure.
    """
    root = (Path(repo_root) if repo_root is not None else REPO_ROOT).resolve()
    allowed = set(WEBSITE_SEMANTIC_DEPENDENCIES if dependencies is None else dependencies)
    code_root = root / "quant/code"
    for path in loaded_paths:
        candidate = Path(path).resolve()
        try:
            relative = candidate.relative_to(code_root)
        except ValueError:
            raise ValueError(f"unregistered website semantic dependency: {candidate}") from None
        if candidate.suffix == ".py":
            name = str((Path("quant/code") / relative).as_posix())
            if name not in allowed:
                raise ValueError(f"unregistered website semantic dependency: {name}")
    return True


def source_manifest(kind, *, repo_root=None):
    """Bind all extension Python implementations and the training YAML inputs.

    Both launcher and worker independently construct this manifest. Hashing the
    complete extension avoids silently omitting indirect or dynamic imports.
    Documentation and data artifacts do not invalidate executable caches.
    """
    if kind not in ("replay", "training"):
        raise ValueError("unknown research source scope")
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    paths = sorted((root / "quant/code").rglob("*.py"))
    if not paths:
        raise ValueError("missing extension implementation tree")
    if kind == "training":
        for name in production_model_config_paths():
            yaml = root / name
            if not yaml.is_file():
                raise ValueError(f"missing training configuration: {name}")
            paths.append(yaml)
    body = {
        "schema_version": SOURCE_IDENTITY_VERSION,
        "kind": kind,
        "files": {str(path.relative_to(root)): file_sha(path) for path in sorted(paths)},
    }
    return {**body, "sha256": content_sha(body)}


def verify_source_manifest(expected, kind, *, repo_root=None):
    if not isinstance(expected, dict) or expected != source_manifest(kind, repo_root=repo_root):
        raise ValueError("shared implementation source manifest changed or is missing")
    return expected["sha256"]


def matching_training_smoke(smoke, job):
    """Legacy entry-script-only smoke receipts cannot authorize new fits."""
    return (
        smoke.get("status") == "SMOKE_PASS"
        and smoke.get("source_identity_version") == SOURCE_IDENTITY_VERSION
        and smoke.get("source_manifest") == job["source_manifest"]
        and smoke.get("source_sha256") == job["source_manifest"]["sha256"]
        and smoke.get("asset_digest") == job["asset"]["digest"]
        and smoke.get("first_execution") == job["first_execution"]
    )


def matching_fit_receipt(receipt, job, block, profile, prediction_sha):
    return (
        receipt.get("status") == "PASS"
        and receipt.get("source_identity_version") == SOURCE_IDENTITY_VERSION
        and receipt.get("source_manifest_sha256") == job["source_manifest"]["sha256"]
        and receipt.get("asset_digest") == job["asset"]["digest"]
        and receipt.get("fit_asof") == block["fit_asof"]
        and receipt.get("signal_end") == block["signal_end"]
        and receipt.get("profile") == profile
        and receipt.get("prediction_sha256") == prediction_sha
    )


def provider_contract(register, manifest_path, amendment_path=None):
    """Load the registered file set and an explicitly permitted amendment."""
    identity = register["provider_identity"]
    document = json.loads(Path(manifest_path).read_text())
    files = document["files"]
    if (
        not isinstance(files, dict)
        or content_sha(files) != identity["files_manifest_sha256"]
        or len(files) != identity["file_count"]
        or document.get("calendar_end") != identity["calendar_end"]
    ):
        raise ValueError("provider manifest differs from registered identity")
    files = dict(files)
    amendment_sha = None
    if amendment_path is not None:
        amendment_sha = file_sha(amendment_path)
        if amendment_sha not in identity.get("allowed_amendment_sha256", []):
            raise ValueError("provider amendment is not explicitly registered")
        amendment = json.loads(Path(amendment_path).read_text())
        if (
            amendment.get("schema_version") != 1
            or amendment.get("base_files_manifest_sha256") != identity["files_manifest_sha256"]
            or not amendment.get("replacements")
        ):
            raise ValueError("provider amendment does not bind the registered base")
        for name, replacement in amendment["replacements"].items():
            if name not in files or replacement.get("before_sha256") != files[name]:
                raise ValueError("provider amendment original file mismatch")
            files[name] = replacement["after_sha256"]
    body = {
        "base_files_manifest_sha256": identity["files_manifest_sha256"],
        "calendar_end": identity["calendar_end"],
        "files": files,
        "amendment_sha256": amendment_sha,
    }
    return {**body, "sha256": content_sha(body)}


def verify_provider(provider, contract):
    """Recheck bytes before every job, including prepared-data cache reuse."""
    root = Path(provider)
    body = {key: value for key, value in contract.items() if key != "sha256"}
    if contract.get("sha256") != content_sha(body):
        raise ValueError("provider contract fingerprint mismatch")
    calendar = _relative_file(root, "calendars/day.txt").read_text().splitlines()
    if not calendar or calendar[-1] != contract["calendar_end"] or calendar != sorted(set(calendar)):
        raise ValueError("provider calendar differs from registered identity")
    for name, digest in contract["files"].items():
        if file_sha(_relative_file(root, name)) != digest:
            raise ValueError(f"provider file differs from registered identity: {name}")
    return contract["sha256"]


def verify_prepared_provenance(provenance, contract):
    used = dict(provenance["provider_metadata"])
    for name, digest in provenance["quote_files"].items():
        symbol, field = name.split("/")
        used[f"features/{symbol.lower()}/{field}.day.bin"] = digest
    if any(contract["files"].get(name) != digest for name, digest in used.items()):
        raise ValueError("prepared data includes unregistered or changed provider files")
