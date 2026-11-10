"""Contract tests for the shared chenditc provider cache."""

import json
from pathlib import Path

from provider_cache import (
    install_provider,
    provider_asset,
    provider_identity,
    select_cached_provider,
)


def _extracted(root: Path) -> Path:
    provider = root / "extracted"
    (provider / "calendars").mkdir(parents=True)
    (provider / "calendars" / "day.txt").write_text("2026-11-06\n2026-11-09\n")
    (provider / "features").mkdir()
    return provider


def test_install_and_select_requires_exact_calendar_end(tmp_path):
    root = tmp_path / "cache"
    digest = "sha256:" + "a" * 64
    descriptor = install_provider(
        _extracted(tmp_path),
        root,
        digest=digest,
        release="2026-11-09",
        published_at="2026-11-09T12:16:03Z",
        source_url="https://example.invalid/qlib_bin.tar.gz",
        size=123,
    )
    assert descriptor["calendar_end"] == "2026-11-09"
    assert descriptor["base_calendar_end"] == "2026-11-06"
    manifest = json.loads((root / ("a" * 64) / "manifest.json").read_text())
    assert manifest["source"] == "chenditc"
    assert manifest["base"] == {"path": "provider:chenditc/2026-11-06", "calendar_end": "2026-11-06"}

    selected = select_cached_provider("2026-11-09", root)
    assert selected is not None
    assert selected["local_path"].endswith("a" * 64)
    assert selected["digest"] == digest
    assert select_cached_provider("2026-11-10", root) is None
    assert provider_asset(selected)["source"] == "chenditc"
    identity = provider_identity(selected)
    assert identity["name"] == "chenditc"
    assert identity["calendar_end"] == "2026-11-09"
    assert identity["archive_sha256"] == "a" * 64
    assert identity["base_calendar_end"] == "2026-11-06"


def test_select_rejects_missing_or_mismatched_manifest(tmp_path):
    root = tmp_path / "cache"
    digest = "b" * 64
    provider = root / digest
    (provider / "calendars").mkdir(parents=True)
    (provider / "calendars" / "day.txt").write_text("2026-11-06\n2026-11-09\n")
    (root / "latest.json").write_text(
        json.dumps(
            {
                "status": "PASS",
                "source": "chenditc",
                "calendar_end": "2026-11-09",
                "digest": f"sha256:{digest}",
                "digest_hex": digest,
            }
        )
    )
    assert select_cached_provider("2026-11-09", root) is None  # manifest missing
    (provider / "manifest.json").write_text(
        json.dumps({"status": "PASS", "source": "chenditc", "calendar_end": "2026-11-08"})
    )
    assert select_cached_provider("2026-11-09", root) is None  # calendar mismatch
