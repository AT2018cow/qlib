"""Isolated, read-only probe for the self-hosted provider pipeline.

The production Volume is mounted **read-only**.  Every artifact produced by a
probe goes to a dedicated probe Volume (``qlib-overlay-probe``), never to
``qlib-prod-data``.  This module deliberately does not import the production
app module at import time, so it can never publish a signal or advance a paper
account.

Run attached in the foreground from the repository root::

    modal run quant/code/ops/overlay_primary_probe.py --mode recon

Modes:
    recon          file counts, byte sizes, manifests, snapshot chain
    read_bench     sequential small-file read vs archive read/local extract
    sample_compare compare provider bins with a fresh Sina raw+hfq fetch
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
if not (CODE / "quant_bootstrap.py").is_file():
    CODE = Path("/root/qlib/quant/code")
sys.path.insert(0, str(CODE))
from quant_bootstrap import ensure_quant_import_paths

ensure_quant_import_paths()
sys.path.insert(0, str(CODE / "production"))

import modal

from image_filters import should_exclude_from_image

PRODUCTION_VOLUME = "qlib-prod-data"
PROBE_VOLUME = "qlib-overlay-probe"
PROBE_APP = "qlib-overlay-primary-probe"


def _local_repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "quant/code/quant_bootstrap.py").is_file():
            return parent
    return Path("/root/qlib")


def _probe_image() -> modal.Image:
    image = (
        modal.Image.debian_slim(python_version="3.11")
        .apt_install("build-essential")
        .pip_install(
            "numpy==1.26.4",
            "pandas==2.2.3",
            "requests",
            "akshare==1.18.94",
            "baostock",
            "pyarrow==19.0.1",
        )
    )
    if modal.is_local():
        image = image.add_local_dir(
            str(_local_repo_root()),
            remote_path="/root/qlib",
            copy=True,
            ignore=lambda path: should_exclude_from_image(path),
        )
    return image


probe_image = _probe_image()

app = modal.App(PROBE_APP, image=probe_image)
production = modal.Volume.from_name(PRODUCTION_VOLUME).with_mount_options(read_only=True)
probe = modal.Volume.from_name(PROBE_VOLUME, create_if_missing=True)


def _tree_stats(root: Path, limit_files: int | None = None) -> dict:
    import time

    started = time.monotonic()
    files = 0
    directories = 0
    total_bytes = 0
    for path in root.rglob("*"):
        if path.is_dir():
            directories += 1
            continue
        if not path.is_file():
            continue
        files += 1
        try:
            total_bytes += path.stat().st_size
        except OSError:
            pass
        if limit_files is not None and files >= limit_files:
            break
    return {
        "root": str(root),
        "files": files,
        "directories": directories,
        "bytes": total_bytes,
        "seconds": round(time.monotonic() - started, 3),
        "truncated": limit_files is not None and files >= limit_files,
    }


def _manifest(root: Path) -> dict:
    path = root / "manifest.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text())


def _snapshot_chain() -> dict:
    root = Path("/vol/cn_data_overlay")
    chain = {}
    if not root.is_dir():
        return chain
    for path in sorted(root.iterdir()):
        if not path.is_dir():
            continue
        payload = _manifest(path)
        calendar = path / "calendars" / "day.txt"
        chain[path.name] = {
            "status": payload.get("status"),
            "base": payload.get("base"),
            "target": payload.get("target"),
            "gate": (payload.get("gate") or {}).get("status"),
            "calendar_end": (calendar.read_text().strip().splitlines()[-1] if calendar.is_file() else None),
        }
    return chain


@app.function(
    volumes={"/vol": production, "/probe": probe}, cpu=2, memory=4096, timeout=1800, retries=0, max_containers=1
)
def recon() -> dict:
    import time

    started = time.monotonic()
    base = Path("/vol/base/chenditc_bin")
    overlay_root = Path("/vol/cn_data_overlay")
    result = {
        "volume": PRODUCTION_VOLUME,
        "mount_read_only": True,
        "base_exists": base.is_dir(),
        "base_calendar_end": (
            (base / "calendars/day.txt").read_text().strip().splitlines()[-1]
            if (base / "calendars/day.txt").is_file()
            else None
        ),
        "overlay_chain": _snapshot_chain(),
        "snapshots": {},
        "base": _tree_stats(base) if base.is_dir() else None,
    }
    for path in sorted(overlay_root.iterdir()) if overlay_root.is_dir() else []:
        if path.is_dir():
            result["snapshots"][path.name] = _tree_stats(path)
    result["volume_root_entries"] = sorted(p.name for p in Path("/vol").iterdir())[:60]
    result["recon_seconds"] = round(time.monotonic() - started, 3)
    out = Path("/probe/recon") / f"recon-{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    probe.commit()
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return result


def _sample_paths(root: Path, limit: int = 240) -> list[Path]:
    paths = []
    for path in (root / "features").rglob("*.day.bin"):
        paths.append(path)
        if len(paths) >= limit:
            break
    return paths


def _read_bytes(paths: list[Path]) -> int:
    total = 0
    for path in paths:
        total += len(path.read_bytes())
    return total


def _make_archive(root: Path, destination: Path) -> dict:
    import hashlib
    import tarfile
    import time

    started = time.monotonic()
    with tarfile.open(destination, "w:gz", compresslevel=1) as archive:
        archive.add(root, arcname="provider")
    elapsed = time.monotonic() - started
    digest = hashlib.sha256()
    with destination.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return {
        "seconds": round(elapsed, 3),
        "bytes": destination.stat().st_size,
        "sha256": digest.hexdigest(),
    }


@app.function(
    volumes={"/vol": production, "/probe": probe}, cpu=4, memory=8192, timeout=3600, retries=0, max_containers=1
)
def read_bench(snapshot: str = "2026-11-10") -> dict:
    import shutil
    import tarfile
    import time

    root = Path("/vol/cn_data_overlay") / snapshot
    if not root.is_dir():
        raise ValueError(f"missing snapshot: {root}")
    sample = _sample_paths(root, limit=240)
    started = time.monotonic()
    direct_bytes = _read_bytes(sample)
    direct_seconds = time.monotonic() - started

    archive = Path("/tmp") / f"provider-{snapshot}.tar.gz"
    archive_meta = _make_archive(root, archive)

    uploaded = Path("/probe/archive_bench") / f"{snapshot}.tar.gz"
    uploaded.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with archive.open("rb") as source, uploaded.open("wb") as target:
        shutil.copyfileobj(source, target, length=8 * 1024 * 1024)
    upload_seconds = time.monotonic() - started

    started = time.monotonic()
    copy_target = Path("/tmp") / f"provider-{snapshot}-read.tar.gz"
    with archive.open("rb") as source, copy_target.open("wb") as target:
        shutil.copyfileobj(source, target, length=8 * 1024 * 1024)
    copy_seconds = time.monotonic() - started

    extract_root = Path("/tmp") / f"provider-{snapshot}-extract"
    if extract_root.exists():
        shutil.rmtree(extract_root)
    extract_root.mkdir(parents=True)
    started = time.monotonic()
    with tarfile.open(archive, "r:gz") as tf:
        tf.extractall(extract_root)
    extract_seconds = time.monotonic() - started

    result = {
        "snapshot": snapshot,
        "tree": _tree_stats(root),
        "sample_files": len(sample),
        "direct_read_bytes": direct_bytes,
        "direct_read_seconds": round(direct_seconds, 3),
        "direct_read_mib_per_s": round(direct_bytes / 1024**2 / max(direct_seconds, 1e-6), 3),
        "archive_build": archive_meta,
        "archive_upload_seconds": round(upload_seconds, 3),
        "archive_upload_mib_per_s": round(archive_meta["bytes"] / 1024**2 / max(upload_seconds, 1e-6), 3),
        "archive_copy_seconds": round(copy_seconds, 3),
        "archive_extract_seconds": round(extract_seconds, 3),
        "archive_source_mib_per_s": round(archive_meta["bytes"] / 1024**2 / max(copy_seconds, 1e-6), 3),
    }
    out = Path("/probe/read_bench") / f"{snapshot}-{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    probe.commit()
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return result


def _read_bin(path: Path) -> tuple[int, list[float]]:
    import numpy as np

    data = np.fromfile(path, dtype="<f")
    if data.size == 0:
        raise ValueError(f"empty bin: {path}")
    return int(data[0]), [float(item) for item in data[1:]]


@app.function(
    volumes={"/vol": production, "/probe": probe}, cpu=2, memory=4096, timeout=1800, retries=0, max_containers=1
)
def sample_compare(snapshot: str = "2026-11-10", symbols: str = "SH600000,SZ000001,SH601318") -> dict:
    """Compare the provider bins against one fresh Sina raw/hfq fetch."""
    import time

    import akshare as ak
    import numpy as np
    import pandas as pd

    root = Path("/vol/cn_data_overlay") / snapshot
    calendar = (root / "calendars/day.txt").read_text().strip().splitlines()
    end = calendar[-1]
    start = calendar[-6]
    comparisons = {}
    for symbol in [item.strip().upper() for item in symbols.split(",") if item.strip()]:
        sina_symbol = symbol[:2].lower() + symbol[2:]
        raw = ak.stock_zh_a_daily(
            symbol=sina_symbol, start_date=start.replace("-", ""), end_date=end.replace("-", ""), adjust=""
        )
        hfq = ak.stock_zh_a_daily(
            symbol=sina_symbol, start_date=start.replace("-", ""), end_date=end.replace("-", ""), adjust="hfq"
        )
        hfq_factor = ak.stock_zh_a_daily(
            symbol=sina_symbol, start_date=start.replace("-", ""), end_date=end.replace("-", ""), adjust="hfq-factor"
        )
        raw_map = {str(row["date"])[:10]: row for _, row in raw.iterrows()}
        hfq_map = {str(row["date"])[:10]: row for _, row in hfq.iterrows()}
        factor_map = {str(row["date"])[:10]: float(row["hfq_factor"]) for _, row in hfq_factor.iterrows()}
        per_date = {}
        for date in calendar[-4:]:
            index = calendar.index(date)
            values = {}
            for field in ("open", "close", "high", "low", "volume", "amount", "factor", "adjclose", "vwap"):
                bin_start, bin_values = _read_bin(root / "features" / symbol.lower() / f"{field}.day.bin")
                values[field] = bin_values[index - bin_start] if index >= bin_start else None
            raw_row = raw_map.get(date)
            hfq_row = hfq_map.get(date)
            if raw_row is None or hfq_row is None or values["close"] is None:
                continue
            provider_close = float(values["close"])
            provider_factor = float(values["factor"])
            provider_adjclose = float(values["adjclose"])
            sina_raw_close = float(raw_row["close"])
            sina_hfq_close = float(hfq_row["close"])
            sina_hfq_factor = factor_map.get(date)
            per_date[date] = {
                "provider": values,
                "sina_raw_close": sina_raw_close,
                "sina_hfq_close": sina_hfq_close,
                "sina_hfq_factor": sina_hfq_factor,
                "provider_raw_from_close_over_factor": provider_close / provider_factor if provider_factor else None,
                "adjclose_over_close": provider_adjclose / provider_close if provider_close else None,
                "adjclose_over_hfq": provider_adjclose / sina_hfq_close if sina_hfq_close else None,
                "hfq_over_raw": sina_hfq_close / sina_raw_close if sina_raw_close else None,
                "sina_hfq_factor_over_provider_factor": (
                    sina_hfq_factor / provider_factor if sina_hfq_factor and provider_factor else None
                ),
            }
        if per_date:
            comparisons[symbol] = {"dates": per_date}
    result = {
        "snapshot": snapshot,
        "calendar_end": end,
        "start": start,
        "comparisons": comparisons,
        "akshare_version": getattr(ak, "__version__", "unknown"),
        "pandas_version": pd.__version__,
        "numpy_version": np.__version__,
        "timestamp": time.time(),
    }
    out = Path("/probe/sample_compare") / f"{snapshot}-{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
    probe.commit()
    print(json.dumps(result, indent=2, sort_keys=True)[:8000], flush=True)
    return result


@app.function(volumes={"/probe": probe}, cpu=4, memory=8192, timeout=1800, retries=0, max_containers=1)
def archive_read_bench(snapshot: str = "2026-11-10") -> dict:
    """Measure a single sequential Volume read of the provider archive."""
    import hashlib
    import shutil
    import tarfile
    import time

    source = Path("/probe/archive_bench") / f"{snapshot}.tar.gz"
    if not source.is_file():
        raise ValueError(f"missing probe archive: {source}")
    target = Path("/tmp") / f"{snapshot}.tar.gz"
    started = time.monotonic()
    digest = hashlib.sha256()
    with source.open("rb") as stream, target.open("wb") as output:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
            output.write(chunk)
    read_seconds = time.monotonic() - started
    extract_root = Path("/tmp") / f"{snapshot}-read-extract"
    if extract_root.exists():
        shutil.rmtree(extract_root)
    extract_root.mkdir(parents=True)
    started = time.monotonic()
    with tarfile.open(target, "r:gz") as tf:
        tf.extractall(extract_root)
    extract_seconds = time.monotonic() - started
    result = {
        "snapshot": snapshot,
        "bytes": target.stat().st_size,
        "sha256": digest.hexdigest(),
        "volume_read_seconds": round(read_seconds, 3),
        "volume_read_mib_per_s": round(target.stat().st_size / 1024**2 / max(read_seconds, 1e-6), 3),
        "extract_seconds": round(extract_seconds, 3),
    }
    out = Path("/probe/read_bench") / f"{snapshot}-read-{int(time.time())}.json"
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    probe.commit()
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return result


@app.function(
    volumes={"/vol": production, "/probe": probe}, cpu=2, memory=4096, timeout=1800, retries=0, max_containers=1
)
def factor_convention(snapshot: str = "2026-11-10", symbols: str = "") -> dict:
    """Test whether provider factor == hfq_ratio(first provider bar) / hfq_ratio(t).

    ``hfq_ratio`` is Sina hfq close / raw close.  The provider stores
    ``close = raw * factor``; ``factor`` is a front-adjustment normalized at
    the start of the provider history, not at "today".
    """
    import time

    import akshare as ak
    import pandas as pd

    root = Path("/vol/cn_data_overlay") / snapshot
    calendar = (root / "calendars/day.txt").read_text().strip().splitlines()
    if not symbols:
        paths = sorted((root / "features").iterdir())[:12]
        symbols = ",".join(path.name.upper() for path in paths)
    results = {}
    for symbol in [item.strip().upper() for item in symbols.split(",") if item.strip()]:
        sina_symbol = symbol[:2].lower() + symbol[2:]
        try:
            raw = ak.stock_zh_a_daily(symbol=sina_symbol, start_date="19900101", end_date="21000118", adjust="")
            hfq = ak.stock_zh_a_daily(symbol=sina_symbol, start_date="19900101", end_date="21000118", adjust="hfq")
        except Exception as exc:  # noqa: BLE001 - provider boundary
            results[symbol] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        raw_map = {str(row["date"])[:10]: float(row["close"]) for _, row in raw.iterrows()}
        hfq_map = {str(row["date"])[:10]: float(row["close"]) for _, row in hfq.iterrows()}
        common = sorted(set(raw_map) & set(hfq_map))
        if not common:
            results[symbol] = {"error": "no common Sina dates"}
            continue
        # The provider normalizes each stock's adjusted close to 1.0 on its
        # first bar in the provider calendar, not on Sina's first history day.
        ref = max(calendar[0], common[0])
        if ref not in raw_map or ref not in hfq_map:
            results[symbol] = {"error": f"reference date {ref} missing from Sina data"}
            continue
        h_ref = hfq_map[ref] / raw_map[ref]
        factor_start, factor_values = _read_bin(root / "features" / symbol.lower() / "factor.day.bin")
        close_start, close_values = _read_bin(root / "features" / symbol.lower() / "close.day.bin")
        checks = {}
        for date in (calendar[0], "2005-01-04", "2010-01-04", "2015-01-05", "2020-01-02", calendar[-1]):
            if date not in calendar or date not in raw_map or date not in hfq_map:
                continue
            index = calendar.index(date)
            if index < factor_start or index < close_start:
                continue
            if index - factor_start >= len(factor_values) or index - close_start >= len(close_values):
                continue
            h = hfq_map[date] / raw_map[date]
            predicted = h_ref / h
            actual = float(factor_values[index - factor_start])
            actual_close = float(close_values[index - close_start])
            predicted_close = raw_map[date] * predicted
            checks[date] = {
                "provider_factor": actual,
                "predicted_factor": predicted,
                "factor_rel_error": abs(actual - predicted) / max(abs(actual), 1e-12),
                "provider_close": actual_close,
                "predicted_close": predicted_close,
                "close_rel_error": abs(actual_close - predicted_close) / max(abs(actual_close), 1e-12),
            }
        results[symbol] = {
            "first_common_date": common[0],
            "reference_date": ref,
            "provider_calendar_start": calendar[0],
            "provider_close_at_reference": (
                float(close_values[max(0, calendar.index(ref) - close_start)])
                if calendar.index(ref) >= close_start and calendar.index(ref) - close_start < len(close_values)
                else None
            ),
            "checks": checks,
            "max_factor_rel_error": max((v["factor_rel_error"] for v in checks.values()), default=None),
            "max_close_rel_error": max((v["close_rel_error"] for v in checks.values()), default=None),
        }
    result = {
        "snapshot": snapshot,
        "calendar_start": calendar[0],
        "calendar_end": calendar[-1],
        "results": results,
        "akshare_version": getattr(ak, "__version__", "unknown"),
        "pandas_version": pd.__version__,
        "timestamp": time.time(),
    }
    out = Path("/probe/factor_convention") / f"{snapshot}-{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
    probe.commit()
    print(json.dumps(result, indent=2, sort_keys=True)[:12000], flush=True)
    return result


@app.function(
    volumes={"/vol": production, "/probe": probe}, cpu=2, memory=4096, timeout=1800, retries=0, max_containers=1
)
def baostock_convention(snapshot: str = "2026-11-10", symbols: str = "") -> dict:
    """Reconstruct provider close/factor from BaoStock raw+preclose history.

    Rule under test: anchor adjusted close at 1.0 on the stock's first bar in
    the provider calendar; on an ex-date use ``prev_close * raw/preclose``,
    otherwise ``prev_close * raw/prev_raw``.  This is exactly
    ``data_ingest.adjust.build_rows`` without a base snapshot.
    """
    import math
    import time

    import baostock as bs
    import pandas as pd

    from data_ingest.symbols import to_baostock_symbol

    root = Path("/vol/cn_data_overlay") / snapshot
    calendar = (root / "calendars/day.txt").read_text().strip().splitlines()
    if not symbols:
        paths = sorted((root / "features").iterdir())[:8]
        symbols = ",".join(path.name.upper() for path in paths)
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock login failed: {login.error_code} {login.error_msg}")
    results = {}
    try:
        for symbol in [item.strip().upper() for item in symbols.split(",") if item.strip()]:
            result = bs.query_history_k_data_plus(
                to_baostock_symbol(symbol),
                "date,open,high,low,close,preclose,volume,amount,tradestatus",
                start_date=calendar[0],
                end_date=calendar[-1],
                frequency="d",
                adjustflag="3",
            )
            if result.error_code != "0":
                results[symbol] = {"error": f"{result.error_code} {result.error_msg}"}
                continue
            rows = []
            while result.next():
                rows.append(dict(zip(result.fields, result.get_row_data())))
            if not rows:
                results[symbol] = {"error": "empty BaoStock history"}
                continue
            factor_start, factor_values = _read_bin(root / "features" / symbol.lower() / "factor.day.bin")
            close_start, close_values = _read_bin(root / "features" / symbol.lower() / "close.day.bin")
            first = rows[0]
            close_series = {first["date"]: 1.0}
            prev_raw = float(first["close"])
            prev_close = 1.0
            for row in rows[1:]:
                date = row["date"]
                raw = float(row["close"]) if row["close"] else float("nan")
                pre = float(row["preclose"]) if row["preclose"] else None
                if not math.isfinite(raw) or raw <= 0 or prev_raw <= 0:
                    close_series[date] = float("nan")
                    continue
                if pre is not None and pre > 0 and abs(pre - prev_raw) / max(abs(prev_raw), 1.0) > 1e-6:
                    close = prev_close * raw / pre
                else:
                    close = prev_close * raw / prev_raw
                close_series[date] = close
                prev_raw = raw
                prev_close = close
            checks = {}
            reference = calendar.index(first["date"]) if first["date"] in calendar else None
            if reference is not None and reference >= close_start:
                checks["reference"] = {
                    "date": first["date"],
                    "provider_close": float(close_values[reference - close_start]),
                    "predicted_close": 1.0,
                }
            for date in (calendar[0], "2005-01-04", "2010-01-04", "2015-01-05", "2020-01-02", calendar[-1]):
                if date not in calendar or date not in close_series:
                    continue
                index = calendar.index(date)
                if index < close_start or index < factor_start:
                    continue
                if index - close_start >= len(close_values) or index - factor_start >= len(factor_values):
                    continue
                predicted_close = close_series[date]
                raw_row = next((row for row in rows if row["date"] == date), None)
                if raw_row is None or not math.isfinite(predicted_close):
                    continue
                raw = float(raw_row["close"])
                predicted_factor = predicted_close / raw
                actual_close = float(close_values[index - close_start])
                actual_factor = float(factor_values[index - factor_start])
                checks[date] = {
                    "provider_close": actual_close,
                    "predicted_close": predicted_close,
                    "close_rel_error": abs(actual_close - predicted_close) / max(abs(actual_close), 1e-12),
                    "provider_factor": actual_factor,
                    "predicted_factor": predicted_factor,
                    "factor_rel_error": abs(actual_factor - predicted_factor) / max(abs(actual_factor), 1e-12),
                }
            results[symbol] = {
                "first_bar": first["date"],
                "checks": checks,
                "max_factor_rel_error": max(
                    (v["factor_rel_error"] for v in checks.values() if "factor_rel_error" in v), default=None
                ),
                "max_close_rel_error": max(
                    (v["close_rel_error"] for v in checks.values() if "close_rel_error" in v), default=None
                ),
            }
    finally:
        bs.logout()
    result = {
        "snapshot": snapshot,
        "calendar_start": calendar[0],
        "calendar_end": calendar[-1],
        "results": results,
        "pandas_version": pd.__version__,
        "timestamp": time.time(),
    }
    out = Path("/probe/baostock_convention") / f"{snapshot}-{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
    probe.commit()
    print(json.dumps(result, indent=2, sort_keys=True)[:12000], flush=True)
    return result


@app.local_entrypoint()
def main(mode: str = "recon", snapshot: str = "2026-11-10", symbols: str = ""):
    if mode == "recon":
        print(recon.remote())
    elif mode == "read_bench":
        print(read_bench.remote(snapshot))
    elif mode == "archive_read_bench":
        print(archive_read_bench.remote(snapshot))
    elif mode == "sample_compare":
        print(sample_compare.remote(snapshot))
    elif mode == "factor_convention":
        print(factor_convention.remote(snapshot, symbols))
    elif mode == "baostock_convention":
        print(baostock_convention.remote(snapshot, symbols))
    else:
        raise SystemExit(f"unknown mode: {mode}")


# ---------------------------------------------------------------------------
# Full provider-build rehearsal in the production image.
#
# The production Volume is mounted read-only.  The probe seals a frozen
# directory into its own Volume and rebuilds the *next* session with the exact
# production build module, then compares rows/bins against the frozen snapshot.
# ---------------------------------------------------------------------------
import os as _os

_os.environ.setdefault("QLIB_DEPLOY_PROFILE", "infi-prod")
from modal_qlib_cn_a10g import image as production_image  # noqa: E402

repro_app = modal.App("qlib-overlay-primary-repro", image=production_image)


def _frozen_membership_provider(snapshot_date: str):
    from data_ingest.model import Membership
    from data_ingest.overlay import active_members, read_ranges
    from data_ingest.manifest import sha256_text

    root = Path("/vol/cn_data_overlay") / snapshot_date / "instruments"
    memberships = {}
    for market in ("csi300", "csi500", "csi1000"):
        ranges = read_ranges(root / f"{market}.txt")
        members = tuple(sorted(active_members(ranges, snapshot_date)))
        memberships[market] = Membership(
            market=market,
            asof=snapshot_date,
            members=members,
            raw_sha256=sha256_text("\n".join(members)),
            source_url=f"probe:frozen:{snapshot_date}",
        )
    return memberships


def _compare_provider(new_root: Path, frozen_root: Path, snapshot_date: str) -> dict:
    import numpy as np

    new_rows = json.loads((new_root / "new_rows.json").read_text())
    old_rows = json.loads((frozen_root / "new_rows.json").read_text())
    symbols = sorted(set(new_rows) & set(old_rows))
    mismatches = []
    for symbol in symbols:
        for date in sorted(set(new_rows[symbol]) & set(old_rows[symbol])):
            left, right = new_rows[symbol][date], old_rows[symbol][date]
            for field in sorted(set(left) | set(right)):
                a, b = left.get(field), right.get(field)
                if a is None and b is None:
                    continue
                if a is None or b is None or not np.isfinite(a) or not np.isfinite(b):
                    if a != b:
                        mismatches.append([symbol, date, field, a, b])
                    continue
                if not np.isclose(a, b, rtol=1e-6, atol=1e-8):
                    mismatches.append([symbol, date, field, a, b])
    field_mismatches = []
    sample = symbols[:40]
    for symbol in sample:
        for field in ("open", "high", "low", "close", "volume", "amount", "factor", "vwap", "change", "adjclose"):
            old_path = frozen_root / "features" / symbol.lower() / f"{field}.day.bin"
            new_path = new_root / "features" / symbol.lower() / f"{field}.day.bin"
            old = np.fromfile(old_path, dtype="<f") if old_path.is_file() else np.array([])
            new = np.fromfile(new_path, dtype="<f") if new_path.is_file() else np.array([])
            if old.shape != new.shape or not np.allclose(old, new, equal_nan=True, rtol=1e-6, atol=1e-8):
                field_mismatches.append([symbol, field, int(old.size), int(new.size)])
    old_calendar = (frozen_root / "calendars" / "day.txt").read_text()
    new_calendar = (new_root / "calendars" / "day.txt").read_text()
    instruments = {}
    for market in ("csi300", "csi500", "csi1000"):
        old_text = (frozen_root / "instruments" / f"{market}.txt").read_text()
        new_text = (new_root / "instruments" / f"{market}.txt").read_text()
        instruments[market] = old_text == new_text
    return {
        "frozen_symbols": len(old_rows),
        "rebuilt_symbols": len(new_rows),
        "common_symbols": len(symbols),
        "row_mismatches": len(mismatches),
        "row_mismatch_sample": mismatches[:10],
        "field_sample": len(sample),
        "field_mismatches": len(field_mismatches),
        "field_mismatch_sample": field_mismatches[:10],
        "calendar_equal": old_calendar == new_calendar,
        "instruments_equal": instruments,
    }


@repro_app.function(
    volumes={
        "/vol": production,
        "/simulation": modal.Volume.from_name("qlib-prod-website-simulation").with_mount_options(read_only=True),
        "/probe": probe,
    },
    cpu=8,
    memory=16384,
    timeout=60 * 60,
    retries=0,
    max_containers=1,
)
def build_repro(snapshot_date: str = "2026-11-10", base_date: str = "2026-11-09") -> dict:
    """Seal the frozen base, rebuild the next session, and compare bins."""
    import time

    from data_ingest.store import load_snapshot, materialize, seal_directory_snapshot
    from provider_build import build_provider_archive

    started = time.monotonic()
    root = Path("/probe/repro-root")
    root.mkdir(parents=True, exist_ok=True)
    seal = seal_directory_snapshot(
        root,
        base_date,
        source_dir=Path("/vol/cn_data_overlay") / base_date,
        commit_fn=probe.commit,
        work_dir="/tmp/repro-seal",
    )
    print(f"[repro] sealed base {base_date} digest={seal['archive']['sha256'][:16]}", flush=True)
    result = build_provider_archive(
        root,
        snapshot_date,
        commit_fn=probe.commit,
        membership_provider=lambda markets: _frozen_membership_provider(snapshot_date),
        extra_symbols=(),
        required_symbols=(),
        require_qlib_smoke=True,
        work_root="/tmp/repro-build",
    )
    print(f"[repro] build result {json.dumps(result, sort_keys=True)[:1000]}", flush=True)
    snapshot = load_snapshot(root, snapshot_date)
    if snapshot is None:
        raise RuntimeError("rebuilt snapshot is not resolvable")
    new_root = materialize(snapshot, "/tmp/repro-new", cache_root="/tmp/repro-cache")
    frozen_root = Path("/vol/cn_data_overlay") / snapshot_date
    comparison = _compare_provider(new_root, frozen_root, snapshot_date)
    report = {
        "status": "COMPARED",
        "snapshot_date": snapshot_date,
        "base_date": base_date,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "seal": seal,
        "build": result,
        "comparison": comparison,
        "probe_volume_root": str(root),
    }
    out = Path("/probe/repro") / f"{snapshot_date}-{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n")
    probe.commit()
    print(json.dumps(comparison, indent=2, sort_keys=True), flush=True)
    return report


@repro_app.local_entrypoint()
def repro_main(snapshot_date: str = "2026-11-10", base_date: str = "2026-11-09"):
    print(build_repro.remote(snapshot_date, base_date))


@repro_app.function(
    volumes={"/vol": production, "/probe": probe},
    cpu=4,
    memory=8192,
    timeout=30 * 60,
    retries=0,
    max_containers=1,
)
def mini_build(snapshot_date: str = "2026-11-10", base_date: str = "2026-11-09", n_symbols: int = 20) -> dict:
    """Bounded real-data rebuild of one session from a frozen archive base."""
    import hashlib
    import shutil
    import time

    import numpy as np
    from provider_archive import safe_extract_provider_archive
    from data_ingest.model import Membership
    from data_ingest.overlay import active_members, read_ranges
    from data_ingest.store import load_snapshot, materialize
    from provider_build import build_provider_archive

    started = time.monotonic()
    archive = Path("/probe/archive_bench") / f"{snapshot_date}.tar.gz"
    if not archive.is_file():
        raise ValueError(f"missing probe archive: {archive}")
    expected = "752edd0f7d6c4f363bc181e3c556ddd88be0eec0698eb36c78b9022380684f6e"
    digest = hashlib.sha256()
    local_archive = Path("/tmp") / "mini-source.tar.gz"
    with archive.open("rb") as source, local_archive.open("wb") as target:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
            target.write(chunk)
    if digest.hexdigest() != expected:
        raise ValueError("probe archive digest changed")
    extracted = Path(safe_extract_provider_archive(local_archive, Path("/tmp/mini-source")))
    full_calendar = [
        line.strip() for line in (extracted / "calendars/day.txt").read_text().splitlines() if line.strip()
    ]
    end_index = full_calendar.index(snapshot_date)

    # Pick a deterministic cross-market sample.
    selected = {}
    for market in ("csi300", "csi500", "csi1000"):
        ranges = read_ranges(extracted / "instruments" / f"{market}.txt")
        members = sorted(active_members(ranges, snapshot_date))
        take = max(1, n_symbols // 3)
        selected[market] = members[:take]
    symbols = sorted({symbol for values in selected.values() for symbol in values})

    root = Path("/probe/mini-root")
    if root.exists():
        shutil.rmtree(root)
    base = root / base_date
    (base / "calendars").mkdir(parents=True)
    (base / "instruments").mkdir(parents=True)
    (base / "calendars" / "day.txt").write_text("\n".join(full_calendar[:end_index]) + "\n")
    selected_set = set(symbols)
    for name in ("csi300.txt", "csi500.txt", "csi1000.txt", "csi800.txt", "all.txt"):
        source = extracted / "instruments" / name
        lines = []
        if source.is_file():
            for line in source.read_text().splitlines():
                if not line.strip():
                    continue
                code, start, end = line.split("\t")
                if code not in selected_set or start > base_date:
                    continue
                lines.append(f"{code}\t{start}\t{min(end, base_date)}")
        (base / "instruments" / name).write_text("\n".join(lines) + ("\n" if lines else ""))
    for symbol in symbols:
        source = extracted / "features" / symbol.lower()
        target = base / "features" / symbol.lower()
        target.mkdir(parents=True)
        for path in source.glob("*.day.bin"):
            data = np.fromfile(path, dtype="<f")
            start_index = int(data[0])
            keep = min(len(data) - 1, end_index - start_index)
            np.hstack([[float(start_index)], data[1 : keep + 1]]).astype("<f").tofile(target / path.name)
    (base / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "PASS",
                "base": {"path": "probe:frozen", "calendar_end": "2026-11-06"},
                "target": {"asof": base_date, "dates": [base_date]},
            },
            indent=2,
        )
        + "\n"
    )

    memberships = {
        market: Membership(
            market=market,
            asof=snapshot_date,
            members=tuple(sorted(selected[market])),
            raw_sha256=hashlib.sha256("\n".join(selected[market]).encode()).hexdigest(),
            source_url=f"probe:frozen:{snapshot_date}",
        )
        for market in selected
    }
    result = build_provider_archive(
        root,
        snapshot_date,
        commit_fn=probe.commit,
        membership_provider=lambda markets: {market: memberships[market] for market in markets},
        extra_symbols=(),
        required_symbols=(),
        require_qlib_smoke=True,
        work_root="/tmp/mini-build",
        max_baostock_symbols=100,
    )
    snapshot = load_snapshot(root, snapshot_date)
    new_root = materialize(snapshot, "/tmp/mini-new", cache_root="/tmp/mini-cache")
    new_rows = json.loads((new_root / "new_rows.json").read_text())
    old_rows = json.loads((extracted / "new_rows.json").read_text())
    mismatches = []
    for symbol in symbols:
        for date in (snapshot_date,):
            left, right = new_rows.get(symbol, {}).get(date), old_rows.get(symbol, {}).get(date)
            if left is None or right is None:
                mismatches.append([symbol, date, "missing", left is not None, right is not None])
                continue
            for field in sorted(set(left) | set(right)):
                a, b = left.get(field), right.get(field)
                if a is None and b is None:
                    continue
                if a is None or b is None or not np.isfinite(a) or not np.isfinite(b):
                    if a != b:
                        mismatches.append([symbol, date, field, a, b])
                    continue
                if not np.isclose(a, b, rtol=1e-6, atol=1e-8):
                    mismatches.append([symbol, date, field, a, b])
    report = {
        "status": "COMPARED",
        "snapshot_date": snapshot_date,
        "base_date": base_date,
        "symbols": symbols,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "build": result,
        "row_mismatches": len(mismatches),
        "row_mismatch_sample": mismatches[:20],
    }
    out = Path("/probe/mini") / f"{snapshot_date}-{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n")
    probe.commit()
    print(json.dumps(report, indent=2, sort_keys=True)[:12000], flush=True)
    return report


@repro_app.function(
    volumes={"/vol": production, "/probe": probe},
    cpu=8,
    memory=16384,
    timeout=45 * 60,
    retries=0,
    max_containers=1,
)
def short_window_probe(snapshot_date: str = "2026-11-10", model_id: str = "3529e5234cec6dedd7c7c3c7") -> dict:
    """Compare cached-model predictions over the full vs bounded feature window."""
    import hashlib
    import pickle
    import time

    import pandas as pd
    import qlib
    from qlib.data.dataset import DatasetH
    from qlib.data.dataset.handler import DataHandler
    from qlib.utils import init_instance_by_config
    from data_ingest.store import ProviderSnapshot, materialize
    from modal_qlib_cn_a10g import MODEL_CFG, _load_and_patch_cfg

    archive = Path("/probe/archive_bench") / f"{snapshot_date}.tar.gz"
    digest = hashlib.sha256()
    with archive.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    size = archive.stat().st_size
    snapshot = ProviderSnapshot(
        kind="archive",
        date=snapshot_date,
        path=archive,
        digest=digest.hexdigest(),
        calendar_start="2000-01-04",
        calendar_end=snapshot_date,
        calendar_sha256="0" * 64,
        manifest_sha256="",
        content_sha256=None,
        size=size,
        receipt=None,
        receipt_path=None,
        manifest={},
    )
    provider = materialize(snapshot, "/tmp/short-new", cache_root="/tmp/short-cache")
    calendar = (provider / "calendars/day.txt").read_text().strip().splitlines()
    asof = calendar[-1]
    qlib.init(
        provider_uri=str(provider),
        region="cn",
        exp_manager={
            "class": "MLflowExpManager",
            "module_path": "qlib.workflow.expm",
            "kwargs": {"uri": "file:/tmp/short-mlruns", "default_exp_name": "short-window-probe"},
        },
    )
    model_path = Path("/vol/live_models") / f"{model_id}.pkl"
    meta_path = Path("/vol/live_models") / f"{model_id}.json"
    model = pickle.loads(model_path.read_bytes())
    meta = json.loads(meta_path.read_text())
    cfg = _load_and_patch_cfg(
        MODEL_CFG["lgb158"],
        smoke=False,
        recent=True,
        long_train=True,
        label20=True,
        topk=10,
        nd=1,
        market="csi1000",
        provider_dir=str(provider),
        csi1000_profile="c98856b4_top10_drop1",
    )
    handler = cfg["task"]["dataset"]["kwargs"]["handler"]["kwargs"]
    long_start = handler["start_time"]
    index = calendar.index(asof)
    short_start = calendar[max(0, index - 180)]
    results = {}
    predictions = {}
    for label, start in (("long", long_start), ("short", short_start)):
        light = __import__("copy").deepcopy(cfg)
        opts = light["task"]["dataset"]["kwargs"]
        opts["segments"] = {"test": [asof, asof]}
        opts["handler"]["kwargs"]["start_time"] = start
        opts["handler"]["kwargs"]["end_time"] = asof
        started = time.monotonic()
        handler_obj = init_instance_by_config(opts["handler"], accept_types=DataHandler)
        dataset = DatasetH(handler=handler_obj, segments=opts["segments"])
        pred = model.predict(dataset, segment="test")
        if isinstance(pred, pd.DataFrame):
            pred = pred.iloc[:, 0]
        pred = pred.sort_index()
        elapsed = time.monotonic() - started
        results[label] = {"start": start, "seconds": round(elapsed, 3), "rows": int(pred.size)}
        predictions[label] = pred
    long_pred, short_pred = predictions["long"], predictions["short"]
    exact = long_pred.equals(short_pred)
    diff = None
    if set(long_pred.index) == set(short_pred.index):
        delta = (long_pred - short_pred).abs()
        diff = {"max_abs": float(delta.max()), "mean_abs": float(delta.mean())}
    report = {
        "status": "COMPARED",
        "snapshot_date": snapshot_date,
        "model_id": model_id,
        "fit_asof": meta.get("fit_asof"),
        "asof": asof,
        "long_start": long_start,
        "short_start": short_start,
        "exact_equal": bool(exact),
        "index_equal": bool(long_pred.index.equals(short_pred.index)),
        "diff": diff,
        "runs": results,
    }
    if exact:
        pd.testing.assert_series_equal(long_pred, short_pred, check_exact=True, check_names=False)
    out = Path("/probe/short_window") / f"{snapshot_date}-{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n")
    probe.commit()
    print(json.dumps(report, indent=2, sort_keys=True), flush=True)
    return report


@repro_app.function(
    volumes={"/vol": production, "/probe": probe},
    cpu=8,
    memory=8192,
    timeout=30 * 60,
    retries=0,
    max_containers=1,
)
def seal_bench(snapshot_date: str = "2026-11-10") -> dict:
    """Measure the one-time parallel seal of a legacy provider directory."""
    import time

    from data_ingest.store import load_snapshot, seal_directory_snapshot

    root = Path("/probe/seal-root")
    started = time.monotonic()
    receipt = seal_directory_snapshot(
        root,
        snapshot_date,
        source_dir=Path("/vol/cn_data_overlay") / snapshot_date,
        commit_fn=probe.commit,
        work_dir="/tmp/seal-bench",
    )
    elapsed = time.monotonic() - started
    snapshot = load_snapshot(root, snapshot_date)
    report = {
        "status": "SEALED",
        "snapshot_date": snapshot_date,
        "elapsed_seconds": round(elapsed, 3),
        "receipt": receipt,
        "resolved": snapshot is not None,
        "resolved_digest": snapshot.digest if snapshot else None,
    }
    out = Path("/probe/seal") / f"{snapshot_date}-{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n")
    probe.commit()
    print(
        json.dumps({k: report[k] for k in ("status", "elapsed_seconds", "resolved", "resolved_digest")}, indent=2),
        flush=True,
    )
    return report
