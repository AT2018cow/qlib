"""Bounded Modal production-image and I/O smoke; no production publication.

Choose the authorized profile outside this file and keep the CLI attached.
Bulk replay uses prelive_local.py until cloud bulk-I/O failures are resolved.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if HERE == Path("/root"):
    # Load compiled Qlib before bootstrap adds its uncompiled source checkout.
    import qlib

    HERE = Path("/root/qlib/quant/code/research/regime_reselection")
sys.path.insert(0, str(HERE.parents[1]))
from quant_bootstrap import ensure_quant_import_paths

ensure_quant_import_paths()
import modal
from modal_qlib_cn_a10g import image

app = modal.App("qlib-prelive-revalidation", image=image)
source = modal.Volume.from_name("qlib-cn-data", create_if_missing=False)
evidence = modal.Volume.from_name("qlib-prelive-revalidation", create_if_missing=True)
MOUNTS = {"/inputs": source.with_mount_options(read_only=True), "/evidence": evidence}


@app.function(volumes=MOUNTS, cpu=1, memory=2048, timeout=120, retries=0, scaledown_window=5)
def io_smoke():
    import faulthandler
    import hashlib
    import io
    import pandas as pd

    faulthandler.dump_traceback_later(30, repeat=True)
    try:
        p = Path("/inputs/cn_data/features/sh600000/close.day.bin")
        print("[io-smoke] reading raw feature", flush=True)
        raw = p.read_bytes()
        p = Path("/inputs/regime_reselection/spines/regime_g2_csi300_20261102_a1/csi300/B/signal.parquet")
        print("[io-smoke] reading frozen prediction bytes", flush=True)
        payload = p.read_bytes()
        frame = pd.read_parquet(io.BytesIO(payload))
        print(f"[io-smoke] parsed {len(frame)} prediction rows", flush=True)
        probe = Path("/evidence/io-smoke/feature-copy.bin")
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(raw)
        if probe.read_bytes() != raw:
            raise ValueError("isolated evidence volume read/write mismatch")
        evidence.commit()
        print("[io-smoke] evidence read/write/commit passed", flush=True)
        return {
            "status": "PASS",
            "feature_bytes": len(raw),
            "signal_sha256": hashlib.sha256(payload).hexdigest(),
            "signal_rows": len(frame),
            "evidence_write_verified": True,
            "production_mutation": False,
        }
    finally:
        faulthandler.cancel_dump_traceback_later()


@app.function(volumes=MOUNTS, cpu=1, memory=2048, timeout=180, retries=0, scaledown_window=5)
def inspect_environment():
    import importlib.metadata
    import hashlib

    sys.path.insert(0, "/root/qlib/quant/code")
    from quant_bootstrap import ensure_quant_import_paths

    ensure_quant_import_paths()
    names = (
        "paper_portfolio",
        "paper_execution_context",
        "paper_accounting",
        "coordinate_contract",
        "order_quantity",
        "portfolio_diagnostics",
        "publication_journal",
        "signal_publication_gate",
    )
    imports = {name: hashlib.sha256(Path(__import__(name).__file__).read_bytes()).hexdigest() for name in names}
    root = Path("/inputs")
    calendar = (root / "cn_data/calendars/day.txt").read_text().splitlines()
    result = {
        "status": "PASS_IMPORTS",
        "calendar_start": calendar[0],
        "calendar_end": calendar[-1],
        "imports": imports,
        "python": sys.version.split()[0],
        "packages": {name: importlib.metadata.version(name) for name in ("pyqlib", "numpy", "pandas", "lightgbm")},
        "paper_state_directory_present": (root / "paper_portfolio").is_dir(),
        "source_mounted_read_only": True,
        "production_mutation": False,
    }
    for field, pattern in (("dated_st_candidates", "**/*st*snapshot*.json"), ("halt_candidates", "**/*suspend*.json")):
        result[field] = [
            str(p.relative_to(root)) for sub in ("signals", "live_models") for p in (root / sub).glob(pattern)
        ][:30]
    print(
        f"[inspect] imports passed, source calendar ends {calendar[-1]}, paper state present={result['paper_state_directory_present']}",
        flush=True,
    )
    return result


@app.local_entrypoint()
def main(run_id: str, mode: str = "io", output: str = "/tmp/quant-prelive-modal"):
    import json
    import time

    if not run_id or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in run_id):
        raise ValueError("invalid run identifier")
    if mode not in ("inspect", "io"):
        raise ValueError("bulk cloud replay unavailable after observed worker failures; use the local bounded runner")
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    started = time.time()
    env = inspect_environment.remote()
    (out / f"{run_id}_environment.json").write_text(json.dumps(env, indent=2) + "\n")
    if mode == "io":
        result = io_smoke.remote()
        result["wall_seconds_including_inspection"] = time.time() - started
        (out / f"{run_id}_io.json").write_text(json.dumps(result, indent=2) + "\n")
