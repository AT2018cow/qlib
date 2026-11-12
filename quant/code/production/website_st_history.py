"""Dated ST history for the website causal account.

The website account is rebuilt from 2026-10-08.  A current ST snapshot is not a
historical input: using today's list for a past session would be look-ahead.
This module keeps a small, append-only date -> ST-symbol map.  The 2026-11-11
seed was verified from BaoStock ``isST`` for the 131-name active universe; each
new session appends the verified daily snapshot (or fails closed).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import date
from pathlib import Path

from st_history import validate_st_history

SCHEMA_VERSION = 1
REPO_ROOT = Path(__file__).resolve().parents[3]
SEED_PATH = REPO_ROOT / "quant" / "data" / "st_history" / "seed.json"


def _canonical(history: dict) -> dict:
    dates = {str(day): sorted(set(map(str, symbols))) for day, symbols in (history.get("dates") or {}).items()}
    return {"schema_version": SCHEMA_VERSION, "dates": dict(sorted(dates.items()))}


def _read(path: Path) -> dict:
    payload = json.loads(Path(path).read_text())
    validate_st_history(payload)
    return payload


def load_seed_history(path: str | Path | None = None) -> dict:
    seed = Path(path) if path is not None else Path(os.environ.get("QUANT_ST_HISTORY_SEED", SEED_PATH))
    if not seed.is_file():
        raise ValueError(f"missing ST history seed: {seed}")
    return _read(seed)


def merge_history(*histories: dict) -> dict:
    """Merge append-only histories; the same date must have the same list."""
    dates: dict[str, list[str]] = {}
    evidence: dict[str, dict] = {}
    for history in histories:
        if not history:
            continue
        validate_st_history(history)
        for day, symbols in history["dates"].items():
            current = dates.get(day)
            if current is not None and sorted(current) != sorted(symbols):
                raise ValueError(f"conflicting ST history for {day}")
            dates[day] = sorted(set(map(str, symbols)))
        for day, item in (history.get("evidence") or {}).items():
            if isinstance(item, dict):
                evidence[day] = dict(item)
    if not dates:
        raise ValueError("empty ST history")
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "merged:baostock+verified-daily-snapshot",
        "dates": dict(sorted(dates.items())),
        "evidence": dict(sorted(evidence.items())),
    }


def record_snapshot(history: dict, snapshot: dict) -> dict:
    """Append one verified execution-date snapshot; reject rewrites."""
    day = str((snapshot or {}).get("date") or "")[:10]
    if not day:
        raise ValueError("ST snapshot has no date")
    date.fromisoformat(day)
    symbols = (snapshot or {}).get("symbols")
    if not isinstance(symbols, list) or any(not isinstance(item, str) for item in symbols):
        raise ValueError("ST snapshot symbols must be a list")
    source = str((snapshot or {}).get("source") or "").strip()
    if not source:
        raise ValueError("ST snapshot has no source")
    dates = {str(key): list(value) for key, value in (history.get("dates") or {}).items()}
    current = dates.get(day)
    normalized = sorted({str(item).upper() for item in symbols})
    if current is not None and sorted(current) != normalized:
        raise ValueError(f"ST snapshot conflicts with frozen history for {day}")
    dates[day] = normalized
    evidence = {str(key): dict(value) for key, value in (history.get("evidence") or {}).items()}
    evidence[day] = {
        "source": source,
        "fetched_at_cst": str((snapshot or {}).get("fetched_at_cst") or ""),
    }
    payload = {
        "schema_version": SCHEMA_VERSION,
        "source": "merged:baostock+verified-daily-snapshot",
        "dates": dict(sorted(dates.items())),
        "evidence": dict(sorted(evidence.items())),
    }
    validate_st_history(payload)
    return payload


def history_sha256(history: dict) -> str:
    body = _canonical(history)
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def prefix_sha256(history: dict, end_date: str) -> str:
    end = str(end_date)[:10]
    body = _canonical(history)
    body["dates"] = {day: values for day, values in body["dates"].items() if day <= end}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def write_history(path: str | Path, history: dict) -> None:
    """Atomically persist an append-only history."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(history, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    handle, temporary = tempfile.mkstemp(prefix=target.name + ".", dir=target.parent)
    try:
        with os.fdopen(handle, "w") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def persisted_path(simulation_root: str | Path, market: str) -> Path:
    return Path(simulation_root) / "st_history" / f"{market}.json"


def load_merged_history(simulation_root: str | Path, market: str, seed_path: str | Path | None = None) -> dict:
    seed = load_seed_history(seed_path)
    path = persisted_path(simulation_root, market)
    if path.is_file():
        return merge_history(seed, _read(path))
    return merge_history(seed)
