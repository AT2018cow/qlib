"""Structural guard for the production Modal pipeline (modal_qlib_cn_a10g.py).

Born from the 2026-09-29 incident: a refactor edit silently deleted the
`schedule=modal.Cron(...)` line from the daily_cron decorator (oldString
contained it, newString omitted it). py_compile cannot detect semantic
deletions like that; nothing else would notice until the next morning's
cron silently never fires.

These tests pin every structural element that production depends on, so
any future edit that drops one fails fast in the test run instead of in
production. Run from repo root:  python -m pytest tests/test_modal_structure.py
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODAL_FILE = os.path.join(ROOT, "modal_qlib_cn_a10g.py")

src = open(MODAL_FILE).read()

# 最近一次成功 deploy（2026-09-29，含 schedule 修复）输出的函数清单
EXPECTED_FUNCTIONS = [
    "bench_years", "independent_recheck", "verify_integrity", "debug_data",
    "prepare_data", "check_gpu", "train", "train_cpu", "build_fund_factors",
    "daily_signal", "daily_signal_cpu", "train_ensemble", "tune_one",
    "tune_driver", "dual_horizon", "p0_diagnostics", "p1_diagnostics",
    "version_check_0911", "version_check_0916", "batch_c_window", "batch_c",
    "p2_rolling", "topk_grid", "batch_a", "batch_b", "daily_standalone",
    "daily_cron", "freq_window", "freq_driver", "backfill_signals",
]


def _decorator_block(func_name):
    """Return the @app.function(...) parameter text for a given function."""
    m = re.search(rf"@app\.function\((.*?)\)\ndef {func_name}\(", src, re.DOTALL)
    assert m is not None, f"function {func_name} not found or not decorated"
    return m.group(1)


def test_all_functions_defined():
    names = re.findall(r"@app\.function\(.*?\)\ndef (\w+)\(", src, re.DOTALL)
    missing = [f for f in EXPECTED_FUNCTIONS if f not in names]
    extra = [n for n in names if n not in EXPECTED_FUNCTIONS]
    assert not missing, f"functions silently deleted: {missing}"
    assert not extra, f"unexpected new functions (update EXPECTED_FUNCTIONS): {extra}"


def test_daily_cron_decorator_complete():
    params = _decorator_block("daily_cron")
    assert 'schedule=modal.Cron("0 7 * * 1-5", timezone="Asia/Shanghai")' in params, \
        "cron schedule line missing — next morning's run would silently never fire!"
    assert 'modal.Secret.from_name(_GH_SECRET_NAME := "github-push")' in params
    assert "nonpreemptible=True" in params
    assert "timeout=2 * 3600" in params


def test_production_functions_nonpreemptible():
    for fn in ("daily_standalone", "backfill_signals"):
        assert "nonpreemptible=True" in _decorator_block(fn), \
            f"{fn} lost nonpreemptible=True — preemption would drop that day's run"


def test_image_helper_modules_copied():
    assert '.add_local_dir(' in src, "image add_local_dir (repo -> /root/qlib) missing"
    assert 'pip install . --no-build-isolation --no-deps' in src, "qlib pip install step missing"
    cp = re.search(r'run_commands\("cp ([^"]+?)/root/"\)', src)
    assert cp is not None, "helper-module cp step missing"
    for helper in (
        "qlib_audit_fixes.py",
        "qlib_live_retrain.py",
        "github_commit.py",
        "csi1000_tuner_core.py",
        "csi1000_production_config.py",
        "signal_publication_gate.py",
    ):
        assert helper in cp.group(1), \
            f"{helper} not copied to /root/ — container import would fail (ModuleNotFoundError)"


def test_constants_intact():
    assert 'GITHUB_REPO = "AT2018cow/qlib"' in src
    assert 'SIGNAL_BRANCH = "main"' in src
    assert 'DATA_DIR = VOL_ROOT / "cn_data"' in src


def test_top_level_helper_imports_intact():
    assert "from qlib_audit_fixes import read_trading_calendar, purge_cfg_splits" in src
    assert "from qlib_live_retrain import (configure_asof, should_retrain, cache_signature" in src


def test_daily_cron_flow_order():
    """Decision (gate/staleness) must run BEFORE the immutable push; dedup and
    the atomic push_files call must be present."""
    i_decision = src.find("action, detail = _publication_decision(")
    i_dedup = src.find("# 同日重跑 dedup")
    i_push = src.find("from github_commit import push_files")
    assert -1 not in (i_decision, i_dedup, i_push), "daily_cron flow section missing"
    assert i_decision < i_dedup < i_push, "daily_cron flow order broken"


def test_stage_b_winner_is_single_canonical_csi1000_wiring():
    assert "csi1000_profile=CANONICAL_PROFILE" in src
    assert "csi1000_profile=BASELINE_PROFILE" not in src
    assert "stage_b_baseline_shadow" not in src
    assert "stage_b_winner_shadow" not in src
    assert "lineage=CANONICAL_PAPER_LINEAGE" in src
    assert "_top20_lgb158_stage_b_winner_shadow.csv" not in src
    assert "_paper_portfolio_stage_b_winner_shadow.json" not in src
    assert 'production_lineage = cfg.get("_csi1000_production_manifest")' in src
    assert "deterministic_score_order(day.items())" in src
    assert "def _csi1000_canonical_publication_decision" in src


def test_github_commit_flow_complete():
    gsrc = open(os.path.join(ROOT, "github_commit.py")).read()
    for step in ("git/ref/heads", "git/blobs", "git/trees", "git/commits", "git/refs/heads"):
        assert step in gsrc, f"Git Data API step missing: {step}"
