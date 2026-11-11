"""Shared local-file filters for Modal image builds.

Both the production image and isolated probe images add the checkout to the
container.  This predicate is intentionally content-free: it only inspects
path names so it can be unit-tested without reading credentials.
"""

from __future__ import annotations

from pathlib import Path

SENSITIVE_SUFFIXES = (
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".crt",
    ".cer",
    ".der",
    ".jks",
    ".keystore",
)
SENSITIVE_NAMES = ("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "credentials", "config.json")
SENSITIVE_PARTS = ("secrets", "credentials", ".ssh", ".docker", ".aws", ".gnupg")
EXCLUDED_PARTS = (
    ".venv",
    ".git",
    "node_modules",
    ".worktrees",
    "mlruns",
    "__pycache__",
    "quant/local_backup",
    "quant/data",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".black_cache",
    ".codex",
    ".agents",
)
EXCLUDED_SUFFIXES = (".cpp", ".so", ".pyc", ".pyo")


def should_exclude_from_image(path: str | Path) -> bool:
    """Return True for secrets, credentials, VCS state, and build artifacts."""
    text = str(path)
    name = Path(text).name
    parts = Path(text).parts
    if any(part == ".env" or part.startswith(".env.") for part in parts):
        return True
    if any(part in SENSITIVE_PARTS for part in parts):
        return True
    if name in SENSITIVE_NAMES:
        return True
    if text.endswith(SENSITIVE_SUFFIXES) or text.endswith(EXCLUDED_SUFFIXES):
        return True
    if any(part in EXCLUDED_PARTS for part in parts):
        return True
    normalized = text.replace("\\", "/")
    if normalized.endswith("quant/data") or "quant/data/strategy_releases/" in normalized:
        return False
    if "quant/data/" in normalized or normalized.startswith("quant/data"):
        return True
    if "quant/local_backup/" in normalized or normalized.startswith("quant/local_backup"):
        return True
    if text.startswith(".git/") or "/.git/" in text:
        return True
    return False
