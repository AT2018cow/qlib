"""Build one self-hosted provider archive in local scratch and publish it.

This module is intentionally Modal-free: the production ``ensure_overlay``
function and the isolated read-only probe both call it.  That keeps the
resource test on the exact same code path as production while guaranteeing the
probe writes only to its own Volume root.
"""

from __future__ import annotations

import re
import shutil
from datetime import date as date_cls
from pathlib import Path
from typing import Callable, Iterable

from data_ingest.baostock_source import BaoStockSource
from data_ingest.cache import IngestCache
from data_ingest.calendar import fetch_sina_calendar, read_calendar, resolve_target_dates
from data_ingest.manifest import canonical_json, sha256_file, sha256_text
from data_ingest.pipeline import OverlayPipeline, PipelineError
from data_ingest.sina import SinaSource
from data_ingest.store import (
    build_receipt,
    create_provider_archive,
    latest_snapshot,
    load_snapshot,
    materialize,
    prune_archives,
    publish_archive,
    seal_directory_snapshot,
)


class ProviderBuildError(RuntimeError):
    """Raised when a provider build cannot produce a PASS archive."""


def _code_source_sha() -> str:
    try:
        from research_identity import source_manifest

        return str(source_manifest("training")["sha256"])
    except Exception:  # noqa: BLE001 - audit identity is not a build gate
        return "unavailable"


