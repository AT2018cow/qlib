import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from board_rules import (
    CHINEXT_REFORM,
    EW_BENCH,
    POOL_BOARDS,
    MAIN_REGISTRATION_FIRST_LISTING,
    MAIN_ST_10_START,
    TH_10,
    TH_20,
    TH_30,
    TH_5,
    board_of,
    board_aware_limited,
    build_custom_instruments,
    ew_index_matrix,
    in_first_sessions,
    limit_threshold,
    star_chn_backtest_guard,
)


class BoardOfTests(unittest.TestCase):
    def test_board_matrix(self):
        cases = {
            "SH688981": "star",
            "SH689009": "star",
            "SH688001": "star",
            "SZ300750": "chinext",
            "SZ301236": "chinext",
            "SZ302132": "chinext",
            "SH600519": "main",
            "SZ000001": "main",
            "SZ002594": "main",
            "SH000852": "index",
            "SZ399006": "index",
            "SH000688": "index",
            "SH880324": "index",
            "BJ430047": "bse",
            "BJ873533": "bse",
            "BJ899050": "index",
        }
        for sym, b in cases.items():
            self.assertEqual(board_of(sym), b, sym)

    def test_malformed_symbols_fail_closed(self):
        for bad in ("600519.SH", "SH6005", "XX600519", "", "sh600519x"):
            with self.assertRaises(ValueError, msg=bad):
                board_of(bad)

    def test_pool_mappings(self):
        self.assertEqual(POOL_BOARDS["star_chn"], ("star", "chinext"))
        self.assertEqual(POOL_BOARDS["chinext"], ("chinext",))
        self.assertEqual(POOL_BOARDS["star"], ("star",))
        self.assertEqual(set(EW_BENCH), set(POOL_BOARDS))
        for bench in EW_BENCH.values():  # 合成基准必须判为 index，绝不混进股票池过滤
            self.assertEqual(board_of(bench), "index", bench)


