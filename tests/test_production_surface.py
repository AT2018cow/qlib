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
    # The page may explain that ChiNext is paused, but must not expose a
    # selectable pool, ChiNext artifact path, or legacy query-route contract.
    assert "_chinext" not in app
    assert "?pool=chinext" not in app.lower()
    assert "market=\"chinext\"" not in app.lower()
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


def test_methodology_tracks_current_frozen_stage_b_contract():
    page = (ROOT / "website" / "methodology.html").read_text()
    for marker in (
        "2025-01-02",
        "2026-09-30",
        "424",
        "0 / 4 / 6 / 10 / 15",
        "+9.16%",
        "+14.25%",
        "5 / 5",
        "2026-09-18",
        "stage_b_winner_canonical",
        "4e908173705c76fee3782be37068672a3a845bd61894202f3152afe3b9d81ef2",
    ):
        assert marker in page, f"methodology missing current Stage-B marker: {marker}"


def test_public_site_uses_shared_stylesheet():
    index = (ROOT / "website" / "index.html").read_text()
    methodology = (ROOT / "website" / "methodology.html").read_text()
    styles = ROOT / "website" / "styles.css"
    assert styles.is_file()
    assert 'href="styles.css?v=1"' in index
    assert 'href="styles.css?v=1"' in methodology
