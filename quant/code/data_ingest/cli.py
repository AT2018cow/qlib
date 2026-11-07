"""Command line entry point for the fail-closed provider overlay."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from data_ingest.baostock_source import BaoStockSource
from data_ingest.pipeline import OverlayPipeline, PipelineError
from data_ingest.sina import SinaSource


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a versioned Qlib provider overlay; never publishes signals.")
    parser.add_argument("--base", required=True, help="frozen Qlib provider directory")
    parser.add_argument("--out", required=True, help="new overlay directory (must not exist)")
    parser.add_argument("--asof", required=True, help="latest target trading date (YYYY-MM-DD)")
    parser.add_argument("--dates", default=None, help="comma-separated target dates; default uses Sina calendar")
    parser.add_argument("--markets", default="csi300,csi500,csi1000")
    parser.add_argument("--extra-symbols", default="")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--crosscheck", type=int, default=20)
    parser.add_argument("--base-archive-sha256", default=None)
    parser.add_argument("--require-qlib", action="store_true")
    parser.add_argument("--skip-weights", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    pipeline = OverlayPipeline(
        sina=SinaSource(workers=args.workers),
        baostock=BaoStockSource(),
        crosscheck_count=args.crosscheck,
        require_weights=not args.skip_weights,
    )
    try:
        manifest = pipeline.build(
            args.base,
            args.out,
            asof=args.asof,
            dates=_csv(args.dates) if args.dates else None,
            markets=tuple(_csv(args.markets)),
            extra_symbols=_csv(args.extra_symbols),
            base_archive_sha256=args.base_archive_sha256,
            require_qlib_smoke=args.require_qlib,
        )
    except PipelineError as exc:
        print(f"OVERLAY_FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"status": manifest["status"], "out": str(Path(args.out)), "asof": args.asof}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
