"""Content-addressed provider snapshots for the self-hosted daily pipeline.

A provider snapshot is published as **one archive** plus a small immutable
receipt.  Reading 36k+ Qlib bin files from a Modal Volume takes minutes because
of per-file latency; one sequential archive read plus a local extract takes
seconds.  The archive is therefore the transport and the receipt is the commit
point: a snapshot only exists when its receipt is durable and PASS.

Directory snapshots created before this module are still readable as frozen
history.  They are never modified in place.

Layout::

    <overlay_root>/
        <YYYY-MM-DD>/                  # legacy directory snapshot (frozen)
            manifest.json
            calendars/ instruments/ features/
        archives/
            <YYYY-MM-DD>/
                <sha256>.tar.gz        # content-addressed provider archive
                receipt.json           # commit point (PASS)
                manifest.json          # copy for cheap inspection
                calendar.txt           # copy for cheap date checks
        latest.json                    # convenience pointer only

The archive itself contains exactly one ``provider/`` package root with the
Qlib structure.  Tar metadata is normalised (mtime=0, uid/gid=0, sorted
members) so an identical provider tree yields an identical archive digest.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import shutil
import tarfile
import time
import uuid
from datetime import date
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from data_ingest.manifest import canonical_json, sha256_file, sha256_text, tree_sha256, write_json

ARCHIVE_SCHEMA_VERSION = 1
ARCHIVE_CONTRACT = "qlib-provider-archive-v1"
ARCHIVE_DIRNAME = "archives"
RECEIPT_NAME = "receipt.json"
DEFAULT_CACHE_ROOT = "/tmp/provider-cache"
SKIP_NAMES = ("__pycache__", ".DS_Store")
SKIP_SUFFIXES = (".tmp", ".pyc")


class ProviderStoreError(RuntimeError):
    """Raised when a snapshot cannot be published or safely resolved."""


@dataclass(frozen=True)
class ProviderSnapshot:
    """One resolved provider input (archive or legacy directory)."""

    kind: str
    date: str
    path: Path
    digest: str
    calendar_start: str
    calendar_end: str
    calendar_sha256: str
    manifest_sha256: str
    content_sha256: str | None
    size: int
    receipt: dict | None
    receipt_path: Path | None
    manifest: dict

    @property
    def base(self) -> dict:
        return dict(self.manifest.get("base") or {})

    def identity(self) -> dict:
        """Stable provider identity embedded in signal publication metadata."""
        base = self.base
        identity = {
            "name": "overlay",
            "source_url": f"overlay:cn_data_overlay:{self.kind}",
            "archive_sha256": self.digest,
            "calendar_sha256": self.calendar_sha256,
            "calendar_end": self.calendar_end,
            "overlay_manifest_sha256": self.manifest_sha256,
            "base_archive_sha256": base.get("archive_sha256"),
            "provider_kind": self.kind,
            "provider_date": self.date,
        }
        if self.content_sha256:
            identity["overlay_content_sha256"] = self.content_sha256
        if self.receipt:
            identity["provider_receipt_sha256"] = self.receipt.get("receipt_sha256")
        return identity


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def receipt_sha256(receipt: dict) -> str:
    body = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    return sha256_text(canonical_json(body))


def _calendar(provider_dir: Path) -> list[str]:
    path = provider_dir / "calendars" / "day.txt"
    if not path.is_file():
        raise ProviderStoreError(f"provider has no calendar: {provider_dir}")
    dates = [line.strip() for line in path.read_text().splitlines() if line.strip()]
    if not dates:
        raise ProviderStoreError(f"provider calendar is empty: {provider_dir}")
    return dates


def archive_relative_path(date: str, digest: str) -> str:
    return f"{ARCHIVE_DIRNAME}/{date}/{digest}.tar.gz"


def _iter_provider_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if any(part in SKIP_NAMES for part in path.parts):
            continue
        if path.is_file() and not path.name.endswith(SKIP_SUFFIXES):
            files.append(path)
    return files


def _normalised_tarinfo(name: str, *, is_dir: bool, size: int = 0) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.mtime = 0
    info.mode = 0o755 if is_dir else 0o644
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.type = tarfile.DIRTYPE if is_dir else tarfile.REGTYPE
    info.size = 0 if is_dir else size
    return info


def create_provider_archive(
    source_dir: str | Path,
    destination: str | Path,
    *,
    read_workers: int = 16,
    read_batch: int = 256,
) -> dict:
    """Create a deterministic ``tar.gz`` of one provider directory.

    The source directory itself becomes the ``provider/`` package root.  The
    returned metadata contains both the archive digest and the provider content
    digest (sorted relative paths + file SHA256); they are intentionally
    distinct identities.
    """
    source = Path(source_dir)
    destination = Path(destination)
    if not (source / "calendars" / "day.txt").is_file():
        raise ProviderStoreError(f"source is not a provider: {source}")
    files = _iter_provider_files(source)
    if not files:
        raise ProviderStoreError(f"provider has no files: {source}")
    read_workers = max(1, int(read_workers))
    read_batch = max(1, int(read_batch))
    directories: set[str] = {"provider"}
    for path in files:
        relative = path.relative_to(source)
        for parent in relative.parents:
            if str(parent) not in ("", "."):
                directories.add(f"provider/{parent}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    digest = hashlib.sha256()
    written = 0
    try:
        with staging.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=1, mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                    for directory in sorted(directories):
                        archive.addfile(_normalised_tarinfo(directory, is_dir=True))
                    from concurrent.futures import ThreadPoolExecutor

                    def _read(path: Path) -> bytes:
                        return path.read_bytes()

                    with ThreadPoolExecutor(max_workers=read_workers) as executor:
                        for start in range(0, len(files), read_batch):
                            batch = files[start : start + read_batch]
                            blobs = list(executor.map(_read, batch, chunksize=8))
                            for path, blob in zip(batch, blobs):
                                relative = path.relative_to(source)
                                info = _normalised_tarinfo(
                                    f"provider/{relative.as_posix()}", is_dir=False, size=len(blob)
                                )
                                archive.addfile(info, io.BytesIO(blob))
            raw.flush()
            os.fsync(raw.fileno())
        with staging.open("rb") as stream:
            for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                digest.update(chunk)
                written += len(chunk)
        os.replace(staging, destination)
    finally:
        staging.unlink(missing_ok=True)
    return {
        "sha256": digest.hexdigest(),
        "size": written,
        "content_sha256": tree_sha256(source, [str(path.relative_to(source)) for path in files]),
        "member_count": len(files) + len(directories),
        "compression": "gzip-1",
        "contract": ARCHIVE_CONTRACT,
    }


def _extract_archive(archive_path: Path, target_dir: Path) -> Path:
    """Extract a validated provider archive into a fresh local directory."""
    from provider_archive import safe_extract_provider_archive

    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True)
    return Path(safe_extract_provider_archive(archive_path, target_dir))


def _materialized_identity_error(snapshot: ProviderSnapshot, root: Path) -> str | None:
    """Return an identity error for an extracted provider, or ``None``."""
    calendar_path = root / "calendars" / "day.txt"
    if not calendar_path.is_file():
        return "materialized provider calendar is missing"
    if sha256_file(calendar_path) != snapshot.calendar_sha256:
        return "materialized provider calendar SHA256 mismatch"
    try:
        calendar = _calendar(root)
    except ProviderStoreError as exc:
        return str(exc)
    if calendar != sorted(set(calendar)):
        return "materialized provider calendar is not ordered and unique"
    if calendar[0] != snapshot.calendar_start or calendar[-1] != snapshot.calendar_end:
        return (
            "materialized provider calendar endpoints mismatch: "
            f"{calendar[0]}..{calendar[-1]} != {snapshot.calendar_start}..{snapshot.calendar_end}"
        )
    manifest_path = root / "manifest.json"
    if snapshot.manifest_sha256 and (
        not manifest_path.is_file() or sha256_file(manifest_path) != snapshot.manifest_sha256
    ):
        return "materialized provider manifest SHA256 mismatch"
    return None


def materialize(
    snapshot: ProviderSnapshot,
    destination: str | Path,
    *,
    cache_root: str | Path = DEFAULT_CACHE_ROOT,
    verify_digest: bool = True,
) -> Path:
    """Return a local provider directory for ``snapshot``.

    Archive snapshots are copied from the Volume with one sequential read,
    verified against the receipt digest, and extracted.  A per-container cache
    keyed by the archive digest avoids a second copy/extract for callers that
    resolve the same snapshot more than once.
    """
    destination = Path(destination)
    if snapshot.kind == "directory":
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(snapshot.path, destination)
        return destination
    if snapshot.kind != "archive":
        raise ProviderStoreError(f"unknown provider kind: {snapshot.kind}")
    cache = Path(cache_root) / snapshot.digest
    marker = cache / "ready.json"
    if marker.is_file():
        receipt = _read_json(marker)
        if receipt is not None and receipt.get("digest") == snapshot.digest:
            relative = receipt.get("relative_root")
            if (
                isinstance(relative, str)
                and relative
                and not Path(relative).is_absolute()
                and ".." not in Path(relative).parts
            ):
                root = cache / relative
                marker_identity_ok = (
                    receipt.get("calendar_sha256") == snapshot.calendar_sha256
                    and receipt.get("manifest_sha256") == snapshot.manifest_sha256
                )
                if marker_identity_ok and _materialized_identity_error(snapshot, root) is None:
                    return root
    if cache.exists():
        shutil.rmtree(cache)
    cache.mkdir(parents=True)
    archive = cache / "provider.tar.gz"
    digest = hashlib.sha256()
    size = 0
    with snapshot.path.open("rb") as source, archive.open("wb") as target:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
            target.write(chunk)
            size += len(chunk)
    if verify_digest:
        if size != snapshot.size:
            raise ProviderStoreError(f"archive size mismatch: {size} != {snapshot.size}")
        if digest.hexdigest() != snapshot.digest:
            raise ProviderStoreError("archive SHA256 mismatch")
    root = _extract_archive(archive, cache / "extract")
    identity_error = _materialized_identity_error(snapshot, root)
    if identity_error is not None:
        raise ProviderStoreError(identity_error)
    manifest_sha256 = sha256_file(root / "manifest.json")
    calendar_end = snapshot.calendar_end
    marker.write_text(
        json.dumps(
            {
                "digest": snapshot.digest,
                "relative_root": str(root.relative_to(cache)),
                "manifest_sha256": manifest_sha256,
                "calendar_end": calendar_end,
                "calendar_sha256": snapshot.calendar_sha256,
            },
            sort_keys=True,
        )
        + "\n"
    )
    archive.unlink(missing_ok=True)
    return root


def _directory_snapshot_at(path: Path, date: str) -> ProviderSnapshot | None:
    manifest_path = path / "manifest.json"
    manifest = _read_json(manifest_path)
    if manifest is None or manifest.get("status") != "PASS":
        return None
    calendar_path = path / "calendars" / "day.txt"
    if not calendar_path.is_file():
        return None
    calendar = _calendar(path)
    if calendar[-1] != date:
        return None
    manifest_sha = sha256_file(manifest_path)
    return ProviderSnapshot(
        kind="directory",
        date=date,
        path=path,
        digest=manifest_sha,
        calendar_start=calendar[0],
        calendar_end=calendar[-1],
        calendar_sha256=sha256_file(calendar_path),
        manifest_sha256=manifest_sha,
        content_sha256=None,
        size=0,
        receipt=None,
        receipt_path=None,
        manifest=manifest,
    )


def _directory_snapshot(root: Path, date: str) -> ProviderSnapshot | None:
    return _directory_snapshot_at(root / date, date)


def _archive_snapshot(root: Path, date: str) -> ProviderSnapshot | None:
    directory = root / ARCHIVE_DIRNAME / date
    receipt_path = directory / RECEIPT_NAME
    receipt = _read_json(receipt_path)
    if receipt is None or receipt.get("status") != "PASS" or receipt.get("date") != date:
        return None
    if receipt.get("receipt_sha256") != receipt_sha256(receipt):
        return None
    archive = receipt.get("archive") or {}
    relative = archive.get("path")
    if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
        return None
    path = root / relative
    if not path.is_file():
        return None
    if int(archive.get("size") or 0) != path.stat().st_size:
        return None
    digest = str(archive.get("sha256") or "")
    if len(digest) != 64:
        return None
    if path.name != f"{digest}.tar.gz":
        return None
    manifest = _read_json(directory / "manifest.json")
    if manifest is None or manifest.get("status") != "PASS":
        return None
    manifest_sha = sha256_file(directory / "manifest.json")
    if receipt.get("manifest", {}).get("sha256") != manifest_sha:
        return None
    calendar_path = directory / "calendar.txt"
    if not calendar_path.is_file():
        return None
    calendar = [line.strip() for line in calendar_path.read_text().splitlines() if line.strip()]
    if not calendar or calendar[-1] != date:
        return None
    calendar_sha = receipt.get("calendar_sha256") or (receipt.get("calendar") or {}).get("sha256")
    if calendar_sha != sha256_file(calendar_path):
        return None
    return ProviderSnapshot(
        kind="archive",
        date=date,
        path=path,
        digest=digest,
        calendar_start=calendar[0],
        calendar_end=calendar[-1],
        calendar_sha256=sha256_file(calendar_path),
        manifest_sha256=manifest_sha,
        content_sha256=archive.get("content_sha256"),
        size=int(archive["size"]),
        receipt=receipt,
        receipt_path=receipt_path,
        manifest=manifest,
    )


def load_snapshot(root: str | Path, date: str) -> ProviderSnapshot | None:
    """Resolve the published PASS snapshot for an exact date.

    An archive receipt always wins over a legacy directory with the same date,
    so signal and website workers cannot disagree about the provider.
    """
    root = Path(root)
    date = str(date)[:10]
    return _archive_snapshot(root, date) or _directory_snapshot(root, date)


def list_snapshots(root: str | Path) -> dict[str, ProviderSnapshot]:
    root = Path(root)
    dates: set[str] = set()
    archive_root = root / ARCHIVE_DIRNAME
    if archive_root.is_dir():
        for path in archive_root.iterdir():
            if path.is_dir() and len(path.name) == 10:
                dates.add(path.name)
    if root.is_dir():
        for path in root.iterdir():
            if path.is_dir() and len(path.name) == 10 and path.name[:4].isdigit():
                dates.add(path.name)
    snapshots = {}
    for date in sorted(dates):
        snapshot = load_snapshot(root, date)
        if snapshot is not None:
            snapshots[date] = snapshot
    return snapshots


def latest_snapshot(root: str | Path, *, before: str | None = None) -> ProviderSnapshot | None:
    snapshots = list_snapshots(root)
    candidates = [snapshot for date, snapshot in snapshots.items() if before is None or date < before]
    return candidates[-1] if candidates else None


def build_receipt(
    snapshot: ProviderSnapshot | None,
    *,
    date: str,
    archive: dict,
    manifest_sha256: str,
    calendar_sha256: str,
    base: dict,
    provenance: dict,
    gate: dict | None = None,
    extra: dict | None = None,
) -> dict:
    receipt = {
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "status": "PASS",
        "contract": ARCHIVE_CONTRACT,
        "date": date,
        "calendar_end": date,
        "published_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "archive": {
            "path": archive_relative_path(date, archive["sha256"]),
            "sha256": archive["sha256"],
            "size": int(archive["size"]),
            "content_sha256": archive["content_sha256"],
            "member_count": archive.get("member_count"),
            "compression": archive.get("compression"),
        },
        "manifest": {"path": f"{ARCHIVE_DIRNAME}/{date}/manifest.json", "sha256": manifest_sha256},
        "calendar": {"path": f"{ARCHIVE_DIRNAME}/{date}/calendar.txt", "sha256": calendar_sha256},
        "base": base,
        "provenance": provenance,
        "gate": gate or {},
    }
    if snapshot is not None and snapshot.kind == "directory":
        receipt["source_directory"] = {
            "date": snapshot.date,
            "manifest_sha256": snapshot.manifest_sha256,
            "calendar_sha256": snapshot.calendar_sha256,
        }
    if extra:
        receipt.update(extra)
    receipt["receipt_sha256"] = receipt_sha256(receipt)
    return receipt


def publish_archive(
    root: str | Path,
    receipt: dict,
    local_archive: str | Path,
    *,
    local_manifest: str | Path,
    local_calendar: str | Path,
    commit_fn: Callable[[], None] | None = None,
    copy_fn: Callable[[Path, Path], None] | None = None,
) -> dict:
    """Persist one archive + receipt.  The receipt is the commit point.

    The archive is copied to a hidden staging name first, committed, then
    renamed to its digest name.  A crash before the receipt is written leaves
    an orphan archive, which is ignored by readers and can be overwritten by a
    retry.  An existing PASS receipt for a different digest fails closed.
    """
    root = Path(root)
    date = str(receipt.get("date") or "")[:10]
    if receipt.get("status") != "PASS" or date != receipt.get("calendar_end"):
        raise ProviderStoreError("receipt is not a PASS snapshot")
    if receipt.get("receipt_sha256") != receipt_sha256(receipt):
        raise ProviderStoreError("receipt fingerprint mismatch")
    archive_meta = receipt.get("archive") or {}
    digest = str(archive_meta.get("sha256") or "")
    if len(digest) != 64:
        raise ProviderStoreError("receipt has no archive digest")
    target_dir = root / ARCHIVE_DIRNAME / date
    target_dir.mkdir(parents=True, exist_ok=True)
    final = target_dir / f"{digest}.tar.gz"
    existing = _read_json(target_dir / RECEIPT_NAME)
    if existing is not None and existing.get("status") == "PASS":
        if existing.get("archive", {}).get("sha256") != digest:
            raise ProviderStoreError(f"{date} already has a different PASS receipt")
        if existing.get("receipt_sha256") == receipt_sha256(existing) and final.is_file():
            return existing
    copy = copy_fn or shutil.copyfile
    local_archive = Path(local_archive)
    if not final.is_file() or final.stat().st_size != int(archive_meta.get("size") or -1):
        staging = target_dir / f".staging-{uuid.uuid4().hex}.tar.gz"
        try:
            copy(local_archive, staging)
            if staging.stat().st_size != int(archive_meta.get("size") or 0):
                raise ProviderStoreError("staging archive size mismatch")
            if commit_fn is not None:
                commit_fn()
            os.replace(staging, final)
        finally:
            staging.unlink(missing_ok=True)
        if commit_fn is not None:
            commit_fn()
    if not final.is_file() or final.stat().st_size != int(archive_meta.get("size") or 0):
        raise ProviderStoreError("published archive is missing or truncated")
    copy(local_manifest, target_dir / "manifest.json")
    copy(local_calendar, target_dir / "calendar.txt")
    write_json(target_dir / RECEIPT_NAME, receipt)
    if commit_fn is not None:
        commit_fn()
    latest = {
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "date": date,
        "digest": digest,
        "archive_path": archive_relative_path(date, digest),
        "calendar_end": receipt["calendar_end"],
        "receipt_sha256": receipt["receipt_sha256"],
        "published_at_utc": receipt["published_at_utc"],
    }
    write_json(root / "latest.json", latest)
    if commit_fn is not None:
        commit_fn()
    return receipt


def seal_directory_snapshot(
    root: str | Path,
    date: str,
    *,
    source_dir: str | Path | None = None,
    commit_fn: Callable[[], None] | None = None,
    work_dir: str | Path = "/tmp/provider-seal",
) -> dict:
    """One-time migration: turn a legacy PASS directory into an archive receipt.

    The directory itself is never modified.  The resulting archive is verified
    by re-reading its digest, and the receipt records both identities.
    """
    root = Path(root)
    date = str(date)[:10]
    snapshot = (
        _directory_snapshot_at(Path(source_dir), date) if source_dir is not None else _directory_snapshot(root, date)
    )
    if snapshot is None:
        raise ProviderStoreError(f"no PASS directory snapshot for {date}")
    work = Path(work_dir) / date
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    archive = work / "provider.tar.gz"
    meta = create_provider_archive(snapshot.path, archive, read_workers=32)
    base_payload = dict(snapshot.base)
    prior = latest_snapshot(root, before=date)
    if prior is not None and prior.calendar_end == str(base_payload.get("calendar_end") or "")[:10]:
        base_payload = {
            "kind": prior.kind,
            "date": prior.date,
            "digest": prior.digest,
            "path": str(prior.path),
            "calendar_start": prior.calendar_start,
            "calendar_end": prior.calendar_end,
            "calendar_sha256": prior.calendar_sha256,
            "archive_sha256": prior.digest,
            "manifest_sha256": prior.manifest_sha256,
        }
    receipt = build_receipt(
        snapshot,
        date=date,
        archive=meta,
        manifest_sha256=snapshot.manifest_sha256,
        calendar_sha256=snapshot.calendar_sha256,
        base=base_payload,
        provenance={
            "builder": "seal-directory",
            "source_directory": str(snapshot.path),
            "contract": ARCHIVE_CONTRACT,
        },
        gate=(snapshot.manifest.get("gate") or {}),
    )
    return publish_archive(
        root,
        receipt,
        archive,
        local_manifest=snapshot.path / "manifest.json",
        local_calendar=snapshot.path / "calendars" / "day.txt",
        commit_fn=commit_fn,
    )


def _day(value):
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _digest_hex(value):
    text = str(value or "")
    return text.split(":", 1)[1] if text.startswith("sha256:") else text


def collect_retention_references(provider_root, *, journal_root=None, simulation_root=None) -> dict:
    """Collect reachability facts from real provider/journal/marker/website files.

    The collector is read-only.  Corrupt or unknown references are recorded in
    ``unresolved`` so the planner can keep every archive instead of guessing.
    """
    provider_root = Path(provider_root)
    references = {"frozen_anchor_dates": [], "referenced": [], "incomplete": [], "unresolved": []}
    seen = set()

    def add(bucket, day, digest=None, source=""):
        normalized = _day(day)
        if normalized is None:
            references["unresolved"].append({"source": source, "date": day, "reason": "invalid_date"})
            return
        key = (bucket, normalized, _digest_hex(digest))
        if key in seen:
            return
        seen.add(key)
        if bucket == "frozen_anchor_dates":
            references[bucket].append(normalized)
        else:
            references[bucket].append({"date": normalized, "digest": _digest_hex(digest) or None, "source": source})

    # Frozen legacy directory anchors.
    if provider_root.is_dir():
        for path in sorted(provider_root.iterdir()):
            if not path.is_dir() or _day(path.name) != path.name:
                continue
            manifest = _read_json(path / "manifest.json")
            if manifest is not None and manifest.get("status") == "PASS":
                add("frozen_anchor_dates", path.name, source="legacy_directory_anchor")

    # A PASS archive is a complete, standalone provider package.  Its ``base``
    # and ``source_directory`` fields record provenance, not materialization
    # dependencies, so treating every historical base as a retention root
    # would make a normal daily chain impossible to prune.  Still inspect each
    # receipt so unreadable archive metadata forces the conservative plan.
    archive_root = provider_root / ARCHIVE_DIRNAME
    if archive_root.is_dir():
        for directory in sorted(archive_root.iterdir()):
            if not directory.is_dir() or _day(directory.name) != directory.name:
                continue
            receipt = _read_json(directory / RECEIPT_NAME)
            if receipt is None:
                references["unresolved"].append(
                    {"source": "archive_receipt", "date": directory.name, "reason": "unreadable_receipt"}
                )
                continue
            if receipt.get("status") != "PASS":
                continue

    volume = Path(journal_root) if journal_root is not None else None
    committed = []
    if volume is not None:
        journal_base = volume / "publication_journal"
        if journal_base.is_dir():
            for record_path in sorted(journal_base.glob("*/record.json")):
                record = _read_json(record_path)
                if record is None:
                    references["unresolved"].append(
                        {"source": "publication_journal", "date": record_path.parent.name, "reason": "unreadable"}
                    )
                    continue
                context = record.get("publication_context") or {}
                session = context.get("signal_data_date")
                usage = record.get("signal_date") or record_path.parent.name
                if record.get("stage") != "VOLUME_COMMITTED":
                    add("incomplete", session, source="incomplete_journal")
                else:
                    committed.append((str(usage), session))
        if committed:
            committed.sort()
            add("referenced", committed[-1][1], source="latest_committed_journal")
        for marker_name in ("postclose_status", "preopen_status"):
            marker_base = volume / marker_name
            if not marker_base.is_dir():
                continue
            completed = []
            for marker_path in sorted(marker_base.glob("*.json")):
                marker = _read_json(marker_path)
                if marker is None:
                    references["unresolved"].append(
                        {"source": marker_name, "date": marker_path.stem, "reason": "unreadable_marker"}
                    )
                    continue
                session = marker.get("session_date") or marker.get("data_asof")
                digest = (marker.get("provider") or {}).get("digest")
                usage = marker.get("usage_date") or marker.get("signal_date") or marker_path.stem
                if marker.get("status") == "COMPLETED":
                    completed.append((str(usage), session, digest))
                else:
                    add("incomplete", session, digest, marker_name)
            if completed:
                completed.sort()
                add("referenced", completed[-1][1], completed[-1][2], f"latest_{marker_name}")

    simulation = Path(simulation_root) if simulation_root is not None else None
    if simulation is not None and simulation.is_dir():
        results = []
        for result_path in sorted(simulation.glob("*/*/*/full/result.json")):
            payload = _read_json(result_path)
            if payload is None:
                references["unresolved"].append(
                    {"source": "website_result", "date": result_path.parents[2].name, "reason": "unreadable"}
                )
                continue
            if payload.get("status") != "COMPLETED":
                continue
            session = payload.get("provider_calendar_end")
            if _day(session) is None:
                references["unresolved"].append(
                    {"source": "website_result", "date": session, "reason": "invalid_session"}
                )
                continue
            results.append((session, payload.get("asset_digest")))
        if results:
            results.sort()
            add("referenced", results[-1][0], results[-1][1], "website_predecessor")
        for receipt_path in sorted(simulation.glob("*/*/publication.json")):
            payload = _read_json(receipt_path)
            if payload is None:
                references["unresolved"].append(
                    {"source": "website_receipt", "date": receipt_path.parent.name, "reason": "unreadable"}
                )
                continue
            if payload.get("status") == "RECOVERED_AFTER_PUBLICATION":
                add("referenced", payload.get("latest_date"), payload.get("asset_digest"), "recovery_receipt")
    return references


def _month_end_checkpoint(day: str) -> bool:
    return day[5:] in (
        "01-31",
        "02-28",
        "02-29",
        "03-31",
        "04-30",
        "05-31",
        "06-30",
        "07-31",
        "08-31",
        "09-30",
        "10-31",
        "11-30",
        "12-31",
    )


def retention_plan(
    root: str | Path,
    *,
    keep: int = 30,
    frozen_anchor_dates: Iterable[str] = (),
    referenced_dates: Iterable[str] = (),
    incomplete_dates: Iterable[str] = (),
    references: dict | None = None,
    generated_at: str | None = None,
) -> dict:
    """Build a reachability-aware archive retention plan; never delete.

    Keep rules: frozen anchors, the latest PASS, the most recent ``keep``
    PASS archives, month-end checkpoints, and every date referenced by an
    incomplete journal/marker, current website predecessor or recovery receipt.
    A remove entry is a complete ``archives/<date>`` directory candidate whose
    receipt, manifest, calendar and archive are handled as one unit.
    """
    root = Path(root)
    archive_root = root / ARCHIVE_DIRNAME
    anchors = {str(day)[:10] for day in frozen_anchor_dates}
    referenced = {str(day)[:10] for day in referenced_dates}
    incomplete = {str(day)[:10] for day in incomplete_dates}
    expected_digests = {}
    unresolved = []
    if references is not None:
        if not isinstance(references, dict):
            raise ValueError("retention references must be an object")
        anchors.update(_day(day) for day in references.get("frozen_anchor_dates") or () if _day(day))
        for bucket, target in (("referenced", referenced), ("incomplete", incomplete)):
            for item in references.get(bucket) or ():
                if not isinstance(item, dict):
                    unresolved.append({"source": bucket, "reason": "invalid_reference"})
                    continue
                normalized = _day(item.get("date"))
                if normalized is None:
                    unresolved.append(
                        {"source": item.get("source"), "date": item.get("date"), "reason": "invalid_date"}
                    )
                    continue
                target.add(normalized)
                if item.get("digest"):
                    expected_digests[normalized] = _digest_hex(item["digest"])
        unresolved.extend(references.get("unresolved") or [])
    if keep < 1:
        raise ValueError("retention keep must be positive")
    entries = []
    if archive_root.is_dir():
        for path in sorted(archive_root.iterdir()):
            if not path.is_dir() or len(path.name) != 10:
                continue
            receipt = _read_json(path / RECEIPT_NAME)
            if receipt is None or receipt.get("status") != "PASS":
                continue
            archive = receipt.get("archive") or {}
            archive_path = root / str(archive.get("path") or "")
            entries.append(
                {
                    "date": path.name,
                    "path": str(path.relative_to(root)),
                    "digest": archive.get("sha256"),
                    "size": int(archive.get("size") or 0),
                    "receipt_sha256": receipt.get("receipt_sha256"),
                    "archive_present": archive_path.is_file(),
                    "month_end": _month_end_checkpoint(path.name),
                }
            )
    entry_by_date = {item["date"]: item for item in entries}
    for day, digest in expected_digests.items():
        entry = entry_by_date.get(day)
        if entry is None or entry.get("digest") != digest:
            unresolved.append({"source": "reachability", "date": day, "reason": "missing_or_changed_archive"})
    entries.sort(key=lambda item: item["date"], reverse=True)
    latest = entries[0]["date"] if entries else None
    recent = {item["date"] for item in entries[:keep]}
    keep_entries, remove_entries = [], []
    for item in entries:
        day = item["date"]
        reason = None
        if unresolved:
            reason = "unresolved_reference"
        elif day in anchors:
            reason = "frozen_anchor"
        elif day == latest:
            reason = "latest_pass"
        elif day in incomplete:
            reason = "incomplete_publication"
        elif day in referenced:
            reason = "referenced_by_journal_website_or_recovery"
        elif item["month_end"]:
            reason = "month_end_checkpoint"
        elif day in recent:
            reason = "recent_pass"
        if reason is not None:
            keep_entries.append({**item, "keep": True, "reason": reason})
        elif not item["archive_present"]:
            keep_entries.append({**item, "keep": True, "reason": "incomplete_archive_directory"})
        else:
            remove_entries.append({**item, "keep": False, "reason": "retention_candidate"})
    return {
        "schema_version": 1,
        "generated_at_utc": generated_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "root": str(root),
        "keep": keep_entries,
        "remove": remove_entries,
        "counts": {"keep": len(keep_entries), "remove": len(remove_entries)},
        "unresolved": unresolved,
        "dry_run": True,
        "apply": "NOT_PERMITTED_WITHOUT_SEPARATE_OPERATOR_APPROVAL",
    }


def prune_archives(
    root: str | Path,
    *,
    keep: int = 30,
    dry_run: bool = True,
    commit_fn: Callable[[], None] | None = None,
    **plan_kwargs,
) -> dict:
    """Dry-run compatibility wrapper around :func:`retention_plan`.

    Destructive application is intentionally unavailable in this repository
    round.  The first production apply requires Codex acceptance, a fixed
    fixture and explicit operator approval of the exact remove list.
    """
    if not dry_run:
        raise ProviderStoreError(
            "archive retention apply is disabled; review retention_plan output and obtain separate approval"
        )
    plan = retention_plan(root, keep=keep, **plan_kwargs)
    checkpoints = [item["date"] for item in plan["keep"] if item["reason"] == "month_end_checkpoint"]
    return {
        "dry_run": True,
        "kept": plan["counts"]["keep"],
        "removed": [item["date"] for item in plan["remove"]],
        "checkpoints": checkpoints,
        "plan": plan,
    }
