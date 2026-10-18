"""Durable publication journal for the daily canonical cron (R02).

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

Serialization contract (no file-based locks)
--------------------------------------------
Modal Volumes are not a compare-and-swap medium: read-write-reread on a lease
file cannot exclude two containers (the first review round rejected that
approach).  Cross-run serialization is therefore delegated to the Modal
platform: ``daily_cron`` runs with ``max_containers=1``, so the scheduler and
the 07:45 second chance (which forwards into ``daily_cron``) queue behind the
active run and can never overlap.  The journal is the single-writer, crash-
consistent state machine *inside* that serialized execution; every state write
is additionally guarded by the recorded prior/post SHA256 (fencing by
content, not by lease).  If ``max_containers`` is ever changed, scheduled
publication must be paused until an external lock service is introduced.

Cross-day ledger continuity (R02/B06)
-------------------------------------
Per market state path the journal must form one strictly sequential chain:

    first record: prior == attested bootstrap anchor (or None for a fresh
                  account that never existed on the Volume)
    every later record: prior_sha256 == previous record's post sha256
    committed records form a prefix of the chain (an incomplete record may
    only be followed by records created after it was recovered/committed)

``verify_chain_integrity`` enforces that chain; ``check_live_state`` then
requires the live Volume bytes to be the last committed post-state or the
post-state of a still-incomplete successor (crash-after-write window).  A new
trading day calls both *before* loading/planning any paper order, and
``prepare`` refuses a prior hash that differs from the chain expectation.
Existing pre-journal Volume states are never trusted implicitly: they must be
attested once by the operator (``write_anchors`` / the Modal
``attest_paper_state_bootstrap`` command) before the first journaled day.

The module is stdlib-only and storage-agnostic so CI can fault-inject writes
and crashes without Modal.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

STAGE_PREPARED = "PREPARED"
STAGE_GITHUB_PUBLISHED = "GITHUB_PUBLISHED"
STAGE_VOLUME_COMMITTED = "VOLUME_COMMITTED"
_STAGE_ORDER = (STAGE_PREPARED, STAGE_GITHUB_PUBLISHED, STAGE_VOLUME_COMMITTED)

BASE_DIR = "publication_journal"
ANCHOR_FILE = f"{BASE_DIR}/bootstrap_anchors.json"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class JournalError(RuntimeError):
    """Base journal failure."""


class JournalCorrupt(JournalError):
    """Journal/payload/state divergence; requires manual recovery (fail closed)."""


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

    def _anchor_path(self) -> str:
        return f"{self.base_dir}/bootstrap_anchors.json" if self.base_dir != BASE_DIR else ANCHOR_FILE

    # ------------------------------------------------------------- records

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

    def all_records(self) -> list:
        records = []
        for name in self.storage.list(self.base_dir):
            record = self.load(name)
            if record is not None:
                records.append(record)
        records.sort(key=lambda record: record["signal_date"])
        return records

    def incomplete_records(self) -> list:
        return [record for record in self.all_records() if record["stage"] != STAGE_VOLUME_COMMITTED]

    def _path_records(self, path: str) -> list:
        """[(record, market, entry)] ascending for one state path."""
        chain = []
        for record in self.all_records():
            for market, entry in record["states"].items():
                if entry.get("path") == path:
                    chain.append((record, market, entry))
        return chain

    # ------------------------------------------------------------- anchors

    def load_anchors(self) -> dict:
        raw = self.storage.read(self._anchor_path())
        if raw is None:
            return {}
        try:
            payload = json.loads(raw.decode())
        except ValueError as exc:
            raise JournalCorrupt("bootstrap anchor file unreadable") from exc
        anchors = payload.get("anchors") if isinstance(payload, dict) else None
        if not isinstance(anchors, dict):
            raise JournalCorrupt("bootstrap anchor file malformed")
        for path, meta in anchors.items():
            if not isinstance(meta, dict) or not _SHA256_RE.fullmatch(str(meta.get("sha256", ""))):
                raise JournalCorrupt(f"bootstrap anchor malformed for {path}")
        return anchors

    def write_anchors(self, anchors: dict, note: str, holder: str) -> dict:
        """Explicit operator attestation of pre-journal Volume states (R02/B06).

        Anchors are only accepted for paths with *no* journal history; they can
        never whitewash a broken chain.  Re-attesting the same bytes is
        idempotent; different bytes for an existing anchor are refused.
        """
        if not anchors:
            raise JournalError("no anchors supplied")
        existing = self.load_anchors()
        merged = dict(existing)
        for path, sha in anchors.items():
            if not _SHA256_RE.fullmatch(str(sha)):
                raise JournalError(f"invalid anchor sha256 for {path!r}")
            if self._path_records(path):
                raise JournalError(
                    f"cannot attest {path}: journal history already exists; "
                    "resolve the chain instead of overriding it"
                )
            current = existing.get(path)
            if current is not None and current.get("sha256") != sha:
                raise JournalError(f"anchor already attested with different bytes: {path}")
            merged[path] = {
                "sha256": sha,
                "note": note,
                "attested_at": self.now_fn(),
                "holder": holder,
            }
        payload = {"anchors": merged, "updated_at": self.now_fn()}
        self.storage.write(self._anchor_path(), json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode())
        self.storage.commit()
        return merged

    # ------------------------------------------------- chain / live state

    def verify_chain_integrity(self) -> None:
        """Strict per-path chain: prior == previous post, committed prefix only."""
        anchors = self.load_anchors()
        path_records = {}
        for record in self.all_records():
            for market, entry in record["states"].items():
                path_records.setdefault(entry["path"], []).append((record, market, entry))
        for path, chain in path_records.items():
            anchor = anchors.get(path, {}).get("sha256")
            previous_post = None
            first = True
            seen_incomplete = False
            for record, _market, entry in chain:
                prior = entry.get("prior_sha256")
                if first:
                    if anchor is None:
                        if prior is not None:
                            raise JournalCorrupt(
                                "BLOCKED_MANUAL_RECOVERY: first journal record for "
                                f"{path} claims prior={prior} without an attested bootstrap anchor"
                            )
                    elif prior != anchor:
                        raise JournalCorrupt(
                            "BLOCKED_MANUAL_RECOVERY: first journal prior for "
                            f"{path} does not match the attested bootstrap anchor"
                        )
                    first = False
                elif prior != previous_post:
                    raise JournalCorrupt(
                        "BLOCKED_MANUAL_RECOVERY: journal chain break for "
                        f"{path} at {record['signal_date']}: prior={prior} != previous post={previous_post}"
                    )
                if record["stage"] == STAGE_VOLUME_COMMITTED:
                    if seen_incomplete:
                        raise JournalCorrupt(
                            "BLOCKED_MANUAL_RECOVERY: committed record after an "
                            f"incomplete one for {path} ({record['signal_date']})"
                        )
                else:
                    seen_incomplete = True
                previous_post = entry["sha256"]

    def expected_prior_for_path(self, path: str) -> str:
        """The only prior hash a new record may declare for ``path``."""
        chain = self._path_records(path)
        if chain:
            if any(record["stage"] != STAGE_VOLUME_COMMITTED for record, _m, _e in chain):
                raise JournalCorrupt(
                    "BLOCKED_MANUAL_RECOVERY: incomplete publication must be "
                    f"recovered before a new record for {path}"
                )
            return chain[-1][2]["sha256"]
        return self.load_anchors().get(path, {}).get("sha256")

    def check_live_state(self, path: str, actual) -> None:
        """Live Volume bytes must be a legal state for this path (R02/B06)."""
        self.verify_chain_integrity()
        chain = self._path_records(path)
        anchor = self.load_anchors().get(path, {}).get("sha256")
        if not chain:
            if actual is None:
                if anchor is not None:
                    raise JournalCorrupt(
                        "BLOCKED_MANUAL_RECOVERY: anchored paper state is missing: " + path
                    )
                return
            if anchor is None:
                raise JournalCorrupt(
                    "BLOCKED_MANUAL_RECOVERY: unanchored existing paper state; run the "
                    f"approved bootstrap attestation before trading on it: {path}"
                )
            if _sha256(actual) != anchor:
                raise JournalCorrupt(
                    "BLOCKED_MANUAL_RECOVERY: paper state differs from its attested "
                    f"bootstrap anchor: {path}"
                )
            return
        committed = [pair for pair in chain if pair[0]["stage"] == STAGE_VOLUME_COMMITTED]
        allowed = set()
        if committed:
            allowed.add(committed[-1][2]["sha256"])
        else:
            first_prior = chain[0][2].get("prior_sha256")
            allowed.add(first_prior)  # None when the account never existed
        for record, _market, entry in chain:
            if record["stage"] != STAGE_VOLUME_COMMITTED:
                allowed.add(entry["sha256"])
        if actual is None:
            if None in allowed:
                return
            raise JournalCorrupt(
                "BLOCKED_MANUAL_RECOVERY: committed paper state file is missing: " + path
            )
        if _sha256(actual) not in allowed:
            raise JournalCorrupt(
                "BLOCKED_MANUAL_RECOVERY: paper state diverged from the journal chain: " + path
            )

    def check_paper_state_entry(self, state_paths: dict) -> None:
        """Day-start gate: every market's live state must be chain-legal.

        ``state_paths`` maps market -> state path (relative to the Volume
        root).  Raises on the first violation; callers must run it before
        loading or planning any paper order.
        """
        for market, path in state_paths.items():
            actual = self.storage.read(path)
            try:
                self.check_live_state(path, actual)
            except JournalCorrupt as exc:
                raise JournalCorrupt(f"{market}: {exc}") from exc

    # ------------------------------------------------------------- prepare

    def prepare(
        self,
        signal_date: str,
        holder: str,
        files: dict,
        states: dict,
    ) -> dict:
        """Freeze the publication intent.  ``files`` maps path -> content str;
        ``states`` maps market -> {path, prior_sha256, content}.

        The declared prior must equal the chain expectation
        (``expected_prior_for_path``); drifted or unanchored live states are
        refused here as well as at the day-start gate.
        """
        if self.load(signal_date) is not None:
            raise JournalError(f"journal already exists for {signal_date}")
        for market, entry in states.items():
            expected = self.expected_prior_for_path(entry["path"])
            declared = entry.get("prior_sha256")
            if declared != expected:
                raise JournalCorrupt(
                    "BLOCKED_MANUAL_RECOVERY: declared prior for "
                    f"{market} ({entry['path']}) is {declared}, expected {expected}"
                )
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
                    "BLOCKED_MANUAL_RECOVERY: published artifact differs from frozen journal "
                    f"hash: {path}"
                )
        return sorted(missing)

    def plan_state_writes(self, record: dict, current: dict) -> dict:
        """Return market -> entry to write, or fail closed on divergence.

        A state file may only be replaced when it is untouched since the journal
        was prepared (its bytes still match ``prior_sha256``) or already carries
        the frozen post-state bytes.  A file that existed when the journal was
        prepared (``prior_sha256`` recorded) must NOT disappear silently: that
        is an unrecoverable ledger loss and fails closed (R02/B04).
        """
        writes = {}
        for market, entry in record["states"].items():
            target = entry["sha256"]
            prior = entry.get("prior_sha256")
            actual = current.get(market)
            if actual is None:
                if prior is not None:
                    raise JournalCorrupt(
                        "BLOCKED_MANUAL_RECOVERY: previously existing paper state file is "
                        f"missing ({market} {entry['path']}); refusing to recreate it from "
                        "the frozen post-state"
                    )
                writes[market] = entry
                continue
            actual_sha = _sha256(actual)
            if actual_sha == target:
                writes[market] = entry
                continue
            if prior is not None and actual_sha == prior:
                writes[market] = entry
                continue
            raise JournalCorrupt(
                "BLOCKED_MANUAL_RECOVERY: paper state diverged from journal "
                f"({market} {entry['path']}); refusing to overwrite"
            )
        return writes

    def verify_committed_states(self, record: dict, current: dict) -> None:
        """Verify live Volume states against the journal chain (R02/B05+B06)."""
        for market, entry in record["states"].items():
            actual = current.get(market)
            try:
                self.check_live_state(entry["path"], actual)
            except JournalCorrupt as exc:
                raise JournalCorrupt(f"{market}: {exc}") from exc

    def _write_record(self, record: dict) -> None:
        self.storage.write(
            self._record_path(record["signal_date"]),
            json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2).encode(),
        )
        self.storage.commit()
