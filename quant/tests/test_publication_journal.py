"""R02 behavioral tests: publication journal, lease and fault-injected recovery.

Simulates the Modal Volume with an in-memory storage and the GitHub repo with a
path->bytes dict, then injects crashes at each commit stage.  A rerun must
complete the publication from the frozen payloads without regenerating content,
without double-persisting divergent state and without rewriting published
history.
"""

import hashlib
import json

import pytest

from publication_journal import (
    JournalCorrupt,
    JournalError,
    JournalLocked,
    PublicationJournal,
    PublicationLease,
)


class InjectedCrash(RuntimeError):
    pass


class MemoryStorage:
    def __init__(self):
        self.files = {}
        self.reloads = 0
        self.commits = 0

    def read(self, rel):
        return self.files.get(rel)

    def write(self, rel, data):
        self.files[rel] = bytes(data)

    def list(self, rel):
        prefix = rel.rstrip("/") + "/"
        names = set()
        for key in self.files:
            if key.startswith(prefix):
                names.add(key[len(prefix) :].split("/", 1)[0])
        return sorted(names)

    def reload(self):
        self.reloads += 1

    def commit(self):
        self.commits += 1


FILES = {
    "quant/data/signals/2026-10-18_top20_lgb158.csv": "rank,instrument,score\n1,SH600000,0.5\n",
    "quant/data/signals/2026-10-18_paper_portfolio.json": '{"market": "csi1000"}\n',
}
STATES = {
    "csi1000": {
        "path": "paper_portfolio/csi1000__canonical.json",
        "prior_sha256": None,
        "content": '{"version": 2, "cash": 100.0}\n',
    },
    "csi500": {
        "path": "paper_portfolio/csi500__canonical.json",
        "prior_sha256": None,
        "content": '{"version": 2, "cash": 200.0}\n',
    },
}


def make_journal():
    storage = MemoryStorage()
    return PublicationJournal(storage), storage


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def simulate_completion(record, journal, github, state_fs, fail_at=None):
    """Mirror the modal helper so crashes can be injected stage by stage."""
    files, states = journal.read_payloads(record)
    published = {path: github.get(path) for path in files}
    missing = journal.plan_completion(record, published)
    if fail_at == "before_push":
        raise InjectedCrash()
    for path in missing:
        github[path] = files[path].encode()
    if fail_at == "after_push":
        raise InjectedCrash()
    record = journal.mark(record, "GITHUB_PUBLISHED", github_commit="c" * 40)
    current = {market: state_fs.get(entry["path"]) for market, entry in record["states"].items()}
    journal.plan_state_writes(record, current)
    for index, market in enumerate(sorted(record["states"])):
        if fail_at == "mid_states" and index == 1:
            raise InjectedCrash()
        state_fs[record["states"][market]["path"]] = states[market]["content"].encode()
    if fail_at == "before_volume_commit":
        raise InjectedCrash()
    record = journal.mark(record, "VOLUME_COMMITTED")
    return record


def test_prepare_freezes_hashes_and_payloads_survive_reload():
    journal, _storage = make_journal()
    record = journal.prepare("2026-10-18", "holder-a", FILES, STATES)
    assert record["stage"] == "PREPARED"
    assert record["files"] == {path: sha(content) for path, content in FILES.items()}
    assert record["states"]["csi1000"]["sha256"] == sha(STATES["csi1000"]["content"])
    files, states = journal.read_payloads(record)
    assert files == FILES
    assert states["csi500"]["content"] == STATES["csi500"]["content"]


def test_prepare_refuses_double_freeze():
    journal, _storage = make_journal()
    journal.prepare("2026-10-18", "holder-a", FILES, STATES)
    with pytest.raises(JournalError):
        journal.prepare("2026-10-18", "holder-b", FILES, STATES)


def test_payload_corruption_is_detected():
    journal, storage = make_journal()
    record = journal.prepare("2026-10-18", "holder-a", FILES, STATES)
    payload_path = record["payloads"]["files"]["path"]
    storage.files[payload_path] = storage.files[payload_path].replace(b"SH600000", b"SH000001")
    with pytest.raises(JournalCorrupt):
        journal.read_payloads(record)


def test_plan_completion_missing_matches_and_mismatch():
    journal, _storage = make_journal()
    record = journal.prepare("2026-10-18", "holder-a", FILES, STATES)
    all_missing = journal.plan_completion(record, {path: None for path in FILES})
    assert all_missing == sorted(FILES)
    published = {path: content.encode() for path, content in FILES.items()}
    assert journal.plan_completion(record, published) == []
    published[next(iter(FILES))] = b"tampered"
    with pytest.raises(JournalCorrupt, match="BLOCKED_MANUAL_RECOVERY"):
        journal.plan_completion(record, published)


def test_plan_state_writes_accepts_prior_or_target_and_rejects_divergence():
    journal, _storage = make_journal()
    record = journal.prepare("2026-10-18", "holder-a", FILES, STATES)
    untouched = {"csi1000": None, "csi500": None}
    assert set(journal.plan_state_writes(record, untouched)) == {"csi1000", "csi500"}
    committed = {market: entry["content"].encode() for market, entry in STATES.items()}
    assert set(journal.plan_state_writes(record, committed)) == {"csi1000", "csi500"}
    diverged = dict(committed)
    diverged["csi1000"] = b'{"version": 2, "cash": 999.0, "foreign": true}\n'
    with pytest.raises(JournalCorrupt, match="BLOCKED_MANUAL_RECOVERY"):
        journal.plan_state_writes(record, diverged)


