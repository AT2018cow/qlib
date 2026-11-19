"""R01: CSIndex-lag retry must not delete a non-existent staging directory."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from data_ingest.pipeline import PipelineError


def _fake_pipeline():
    class FakePipeline:
        calls = []

        def __init__(self, **kwargs):
            pass

        def build(self, base, out, **kwargs):
            FakePipeline.calls.append(dict(kwargs))
            if len(FakePipeline.calls) == 1:
                raise PipelineError("csi300 constituent date 2026-11-09 != asof 2026-11-10")
            assert not out.exists(), "the retry must start from clean staging"
            (out / "calendars").mkdir(parents=True)
            (out / "calendars" / "day.txt").write_text("2026-11-09\n2026-11-10\n")
            (out / "manifest.json").write_text("{}")
            return {
                "base": {"path": "provider:base", "calendar_end": "2026-11-09"},
                "gate": {"status": "PASS", "checks": []},
                "overlay": {"row_symbols": 1},
                "sources": {},
                "membership_policy": {"mode": "BASE_PIT_LAG"},
                "history_backfill": {"symbols": []},
            }

    return FakePipeline()


def test_retry_handles_absent_staging_and_completes(tmp_path):
    from provider_build import _build_with_membership_retry

    pipeline = _fake_pipeline()
    result = _build_with_membership_retry(
        pipeline,
        tmp_path / "base",
        tmp_path / "out",
        {},
        ["2026-11-10"],
        "2026-11-10",
    )
    assert result["overlay"]["row_symbols"] == 1
    assert len(pipeline.calls) == 2
    assert pipeline.calls[1]["allow_base_membership"] is True


def test_retry_cleans_partial_staging(tmp_path):
    from provider_build import _build_with_membership_retry

    out = tmp_path / "out"
    (out / "partial").mkdir(parents=True)
    (out / "partial" / "leftover.bin").write_bytes(b"x")
    pipeline = _fake_pipeline()
    result = _build_with_membership_retry(
        pipeline,
        tmp_path / "base",
        out,
        {},
        ["2026-11-10"],
        "2026-11-10",
    )
    assert result["overlay"]["row_symbols"] == 1
    assert len(pipeline.calls) == 2


def test_cleanup_failure_preserves_membership_error(tmp_path):
    from provider_build import _build_with_membership_retry

    out = tmp_path / "out"
    out.write_bytes(b"not a directory")

    class FakePipeline:
        calls = 0

        def build(self, base, out_dir, **kwargs):
            FakePipeline.calls += 1
            raise PipelineError("csi300 constituent date 2026-11-09 != asof 2026-11-10")

    with pytest.raises(PipelineError, match="constituent date"):
        _build_with_membership_retry(
            FakePipeline(),
            tmp_path / "base",
            out,
            {},
            ["2026-11-10"],
            "2026-11-10",
        )
    assert FakePipeline.calls == 1


@pytest.mark.parametrize(
    "message,target_dates",
    [
        ("csi300 constituent date 2026-11-01 != asof 2026-11-10", ["2026-11-10"]),
        ("csi300 constituent date 2026-11-09 != asof 2026-11-10", ["2026-11-09", "2026-11-10"]),
        ("some other membership failure", ["2026-11-10"]),
    ],
)
def test_policy_violations_fail_closed_without_retry(tmp_path, message, target_dates):
    from provider_build import _build_with_membership_retry

    class FakePipeline:
        calls = 0

        def build(self, base, out, **kwargs):
            FakePipeline.calls += 1
            raise PipelineError(message)

    with pytest.raises(PipelineError):
        _build_with_membership_retry(
            FakePipeline(),
            tmp_path / "base",
            tmp_path / "out",
            {},
            target_dates,
            "2026-11-10",
        )
    assert FakePipeline.calls == 1


def test_build_provider_archive_entrypoint_survives_lag_before_staging(tmp_path, monkeypatch):
    import provider_build

    base = tmp_path / "work" / "base"
    base.mkdir(parents=True)
    (base / "calendars").mkdir()
    (base / "calendars" / "day.txt").write_text("2026-11-09\n")
    snapshot = SimpleNamespace(
        kind="archive",
        date="2026-11-09",
        digest="a" * 64,
        path=tmp_path / "base.tar.gz",
        calendar_start="2026-11-09",
        calendar_end="2026-11-09",
        calendar_sha256="b" * 64,
        manifest_sha256="c" * 64,
        read_sessions=lambda: ("2026-11-09",),
    )
    pipeline = _fake_pipeline()
    monkeypatch.setattr(provider_build, "load_snapshot", lambda root, date: None)
    monkeypatch.setattr(provider_build, "latest_snapshot", lambda root, before: snapshot)
    monkeypatch.setattr(provider_build, "materialize", lambda snapshot, dest, **kwargs: base)
    monkeypatch.setattr(provider_build, "fetch_sina_calendar", lambda: ["2026-11-09", "2026-11-10"])
    monkeypatch.setattr(provider_build, "OverlayPipeline", lambda **kwargs: pipeline)
    monkeypatch.setattr(provider_build, "IngestCache", lambda *args, **kwargs: SimpleNamespace(status=lambda: {}))
    monkeypatch.setattr(
        provider_build,
        "create_provider_archive",
        lambda source, destination, **kwargs: {
            "sha256": "d" * 64,
            "size": 1,
            "content_sha256": "e" * 64,
            "member_count": 1,
            "contract": "qlib-provider-archive-v1",
        },
    )
    monkeypatch.setattr(
        provider_build,
        "build_receipt",
        lambda *args, **kwargs: {
            "status": "PASS",
            "archive": {"sha256": "d" * 64, "size": 1},
            "receipt_sha256": "f" * 64,
            "calendar_end": "2026-11-10",
        },
    )
    published = {}
    monkeypatch.setattr(
        provider_build, "publish_archive", lambda root, receipt, archive, **kwargs: published.update(receipt)
    )
    monkeypatch.setattr(provider_build, "prune_archives", lambda root, **kwargs: {"removed": []})
    failures = []
    result = provider_build.build_provider_archive(
        tmp_path / "root",
        "2026-11-10",
        commit_fn=lambda: None,
        require_qlib_smoke=False,
        work_root=tmp_path / "work",
        failure_record_fn=lambda *args: failures.append(args),
    )
    assert result["status"] == "PUBLISHED"
    assert published["status"] == "PASS"
    assert failures == []
    assert len(pipeline.calls) == 2
    assert pipeline.calls[1]["allow_base_membership"] is True


@pytest.mark.parametrize(
    "mode,official_asof,blocked",
    [
        ("BASE_PIT_LAG", "2026-12-11", True),
        ("OFFICIAL", "2026-12-11", True),
        ("OFFICIAL", "2026-12-14", False),
    ],
)
def test_existing_pass_archive_respects_rebalance_gate(tmp_path, monkeypatch, mode, official_asof, blocked):
    import provider_build

    snapshot = SimpleNamespace(
        kind="archive",
        date="2026-12-14",
        calendar_end="2026-12-14",
        read_sessions=lambda: ("2026-12-11", "2026-12-14"),
        digest="a" * 64,
        receipt={"receipt_sha256": "b" * 64},
        manifest={
            "membership_policy": {"mode": mode, "official_asof": official_asof},
            "memberships": {market: {"asof": official_asof} for market in ("csi300", "csi500", "csi1000")},
        },
    )
    monkeypatch.setattr(provider_build, "load_snapshot", lambda root, date: snapshot)

    def build():
        return provider_build.build_provider_archive(
            tmp_path / "root", "2026-12-14", commit_fn=lambda: None, work_root=tmp_path / "work"
        )

    if blocked:
        with pytest.raises(provider_build.ProviderBuildError, match="rebalance.*current official constituent"):
            build()
    else:
        assert build()["status"] == "EXISTS"


def _write_rebalance_snapshot(root, sessions, appended, mode, *, base=None, drop_check=None):
    from data_ingest.manifest import write_json
    from data_ingest.store import load_snapshot

    end = sessions[-1]
    path = root / end
    (path / "calendars").mkdir(parents=True)
    (path / "calendars" / "day.txt").write_text("\n".join(sessions) + "\n")
    markets = ("csi300", "csi500", "csi1000")
    policy = {"mode": mode, "official_asof": end if mode != "BASE_PIT_LAG" else sessions[-2]}
    checks = []
    if mode == "MULTI_DAY_EVIDENCE":
        policy["dates"] = appended
        checks = [
            {"name": f"membership.{check}.{market}.{day}", "status": "PASS"}
            for day in appended
            for market in markets
            for check in ("date", "members")
        ]
        checks = [check for check in checks if check["name"] != drop_check]
    write_json(
        path / "manifest.json",
        {
            "status": "PASS",
            "target": {"asof": end, "dates": appended},
            "base": {"calendar_end": base.calendar_end, "archive_sha256": base.digest} if base else {},
            "membership_policy": policy,
            "memberships": {market: {"asof": end} for market in markets},
            "gate": {"status": "PASS", "checks": checks},
        },
    )
    return load_snapshot(root, end)


@pytest.mark.parametrize(
    "parent_mode, bad_identity, bad_calendar, error",
    [
        ("BASE_PIT_LAG", False, False, "rebalance 2026-12-14"),
        ("OFFICIAL", True, False, "mismatched base 2026-12-14"),
        ("OFFICIAL", False, True, "calendar differs from base 2026-12-14"),
        ("OFFICIAL", False, False, None),
    ],
)
def test_rebalance_descendant_reuse_verifies_parent(
    tmp_path, monkeypatch, parent_mode, bad_identity, bad_calendar, error
):
    import provider_build

    root = tmp_path / "provider"
    sessions = ["2026-12-10", "2026-12-11", "2026-12-14", "2026-12-15"]
    parent_sessions = sessions[1:-1] if bad_calendar else sessions[:-1]
    parent = _write_rebalance_snapshot(root, parent_sessions, [sessions[-2]], parent_mode)
    if bad_identity:
        parent = SimpleNamespace(calendar_end=parent.calendar_end, digest="f" * 64)
    head = _write_rebalance_snapshot(root, sessions, [sessions[-1]], "OFFICIAL", base=parent)
    monkeypatch.setattr(
        provider_build,
        "seal_directory_snapshot",
        lambda *a, **k: {"archive": {"sha256": "a" * 64}, "receipt_sha256": "b" * 64, "calendar_end": head.date},
    )

    def build():
        return provider_build.build_provider_archive(
            root, head.date, commit_fn=lambda: None, work_root=tmp_path / "work"
        )

    if error:
        with pytest.raises(provider_build.ProviderBuildError, match=error):
            build()
    else:
        assert build()["status"] == "SEALED"


@pytest.mark.parametrize("drop_check", [None, "membership.members.csi500.2026-12-14"])
def test_existing_rebalance_multi_day_snapshot_requires_verified_lists_for_every_session(
    tmp_path, monkeypatch, drop_check
):
    import provider_build

    root = tmp_path / "provider"
    sessions = ["2026-12-10", "2026-12-11", "2026-12-14", "2026-12-15"]
    if drop_check is None:
        sessions = sessions[:-1]  # also exercise sealing a recovery that ends on rebalance day
    snapshot = _write_rebalance_snapshot(root, sessions, sessions[1:], "MULTI_DAY_EVIDENCE", drop_check=drop_check)
    monkeypatch.setattr(
        provider_build,
        "seal_directory_snapshot",
        lambda *a, **k: {"archive": {"sha256": "a" * 64}, "receipt_sha256": "b" * 64, "calendar_end": snapshot.date},
    )

    def build():
        return provider_build.build_provider_archive(
            root, snapshot.date, commit_fn=lambda: None, work_root=tmp_path / "work"
        )

    if drop_check:
        with pytest.raises(provider_build.ProviderBuildError, match="rebalance 2026-12-14"):
            build()
    else:
        assert build()["status"] == "SEALED"
