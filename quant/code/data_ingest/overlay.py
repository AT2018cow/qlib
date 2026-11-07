"""Append-only Qlib binary overlay writer."""

from __future__ import annotations

import shutil
import struct
from pathlib import Path
from typing import Iterable, Mapping

import numpy as np

from data_ingest.adjust import FIELDS
from data_ingest.calendar import read_calendar
from data_ingest.model import Membership


class OverlayError(RuntimeError):
    """Raised when the frozen base cannot be extended safely."""


def read_ranges(path: str | Path) -> dict[str, list[list[str]]]:
    ranges: dict[str, list[list[str]]] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            raise OverlayError(f"invalid instrument row in {path}: {line!r}")
        code, start, end = parts
        ranges.setdefault(code, []).append([start, end])
    return ranges


def write_ranges(path: str | Path, ranges: Mapping[str, list[list[str]]]) -> None:
    lines = []
    for code in sorted(ranges):
        for start, end in ranges[code]:
            lines.append(f"{code}\t{start}\t{end}")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def active_members(ranges: Mapping[str, list[list[str]]], date: str) -> set[str]:
    return {code for code, spans in ranges.items() if any(start <= date <= end for start, end in spans)}


def historical_union(base_dir: str | Path, markets: Iterable[str] = ("csi300", "csi500", "csi1000")) -> set[str]:
    base = Path(base_dir)
    union: set[str] = set()
    for market in markets:
        union.update(read_ranges(base / "instruments" / f"{market}.txt"))
    return union


