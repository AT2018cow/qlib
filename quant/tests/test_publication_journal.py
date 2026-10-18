"""R02 behavioral tests: publication journal, strict cross-day chains and fault-injected recovery.

Simulates the Modal Volume with an in-memory storage and the GitHub repo with a
path->bytes dict, then injects crashes and cross-day tampering.  The invariants
under test (review R02/B06):

- per state path the journal must be one strictly sequential chain
  (``prior == previous post``, first prior == attested anchor or None);
- a new day may only trade on live bytes that equal the last committed post
  state (or an incomplete successor's post during a crash window);
- any drift/missing committed state fails closed *before* prepare side effects;
- existing pre-journal states are never trusted without an explicit anchor.

Cross-run serialization itself is a Modal platform property (max_containers=1)
pinned by test_modal_structure.py::test_daily_cron_single_execution_contract.
"""

import hashlib
import json

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
PATHS = {
    "csi1000": "paper_portfolio/csi1000__canonical.json",
    "csi500": "paper_portfolio/csi500__canonical.json",
    "csi300": "paper_portfolio/csi300__canonical.json",
}
DAY1_CONTENT = {
    "csi1000": '{"version": 2, "cash": 100.0}\n',
    "csi500": '{"version": 2, "cash": 200.0}\n',
}
STATES = {
    market: {"path": PATHS[market], "prior_sha256": None, "content": content}
    for market, content in DAY1_CONTENT.items()
}


def make_journal():
    storage = MemoryStorage()
    return PublicationJournal(storage), storage


def sha(data):
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


def day_states(journal, contents):
    """Build state entries with the chain-expected prior (mirrors modal_cron)."""
    return {
        market: {
            "path": PATHS[market],
            "prior_sha256": journal.expected_prior_for_path(PATHS[market]),
            "content": content,
        }
        for market, content in contents.items()
    }


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


def complete_day(journal, date="2026-10-16", contents=None):
    """Publish one fully successful day; returns (record, github, state_fs).

    State bytes are written into the journal storage itself so day-start gates
    that read the live Volume see the same bytes."""
    contents = DAY1_CONTENT if contents is None else contents
    github = {}
    state_fs = journal.storage.files
    record = journal.prepare(date, f"inv-{date}", FILES, day_states(journal, contents))
    simulate_completion(record, journal, github, state_fs)
    return journal.load(date), github, state_fs


def inject_record(storage, date, stage, prior, post, market="csi1000"):
    """Write a raw journal record (bypassing prepare) to test chain rejection."""
    fake = {
        "publication_id": f"signal-{date}",
        "signal_date": date,
        "stage": stage,
        "holder": "injected",
        "created_at": "t",
        "updated_at": "t",
        "github_commit": None,
        "files": {},
        "states": {market: {"path": PATHS[market], "prior_sha256": prior, "sha256": post}},
        "payloads": {},
    }
    storage.files[f"publication_journal/{date}/record.json"] = json.dumps(fake).encode()


# ---------------------------------------------------------------- records/payloads


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


# ---------------------------------------------------------------- bootstrap anchors


def test_existing_unanchored_state_is_rejected_until_attested():
    journal, _storage = make_journal()
    path = PATHS["csi1000"]
    live = b'{"version": 2, "cash": 1.0}\n'
    with pytest.raises(JournalCorrupt, match="unanchored"):
        journal.check_live_state(path, live)
    journal.write_anchors({path: sha(live)}, note="reviewed pre-journal state", holder="operator")
    journal.check_live_state(path, live)
    with pytest.raises(JournalCorrupt, match="anchor"):
        journal.check_live_state(path, b'{"version": 2, "cash": 2.0}\n')
    with pytest.raises(JournalCorrupt, match="missing"):
        journal.check_live_state(path, None)


def test_anchor_write_refuses_history_and_mismatched_reanchor():
    journal, _storage = make_journal()
    complete_day(journal, contents={"csi1000": DAY1_CONTENT["csi1000"]})
    with pytest.raises(JournalError, match="journal history"):
        journal.write_anchors({PATHS["csi1000"]: sha(b"whatever")}, note="n", holder="op")
    journal.write_anchors({PATHS["csi500"]: sha(b"reviewed")}, note="n", holder="op")
    journal.write_anchors({PATHS["csi500"]: sha(b"reviewed")}, note="re-run", holder="op")  # idempotent
    with pytest.raises(JournalError, match="different bytes"):
        journal.write_anchors({PATHS["csi500"]: sha(b"changed")}, note="n", holder="op")


