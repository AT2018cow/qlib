"""Negative controls for Stage-B F5; no Qlib, provider or Modal required."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from audit.stage_b_f5_gates import (
    audit_failures, check_complete_sell, check_full_buy,
    require_pass, sha256_file, verify_input,
)


def valid_gate():
    return dict(
        calendar_exact=True, decisions_n=424, report_n=424,
        decision_mismatches=0, tradability_violations=0,
        lot_violations=0, factor_mismatches=0,
        buy_amount_mismatches=0, sell_amount_mismatches=0,
        negative_cash_events=0, max_account_diff=Decimal("0.00000007"),
        max_return_diff=5e-16, max_fee_diff=Decimal("0.00001"),
        max_turnover_diff=Decimal("0.00005"),
        max_fee_rate_diff=Decimal("1e-16"),
        max_turnover_rate_diff=Decimal("1e-16"),
    )


class InputIdentityTests(unittest.TestCase):
    def test_actual_bytes_must_match_independent_expected_sha(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "market_data.parquet"
            source.write_bytes(b"authorized-provider-export-original")
            pinned = hashlib.sha256(source.read_bytes()).hexdigest()
            identity = verify_input(source, pinned, "market")
            self.assertEqual(identity["actual_sha256"], pinned)
            self.assertTrue(identity["match"])
            self.assertEqual(identity["name"], "market_data.parquet")
            # A supplied different market file cannot silently use default
            # raw/market_data.parquet's SHA or a self-computed expected hash.
            source.write_bytes(b"authorized-provider-export-TAMPERED")
            with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                verify_input(source, pinned, "market")

    def test_rejects_missing_or_bad_external_pin(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.json"
            source.write_text("{}")
            for wrong in (None, "", "deadbeef", "a" * 63, "G" * 64):
                with self.subTest(pin=wrong):
                    with self.assertRaisesRegex(ValueError, "expected SHA256"):
                        verify_input(source, wrong, "decisions")
            with self.assertRaisesRegex(ValueError, "file missing"):
                verify_input(Path(directory) / "missing", "a" * 64, "decisions")

    def test_committed_fixed_diagnostics_match_self_reported_input_hashes(self):
        base = Path("audit/evidence/winner_phase0_fixed")
        summary = json.loads((base / "fixed_independent_ledger_summary.json").read_text())
        for label, filename in (
            ("report", "fixed_diagnostic_report.parquet"),
            ("decisions", "fixed_diagnostic_decisions.json"),
        ):
            pinned = summary["artifacts"][label]["actual_sha256"]
            actual = sha256_file(base / filename)
            self.assertEqual(actual, pinned, filename)


class OrderQuantityTests(unittest.TestCase):
    def test_full_sell_uses_position_before_mutation(self):
        check_complete_sell(991231.123456, 991231.123456, 991231.123456, "SH688066")
        with self.assertRaisesRegex(ValueError, "pre-trade position"):
            check_complete_sell(991231.123456, 991231.023456, 991231.023456, "SH688066")
        with self.assertRaisesRegex(ValueError, "full requested"):
            check_complete_sell(991231.123456, 991231.123456, 991231.023456, "SH688066")

    def test_full_buy_required(self):
        check_full_buy(200_000.0, 200_000.0, "SH603301")
        with self.assertRaisesRegex(ValueError, "partial buy"):
            check_full_buy(200_000.0, 180_000.0, "SH603301")
        with self.assertRaisesRegex(ValueError, "nonfinite buy"):
            check_full_buy(float("nan"), 200_000.0, "SH603301")


class StrictVerdictTests(unittest.TestCase):
    def test_valid_values_pass(self):
        self.assertEqual(audit_failures(**valid_gate()), [])
        require_pass(**valid_gate())

    def test_each_error_causes_fail(self):
        cases = {
            "calendar_exact": False,
            "decisions_n": 423,
            "report_n": 423,
            "decision_mismatches": 1,
            "tradability_violations": 1,
            "lot_violations": 1,
            "factor_mismatches": 1,
            "buy_amount_mismatches": 1,
            "sell_amount_mismatches": 1,
            "negative_cash_events": 1,
            "max_account_diff": Decimal("0.02"),
            "max_return_diff": 1e-9,
            "max_fee_diff": Decimal("0.02"),
            "max_turnover_diff": Decimal("0.02"),
            "max_fee_rate_diff": Decimal("1e-9"),
            "max_turnover_rate_diff": Decimal("1e-9"),
        }
        for key, wrong_value in cases.items():
            with self.subTest(key=key):
                args = valid_gate()
                args[key] = wrong_value
                self.assertTrue(audit_failures(**args), key)
                with self.assertRaisesRegex(ValueError, "F5 independent audit FAIL"):
                    require_pass(**args)

    def test_nan_and_infinity_can_never_pass(self):
        for invalid in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(value=invalid):
                args = valid_gate()
                args["max_fee_diff"] = Decimal(invalid)
                self.assertIn("fees_cny", audit_failures(**args))


if __name__ == "__main__":
    unittest.main()
