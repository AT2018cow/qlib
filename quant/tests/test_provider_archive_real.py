"""Hash-pinned regression against the *real* trusted chenditc provider package.

The synthetic archives in test_provider_archive.py cover the attacker cases;
this opt-in test covers the production compatibility case the 2026-10-18
review asked for: extract the exact tarball that the canonical publication
consumed, pinned by its SHA256, and require the Qlib package structure.

Usage (operator / CI-with-artifact):

    QLIB_PROVIDER_ARCHIVE=/path/to/qlib_bin.tar.gz \
        python -m pytest -q quant/tests/test_provider_archive_real.py

The archive is skipped when the env var is unset.  The pin below is the
``provider_identity.archive_sha256`` recorded in
``quant/data/signals/2026-10-16_mvp_metadata.json``; update it only when a new
package hash is accepted into production (and record the new provenance).
"""
import hashlib
import os
from pathlib import Path

import pytest

from provider_archive import safe_extract_provider_archive

PINNED_SHA256 = "efb13fc72f6295932413cbfb1814198a4ac6f8c077538d899037b20a23c04ed4"
PINNED_CALENDAR_END = "2026-10-15"


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_trusted_provider_package_extracts_safely(tmp_path):
    archive = os.environ.get("QLIB_PROVIDER_ARCHIVE")
    if not archive:
        pytest.skip("set QLIB_PROVIDER_ARCHIVE to the hash-pinned chenditc tarball")
    archive_path = Path(archive)
    assert archive_path.is_file(), f"archive not found: {archive_path}"
    actual = sha256_file(archive_path)
    assert actual == PINNED_SHA256, (
        f"archive hash {actual} != trusted pin {PINNED_SHA256}; "
        "do not extract an unpinned provider package in this test"
    )
    root = safe_extract_provider_archive(archive_path, tmp_path / "extract")
    day = Path(root) / "calendars" / "day.txt"
    assert day.is_file()
    lines = day.read_text().strip().splitlines()
    assert lines[-1] == PINNED_CALENDAR_END
    assert (Path(root) / "features").is_dir()
