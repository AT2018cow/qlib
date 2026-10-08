"""Chromium screenshot and geometry QA of checked-out website assets.

Only generated fixture signals are used. This is NOT an audit of the live
GitHub Pages URL or production paper account.
"""
from __future__ import annotations

import csv
import json
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "website-visual-qa"


def business_dates(count: int, latest):
    dates = []
    day = latest
    while len(dates) < count:
        if day.weekday() < 5:
            dates.append(day)
        day -= timedelta(days=1)
    return list(reversed(dates))


def make_fixture(root: Path):
    shutil.copytree(ROOT / "website", root, dirs_exist_ok=True)
    signals = root / "signals"
    signals.mkdir(exist_ok=True)
    latest = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    while latest.weekday() >= 5:
        latest -= timedelta(days=1)
    days = business_dates(60, latest)
    codes = ["SH688146", "SH605376", "SZ301175"] + ["SH60" + str(i).zfill(4) for i in range(17)]
    stocks = {}
    for i, code in enumerate(codes):
        prices = [round(8 + i * 0.18 + j * (0.024 if i % 2 else -0.014), 3) for j in range(60)]
        if i == 0:
            prices[24:27] = [None, None, None]
        stocks[code] = prices
    chart = {"dates": [d.isoformat() for d in days], "stocks": stocks}
    with (signals / "code_name_map.csv").open("w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["code", "name"])
        for i, code in enumerate(codes):
            writer.writerow([code[2:], ("测试样例股票" if i == 0 else "示例名称") + str(i + 1)])
    for offset, selected in enumerate((days[-1], days[-2])):
        key = selected.isoformat()
        with (signals / (key + "_top20_lgb158.csv")).open("w", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["rank", "instrument", "score"])
            for i, code in enumerate(codes):
                writer.writerow([i + 1, code, round(0.15 - i * 0.004, 6)])
        (signals / (key + "_chart.json")).write_text(json.dumps(chart))
        paper = {
            "signal_data_date": days[-2 - offset].isoformat(),
            "model_fit_asof": days[-2 - offset].isoformat(),
        }
        if offset == 0:
            paper.update({
                "paper_lineage": "stage_b_winner_canonical",
                "production_lineage": {"profile": "stage_b_winner"},
            })
        (signals / (key + "_paper_portfolio.json")).write_text(json.dumps(paper))
    waiting = {
        "start_date": "2026-10-12",
        "status": "awaiting_first_valuation",
        "latest_date": None,
        "cumulative_return": None,
        "points": [],
    }
    performance_path = signals / "csi1000_forward_performance.json"
    performance_path.write_text(json.dumps(waiting))
    subprocess.run([
        sys.executable, str(ROOT / "website" / "build_index.py"),
        "--signals", str(signals),
        "--output", str(signals / "available_dates.json"),
    ], check=True, capture_output=True, text=True)
    return performance_path


def inspect_viewport(browser, base_url, width: int):
    page = browser.new_page(viewport={"width": width, "height": 900}, device_scale_factor=1)
    errors = []
    signals_requests = []
    statuses = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: signals_requests.append(request.url) if "/signals/" in request.url else None)
    page.on("response", lambda response: statuses.append((response.url, response.status)) if "/signals/" in response.url else None)
    page.goto(base_url + "/index.html", wait_until="domcontentloaded")
    page.locator("#signal-rows tr").first.wait_for(state="attached", timeout=20000)
    assert page.locator("#signal-rows tr").count() == 20
    page.wait_for_function("document.getElementById('stat-lineage')?.textContent === 'Stage-B winner'")
    assert len(signals_requests) == 6, signals_requests
    assert all(status == 200 for _, status in statuses), statuses
    assert not any("code_name_map.csv" in url for url in signals_requests)
    initial_requests = len(signals_requests)
    actual = page.evaluate("""() => ({
      overflow: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - window.innerWidth,
      table: getComputedStyle(document.querySelector('.table-wrap')).display,
      cards: getComputedStyle(document.querySelector('.mobile-cards')).display,
      background: getComputedStyle(document.body).backgroundColor,
      chartWidth: document.querySelector('.stock-card .sparkline-svg')?.getBoundingClientRect().width,
      dateRail: getComputedStyle(document.querySelector('.history-buttons')).overflowX,
    })""")
    assert actual["overflow"] <= 1, f"{width}px horizontal overflow: {actual}"
    assert actual["background"] == "rgb(12, 18, 27)", actual
    assert actual["dateRail"] == "auto", actual
    assert (actual["table"] == "none") == (width <= 860), actual
    assert (actual["cards"] == "grid") == (width <= 860), actual
    assert not errors, errors
    assert page.locator("#forward-return").inner_text() == "待开始"
    assert page.locator("#stat-lineage").inner_text() == "Stage-B winner"
    OUT.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(OUT / f"dashboard-fixture-awaiting-{width}.png"), full_page=True)
    page.locator(".date-btn").nth(1).click()
    page.wait_for_function("document.getElementById('stat-lineage')?.textContent === '历史 / 未核验'")
    after_history = len(signals_requests)
    assert after_history == initial_requests + 2, signals_requests
    if width in (390, 320):
        page.screenshot(path=str(OUT / f"dashboard-fixture-historical-{width}.png"), full_page=True)
    page.locator(".date-btn").first.click()
    page.wait_for_function("document.getElementById('stat-lineage')?.textContent === 'Stage-B winner'")
    assert len(signals_requests) == after_history, "previously loaded context should be cached"
    page.close()
    return {**actual, "first_load_data_requests": initial_requests, "after_history_data_requests": after_history, "data_404s": 0}


