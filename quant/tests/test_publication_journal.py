"""R02 behavioral tests: publication journal and fault-injected recovery.

Simulates the Modal Volume with an in-memory storage and the GitHub repo with a
path->bytes dict, then injects crashes at each commit stage.  A rerun must
complete the publication from the frozen payloads without regenerating content,
without double-persisting divergent state and without rewriting published
history.

Cross-run serialization itself is a Modal platform property (max_containers=1)
pinned by test_modal_structure.py::test_daily_cron_single_execution_contract;
these tests cover the single-writer journal state machine *inside* that
serialized execution, including every crash/replay window and the fail-closed
preconditions on Volume state files.
"""

import hashlib

import pytest

from publication_journal import (
    JournalCorrupt,
    JournalError,
    PublicationJournal,
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
        # push_files applied the commit but the response was lost / crashed.
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
    if fail_at == "after_volume_commit":
        raise InjectedCrash()
    return record


def test_prepare_freezes_hashes_and_payloads_survive_reload():
    journal, _storage = make_journal()
    record = journal.prepare("2026-10-18", "invocation-a", FILES, STATES)
    assert record["stage"] == "PREPARED"
    assert record["files"] == {path: sha(content) for path, content in FILES.items()}
    assert record["states"]["csi1000"]["sha256"] == sha(STATES["csi1000"]["content"])
    files, states = journal.read_payloads(record)
    assert files == FILES
    assert states["csi500"]["content"] == STATES["csi500"]["content"]


def test_prepare_refuses_double_freeze():
    journal, _storage = make_journal()
    journal.prepare("2026-10-18", "invocation-a", FILES, STATES)
    with pytest.raises(JournalError):
        journal.prepare("2026-10-18", "invocation-b", FILES, STATES)


def test_payload_corruption_is_detected():
    journal, storage = make_journal()
    record = journal.prepare("2026-10-18", "invocation-a", FILES, STATES)
    payload_path = record["payloads"]["files"]["path"]
    storage.files[payload_path] = storage.files[payload_path].replace(b"SH600000", b"SH000001")
    with pytest.raises(JournalCorrupt):
        journal.read_payloads(record)


def test_plan_completion_missing_matches_and_mismatch():
    journal, _storage = make_journal()
    record = journal.prepare("2026-10-18", "invocation-a", FILES, STATES)
    all_missing = journal.plan_completion(record, {path: None for path in FILES})
    assert all_missing == sorted(FILES)
    published = {path: content.encode() for path, content in FILES.items()}
    assert journal.plan_completion(record, published) == []
    published[next(iter(FILES))] = b"tampered"
    with pytest.raises(JournalCorrupt, match="BLOCKED_MANUAL_RECOVERY"):
        journal.plan_completion(record, published)


def test_plan_state_writes_accepts_prior_or_target_and_rejects_divergence():
    journal, _storage = make_journal()
    record = journal.prepare("2026-10-18", "invocation-a", FILES, STATES)
    untouched = {"csi1000": None, "csi500": None}
    assert set(journal.plan_state_writes(record, untouched)) == {"csi1000", "csi500"}
    committed = {market: entry["content"].encode() for market, entry in STATES.items()}
    assert set(journal.plan_state_writes(record, committed)) == {"csi1000", "csi500"}
    diverged = dict(committed)
    diverged["csi1000"] = b'{"version": 2, "cash": 999.0, "foreign": true}\n'
    with pytest.raises(JournalCorrupt, match="BLOCKED_MANUAL_RECOVERY"):
        journal.plan_state_writes(record, diverged)


def _prepared_authored_states():
    """Prior states where the csi1000 account file already exists on disk."""
    states = {market: dict(entry) for market, entry in STATES.items()}
    states["csi1000"]["prior_sha256"] = sha('{"version": 2, "cash": 50.0}\n')
    return states


def test_missing_previously_existing_state_file_fails_closed():
    """R02/B04: a recorded prior state that disappeared must never be recreated."""
    journal, _storage = make_journal()
    record = journal.prepare("2026-10-18", "invocation-a", FILES, _prepared_authored_states())
    # csi500 never existed (prior None) -> creation allowed; csi1000 prior exists
    # but the file is gone -> fail closed.
    with pytest.raises(JournalCorrupt, match="missing"):
        journal.plan_state_writes(record, {"csi1000": None, "csi500": None})


def test_truncated_or_replaced_state_file_fails_closed():
    journal, _storage = make_journal()
    record = journal.prepare("2026-10-18", "invocation-a", FILES, _prepared_authored_states())
    truncated = b'{"version": 2, "cash": 50.0'  # prior file cut off mid-write
    with pytest.raises(JournalCorrupt, match="diverged"):
        journal.plan_state_writes(record, {"csi1000": truncated, "csi500": None})
    replaced = b'{"version": 2, "cash": 50.0}\nextra\n'
    with pytest.raises(JournalCorrupt, match="diverged"):
        journal.plan_state_writes(record, {"csi1000": replaced, "csi500": None})


def test_multimarket_partial_persistence_is_recoverable_and_never_duplicated():
    journal, _storage = make_journal()
    github = {}
    state_fs = {}
    record = journal.prepare("2026-10-18", "invocation-a", FILES, STATES)
    with pytest.raises(InjectedCrash):
        simulate_completion(record, journal, github, state_fs, fail_at="mid_states")
    # First market was persisted, second was not; the rerun writes both from the
    # frozen payloads and reaches VOLUME_COMMITTED exactly once.
    assert state_fs.get(STATES["csi1000"]["path"]) == STATES["csi1000"]["content"].encode()
    final = simulate_completion(journal.load("2026-10-18"), journal, github, state_fs)
    assert final["stage"] == "VOLUME_COMMITTED"
    assert state_fs == {entry["path"]: entry["content"].encode() for entry in STATES.values()}


@pytest.mark.parametrize(
    "fail_at",
    ["before_push", "after_push", "mid_states", "before_volume_commit"],
)
def test_crash_at_every_stage_recovers_idempotently(fail_at):
    journal, _storage = make_journal()
    github = {}
    state_fs = {}
    record = journal.prepare("2026-10-18", "invocation-a", FILES, STATES)
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
    assert journal.incomplete_records() == []


def test_crash_after_volume_commit_then_fast_path_verifies_without_repush():
    journal, _storage = make_journal()
    github = {}
    state_fs = {}
    record = journal.prepare("2026-10-18", "invocation-a", FILES, STATES)
    with pytest.raises(InjectedCrash):
        simulate_completion(record, journal, github, state_fs, fail_at="after_volume_commit")
    committed = journal.load("2026-10-18")
    assert committed["stage"] == "VOLUME_COMMITTED"
    assert journal.incomplete_records() == []
    # The committed fast path verifies instead of re-completing; nothing changes.
    current = {market: state_fs.get(entry["path"]) for market, entry in committed["states"].items()}
    journal.verify_committed_states(committed, current)
    assert github == {path: content.encode() for path, content in FILES.items()}


def test_verify_committed_states_accepts_chain_and_rejects_loss_or_divergence():
    journal, _storage = make_journal()
    github = {}
    state_fs = {}
    record = journal.prepare("2026-10-16", "invocation-a", FILES, STATES)
    simulate_completion(record, journal, github, state_fs)
    committed = journal.load("2026-10-16")
    current = {market: state_fs.get(entry["path"]) for market, entry in committed["states"].items()}
    journal.verify_committed_states(committed, current)

    # A later serialized run legitimately advances both accounts; its prior
    # hashes link back to the committed post-states, so the chain verifies.
    later_states = {
        market: {
            "path": entry["path"],
            "prior_sha256": sha(entry["content"]),
            "content": entry["content"].replace('"cash": 100', '"cash": 111').replace('"cash": 200', '"cash": 222'),
        }
        for market, entry in STATES.items()
    }
    later = journal.prepare("2026-10-17", "invocation-b", FILES, later_states)
    simulate_completion(later, journal, github, state_fs)
    advanced = {market: state_fs.get(entry["path"]) for market, entry in committed["states"].items()}
    journal.verify_committed_states(committed, advanced)

    # A later PREPARED record that read the live state proves those exact bytes
    # were current at that time (prior_sha256 link), even before it writes.
    live_bytes = b'{"version": 2, "cash": 7.0}\n'
    journal.prepare(
        "2026-10-19",
        "invocation-c",
        FILES,
        {
            market: {
                "path": entry["path"],
                "prior_sha256": hashlib.sha256(live_bytes).hexdigest(),
                "content": entry["content"],
            }
            for market, entry in STATES.items()
        },
    )
    prior_link = {"csi1000": live_bytes, "csi500": state_fs[STATES["csi500"]["path"]]}
    journal.verify_committed_states(committed, prior_link)

    # Missing committed file fails closed.
    lost = dict(advanced)
    lost["csi1000"] = None
    with pytest.raises(JournalCorrupt, match="missing"):
        journal.verify_committed_states(committed, lost)

    # Unrelated bytes fail closed.
    foreign = dict(advanced)
    foreign["csi1000"] = b'{"version": 2, "cash": 42.0, "unrelated": true}\n'
    with pytest.raises(JournalCorrupt, match="chain"):
        journal.verify_committed_states(committed, foreign)


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
    record = journal.prepare("2026-10-18", "invocation-a", FILES, state_records)
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

    helper(record, journal, {})
    assert pushes == FILES
    for entry in state_records.values():
        assert (tmp_path / entry["path"]).read_bytes() == entry["content"].encode()
    assert journal.load("2026-10-18")["stage"] == "VOLUME_COMMITTED"

    # A repeated call must not re-push or regenerate anything.
    helper(journal.load("2026-10-18"), journal, {})
    assert len(pushes) == len(FILES)


def test_modal_verify_helper_checks_volume_states(tmp_path, monkeypatch):
    """B05: the committed-record verifier must also validate Volume states."""
    import sys
    import types

    journal, _storage = make_journal()
    published = {}

    def push_files(token, repo, branch, files, message):
        for path, content in files.items():
            published[path] = content.encode()
        return "c" * 40

    gh_module = types.ModuleType("github_commit")
    gh_module.push_files = push_files
    monkeypatch.setitem(sys.modules, "github_commit", gh_module)
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    namespace = {
        "VOL_ROOT": tmp_path,
        "journal": journal,
        "vol": types.SimpleNamespace(commit=lambda: None),
        "GITHUB_REPO": "owner/repo",
        "SIGNAL_BRANCH": "main",
        "_github_existing_files": lambda paths, headers: {path: published.get(path) for path in paths},
    }
    exec(compile(_modal_helper_source("_verify_committed_record"), "helper", "exec"), namespace)
    verify = namespace["_verify_committed_record"]

    record = journal.prepare("2026-10-18", "invocation-a", FILES, STATES)
    simulate_completion(record, journal, published, {})
    # Write the committed states to disk, then verify the full glue.
    for market, entry in record["states"].items():
        p = tmp_path / entry["path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(STATES[market]["content"].encode())
    verify(journal.load("2026-10-18"), journal, {})

    # Losing one account state must fail closed instead of appearing verified.
    (tmp_path / STATES["csi500"]["path"]).unlink()
    with pytest.raises(JournalCorrupt, match="missing"):
        verify(journal.load("2026-10-18"), journal, {})
