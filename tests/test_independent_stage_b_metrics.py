"""Synthetic unit tests; never claim to validate actual frozen results."""
from datetime import date
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import pandas as pd

MODULE = Path(__file__).resolve().parents[1] / "audit" / "independent_stage_b_metrics.py"
spec = importlib.util.spec_from_file_location("audit_metrics", MODULE)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class IndependentMetricsTests(unittest.TestCase):
    def setUp(self):
        self.dates = [date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)]
        self.gross = [0.01, -0.02, 0.015]
        self.cost = [0.001, 0.0005, 0.001]
        nav = 100_000_000.0
        self.account = []
        for r, c in zip(self.gross, self.cost):
            nav *= 1 + r - c
            self.account.append(nav)
        self.values = dict(account=self.account, return_=self.gross, cost=self.cost,
                           bench=[0.005, -0.01, 0.02], turnover=[0.1, 0.2, 0.1],
                           total_turnover=[0.1, 0.2, 0.1], total_cost=[10, 20, 30],
                           cash=[0, 0, 0], value=self.account)
        self.values['return'] = self.values.pop('return_')

    def test_account_reconciles_including_first_day(self):
        numbers, sensitivity, diagnostics, daily = audit.metrics(self.dates, self.values)
        self.assertEqual(numbers['n_days'], 3)
        self.assertLess(diagnostics['max_account_return_delta'], 1e-14)
        self.assertAlmostEqual(numbers['strategy_total_return'], self.account[-1] / 100_000_000 - 1, places=14)
        self.assertAlmostEqual(numbers['total_cost_sum'], sum(self.cost), places=14)
        self.assertAlmostEqual(sensitivity['sharpe_252_rf0_ddof1'] / sensitivity['sharpe_238_rf0_ddof1'], (252/238)**0.5, places=13)
        self.assertEqual(len(daily), 3)

    def test_independent_account_mismatch_detected(self):
        self.values['account'][-1] += 100_000
        result, _, diagnostics, _ = audit.metrics(self.dates, self.values)
        self.assertGreater(diagnostics['max_account_return_delta'], 1e-4)

    def test_byte_hash_and_calendar_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            dest = root / 'phases' / 'sample' / 'phase00' / 'report.parquet'
            dest.parent.mkdir(parents=True)
            dest.write_bytes(b'controlled raw fake file')
            frame = pd.DataFrame(self.values, index=pd.DatetimeIndex(self.dates))
            numbers, _, _, _ = audit.metrics(self.dates, self.values)
            fields = {key: round(value, 8 if key.startswith('mean_daily') else 6)
                      if isinstance(value, float) else value for key, value in numbers.items()}
            fields.pop('years')
            item = {'cohort': 'winner', 'phase': 0, 'expected_report': {
                'path': '/vol/csi1000_stage_b/snapshot/phases/sample/phase00/report.parquet',
                'sha256': audit.file_sha256(dest), 'columns': list(frame.columns), 'rows': 3},
                'reported_metrics': fields}
            with patch.object(pd, 'read_parquet', return_value=frame):
                check = audit.audit_one(item, 'snapshot', root, self.dates, root)
            self.assertEqual(check['metric_formula'], 'PASS', check)
            self.assertEqual(check['daily_account'], 'INCONCLUSIVE')
            with patch.object(pd, 'read_parquet', return_value=frame):
                check_no_cal = audit.audit_one(item, 'snapshot', root, None, root)
            self.assertEqual(check_no_cal['metric_formula'], 'BLOCKED')
            dest.write_bytes(b'tampered')
            check_bad_hash = audit.audit_one(item, 'snapshot', root, self.dates, root)
            self.assertEqual(check_bad_hash['metric_formula'], 'FAIL')


if __name__ == '__main__':
    unittest.main()
