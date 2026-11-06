"""An official benchmark repair must not rewrite existing or stock prices."""

import hashlib
import json

import numpy as np
import pytest

from benchmark_evidence_overlay import build_overlay, validate_and_patch_close


def evidence():
    return {
        "code": "200",
        "success": True,
        "data": [
            {"tradeDate": day, "indexCode": "000852", "close": close}
            for day, close in [("20250102", 20), ("20250103", 21), ("20250106", 22)]
        ],
    }


def test_only_missing_close_is_patched_after_neighbor_verification():
    closes = np.array([0, 10, np.nan, 11], dtype="<f4")
    factors = np.array([0, 0.5, 0.5, 0.5], dtype="<f4")
    patched, detail = validate_and_patch_close(
        ["2025-01-02", "2025-01-03", "2025-01-06"], closes, factors, evidence(), day="2025-01-03"
    )
    assert patched.tolist() == [0, 10, 10.5, 11]
    assert np.isnan(closes[2])
    assert detail["raw_close"] == 21
    bad = evidence()
    bad["data"][0]["close"] = 200
    with pytest.raises(ValueError, match="neighbours"):
        validate_and_patch_close(["2025-01-02", "2025-01-03", "2025-01-06"], closes, factors, bad, day="2025-01-03")
    with pytest.raises(ValueError, match="missing close"):
        validate_and_patch_close(
            ["2025-01-02", "2025-01-03", "2025-01-06"], patched, factors, evidence(), day="2025-01-03"
        )


def test_overlay_preserves_original_hardlinks_and_changes_only_one_file(tmp_path):
    source = tmp_path / "source"
    (source / "calendars").mkdir(parents=True)
    (source / "features/sh000852").mkdir(parents=True)
    (source / "calendars/day.txt").write_text("2025-01-02\n2025-01-03\n2025-01-06\n")
    np.array([0, 10, np.nan, 11], dtype="<f4").tofile(source / "features/sh000852/close.day.bin")
    np.array([0, 0.5, 0.5, 0.5], dtype="<f4").tofile(source / "features/sh000852/factor.day.bin")
    files = {
        str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest() for p in source.rglob("*") if p.is_file()
    }
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps(evidence()))
    meta = tmp_path / "provenance.json"
    meta.write_text(
        json.dumps(
            {
                "url": "https://www.csindex.com.cn/csindex-home/perf/index-perf?indexCode=000852",
                "sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
                "status": 200,
            }
        )
    )
    receipt = build_overlay(source, tmp_path / "new", {"files": files}, raw, meta, day="2025-01-03")
    for rel, digest in files.items():
        assert hashlib.sha256((source / rel).read_bytes()).hexdigest() == digest
    assert np.fromfile(tmp_path / "new/features/sh000852/close.day.bin", dtype="<f4")[2] == 10.5
    assert receipt["changed_files"] == ["features/sh000852/close.day.bin"]
