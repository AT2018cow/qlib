"""Dated ST history contract for the website causal account."""

import json
from pathlib import Path

import pytest

from website_st_history import (
    history_sha256,
    load_merged_history,
    load_seed_history,
    merge_history,
    persisted_path,
    prefix_sha256,
    record_snapshot,
    write_history,
)


def _history(dates):
    return {"schema_version": 1, "source": "test:dated-st", "dates": dates}


def test_seed_history_covers_the_published_window_and_next_session():
    history = load_seed_history()
    assert len(history["dates"]) == 26
    assert min(history["dates"]) == "2026-10-08"
    assert max(history["dates"]) == "2026-11-12"
    assert all(values == [] for values in history["dates"].values())
    assert history["evidence"]["2026-11-12"]["source"] == "akshare.stock_zh_a_spot_tx"
    assert len(history_sha256(history)) == 64


def test_merge_rejects_conflicting_dates():
    left = _history({"2026-10-08": ["SH600000"]})
    right = _history({"2026-10-08": ["SH600001"]})
    with pytest.raises(ValueError, match="conflicting ST history"):
        merge_history(left, right)
    merged = merge_history(left, _history({"2026-10-09": []}))
    assert merged["dates"]["2026-10-08"] == ["SH600000"]
    assert merged["dates"]["2026-10-09"] == []


def test_snapshot_append_is_idempotent_and_cannot_rewrite():
    history = _history({"2026-10-08": []})
    snapshot = {
        "date": "2026-10-09",
        "symbols": ["SH600001"],
        "source": "akshare.stock_zh_a_st_em",
        "fetched_at_cst": "2026-10-08T18:30:00+08:00",
    }
    updated = record_snapshot(history, snapshot)
    assert updated["dates"]["2026-10-09"] == ["SH600001"]
    assert updated["evidence"]["2026-10-09"]["source"] == snapshot["source"]
    assert record_snapshot(updated, snapshot)["dates"]["2026-10-09"] == ["SH600001"]
    with pytest.raises(ValueError, match="conflicts with frozen history"):
        record_snapshot(updated, {**snapshot, "symbols": []})
    with pytest.raises(ValueError, match="no source"):
        record_snapshot(updated, {**snapshot, "date": "2026-10-10", "source": ""})


def test_prefix_hash_excludes_future_dates():
    history = _history({"2026-10-08": [], "2026-10-09": ["SH600001"], "2026-10-12": ["SH600002"]})
    assert prefix_sha256(history, "2026-10-08") == prefix_sha256(_history({"2026-10-08": []}), "2026-10-08")
    assert prefix_sha256(history, "2026-10-09") != prefix_sha256(history, "2026-10-08")
    assert prefix_sha256(history, "2026-10-12") == history_sha256(history)


def test_persisted_history_is_merged_with_the_seed(tmp_path):
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps(_history({"2026-10-08": []})))
    root = tmp_path / "simulation"
    path = persisted_path(root, "csi300")
    write_history(path, _history({"2026-10-09": ["SH600001"]}))
    merged = load_merged_history(root, "csi300", seed_path=seed)
    assert merged["dates"] == {"2026-10-08": [], "2026-10-09": ["SH600001"]}
    assert path.is_file()
    assert Path(path).read_text().startswith("{")
