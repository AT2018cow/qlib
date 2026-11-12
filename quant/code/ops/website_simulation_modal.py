"""Build and publish the three October 8 website simulation accounts.

Run ``--mode smoke`` before ``--mode full`` in an attached foreground Modal
session. Deploy this app in the existing production environment only after
validation. Research runs never publish; the production orchestrator publishes three
markets together, retaining content-addressed snapshots and an idempotent receipt.
"""

from __future__ import annotations

import sys
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
if not (CODE / "quant_bootstrap.py").is_file():
    CODE = Path("/root/qlib/quant/code")
sys.path.insert(0, str(CODE))
from quant_bootstrap import ensure_quant_import_paths

ensure_quant_import_paths()
sys.path.insert(0, str(CODE / "research/regime_reselection"))

import modal
from modal_qlib_cn_a10g import WEBSITE_APP_NAME, WEBSITE_SIM_VOLUME, image, vol as data_volume
from website_replay_core import (
    _build_market_impl as _core_build_market_impl,
    _cached_model as _core_cached_model,
    _incremental_prior as _core_incremental_prior,
    _load_prior_ledger,
    _overlay_base,
    _predict_days,
    _provider_calendar,
    _sha256_path,
    _validate_required_replay_mode,
    fit_website_model,
    make_job,
    market_name,
    publish_results as _core_publish_results,
)
from daily_pipeline import WEBSITE_STAGE_TIMEOUT_SECONDS, WEBSITE_WORKER_TIMEOUT_SECONDS
from website_budget import billing_observation, checked_publication_receipt

app = modal.App(WEBSITE_APP_NAME, image=image)
simulation = modal.Volume.from_name(WEBSITE_SIM_VOLUME, create_if_missing=True)
simulation_readonly = simulation.with_mount_options(read_only=True)
data_volume_readonly = data_volume.with_mount_options(read_only=True)


def _build_market_impl(job, sampler=None):
    return _core_build_market_impl(
        job,
        sampler=sampler,
        simulation_root=Path("/simulation"),
        reload_fn=simulation.reload,
        commit_fn=simulation.commit,
    )


def _cached_model(*args, **kwargs):
    kwargs.setdefault("simulation_root", Path("/simulation"))
    return _core_cached_model(*args, **kwargs)


def _incremental_prior(cache_sha, asset, market, end, calendar, *, semantic_sha, account_sha):
    return _core_incremental_prior(
        cache_sha,
        asset,
        market,
        end,
        calendar,
        semantic_sha=semantic_sha,
        account_sha=account_sha,
        simulation_root=Path("/simulation"),
    )


def publish_results(results, source, asset, *, token):
    return _core_publish_results(
        results,
        source,
        asset,
        token=token,
        simulation_root=Path("/simulation"),
        commit_fn=simulation.commit,
    )


