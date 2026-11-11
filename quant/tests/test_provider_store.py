"""Tests for content-addressed provider snapshots."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from data_ingest.store import (
    ProviderStoreError,
    build_receipt,
    create_provider_archive,
    list_snapshots,
    load_snapshot,
    materialize,
    prune_archives,
    publish_archive,
    seal_directory_snapshot,
)


def make_provider(root: Path, date: str = "2026-11-10", *, value: float = 1.0) -> Path:
    root.mkdir(parents=True)
    (root / "calendars").mkdir()
    (root / "calendars" / "day.txt").write_text(f"2026-11-09\n{date}\n")
    (root / "instruments").mkdir()
    (root / "instruments" / "csi300.txt").write_text(f"SH600000\t2026-11-09\t{date}\n")
    feature = root / "features" / "sh600000"
    feature.mkdir(parents=True)
    import numpy as np

    np.hstack([[1.0], [value, value]]).astype("<f").tofile(feature / "close.day.bin")
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "PASS",
                "base": {"path": "provider:base", "calendar_end": "2026-11-09"},
                "target": {"asof": date, "dates": [date]},
            },
            indent=2,
        )
        + "\n"
    )
    (root / "SHA256SUMS.json").write_text("{}\n")
    return root


def publish(temp: Path, provider: Path, date: str = "2026-11-10") -> dict:
    archive = temp / "local.tar.gz"
    meta = create_provider_archive(provider, archive)
    manifest_sha = __import__("hashlib").sha256((provider / "manifest.json").read_bytes()).hexdigest()
    calendar_sha = __import__("hashlib").sha256((provider / "calendars/day.txt").read_bytes()).hexdigest()
    receipt = build_receipt(
        None,
        date=date,
        archive=meta,
        manifest_sha256=manifest_sha,
        calendar_sha256=calendar_sha,
        base={"path": "provider:base", "calendar_end": "2026-11-09"},
        provenance={"builder": "test"},
        gate={"status": "PASS"},
    )
    root = temp / "vol"
    root.mkdir(exist_ok=True)
    return publish_archive(
        root,
        receipt,
        archive,
        local_manifest=provider / "manifest.json",
        local_calendar=provider / "calendars" / "day.txt",
    )


def test_deterministic_archive_and_content_digest(tmp_path):
    provider = make_provider(tmp_path / "provider")
    first = create_provider_archive(provider, tmp_path / "a.tar.gz")
    # Change source mtimes: the archive digest must not depend on file times.
    for path in provider.rglob("*"):
        path.touch()
    second = create_provider_archive(provider, tmp_path / "b.tar.gz")
    assert first["sha256"] == second["sha256"]
    assert first["content_sha256"] == second["content_sha256"]
    assert first["size"] == second["size"]


def test_archive_roundtrip_and_materialize(tmp_path):
    provider = make_provider(tmp_path / "provider")
    receipt = publish(tmp_path, provider)
    root = tmp_path / "vol"
    snapshot = load_snapshot(root, "2026-11-10")
    assert snapshot is not None
    assert snapshot.kind == "archive"
    assert snapshot.digest == receipt["archive"]["sha256"]
    local = materialize(snapshot, tmp_path / "consumer")
    assert (local / "calendars" / "day.txt").read_text() == (provider / "calendars" / "day.txt").read_text()
    # A second materialize reuses the digest cache.
    cached = materialize(snapshot, tmp_path / "consumer2")
    assert cached == local


def test_archive_wins_over_legacy_directory(tmp_path):
    provider = make_provider(tmp_path / "provider")
    publish(tmp_path, provider)
    root = tmp_path / "vol"
    # Add a legacy directory with the same date but different content.
    legacy = make_provider(root / "2026-11-10", value=9.0)
    assert legacy.is_dir()
    snapshot = load_snapshot(root, "2026-11-10")
    assert snapshot is not None and snapshot.kind == "archive"
    assert "2026-11-10" in list_snapshots(root)


def test_corrupted_archive_is_rejected_by_materialize(tmp_path):
    provider = make_provider(tmp_path / "provider")
    publish(tmp_path, provider)
    root = tmp_path / "vol"
    snapshot = load_snapshot(root, "2026-11-10")
    assert snapshot is not None
    snapshot.path.write_bytes(b"corrupt")
    with pytest.raises(ProviderStoreError):
        materialize(snapshot, tmp_path / "consumer", cache_root=tmp_path / "cache")


def test_tampered_receipt_is_not_resolved(tmp_path):
    provider = make_provider(tmp_path / "provider")
    publish(tmp_path, provider)
    root = tmp_path / "vol"
    receipt_path = root / "archives" / "2026-11-10" / "receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["calendar_end"] = "2026-11-11"
    receipt_path.write_text(json.dumps(receipt))
    assert load_snapshot(root, "2026-11-10") is None


def test_different_pass_receipt_for_same_date_fails_closed(tmp_path):
    provider = make_provider(tmp_path / "provider")
    publish(tmp_path, provider)
    root = tmp_path / "vol"
    # A second, different provider for the same date must not overwrite PASS.
    other = make_provider(tmp_path / "other", value=3.0)
    archive = tmp_path / "other.tar.gz"
    meta = create_provider_archive(other, archive)
    receipt = build_receipt(
        None,
        date="2026-11-10",
        archive=meta,
        manifest_sha256="0" * 64,
        calendar_sha256="0" * 64,
        base={},
        provenance={"builder": "test"},
    )
    with pytest.raises(ProviderStoreError):
        publish_archive(
            root,
            receipt,
            archive,
            local_manifest=other / "manifest.json",
            local_calendar=other / "calendars" / "day.txt",
        )


def test_prune_only_archives_and_keeps_checkpoints(tmp_path):
    root = tmp_path / "vol"
    root.mkdir()
    for day in ("2026-11-01", "2026-11-02", "2026-11-03"):
        provider = make_provider(tmp_path / f"p-{day}", date=day)
        publish(tmp_path / day, provider, date=day)
    # publish() writes into its own root; copy the archive directories over.
    for day in ("2026-11-01", "2026-11-02", "2026-11-03"):
        shutil.copytree(tmp_path / day / "vol" / "archives" / day, root / "archives" / day)
    result = prune_archives(root, keep=1, dry_run=True)
    assert result["dry_run"] is True
    assert all(date < "2026-11-03" for date in result["removed"])
    assert (root / "archives" / "2026-11-01").is_dir()


def test_seal_legacy_directory_is_content_addressed(tmp_path):
    root = tmp_path / "vol"
    provider = make_provider(root / "2026-11-10")
    receipt = seal_directory_snapshot(root, "2026-11-10", work_dir=tmp_path / "work")
    assert receipt["status"] == "PASS"
    assert receipt["provenance"]["builder"] == "seal-directory"
    snapshot = load_snapshot(root, "2026-11-10")
    assert snapshot is not None and snapshot.kind == "archive"
    assert snapshot.digest == receipt["archive"]["sha256"]
    # The frozen directory is untouched.
    assert provider.is_dir()


def test_materialize_cache_hit_does_not_read_archive_or_extract(tmp_path, monkeypatch):
    import data_ingest.store as store

    provider = make_provider(tmp_path / "provider")
    publish(tmp_path, provider)
    root = tmp_path / "vol"
    snapshot = load_snapshot(root, "2026-11-10")
    assert snapshot is not None
    calls = []
    original = store._extract_archive

    def spy(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "_extract_archive", spy)
    first = materialize(snapshot, tmp_path / "first", cache_root=tmp_path / "cache")
    # A cache hit must not read the archive at all: remove it and call again.
    snapshot.path.unlink()
    second = materialize(snapshot, tmp_path / "second", cache_root=tmp_path / "cache")
    assert first == second
    assert len(calls) == 1
    assert (second / "calendars" / "day.txt").is_file()


def test_materialize_recovers_when_marker_root_is_missing(tmp_path):
    provider = make_provider(tmp_path / "provider")
    publish(tmp_path, provider)
    root = tmp_path / "vol"
    snapshot = load_snapshot(root, "2026-11-10")
    assert snapshot is not None
    cache_root = tmp_path / "cache"
    local = materialize(snapshot, tmp_path / "first", cache_root=cache_root)
    marker = cache_root / snapshot.digest / "ready.json"
    payload = json.loads(marker.read_text())
    assert payload["relative_root"] == "extract/provider"
    shutil.rmtree(local)
    recovered = materialize(snapshot, tmp_path / "second", cache_root=cache_root)
    assert recovered == local
    assert (recovered / "calendars" / "day.txt").is_file()


def test_materialize_rejects_corrupt_marker_and_reextracts(tmp_path):
    provider = make_provider(tmp_path / "provider")
    publish(tmp_path, provider)
    root = tmp_path / "vol"
    snapshot = load_snapshot(root, "2026-11-10")
    assert snapshot is not None
    cache_root = tmp_path / "cache"
    materialize(snapshot, tmp_path / "first", cache_root=cache_root)
    marker = cache_root / snapshot.digest / "ready.json"
    marker.write_text("{not-json")
    recovered = materialize(snapshot, tmp_path / "second", cache_root=cache_root)
    assert (recovered / "calendars" / "day.txt").is_file()


@pytest.mark.parametrize("damage", ["truncate", "change_middle", "empty"])
def test_materialize_rejects_cached_calendar_with_wrong_content_identity(tmp_path, monkeypatch, damage):
    """S05: ready markers cannot bless a tampered extracted calendar."""
    import data_ingest.store as store

    provider = make_provider(tmp_path / "provider")
    publish(tmp_path, provider)
    snapshot = load_snapshot(tmp_path / "vol", "2026-11-10")
    assert snapshot is not None
    calls = []
    original = store._extract_archive

    def spy(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "_extract_archive", spy)
    local = materialize(snapshot, tmp_path / "first", cache_root=tmp_path / "cache")
    calendar = local / "calendars" / "day.txt"
    if damage == "truncate":
        calendar.write_text("2026-11-09\n")
    elif damage == "change_middle":
        calendar.write_text("2026-11-08\n2026-11-10\n")
    else:
        calendar.write_text("")

    recovered = materialize(snapshot, tmp_path / "second", cache_root=tmp_path / "cache")
    assert recovered == local
    assert calendar.read_text() == (provider / "calendars" / "day.txt").read_text()
    assert len(calls) == 2


def test_seal_binds_the_resolved_prior_archive_digest(tmp_path):
    root = tmp_path / "vol"
    prior_provider = make_provider(tmp_path / "prior", date="2026-11-09")
    prior_receipt = publish(tmp_path / "prior-publish", prior_provider, date="2026-11-09")
    source_archives = tmp_path / "prior-publish" / "vol" / "archives" / "2026-11-09"
    target_archives = root / "archives" / "2026-11-09"
    target_archives.parent.mkdir(parents=True)
    shutil.copytree(source_archives, target_archives)
    current = make_provider(root / "2026-11-10", date="2026-11-10")
    (current / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "PASS",
                "base": {"path": "/vol/cn_data_overlay/2026-11-09", "calendar_end": "2026-11-09"},
                "target": {"asof": "2026-11-10", "dates": ["2026-11-10"]},
            }
        )
    )
    receipt = seal_directory_snapshot(root, "2026-11-10", work_dir=tmp_path / "work")
    assert receipt["base"]["kind"] == "archive"
    assert receipt["base"]["digest"] == prior_receipt["archive"]["sha256"]
    assert receipt["base"]["calendar_end"] == "2026-11-09"


def test_retention_plan_keeps_anchors_latest_recent_checkpoints_and_references(tmp_path):
    from data_ingest.store import ProviderStoreError, retention_plan

    root = tmp_path / "vol"
    root.mkdir()
    days = (
        "2026-09-30",
        "2026-10-08",
        "2026-10-09",
        "2026-10-12",
        "2026-10-13",
        "2026-10-30",
        "2026-10-31",
        "2026-11-30",
        "2026-12-31",
    )
    for day in days:
        provider = make_provider(tmp_path / f"p-{day}", date=day)
        publish(tmp_path / day, provider, date=day)
        shutil.copytree(tmp_path / day / "vol" / "archives" / day, root / "archives" / day)
    plan = retention_plan(
        root,
        keep=2,
        frozen_anchor_dates=["2026-09-30"],
        referenced_dates=["2026-10-08"],
        incomplete_dates=["2026-10-09"],
        generated_at="2026-11-11T00:00:00Z",
    )
    reasons = {item["date"]: item["reason"] for item in plan["keep"]}
    assert reasons["2026-09-30"] == "frozen_anchor"
    assert reasons["2026-10-08"] == "referenced_by_journal_website_or_recovery"
    assert reasons["2026-10-09"] == "incomplete_publication"
    assert reasons["2026-12-31"] == "latest_pass"
    assert reasons["2026-11-30"] == "month_end_checkpoint"
    assert reasons["2026-10-31"] == "month_end_checkpoint"
    assert [item["date"] for item in plan["remove"]] == ["2026-10-30", "2026-10-13", "2026-10-12"]
    assert plan["remove"][0]["path"] == "archives/2026-10-30"
    assert plan["remove"][0]["digest"]
    assert plan["apply"] == "NOT_PERMITTED_WITHOUT_SEPARATE_OPERATOR_APPROVAL"
    # Planning never deletes or rewrites any receipt.
    assert (root / "archives" / "2026-10-30" / "receipt.json").is_file()
    with pytest.raises(ProviderStoreError, match="apply is disabled"):
        from data_ingest.store import prune_archives

        prune_archives(root, keep=2, dry_run=False)
    assert (root / "archives" / "2026-10-30" / "receipt.json").is_file()


def test_retention_plan_keeps_a_directory_with_a_missing_archive(tmp_path):
    from data_ingest.store import retention_plan

    root = tmp_path / "vol"
    root.mkdir()
    day = "2026-10-09"
    provider = make_provider(tmp_path / f"p-{day}", date=day)
    publish(tmp_path / day, provider, date=day)
    shutil.copytree(tmp_path / day / "vol" / "archives" / day, root / "archives" / day)
    receipt = json.loads((root / "archives" / day / "receipt.json").read_text())
    (root / receipt["archive"]["path"]).unlink()
    latest = "2026-10-12"
    provider = make_provider(tmp_path / f"p-{latest}", date=latest)
    publish(tmp_path / latest, provider, date=latest)
    shutil.copytree(tmp_path / latest / "vol" / "archives" / latest, root / "archives" / latest)
    plan = retention_plan(root, keep=1, generated_at="2026-11-11T00:00:00Z")
    assert plan["counts"]["remove"] == 0
    reasons = {item["date"]: item["reason"] for item in plan["keep"]}
    assert reasons[latest] == "latest_pass"
    assert reasons[day] == "incomplete_archive_directory"


def test_production_code_identity_never_falls_back_to_unavailable(monkeypatch):
    import provider_build
    import research_identity

    def missing(kind):
        raise ValueError("manifest file missing")

    monkeypatch.setattr(research_identity, "source_manifest", missing)
    with pytest.raises(ValueError, match="manifest file missing"):
        provider_build._code_source_sha()

    import modal_qlib_cn_a10g as production

    monkeypatch.setattr(production, "_CODE_SOURCE_SHA", None)
    with pytest.raises(ValueError, match="manifest file missing"):
        production._code_source_sha()
    assert production._CODE_SOURCE_SHA is None
