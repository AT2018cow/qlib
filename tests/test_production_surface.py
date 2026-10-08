"""Guards for the public production surface.

ChiNext research code/history is intentionally retained, but scheduled
production and the public daily-signal UI are paused until a new model passes
an explicit production gate.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _daily_cron_source() -> str:
    src = (ROOT / "modal_qlib_cn_a10g.py").read_text()
    start = src.index("def daily_cron():")
    end = src.index("# ===================== 第六步", start)
    return src[start:end]


def test_daily_cron_publishes_only_csi1000():
    cron = _daily_cron_source()
    assert 'market="csi1000"' in cron
    assert "csi1000_profile=CANONICAL_PROFILE" in cron
    assert 'market="chinext"' not in cron
    assert "res_chi" not in cron
    assert "_chinext" not in cron
    assert "stage_b_winner_canonical" not in cron  # lineage comes from frozen config


def test_chinext_hidden_from_public_signal_ui():
    app = (ROOT / "website" / "app.js").read_text()
    assert "chinext" not in app.lower()
    assert "创业板" not in app
    assert "csi1000" in app
    assert "中证1000" in app


def test_methodology_marks_chinext_production_paused():
    page = (ROOT / "website" / "methodology.html").read_text()
    assert "研究线 · 创业板" in page
    assert "production paused" in page
    assert "每日 production 更新与网页展示已暂停" in page
    assert "Stage-B rank #1" in page


def test_historical_chinext_files_are_not_deleted_by_runtime():
    cron = _daily_cron_source()
    # Runtime may stop writing new ChiNext files, but it must not contain any
    # deletion/migration path targeting historical signal artifacts.
    assert "delete_file" not in cron
    assert "unlink(" not in cron