def main():
    with TemporaryDirectory() as temp:
        root = Path(temp) / "site"
        performance_path = make_fixture(root)
        handler = partial(SimpleHTTPRequestHandler, directory=str(root))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = "http://127.0.0.1:" + str(server.server_port)
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                for width in (1440, 1024, 768, 390, 320):
                    actual = inspect_viewport(browser, url, width)
                    print(f"PASS {width}px: {actual}")
                # A slow chart must not hold the first real Top20 off screen.
                page = browser.new_page(viewport={"width": 390, "height": 844})
                page.add_init_script("""
                    const nativeFetch = window.fetch.bind(window);
                    window.fetch = (url, opts) => String(url).includes('_chart.json')
                      ? new Promise(resolve => setTimeout(() => resolve(nativeFetch(url, opts)), 1200))
                      : nativeFetch(url, opts);
                """)
                page.goto(url + "/index.html", wait_until="domcontentloaded")
                page.locator("#signal-rows tr").first.wait_for(state="attached")
                assert page.locator("#stat-lineage").inner_text() == "核验中"
                page.wait_for_function("document.getElementById('stat-lineage')?.textContent === 'Stage-B winner'")
                page.close()
                print("PASS delayed chart: Top20 visible before lineage/chart response")

                # A separate synthetic active-series example covers chart layout.
                active = {
                    "start_date": "2026-10-12",
                    "status": "active",
                    "latest_date": "2026-10-13",
                    "cumulative_return": -0.012,
                    "points": [
                        {"date": "2026-10-12", "nav": 1.02, "daily_return": 0.02, "cumulative_return": 0.02},
                        {"date": "2026-10-13", "nav": 0.988, "daily_return": 0.988 / 1.02 - 1, "cumulative_return": -0.012},
                    ],
                }
                performance_path.write_text(json.dumps(active))
                page = browser.new_page(viewport={"width": 390, "height": 844})
                page.goto(url + "/index.html")
                page.locator("#signal-rows tr").first.wait_for(state="attached")
                assert page.locator("#forward-return").inner_text() == "-1.20%"
                assert page.locator(".performance-svg").count() == 1
                line_fill = page.locator(".performance-line").evaluate(
                    "(el) => getComputedStyle(el).fill"
                )
                assert line_fill == "none", f"Performance curve must not render as filled polygon: {line_fill}"
                page.screenshot(path=str(OUT / "dashboard-fixture-active-390.png"), full_page=True)
                page.close()
                for width in (1440, 320):
                    page = browser.new_page(viewport={"width": width, "height": 900})
                    page.goto(url + "/methodology.html")
                    overflow = page.evaluate("document.documentElement.scrollWidth - innerWidth")
                    assert overflow <= 1, f"Methodology overflow at {width}px: {overflow}"
                    page.screenshot(path=str(OUT / f"methodology-{width}.png"), full_page=True)
                    page.close()
                browser.close()
        finally:
            server.shutdown()
            thread.join(timeout=10)


if __name__ == "__main__":
    main()
