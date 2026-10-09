"""Negative controls for the offline published-F5 verifier (stdlib only).

These tests DO NOT validate the private market provider or real fill feasibility.
"""
from __future__ import annotations

import csv
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from audit.verify_f5_committed_evidence import metrics, near, number, verify_repo


REPO = Path(__file__).resolve().parent.parent
FIXED = Path("audit/evidence/winner_phase0_fixed")
RAW = Path("audit/evidence/winner_phase0/raw")


def copy_fixture(root: Path) -> None:
    (root / FIXED).mkdir(parents=True)
    (root / RAW).mkdir(parents=True)
    for name in (
        "f5_hardened_manifest.json",
        "f5_hardened_summary.json",
        "comparison.json",
        "fixed_diagnostic_report.parquet",
        "fixed_diagnostic_decisions.json",
        "daily_legacy_vs_fixed.csv",
        "account_rebuild_daily.csv",
        "execution_orders.csv",
        "decision_vs_signal.csv",
    ):
        shutil.copyfile(REPO / FIXED / name, root / FIXED / name)
    for name in ("signal.parquet", "day.txt", "csi1000_instruments.txt", "decisions.json"):
        shutil.copyfile(REPO / RAW / name, root / RAW / name)


def corrupt_csv_first_row(path: Path, column: str, new_value: str) -> None:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames
        rows = list(reader)
    rows[0][column] = new_value
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class F5PublishedEvidenceTests(unittest.TestCase):
    def test_committed_evidence_passes_offline_only(self):
        output = verify_repo(REPO)
        self.assertEqual(output["status"], "PASS_PUBLISHED_SIMULATION_EVIDENCE_ONLY")
        self.assertEqual(output["days"], 424)
        self.assertEqual(output["orders"], 1696)
        self.assertEqual(output["stall_legacy_orders"], 0)
        self.assertEqual(output["stall_fixed_orders"], 950)
        self.assertEqual(output["market_execution"], "BLOCKED")
        self.assertAlmostEqual(output["sharpe"], 0.9988782409498526, places=11)

    def test_tampered_report_bytes_abort_before_ledger_check(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            copy_fixture(root)
            path = root / FIXED / "fixed_diagnostic_report.parquet"
            with path.open("ab") as handle:
                handle.write(b"tampered")
            with self.assertRaisesRegex(ValueError, "report: committed byte SHA mismatch"):
                verify_repo(root)

    def test_tampered_market_manifest_identity_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            copy_fixture(root)
            path = root / FIXED / "f5_hardened_manifest.json"
            obj = json.loads(path.read_text())
            obj["verified_inputs"]["market"]["actual_sha256"] = "0" * 64
            path.write_text(json.dumps(obj))
            with self.assertRaisesRegex(ValueError, "unmatched market-export SHA"):
                verify_repo(root)

    def test_mutated_execution_order_or_account_fails(self):
        cases = [
            ("execution_orders.csv", "filled_qty", "0", "decision filled quantity"),
            ("daily_legacy_vs_fixed.csv", "fixed_account", "1", "cash + holdings"),
            ("decision_vs_signal.csv", "match", "False", "signal/order reconstruction"),
        ]
        for filename, col, new_value, diagnostic in cases:
            with self.subTest(file=filename, column=col):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp)
                    copy_fixture(root)
                    corrupt_csv_first_row(root / FIXED / filename, col, new_value)
                    with self.assertRaisesRegex(ValueError, diagnostic):
                        verify_repo(root)

    def test_nonfinite_and_metric_math_fail_closed(self):
        for candidate in ("NaN", "Infinity", "-Infinity", ""):
            with self.subTest(candidate=candidate):
                with self.assertRaises(ValueError):
                    number(candidate, "bad")
        with self.assertRaisesRegex(ValueError, "mismatch"):
            near(1, 2, tolerance=0.01, label="test")
        with self.assertRaisesRegex(ValueError, "metric calendar length"):
            metrics([0.01], [101.0], ["2025-01-02"])


if __name__ == "__main__":
    unittest.main()
