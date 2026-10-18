"""R05 behavioral tests: safe external provider archive extraction."""

import io
import tarfile

import pytest

import provider_archive
from provider_archive import UnsafeArchiveError, safe_extract_provider_archive


def write_tar(path, entries):
    with tarfile.open(path, "w:gz") as tf:
        for entry in entries:
            info = tarfile.TarInfo(entry["name"])
            if entry.get("type") == "dir":
                info.type = tarfile.DIRTYPE
                tf.addfile(info)
            elif entry.get("type") == "sym":
                info.type = tarfile.SYMTYPE
                info.linkname = entry["target"]
                tf.addfile(info)
            else:
                data = entry.get("data", b"")
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))


def valid_entries(prefix="pkg/"):
    return [
        {"name": f"{prefix}calendars/day.txt", "data": b"2026-01-01\n"},
        {"name": f"{prefix}features/sh600000/close.day.bin", "data": b"BINARY"},
    ]


def test_valid_nested_archive_extracts_to_package_root(tmp_path):
    archive = tmp_path / "provider.tar.gz"
    write_tar(archive, valid_entries())
    target = tmp_path / "extract"
    root = safe_extract_provider_archive(archive, target)
    assert root == str(target / "pkg")
    assert (target / "pkg" / "calendars" / "day.txt").read_bytes() == b"2026-01-01\n"
    assert (target / "pkg" / "features" / "sh600000" / "close.day.bin").read_bytes() == b"BINARY"


def test_flat_root_archive_extracts(tmp_path):
    archive = tmp_path / "provider.tar.gz"
    write_tar(archive, valid_entries(prefix=""))
    root = safe_extract_provider_archive(archive, tmp_path / "extract")
    assert root == str(tmp_path / "extract")


def test_path_traversal_is_rejected_before_any_write(tmp_path):
    archive = tmp_path / "evil.tar.gz"
    write_tar(archive, [{"name": "../escape.txt", "data": b"boom"}] + valid_entries())
    target = tmp_path / "extract"
    with pytest.raises(UnsafeArchiveError):
        safe_extract_provider_archive(archive, target)
    assert not (tmp_path / "escape.txt").exists()


def test_absolute_member_path_is_rejected(tmp_path):
    archive = tmp_path / "evil.tar.gz"
    write_tar(archive, [{"name": "/tmp/evil_pkg/calendars/day.txt", "data": b"x"}] + valid_entries())
    with pytest.raises(UnsafeArchiveError):
        safe_extract_provider_archive(archive, tmp_path / "extract")


def test_symlink_member_is_rejected(tmp_path):
    archive = tmp_path / "evil.tar.gz"
    entries = valid_entries() + [{"name": "pkg/escape", "type": "sym", "target": "../../outside"}]
    write_tar(archive, entries)
    with pytest.raises(UnsafeArchiveError, match="links"):
        safe_extract_provider_archive(archive, tmp_path / "extract")


def test_missing_qlib_structure_is_rejected(tmp_path):
    archive = tmp_path / "provider.tar.gz"
    write_tar(archive, [{"name": "pkg/features/x.bin", "data": b"x"}])
    with pytest.raises(UnsafeArchiveError, match="package root"):
        safe_extract_provider_archive(archive, tmp_path / "extract")


def test_ambiguous_multiple_roots_are_rejected(tmp_path):
    archive = tmp_path / "provider.tar.gz"
    entries = valid_entries("a/") + valid_entries("b/")
    write_tar(archive, entries)
    with pytest.raises(UnsafeArchiveError, match="package root"):
        safe_extract_provider_archive(archive, tmp_path / "extract")


def test_member_count_bound_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_archive, "MAX_MEMBERS", 1)
    archive = tmp_path / "provider.tar.gz"
    write_tar(archive, valid_entries())
    with pytest.raises(UnsafeArchiveError, match="too many members"):
        safe_extract_provider_archive(archive, tmp_path / "extract")


def test_file_size_bound_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_archive, "MAX_TOTAL_BYTES", 4)
    archive = tmp_path / "provider.tar.gz"
    write_tar(archive, valid_entries())
    with pytest.raises(UnsafeArchiveError):
        safe_extract_provider_archive(archive, tmp_path / "extract")
