import tempfile
import unittest
from pathlib import Path
from qlib_audit_fixes import (
    read_trading_calendar, last_matured_sample, infer_label_horizon, purge_cfg_splits,
)


class SplitTests(unittest.TestCase):
    def setUp(self):
        self.cal = ["2022-12-26", "2022-12-27", "2022-12-28", "2022-12-29",
                    "2022-12-30", "2023-01-03", "2023-01-04", "2023-01-05",
                    "2023-01-06", "2023-01-09", "2023-01-10", "2023-01-11"]

    def test_boundary_strictly_before_test(self):
        self.assertEqual(last_matured_sample(self.cal, "2023-01-09", 2), "2023-01-04")
        self.assertLess(self.cal.index("2023-01-04") + 2, self.cal.index("2023-01-09"))

    def test_no_calendar_is_not_silently_accepted(self):
        with self.assertRaises(FileNotFoundError):
            read_trading_calendar("/no/such/provider")

    def test_read_calendar_rejects_duplicates(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "calendars" / "day.txt"
            p.parent.mkdir()
            p.write_text("2023-01-03\n2023-01-03\n")
            with self.assertRaises(ValueError):
                read_trading_calendar(d)

    def test_infer_forward_label_horizon(self):
        cfg = self.config()
        self.assertEqual(infer_label_horizon(cfg), 2)
        cfg["task"]["dataset"]["kwargs"]["handler"]["kwargs"]["label"] = ["Ref($close,-20)/$close-1"]
        self.assertEqual(infer_label_horizon(cfg), 20)

    def config(self):
        return {"task": {"dataset": {"kwargs": {
            "handler": {"kwargs": {"fit_end_time": "2022-12-29", "label": ["Ref($close,-2)/Ref($close,-1)-1"]}},
            "segments": {"train": ["2022-12-26", "2022-12-29"],
                         "valid": ["2022-12-30", "2023-01-06"],
                         "test": ["2023-01-09", "2023-01-11"]},
        }}}}

    def test_purge_train_and_validation_and_fit(self):
        cfg = self.config()
        purge_cfg_splits(cfg, self.cal)
        seg = cfg["task"]["dataset"]["kwargs"]["segments"]
        self.assertEqual(seg["train"][1], "2022-12-27")
        self.assertEqual(seg["valid"][1], "2023-01-04")
        self.assertEqual(cfg["task"]["dataset"]["kwargs"]["handler"]["kwargs"]["fit_end_time"], "2022-12-27")
        self.assertEqual(seg["test"], ["2023-01-09", "2023-01-11"])

    def test_large_horizon_fails_closed(self):
        with self.assertRaises(ValueError):
            purge_cfg_splits(self.config(), self.cal, horizon=20)

    def test_unknown_labels_fail_closed(self):
        cfg = self.config()
        cfg["task"]["dataset"]["kwargs"]["handler"]["kwargs"]["label"] = ["Something($close)"]
        with self.assertRaises(ValueError):
            infer_label_horizon(cfg)


if __name__ == "__main__":
    unittest.main()
