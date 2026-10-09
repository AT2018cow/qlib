"""Offline negative controls for Stage-B F6 original frozen-input identity.

Tests run against committed export Parquets; do NOT run Qlib or nine new
replays. The F6 execution outcome must remain BLOCKED.
"""
from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from audit.stage_b_f6_preflight import (
    BASELINE, EXPORT_ROOT, FROZEN, PHASES, PREFIX, WINNER,
    check_optional, preflight, require, select_phases, source_suffix,
    validate_report_export,
)


ROOT = Path(__file__).resolve().parent.parent


class F6FrozenInputsTests(unittest.TestCase):
    def test_exactly_ten_original_reports_are_really_sha_verified(self):
        output = preflight(ROOT)
        self.assertEqual(output["report_hashes_verified"], 10)
        self.assertEqual(output["total_phases"], 10)
        self.assertEqual(output["source_pairs_ready"], 1)
        self.assertEqual(output["source_pairs_blocked"], 9)
        self.assertEqual(output["overall_f6"], "BLOCKED")
        self.assertEqual(output["provider_live_fingerprint_check"], "NOT_PERFORMED")
        self.assertEqual(
            {(x["candidate"], x["phase"]) for x in output["rows"]},
            {(candidate, phase) for candidate in ("winner", "baseline") for phase in PHASES},
        )
        self.assertTrue(all(x["f6_fixed_replay"] == "NOT_EXECUTED" for x in output["rows"]))
        self.assertTrue(all(x["f6_independent_fixed_ledger"] == "NOT_EXECUTED" for x in output["rows"]))
        self.assertTrue(all(x["report"]["status"] == "VERIFIED_FROZEN_BYTES" for x in output["rows"]))
        self.assertIn("_preflight/", next(x for x in output["rows"] if x["candidate"] == "baseline"
                                         and x["phase"] == 0)["source_directory_relative_to_snapshot"])
        self.assertIn("/repeat_b/", next(x for x in output["rows"] if x["candidate"] == "baseline"
                                        and x["phase"] == 0)["source_directory_relative_to_snapshot"])

    def test_preserves_preflight_baseline_origin(self):
        payload = json.loads((ROOT / FROZEN).read_text())
        phase = select_phases(payload)["baseline"][0]
        actual = source_suffix(phase["report_artifact"]["path"], phase=0,
                               candidate_id=BASELINE)
        self.assertIn("repeat_b", actual.parts)
        with self.assertRaisesRegex(ValueError, "preflight repeat_b"):
            source_suffix(PREFIX + f"phases/{BASELINE}/phase00/report.parquet",
                          phase=0, candidate_id=BASELINE)
        with self.assertRaisesRegex(ValueError, "candidate/phase path mismatch"):
            source_suffix(PREFIX + f"phases/{WINNER}/phase04/report.parquet",
                          phase=10, candidate_id=WINNER)
        with self.assertRaisesRegex(ValueError, "unsafe snapshot artifact"):
            source_suffix(PREFIX + f"phases/{WINNER}/../phase00/report.parquet",
                          phase=0, candidate_id=WINNER)
        with self.assertRaisesRegex(ValueError, "outside frozen result"):
            source_suffix("/tmp/unverified/report.parquet", phase=0, candidate_id=WINNER)

    def test_rejects_candidate_or_snapshot_drift(self):
        payload = json.loads((ROOT / FROZEN).read_text())
        modified = copy.deepcopy(payload)
        modified["manifest"]["snapshot_token"] = "WRONG"
        with self.assertRaisesRegex(ValueError, "snapshot drift"):
            select_phases(modified)
        modified = copy.deepcopy(payload)
        winner = next(x for x in modified["ranked_candidates"]
                      if x["candidate_id"] == WINNER)
        winner["phase_results"].pop()
        with self.assertRaisesRegex(ValueError, "phase count/order drift"):
            select_phases(modified)

    def test_corrupting_an_exported_report_aborts(self):
        published = json.loads((ROOT / EXPORT_ROOT / "manifest.json").read_text())
        original = json.loads((ROOT / FROZEN).read_text())
        winner = select_phases(original)["winner"][0]
        entry = next(x for x in published["files"]
                     if x["volume_source_path"] == winner["report_artifact"]["path"])
        with tempfile.TemporaryDirectory() as folder:
            copyfile = Path(folder) / "report.parquet"
            shutil.copyfile(ROOT / EXPORT_ROOT / entry["file"], copyfile)
            validate_report_export(copyfile, entry, winner)
            with copyfile.open("ab") as f:
                f.write(b"tampering")
            with self.assertRaisesRegex(ValueError, "report source bytes differ"):
                validate_report_export(copyfile, entry, winner)

    def test_missing_or_bad_signal_and_decisions_are_not_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            src = Path(folder) / "signal.parquet"
            self.assertEqual(check_optional(src, "f" * 64, "winner phase04 signal")["status"],
                             "BLOCKED_MISSING_ORIGINAL_SOURCE")
            src.write_bytes(b"wrong bytes")
            with self.assertRaisesRegex(ValueError, "original source SHA mismatch"):
                check_optional(src, "f" * 64, "winner phase04 signal")

    def test_missing_nine_source_pairs_are_blocked_not_pass(self):
        output = preflight(ROOT)
        with self.assertRaisesRegex(ValueError, "some original phase sources unavailable"):
            require(output["source_pairs_ready"] == 10, "some original phase sources unavailable")


if __name__ == "__main__":
    unittest.main()
