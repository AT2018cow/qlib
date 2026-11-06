"""Bounded local fallback for the exact same registered paper-engine replay."""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from quant_bootstrap import ensure_quant_import_paths

ensure_quant_import_paths()
from research_identity import (
    SOURCE_IDENTITY_VERSION,
    provider_contract,
    source_manifest,
    verify_prepared_provenance,
    verify_provider,
    verify_source_manifest,
)

_DATA_CACHE = None


def execute(job):
    global _DATA_CACHE
    for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[key] = "1"
    from prelive_revalidation import prepare_data, replay, write_result, file_sha
    import resource

    started = time.time()
    root = Path(job["output_root"]) / job["run_id"] / job["key"]
    root.mkdir(parents=True, exist_ok=False)
    (root / "started.json").write_text(json.dumps({"started_epoch": started, "key": job["key"]}))
    try:
        if [file_sha(p) for p in job["signals"]] != job["expected_signal_sha256"]:
            raise ValueError("signal differs from registered frozen source")
        if verify_source_manifest(job["source_manifest"], "replay") != job["engine_sha256"]:
            raise ValueError("execution source identity mismatch")
        provider_sha = verify_provider(job["provider"], job["provider_contract"])
        data_key = (
            job["provider"],
            provider_sha,
            job["engine_sha256"],
            tuple(job["expected_signal_sha256"]),
            job["candidate"]["market"],
            job["start"],
            job["end"],
        )
        reused = _DATA_CACHE is not None and _DATA_CACHE[0] == data_key
        if not reused:
            import gc

            _DATA_CACHE = None
            gc.collect()
            _DATA_CACHE = (
                data_key,
                prepare_data(job["provider"], job["signals"], job["candidate"]["market"], job["start"], job["end"]),
            )
        data = _DATA_CACHE[1]
        verify_prepared_provenance(data["provenance"], job["provider_contract"])
        (root / "inputs.json").write_text(json.dumps(data["provenance"], indent=2) + "\n")
        metrics = []
        for bps in job["slippage"]:
            result = replay(data, job["candidate"], account=job["account"], slippage_bps=bps)
            write_result(result, root / f"extra_{bps:g}bp")
            metrics.append(result["metrics"])
        verify_source_manifest(job["source_manifest"], "replay")
        verify_provider(job["provider"], job["provider_contract"])
        receipt = {
            "status": "COMPLETED",
            "key": job["key"],
            "metrics": metrics,
            "wall_seconds": time.time() - started,
            "max_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
            "engine_sha256": job["engine_sha256"],
            "source_identity_version": SOURCE_IDENTITY_VERSION,
            "source_manifest": job["source_manifest"],
            "provider_contract_sha256": provider_sha,
            "prepared_data_reused": reused,
            "register_sha256": job["register_sha256"],
            "files": {str(p.relative_to(root)): file_sha(p) for p in root.rglob("*") if p.is_file()},
        }
        (root / "receipt.json").write_text(json.dumps(receipt, indent=2, allow_nan=False) + "\n")
        return receipt
    except Exception as exc:
        (root / "failed.json").write_text(json.dumps({"error": repr(exc), "wall_seconds": time.time() - started}))
        raise


def build_jobs(args, register):
    jobs = []
    implementation = source_manifest("replay")
    engine_sha = implementation["sha256"]
    register_sha = hashlib.sha256(args.register.read_bytes()).hexdigest()
    provider = provider_contract(register, args.provider_manifest, args.provider_amendment)
    verify_provider(args.provider, provider)
    for candidate in register["candidates"]:
        market, spine = candidate["market"], candidate["spine"]
        if args.mode == "smoke" and not candidate["incumbent"]:
            continue
        if args.mode == "extension" and spine != {"csi300": "B", "csi500": "B", "csi1000": "A"}[market]:
            continue
        if args.mode == "phases" and market != "csi1000":
            continue
        phases = [0, 4, 6, 15] if args.mode == "phases" else [None]
        for phase in phases:
            paths = [args.signals / market / spine / "signal.parquet"]
            hashes = [register["frozen_signal_sha256"][f"primary/{market}/{spine}"]]
            start, end = "2018-01-02", "2024-12-31"
            if args.mode == "smoke":
                end = "2018-01-31"
            if args.mode == "extension":
                paths.append(args.extension / market / spine / "signal.parquet")
                hashes.append(register["frozen_signal_sha256"][f"extension/{market}/{spine}"])
                end = "2026-09-30"
            if phase is not None:
                start = "2018-01-23"
                if phase:
                    paths = [args.phases / f"regime_g2_csi1000_phase{phase:02}_20261102_a1.parquet"]
                    hashes = [register["frozen_signal_sha256"][f"phase{phase:02}/{market}/{spine}"]]
            jobs.append(
                {
                    "candidate": candidate,
                    "key": candidate["id"] + (f"_phase{phase:02}" if phase is not None else ""),
                    "start": start,
                    "end": end,
                    "provider": str(args.provider),
                    "provider_contract": provider,
                    "account": register["account"],
                    "signals": [str(p) for p in paths],
                    "expected_signal_sha256": hashes,
                    "slippage": register["slippage_bps"],
                    "output_root": str(args.output),
                    "run_id": args.run_id,
                    "engine_sha256": engine_sha,
                    "source_manifest": implementation,
                    "register_sha256": register_sha,
                }
            )
    if args.mode == "phases":
        jobs.sort(key=lambda j: (int(j["key"][-2:]), j["candidate"]["id"]))
    if args.only:
        selected = set(args.only.split(","))
        jobs = [job for job in jobs if job["candidate"]["id"] in selected]
    if not jobs:
        raise ValueError("empty candidate batch")
    return jobs


