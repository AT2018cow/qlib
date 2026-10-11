"""Synthetic negative controls plus pinned JSON smoke; no data provider required."""
from __future__ import annotations

import copy
import unittest
from datetime import date, timedelta

from audit.csi1000_residual_evidence import (
    ROOT, compare_index_prices, read_pinned, validate_residual,
)


def fake_index():
    # Entirely synthetic chronology, not external historical market evidence.
    start = date(2025, 1, 2)
    dates = [start + timedelta(days=i) for i in range(424)]
    index_days = [start - timedelta(days=1)] + dates
    levels = [1000 + float(i) for i in range(425)]
    rows = [
        {"date": d.isoformat(), "close": str(levels[i]), "index_code": "000852",
         "return_type": "price_return", "source_id": "independent_fixture",
         "source_url": "https://example.invalid/synthetic"}
        for i, d in enumerate(index_days)
    ]
    saved = [levels[i + 1] / levels[i] - 1 for i in range(424)]
    return rows, dates, saved


class TestBenchmarkGate(unittest.TestCase):
    def test_synthetic_calculation_matches(self):
        rows, dates, saved = fake_index()
        result = compare_index_prices(rows, dates, saved)
        self.assertEqual(result["n_sessions"], 424)
        self.assertEqual(result["status"],
                         "NUMERICAL_MATCH_PENDING_HUMAN_SOURCE_CERTIFICATION")
        self.assertEqual(result["max_abs_daily_return_error"], 0)

    def test_negative_shift_date(self):
        rows, dates, saved = fake_index()
        rows[25]["date"] = "2024-01-01"
        with self.assertRaisesRegex(ValueError, "dates"):
            compare_index_prices(rows, dates, saved)

    def test_negative_missing_previous_close(self):
        rows, dates, saved = fake_index()
        with self.assertRaisesRegex(ValueError, "previous index close"):
            compare_index_prices(rows[1:], dates, saved)

    def test_negative_duplicate_date(self):
        rows, dates, saved = fake_index()
        rows[3]["date"] = rows[2]["date"]
        with self.assertRaisesRegex(ValueError, "duplicate/unsorted"):
            compare_index_prices(rows, dates, saved)

    def test_negative_close_tamper(self):
        rows, dates, saved = fake_index()
        rows[200]["close"] = str(float(rows[200]["close"]) + 2)
        with self.assertRaisesRegex(ValueError, "not within"):
            compare_index_prices(rows, dates, saved)

    def test_negative_wrong_index_and_return_basis(self):
        rows, dates, saved = fake_index()
        rows[1]["index_code"] = "000300"
        with self.assertRaisesRegex(ValueError, "index identity"):
            compare_index_prices(rows, dates, saved)
        rows[1]["index_code"] = "000852"
        rows[1]["return_type"] = "total_return"
        with self.assertRaisesRegex(ValueError, "methodology mismatch"):
            compare_index_prices(rows, dates, saved)

    def test_negative_same_provider(self):
        rows, dates, saved = fake_index()
        rows[0]["source_id"] = "qlib"
        with self.assertRaisesRegex(ValueError, "self-certify"):
            compare_index_prices(rows, dates, saved)


class TestPinnedResidual(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.objects = [
            read_pinned(ROOT, k) for k in ("batch", "ledger", "baseline", "winner")
        ]

    def test_expected_frozen_artifacts_and_order_links(self):
        result = validate_residual(*self.objects)
        self.assertEqual(len(result["st_events"]), 4)
        self.assertEqual(result["ranking"]["baseline_diagnostic_rank"], 1)
        self.assertEqual(result["ranking"]["winner_diagnostic_rank"], 10)
        self.assertEqual(result["recommendation"], "KEEP_CANONICAL_PENDING_FORWARD")
        self.assertEqual(result["st_exposure"], "UNQUANTIFIED_PATH_DEPENDENCE")

    def test_negative_ledger_status(self):
        batch, ledger, baseline, winner = self.objects
        broken = copy.deepcopy(ledger)
        broken["cells"][0]["ledger"] = "PASS_REAL_MARKET"
        with self.assertRaisesRegex(ValueError, "accepted scope"):
            validate_residual(batch, broken, baseline, winner)

    def test_negative_st_order_tamper(self):
        batch, ledger, baseline, winner = self.objects
        altered = copy.deepcopy(baseline)
        match = next(
            order for row in altered if row["start_time"].startswith("2025-02-21")
            for order in row["orders"] if order["stock_id"] == "SZ000488"
        )
        match["deal_amount"] = 0
        with self.assertRaisesRegex(ValueError, "full-fill"):
            validate_residual(batch, ledger, altered, winner)

    def test_negative_rerank_identity(self):
        batch, ledger, baseline, winner = self.objects
        bad = copy.deepcopy(batch)
        bad["ranking"]["new_ranking"][0]["new_rank"] = 11
        with self.assertRaisesRegex(ValueError, "ordering drift"):
            validate_residual(bad, ledger, baseline, winner)

    def test_negative_snapshot(self):
        batch, ledger, baseline, winner = self.objects
        bad = copy.deepcopy(batch)
        bad["original_snapshot_token"] = "wrong"
        with self.assertRaisesRegex(ValueError, "snapshot"):
            validate_residual(bad, ledger, baseline, winner)


if __name__ == "__main__":
    unittest.main()
