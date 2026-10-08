"""Publish an index of existing CSI1000 daily signals for static Pages clients.

Only filenames are indexed; no model/paper data are recalculated or modified.
"""

import argparse
import json
import re
from datetime import date
from pathlib import Path

SIGNAL_NAME = re.compile(r"^(\\d{4}-\\d{2}-\\d{2})_top20_lgb158\\.csv$")


def collect_signal_dates(signals: Path) -> list[str]:
    dates = set()
    for file in signals.iterdir():
        if not file.is_file() or file.stat().st_size == 0:
            continue
        match = SIGNAL_NAME.fullmatch(file.name)
        if match is None:
            continue
        try:
            day = date.fromisoformat(match.group(1))
        except ValueError:
            continue
        if day.isoformat() == match.group(1):
            dates.add(match.group(1))
    return sorted(dates, reverse=True)


def build_index(signals: Path, output: Path) -> dict:
    dates = collect_signal_dates(signals)
    if not dates:
        raise ValueError(f"No CSI1000 Top20 CSVs found in {signals}")
    index = {"schema_version": 1, "dates": dates}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    return index


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signals", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    index = build_index(args.signals, args.output)
    print(f"Indexed {len(index['dates'])} CSI1000 signal dates")


if __name__ == "__main__":
    main()