def matching_replay_smoke(receipt, job):
    if receipt.get("status") != "COMPLETED" or len(receipt.get("results", [])) != 3:
        return False
    jobs = receipt.get("jobs", [])
    if len(jobs) != 3 or {j["candidate"]["market"] for j in jobs} != {"csi300", "csi500", "csi1000"}:
        return False
    if len({row.get("key") for row in receipt["results"]}) != 3 or {row.get("key") for row in receipt["results"]} != {
        row.get("key") for row in jobs
    }:
        return False
    return all(
        row.get("status") == "COMPLETED"
        and row.get("source_identity_version") == SOURCE_IDENTITY_VERSION
        and row.get("source_manifest") == job["source_manifest"]
        and row.get("provider_contract_sha256") == job["provider_contract"]["sha256"]
        and row.get("register_sha256") == job["register_sha256"]
        and row.get("engine_sha256") == job["engine_sha256"]
        for row in receipt["results"]
    ) and all(
        row.get("register_sha256") == job["register_sha256"] and row.get("engine_sha256") == job["engine_sha256"]
        for row in jobs
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--register", required=True, type=Path)
    parser.add_argument("--signals", required=True, type=Path)
    parser.add_argument("--extension", required=True, type=Path)
    parser.add_argument("--phases", required=True, type=Path)
    parser.add_argument("--provider", required=True, type=Path)
    parser.add_argument("--provider-manifest", required=True, type=Path, help="file map bound by provider_identity")
    parser.add_argument(
        "--provider-amendment", type=Path, help="amendment whose SHA is explicitly allowed by the register"
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--mode", required=True, choices=["smoke", "primary", "extension", "phases"])
    parser.add_argument("--workers", type=int, default=2, choices=[1, 2])
    parser.add_argument("--only", default="", help="registered candidate IDs for a bounded recovery batch")
    args = parser.parse_args()
    register = json.loads(args.register.read_text())
    jobs = build_jobs(args, register)
    args.output.mkdir(parents=True, exist_ok=True)
    if args.mode != "smoke":
        receipts = [json.loads(p.read_text()) for p in args.output.glob("*_smoke_results.json")]
        valid = [r for r in receipts if matching_replay_smoke(r, jobs[0])]
        if not valid:
            raise ValueError("matching successful three-market smoke required")
    started = time.time()
    results, errors = [], []
    print(f"[local-batch] {args.mode} {len(jobs)} jobs workers={args.workers}", flush=True)
    with ProcessPoolExecutor(max_workers=args.workers, mp_context=multiprocessing.get_context("spawn")) as pool:
        futures = {pool.submit(execute, job): job for job in jobs}
        for future in as_completed(futures):
            job = futures[future]
            try:
                result = future.result()
                results.append(result)
                print(
                    f"[local-batch] {len(results)}/{len(jobs)} {result['key']} seconds={result['wall_seconds']:.1f} RSS={result['max_rss_mb']:.0f}MiB",
                    flush=True,
                )
            except Exception as exc:
                errors.append({"key": job["key"], "error": repr(exc)})
                print(f"[local-batch] FAILED {job['key']} {exc!r}", flush=True)
            (args.output / f"{args.run_id}_{args.mode}_progress.json").write_text(
                json.dumps({"completed": len(results), "expected": len(jobs), "errors": errors}, indent=2)
            )
    # Runtime paths remain in local control logs; public result receipts contain only identities and hashes.
    public_jobs = [
        {
            k: v
            for k, v in job.items()
            if k not in ("provider", "signals", "output_root", "provider_contract", "source_manifest")
        }
        | {"provider_contract_sha256": job["provider_contract"]["sha256"]}
        for job in jobs
    ]
    payload = {
        "status": "COMPLETED" if len(results) == len(jobs) and not errors else "FAILED",
        "mode": args.mode,
        "run_id": args.run_id,
        "execution_environment": "LOCAL_BOUNDED_FALLBACK",
        "source_manifest": jobs[0]["source_manifest"],
        "provider_contract": jobs[0]["provider_contract"],
        "wall_seconds": time.time() - started,
        "jobs": public_jobs,
        "results": results,
        "errors": errors,
    }
    (args.output / f"{args.run_id}_{args.mode}_results.json").write_text(
        json.dumps(payload, indent=2, allow_nan=False) + "\n"
    )
    return 0 if payload["status"] == "COMPLETED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
