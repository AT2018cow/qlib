"""Regression coverage for the static CSI1000 Pages date/name manifest."""
import csv
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

from website.build_index import build_index

ROOT = Path(__file__).resolve().parents[1]


def write_signal(folder, day, codes):
    with (folder / (day + "_top20_lgb158.csv")).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["rank", "instrument", "score"])
        for rank, code in enumerate(codes, 1):
            writer.writerow([rank, code, 0.1])


def test_index_only_includes_published_csi1000_dates_and_required_names(tmp_path):
    with (tmp_path / "code_name_map.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["code", "name"])
        writer.writerows([["600001", "测试甲"], ["000002", "测试乙"], ["688001", "无关名称"]])
    write_signal(tmp_path, "2026-10-08", ["SH600001"])
    write_signal(tmp_path, "2026-09-30", ["SZ000002"])
    write_signal(tmp_path, "2026-09-24", ["SH600001"])
    (tmp_path / "2026-10-07_chinext_top20.csv").write_text("old pool\n")
    (tmp_path / "2026-10-06_chart.json").write_text("{}")
    (tmp_path / "2026-99-99_top20_lgb158.csv").write_text("invalid date\n")
    index = build_index(tmp_path)
    assert index == {
        "version": 1,
        "dates": ["2026-10-08", "2026-09-30", "2026-09-24"],
        "names": {"000002": "测试乙", "600001": "测试甲"},
    }
    output = tmp_path / "out" / "available_dates.json"
    subprocess.run([
        sys.executable, str(ROOT / "website" / "build_index.py"),
        "--signals", str(tmp_path), "--output", str(output),
    ], check=True, capture_output=True, text=True)
    assert json.loads(output.read_text()) == index


def test_index_caps_archive_to_24_buttons_plus_previous_comparison(tmp_path):
    day = date(2026, 10, 8)
    for _ in range(29):
        write_signal(tmp_path, day.isoformat(), ["SH600001"])
        day -= timedelta(days=1)
    index = build_index(tmp_path)
    assert len(index["dates"]) == 25
    assert index["dates"][0] == "2026-10-08"
    assert index["dates"][-1] == "2026-09-14"
    assert index["names"] == {}


def test_index_fails_closed_without_signals(tmp_path):
    output = tmp_path / "available_dates.json"
    result = subprocess.run([
        sys.executable, str(ROOT / "website" / "build_index.py"),
        "--signals", str(tmp_path), "--output", str(output),
    ], capture_output=True, text=True)
    assert result.returncode != 0
    assert not output.exists()
