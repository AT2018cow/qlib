"""Repair one missing benchmark close in a separate, versioned provider.

Only official CSIndex evidence with matching neighbouring raw closes is
accepted. Existing prices, stock data and the original provider stay intact.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import os
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quant_bootstrap import ensure_quant_import_paths

ensure_quant_import_paths()
import artifact_io as json
import numpy as np


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_and_patch_close(calendar, closes, factors, response, *, day, index_code="000852"):
    if response.get("success") is not True or str(response.get("code")) != "200":
        raise ValueError("official source did not report success")
    quotes = {}
    for row in response.get("data", []):
        if row.get("indexCode") != index_code or row.get("tradeDate") in quotes:
            raise ValueError("wrong index or duplicate official date")
        value = float(row["close"])
        if not math.isfinite(value) or value <= 0:
            raise ValueError("invalid official close")
        quotes[row["tradeDate"]] = value
    i = calendar.index(day)
    start = int(closes[0])
    offset = i - start + 1
    if not 1 < offset < len(closes) - 1 or math.isfinite(float(closes[offset])):
        raise ValueError("only an interior missing close may be repaired")
    factor_start = int(factors[0])
    factor = float(factors[i - factor_start + 1])
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError("observed normalization factor required")
    for neighbor_i in (i - 1, i + 1):
        date = calendar[neighbor_i]
        p = float(closes[neighbor_i - start + 1])
        f = float(factors[neighbor_i - factor_start + 1])
        official = quotes.get(date.replace("-", ""))
        if official is None or not math.isfinite(p) or not math.isfinite(f) or f <= 0 or abs(p / f - official) > 0.01:
            raise ValueError("official neighbours do not match provider normalization")
    key = day.replace("-", "")
    if key not in quotes:
        raise ValueError("target date absent from official evidence")
    patched = closes.copy()
    patched[offset] = np.float32(quotes[key] * factor)
    return patched, {
        "date": day,
        "raw_close": quotes[key],
        "observed_factor": factor,
        "normalized_close": float(patched[offset]),
        "field": "close",
        "index": index_code,
    }


def build_overlay(source, output, manifest, evidence_path, provenance_path, *, day):
    source, output = Path(source), Path(output)
    raw = Path(evidence_path)
    provenance = json.loads(Path(provenance_path).read_text())
    url = urlparse(provenance["url"])
    if (
        url.scheme != "https"
        or url.netloc != "www.csindex.com.cn"
        or url.path != "/csindex-home/perf/index-perf"
        or parse_qs(url.query).get("indexCode") != ["000852"]
        or provenance.get("status") != 200
        or sha(raw) != provenance.get("sha256")
    ):
        raise ValueError("official retrieval identity/hash mismatch")
    calendar = (source / "calendars/day.txt").read_text().splitlines()
    relative = "features/sh000852/close.day.bin"
    old_path = source / relative
    closes = np.fromfile(old_path, dtype="<f4")
    factors = np.fromfile(source / "features/sh000852/factor.day.bin", dtype="<f4")
    patched, detail = validate_and_patch_close(calendar, closes, factors, json.loads(raw.read_text()), day=day)
    if output.exists():
        raise ValueError("overlay output must be new")
    for rel, digest in manifest["files"].items():
        path = Path(rel)
        if path.is_absolute() or ".." in path.parts or sha(source / path) != digest:
            raise ValueError("source manifest changed or contains an unsafe path")
    output.mkdir(parents=True)
    for rel in manifest["files"]:
        target = output / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        os.link(source / rel, target)
    target = output / relative
    # Detach this one hard link before writing; never mutate the frozen original.
    target.unlink()
    patched.astype("<f4").tofile(target)
    after = dict(manifest["files"])
    after[relative] = sha(target)
    receipt = {
        "status": "PATCHED_FROM_OFFICIAL_EVIDENCE",
        "patch": detail,
        "before_sha256": manifest["files"][relative],
        "after_sha256": after[relative],
        "evidence": provenance,
        "stock_files_unchanged": True,
        "original_provider_unchanged": True,
        "changed_files": [relative],
        "files": after,
    }
    if sha(old_path) != manifest["files"][relative]:
        raise RuntimeError("original provider unexpectedly changed")
    (output / "benchmark-overlay-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--provenance", required=True, type=Path)
    parser.add_argument("--date", required=True)
    args = parser.parse_args()
    result = build_overlay(
        args.source, args.output, json.loads(args.manifest.read_text()), args.evidence, args.provenance, day=args.date
    )
    print(json.dumps({k: v for k, v in result.items() if k != "files"}, indent=2))


if __name__ == "__main__":
    main()
