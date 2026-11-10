"""Shared local cache for the chenditc daily provider.

The daily job downloads one immutable ``qlib_bin.tar.gz`` release per morning and
stores it under the production data volume.  Signals and the website worker then
read the same local provider instead of downloading the 570 MB archive again in
every market container.

The cache also writes a small ``manifest.json`` that carries the previous
trading session.  ``website_simulation_modal._incremental_prior`` already knows
how to read the overlay manifest's ``base.path`` / ``base.calendar_end`` fields;
the same shape lets the website reuse its existing single-session replay engine
for chenditc days without a second account implementation.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

CACHE_VERSION = 1
CACHE_DIRNAME = "chenditc_cache"
RELEASE_ASSET = "qlib_bin.tar.gz"


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text())
    except Exception:
        return None
    return raw if isinstance(raw, dict) else None


def calendar_end(provider: str | Path) -> str | None:
    path = Path(provider) / "calendars" / "day.txt"
    if not path.is_file():
        return None
    lines = [line.strip() for line in path.read_text().splitlines() if line.strip()]
    return lines[-1] if lines else None


def previous_calendar_end(provider: str | Path) -> str | None:
    path = Path(provider) / "calendars" / "day.txt"
    if not path.is_file():
        return None
    lines = [line.strip() for line in path.read_text().splitlines() if line.strip()]
    return lines[-2] if len(lines) >= 2 else None


def latest_release(*, timeout: int = 30) -> dict[str, Any]:
    """Return the newest release that actually carries the qlib archive.

    Some repair/backup releases have no ``qlib_bin.tar.gz`` asset.  The GitHub
    ``releases/latest`` endpoint can point at such a release, so scan a bounded
    page and pick the newest usable date-like release instead.
    """
    import requests

    response = requests.get(
        "https://api.github.com/repos/chenditc/investment_data/releases",
        params={"per_page": 30},
        headers={"User-Agent": "qlib-provider-cache"},
        timeout=timeout,
    )
    response.raise_for_status()
    for release in response.json():
        if release.get("draft") or release.get("prerelease"):
            continue
        tag = str(release.get("tag_name") or "")
        if not tag or "backup" in tag.lower() or not tag[:4].isdigit():
            continue
        asset = next((item for item in release.get("assets", []) if item.get("name") == RELEASE_ASSET), None)
        if asset is None or not str(asset.get("digest", "")).startswith("sha256:"):
            continue
        return {
            "tag": tag,
            "published_at": release.get("published_at"),
            "url": asset["browser_download_url"],
            "digest": asset["digest"],
            "digest_hex": asset["digest"].split(":", 1)[1],
            "size": int(asset.get("size") or 0),
        }
    raise RuntimeError("no usable chenditc qlib_bin.tar.gz release found")


def _provider_dir(root: str | Path, digest_hex: str) -> Path:
    return Path(root) / digest_hex


def _write_latest(root: str | Path, descriptor: dict[str, Any]) -> None:
    latest = Path(root) / "latest.json"
    latest.write_text(json.dumps(descriptor, indent=2, sort_keys=True) + "\n")


def install_provider(
    extracted: str | Path,
    root: str | Path,
    *,
    digest: str,
    release: str,
    published_at: str | None,
    source_url: str,
    size: int = 0,
) -> dict[str, Any]:
    """Move an extracted provider into the shared cache and write its manifests."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    digest_hex = digest.split(":", 1)[1] if ":" in digest else digest
    target = _provider_dir(root, digest_hex)
    end = calendar_end(extracted)
    if end is None:
        raise ValueError("chenditc provider has no calendar")
    previous = previous_calendar_end(extracted)
    if previous is None:
        raise ValueError("chenditc provider has no previous trading session")
    if target.exists():
        shutil.rmtree(target)
    shutil.move(str(extracted), str(target))
    manifest = {
        "schema_version": CACHE_VERSION,
        "status": "PASS",
        "source": "chenditc",
        "calendar_end": end,
        "release": release,
        "published_at": published_at,
        "asset_digest": digest,
        "base": {
            "path": f"provider:chenditc/{previous}",
            "calendar_end": previous,
        },
    }
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    descriptor = {
        "schema_version": CACHE_VERSION,
        "status": "PASS",
        "source": "chenditc",
        "calendar_end": end,
        "base_calendar_end": previous,
        "digest": digest,
        "digest_hex": digest_hex,
        "size": int(size),
        "release": release,
        "published_at": published_at,
        "source_url": source_url,
        "path": str(target),
    }
    _write_latest(root, descriptor)
    _prune(root, keep=3, keep_digest=digest_hex)
    return descriptor


def _prune(root: Path, *, keep: int, keep_digest: str | None = None) -> None:
    directories = [path for path in root.iterdir() if path.is_dir() and len(path.name) == 64]
    directories.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    kept = 0
    for path in directories:
        if path.name == keep_digest:
            continue
        kept += 1
        if kept >= keep:
            shutil.rmtree(path, ignore_errors=True)


def read_latest(root: str | Path) -> dict[str, Any] | None:
    latest = _read_json(Path(root) / "latest.json")
    if latest is None or latest.get("status") != "PASS":
        return None
    return latest


def select_cached_provider(expected_end: str | None, root: str | Path) -> dict[str, Any] | None:
    """Return a ready cached provider, optionally requiring an exact session end."""
    root = Path(root)
    latest = read_latest(root)
    if latest is None:
        return None
    digest_hex = str(latest.get("digest_hex") or "")
    if len(digest_hex) != 64:
        return None
    provider = _provider_dir(root, digest_hex)
    actual_end = calendar_end(provider)
    if actual_end is None:
        return None
    if expected_end is not None and actual_end != str(expected_end)[:10]:
        return None
    manifest = _read_json(provider / "manifest.json")
    if manifest is None or manifest.get("status") != "PASS" or manifest.get("source") != "chenditc":
        return None
    if manifest.get("calendar_end") != actual_end:
        return None
    return {
        **latest,
        "calendar_end": actual_end,
        "local_path": str(provider),
        "base_calendar_end": (manifest.get("base") or {}).get("calendar_end"),
    }


def provider_identity(descriptor: dict[str, Any]) -> dict[str, Any]:
    """Signal publication identity for a cached chenditc provider."""
    local = Path(descriptor["local_path"])
    manifest = _read_json(local / "manifest.json") or {}
    calendar = local / "calendars" / "day.txt"
    return {
        "name": "chenditc",
        "source_url": descriptor.get("source_url", ""),
        "archive_sha256": str(descriptor["digest"]).split(":", 1)[-1],
        "calendar_sha256": sha256_file(calendar),
        "calendar_end": descriptor["calendar_end"],
        "chenditc_release": descriptor.get("release"),
        "chenditc_published_at": descriptor.get("published_at"),
        "provider_cache": str(local),
        "base_calendar_end": (manifest.get("base") or {}).get("calendar_end"),
    }


def provider_asset(descriptor: dict[str, Any]) -> dict[str, Any]:
    """Website worker asset shape for a cached chenditc provider."""
    return {
        "local_path": descriptor["local_path"],
        "digest": descriptor["digest"],
        "size": int(descriptor.get("size") or 0),
        "release": descriptor.get("release"),
        "published_at": descriptor.get("published_at"),
        "source": "chenditc",
        "calendar_end": descriptor["calendar_end"],
    }