def _copy_tree_files(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.is_file():
            shutil.copy2(item, dst / item.name)
        elif item.is_dir():
            shutil.copytree(item, dst / item.name)


def copy_base(
    base_dir: str | Path,
    out_dir: str | Path,
    *,
    feature_symbols: Iterable[str],
) -> dict:
    """Copy calendars/instruments and the required feature directories."""
    base = Path(base_dir)
    out = Path(out_dir)
    if out.exists():
        raise OverlayError(f"overlay target already exists: {out}")
    out.mkdir(parents=True)
    _copy_tree_files(base / "calendars", out / "calendars")
    _copy_tree_files(base / "instruments", out / "instruments")
    (out / "features").mkdir(parents=True)
    copied = 0
    missing: list[str] = []
    for symbol in sorted(set(feature_symbols)):
        src = base / "features" / symbol.lower()
        if not src.is_dir():
            missing.append(symbol)
            continue
        shutil.copytree(src, out / "features" / symbol.lower())
        copied += 1
    return {"feature_dirs_copied": copied, "missing_feature_dirs": missing}


def append_calendar(out_dir: str | Path, dates: list[str]) -> None:
    path = Path(out_dir) / "calendars" / "day.txt"
    existing = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not dates:
        raise OverlayError("no dates to append")
    if dates != sorted(set(dates)):
        raise OverlayError("new dates are not ordered and unique")
    if dates[0] <= existing[-1]:
        raise OverlayError("new dates overlap the base calendar")
    path.write_text("\n".join(existing + list(dates)) + "\n", encoding="utf-8")


def _append_bin(path: Path, start_index: int, values: list[float]) -> None:
    if not values:
        return
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        np.hstack([[float(start_index)], values]).astype("<f").tofile(path)
        return
    data = np.fromfile(path, dtype="<f")
    if len(data) == 0:
        raise OverlayError(f"empty feature bin: {path}")
    old_start = int(data[0])
    old_end = old_start + len(data) - 2
    target_end = start_index + len(values) - 1
    if old_end >= target_end:
        return
    pad = np.full(target_end - old_end, np.nan, dtype="<f")
    pad[-len(values) :] = values
    with path.open("ab") as handle:
        pad.tofile(handle)


def append_features(
    out_dir: str | Path,
    rows: Mapping[str, Mapping[str, Mapping[str, float]]],
    *,
    start_index: int,
    dates: list[str],
) -> int:
    out = Path(out_dir)
    appended = 0
    for symbol, per_date in rows.items():
        for field in FIELDS:
            values = [float(per_date.get(date, {}).get(field, float("nan"))) for date in dates]
            _append_bin(out / "features" / symbol.lower() / f"{field}.day.bin", start_index, values)
            appended += 1
    return appended


def update_instruments(
    out_dir: str | Path,
    memberships: Mapping[str, Membership],
    *,
    base_end: str,
    asof: str,
    row_symbols: Iterable[str],
) -> dict:
    out = Path(out_dir)
    updated: dict[str, dict] = {}
    new_sets: dict[str, set[str]] = {market: set(item.members) for market, item in memberships.items()}
    for market, membership in memberships.items():
        path = out / "instruments" / f"{market}.txt"
        ranges = read_ranges(path)
        old = active_members(ranges, base_end)
        new = set(membership.members)
        for code in old & new:
            for span in ranges.get(code, []):
                if span[0] <= base_end <= span[1]:
                    span[1] = asof
        for code in new - old:
            ranges.setdefault(code, []).append([asof, asof])
        write_ranges(path, ranges)
        updated[market] = {
            "old_count": len(old),
            "new_count": len(new),
            "added": sorted(new - old),
            "removed": sorted(old - new),
        }
    if "csi300" in new_sets and "csi500" in new_sets:
        path = out / "instruments" / "csi800.txt"
        if path.is_file():
            ranges = read_ranges(path)
            old = active_members(ranges, base_end)
            new = new_sets["csi300"] | new_sets["csi500"]
            for code in old & new:
                for span in ranges.get(code, []):
                    if span[0] <= base_end <= span[1]:
                        span[1] = asof
            for code in new - old:
                ranges.setdefault(code, []).append([asof, asof])
            write_ranges(path, ranges)
            updated["csi800"] = {"old_count": len(old), "new_count": len(new)}
    all_path = out / "instruments" / "all.txt"
    if all_path.is_file():
        ranges = read_ranges(all_path)
        for code in set(row_symbols):
            for span in ranges.get(code, []):
                if span[0] <= base_end <= span[1]:
                    span[1] = asof
        write_ranges(all_path, ranges)
    return updated


def read_base_features(
    base_dir: str | Path,
    symbols: Iterable[str],
    date: str,
    fields: tuple[str, ...] = ("close", "adjclose", "factor"),
) -> dict[str, dict[str, float]]:
    base = Path(base_dir)
    calendar = read_calendar(base)
    if date not in calendar:
        raise OverlayError(f"base date {date} not in calendar")
    index = calendar.index(date)
    output: dict[str, dict[str, float]] = {}
    for symbol in symbols:
        values: dict[str, float] = {}
        for field in fields:
            path = base / "features" / symbol.lower() / f"{field}.day.bin"
            if not path.is_file():
                break
            with path.open("rb") as handle:
                raw = handle.read(4)
                if len(raw) != 4:
                    break
                start = int(struct.unpack("<f", raw)[0])
                if index < start:
                    value = float("nan")
                else:
                    handle.seek(4 * (index - start) + 4)
                    payload = handle.read(4)
                    value = struct.unpack("<f", payload)[0] if len(payload) == 4 else float("nan")
            if not np.isfinite(value):
                break
            values[field] = float(value)
        if len(values) == len(fields):
            output[symbol] = values
    return output


def read_bin_value(provider_dir: str | Path, symbol: str, field: str, date: str) -> float:
    root = Path(provider_dir)
    calendar = read_calendar(root)
    if date not in calendar:
        return float("nan")
    index = calendar.index(date)
    path = root / "features" / symbol.lower() / f"{field}.day.bin"
    if not path.is_file():
        return float("nan")
    with path.open("rb") as handle:
        raw = handle.read(4)
        if len(raw) != 4:
            return float("nan")
        start = int(struct.unpack("<f", raw)[0])
        if index < start:
            return float("nan")
        handle.seek(4 * (index - start) + 4)
        payload = handle.read(4)
        return struct.unpack("<f", payload)[0] if len(payload) == 4 else float("nan")


def build_overlay(
    base_dir: str | Path,
    out_dir: str | Path,
    *,
    dates: list[str],
    rows: Mapping[str, Mapping[str, Mapping[str, float]]],
    memberships: Mapping[str, Membership],
    base_end: str,
    asof: str,
) -> dict:
    base = Path(base_dir)
    base_calendar = read_calendar(base)
    if base_calendar[-1] != base_end:
        raise OverlayError(f"base calendar ends {base_calendar[-1]}, expected {base_end}")
    if dates[-1] != asof:
        raise OverlayError(f"target dates end {dates[-1]}, expected {asof}")
    feature_symbols = historical_union(base) | set(rows)
    metadata = copy_base(base, out_dir, feature_symbols=feature_symbols)
    append_calendar(out_dir, dates)
    metadata["bins_appended"] = append_features(out_dir, rows, start_index=len(base_calendar), dates=dates)
    metadata["instruments"] = update_instruments(out_dir, memberships, base_end=base_end, asof=asof, row_symbols=rows)
    metadata["base_end"] = base_end
    metadata["asof"] = asof
    metadata["dates"] = list(dates)
    metadata["calendar_len"] = len(base_calendar) + len(dates)
    metadata["row_symbols"] = len(rows)
    return metadata