def test_anchored_fresh_account_chain_accepts_matching_prior():
    journal, _storage = make_journal()
    prior = b'{"version": 2, "cash": 50.0}\n'
    path = PATHS["csi1000"]
    journal.write_anchors({path: sha(prior)}, note="n", holder="op")
    states = day_states(journal, {"csi1000": '{"version": 2, "cash": 60.0}\n'})
    assert states["csi1000"]["prior_sha256"] == sha(prior)
    record = journal.prepare("2026-10-18", "inv", FILES, states)
    journal.verify_chain_integrity()
    journal.check_live_state(path, prior)
    live_fs = {path: prior}
    simulate_completion(record, journal, {}, live_fs)
    journal.check_live_state(path, live_fs[path])
    assert live_fs[path] == b'{"version": 2, "cash": 60.0}\n'


# ---------------------------------------------------------------- cross-day (B06)


def test_cross_day_tamper_fails_before_any_prepare_side_effect():
    journal, storage = make_journal()
    complete_day(journal)
    path = PATHS["csi1000"]
    tampered = DAY1_CONTENT["csi1000"].encode() + b" "
    storage.files[path] = tampered
    with pytest.raises(JournalCorrupt, match="diverged"):
        journal.check_paper_state_entry({market: PATHS[market] for market in PATHS})
    bogus = day_states(journal, {"csi1000": '{"version": 2, "cash": 101.0}\n'})
    bogus["csi1000"]["prior_sha256"] = sha(tampered)
    with pytest.raises(JournalCorrupt, match="expected"):
        journal.prepare("2026-10-17", "inv", FILES, bogus)
    assert journal.load("2026-10-17") is None


def test_bogus_successor_prior_never_makes_an_old_record_valid():
    """Reviewer repro: an arbitrary later PREPARED prior must not whitelist drift."""
    journal, storage = make_journal()
    complete_day(journal)
    path = PATHS["csi1000"]
    inject_record(storage, "2026-10-17", "PREPARED", prior=sha(b"garbage"), post=sha(b"post"))
    with pytest.raises(JournalCorrupt, match="chain break"):
        journal.verify_chain_integrity()
    with pytest.raises(JournalCorrupt, match="chain break"):
        journal.check_live_state(path, DAY1_CONTENT["csi1000"].encode())
    with pytest.raises(JournalCorrupt):
        journal.prepare("2026-10-18", "inv", FILES, day_states(journal, {"csi1000": "x"}))


def test_committed_record_after_incomplete_is_rejected():
    journal, storage = make_journal()
    complete_day(journal)
    record2 = journal.prepare(
        "2026-10-17", "inv2", FILES,
        day_states(journal, {"csi1000": '{"version": 2, "cash": 101.0}\n'}),
    )
    inject_record(storage, "2026-10-18", "VOLUME_COMMITTED",
                  prior=record2["states"]["csi1000"]["sha256"], post=sha(b"later"))
    with pytest.raises(JournalCorrupt, match="committed record after"):
        journal.verify_chain_integrity()


def test_expected_prior_requires_recovery_of_incomplete_successor():
    journal, _storage = make_journal()
    complete_day(journal)
    journal.prepare(
        "2026-10-17", "inv2", FILES,
        day_states(journal, {"csi1000": '{"version": 2, "cash": 101.0}\n'}),
    )
    with pytest.raises(JournalCorrupt, match="recovered"):
        journal.expected_prior_for_path(PATHS["csi1000"])


def test_two_day_legitimate_chain_with_mid_crash_recovery():
    journal, _storage = make_journal()
    _day1, github, state_fs = complete_day(journal)
    day2_content = {
        "csi1000": '{"version": 2, "cash": 150.0}\n',
        "csi500": '{"version": 2, "cash": 250.0}\n',
    }
    record2 = journal.prepare("2026-10-17", "inv2", FILES, day_states(journal, day2_content))
    # Before any write: live day-1 post is legal (incomplete successor window).
    journal.check_live_state(PATHS["csi1000"], DAY1_CONTENT["csi1000"].encode())
    with pytest.raises(InjectedCrash):
        simulate_completion(record2, journal, github, state_fs, fail_at="mid_states")
    # Mixed window: csi1000 advanced, csi500 still at day-1 post; both legal.
    journal.check_live_state(PATHS["csi1000"], day2_content["csi1000"].encode())
    journal.check_live_state(PATHS["csi500"], DAY1_CONTENT["csi500"].encode())
    final = simulate_completion(journal.load("2026-10-17"), journal, github, state_fs)
    assert final["stage"] == "VOLUME_COMMITTED"
    journal.verify_chain_integrity()
    journal.check_paper_state_entry({market: PATHS[market] for market in PATHS})
    # After day-2 commit, the day-1 bytes are stale and must be rejected.
    with pytest.raises(JournalCorrupt, match="diverged"):
        journal.check_live_state(PATHS["csi1000"], DAY1_CONTENT["csi1000"].encode())
    # Missing committed state is rejected too.
    with pytest.raises(JournalCorrupt, match="missing"):
        journal.check_live_state(PATHS["csi1000"], None)


