"""No Qlib/Modal runtime needed: regression tests for F1 quote coverage."""
import unittest
from execution_quote_universe import execution_quote_codes


class FakeInstrumentProvider:
    def __init__(self):
        self.calls = []
        self.memberships = {
            "SH603301": [("2025-01-01", "2025-06-29"), ("2026-06-30", "2026-09-30")],
            "SH688066": [("2025-01-01", "2025-06-29")],
            "SZ002001": [("2025-01-01", "2026-09-30")],
            "SH123456": [("2027-01-01", "2027-12-31")],
        }

    def instruments(self, market):
        if market != "csi1000":
            raise AssertionError("unexpected market")
        return {"market": market}

    def list_instruments(self, universe, *, start_time, end_time, freq, as_list):
        self.calls.append((universe, start_time, end_time, freq, as_list))
        if not as_list:
            raise AssertionError("must return a static list, not point-in-time intervals")
        return [
            code for code, spans in self.memberships.items()
            if any(a <= end_time and b >= start_time for a, b in spans)
        ] + ["SH603301"]  # fake provider duplicates must not leak into quote list


class F1QuoteUniverseTests(unittest.TestCase):
    def test_removed_stocks_keep_quotes_after_index_exit(self):
        provider = FakeInstrumentProvider()
        codes = execution_quote_codes(
            "csi1000", "2025-01-02", "2026-09-30", provider=provider
        )
        self.assertEqual(codes, ["SH603301", "SH688066", "SZ002001"])
        self.assertEqual(
            provider.calls,
            [({"market": "csi1000"}, "2025-01-02", "2026-09-30", "day", True)],
        )
        self.assertNotIn("SH123456", codes)
        # SH688066 does not rejoin, yet a pre-exit holding must still be
        # queryable for valuation and selling on 2025-07-02.
        self.assertIn("SH688066", codes)

    def test_rejects_invalid_inputs_without_qlib(self):
        provider = FakeInstrumentProvider()
        for market, start, end in [
            ("", "2025-01-02", "2026-09-30"),
            ("csi1000", "", "2026-09-30"),
            ("csi1000", "2026-09-30", "2025-01-02"),
        ]:
            with self.subTest(market=market, start=start, end=end):
                with self.assertRaises(ValueError):
                    execution_quote_codes(market, start, end, provider=provider)

    def test_rejects_empty_universe_instead_of_falling_back_to_all(self):
        class EmptyProvider(FakeInstrumentProvider):
            def list_instruments(self, *args, **kwargs):
                return []
        with self.assertRaisesRegex(ValueError, "invalid execution quote universe"):
            execution_quote_codes("csi1000", "2025-01-02", "2026-09-30", provider=EmptyProvider())


if __name__ == "__main__":
    unittest.main()
