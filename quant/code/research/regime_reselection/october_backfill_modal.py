"""Isolated October reconstruction using fresh public data and causal fits.

Run smoke first, then full, in an attached foreground Modal session. Output is
research evidence on an independent volume; production state is never written.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if HERE == Path("/root"):
    import qlib

    HERE = Path("/root/qlib/quant/code/research/regime_reselection")
sys.path.insert(0, str(HERE.parents[1]))
from quant_bootstrap import ensure_quant_import_paths

ensure_quant_import_paths()
sys.path.insert(0, str(HERE))
from research_identity import (
    SOURCE_IDENTITY_VERSION,
    matching_fit_receipt,
    matching_training_smoke,
    source_manifest,
    verify_source_manifest,
)

import modal
from modal_qlib_cn_a10g import image

app = modal.App("qlib-october-corrected-backfill", image=image)
evidence = modal.Volume.from_name("qlib-prelive-revalidation", create_if_missing=True)
SPEC = {
    "csi300": {"profile": "59b93929_top10_drop1_10d", "horizon": 10},
    "csi500": {"profile": "raw10d_c98856b4_top20_drop2_20d", "horizon": 20},
    "csi1000": {"profile": "c98856b4_top10_drop1", "horizon": 20},
}


def download_provider(asset):
    import hashlib
    import requests
    import json
    from provider_archive import safe_extract_provider_archive

    if asset.get("local_path"):
        local = Path(asset["local_path"])
        if not (local / "calendars" / "day.txt").is_file():
            raise ValueError(f"local provider is incomplete: {local}")
        return local

    root = Path("/tmp") / f"october-provider-{asset['digest'].split(':',1)[1][:16]}"
    marker = root / "ready.json"
    if marker.is_file():
        receipt = json.loads(marker.read_text())
        if receipt.get("digest") != asset["digest"]:
            raise ValueError("provider scratch cache identity mismatch")
        existing = root / receipt["relative_root"]
        if not (existing / "calendars/day.txt").is_file():
            raise ValueError("provider scratch cache incomplete")
        return existing
    archive = root.with_suffix(".tar.gz")
    digest = hashlib.sha256()
    written = 0
    mark = 0
    with requests.get(asset["url"], stream=True, timeout=(30, 180)) as response:
        response.raise_for_status()
        with archive.open("wb") as stream:
            for chunk in response.iter_content(8 * 1024 * 1024):
                digest.update(chunk)
                stream.write(chunk)
                written += len(chunk)
                if written // (64 * 1024 * 1024) > mark:
                    mark = written // (64 * 1024 * 1024)
                    print(f"[provider] downloaded {written//1024**2}MiB", flush=True)
    if written != asset["size"] or "sha256:" + digest.hexdigest() != asset["digest"]:
        raise ValueError("public release size/SHA256 mismatch")
    print("[provider] digest passed; extracting to container scratch", flush=True)
    extracted = Path(safe_extract_provider_archive(archive, root))
    marker.write_text(json.dumps({"digest": asset["digest"], "relative_root": str(extracted.relative_to(root))}))
    return extracted


@app.function(
    volumes={"/evidence": evidence}, cpu=20, memory=32768, timeout=3600, retries=0, max_containers=3, scaledown_window=5
)
def build_market(job):
    import copy
    import hashlib
    import io
    import json
    import os
    import resource
    import time
    import numpy as np
    import pandas as pd
    import qlib
    from qlib.data.dataset import Dataset
    from qlib.model.base import Model
    from qlib.utils import init_instance_by_config
    from modal_qlib_cn_a10g import _load_and_patch_cfg, MODEL_CFG
    from csi1000_production_config import profile_manifest, stable_json_sha256
    from qlib_live_retrain import configure_asof
    from october_backfill_plan import fit_blocks

    started = time.time()
    if verify_source_manifest(job["source_manifest"], "training") != job["source_sha256"]:
        raise ValueError("training source identity mismatch")
    market = job["market"]
    spec = SPEC[market]
    root = Path("/evidence/october_backfill") / job["run_id"] / market
    root.mkdir(parents=True, exist_ok=True)
    if (root / "complete.json").exists():
        raise ValueError("completed output is immutable; choose a new run ID")
    (root / f"{job['mode']}_started.json").write_text(json.dumps({"started_epoch": started, "job": job}))
    evidence.commit()
    provider = download_provider(job["asset"])
    calendar = (provider / "calendars/day.txt").read_text().splitlines()
    end = min(job["end"], calendar[-1])
    if end != job["end"]:
        raise ValueError(f"provider ends {calendar[-1]} before requested {job['end']}")
    blocks = fit_blocks(calendar, job["first_execution"], end)
    if blocks[0]["fit_asof"] != "2026-09-30":
        raise ValueError("October 8 execution must use the September 30 signal")
    if len(blocks) > 3:
        raise ValueError("bounded reconstruction expected at most three fits per market")
    if job["mode"] == "smoke":
        blocks = blocks[:1]
    print(
        f"[backfill] {market} provider_end={calendar[-1]} blocks={[(b['fit_asof'],b['signal_end']) for b in blocks]}",
        flush=True,
    )
    qlib.init(
        provider_uri=str(provider),
        region="cn",
        kernels=8,
        exp_manager={
            "class": "MLflowExpManager",
            "module_path": "qlib.workflow.expm",
            "kwargs": {"uri": "file:/tmp/october-mlruns", "default_exp_name": "october-corrected-backfill"},
        },
    )
    frames = []
    receipts = []
    for block in blocks:
        block_dir = root / block["fit_asof"]
        block_dir.mkdir(parents=True, exist_ok=True)
        receipt_path = block_dir / "receipt.json"
        signal_path = block_dir / "prediction.parquet"
        if receipt_path.is_file() and signal_path.is_file():
            old = json.loads(receipt_path.read_text())
            verify_source_manifest(job["source_manifest"], "training")
            if matching_fit_receipt(
                old, job, block, spec["profile"], hashlib.sha256(signal_path.read_bytes()).hexdigest()
            ):
                frames.append(pd.read_parquet(io.BytesIO(signal_path.read_bytes())))
                receipts.append(old)
                print(f"[backfill] {market} reusing smoke fit {block['fit_asof']}", flush=True)
                continue
            raise ValueError("prior fit receipt does not match frozen input/code")
        cfg = _load_and_patch_cfg(
            MODEL_CFG["lgb158"],
            smoke=False,
            recent=True,
            long_train=True,
            label20=(spec["horizon"] == 20),
            market=market,
            provider_dir=str(provider),
            csi1000_profile=spec["profile"],
        )
        snapshot = configure_asof(
            cfg, calendar[: calendar.index(block["fit_asof"]) + 1], block["fit_asof"], horizon=spec["horizon"]
        )
        model_sha = stable_json_sha256(cfg["task"]["model"])
        if model_sha != profile_manifest(spec["profile"])["model_config_sha256"]:
            raise ValueError("production model configuration fingerprint changed")
        prefix_cfg = copy.deepcopy(cfg)
        dk = cfg["task"]["dataset"]["kwargs"]
        dk["segments"]["test"] = [block["fit_asof"], block["signal_end"]]
        dk["handler"]["kwargs"]["end_time"] = block["signal_end"]
        t = time.time()
        print(
            f"[fit] {market} asof={block['fit_asof']} train_end={snapshot['train_end']} valid_end={snapshot['valid_end']} threads=20",
            flush=True,
        )
        dataset = init_instance_by_config(cfg["task"]["dataset"], accept_types=Dataset)
        model = init_instance_by_config(cfg["task"]["model"], accept_types=Model)
        model.fit(dataset)
        pred = model.predict(dataset, segment="test")
        if isinstance(pred, pd.DataFrame):
            pred = pred.iloc[:, 0]
        pred = pred.sort_index()
        days = sorted(str(d)[:10] for d in pred.index.get_level_values(0).unique())
        if (
            days != block["signal_days"]
            or pred.index.has_duplicates
            or not np.isfinite(pred.to_numpy(dtype=float)).all()
        ):
            raise ValueError("prediction dates/uniqueness/finiteness mismatch")
        prefix_equal = None
        if job["mode"] == "smoke":
            # Prove extending the prediction interval cannot alter the first day's features/scores.
            prefix_ds = init_instance_by_config(prefix_cfg["task"]["dataset"], accept_types=Dataset)
            prefix = model.predict(prefix_ds, segment="test")
            if isinstance(prefix, pd.DataFrame):
                prefix = prefix.iloc[:, 0]
            actual = pred.loc[[pd.Timestamp(block["fit_asof"])]]
            pd.testing.assert_series_equal(
                actual.sort_index(), prefix.sort_index(), check_exact=True, check_names=False
            )
            prefix_equal = True
        buffer = io.BytesIO()
        pred.to_frame("score").to_parquet(buffer)
        payload = buffer.getvalue()
        signal_path.write_bytes(payload)
        receipt = {
            "status": "PASS",
            "market": market,
            "profile": spec["profile"],
            "label_horizon": spec["horizon"],
            "fit_asof": block["fit_asof"],
            "signal_end": block["signal_end"],
            "signal_days": days,
            "rows": len(pred),
            "training_contract": snapshot,
            "model_config_sha256": model_sha,
            "asset_digest": job["asset"]["digest"],
            "engine_source_sha256": job["source_sha256"],
            "source_identity_version": SOURCE_IDENTITY_VERSION,
            "source_manifest_sha256": job["source_manifest"]["sha256"],
            "prediction_sha256": hashlib.sha256(payload).hexdigest(),
            "prefix_prediction_exact": prefix_equal,
            "fit_wall_seconds": time.time() - t,
            "max_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
            "evaluation_mode": "BACKFILL_RESEARCH",
            "promotion_eligible": False,
        }
        verify_source_manifest(job["source_manifest"], "training")
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        evidence.commit()
        frames.append(pred.to_frame("score"))
        receipts.append(receipt)
        print(f"[fit] PASS {market} {block['fit_asof']} {len(pred)} scores seconds={time.time()-t:.1f}", flush=True)
    merged = pd.concat(frames).sort_index()
    expected_days = [day for block in blocks for day in block["signal_days"]]
    actual_days = sorted(str(day)[:10] for day in merged.index.get_level_values(0).unique())
    if merged.index.has_duplicates or actual_days != expected_days:
        raise ValueError("assembled signal dates differ from the frozen causal plan")
    buf = io.BytesIO()
    merged.to_parquet(buf)
    payload = buf.getvalue()
    (root / "signal.parquet").write_bytes(payload)
    result = {
        "status": "SMOKE_PASS" if job["mode"] == "smoke" else "COMPLETED",
        "market": market,
        "source_sha256": job["source_sha256"],
        "source_identity_version": SOURCE_IDENTITY_VERSION,
        "source_manifest": job["source_manifest"],
        "driver_sha256": job["driver_sha256"],
        "asset_digest": job["asset"]["digest"],
        "first_execution": job["first_execution"],
        "last_signal": str(merged.index.get_level_values(0).max())[:10],
        "provider_calendar_end": calendar[-1],
        "signal_sha256": hashlib.sha256(payload).hexdigest(),
        "fits": receipts,
        "wall_seconds": time.time() - started,
        "max_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        "production_mutation": False,
    }
    verify_source_manifest(job["source_manifest"], "training")
    (root / ("smoke.json" if job["mode"] == "smoke" else "complete.json")).write_text(
        json.dumps(result, indent=2) + "\n"
    )
    evidence.commit()
    return result


@app.local_entrypoint()
def main(run_id: str, release_file: str, output: str, mode: str = "smoke", end: str = "2026-11-04"):
    import hashlib
    import json

    if not run_id or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in run_id):
        raise ValueError("invalid run ID")
    if mode not in ("smoke", "full"):
        raise ValueError("smoke or full required")
    release = json.loads(Path(release_file).read_text())
    asset = release["assets"][0]
    if not asset.get("digest", "").startswith("sha256:"):
        raise ValueError("pinned public asset digest required")
    implementation = source_manifest("training")
    source_sha = implementation["sha256"]
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    common = {
        "asset": asset,
        "first_execution": "2026-10-08",
        "source_manifest": implementation,
        "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    if mode == "full":
        smoke = json.loads((root / f"{run_id}_training_smoke.json").read_text())
        if not matching_training_smoke(smoke, common):
            raise ValueError("matching successful training smoke required")
    markets = ["csi300"] if mode == "smoke" else list(SPEC)
    jobs = [
        {
            "run_id": run_id,
            "mode": mode,
            "market": m,
            **common,
            "end": end,
            "source_sha256": source_sha,
        }
        for m in markets
    ]
    results = []
    errors = []
    for result in build_market.map(jobs, order_outputs=False, return_exceptions=True):
        if isinstance(result, BaseException):
            errors.append(repr(result))
            print(f"[batch] FAILED {result!r}", flush=True)
        else:
            results.append(result)
            print(
                f"[batch] {result['market']} {result['status']} seconds={result['wall_seconds']:.1f} RSS={result['max_rss_mb']:.0f}MiB",
                flush=True,
            )
        (root / f"{run_id}_{mode}_progress.json").write_text(
            json.dumps({"completed": len(results), "expected": len(jobs), "errors": errors}, indent=2)
        )
    if errors or len(results) != len(jobs):
        raise RuntimeError("incomplete bounded backfill; inspect immutable fit receipts")
    if mode == "smoke":
        (root / f"{run_id}_training_smoke.json").write_text(json.dumps(results[0], indent=2) + "\n")
    else:
        (root / f"{run_id}_training_results.json").write_text(json.dumps(results, indent=2) + "\n")