def test_three_market_single_tamper_fails_whole_batch():
    journal, storage = make_journal()
    contents = {
        "csi1000": DAY1_CONTENT["csi1000"],
        "csi500": DAY1_CONTENT["csi500"],
        "csi300": '{"version": 2, "cash": 300.0}\n',
    }
    complete_day(journal, contents=contents)
    storage.files[PATHS["csi500"]] = DAY1_CONTENT["csi500"].encode() + b"\n"
    with pytest.raises(JournalCorrupt) as excinfo:
        journal.check_paper_state_entry({market: PATHS[market] for market in PATHS})
    assert "csi500" in str(excinfo.value)
    # The untampered markets alone would pass; the batch must fail as a whole.
    journal.check_live_state(PATHS["csi1000"], DAY1_CONTENT["csi1000"].encode())
    journal.check_live_state(PATHS["csi300"], contents["csi300"].encode())


def test_missing_previously_existing_state_file_fails_closed():
    """R02/B04: a recorded prior state that disappeared must never be recreated."""
    journal, _storage = make_journal()
    prior = b'{"version": 2, "cash": 50.0}\n'
    journal.write_anchors({PATHS["csi1000"]: sha(prior)}, note="n", holder="op")
    record = journal.prepare(
        "2026-10-18", "inv",
        FILES,
        day_states(journal, {"csi1000": '{"version": 2, "cash": 60.0}\n',
                             "csi500": '{"version": 2, "cash": 70.0}\n'}),
    )
    with pytest.raises(JournalCorrupt, match="missing"):
        journal.plan_state_writes(record, {"csi1000": None, "csi500": None})


def test_truncated_or_replaced_state_file_fails_closed():
    journal, _storage = make_journal()
    prior = b'{"version": 2, "cash": 50.0}\n'
    journal.write_anchors({PATHS["csi1000"]: sha(prior)}, note="n", holder="op")
    record = journal.prepare(
        "2026-10-18", "inv",
        FILES,
        day_states(journal, {"csi1000": '{"version": 2, "cash": 60.0}\n',
                             "csi500": '{"version": 2, "cash": 70.0}\n'}),
    )
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


def test_verify_committed_states_rejects_loss_divergence_and_stale_state():
    journal, _storage = make_journal()
    _day1, github, state_fs = complete_day(journal)
    committed = journal.load("2026-10-16")
    current = {market: state_fs.get(entry["path"]) for market, entry in committed["states"].items()}
    journal.verify_committed_states(committed, current)

    # Missing committed file fails closed.
    lost = dict(current)
    lost["csi1000"] = None
    with pytest.raises(JournalCorrupt, match="missing"):
        journal.verify_committed_states(committed, lost)

    # Unrelated bytes fail closed.
    foreign = dict(current)
    foreign["csi1000"] = b'{"version": 2, "cash": 42.0, "unrelated": true}\n'
    with pytest.raises(JournalCorrupt, match="chain"):
        journal.verify_committed_states(committed, foreign)

    # A legitimate later day advances both accounts; the chain stays valid.
    day2 = {"csi1000": '{"version": 2, "cash": 111.0}\n',
            "csi500": '{"version": 2, "cash": 222.0}\n'}
    journal.prepare("2026-10-17", "inv2", FILES, day_states(journal, day2))
    simulate_completion(journal.load("2026-10-17"), journal, github, state_fs)
    advanced = {market: state_fs.get(entry["path"]) for market, entry in committed["states"].items()}
    journal.verify_committed_states(committed, advanced)
    # Day-1 bytes are stale once day 2 is committed.
    with pytest.raises(JournalCorrupt):
        journal.verify_committed_states(committed, {
            "csi1000": DAY1_CONTENT["csi1000"].encode(),
            "csi500": DAY1_CONTENT["csi500"].encode(),
        })


def test_journal_records_sort_oldest_first():
    journal, storage = make_journal()
    inject_record(storage, "2026-10-18", "PREPARED", prior=None, post=sha(b"post18"))
    inject_record(storage, "2026-10-16", "PREPARED", prior=None, post=sha(b"post16"))
    records = journal.incomplete_records()
    assert [record["signal_date"] for record in records] == ["2026-10-16", "2026-10-18"]


# ---------------------------------------------------------------- modal glue


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
    """B05/B06: the committed-record verifier must also validate Volume states."""
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

    # A one-byte tamper must also fail closed.
    (tmp_path / STATES["csi500"]["path"]).write_bytes(STATES["csi500"]["content"].encode() + b" ")
    with pytest.raises(JournalCorrupt, match="diverged"):
        verify(journal.load("2026-10-18"), journal, {})
