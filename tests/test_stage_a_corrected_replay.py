"""Synthetic tests only; not a substitute for original Modal provider smoke."""
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit import replay_stage_a_corrected as a


def frozen():
    candidates = [{"candidate_id": f"{i:064x}", "candidate": {"candidate_id": f"{i:064x}"},
                   "rank": i + 1} for i in range(78)]
    candidates += [{"candidate_id": a.BASELINE, "candidate": {"candidate_id": a.BASELINE}, "rank": 79},
                   {"candidate_id": a.WINNER, "candidate": {"candidate_id": a.WINNER}, "rank": 80}]
    folds = [{"fold_id": f"fold{f}", "signal": ["2021-01-01", "2021-01-31"],
              "execution": ["2021-01-04", "2021-02-01"]} for f in range(1, 5)]
    summary = {"protocol": "csi1000_lgb_stage_a_v3", "manifest":
               {"snapshot_token": a.SNAPSHOT, "provider_fingerprint": "test"},
               "ranked_candidates": candidates, "folds": folds}
    return summary


def fixture_files(tmp_path):
    s = frozen()
    c = s["ranked_candidates"][-2]
    f = s["folds"][0]
    path = tmp_path / "csi1000_tuner" / "stage_a" / a.SNAPSHOT / c["candidate_id"] / f["fold_id"]
    path.mkdir(parents=True)
    payload = {"protocol": s["protocol"], "snapshot_token": a.SNAPSHOT,
               "candidate_id": c["candidate_id"], "candidate": c["candidate"],
               "fold_id": f["fold_id"], "fold": f, "artifact_namespace": None}
    for k, name in (("signal", "signal.parquet"), ("report", "report.parquet"),
                    ("decision", "decisions.json")):
        (path / name).write_bytes((k + " frozen").encode())
        payload[k + "_artifact"] = {"sha256": a.sha256(path / name),
                                     "path": "/vol/csi1000_tuner/stage_a/" + a.SNAPSHOT + "/" + c["candidate_id"] + "/" + f["fold_id"] + "/" + name}
    a.save_json(path / "result.json", payload)
    return s, c, f, path


def test_controls_eight_and_all_320():
    s = frozen()
    assert len(a.roster(s, "controls")) == 8
    assert len(a.roster(s, "all")) == 320
    s["ranked_candidates"].pop()
    with pytest.raises(ValueError, match="80 candidate"):
        a.roster(s, "all")


def test_provider_manifest_fail_closed(tmp_path):
    s = frozen()
    path = tmp_path / "provider_snapshot.json"
    a.save_json(path, s["manifest"])
    assert a.check_provider(path, s["manifest"]) == a.sha256(path)
    bad = dict(s["manifest"], provider_fingerprint="altered")
    a.save_json(path, bad)
    with pytest.raises(ValueError, match="fingerprint"):
        a.check_provider(path, s["manifest"])


def test_source_bytes_and_identity_fail_closed(tmp_path):
    s, c, f, path = fixture_files(tmp_path)
    payload, paths, hashes = a.checked_sources(tmp_path, c, f)
    assert len(paths) == len(hashes) == 4
    assert payload["artifact_namespace"] is None
    with (path / "signal.parquet").open("ab") as h:
        h.write(b"tampered")
    with pytest.raises(ValueError, match="SHA mismatch"):
        a.checked_sources(tmp_path, c, f)


def test_reject_wrong_fold_and_repeat_a(tmp_path):
    s, c, f, path = fixture_files(tmp_path)
    with pytest.raises(ValueError, match="result identity"):
        a.checked_sources(tmp_path, c, dict(f, execution=["2099-01-01", "2099-01-31"]))
    payload = a.read_json(path / "result.json")
    payload["artifact_namespace"] = "_repro/repeat_a"
    a.save_json(path / "result.json", payload)
    with pytest.raises(ValueError, match="repeat_a"):
        a.checked_sources(tmp_path, c, f)


def test_reject_source_symlink(tmp_path):
    s, c, f, path = fixture_files(tmp_path)
    (path / "signal.parquet").unlink()
    (path / "signal.parquet").symlink_to(path / "report.parquet")
    with pytest.raises(ValueError, match="symlink"):
        a.checked_sources(tmp_path, c, f)


def test_output_cannot_overlap_source(tmp_path):
    orig, provider = tmp_path / "source", tmp_path / "source" / "cn_data"
    provider.mkdir(parents=True)
    with pytest.raises(ValueError, match="immutable"):
        a.safe_output(orig, provider, orig / "new_artifacts")
    assert a.safe_output(orig, provider, tmp_path / "research") == (tmp_path / "research").resolve()


def test_order_replay_requires_exact_identity_and_fill():
    base = [{"start_time": "2021-01-04", "end_time": "2021-01-05",
             "orders": [{"stock_id": "SH600000", "direction": 1,
                         "start_time": "a", "end_time": "b", "amount": 100.0,
                         "deal_amount": 100.0, "factor": 1.0}]}]
    a.compare_decisions(base, json.loads(json.dumps(base)))
    other = json.loads(json.dumps(base))
    other[0]["orders"][0]["deal_amount"] = 90
    with pytest.raises(ValueError, match="quantity"):
        a.compare_decisions(base, other)


def test_numerical_report_strict_and_calendar():
    pd = pytest.importorskip("pandas")
    old = pd.DataFrame({"account": [100.0, 101.0], "cost": [0.0, .0001]},
                       index=pd.to_datetime(["2021-01-04", "2021-01-05"]))
    a.compare_report(old, old.copy())
    changed = old.copy()
    changed.iloc[1, 0] += 1
    with pytest.raises(ValueError, match="numerical"):
        a.compare_report(old, changed)
    with pytest.raises(ValueError, match="calendar"):
        a.compare_report(old, old.iloc[::-1])


def test_resume_checks_output_byte_hash_and_result_path(tmp_path):
    s, c, f, src = fixture_files(tmp_path / "source")
    snapshot = tmp_path / "source" / "csi1000_tuner" / "stage_a" / "provider_snapshot.json"
    a.save_json(snapshot, s["manifest"])
    output = tmp_path / "results"
    output.mkdir()
    needed = ("fixed_report.parquet", "fixed_decisions.json", "daily_nav_delta.csv",
              "order_delta.json", "legacy_reproduction.json", "fixed_metrics.json")
    for name in needed:
        (output / name).write_text("test")
    record = {"status": "PASS", "run_version": a.VERSION, "candidate_id": c["candidate_id"],
              "fold_id": f["fold_id"], "source_sha256": a.checked_sources(tmp_path / "source", c, f)[2],
              "provider_manifest_sha256": a.sha256(snapshot),
              "output_sha256": {x: a.sha256(output / x) for x in needed}}
    a.save_json(output / "comparison.json", record)
    assert a.checked_resume(tmp_path / "source", snapshot, s, c, f, output) == record
    (output / "order_delta.json").write_text("changed")
    with pytest.raises(ValueError, match="resume output SHA"):
        a.checked_resume(tmp_path / "source", snapshot, s, c, f, output)


def test_incomplete_batch_never_produces_80_rank():
    s = frozen()
    c, f = a.roster(s, "controls")[0]
    with pytest.raises(ValueError, match="missing cell"):
        a.aggregate(s, [{"status": "BLOCKED", "candidate_id": c["candidate_id"], "fold_id": f["fold_id"]}], "controls")
