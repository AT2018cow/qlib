#!/usr/bin/env python3
"""Build the public CSI1000 forward-performance JSON for GitHub Pages."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))

from quant_bootstrap import ensure_quant_import_paths  # noqa: E402

ensure_quant_import_paths()

from forward_performance import FORWARD_PERFORMANCE_START, build_forward_performance  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signals", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--market",
        default="csi1000",
        choices=("csi1000", "csi500", "csi300"),
    )
    args = parser.parse_args()

    pattern = (
        "*_paper_portfolio.json"
        if args.market == "csi1000"
        else f"*_paper_portfolio_{args.market}.json"
    )
    artifacts = []
    for path in sorted(args.signals.glob(pattern)):
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

    result = build_forward_performance(artifacts, market=args.market)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