class LimitThresholdTests(unittest.TestCase):
    def test_star_always_20(self):
        self.assertEqual(limit_threshold("SH688981", "2019-07-23"), TH_20)
        self.assertEqual(limit_threshold("SH688981", "2026-09-15"), TH_20)

    def test_chinext_reform_boundary(self):
        self.assertEqual(limit_threshold("SZ300750", "2020-08-21"), TH_10)
        self.assertEqual(limit_threshold("SZ300750", "2020-08-24"), TH_20)
        self.assertEqual(limit_threshold("SZ300750", "2026-09-15"), TH_20)

    def test_main_bse_index(self):
        self.assertEqual(limit_threshold("SH600519", "2026-09-15"), TH_10)
        self.assertEqual(limit_threshold("BJ430047", "2026-09-15"), TH_30)
        self.assertIsNone(limit_threshold("SZ399006", "2026-09-15"))
        self.assertIsNone(limit_threshold("SH000852", "2020-08-21"))

    def test_st_override_is_board_and_date_aware(self):
        # 创业板 ST：改革前 5%，改革后仍按创业板 20%。
        self.assertEqual(limit_threshold("SZ300750", "2020-08-21", st_symbols={"SZ300750"}), TH_5)
        self.assertEqual(limit_threshold("SZ300750", "2026-09-15", st_symbols={"SZ300750"}), TH_20)
        # 主板风险警示：2026-07-06 起从 5% 调整为 10%。
        self.assertEqual(limit_threshold("SH600001", "2026-07-03", st_symbols={"SH600001"}), TH_5)
        self.assertEqual(limit_threshold("SH600001", "2026-07-06", st_symbols={"SH600001"}), TH_10)
        self.assertEqual(MAIN_ST_10_START, "2026-07-06")

    CAL = [
        "2026-09-01",
        "2026-09-02",
        "2026-09-03",
        "2026-09-04",
        "2026-09-07",
        "2026-09-08",
        "2026-09-09",
        "2026-09-10",
    ]


    def test_main_registration_ipo_first_five_sessions_unlimited(self):
        cal = [
            "2023-04-10", "2023-04-11", "2023-04-12", "2023-04-13",
            "2023-04-14", "2023-04-17",
        ]
        listing = {"SZ001286": "2023-04-10"}
        for d in cal[:5]:
            self.assertIsNone(limit_threshold("SZ001286", d, listing_dates=listing, calendar=cal))
        self.assertEqual(limit_threshold("SZ001286", cal[5], listing_dates=listing, calendar=cal), TH_10)
        self.assertEqual(MAIN_REGISTRATION_FIRST_LISTING, "2023-04-10")

    def test_bse_only_listing_day_unlimited(self):
        cal = ["2026-01-05", "2026-01-06"]
        listing = {"BJ430047": "2026-01-05"}
        self.assertIsNone(limit_threshold("BJ430047", cal[0], listing_dates=listing, calendar=cal))
        self.assertEqual(limit_threshold("BJ430047", cal[1], listing_dates=listing, calendar=cal), TH_30)

    def test_new_listing_no_limit_with_calendar(self):
        listing = {"SH688001": "2026-09-01"}
        # sessions 1-4 (09-01..09-04): no limit
        for d in self.CAL[:4]:
            self.assertIsNone(limit_threshold("SH688001", d, listing_dates=listing, calendar=self.CAL), d)
        # session 6 (09-09) onward: 20%
        self.assertEqual(limit_threshold("SH688001", "2026-09-09", listing_dates=listing, calendar=self.CAL), TH_20)
        # before listing: regular rule applies
        self.assertEqual(limit_threshold("SH688001", "2026-08-31", listing_dates=listing, calendar=self.CAL), TH_20)

    def test_new_listing_approx_without_calendar(self):
        listing = {"SH688981": "2026-09-01"}
        self.assertIsNone(limit_threshold("SH688981", "2026-09-07", listing_dates=listing))
        self.assertEqual(limit_threshold("SH688981", "2026-09-09", listing_dates=listing), TH_20)

    def test_listing_not_in_calendar_fails_closed(self):
        listing = {"SH688001": "2026-09-13"}
        with self.assertRaises(ValueError):
            limit_threshold("SH688001", "2026-09-15", listing_dates=listing, calendar=self.CAL)

    def test_in_first_sessions(self):
        self.assertTrue(in_first_sessions("2026-09-01", "2026-09-03", calendar=self.CAL))
        self.assertFalse(in_first_sessions("2026-09-01", "2026-09-10", calendar=self.CAL))
        self.assertFalse(in_first_sessions("2026-09-01", "2026-08-31", calendar=self.CAL))
        self.assertTrue(in_first_sessions("2026-09-01", "2026-09-07"))
        self.assertFalse(in_first_sessions("2026-09-01", "2026-09-09"))


class BuildPoolTests(unittest.TestCase):
    ALL_TXT = (
        "SH600519\t2001-08-27\t2026-09-18\n"
        "SH688981\t2020-07-16\t2026-09-18\n"
        "SH688981\t2021-01-01\t2026-09-18\n"
        "SZ300750\t2018-06-11\t2026-09-18\n"
        "SZ301236\t2021-12-20\t2026-09-18\n"
        "SH000852\t2005-01-04\t2026-09-18\n"
        "BJ430047\t2021-11-15\t2026-09-18\n"
    )

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.d = Path(self._tmp.name)
        (self.d / "instruments").mkdir()
        (self.d / "instruments" / "all.txt").write_text(self.ALL_TXT)

    def tearDown(self):
        self._tmp.cleanup()

    def test_filter_preserves_spans_and_counts(self):
        out = self.d / "instruments" / "star_chn.txt"
        stats = build_custom_instruments(self.d / "instruments" / "all.txt", out)
        self.assertEqual(stats, {"star": 1, "chinext": 2, "dropped": 3})
        lines = out.read_text().splitlines()
        self.assertEqual(len(lines), 4)  # 1 star + 1 extra star span + 2 chinext
        self.assertIn("SH688981\t2021-01-01\t2026-09-18", lines)
        self.assertNotIn("SH000852", out.read_text())
        self.assertNotIn("BJ430047", out.read_text())

    def test_missing_source_fails_closed(self):
        with self.assertRaises(FileNotFoundError):
            build_custom_instruments("/no/such/all.txt", self.d / "x.txt")

    def test_malformed_row_fails_closed(self):
        (self.d / "instruments" / "all.txt").write_text("SH600519\t2001-08-27\n")
        with self.assertRaises(ValueError):
            build_custom_instruments(self.d / "instruments" / "all.txt", self.d / "x.txt")

    def test_empty_pool_fails_closed(self):
        (self.d / "instruments" / "all.txt").write_text("SH600519\t2001-08-27\t2026-09-18\n")
        with self.assertRaises(ValueError):
            build_custom_instruments(self.d / "instruments" / "all.txt", self.d / "x.txt")


