"""Thin CLI for rebuilding the current-window correction inventory."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
if str(CODE / "research" / "current_window") not in sys.path:
    sys.path.insert(0, str(CODE / "research" / "current_window"))
if str(CODE / "execution") not in sys.path:
    sys.path.insert(0, str(CODE / "execution"))

from current_window_net import build_correction_inventory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-label", default="")
    args = parser.parse_args()
    payload = build_correction_inventory(args.source, source_label=args.source_label or None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n")
    temporary.replace(args.output)


if __name__ == "__main__":
    main()
