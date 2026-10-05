import copy
import unittest

from qlib_live_retrain import (configure_asof, should_retrain, cache_signature,
                               retrain_due_calendar, RETRAIN_ORIGIN,
                               provider_training_fingerprint)


class LiveRetrainTests(unittest.TestCase):
    def setUp(self):
        import datetime
        start = datetime.date(2014, 1, 1)
        self.cal = [(start + datetime.timedelta(days=i)).isoformat() for i in range(4700)]
        self.cfg = {'task': {'model': {'class': 'LGBModel', 'kwargs': {'learning_rate': 0.1}},
                             'dataset': {'kwargs': {
                                 'handler': {'class': 'Alpha158', 'module_path': 'handler',
                                             'kwargs': {'start_time': '2015-01-01',
                                                        'fit_start_time': '2016-01-01',
                                                        'fit_end_time': '2024-12-31',
                                                        'end_time': self.cal[-1],
                                                        'instruments': 'csi1000',
                                                        'label': ['Ref($close, -20)/$close - 1']}},
                                 'segments': {'train': ['2016-01-01', '2024-12-31'],
                                              'valid': ['2025-01-01', '2025-12-31'],
                                              'test': ['2026-01-01', self.cal[-1]]}}}}}

    def test_asof_uses_rolling_matured_labels(self):
        a = configure_asof(self.cfg, self.cal, self.cal[-1])
        idx = {date: i for i, date in enumerate(self.cal)}
        self.assertLess(idx[a['train_end']] + 20, idx[a['valid_start']])
        self.assertLess(idx[a['valid_end']] + 20, idx[a['asof']])
        self.assertEqual(self.cfg['task']['dataset']['kwargs']['segments']['test'], [a['asof'], a['asof']])
        self.assertEqual(self.cfg['task']['dataset']['kwargs']['handler']['kwargs']['fit_end_time'], a['train_end'])

    def test_asof_moves_forward_with_calendar(self):
        before = configure_asof(copy.deepcopy(self.cfg), self.cal[:-30], self.cal[-31])
        after = configure_asof(copy.deepcopy(self.cfg), self.cal, self.cal[-1])
        self.assertGreater(after['train_end'], before['train_end'])
        self.assertGreater(after['valid_end'], before['valid_end'])

    def test_retraining_every_20_sessions(self):
        self.assertTrue(should_retrain(self.cal, self.cal[-1], None))
        self.assertFalse(should_retrain(self.cal, self.cal[-1], self.cal[-20]))
        self.assertTrue(should_retrain(self.cal, self.cal[-1], self.cal[-21]))
        self.assertRaises(ValueError, should_retrain, self.cal, self.cal[-1], '2099-01-01')

    def test_global_due_anchor_and_cadence(self):
        # origin 自身 → due；+20 session → due；+10 → 非 due
        i0 = self.cal.index(RETRAIN_ORIGIN)
        self.assertTrue(retrain_due_calendar(self.cal, RETRAIN_ORIGIN))
        self.assertTrue(retrain_due_calendar(self.cal, self.cal[i0 + 20]))
        self.assertFalse(retrain_due_calendar(self.cal, self.cal[i0 + 10]))
        # 锚点之前的谱系不由全局时钟管（回放期自行 bootstrap）
        self.assertFalse(retrain_due_calendar(self.cal, self.cal[i0 - 5]))
        # 两池同历 → 同 due 日（本函数纯日历推导，无池参数——这就是同步机制本身）
        self.assertTrue(retrain_due_calendar(self.cal, self.cal[i0 + 40]))
        # fail-closed：bar / origin 不在日历
        self.assertRaises(ValueError, retrain_due_calendar, self.cal, "2099-01-01")
        self.assertRaises(ValueError, retrain_due_calendar, self.cal, self.cal[-1], origin="2099-01-01")

    def test_cache_signature_stable_dates_not_parameters(self):
        configure_asof(self.cfg, self.cal, self.cal[-1])
        initial = cache_signature(self.cfg)
        cfg2 = copy.deepcopy(self.cfg)
        cfg2['task']['dataset']['kwargs']['handler']['kwargs']['end_time'] = '2026-01-01'
        self.assertEqual(initial, cache_signature(cfg2))
        cfg2['task']['model']['kwargs']['learning_rate'] = 0.05
        self.assertNotEqual(initial, cache_signature(cfg2))
        self.assertNotEqual(
            cache_signature(self.cfg, runtime_lineage={"pyqlib": "a"}),
            cache_signature(self.cfg, runtime_lineage={"pyqlib": "b"}),
        )


    def test_provider_fingerprint_ignores_new_bars_but_detects_history_revision(self):
        import struct
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "calendars").mkdir()
            (root / "instruments").mkdir()
            (root / "features" / "sh600001").mkdir(parents=True)
            cal = ["2026-01-05", "2026-01-06", "2026-01-07"]
            (root / "calendars" / "day.txt").write_text("\n".join(cal))
            (root / "instruments" / "csi1000.txt").write_text(
                "SH600001\t2026-01-05\t2026-01-07\n"
            )

            def write_field(field, values):
                payload = struct.pack("<f", 0.0) + b"".join(struct.pack("<f", float(v)) for v in values)
                (root / "features" / "sh600001" / f"{field}.day.bin").write_bytes(payload)

            for field in ("open", "high", "low", "close", "volume", "factor"):
                write_field(field, [1.0, 2.0, 3.0])

            fp1 = provider_training_fingerprint(root, "csi1000", "2026-01-06")

            # Appending a normal new bar and extending an open membership span
            # must not change the prefix hash at the old fit cutoff.
            cal.append("2026-01-08")
            (root / "calendars" / "day.txt").write_text("\n".join(cal))
            (root / "instruments" / "csi1000.txt").write_text(
                "SH600001\t2026-01-05\t2026-01-08\n"
            )
            for field in ("open", "high", "low", "close", "volume", "factor"):
                write_field(field, [1.0, 2.0, 3.0, 4.0])
            fp2 = provider_training_fingerprint(root, "csi1000", "2026-01-06")
            self.assertEqual(fp1, fp2)

            # A value revision before the cached model's cutoff must invalidate.
            write_field("close", [1.0, 2.5, 3.0, 4.0])
            fp3 = provider_training_fingerprint(root, "csi1000", "2026-01-06")
            self.assertNotEqual(fp1, fp3)

    def test_provider_fingerprint_detects_membership_revision(self):
        import struct
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "calendars").mkdir()
            (root / "instruments").mkdir()
            (root / "features" / "sh600001").mkdir(parents=True)
            cal = ["2026-01-05", "2026-01-06", "2026-01-07"]
            (root / "calendars" / "day.txt").write_text("\n".join(cal))
            for field in ("open", "high", "low", "close", "volume", "factor"):
                (root / "features" / "sh600001" / f"{field}.day.bin").write_bytes(
                    struct.pack("<f", 0.0) + b"".join(struct.pack("<f", x) for x in (1.0, 2.0, 3.0))
                )
            inst = root / "instruments" / "csi1000.txt"
            inst.write_text("SH600001\t2026-01-05\t2026-01-07\n")
            a = provider_training_fingerprint(root, "csi1000", "2026-01-07")
            inst.write_text("SH600001\t2026-01-06\t2026-01-07\n")
            b = provider_training_fingerprint(root, "csi1000", "2026-01-07")
            self.assertNotEqual(a, b)


    def test_fail_closed_bad_asof(self):
        self.assertRaises(ValueError, configure_asof, self.cfg, self.cal, self.cal[-2])


if __name__ == '__main__':
    unittest.main()