def _latest_overlay_asset(expected_end: str | None = None):
    """Return the single self-hosted provider asset (archive preferred)."""
    import hashlib
    import json

    root = Path("/data/cn_data_overlay")
    if not root.is_dir():
        return None
    archive_root = root / "archives"
    candidates = []
    if archive_root.is_dir():
        for directory in sorted(archive_root.iterdir()):
            if not directory.is_dir() or len(directory.name) != 10:
                continue
            receipt_path = directory / "receipt.json"
            try:
                receipt = json.loads(receipt_path.read_text())
            except (OSError, ValueError):
                continue
            if receipt.get("status") != "PASS" or receipt.get("date") != directory.name:
                continue
            if expected_end is not None and directory.name != expected_end:
                continue
            archive = receipt.get("archive") or {}
            relative = archive.get("path")
            if not isinstance(relative, str):
                continue
            path = root / relative
            if not path.is_file() or path.stat().st_size != int(archive.get("size") or -1):
                continue
            calendar_path = directory / "calendar.txt"
            calendar = (
                [line.strip() for line in calendar_path.read_text().splitlines() if line.strip()]
                if calendar_path.is_file()
                else []
            )
            candidates.append(
                {
                    "kind": "archive",
                    "archive_path": str(path),
                    "digest": f"sha256:{archive['sha256']}",
                    "size": int(archive["size"]),
                    "release": f"overlay-{directory.name}",
                    "published_at": receipt.get("published_at_utc", ""),
                    "source": "overlay",
                    "calendar_end": receipt.get("calendar_end", directory.name),
                    "calendar": calendar,
                    "base": receipt.get("base") or {},
                    "manifest_sha256": (receipt.get("manifest") or {}).get("sha256"),
                    "content_sha256": archive.get("content_sha256"),
                }
            )
    if candidates:
        return candidates[-1]
    for path in reversed(sorted(root.iterdir())):
        if not path.is_dir() or len(path.name) != 10:
            continue
        if expected_end is not None and path.name != expected_end:
            continue
        manifest_path = path / "manifest.json"
        calendar_path = path / "calendars" / "day.txt"
        if not manifest_path.is_file() or not calendar_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text())
        except (OSError, ValueError):
            continue
        if manifest.get("status") != "PASS":
            continue
        calendar = [line.strip() for line in calendar_path.read_text().splitlines() if line.strip()]
        if not calendar or (expected_end is not None and calendar[-1] != expected_end):
            continue
        digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        return {
            "kind": "directory",
            "local_path": str(path),
            "digest": f"sha256:{digest}",
            "size": 0,
            "release": f"overlay-{calendar[-1]}",
            "published_at": manifest.get("generated_at_utc", ""),
            "source": "overlay",
            "calendar_end": calendar[-1],
            "calendar": calendar,
            "base": manifest.get("base") or {},
            "manifest_sha256": digest,
            "content_sha256": None,
        }
    return None


def latest_asset(expected_end: str | None = None):
    """Resolve the self-hosted provider; a missing session is a hard error."""
    asset = _latest_overlay_asset(expected_end)
    if asset is None:
        raise ValueError(f"no PASS self-hosted provider for {expected_end or 'latest'}")
    if expected_end is not None and asset["calendar_end"] != expected_end:
        raise ValueError(f"provider ends {asset['calendar_end']}, expected {expected_end}")
    return asset


def _billing_status():
    """Read-only cost telemetry; never blocks, allows or changes a replay."""
    from datetime import datetime, time, timezone
    from zoneinfo import ZoneInfo

    try:
        workspace = modal.Workspace.from_context()
        local_tz = ZoneInfo("Asia/Shanghai")
        now_local = datetime.now(local_tz)
        start_local = datetime.combine(now_local.date(), time.min, tzinfo=local_tz)
        observation = billing_observation(
            report=lambda: workspace.billing.report(
                start=start_local.astimezone(timezone.utc),
                end=now_local.astimezone(timezone.utc),
                resolution="h",
            ),
            summary=workspace.billing.summary,
        )
    except Exception as exc:  # noqa: BLE001 - telemetry boundary
        observation = {
            "status": "unavailable",
            "source": "workspace.billing",
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
        }
    print(f"[website] billing telemetry: {observation}", flush=True)
    return observation


@app.function(volumes={"/simulation": simulation_readonly}, cpu=1, memory=1024, timeout=120)
def billing_status():
    """Small read-only ops probe for cost telemetry; no replay decision."""
    return _billing_status()