def build_provider_archive(
    root: str | Path,
    trade_date: str,
    *,
    commit_fn: Callable[[], None],
    extra_symbols: Iterable[str] = (),
    required_symbols: Iterable[str] = (),
    refresh_memberships: bool = False,
    membership_provider=None,
    require_qlib_smoke: bool = True,
    work_root: str | Path = "/tmp/provider-build",
    max_baostock_symbols: int = 300,
    failure_record_fn: Callable[[str, Exception, dict, dict], None] | None = None,
    sampler=None,
) -> dict:
    """Publish the PASS archive for ``trade_date`` or return an existing one.

    The provider root receives only an archive, a manifest/calendar copy and a
    receipt.  Legacy directory snapshots are sealed once and then never
    modified.
    """
    from resource_sampler import ResourceSampler

    root = Path(root)
    work = Path(work_root)
    work.mkdir(parents=True, exist_ok=True)
    trade_date = str(trade_date)[:10]
    owns_sampler = sampler is None
    if sampler is None:
        sampler = ResourceSampler(interval=2.0)
        sampler.start()
    context: dict = {"date": trade_date}
    try:
        existing = load_snapshot(root, trade_date)
        if existing is not None:
            if existing.kind == "archive":
                return {
                    "status": "EXISTS",
                    "date": trade_date,
                    "digest": existing.digest,
                    "receipt_sha256": (existing.receipt or {}).get("receipt_sha256"),
                    "calendar_end": existing.calendar_end,
                    "resources": sampler.stop() if owns_sampler else sampler.summary(),
                }
            receipt = seal_directory_snapshot(
                root,
                trade_date,
                commit_fn=commit_fn,
                work_dir=work / "seal",
            )
            return {
                "status": "SEALED",
                "date": trade_date,
                "digest": receipt["archive"]["sha256"],
                "receipt_sha256": receipt["receipt_sha256"],
                "calendar_end": receipt["calendar_end"],
                "resources": sampler.stop() if owns_sampler else sampler.summary(),
            }
        base = latest_snapshot(root, before=trade_date)
        if base is None:
            raise ProviderBuildError(f"no PASS provider base before {trade_date}")
        context["base"] = {"kind": base.kind, "date": base.date, "digest": base.digest}
        base_local = materialize(base, work / "base", cache_root=str(work / "cache"))
        base_calendar = read_calendar(base_local)
        target_dates = resolve_target_dates(base_calendar, trade_date, fetch_sina_calendar())
        context["target_dates"] = target_dates

        cache = IngestCache(root, trade_date, commit_fn=commit_fn)
        metrics: dict = {"stages": {}}
        pipeline = OverlayPipeline(
            sina=SinaSource(
                workers=8,
                retries=3,
                connect_timeout=10,
                read_timeout=60,
                request_retries=2,
                batch_deadline=45 * 60,
            ),
            baostock=BaoStockSource(isolate=True, deadline_seconds=10 * 60),
            crosscheck_count=20,
            require_weights=True,
            cache=cache,
            metrics=metrics,
            membership_provider=membership_provider,
        )
        out_local = work / "out"
        if out_local.exists():
            shutil.rmtree(out_local)
        build_kwargs = dict(
            asof=trade_date,
            dates=target_dates,
            markets=("csi300", "csi500", "csi1000"),
            extra_symbols=sorted({str(item).upper() for item in extra_symbols}),
            required_extra_symbols=sorted({str(item).upper() for item in required_symbols}),
            require_qlib_smoke=require_qlib_smoke,
            refresh_memberships=refresh_memberships,
            base_identity={
                "kind": base.kind,
                "date": base.date,
                "digest": base.digest,
                "path": str(base.path),
                "calendar_start": base.calendar_start,
                "calendar_end": base.calendar_end,
                "calendar_sha256": base.calendar_sha256,
                "archive_sha256": base.digest,
                "manifest_sha256": base.manifest_sha256,
            },
        )
        try:
            manifest = pipeline.build(base_local, out_local, **build_kwargs)
        except PipelineError as exc:
            match = re.search(r"constituent date (\d{4}-\d{2}-\d{2}) != asof", str(exc))
            if match is None or len(target_dates) != 1:
                raise
            lag_days = (date_cls.fromisoformat(trade_date) - date_cls.fromisoformat(match.group(1))).days
            if lag_days < 0 or lag_days > 3:
                raise
            print(
                f"[overlay] CSIndex snapshot {match.group(1)} lags asof {trade_date} by {lag_days}d; "
                "retrying with equality-verified base PIT membership",
                flush=True,
            )
            shutil.rmtree(out_local)
            manifest = pipeline.build(base_local, out_local, allow_base_membership=True, **build_kwargs)

        archive_path = work / f"{trade_date}.tar.gz"
        archive_meta = create_provider_archive(out_local, archive_path, read_workers=16)
        manifest_path = out_local / "manifest.json"
        calendar_path = out_local / "calendars" / "day.txt"
        gate = manifest.get("gate") or {}
        receipt = build_receipt(
            None,
            date=trade_date,
            archive=archive_meta,
            manifest_sha256=sha256_file(manifest_path),
            calendar_sha256=sha256_file(calendar_path),
            base=manifest["base"],
            provenance={
                "builder": "overlay-pipeline",
                "contract": archive_meta["contract"],
                "code_source_sha256": _code_source_sha(),
                "sina": manifest.get("sources", {}).get("sina_stats"),
                "membership_policy": manifest.get("membership_policy"),
                "history_backfill": manifest.get("history_backfill", {}).get("symbols", []),
            },
            gate={
                "status": gate.get("status"),
                "checks_sha256": sha256_text(canonical_json(gate.get("checks") or [])),
                "failures": gate.get("failures") or [],
            },
            extra={
                "universe": manifest.get("universe"),
                "metrics": metrics,
                "resources": sampler.summary(),
                "archive_members": archive_meta.get("member_count"),
                "content_sha256": archive_meta.get("content_sha256"),
            },
        )
        publish_archive(
            root,
            receipt,
            archive_path,
            local_manifest=manifest_path,
            local_calendar=calendar_path,
            commit_fn=commit_fn,
        )
        prune = prune_archives(root, keep=30, dry_run=True)
        print(
            f"[overlay] {trade_date} PASS archive={receipt['archive']['sha256'][:16]} "
            f"size={receipt['archive']['size'] // 1024**2}MiB rows={manifest['overlay']['row_symbols']} "
            f"prune_dry_run={prune['removed']}",
            flush=True,
        )
        return {
            "status": "PUBLISHED",
            "date": trade_date,
            "digest": receipt["archive"]["sha256"],
            "receipt_sha256": receipt["receipt_sha256"],
            "calendar_end": receipt["calendar_end"],
            "rows": manifest["overlay"]["row_symbols"],
            "gate": gate.get("status"),
            "membership_policy": manifest.get("membership_policy"),
            "history_backfill": manifest.get("history_backfill", {}).get("symbols", []),
            "resources": receipt.get("resources"),
            "metrics": metrics,
        }
    except Exception as exc:  # noqa: BLE001 - persist evidence then fail closed
        resources = sampler.stop() if owns_sampler else sampler.summary()
        if failure_record_fn is not None:
            failure_record_fn(trade_date, exc, resources, context)
        raise
    finally:
        if owns_sampler:
            sampler.stop()
