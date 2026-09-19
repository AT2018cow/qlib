import copy
import unittest

from qlib_live_retrain import configure_asof, should_retrain, cache_signature


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

    def test_cache_signature_stable_dates_not_parameters(self):
        configure_asof(self.cfg, self.cal, self.cal[-1])
        initial = cache_signature(self.cfg)
        cfg2 = copy.deepcopy(self.cfg)
        cfg2['task']['dataset']['kwargs']['handler']['kwargs']['end_time'] = '2026-01-01'
        self.assertEqual(initial, cache_signature(cfg2))
        cfg2['task']['model']['kwargs']['learning_rate'] = 0.05
        self.assertNotEqual(initial, cache_signature(cfg2))

    def test_fail_closed_bad_asof(self):
        self.assertRaises(ValueError, configure_asof, self.cfg, self.cal, self.cal[-2])


if __name__ == '__main__':
    unittest.main()