class LimitedMaskTests(unittest.TestCase):
    def _series(self, data):
        inst, dt, ret = [], [], []
        for i, vals in data.items():
            for d, v in vals:
                inst.append(i)
                dt.append(pd.Timestamp(d))
                ret.append(v)
        return pd.Series(ret, index=pd.MultiIndex.from_arrays([inst, dt], names=["instrument", "datetime"]))

    DATE = "2026-09-15"

    def test_board_specific_thresholds(self):
        s = self._series(
            {
                "SH688981": [(self.DATE, 0.196)],  # 20% 板块：超阈剔除
                "SZ300750": [(self.DATE, 0.194)],  # 19.4% < 19.5%：保留（旧 0.095 会误剔）
                "SZ300960": [(self.DATE, -0.196)],  # 跌停同样剔除
                "SH600519": [(self.DATE, 0.10)],  # 主板 10%：剔除
                "SH600036": [(self.DATE, 0.09)],  # 主板：保留
            }
        )
        self.assertEqual(board_aware_limited(s, self.DATE), {"SH688981", "SZ300960", "SH600519"})

    def test_reform_date_split(self):
        s = self._series(
            {
                "SZ300750": [("2020-08-21", 0.096)],  # 改革前 10%：剔除
                "SZ300751": [("2020-08-24", 0.096)],  # 改革后 20%：保留
            }
        )
        self.assertEqual(board_aware_limited(s, "2020-08-21"), {"SZ300750"})
        self.assertEqual(board_aware_limited(s, "2020-08-24"), set())

    def test_new_listing_day_not_limited(self):
        cal = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"]
        s = self._series({"SH688001": [("2026-09-03", 0.30)]})
        self.assertEqual(
            board_aware_limited(s, "2026-09-03", listing_dates={"SH688001": "2026-09-01"}, calendar=cal), set()
        )

    def test_requires_series(self):
        with self.assertRaises(TypeError):
            board_aware_limited([0.2], self.DATE)

    def test_reform_date_constant(self):
        self.assertEqual(CHINEXT_REFORM, "2020-08-24")


class EwBenchFilesTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.d = Path(self._tmp.name)
        (self.d / "calendars").mkdir()
        (self.d / "instruments").mkdir()
        cal = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]
        (self.d / "calendars" / "day.txt").write_text("\n".join(cal))
        (self.d / "instruments" / "all.txt").write_text(
            "SZ300750\t2026-01-05\t2026-01-08\nSZ301236\t2026-01-06\t2026-01-08\nSH600519\t2026-01-05\t2026-01-08\n"
        )

        def write_bin(sym, header, vals):
            d = self.d / "features" / sym.lower()
            d.mkdir(parents=True, exist_ok=True)
            np.array([header] + list(vals), dtype="<f").tofile(d / "close.day.bin")

        # SZ300750: 全程 10→11→12→13；SZ301236: 01-06 起上市 4→4.4→4.4（缺首日）；SH600519 不在池内
        write_bin("SZ300750", 0, [10.0, 11.0, 12.0, 13.0])
        write_bin("SZ301236", 1, [4.0, 4.4, 4.4])
        write_bin("SH600519", 0, [100.0, 100.0, 100.0, 100.0])

    def tearDown(self):
        self._tmp.cleanup()

    def test_build_matches_ew_math(self):
        from board_rules import build_ew_bench_files, ew_index_matrix

        rep = build_ew_bench_files(self.d, "chinext")
        self.assertEqual(rep["bench"], "SZ399998")
        self.assertEqual(rep["n_symbols"], 2)
        self.assertEqual(rep["n_bins_missing"], 0)
        # 与纯数学核心对照（单一真源的两层一致性）
        M = np.full((2, 4), np.nan, dtype="<f")
        M[0] = [10.0, 11.0, 12.0, 13.0]
        M[1, 1:] = [4.0, 4.4, 4.4]
        mean_ret, idx_close = ew_index_matrix(M)
        out = np.fromfile(self.d / "features" / "sz399998" / "close.day.bin", dtype="<f")
        self.assertEqual(int(out[0]), 0)  # 全日历对齐 header
        self.assertTrue(np.allclose(out[1:], idx_close, rtol=1e-6, atol=1e-7))
        self.assertTrue(np.isclose(out[1], 1.0, rtol=1e-6, atol=1e-7))  # day0 无有效前收 → flat
        self.assertTrue(np.isclose(out[2], 1.10, rtol=1e-6, atol=1e-7))  # day1 仅 A +10%（B 无前收）
        self.assertTrue(out[3] > out[2])  # day2 起 A/B 双票有效
        # factor bin 全 1
        f = np.fromfile(self.d / "features" / "sz399998" / "factor.day.bin", dtype="<f")
        self.assertTrue((f[1:] == 1.0).all())
        # instruments 行
        self.assertEqual((self.d / "instruments" / "sz399998.txt").read_text(), "SZ399998\t2026-01-05\t2026-01-08\n")

    def test_missing_bin_counted_and_continue(self):
        from board_rules import build_ew_bench_files

        (self.d / "features" / "sz301236" / "close.day.bin").unlink()
        rep = build_ew_bench_files(self.d, "chinext")
        self.assertEqual(rep["n_bins_missing"], 1)
        self.assertEqual(rep["n_symbols"], 2)

    def test_bad_market_fails(self):
        from board_rules import build_ew_bench_files

        with self.assertRaises(ValueError):
            build_ew_bench_files(self.d, "csi1000")

    def test_missing_calendar_fails(self):
        from board_rules import build_ew_bench_files

        (self.d / "calendars" / "day.txt").unlink()
        with self.assertRaises(FileNotFoundError):
            build_ew_bench_files(self.d, "chinext")


class BacktestGuardTests(unittest.TestCase):
    def test_pre_reform_window_refused(self):
        with self.assertRaises(ValueError):
            star_chn_backtest_guard("2020-08-21")

    def test_reform_and_later_ok(self):
        self.assertIsNone(star_chn_backtest_guard("2020-08-24"))
        self.assertIsNone(star_chn_backtest_guard("2021-01-04"))