@pytest.mark.parametrize(
    "fail_at",
    ["before_push", "after_push", "mid_states", "before_volume_commit"],
)
def test_crash_at_every_stage_recovers_idempotently(fail_at):
    journal, _storage = make_journal()
    github = {}
    state_fs = {}
    record = journal.prepare("2026-10-18", "holder-a", FILES, STATES)
    with pytest.raises(InjectedCrash):
        simulate_completion(record, journal, github, state_fs, fail_at=fail_at)
    # Rerun (e.g. Modal retry / 07:45 second chance / next day) must finish from
    # the frozen payloads without any regeneration input.
    final = simulate_completion(journal.load("2026-10-18"), journal, github, state_fs)
    assert final["stage"] == "VOLUME_COMMITTED"
    assert github == {path: content.encode() for path, content in FILES.items()}
    assert state_fs == {entry["path"]: entry["content"].encode() for entry in STATES.values()}
    files, states = journal.read_payloads(final)
    assert files == FILES and states["csi1000"]["content"] == STATES["csi1000"]["content"]
    if fail_at in ("before_push", "after_push"):
        assert [r for r in journal.incomplete_records()] == []


def test_completed_record_is_not_incomplete_and_survives_regeneration_check():
    journal, _storage = make_journal()
    github = {}
    state_fs = {}
    record = journal.prepare("2026-10-18", "holder-a", FILES, STATES)
    simulate_completion(record, journal, github, state_fs)
    assert journal.incomplete_records() == []
    # A later run with drifted regenerated content must adopt the frozen record:
    # the completed record's plan still validates against the live repo bytes.
    live = journal.load("2026-10-18")
    assert journal.plan_completion(live, github) == []
    journal.plan_state_writes(live, {m: state_fs[e["path"]] for m, e in live["states"].items()})


def test_lease_serializes_holders_and_release_advances_generation():
    _journal, storage = make_journal()
    lease = PublicationLease(storage, now_fn=lambda: 1000.0)
    first = lease.acquire("A")
    with pytest.raises(JournalLocked):
        lease.acquire("B", wait_seconds=0)
    lease.release("A")
    second = lease.acquire("B", wait_seconds=0)
    assert second["generation"] > first["generation"]


def test_lease_expiry_and_refresh():
    _journal, storage = make_journal()
    clock = {"now": 1000.0}
    lease = PublicationLease(storage, ttl_seconds=100, now_fn=lambda: clock["now"])
    lease.acquire("A")
    clock["now"] += 50
    lease.refresh("A")
    clock["now"] += 80  # 130s after acquisition, 30s after refresh -> still held
    with pytest.raises(JournalLocked):
        lease.acquire("B", wait_seconds=0)
    clock["now"] += 100  # lease expired
    lease.acquire("B", wait_seconds=0)
    with pytest.raises(JournalLocked, match="lost by"):
        lease.refresh("A")


def test_journal_records_sort_oldest_first():
    journal, _storage = make_journal()
    journal.prepare("2026-10-18", "h", FILES, STATES)
    journal.prepare("2026-10-16", "h", FILES, STATES)
    records = journal.incomplete_records()
    assert [record["signal_date"] for record in records] == ["2026-10-16", "2026-10-18"]


def _modal_helper_source(name):
    import ast

    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    src = (root / "quant/code/production/modal_qlib_cn_a10g.py").read_text()
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(src, node)
    raise AssertionError(f"{name} not found")


def test_modal_completion_helper_pushes_then_persists_and_is_idempotent(tmp_path, monkeypatch):
    """Run the real _complete_journal_publication glue against stubs."""
    import sys
    import types

    journal, _storage = make_journal()
    pushes = {}
    published = {}

    def push_files(token, repo, branch, files, message):
        for path, content in files.items():
            pushes[path] = content
            published[path] = content.encode()
        return "c" * 40

    gh_module = types.ModuleType("github_commit")
    gh_module.push_files = push_files
    monkeypatch.setitem(sys.modules, "github_commit", gh_module)
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    state_records = {
        "csi1000": {"path": "paper_portfolio/csi1000.json", "prior_sha256": None,
                    "content": '{"version": 2, "cash": 1.0}\n'},
        "csi500": {"path": "paper_portfolio/csi500.json", "prior_sha256": None,
                   "content": '{"version": 2, "cash": 2.0}\n'},
    }
    record = journal.prepare("2026-10-18", "holder", FILES, state_records)
    namespace = {
        "VOL_ROOT": tmp_path,
        "journal": journal,
        "vol": types.SimpleNamespace(commit=lambda: None),
        "GITHUB_REPO": "owner/repo",
        "SIGNAL_BRANCH": "main",
        "_github_existing_files": lambda paths, headers: {path: published.get(path) for path in paths},
    }
    exec(compile(_modal_helper_source("_complete_journal_publication"), "helper", "exec"), namespace)
    helper = namespace["_complete_journal_publication"]

    class LeaseStub:
        def refresh(self, holder):
            pass

    helper(record, journal, LeaseStub(), "holder", {})
    assert pushes == FILES
    for entry in state_records.values():
        assert (tmp_path / entry["path"]).read_bytes() == entry["content"].encode()
    assert journal.load("2026-10-18")["stage"] == "VOLUME_COMMITTED"

    # A repeated call must not re-push or regenerate anything.
    helper(journal.load("2026-10-18"), journal, LeaseStub(), "holder", {})
    assert len(pushes) == len(FILES)
