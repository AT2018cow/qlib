"""Gate-0 diagnostics for the balanced strategy reselection (2026-11).

Pure, dependency-light helpers (stdlib only) used to audit the actual paper
drawdown, concentration and provider coverage before any expensive research
run.  The bin reader supports the Qlib float32 layout without importing qlib
or numpy, so the core CI group can exercise it with pytest only.
"""

from __future__ import annotations

import argparse
import array
import json
import math
import statistics
import sys
from pathlib import Path


# --------------------------------------------------------------------------- #
# Paper artifacts
# --------------------------------------------------------------------------- #


def board_of(code: str) -> str:
    """Board classification for an A-share instrument code."""
    if code.startswith("SH68"):
        return "STAR"
    if code.startswith("SZ30"):
        return "ChiNext"
    if code.startswith("BJ"):
        return "BSE"
    return "Main"


def board_exposure(positions: dict) -> dict:
    counts = {}
    for code in positions:
        counts[board_of(code)] = counts.get(board_of(code), 0) + 1
    total = sum(counts.values())
    growth = counts.get("STAR", 0) + counts.get("ChiNext", 0)
    return {"counts": counts, "positions": total, "growth_share": (growth / total) if total else 0.0}


def max_drawdown(nav: list[float]) -> float:
    peak = nav[0]
    worst = 0.0
    for value in nav:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1.0)
    return worst


def annualized_vol(returns: list[float], sessions: int = 238) -> float:
    if len(returns) < 2:
        return 0.0
    return statistics.pstdev(returns) * math.sqrt(sessions)


def reconstruct_paper_series(artifacts: list[dict]) -> dict:
    """Rebuild close-to-close paper NAV from consecutive daily artifacts.

    Each artifact carries the settlement report of the previous execution day;
    the report's pre-trade open is the account value at the start of that day
    and its close is the end-of-day NAV.
    """
    reports = {}
    for artifact in artifacts:
        report = artifact.get("execution_report")
        if not isinstance(report, dict):
            continue
        day = report.get("date")
        close = report.get("portfolio_value_close")
        pre = report.get("portfolio_value_pre_trade_open")
        if not isinstance(day, str) or not isinstance(close, (int, float)) or not isinstance(pre, (int, float)):
            continue
        if day in reports:
            raise ValueError(f"duplicate execution report for {day}")
        reports[day] = {"pre": float(pre), "close": float(close)}
    if not reports:
        return {"status": "NO_REPORTS"}
    days = sorted(reports)
    start = reports[days[0]]["pre"]
    closes = [(day, reports[day]["close"]) for day in days]
    nav = [start] + [value for _, value in closes]
    if start <= 0 or any(value <= 0 for value in nav):
        raise ValueError("non-positive paper NAV")
    returns = [closes[i][1] / closes[i - 1][1] - 1.0 for i in range(1, len(closes))]
    return {
        "status": "PASS",
        "start": days[0],
        "end": days[-1],
        "start_nav": start,
        "end_nav": closes[-1][1],
        "return": closes[-1][1] / start - 1.0,
        "max_drawdown": max_drawdown(nav),
        "annualized_vol": annualized_vol(returns),
        "closes": closes,
    }


def load_paper_artifacts(signals_dir: Path, suffix: str = "") -> list[dict]:
    artifacts = []
    for path in sorted(Path(signals_dir).glob(f"*_paper_portfolio{suffix}.json")):
        day = path.name[:10]
        payload = json.loads(path.read_text())
        if payload.get("usage_date") != day:
            raise ValueError(f"artifact date mismatch: {path.name}")
        artifacts.append(payload)
    return artifacts


# --------------------------------------------------------------------------- #
# Qlib bin reader / coverage audit
# --------------------------------------------------------------------------- #


def read_calendar(provider_dir: Path) -> list[str]:
    return [
        line.strip() for line in (Path(provider_dir) / "calendars" / "day.txt").read_text().splitlines() if line.strip()
    ]


