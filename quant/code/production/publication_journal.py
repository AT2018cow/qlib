"""Durable publication journal and cross-task lease for the daily canonical cron (R02).

GitHub publication and Modal Volume state advancement are two independent
systems.  A crash between ``push_files`` and the paper-state commit can leave
GitHub ahead of the account, while a rerun cannot regenerate byte-identical
proposals (preopen timestamps, frozen ST snapshots, execution context).  The
review (2026-10-18, R02) requires a re-entrant commit state machine:

    PREPARED -> GITHUB_PUBLISHED -> VOLUME_COMMITTED

The journal freezes the complete publication intent (artifact file contents +
paper state payloads, all SHA256-addressed) on the Volume *before* the GitHub
push.  A rerun never regenerates from live inputs: it reloads the frozen
payloads, verifies any already-published bytes against the recorded hashes and
idempotently completes the remaining stages.  Any divergence fails closed with
``BLOCKED_MANUAL_RECOVERY`` instead of silently rewriting or double-executing.

A short-TTL lease guards the critical section across overlapping schedules
(daily_cron 07:00 and the 07:45 second chance).  Modal Volumes have no atomic
compare-and-swap; the lease uses write-then-reread verification with a
monotonic generation, and every paper-state write is additionally protected by
prior/post SHA checks, so a fencing failure can never clobber advanced state.

The module is stdlib-only and storage-agnostic so CI can fault-inject writes,
crashes and concurrent holders without Modal.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

STAGE_PREPARED = "PREPARED"
STAGE_GITHUB_PUBLISHED = "GITHUB_PUBLISHED"
STAGE_VOLUME_COMMITTED = "VOLUME_COMMITTED"
_STAGE_ORDER = (STAGE_PREPARED, STAGE_GITHUB_PUBLISHED, STAGE_VOLUME_COMMITTED)

BASE_DIR = "publication_journal"
LEASE_NAME = "lease.json"
DEFAULT_LEASE_TTL_SECONDS = 150


class JournalError(RuntimeError):
    """Base journal failure."""


class JournalLocked(JournalError):
    """Another publication run holds the lease."""


class JournalCorrupt(JournalError):
    """Journal/payload divergence; requires manual recovery (fail closed)."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class FilesystemStorage:
    """Volume-mount storage adapter (paths are relative to the volume root)."""

    def __init__(self, root, reload_fn=None, commit_fn=None):
        self.root = Path(root)
        self._reload_fn = reload_fn
        self._commit_fn = commit_fn

    def _path(self, rel: str) -> Path:
        return self.root / rel

    def read(self, rel: str):
        path = self._path(rel)
        if not path.is_file():
            return None
        return path.read_bytes()

    def write(self, rel: str, data: bytes) -> None:
        path = self._path(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.journal.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)

    def list(self, rel: str) -> list:
        path = self._path(rel)
        if not path.is_dir():
            return []
        return sorted(entry.name for entry in path.iterdir())

    def reload(self) -> None:
        if self._reload_fn is not None:
            self._reload_fn()

    def commit(self) -> None:
        if self._commit_fn is not None:
            self._commit_fn()


