"""Safe extraction of external provider data archives (R05).

The daily cron downloads a third-party ``tar.gz`` release and extracts it
inside the Modal container.  ``tarfile.extractall`` without member validation
allows path traversal, symlink/hardlink escape, device files and
decompression bombs.  Container Python 3.11 does not have the PEP 706
``filter=`` backport guarantee, so extraction is gated by explicit validation:

- reject absolute paths, ``..`` traversal, backslashes and non-normalized
  member names;
- reject symlinks, hardlinks, devices, FIFOs and any non file/dir entry;
- bound member count, per-file size and total uncompressed size;
- require a unique package root that contains the Qlib structure
  (``calendars/day.txt`` + ``features/``);
- extract only validated regular files/directories into a fresh directory.

The module is import-clean (stdlib only) so CI can test it without Modal.
"""

from __future__ import annotations

import os
import tarfile
from pathlib import Path

MAX_MEMBERS = 200_000
MAX_FILE_BYTES = 8 * 1024**3
MAX_TOTAL_BYTES = 16 * 1024**3
REQUIRED_FILES = ("calendars/day.txt",)
REQUIRED_DIRS = ("features",)


class UnsafeArchiveError(ValueError):
    """Raised when a provider archive fails fail-closed validation."""


def _is_safe_member_path(name: str) -> bool:
    if not name or name in (".", "./"):
        return False
    if name.startswith("/") or "\\" in name:
        return False
    if os.path.isabs(name):
        return False
    normalized = os.path.normpath(name)
    if normalized.startswith(".."):
        return False
    parts = Path(name).parts
    if any(part in ("..", "") for part in parts):
        return False
    if normalized != name.rstrip("/") and normalized != name:
        # Names must already be normalized; anything else is ambiguous.
        return False
    return True


def _package_root(member_names) -> str:
    """Return the unique prefix ("" or "one/top/dir") holding the Qlib package."""
    names = list(member_names)
    roots = []
    candidates = {""} | {name.split("/", 1)[0] + "/" for name in names if "/" in name}
    for prefix in candidates:
        files = {name[len(prefix) :] for name in names if name.startswith(prefix)}
        if all(required in files for required in REQUIRED_FILES) and all(
            any(name.startswith(required + "/") for name in files) for required in REQUIRED_DIRS
        ):
            roots.append(prefix)
    if len(roots) != 1:
        raise UnsafeArchiveError(
            f"provider archive must contain exactly one Qlib package root with "
            f"{REQUIRED_FILES + REQUIRED_DIRS}; found {sorted(roots)}"
        )
    return roots[0]


def validate_archive_members(members) -> list[tarfile.TarInfo]:
    """Validate tar members and return the validated regular entries."""
    if len(members) > MAX_MEMBERS:
        raise UnsafeArchiveError(f"archive has too many members: {len(members)} > {MAX_MEMBERS}")
    validated = []
    total = 0
    names = []
    for member in members:
        if not _is_safe_member_path(member.name):
            raise UnsafeArchiveError(f"unsafe member path: {member.name!r}")
        if member.issym() or member.islnk():
            raise UnsafeArchiveError(f"links are not allowed in provider archive: {member.name!r}")
        if not (member.isfile() or member.isdir()):
            raise UnsafeArchiveError(f"non file/dir entry rejected: {member.name!r}")
        if member.isfile():
            if member.size < 0 or member.size > MAX_FILE_BYTES:
                raise UnsafeArchiveError(f"member size out of bounds: {member.name!r}")
            total += member.size
            if total > MAX_TOTAL_BYTES:
                raise UnsafeArchiveError("archive uncompressed size exceeds safety bound")
        names.append(member.name)
        validated.append(member)
    if not names:
        raise UnsafeArchiveError("archive is empty")
    _package_root(names)
    return validated


def safe_extract_provider_archive(archive_path, target_dir) -> str:
    """Validate and extract a provider archive into ``target_dir``.

    ``target_dir`` must be a fresh directory; nothing is extracted until the
    whole member list passed validation.  Returns the package root directory
    path inside ``target_dir`` (where ``features/`` and ``calendars/`` live).
    """
    archive_path = Path(archive_path)
    target_dir = Path(target_dir)
    if not archive_path.is_file():
        raise UnsafeArchiveError(f"archive not found: {archive_path}")
    target_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "r:gz") as tf:
        members = validate_archive_members(tf.getmembers())
        for member in members:
            destination = target_dir / member.name
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            extracted = tf.extractfile(member)
            if extracted is None:
                raise UnsafeArchiveError(f"cannot read member: {member.name!r}")
            with destination.open("wb") as out:
                while True:
                    chunk = extracted.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
    prefix = _package_root(member.name for member in members)
    root = target_dir / prefix if prefix else target_dir
    if not (root / "calendars" / "day.txt").is_file() or not (root / "features").is_dir():
        raise UnsafeArchiveError("extracted package is missing the required Qlib structure")
    return str(root)