def read_bin_field(provider_dir: Path, code: str, field_name: str) -> dict:
    """Return {date: value} for a Qlib .day.bin field (little-endian float32)."""
    path = Path(provider_dir) / "features" / code.lower() / f"{field_name}.day.bin"
    if not path.is_file():
        return {}
    calendar = read_calendar(provider_dir)
    values = array.array("f")
    with path.open("rb") as stream:
        values.fromfile(stream, path.stat().st_size // 4)
    if sys.byteorder != "little":
        values.byteswap()
    if len(values) < 1:
        return {}
    start = int(values[0])
    series = {}
    for offset, value in enumerate(values[1:]):
        index = start + offset
        if index < len(calendar) and math.isfinite(float(value)):
            series[calendar[index]] = float(value)
    return series


def daily_returns(series: dict, start: str | None = None, end: str | None = None) -> list[float]:
    days = sorted(day for day in series if (start is None or day >= start) and (end is None or day <= end))
    return [series[days[i]] / series[days[i - 1]] - 1.0 for i in range(1, len(days))]


def pairwise_correlation_summary(provider_dir: Path, codes: list[str], end: str, window: int = 60) -> dict:
    calendar = read_calendar(provider_dir)
    cutoff = [day for day in calendar if day <= end][-window:]
    returns = {}
    for code in codes:
        series = read_bin_field(provider_dir, code, "close")
        values = [series[day] for day in cutoff if day in series]
        returns[code] = [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]
    pairs = []
    for i, left in enumerate(sorted(returns)):
        for right in sorted(returns)[i + 1 :]:
            length = min(len(returns[left]), len(returns[right]))
            if length < 20:
                continue
            x = returns[left][-length:]
            y = returns[right][-length:]
            try:
                corr = statistics.correlation(x, y)
            except (statistics.StatisticsError, ValueError):
                continue
            pairs.append({"left": left, "right": right, "corr": corr})
    values = [pair["corr"] for pair in pairs if math.isfinite(pair["corr"])]
    return {
        "pairs": pairs,
        "mean": statistics.mean(values) if values else None,
        "median": statistics.median(values) if values else None,
        "max": max(values) if values else None,
        "pairs_over_0_7": sum(1 for value in values if value > 0.7),
        "n_pairs": len(values),
    }


def audit_provider(provider_dir: Path, markets=("csi300", "csi500", "csi1000")) -> dict:
    provider_dir = Path(provider_dir)
    calendar = read_calendar(provider_dir)
    end = calendar[-1]
    feature_root = provider_dir / "features"
    available = {path.name for path in feature_root.iterdir()} if feature_root.is_dir() else set()
    report = {"calendar_end": end, "calendar_sessions": len(calendar), "markets": {}}
    for market in markets:
        path = provider_dir / "instruments" / f"{market}.txt"
        if not path.is_file():
            report["markets"][market] = {"status": "MISSING_INSTRUMENTS"}
            continue
        members = {}
        for line in path.read_text().splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                members[parts[0].strip().lower()] = (parts[1].strip(), parts[2].strip())
        stale = []
        missing = []
        for code, (_start, member_end) in members.items():
            if code not in available:
                missing.append(code)
                continue
            series = read_bin_field(provider_dir, code, "close")
            if not series:
                missing.append(code)
                continue
            last_day = max(series)
            if last_day < end:
                stale.append(
                    {"code": code, "last": last_day, "member_end": member_end, "still_member": member_end > last_day}
                )
        report["markets"][market] = {
            "members": len(members),
            "missing_features": len(missing),
            "stale_last_close": len(stale),
            "stale_still_member": sum(1 for item in stale if item["still_member"]),
            "examples": stale[:5],
        }
    return report


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signals", type=Path, required=True)
    parser.add_argument("--provider", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = {}
    for label, suffix in (("csi1000", ""), ("csi500", "_csi500"), ("csi300", "_csi300")):
        artifacts = load_paper_artifacts(args.signals, suffix)
        result[label] = reconstruct_paper_series(artifacts)
        if artifacts:
            latest = artifacts[-1]
            result[label]["board_exposure"] = board_exposure(latest.get("positions", {}))
    if args.provider:
        result["provider_audit"] = audit_provider(args.provider)
        latest_codes = []
        for label, suffix in (("csi1000", ""), ("csi500", "_csi500"), ("csi300", "_csi300")):
            artifacts = load_paper_artifacts(args.signals, suffix)
            if artifacts:
                latest_codes.extend(artifacts[-1].get("positions", {}))
        result["correlation_60d"] = pairwise_correlation_summary(
            args.provider, sorted(set(latest_codes)), result["provider_audit"]["calendar_end"]
        )
    payload = json.dumps(result, indent=2, ensure_ascii=False, default=str)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n")
    print(payload)


if __name__ == "__main__":
    main()