class PublicationJournal:
    """Write-ahead journal for one day's multi-market canonical publication."""

    def __init__(self, storage, base_dir: str = BASE_DIR, now_fn=_utc_now):
        self.storage = storage
        self.base_dir = base_dir
        self.now_fn = now_fn

    def _dir(self, signal_date: str) -> str:
        return f"{self.base_dir}/{signal_date}"

    def _record_path(self, signal_date: str) -> str:
        return f"{self._dir(signal_date)}/record.json"

    def load(self, signal_date: str):
        raw = self.storage.read(self._record_path(signal_date))
        if raw is None:
            return None
        try:
            record = json.loads(raw.decode())
        except ValueError as exc:
            raise JournalCorrupt(f"journal record unreadable for {signal_date}") from exc
        if not isinstance(record, dict) or record.get("signal_date") != signal_date:
            raise JournalCorrupt(f"journal record identity mismatch for {signal_date}")
        if record.get("stage") not in _STAGE_ORDER:
            raise JournalCorrupt(f"journal stage unknown: {record.get('stage')!r}")
        return record

    def incomplete_records(self) -> list:
        records = []
        for name in self.storage.list(self.base_dir):
            record = self.load(name)
            if record is not None and record["stage"] != STAGE_VOLUME_COMMITTED:
                records.append(record)
        records.sort(key=lambda record: record["signal_date"])
        return records

    def prepare(
        self,
        signal_date: str,
        holder: str,
        files: dict,
        states: dict,
    ) -> dict:
        """Freeze the publication intent.  ``files`` maps path -> content str;
        ``states`` maps market -> {path, prior_sha256, content}."""
        if self.load(signal_date) is not None:
            raise JournalError(f"journal already exists for {signal_date}")
        files_payload = {path: content for path, content in files.items()}
        states_payload = {
            market: {
                "path": entry["path"],
                "prior_sha256": entry.get("prior_sha256"),
                "content": entry["content"],
            }
            for market, entry in states.items()
        }
        files_bytes = json.dumps(files_payload, ensure_ascii=False, sort_keys=True).encode()
        states_bytes = json.dumps(states_payload, ensure_ascii=False, sort_keys=True).encode()
        record = {
            "publication_id": f"signal-{signal_date}",
            "signal_date": signal_date,
            "stage": STAGE_PREPARED,
            "holder": holder,
            "created_at": self.now_fn(),
            "updated_at": self.now_fn(),
            "github_commit": None,
            "files": {path: _sha256(content.encode()) for path, content in files_payload.items()},
            "states": {
                market: {
                    "path": entry["path"],
                    "prior_sha256": entry.get("prior_sha256"),
                    "sha256": _sha256(entry["content"].encode()),
                }
                for market, entry in states_payload.items()
            },
            "payloads": {
                "files": {"path": f"{self._dir(signal_date)}/files.json", "sha256": _sha256(files_bytes)},
                "states": {"path": f"{self._dir(signal_date)}/states.json", "sha256": _sha256(states_bytes)},
            },
        }
        self.storage.write(record["payloads"]["files"]["path"], files_bytes)
        self.storage.write(record["payloads"]["states"]["path"], states_bytes)
        self._write_record(record)
        return record

    def mark(self, record: dict, stage: str, github_commit=None) -> dict:
        if stage not in _STAGE_ORDER:
            raise JournalError(f"unknown journal stage: {stage!r}")
        current = self.load(record["signal_date"])
        if current is None:
            raise JournalCorrupt(f"journal disappeared for {record['signal_date']}")
        if _STAGE_ORDER.index(stage) < _STAGE_ORDER.index(current["stage"]):
            raise JournalError(f"journal stage regression: {current['stage']} -> {stage}")
        current["stage"] = stage
        current["updated_at"] = self.now_fn()
        if github_commit:
            current["github_commit"] = github_commit
        self._write_record(current)
        return current

    def read_payloads(self, record: dict):
        """Return (files {path: content}, states {market: entry+content})."""
        payloads = {}
        for kind in ("files", "states"):
            meta = record["payloads"][kind]
            raw = self.storage.read(meta["path"])
            if raw is None or _sha256(raw) != meta["sha256"]:
                raise JournalCorrupt(f"journal payload {kind} missing or corrupt for {record['signal_date']}")
            try:
                payloads[kind] = json.loads(raw.decode())
            except ValueError as exc:
                raise JournalCorrupt(f"journal payload {kind} unreadable") from exc
        files = payloads["files"]
        states = payloads["states"]
        if {path: _sha256(content.encode()) for path, content in files.items()} != record["files"]:
            raise JournalCorrupt("journal file payload does not match recorded hashes")
        for market, entry in states.items():
            recorded = record["states"].get(market)
            if not isinstance(entry, dict) or recorded is None:
                raise JournalCorrupt(f"journal state payload drift for {market}")
            if entry.get("path") != recorded["path"] or _sha256(entry["content"].encode()) != recorded["sha256"]:
                raise JournalCorrupt(f"journal state payload mismatch for {market}")
        return files, states

    def plan_completion(self, record: dict, published: dict) -> list:
        """Compare GitHub bytes against the journal; return paths still missing.

        Any published byte that does not match the frozen hash is a hard
        failure (BLOCKED_MANUAL_RECOVERY): never overwrite immutable history.
        """
        missing = []
        for path, expected in record["files"].items():
            actual = published.get(path)
            if actual is None:
                missing.append(path)
                continue
            if _sha256(actual) != expected:
                raise JournalCorrupt(
                    "BLOCKED_MANUAL_RECOVERY: published artifact differs from frozen journal " f"hash: {path}"
                )
        return sorted(missing)

    def plan_state_writes(self, record: dict, current: dict) -> dict:
        """Return market -> content to write, or fail closed on divergence.

        A state file may only be replaced when it is untouched since the journal
        was prepared or already carries the frozen post-state bytes.
        """
        writes = {}
        for market, entry in record["states"].items():
            target = entry["sha256"]
            prior = entry.get("prior_sha256")
            actual = current.get(market)
            if actual is not None:
                actual_sha = _sha256(actual)
                if actual_sha not in (target, prior):
                    raise JournalCorrupt(
                        "BLOCKED_MANUAL_RECOVERY: paper state diverged from journal "
                        f"({market} {entry['path']}); refusing to overwrite"
                    )
            writes[market] = entry
        return writes

    def _write_record(self, record: dict) -> None:
        self.storage.write(
            self._record_path(record["signal_date"]),
            json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2).encode(),
        )
        self.storage.commit()


