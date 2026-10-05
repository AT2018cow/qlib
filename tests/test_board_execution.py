import unittest

import numpy as np

from board_execution import _board_quick, board_thresholds, compute_limit_masks, research_exchange


class BoardThresholdTests(unittest.TestCase):
    def test_thresholds_by_board_and_date(self):
        insts = ["SH600519", "SH688981", "SZ300750", "SZ301236", "SZ300680", "BJ430047", "BJ899050"]
        dates = ["2026-09-15", "2026-09-15", "2026-09-15", "2019-01-02", "2019-01-02", "2026-09-15", "2026-09-15"]
        thr = board_thresholds(insts, dates)
        self.assertEqual(thr[0], 0.095)   # 主板
        self.assertEqual(thr[1], 0.195)   # 科创板
        self.assertEqual(thr[2], 0.195)   # 改革后创业板
        self.assertEqual(thr[3], 0.095)   # 改革前创业板
        self.assertEqual(thr[4], 0.095)   # 改革前创业板
        self.assertTrue(np.isnan(thr[6])) # 指数无阈值
        self.assertEqual(thr[5], 0.295)   # 北交所股票（非指数）

    def test_masks(self):
        insts = ["SH600519", "SH688981", "SZ301236", "SZ300750"]
        dates = ["2026-09-15", "2026-09-15", "2019-01-02", "2020-08-24"]
        opens = [10.9, 10.0, 11.5, 10.0]
        prev = [10.0, 10.0, 10.0, 10.0]
        lb, ls = compute_limit_masks(insts, dates, opens, prev, [False] * 4)
        # 600519: +9% 未及 9.5% 涨停但 >5% 高开保护 → 买被阻
        self.assertTrue(lb[0]); self.assertFalse(ls[0])
        # 688981: 平开 → 可交易
        self.assertFalse(lb[1]); self.assertFalse(ls[1])
        # 改革前创业板 +15% ≥ 9.5% → 涨停买被阻；卖出不受影响
        self.assertTrue(lb[2]); self.assertFalse(ls[2])
        # 改革后创业板平开 → 可交易
        self.assertFalse(lb[3]); self.assertFalse(ls[3])

    def test_limit_down_blocks_sell(self):
        lb, ls = compute_limit_masks(["SZ300750"], ["2026-09-15"], [7.9], [10.0], [False])
        # -21% 越过 -19.5% 阈值 → 跌停卖被阻（-19.5% 恰在阈值边缘属模糊区，不用作测试点）
        self.assertFalse(lb[0]); self.assertTrue(ls[0])

    def test_high_open_applies_on_limit_exempt_days(self):
        # 新股豁免日：涨停不适用，但高开保护仍适用
        cal = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"]
        lb, ls = compute_limit_masks(
            ["SH688001"], ["2026-09-03"], [13.0], [10.0], [False],
            listing_dates={"SH688001": "2026-09-01"}, calendar=cal,
        )
        self.assertTrue(lb[0])   # +30% 被高开>5% 阻止（策略级规则，所有板块适用）
        self.assertFalse(ls[0])

    def test_nan_prev_close_no_block(self):
        lb, ls = compute_limit_masks(["SH600519"], ["2026-09-15"], [np.nan], [np.nan], [False])
        self.assertFalse(lb[0]); self.assertFalse(ls[0])

    def test_suspended_blocked_both(self):
        lb, ls = compute_limit_masks(["SH600519"], ["2026-09-15"], [10.0], [10.0], [True])
        self.assertTrue(lb[0]); self.assertTrue(ls[0])


class ResearchExchangeConfigTests(unittest.TestCase):
    def test_config_shape(self):
        cfg = research_exchange()
        self.assertEqual(cfg["deal_price"], "open")
        self.assertEqual(cfg["limit_threshold"], 0.095)
        self.assertIn("open_cost", cfg)


if __name__ == "__main__":
    unittest.main()


class ConsistencyWithBoardRulesTests(unittest.TestCase):
    def test_quick_matches_board_of(self):
        from board_rules import board_of

        samples = [
            "SH600519", "SH688981", "SH689009", "SZ300750", "SZ301236", "SZ302132",
            "SZ000001", "SZ002594", "SH000852", "SZ399006", "SH000688", "SH880324",
            "BJ430047", "BJ873533", "BJ899050",
        ]
        for s in samples:
            self.assertEqual(_board_quick(s), board_of(s), s)


if __name__ == "__main__":
    unittest.main()
