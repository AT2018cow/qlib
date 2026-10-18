"""R07 behavioral tests: idempotent code->name mapping sync."""

import base64

import pytest

from name_map_sync import NAME_MAP_PATH, sync_code_name_map

REPO = "owner/repo"
BRANCH = "main"
NEW_CSV = "code,name\n600000,浦发银行\n"


class FakeGitHub:
    def __init__(self, existing=None, get_status=404, put_status=200):
        self.existing = existing
        self.get_status = get_status
        self.put_status = put_status
        self.puts = []

    def http_get(self, url):
        assert f"/contents/{NAME_MAP_PATH}" in url
        if self.get_status != 200:
            return self.get_status, None
        return 200, {
            "content": base64.b64encode(self.existing.encode()).decode(),
            "sha": "f" * 40,
        }

    def http_put(self, url, body):
        self.puts.append(body)
        return self.put_status


def test_changed_content_triggers_single_update():
    github = FakeGitHub(existing="code,name\n", get_status=200)
    status = sync_code_name_map(NEW_CSV, http_get=github.http_get, http_put=github.http_put, repo=REPO, branch=BRANCH)
    assert status == "updated"
    assert len(github.puts) == 1
    body = github.puts[0]
    assert base64.b64decode(body["content"]).decode() == NEW_CSV
    assert body["branch"] == BRANCH
    assert body["sha"] == "f" * 40


def test_identical_content_is_not_pushed():
    github = FakeGitHub(existing=NEW_CSV, get_status=200)
    status = sync_code_name_map(NEW_CSV, http_get=github.http_get, http_put=github.http_put, repo=REPO, branch=BRANCH)
    assert status == "unchanged"
    assert github.puts == []


def test_missing_file_is_created_without_sha():
    github = FakeGitHub(get_status=404)
    status = sync_code_name_map(NEW_CSV, http_get=github.http_get, http_put=github.http_put, repo=REPO, branch=BRANCH)
    assert status == "updated"
    assert "sha" not in github.puts[0]


def test_http_failures_raise_for_caller_downgrade():
    github = FakeGitHub(get_status=500)
    with pytest.raises(RuntimeError, match="HTTP 500"):
        sync_code_name_map(NEW_CSV, http_get=github.http_get, http_put=github.http_put, repo=REPO, branch=BRANCH)
    github = FakeGitHub(existing="old\n", get_status=200, put_status=409)
    with pytest.raises(RuntimeError, match="HTTP 409"):
        sync_code_name_map(NEW_CSV, http_get=github.http_get, http_put=github.http_put, repo=REPO, branch=BRANCH)


def test_name_map_runs_on_all_daily_cron_paths():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "quant/code/production/modal_qlib_cn_a10g.py").read_text()
    cron = src[src.index("def daily_cron():") : src.index("# ===================== 第六步")]
    # Same-day rerun fast paths AND the normal publish path refresh the map,
    # so an already-complete day can no longer skip the name update (R07).
    assert cron.count("_update_code_name_map(headers)") >= 3
    committed = cron.index("committed = journal.load(signal_date)")
    early_gate = cron.index("early = _early_gate(cal, signal_date)")
    assert committed < cron.index("_update_code_name_map(headers)", committed) < early_gate