@app.function(
    volumes={"/simulation": simulation_readonly},
    cpu=1,
    memory=1024,
    timeout=120,
    max_containers=1,
    retries=0,
)
def status(expected_source_sha256: str, asset_digest: str = "", expected_latest_date: str = ""):
    """Read-only validated publication receipt lookup for the daily coordinator."""
    from research_identity import account_contract_manifest, source_manifest, website_semantic_manifest
    from website_simulation import account_contract_for_market

    source = source_manifest("training")
    if expected_source_sha256 != source["sha256"]:
        return {
            "status": "INVALID",
            "source_sha256": expected_source_sha256,
            "deployed_source_sha256": source["sha256"],
            "asset_digest": asset_digest,
            "reason": "signal/website deployments have different source identities",
        }
    simulation.reload()
    if not asset_digest:
        return {
            "status": "MISSING",
            "source_sha256": expected_source_sha256,
            "asset_digest": asset_digest,
            "reason": "no asset digest supplied",
        }
    semantic = website_semantic_manifest()
    accounts = {
        market: account_contract_manifest(account_contract_for_market(market))["sha256"]
        for market in ("csi300", "csi500", "csi1000")
    }
    try:
        receipt = checked_publication_receipt(
            simulation_root="/simulation",
            expected_source_sha256=source["sha256"],
            expected_semantic_sha256=semantic["sha256"],
            expected_accounts=accounts,
            expected_asset_digest=asset_digest,
            expected_latest_date=expected_latest_date or None,
        )
    except ValueError as exc:
        return {
            "status": "INVALID",
            "source_sha256": expected_source_sha256,
            "asset_digest": asset_digest,
            "reason": str(exc),
        }
    if receipt is None:
        return {
            "status": "MISSING",
            "source_sha256": expected_source_sha256,
            "asset_digest": asset_digest,
        }
    return {
        "status": receipt.get("status"),
        "source_sha256": receipt.get("source_sha256"),
        "website_semantic_sha256": receipt.get("website_semantic_sha256"),
        "account_contract_sha256": receipt.get("account_contract_sha256"),
        "asset_digest": receipt.get("asset_digest"),
        "latest_date": receipt.get("latest_date"),
        "commit": receipt.get("commit"),
        "files": receipt.get("files"),
        "markets": receipt.get("markets"),
    }


def _run_market_worker(job):
    """Run either resource class through the same replay and receipt implementation."""
    import json
    import time
    import uuid

    from resource_sampler import ResourceSampler

    sampler = ResourceSampler(interval=2.0)
    sampler.start()
    try:
        result = _build_market_impl(job, sampler=sampler)
    except Exception as exc:
        resources = sampler.stop()
        try:
            path = (
                Path("/simulation/metrics/failures")
                / f"{market_name(job)}-{int(time.time())}-{uuid.uuid4().hex[:8]}.json"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "status": "FAILED",
                        "market": market_name(job),
                        "required_replay_mode": (job or {}).get("required_replay_mode"),
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:2000],
                        "resources": resources,
                    },
                    indent=2,
                    sort_keys=True,
                    allow_nan=False,
                )
                + "\n"
            )
            simulation.commit()
        except Exception as record_exc:  # noqa: BLE001 - do not mask the replay error
            print(f"[website] ⚠️ could not persist failure metrics: {record_exc}", flush=True)
        raise
    finally:
        # Success, cache hit and failure all stop the background sampler.  The
        # implementation stops it before writing the immutable payload; this
        # finally is idempotent and protects early-return paths.
        sampler.stop()
    return result


@app.function(
    volumes={"/simulation": simulation, "/data": data_volume_readonly},
    cpu=8,
    memory=16384,
    timeout=WEBSITE_WORKER_TIMEOUT_SECONDS,
    max_containers=3,
    retries=0,
    scaledown_window=5,
)
def build_market(job):
    """Run smoke/full/retrain work with the existing 8 CPU / 16 GiB envelope."""
    return _run_market_worker(job)


@app.function(
    volumes={"/simulation": simulation, "/data": data_volume_readonly},
    cpu=4,
    memory=12288,
    timeout=WEBSITE_WORKER_TIMEOUT_SECONDS,
    max_containers=3,
    retries=0,
    scaledown_window=5,
)
def build_market_incremental(job):
    """Run only a validated one-session incremental replay on the lighter worker."""
    strict_job = {**(job or {}), "required_replay_mode": "INCREMENTAL"}
    return _run_market_worker(strict_job)


