#!/usr/bin/env python3
"""Rank Modal apps by billed cost (top-N) for one or more workspaces.

Uses only the installed ``modal`` CLI (``modal billing report --json``), so it
works with any CLI version that exposes the billing commands.  Reads nothing
but public cost aggregates; never touches Volumes, secrets or app code.

Examples
--------
Top 5 apps on the active profile for this month::

    python scripts/modal_top_apps.py

Top 10 across both workspaces, with per-resource split::

    python scripts/modal_top_apps.py --profile at2018cow infi --top 10 --by-resource

Last month on a single workspace::

    python scripts/modal_top_apps.py --profile infi --for "last month"
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from decimal import Decimal


def fetch_report(profile: str | None, cycle: str, by_resource: bool) -> list[dict]:
    # Prefer `sys.executable -m modal` so the CLI is found even when the
    # `modal` entry point is not on PATH (e.g. project virtualenvs).
    cmd = [sys.executable, "-m", "modal", "billing", "report",
           "--for", cycle, "--json"]
    if by_resource:
        cmd.append("--show-resources")
    if profile:
        cmd += ["--profile", profile]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    except FileNotFoundError:
        sys.exit("error: `modal` CLI not found on PATH")
    except subprocess.CalledProcessError as exc:
        sys.exit(f"error: `{' '.join(cmd)}` failed:\n{exc.stderr.strip()}")
    try:
        rows = json.loads(out)
    except json.JSONDecodeError:
        sys.exit("error: could not parse `modal billing report` JSON output")
    for row in rows:
        row["_profile"] = profile or "(active)"
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", nargs="*", default=None,
                        help="Modal profile(s)/workspace(s) to query (default: active profile)")
    parser.add_argument("--for", dest="cycle", default="this month",
                        help='billing cycle, e.g. "this month", "last month", "2026-09"')
    parser.add_argument("--top", type=int, default=5,
                        help="show the top N apps (default: 5)")
    parser.add_argument("--by-resource", action="store_true",
                        help="also break each app total down by resource (CPU/memory/GPU/...)")
    args = parser.parse_args()

    profiles = args.profile or [None]
    rows: list[dict] = []
    for profile in profiles:
        rows.extend(fetch_report(profile, args.cycle, args.by_resource))

    totals: dict[tuple, Decimal] = defaultdict(Decimal)
    names: dict[tuple, str] = {}
    resources: dict[tuple, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for row in rows:
        key = (row["_profile"], row["object_id"])
        names[key] = row.get("description") or row["object_id"]
        cost = Decimal(str(row["cost"]))
        totals[key] += cost
        if args.by_resource:
            resources[key][row.get("resource", "?")] += cost

    grand = sum(totals.values(), Decimal("0"))
    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)

    print(f"Modal billing: {args.cycle} | profiles: {', '.join(p or '(active)' for p in profiles)}")
    print(f"Workspace total: ${grand:,.4f} across {len(totals)} apps")
    print(f"\n{'#':<3} {'app':<28} {'app_id':<24} {'cost (USD)':>12} {'share':>7}")
    print("-" * 80)
    for rank, (key, total) in enumerate(ranked[: args.top], 1):
        profile, app_id = key
        share = (total / grand * 100) if grand else Decimal("0")
        label = names[key][:27]
        if len(profiles) > 1:
            label = f"[{profile}] {label}"[:27]
        print(f"{rank:<3} {label:<28} {app_id:<24} ${total:>11,.4f} {share:>6.1f}%")
        if args.by_resource:
            for res, amount in sorted(resources[key].items(), key=lambda kv: -kv[1]):
                print(f"      - {res:<12} ${amount:>11,.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