class PublicationLease:
    """Best-effort cross-container lease with TTL, wait and generation fencing."""

    def __init__(
        self,
        storage,
        *,
        name: str = f"{BASE_DIR}/{LEASE_NAME}",
        ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
        now_fn=time.time,
        sleep_fn=time.sleep,
    ):
        self.storage = storage
        self.name = name
        self.ttl_seconds = ttl_seconds
        self.now_fn = now_fn
        self.sleep_fn = sleep_fn

    def _read(self):
        raw = self.storage.read(self.name)
        if raw is None:
            return None
        try:
            return json.loads(raw.decode())
        except ValueError:
            return None

    def acquire(self, holder: str, wait_seconds: float = 0, poll_seconds: float = 5) -> dict:
        deadline = self.now_fn() + wait_seconds
        while True:
            self.storage.reload()
            current = self._read()
            now = self.now_fn()
            held = (
                current is not None
                and current.get("holder")
                and float(current.get("expires_at", 0)) > now
                and current.get("holder") != holder
            )
            if held:
                if self.now_fn() < deadline:
                    self.sleep_fn(min(poll_seconds, max(0.0, deadline - self.now_fn())))
                    continue
                raise JournalLocked(
                    f"publication lease held by {current.get('holder')} " f"until {current.get('expires_at')}"
                )
            generation = int(current.get("generation", 0)) + 1 if current else 1
            payload = {
                "holder": holder,
                "generation": generation,
                "acquired_at": now,
                "expires_at": now + self.ttl_seconds,
            }
            self.storage.write(self.name, json.dumps(payload).encode())
            self.storage.commit()
            # Fencing: re-read and confirm our generation survived; Modal
            # Volumes have no atomic CAS, so a lost race must be detected here.
            self.storage.reload()
            check = self._read()
            if check and check.get("holder") == holder and int(check.get("generation", -1)) == generation:
                return check
            if self.now_fn() < deadline:
                self.sleep_fn(min(poll_seconds, max(0.0, deadline - self.now_fn())))
                continue
            raise JournalLocked("publication lease race lost; another run is active")

    def refresh(self, holder: str) -> None:
        self.storage.reload()
        current = self._read()
        if current is None or current.get("holder") != holder:
            raise JournalLocked(f"publication lease lost by {holder}")
        current["expires_at"] = self.now_fn() + self.ttl_seconds
        self.storage.write(self.name, json.dumps(current).encode())
        self.storage.commit()

    def release(self, holder: str) -> None:
        current = self._read()
        if current is None or current.get("holder") != holder:
            return
        payload = {
            "holder": None,
            "generation": int(current.get("generation", 0)) + 1,
            "released_at": self.now_fn(),
            "expires_at": 0,
        }
        self.storage.write(self.name, json.dumps(payload).encode())
        self.storage.commit()
