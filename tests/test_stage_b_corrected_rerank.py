"""CPU-only unit tests for all-11 frozen replays (without private provider).

The real Qlib replay and independent execution ledger remain unavailable in CI;
these tests cover frozen IDs, SHA-tamper failures, source namespaces and the
unchanged 11-candidate ranking contract.
"""
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from audit.run_stage_b_corrected_rerank import (
    BASELINE, FROZEN_N, PHASES, WINNER, diagnose_ranking, plan_sources,
)
from audit.stage_b_f6_preflight import FROZEN, source_suffix


REPO = Path(__file__).resolve().parents[1]


class BatchOriginalReplayTests(unittest.TestCase):
    def setUp(self):
        self.frozen = json.loads((REPO / FROZEN).read_text())

    def prepare_synthetic_original(self, root: Path):
        """165 independent byte-pinned files under their actual frozen paths."""
        manifest = copy.deepcopy(self.frozen)
        for c in manifest["ranked_candidates"]:
            cid = c["candidate_id"]
            for phase in c["phase_results"]:
                ph = phase["phase"]
                for kind, name in (("report", "report.parquet"),
                                   ("signal", "signal.parquet"),
                                   ("decision", "decisions.json")):
                    meta = phase[kind + "_artifact"]
                    relative = source_suffix(meta["path"], phase=ph, candidate_id=cid)
                    self.assertEqual(relative.name, name)
                    path = root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    data = f"original-frozen-{cid}-p{ph:02d}-{kind}".encode()
                    path.write_bytes(data)
                    meta["sha256"] = hashlib.sha256(data).hexdigest()
        return manifest

    def test_all_11_frozen_candidates_and_baseline_special_namespace(self):
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            payload = self.prepare_synthetic_original(root)
            all_jobs = plan_sources(payload, root, "all")
            controls = plan_sources(payload, root, "controls")
            self.assertEqual(len(all_jobs), 55)
            self.assertEqual(len(controls), 10)
            self.assertEqual(len({j["candidate_id"] for j in all_jobs}), FROZEN_N)
            self.assertEqual({j["phase"] for j in all_jobs}, set(PHASES))
            baseline0 = next(j for j in all_jobs
                             if j["candidate_id"] == BASELINE and j["phase"] == 0)
            self.assertIn("repeat_b", baseline0["paths"]["report"].parts)
            self.assertEqual({j["candidate_id"] for j in controls}, {WINNER, BASELINE})

    def test_missing_original_stops_before_any_replay(self):
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            payload = self.prepare_synthetic_original(root)
            control = plan_sources(payload, root, "controls")
            control[0]["paths"]["signal"].unlink()
            with self.assertRaisesRegex(ValueError, "missing signal"):
                plan_sources(payload, root, "controls")

    def test_tampered_original_bytes_fail_instead_of_fallback(self):
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            payload = self.prepare_synthetic_original(root)
            chosen = plan_sources(payload, root, "controls")
            chosen[0]["paths"]["decision"].write_bytes(b"tampered historical decision")
            with self.assertRaisesRegex(ValueError, "frozen source bytes differ"):
                plan_sources(payload, root, "controls")

    def test_frozen_roster_changes_rejected(self):
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            payload = self.prepare_synthetic_original(root)
            payload["ranked_candidates"].pop()
            with self.assertRaisesRegex(ValueError, "11-candidate roster drift"):
                plan_sources(payload, root, "all")

    def test_only_all_candidates_may_compute_diagnostic_rank(self):
        records = []
        for c in self.frozen["ranked_candidates"]:
            for phase in c["phase_results"]:
                records.append({
                    "candidate_id": c["candidate_id"],
                    "phase": phase["phase"],
                    "fixed_metrics": copy.deepcopy(phase["phase_metrics"]),
                })
        blocked = diagnose_ranking(self.frozen, records[:10], "controls")
        self.assertEqual(blocked["status"], "BLOCKED_NOT_ALL_11_CANDIDATES")
        self.assertFalse(blocked["new_winner_certified"])
        complete = diagnose_ranking(self.frozen, records, "all")
        self.assertEqual(len(complete["new_ranking"]), 11)
        self.assertEqual(complete["diagnostic_top_candidate"], WINNER)
        self.assertTrue(complete["same_winner_after_execution_correction"])
        self.assertFalse(complete["new_winner_certified"])
        with self.assertRaisesRegex(ValueError, "not all frozen candidates"):
            diagnose_ranking(self.frozen, records[:-1], "all")


if __name__ == "__main__":
    unittest.main()
