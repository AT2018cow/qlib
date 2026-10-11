"""Offline, synthetic official CSI index API parsing negative controls."""
import json
import unittest
from datetime import date, timedelta

from audit.export_csi1000_official_index_closes import parse_daily_records


def mock_response():
    def row(day, close):
        r = [None] * 16
        r[0], r[1], r[9] = day.isoformat(), "000852", close
        return r
    d = date(2025, 1, 2)
    return {"data": [row(date(2024, 12, 31), 1000)] +
            [row(d + timedelta(days=i), 1000 + i) for i in range(424)]}


class TestOfficialSourceParser(unittest.TestCase):
    def test_synthetic_source_425_rows(self):
        parsed = parse_daily_records(json.dumps(mock_response()).encode())
        self.assertEqual(len(parsed), 425)
        self.assertEqual(parsed[0][0], date(2024, 12, 31))
        self.assertEqual(parsed[-1][1], 1423)

    def test_negative_missing_previous_close(self):
        x = mock_response()
        x["data"].pop(0)
        with self.assertRaisesRegex(ValueError, "preceding close"):
            parse_daily_records(json.dumps(x).encode())

    def test_negative_index_identity(self):
        x = mock_response()
        x["data"][12][1] = "000905"
        with self.assertRaisesRegex(ValueError, "index_code"):
            parse_daily_records(json.dumps(x).encode())

    def test_negative_invalid_close(self):
        x = mock_response()
        x["data"][12][9] = -1
        with self.assertRaisesRegex(ValueError, "invalid CSI index close"):
            parse_daily_records(json.dumps(x).encode())

    def test_negative_duplicate_day(self):
        x = mock_response()
        x["data"][13][0] = x["data"][12][0]
        with self.assertRaisesRegex(ValueError, "duplicate"):
            parse_daily_records(json.dumps(x).encode())

    def test_negative_missing_session(self):
        x = mock_response()
        x["data"].pop(3)
        with self.assertRaisesRegex(ValueError, "424 exact"):
            parse_daily_records(json.dumps(x).encode())

    def test_negative_bad_schema(self):
        x = mock_response()
        x["data"][8] = ["incomplete"]
        with self.assertRaisesRegex(ValueError, "schema"):
            parse_daily_records(json.dumps(x).encode())


if __name__ == "__main__":
    unittest.main()