class EwIndexMatrixTests(unittest.TestCase):
    def test_equal_weight_math(self):
        # 5 日历日：A 上市第一天起，B 第三天起（含 NaN = 未上市/停牌）
        m = np.array(
            [
                [10.0, 11.0, 12.0, np.nan, np.nan],
                [np.nan, np.nan, 4.0, 4.4, 4.4],
            ],
            dtype="<f",
        )
        mean_ret, idx_close = ew_index_matrix(m)
        # day0 无前收 → NaN；day1 仅 A（+10%）；day2 仅 A（12/11-1）；day3 仅 B（4.4/4-1）；
        # day4 仅 B（0）——A 在 day3 NaN、B day2 无前收，都不计入
        self.assertTrue(np.isnan(mean_ret[0]))
        self.assertTrue(np.isclose(mean_ret[1], 0.10, rtol=1e-6, atol=1e-7))
        self.assertTrue(np.isclose(mean_ret[2], 12.0 / 11.0 - 1, rtol=1e-6, atol=1e-7))
        self.assertTrue(np.isclose(mean_ret[3], 0.10, rtol=1e-6, atol=1e-7))
        self.assertTrue(np.isclose(mean_ret[4], 0.0, rtol=1e-6, atol=1e-7))
        self.assertTrue(np.isclose(idx_close[0], 1.0, rtol=1e-6, atol=1e-7))
        self.assertTrue(np.isclose(idx_close[1], 1.10, rtol=1e-6, atol=1e-7))
        self.assertTrue(np.isclose(idx_close[3], 1.10 * (12.0 / 11.0) * 1.10, rtol=1e-6, atol=1e-7))
        self.assertTrue(np.isclose(idx_close[4], idx_close[3], rtol=1e-6, atol=1e-7))

    def test_suspended_day_excluded(self):
        # A 停牌（day2 NaN）→ A 在 day2（close NaN）与 day3（prev NaN）都不计入等权均值；
        # B 全程有数据：day1 0（持平）、day2 0.05、day3 0——等权按"有效前收股票"重算
        m = np.array(
            [
                [10.0, 11.0, np.nan, 13.0],
                [10.0, 10.0, 10.5, 10.5],
            ],
            dtype="<f",
        )
        mean_ret, idx_close = ew_index_matrix(m)
        self.assertTrue(np.isclose(mean_ret[1], 0.05, rtol=1e-6, atol=1e-7))
        self.assertTrue(np.isclose(mean_ret[2], 0.05, rtol=1e-6, atol=1e-7))
        self.assertTrue(np.isclose(mean_ret[3], 0.0, rtol=1e-6, atol=1e-7))
        self.assertTrue(np.isclose(idx_close[3], 1.05 * 1.05, rtol=1e-6, atol=1e-7))

    def test_flat_before_first_symbol(self):
        m = np.array(
            [
                [np.nan, np.nan, 5.0, 5.5],
            ],
            dtype="<f",
        )
        mean_ret, idx_close = ew_index_matrix(m)
        self.assertTrue(np.isnan(mean_ret[0]))
        self.assertTrue(np.isnan(mean_ret[1]))
        self.assertTrue(np.isclose(idx_close[0], 1.0, rtol=1e-6, atol=1e-7))
        self.assertTrue(np.isclose(idx_close[1], 1.0, rtol=1e-6, atol=1e-7))
        self.assertTrue(np.isclose(idx_close[2], 1.0, rtol=1e-6, atol=1e-7))
        self.assertTrue(np.isclose(idx_close[3], 1.10, rtol=1e-6, atol=1e-7))

    def test_fail_closed(self):
        with self.assertRaises(ValueError):
            ew_index_matrix(np.array([[np.nan, np.nan], [np.nan, np.nan]]))
        with self.assertRaises(ValueError):
            ew_index_matrix(np.array([1.0, 2.0]))
        with self.assertRaises(ValueError):
            ew_index_matrix(np.array([[1.0, 2.0]]).T)  # 1 day

    def test_single_symbol_flat(self):
        m = np.array([[100.0, 100.0, 100.0]], dtype="<f")
        mean_ret, idx_close = ew_index_matrix(m)
        self.assertTrue(np.isclose(mean_ret[1], 0.0, rtol=1e-6, atol=1e-7))
        self.assertTrue(np.isclose(idx_close[2], 1.0, rtol=1e-6, atol=1e-7))


if __name__ == "__main__":
    unittest.main()


class EwBenchSpanEnforcementTests(unittest.TestCase):
    """R26 Option A: 区间外价格不得贡献基准收益。"""

    def test_price_outside_span_ignored(self):
        import tempfile

        from board_rules import build_ew_bench_files

        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "calendars").mkdir()
            (p / "instruments").mkdir()
            cal = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"]
            (p / "calendars" / "day.txt").write_text("\n".join(cal))
            # 成员区间只有 01-06 起生效（01-05 的价格在区间外，必须被忽略）
            (p / "instruments" / "chinext.txt").write_text("SZ300001\t2026-01-06\t2026-01-09\n")
            d = p / "features" / "sz300001"
            d.mkdir(parents=True)
            np.array([0] + [10.0, 11.0, 12.0, 14.0, 20.0], dtype="<f").tofile(d / "close.day.bin")
            build_ew_bench_files(p, "chinext")
            out = np.fromfile(p / "features" / "sz399998" / "close.day.bin", dtype="<f")
            # day0 (01-05) 区间外 → 无有效收益 → flat 1.0
            # day1 (01-06) 成员首日：无前收（01-05 被屏蔽）→ flat
            # day2: 12/11-1 有效
            self.assertTrue(np.isclose(out[1], 1.0, atol=1e-7))
            self.assertTrue(np.isclose(out[2], 1.0, atol=1e-7))
            self.assertTrue(np.isclose(out[3], 12.0 / 11.0, rtol=1e-6))
            # 01-09 的 20.0 在区间内(day4) → 14/12-1? 检查: day3=13(12*?)…直接验证 day4 用 day3 值
            self.assertTrue(np.isclose(out[4], 14.0 / 11.0, rtol=1e-6))  # out[4]=day3=(12/11)*(14/12)
