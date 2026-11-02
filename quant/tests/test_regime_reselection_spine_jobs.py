"""G1 spine job planning tests (stdlib only)."""

import sys
import unittest
from pathlib import Path

try:
    from spine_jobs import build_jobs, expected_days, failed_jobs, swapped_horizon, validate_assembly
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code" / "research" / "regime_reselection"))
    from spine_jobs import build_jobs, expected_days, failed_jobs, swapped_horizon, validate_assembly


def calendar():
    # 45 consecutive business-like labels; enough for three 20-session blocks.
    return [f"2021-{month:02d}-{day:02d}" for month in range(1, 13) for day in range(1, 29)][:45]


class SpineJobTests(unittest.TestCase):
    def test_horizon_swap(self):
        self.assertEqual(swapped_horizon(20), 10)
        self.assertEqual(swapped_horizon(10), 20)
        with self.assertRaises(ValueError):
            swapped_horizon(2)

    def test_jobs_cover_period_without_overlap(self):
        cal = calendar()
        jobs = build_jobs(cal, cal[0], cal[44], "run", "sha", markets=("csi300",), spines=("A", "B"))
        self.assertEqual(len(jobs), 6)  # 3 blocks x 2 spines
        for spine in ("A", "B"):
            blocks = [job for job in jobs if job["spine"] == spine]
            self.assertEqual(blocks[0]["retrain_asof"], cal[0])
            self.assertEqual(blocks[-1]["signal_end"], cal[44])
            for left, right in zip(blocks, blocks[1:]):
                self.assertLess(left["signal_end"], right["retrain_asof"])
        self.assertEqual({job["label_horizon"] for job in jobs if job["spine"] == "A"}, {20})
        self.assertEqual({job["label_horizon"] for job in jobs if job["spine"] == "B"}, {10})

    def test_unknown_market_or_spine_fails(self):
        cal = calendar()
        with self.assertRaises(KeyError):
            build_jobs(cal, cal[0], cal[10], "run", "sha", markets=("chinext",))
        with self.assertRaises(ValueError):
            build_jobs(cal, cal[0], cal[10], "run", "sha", markets=("csi300",), spines=("Z",))

    def test_assembly_validation(self):
        cal = calendar()
        days = expected_days(cal, cal[0], cal[10])
        validate_assembly(cal, cal[0], cal[10], days)
        with self.assertRaises(ValueError):
            validate_assembly(cal, cal[0], cal[10], days[:-1])
        with self.assertRaises(ValueError):
            validate_assembly(cal, cal[0], cal[10], sorted(days + [cal[11]]))

    def test_failed_jobs_classification(self):
        jobs = [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}]
        results = [
            {"status": "PASS"},
            RuntimeError("boom"),
            {"status": "FAIL"},
            {"status": "PASS", "extra": 1},
        ]
        self.assertEqual(failed_jobs(jobs, results), [jobs[1], jobs[2]])


if __name__ == "__main__":
    unittest.main()
