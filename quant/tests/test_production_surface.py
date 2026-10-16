"""Guards for the public production surface.

ChiNext research code/history is intentionally retained, but scheduled
production and the public daily-signal UI are paused until a new model passes
an explicit production gate.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _daily_cron_source() -> str:
    src = (ROOT / "quant/code/production/modal_qlib_cn_a10g.py").read_text()
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
    app = (ROOT / "quant" / "website" / "app.js").read_text()
    # The page may explain that ChiNext is paused, but must not expose a
    # selectable pool, ChiNext artifact path, or legacy query-route contract.
    assert "_chinext" not in app
    assert "?pool=chinext" not in app.lower()
    assert "market=\"chinext\"" not in app.lower()
    assert "csi1000" in app.lower()
    assert "中证1000" in app


def test_methodology_marks_chinext_production_paused():
    page = (ROOT / "quant" / "website" / "methodology.html").read_text()
    app = (ROOT / "quant" / "website" / "app.js").read_text()
    assert "研究支线 · 创业板" not in page
    assert "创业板旧模型" not in page
    assert "创业板旧模型" not in app
    assert "双账本" not in page
    assert "双账本" not in app
    assert "T+1 开盘前生成 → T+1 开盘调仓" in app
    assert "定型主策略 · 参数已锁定" in page


def test_historical_chinext_files_are_not_deleted_by_runtime():
    cron = _daily_cron_source()
    # Runtime may stop writing new ChiNext files, but it must not contain any
    # deletion/migration path targeting historical signal artifacts.
    assert "delete_file" not in cron
    assert "unlink(" not in cron


def test_methodology_tracks_current_frozen_stage_b_contract():
    page = (ROOT / "quant" / "website" / "methodology.html").read_text()
    for marker in (
        "2025-01-02",
        "2026-09-30",
        "研究账本口径",
        "不因事后局部行情波动回溯修改参数或重选模型",
        "2026-09-18",
        "2026-10-12",
        "2026-10-15",
        "1000 只成分股",
        "涨跌停与 ST 规则",
        "自动化数据校验与免责声明",
    ):
        assert marker in page, f"methodology missing current marker: {marker}"


def test_public_site_uses_shared_stylesheet():
    index = (ROOT / "quant" / "website" / "index.html").read_text()
    methodology = (ROOT / "quant" / "website" / "methodology.html").read_text()
    styles = ROOT / "quant" / "website" / "styles.css"
    assert styles.is_file()
    assert 'href="styles.css?v=14"' in index
    assert 'href="styles.css?v=14"' in methodology


def test_dashboard_dark_theme_and_responsive_layout():
    styles = (ROOT / "quant" / "website" / "styles.css").read_text()
    index = (ROOT / "quant" / "website" / "index.html").read_text()
    assert "--background: #0c121b" in styles
    assert "--surface: #151e2b" in styles
    assert "html { color-scheme: dark; }" in styles
    assert "@media (max-width: 700px)" in styles
    assert ".table-wrap { display: none; }" in styles
    assert ".mobile-cards { display: grid;" in styles
    assert ".doc-grid { grid-template-columns: 1fr;" in styles
    assert 'src="app.js?v=33"' in index


def test_forward_performance_panel_is_forward_only_from_20261012():
    app = (ROOT / "quant" / "website" / "app.js").read_text()
    methodology = (ROOT / "quant" / "website" / "methodology.html").read_text()
    styles = (ROOT / "quant" / "website" / "styles.css").read_text()
    assert "csi1000_forward_performance.json" in app
    assert "2026-10-12" in app
    assert "策略累计收益" in app
    assert "performance-panel" in app
    assert ".performance-chart" in styles
    assert "2026-10-12" in methodology
    assert "2026-10-15" in methodology
    assert "历史回测收益拼接或回填" in methodology


def test_pages_builds_forward_performance_artifact():
    workflow = (ROOT / ".github" / "workflows" / "website-deploy.yml").read_text()
    assert "quant/code/ops/build_forward_performance.py" in workflow
    assert "--output _site/signals/csi1000_forward_performance.json" in workflow
    assert "test -s _site/signals/csi1000_forward_performance.json" in workflow
    assert "quant/website/build_index.py" in workflow
    assert "test -s _site/signals/available_dates.json" in workflow


def test_missing_history_metadata_is_not_fabricated():
    app = (ROOT / "quant" / "website" / "app.js").read_text()
    assert "formatDate(paper?.signal_data_date || null)" in app
    assert "_mvp_metadata.json" in app
    assert "evaluateMvpContract" in app
    assert "MVP metadata sidecar missing" in app
    assert "不会把旧版本标识单独解释为“已核验”" in app
    assert "paper.paper_lineage === 'stage_b_winner_canonical'" in app


def test_dashboard_exposes_forward_mvp_contract_without_tradeability_claim():
    app = (ROOT / "quant" / "website" / "app.js").read_text()
    styles = (ROOT / "quant" / "website" / "styles.css").read_text()
    for marker in (
        "每日数据校验 · 已通过",
        "成分股与样本校验",
        "防泄漏校验通过",
        "停牌待执行后确认",
        "risk.tradeability_certified !== false",
    ):
        assert marker in app
    assert ".status-grid.mvp-grid" in styles


def test_pages_deployment_packages_stylesheet():
    """GitHub Pages must publish CSS, not merely keep it in the source tree.

    A missing stylesheet makes both desktop tables and mobile cards visible
    and causes the browser to fall back to an unstyled white document.
    """
    workflow = (ROOT / ".github" / "workflows" / "website-deploy.yml").read_text()
    assert "cp quant/website/*.html quant/website/*.js quant/website/*.css quant/website/*.svg _site/" in workflow
    for filename in ("index.html", "methodology.html", "app.js", "styles.css", "favicon.svg"):
        assert f"test -s _site/{filename}" in workflow


def test_public_methodology_does_not_expose_internal_audit_identifiers():
    page = (ROOT / "quant" / "website" / "methodology.html").read_text()
    index = (ROOT / "quant" / "website" / "index.html").read_text()
    forbidden = (
        "stage_b_winner_canonical",
        "Stage-A",
        "Stage-B",
        "F1–F6",
        "L1–L4",
        "c98856b4",
        "PASS_RESEARCH_ACCOUNTING_ONLY",
        "Audit Closure",
        "confirmation tail",
        "metadata sidecar",
        "Winner candidate ID",
        "Model config SHA256",
        "Stage-B runtime snapshot",
        "GITHUB_TOKEN",
        "github-push",
        "modal secret",
    )
    for marker in forbidden:
        assert marker not in page, f"public methodology exposes internal marker: {marker}"
        assert marker not in index, f"public index exposes internal marker: {marker}"


def test_current_handoff_is_present_and_indexed():
    handoff = ROOT / "quant" / "docs" / "04-mvp-and-website" / "42-csi1000-mvp-handoff-after-ledger-pass-20261011.md"
    index = (ROOT / "quant" / "docs" / "README_HISTORICAL_INDEX.md").read_text()
    assert handoff.is_file()
    text = handoff.read_text()
    for marker in (
        "Audit Closure Register",
        "CANONICAL_PROFILE",
        "CANONICAL_PAPER_LINEAGE",
        "每日数据",
        "PIT",
        "Top20",
        "paper",
        "smoke",
    ):
        assert marker in text
    assert handoff.name in index


def test_sparkline_layout_is_centered_and_mobile_safe():
    app = (ROOT / "quant" / "website" / "app.js").read_text()
    styles = (ROOT / "quant" / "website" / "styles.css").read_text()
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
    app = (ROOT / "quant" / "website" / "app.js").read_text()
    assert "function calendarAlignChartValues(values, dates)" in app
    assert "Array(count - values.length).fill(null).concat(values)" in app
    assert "calendarAlignChartValues(" in app


def test_favicon_is_valid_svg_and_published_on_both_pages():
    from xml.etree import ElementTree

    asset = ROOT / "quant" / "website" / "favicon.svg"
    assert asset.is_file()
    svg = ElementTree.fromstring(asset.read_text())
    assert svg.tag == "{http://www.w3.org/2000/svg}svg"
    assert svg.attrib.get("viewBox") == "0 0 64 64"

    for name in ("index.html", "methodology.html"):
        html = (ROOT / "quant" / "website" / name).read_text()
        assert '<link rel="icon" type="image/svg+xml" href="favicon.svg?v=1">' in html

    workflow = (ROOT / ".github" / "workflows" / "website-deploy.yml").read_text()
    assert "website/*.svg _site/" in workflow
    assert "test -s _site/favicon.svg" in workflow


def test_st_free_reference_return_is_explicit_not_certified():
    app = (ROOT / "quant" / "website" / "app.js").read_text()
    method = (ROOT / "quant" / "website" / "methodology.html").read_text()
    for phrase in ("策略累计收益 · 模拟盘参考",
                   "开盘前自动核验 ST 快照（仅当外部接口连接故障时降级为不考虑 ST 的参考计算）",
                   "ST_IGNORED_REFERENCE_ONLY"):
        assert phrase in app, f"site missing reference-only disclosure: {phrase}"
    assert "外部接口连接故障时降级为不考虑 ST" in method
    assert "研究型参考收益" in method


def test_centered_boot_loader_and_consolidated_bottom_governance_card():
    index = (ROOT / "quant" / "website" / "index.html").read_text()
    app = (ROOT / "quant" / "website" / "app.js").read_text()
    styles = (ROOT / "quant" / "website" / "styles.css").read_text()
    method = (ROOT / "quant" / "website" / "methodology.html").read_text()

    # Centered boot loader in index.html + styles.css
    assert 'class="boot-screen"' in index
    assert 'class="boot-rings"' in index
    assert ".boot-screen {" in styles
    assert "place-items: center;" in styles

    # Governance & ST disclosures consolidated at the bottom after #performance
    assert app.index('id="ranking"') < app.index('id="performance"') < app.index('id="governance-notes"')
    assert app.index('id="performance"') < app.index('id="stat-pit"')
    assert app.index('id="performance"') < app.index('id="mvp-health"')
    assert 'id="stat-risk"' not in app

    # Methodology includes 5-phase audited metrics for c98856b4 Top10/Drop1
    for metric in ("+52.45%", "1.757", "1.464", "-20.50%", "+49.04%"):
        assert metric in method, f"methodology missing 5-phase metric: {metric}"

    # Pool switcher supports CSI1000, CSI500, and CSI300, defaults to CSI300 and persists selection
    for pool in ("csi1000", "csi500", "csi300", "中证1000", "中证500", "沪深300"):
        assert pool in app
    assert "return POOLS.csi300;" in app
    assert "localStorage.getItem(POOL_STORAGE_KEY)" in app
    assert "localStorage.setItem(POOL_STORAGE_KEY, poolKey)" in app
    assert ".pool-switcher {" in styles


