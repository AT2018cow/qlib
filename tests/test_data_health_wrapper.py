import tempfile
import unittest
from pathlib import Path

import pandas as pd

from scripts.check_data_health import DataHealthChecker


class DataHealthCsvTests(unittest.TestCase):
    def _write(self, df):
        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name)
        df.to_csv(path / "sample.csv", index=False)
        self.addCleanup(tmp.cleanup)
        return path

    def test_csv_mode_accepts_no_qlib_dir(self):
        p = self._write(pd.DataFrame({
            "open": [1.0, 1.1], "high": [1.1, 1.2], "low": [0.9, 1.0],
            "close": [1.0, 1.1], "volume": [100, 110], "factor": [1.0, 1.0],
        }))
        checker = DataHealthChecker(csv_path=str(p))
        self.assertIsNone(checker.qlib_dir)
        self.assertIn("sample.csv", checker.data)

    def test_missing_required_columns_are_reported_without_length_error(self):
        p = self._write(pd.DataFrame({"open": [1.0], "close": [1.0], "factor": [1.0]}))
        checker = DataHealthChecker(csv_path=str(p))
        out = checker.check_required_columns()
        self.assertEqual(out.loc["sample.csv", "missing_col"], "high,low,volume")

    def test_missing_factor_is_reported_without_keyerror(self):
        p = self._write(pd.DataFrame({
            "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [100],
        }))
        checker = DataHealthChecker(csv_path=str(p))
        out = checker.check_missing_factor()
        self.assertTrue(bool(out.loc["sample.csv", "missing_factor_col"]))
        self.assertFalse(bool(out.loc["sample.csv", "missing_factor_data"]))

    def test_large_step_plain_csv_index_does_not_assume_multiindex(self):
        p = self._write(pd.DataFrame({
            "open": [1.0, 2.0], "high": [1.0, 2.0], "low": [1.0, 2.0],
            "close": [1.0, 2.0], "volume": [100, 100], "factor": [1.0, 1.0],
        }))
        checker = DataHealthChecker(csv_path=str(p), large_step_threshold_price=0.5)
        out = checker.check_large_step_changes()
        self.assertIsNotNone(out)
        self.assertEqual(out.iloc[0]["date"], "1")


if __name__ == "__main__":
    unittest.main()
