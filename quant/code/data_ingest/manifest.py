"""Canonical JSON and content hashes for provider manifests."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable


def canonical_json(value: Any) -> str:
    """Stable JSON used for content hashes.

    ``allow_nan=False`` is deliberate: a provider artifact with NaN/Infinity
    must fail before it can be hashed or published.
    """
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def sha256_file(path: str | Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: str | Path, value: Any) -> str:
    """Write canonical-ish readable JSON atomically and return its SHA256."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False, allow_nan=False) + "\n"
    tmp = target.with_name(target.name + f".tmp.{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)
    return sha256_text(text)


def tree_sha256(root: str | Path, relative_paths: Iterable[str]) -> str:
    """Hash a deterministic list of (relative path, file SHA256) pairs."""
    base = Path(root)
    digest = hashlib.sha256()
    for rel in sorted(relative_paths):
        path = base / rel
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256_file(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()
