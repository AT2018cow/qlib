"""Content identities for frozen research inputs and executable dependencies."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_IDENTITY_VERSION = 2


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
        yaml = root / "examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml"
        if not yaml.is_file():
            raise ValueError("missing training configuration")
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