@app.function(
    volumes={"/simulation": simulation, "/data": data_volume_readonly},
    secrets=[modal.Secret.from_name("github-push")],
    timeout=WEBSITE_STAGE_TIMEOUT_SECONDS,
    cpu=1,
    memory=2048,
    max_containers=1,
    retries=0,
)
def daily(expected_source_sha256: str, expected_provider_end: str = ""):
    import os
    from research_identity import (
        account_contract_manifest,
        source_manifest,
        website_cache_key,
        website_semantic_manifest,
    )
    from website_simulation import account_contract_for_market

    source = source_manifest("training")
    if expected_source_sha256 != source["sha256"]:
        raise ValueError("signal/website deployments have different source identities")
    semantic = website_semantic_manifest()
    accounts = {
        market: account_contract_manifest(account_contract_for_market(market))
        for market in ("csi300", "csi500", "csi1000")
    }
    cache_keys = {market: website_cache_key(semantic["sha256"], accounts[market]["sha256"]) for market in accounts}
    data_volume_readonly.reload()
    asset = latest_asset(expected_provider_end or None)
    markets = ("csi300", "csi500", "csi1000")
    calendar = _provider_calendar(asset)
    expected_latest = calendar[-1] if calendar else asset.get("calendar_end")
    simulation.reload()
    receipt = checked_publication_receipt(
        simulation_root="/simulation",
        expected_source_sha256=source["sha256"],
        expected_semantic_sha256=semantic["sha256"],
        expected_accounts={market: accounts[market]["sha256"] for market in markets},
        expected_asset_digest=asset["digest"],
        expected_latest_date=expected_latest,
    )
    if receipt is not None:
        print(f"[website] idempotent short-circuit: {source['sha256'][:12]}/{asset['digest'][:19]}", flush=True)
        return {
            "status": "ALREADY_PUBLISHED",
            "source_sha256": source["sha256"],
            "asset_digest": asset["digest"],
            "files": receipt.get("files", 12),
            "idempotent_short_circuit": True,
        }
    incremental = False
    if calendar:
        try:
            incremental = all(
                _incremental_prior(
                    cache_keys[market],
                    asset,
                    market,
                    calendar[-1],
                    calendar,
                    semantic_sha=semantic["sha256"],
                    account_sha=accounts[market]["sha256"],
                )
                is not None
                for market in markets
            )
        except Exception as exc:  # noqa: BLE001 - fall back to the smoke/full path
            print(f"[website] incremental prefix probe failed: {type(exc).__name__}: {exc}", flush=True)
    if incremental:
        print("[website] incremental prefix present for all markets; skipping smoke replay", flush=True)
        worker = build_market_incremental
        required_replay_mode = "INCREMENTAL"
    else:
        # The cache root is per market account contract, so a csi300-only
        # smoke cannot authorize csi500/csi1000 full replays.
        for market in markets:
            build_market.remote(make_job(asset, source, market, "smoke", semantic=semantic, account=accounts[market]))
        worker = build_market
        required_replay_mode = None
    jobs = [
        make_job(
            asset,
            source,
            market,
            "full",
            required_replay_mode=required_replay_mode,
            semantic=semantic,
            account=accounts[market],
        )
        for market in markets
    ]
    results = list(worker.map(jobs, order_outputs=False))
    # Workers run in separate containers.  The coordinator must reload before
    # hashing their immutable result/ledger files into the publication receipt.
    simulation.reload()
    return publish_results(results, source, asset, token=os.environ["GITHUB_TOKEN"])


@app.local_entrypoint()
def main(mode: str = "smoke", output: str = "/tmp/website-simulation", release_file: str = ""):
    import json
    from research_identity import source_manifest

    if mode not in ("smoke", "full"):
        raise ValueError("use smoke or full; publishing is a separate deployed operation")
    asset = json.loads(Path(release_file).read_text()) if release_file else latest_asset()
    source = source_manifest("training")
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    markets = ["csi300"] if mode == "smoke" else ["csi300", "csi500", "csi1000"]
    results = []
    for result in build_market.map([make_job(asset, source, m, mode) for m in markets], order_outputs=False):
        results.append(result)
        (root / f"{mode}-progress.json").write_text(json.dumps({"completed": len(results), "expected": len(markets)}))
        print(f"[batch] {result['market']} {result['status']} seconds={result['wall_seconds']:.1f}", flush=True)
    (root / f"{mode}-results.json").write_text(json.dumps(results, indent=2, allow_nan=False) + "\n")
