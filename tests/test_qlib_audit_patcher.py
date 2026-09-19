"""Patch rewrite is tested against a structural fixture; full repository integration is separate."""
import ast
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from apply_qlib_audit_fixes import patch


MAIN = '''from pathlib import Path

def _load_and_patch_cfg():
    from ruamel.yaml import YAML
    # 7) 基本面因子模式：Alpha158 + $roe 等 6 字段
    if rolling:
        cfg["port_analysis_config"]["strategy"] = {
            "kwargs": {"signal": "<PRED>", "topk": 50, "n_drop": 2, "rebalance_days": 20},
        }
    return cfg

def batch_c_window():
    seg["test"] = [te_s, te_e]

def p2_rolling():
    for w_idx, (tr_s, tr_e, va_s, va_e, te_s, te_e) in enumerate(ROLLING_WINDOWS, 1):
        pass
        seg["test"] = [te_s, te_e]
    full = pd.concat(report_parts).sort_index()

def tune_one():
    pred = model.predict(dataset)
    label_df = dataset.prepare("test", col_set=["feature", "label"], data_key=DataHandlerLP.DK_L)

def prepare_data():
    print(r.stderr[-2000:])
    vol.commit()

def debug_data():
    import qlib
    from qlib.data import D
    start = pd.Timestamp(end)
    import pandas as pd

    df = D.features(insts, cols)

def main():
    print(res_0916['matrix_0916'][k]['excess_with_cost_annual'])

def daily_standalone():
    top = day.sort_values(ascending=False).head(topk)
    return {"csv_content": csv_content,
            "csv_content": csv_content, "data_calendar_end": cal_lines[-1]}

def daily_cron():
    if res["date"] != res["data_calendar_end"]:
        print("[cron] 注：信号日期早于数据日历末日（节假日/数据延迟），照常入库留痕")
    sha = exist.json().get("sha") if exist.status_code == 200 else None
    if r.status_code in (200, 201):
        pass
    else:
        print(f"[cron] ❌ GitHub 推送失败: {r.status_code} {r.text[:200]}")

def p1_diagnostics():
    if True:
        def sim_weighted():
            f = fwd20.reindex(pd.MultiIndex.from_arrays([[d] * len(top), top.index],
                                                        names=["datetime", "instrument"])).dropna()
            gross = float((w * f).sum())
            turnover = float((w.reindex(prev_w.index).fillna(0) - prev_w.reindex(w.index).fillna(0)).abs().sum()) \\
                if len(prev_w) else 1.0
            return {"ann_excess_sim": round(ann, 4)}
'''
HEALTH = '''def check_data():
    if (
            check_large_step_changes_result is not None
            or check_large_step_changes_result is not None
    ):
        pass
'''


class PatcherTests(unittest.TestCase):
    def test_cli_check_apply_backup_on_fixture(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "scripts").mkdir()
            (root / "modal_qlib_cn_a10g.py").write_text(MAIN)
            (root / "scripts" / "check_data_health.py").write_text(HEALTH)
            package = Path(__file__).resolve().parents[1]
            for name in ("apply_qlib_audit_fixes.py", "qlib_audit_fixes.py"):
                (root / name).write_bytes((package / name).read_bytes())
            def call(*args):
                return subprocess.run([sys.executable, "apply_qlib_audit_fixes.py", *args],
                                      cwd=d, capture_output=True, text=True)
            checked = call("--check")
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertEqual((root / "modal_qlib_cn_a10g.py").read_text(), MAIN)
            applied = call("--apply")
            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertEqual((root / "modal_qlib_cn_a10g.py.audit-prepatch.bak").read_text(), MAIN)
            self.assertEqual((root / "scripts" / "check_data_health.py.audit-prepatch.bak").read_text(), HEALTH)
            self.assertNotEqual(call("--apply").returncode, 0)

    def test_patch_structural_fixture(self):
        new_source, new_health = patch(MAIN, HEALTH)
        ast.parse(new_source)
        ast.parse(new_health)
        self.assertIn('purge_cfg_splits(cfg, read_trading_calendar(DATA_DIR), horizon=20)', new_source)
        self.assertIn('pred = model.predict(dataset, segment="valid")', new_source)
        self.assertNotIn('enumerate(ROLLING_WINDOWS, 1)', new_source)
        self.assertIn('check_missing_data_result is not None', new_health)
        self.assertIn('"ranking_only": True', new_source)
        self.assertIn("f'{k}_ndrop3'", new_source)
        with self.assertRaises(RuntimeError):
            patch(new_source, new_health)


if __name__ == '__main__':
    unittest.main()
