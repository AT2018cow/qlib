"""Build a small CSI1000-only date/name manifest for the static Pages dashboard.

Keeps history discovery offline instead of probing nonexistent calendar days
from every visitor's browser. Reads published artifacts, never modifies them.
"""
import argparse
import csv
import json
import re
from datetime import date
from pathlib import Path

SIGNAL_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})_top20_lgb158\.csv$")
VISIBLE_DATES = 24


def build_index(signals: Path) -> dict:
    dates = []
    for entry in signals.iterdir():
        match = SIGNAL_FILE.fullmatch(entry.name)
        if not match or not entry.is_file():
            continue
        try:
            date.fromisoformat(match.group(1))
        except ValueError:
            continue
        dates.append(match.group(1))
    dates.sort(reverse=True)
    # Keep one extra date to compare ranks on the oldest visible archive entry.
    dates = dates[: VISIBLE_DATES + 1]

    name_lookup = {}
    names_path = signals / "code_name_map.csv"
    if names_path.is_file():
        with names_path.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                if row.get("code") and row.get("name"):
                    name_lookup[row["code"]] = row["name"]

    names = {}
    for day in dates[:VISIBLE_DATES]:
        with (signals / (day + "_top20_lgb158.csv")).open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                instrument = row.get("instrument", "")
                if re.fullmatch(r"(SH|SZ|BJ)\d{6}", instrument):
                    code = instrument[2:]
                    if code in name_lookup:
                        names[code] = name_lookup[code]

    return {"version": 1, "dates": dates, "names": dict(sorted(names.items()))}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signals", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    index = build_index(args.signals)
    if not index["dates"]:
        parser.error("no CSI1000 Top20 signal files found")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Pages CSI1000 index: {len(index['dates'])} dates, {len(index['names'])} names")


if __name__ == "__main__":
    main()
