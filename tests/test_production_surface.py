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
    assert "csi1000" in app.lower()
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
        "治理与公开边界",
    ):
        assert marker in page, f"methodology missing current Stage-B marker: {marker}"


def test_public_site_uses_shared_stylesheet():
    index = (ROOT / "website" / "index.html").read_text()
    methodology = (ROOT / "website" / "methodology.html").read_text()
    styles = ROOT / "website" / "styles.css"
    assert styles.is_file()
    assert 'href="styles.css?v=7"' in index
    assert 'href="styles.css?v=7"' in methodology


def test_dashboard_dark_theme_and_responsive_layout():
    styles = (ROOT / "website" / "styles.css").read_text()
    index = (ROOT / "website" / "index.html").read_text()
    assert "--background: #0c121b" in styles
    assert "--surface: #151e2b" in styles
    assert "html { color-scheme: dark; }" in styles
    assert "@media (max-width: 700px)" in styles
    assert ".table-wrap { display: none; }" in styles
    assert ".mobile-cards { display: grid;" in styles
    assert ".doc-grid { grid-template-columns: 1fr;" in styles
    assert 'src="app.js?v=23"' in index


def test_forward_performance_panel_is_forward_only_from_20261012():
    app = (ROOT / "website" / "app.js").read_text()
    methodology = (ROOT / "website" / "methodology.html").read_text()
    styles = (ROOT / "website" / "styles.css").read_text()
    assert "csi1000_forward_performance.json" in app
    assert "2026-10-12" in app
    assert "模型累计收益" in app
    assert "performance-panel" in app
    assert ".performance-chart" in styles
    assert "2026-10-12" in methodology
    assert "不会把此前回测或历史 paper 收益回填" in methodology


def test_pages_builds_forward_performance_artifact():
    workflow = (ROOT / ".github" / "workflows" / "website-deploy.yml").read_text()
    assert "scripts/build_forward_performance.py" in workflow
    assert "--output _site/signals/csi1000_forward_performance.json" in workflow
    assert "test -s _site/signals/csi1000_forward_performance.json" in workflow
    assert "website/build_index.py" in workflow
    assert "test -s _site/signals/available_dates.json" in workflow


def test_missing_history_metadata_is_not_fabricated():
    app = (ROOT / "website" / "app.js").read_text()
    assert "formatDate(ctx.paper?.signal_data_date || null)" in app
    assert "isWinnerCanonical(ctx.paper)" in app
    assert "paper.paper_lineage === 'stage_b_winner_canonical'" in app


def test_pages_deployment_packages_stylesheet():
    """GitHub Pages must publish CSS, not merely keep it in the source tree.

    A missing stylesheet makes both desktop tables and mobile cards visible
    and causes the browser to fall back to an unstyled white document.
    """
    workflow = (ROOT / ".github" / "workflows" / "website-deploy.yml").read_text()
    assert "cp website/*.html website/*.js website/*.css website/*.svg _site/" in workflow
    for filename in ("index.html", "methodology.html", "app.js", "styles.css", "favicon.svg"):
        assert f"test -s _site/{filename}" in workflow


def test_public_methodology_does_not_expose_internal_audit_identifiers():
    page = (ROOT / "website" / "methodology.html").read_text()
    forbidden = (
        "stage_b_winner_canonical",
        "Winner candidate ID",
        "Model config SHA256",
        "Stage-B runtime snapshot",
        "GITHUB_TOKEN",
        "github-push",
        "modal secret",
    )
    for marker in forbidden:
        assert marker not in page, f"public methodology exposes internal marker: {marker}"


def test_current_handoff_is_present_and_indexed():
    handoff = ROOT / "docs" / "experiments" / "21-next-conversation-handoff-star-chinext-20261008.md"
    index = (ROOT / "docs" / "experiments" / "README.md").read_text()
    assert handoff.is_file()
    text = handoff.read_text()
    for marker in (
        "CSI1000 research selection is complete and frozen",
        "STAR reproducibility repair first",
        "ChiNext",
        "2025-01-02",
        "2026-09-30",
    ):
        assert marker in text
    assert handoff.name in index


def test_sparkline_layout_is_centered_and_mobile_safe():
    app = (ROOT / "website" / "app.js").read_text()
    styles = (ROOT / "website" / "styles.css").read_text()
    assert "function makeSparkline(values, w = 128, h = 34)" in app
    assert "Preserve null/suspension gaps" in app
    assert ".signal-table th:last-child, .signal-table td:last-child" in styles
    assert "width: 180px;" in styles
    assert ".sparkline-svg" in styles
    assert ".signal-table td.spark {\n  display: table-cell;" in styles
    assert ".mobile-cards .spark {\n  display: flex;" in styles
    assert ".stock-side .sparkline-svg" in styles
    assert "width: 104px;" in styles
    assert "width: 78px;" in styles


def test_legacy_chart_arrays_are_calendar_padded():
    app = (ROOT / "website" / "app.js").read_text()
    assert "function calendarAlignChartValues(values, dates)" in app
    assert "Array(count - values.length).fill(null).concat(values)" in app
    assert "calendarAlignChartValues(" in app


def test_favicon_is_valid_svg_and_published_on_both_pages():
    from xml.etree import ElementTree

    asset = ROOT / "website" / "favicon.svg"
    assert asset.is_file()
    svg = ElementTree.fromstring(asset.read_text())
    assert svg.tag == "{http://www.w3.org/2000/svg}svg"
    assert svg.attrib.get("viewBox") == "0 0 64 64"

    for name in ("index.html", "methodology.html"):
        html = (ROOT / "website" / name).read_text()
        assert '<link rel="icon" type="image/svg+xml" href="favicon.svg?v=1">' in html

    workflow = (ROOT / ".github" / "workflows" / "website-deploy.yml").read_text()
    assert "website/*.svg _site/" in workflow
    assert "test -s _site/favicon.svg" in workflow
