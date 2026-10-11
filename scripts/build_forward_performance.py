#!/usr/bin/env python3
"""Build the public CSI1000 forward-performance JSON for GitHub Pages."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forward_performance import FORWARD_PERFORMANCE_START, build_forward_performance


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signals", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    artifacts = []
    for path in sorted(args.signals.glob("*_paper_portfolio.json")):
        try:
            raw = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            if path.name[:10] >= FORWARD_PERFORMANCE_START:
                raise ValueError(f"invalid post-start paper artifact: {path}") from exc
            continue
        if not isinstance(raw, dict):
            if path.name[:10] >= FORWARD_PERFORMANCE_START:
                raise ValueError(f"unexpected post-start paper artifact: {path}")
            continue
        artifacts.append(raw)

    result = build_forward_performance(artifacts)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
