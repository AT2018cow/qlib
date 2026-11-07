"""2-3 session canary for the new provider overlay.

The canary runs at 07:00 Asia/Shanghai on weekdays.  It rebuilds the overlay
from the frozen 2026-11-04 base through the latest completed trading day, runs
the three production model profiles, and stores the result in a dedicated
Volume.  It never mounts the production Volume, never publishes a signal, and
never touches paper accounts.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
if not (CODE / "quant_bootstrap.py").is_file():
    CODE = Path("/root/qlib/quant/code")
sys.path.insert(0, str(CODE))
from quant_bootstrap import ensure_quant_import_paths

ensure_quant_import_paths()
sys.path.insert(0, str(CODE / "production"))

import modal
from modal_qlib_cn_a10g import image

BASE_ARCHIVE_SHA256 = "dc917293925635189e982f47d35b960c3db8a9c3f11312c937673c8afdec21aa"
BASE_URL = "https://github.com/chenditc/investment_data/releases/download/2026-11-04/qlib_bin.tar.gz"
BASE_END = "2026-11-04"
CANARY_START = "2026-11-05"

KNOWN_1106 = {
    "csi1000": [
        "SH688449",
        "SH600353",
        "SH688182",
        "SZ300131",
        "SH688627",
        "SZ002484",
        "SH688141",
        "SZ000636",
        "SH605376",
        "SH688362",
    ],
    "csi500": [
        "SZ300620",
        "SZ001309",
        "SH688052",
        "SH688266",
        "SZ301536",
        "SH688234",
        "SH688318",
        "SH688120",
        "SH688002",
        "SZ002126",
        "SZ000021",
        "SZ002821",
        "SH688561",
        "SZ000415",
        "SH603899",
        "SH600711",
        "SH600378",
        "SH688122",
        "SH605589",
        "SZ000032",
    ],
    "csi300": [
        "SH688521",
        "SH603986",
        "SH601138",
        "SZ301308",
        "SZ000657",
        "SZ300476",
        "SH600183",
        "SZ000988",
        "SZ002938",
        "SZ300394",
    ],
}
PROFILES = {
    "csi1000": ("c98856b4_top10_drop1", 10, 1),
    "csi500": ("raw10d_c98856b4_top20_drop2_20d", 20, 2),
    "csi300": ("59b93929_top10_drop1_10d", 10, 1),
}

app = modal.App("qlib-data-overlay-canary", image=image)
volume = modal.Volume.from_name("qlib-data-overlay-canary", create_if_missing=True)
production_volume = modal.Volume.from_name("qlib-cn-data")


def _latest_completed_trading_day() -> str:
    from zoneinfo import ZoneInfo

    from data_ingest.calendar import fetch_sina_calendar

    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    calendar = fetch_sina_calendar()
    candidates = [day for day in calendar if day < today]
    if not candidates:
        raise RuntimeError("no completed trading day in the calendar")
    return candidates[-1]


def _ensure_base(root: Path) -> Path:
    import hashlib
    import shutil

    import requests

    from provider_archive import safe_extract_provider_archive

    base_dir = root / "chenditc_bin"
    archive = root / "qlib_bin.tar.gz"
    root.mkdir(parents=True, exist_ok=True)
    if base_dir.is_dir():
        return base_dir
    if not archive.is_file():
        with requests.get(BASE_URL, stream=True, timeout=900) as response:
            response.raise_for_status()
            with archive.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1 << 20):
                    handle.write(chunk)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != BASE_ARCHIVE_SHA256:
        raise RuntimeError(f"base archive SHA mismatch: {digest}")
    extract = root / "extract"
    if extract.exists():
        shutil.rmtree(extract)
    package = Path(safe_extract_provider_archive(archive, extract))
    shutil.move(str(package), str(base_dir))
    return base_dir


def _run_model(provider: str, market: str) -> dict:
    import resource
    import time

    import modal_qlib_cn_a10g as m
    import qlib
    from csi1000_tuner_core import deterministic_score_order
    from qlib.data.dataset import Dataset
    from qlib.model.base import Model
    from qlib.utils import init_instance_by_config
    from qlib_audit_fixes import read_trading_calendar
    from qlib_live_retrain import configure_asof
    from qlib.workflow import R

    profile, topk, nd = PROFILES[market]
    yaml_path = "/root/qlib/examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158_csi500.yaml"
    cfg = m._load_and_patch_cfg(
        yaml_path,
        smoke=False,
        recent=True,
        long_train=True,
        label20=True,
        topk=topk,
        nd=nd,
        market=market,
        provider_dir=provider,
        csi1000_profile=profile,
    )
    calendar = read_trading_calendar(provider)
    asof = calendar[-1]
    horizon = int(cfg.get("_csi1000_production_manifest", {}).get("label_horizon", 20))
    configure_asof(cfg, calendar, asof, horizon=horizon)
    cfg["qlib_init"]["provider_uri"] = provider
    cfg["qlib_init"]["exp_manager"] = {
        "class": "MLflowExpManager",
        "module_path": "qlib.workflow.expm",
        "kwargs": {"uri": "file:/tmp/mlruns", "default_exp_name": f"canary-{market}"},
    }
    kwargs = cfg["task"]["model"]["kwargs"]
    kwargs["num_threads"] = 20
    kwargs.pop("n_jobs", None)
    try:
        R.end_exp()
    except Exception:
        pass
    qlib.init(**cfg["qlib_init"])
    started = time.time()
    model = init_instance_by_config(cfg["task"]["model"], accept_types=Model)
    dataset = init_instance_by_config(cfg["task"]["dataset"], accept_types=Dataset)
    model.fit(dataset)
    pred = model.predict(dataset, segment="test")
    day = pred.loc[asof].dropna()
    raw_top = day.reindex(deterministic_score_order(day.items())[:20])
    from qlib.data import D

    day_df = D.features(
        [str(x) for x in day.index],
        ["$close", "Ref($close,1)"],
        start_time=asof,
        end_time=asof,
        freq="day",
    )
    limited = set()
    if len(day_df) > 0 and ("Ref($close,1)" in day_df.columns):
        day_ret = (day_df["$close"] / day_df["Ref($close,1)"] - 1).dropna()
        limited = set(
            m.board_aware_limited(day_ret, str(asof)[:10], listing_dates=m._board_listing_dates(provider, day.index))
        )
    filtered = day.loc[~day.index.isin(limited)]
    display = filtered.reindex(deterministic_score_order(filtered.items())[:topk])
    return {
        "market": market,
        "profile": profile,
        "asof": asof,
        "segments": cfg["task"]["dataset"]["kwargs"]["segments"],
        "raw_top20": [
            {"rank": index, "instrument": str(inst), "score": float(score)}
            for index, (inst, score) in enumerate(raw_top.items(), 1)
        ],
        "display_top": [
            {"rank": index, "instrument": str(inst), "score": float(score)}
            for index, (inst, score) in enumerate(display.items(), 1)
        ],
        "display_limit_removed": len(limited),
        "fit_seconds": round(time.time() - started, 1),
        "maxrss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
    }


def _run_canary(asof: str | None = None, validation: bool = False) -> dict:
    import shutil
    import traceback

    from data_ingest.baostock_source import BaoStockSource
    from data_ingest.calendar import fetch_sina_calendar, read_calendar, resolve_target_dates
    from data_ingest.pipeline import OverlayPipeline
    from data_ingest.sina import SinaSource

    volume.reload()
    base_root = Path("/vol/base")
    result_root = Path("/vol/canary_validation" if validation else "/vol/canary")
    asof = asof or _latest_completed_trading_day()
    generated = datetime.now(timezone.utc).isoformat()
    if asof <= BASE_END:
        result = {
            "status": "SKIPPED_NO_NEW_DATA",
            "asof": asof,
            "generated_at_utc": generated,
            "validation": validation,
        }
        result_root.mkdir(parents=True, exist_ok=True)
        (result_root / "latest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        volume.commit()
        return result
    try:
        base_dir = _ensure_base(base_root)
        calendar = fetch_sina_calendar()
        base_calendar = read_calendar(base_dir)
        dates = resolve_target_dates(base_calendar, asof, calendar)
        overlay = Path("/vol/overlays") / asof
        if overlay.exists():
            shutil.rmtree(overlay)
        pipeline = OverlayPipeline(
            sina=SinaSource(workers=6),
            baostock=BaoStockSource(),
            crosscheck_count=20,
            require_weights=True,
        )
        manifest = pipeline.build(
            base_dir,
            overlay,
            asof=asof,
            dates=dates,
            markets=("csi300", "csi500", "csi1000"),
            base_archive_sha256=BASE_ARCHIVE_SHA256,
            require_qlib_smoke=True,
            allow_base_membership=True,
            allow_missing_base_suspensions=True,
        )
        rankings = {market: _run_model(str(overlay), market) for market in PROFILES}
        known = None
        if asof == "2026-11-06":
            mismatches = {}
            for market, expected in KNOWN_1106.items():
                actual = [row["instrument"] for row in rankings[market]["display_top"]]
                if actual != expected:
                    mismatches[market] = {"expected": expected, "actual": actual}
            known = {"status": "PASS" if not mismatches else "FAIL", "mismatches": mismatches}
        status = "PASS"
        if known is not None and known["status"] != "PASS":
            status = "FAIL"
        result = {
            "status": status,
            "asof": asof,
            "generated_at_utc": generated,
            "validation": validation,
            "dates": dates,
            "gate": manifest["gate"],
            "sources": {
                "sina_errors": manifest["sources"]["sina_errors"],
                "baostock_errors": manifest["sources"]["baostock_errors"],
            },
            "overlay": {
                key: manifest["overlay"][key]
                for key in ("feature_dirs_copied", "bins_appended", "row_symbols", "calendar_len")
            },
            "known_1106": known,
            "rankings": rankings,
        }
        target = result_root / asof
        target.mkdir(parents=True, exist_ok=True)
        (target / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        shutil.copy2(overlay / "manifest.json", target / "manifest.json")
        (result_root / "latest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        passed = []
        for path in sorted(result_root.glob("2026-*/result.json")):
            payload = json.loads(path.read_text())
            if payload.get("status") == "PASS":
                passed.append(payload.get("asof"))
        result["passed_canary_days"] = sorted(set(passed))
        (target / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        volume.commit()
        return result
    except Exception as exc:  # noqa: BLE001 - canary boundary
        result = {
            "status": "FAIL",
            "asof": asof,
            "generated_at_utc": generated,
            "validation": validation,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc()[-4000:],
        }
        target = result_root / asof
        target.mkdir(parents=True, exist_ok=True)
        (target / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        (result_root / "latest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        volume.commit()
        return result


@app.function(
    volumes={"/vol": volume},
    cpu=20,
    memory=32768,
    timeout=7200,
    schedule=modal.Cron("0 7 * * 1-5", timezone="Asia/Shanghai"),
    max_containers=1,
    retries=1,
)
def daily_canary(asof: str = "", validation: bool = False):
    return _run_canary(asof=asof or None, validation=validation)


@app.function(volumes={"/source": volume, "/target": production_volume}, cpu=8, memory=16384, timeout=3600)
def promote(asof: str = "2026-11-06"):
    """Copy a gate-passed canary overlay into the production provider volume.

    Cross-volume copies of 18k tiny bins are slow and can leave a partial
    target.  Archive on the source side, copy one file, then extract locally
    inside the target volume.
    """
    import hashlib
    import shutil
    import tarfile

    source = Path("/source/overlays") / asof
    target_root = Path("/target/cn_data_overlay")
    target = target_root / asof
    manifest_path = source / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"canary overlay missing: {source}")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("status") != "PASS":
        raise RuntimeError(f"refusing to promote non-PASS overlay: {manifest.get('status')}")
    target_root.mkdir(parents=True, exist_ok=True)
    if target.exists():
        existing_manifest = target / "manifest.json"
        if existing_manifest.is_file() and json.loads(existing_manifest.read_text()) == manifest:
            return {"status": "ALREADY_PROMOTED", "asof": asof, "target": str(target)}
        shutil.rmtree(target)
    archive = Path("/tmp") / f"{asof}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(source, arcname=asof)
    target_archive = target_root / f"{asof}.tar.gz"
    shutil.copy2(archive, target_archive)
    with tarfile.open(target_archive) as tar:
        tar.extractall(target_root)
    target_archive.unlink(missing_ok=True)
    if not (target / "manifest.json").is_file():
        raise RuntimeError(f"promoted overlay is incomplete: {target}")
    receipt = {
        "status": "PROMOTED",
        "asof": asof,
        "source": str(source),
        "target": str(target),
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    (target / "promotion_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    production_volume.commit()
    return receipt


@app.function(volumes={"/target": production_volume}, cpu=1, memory=2048, timeout=600)
def archive_journal(signal_date: str, label: str = "operator_override"):
    """Archive a committed publication journal before a controlled reissue."""
    import shutil

    source = Path("/target/publication_journal") / signal_date
    if not source.is_dir():
        return {"status": "MISSING", "signal_date": signal_date}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = Path("/target/publication_journal") / "archive" / f"{signal_date}_{label}_{stamp}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    shutil.rmtree(source)
    production_volume.commit()
    return {"status": "ARCHIVED", "signal_date": signal_date, "archive": str(target)}


@app.local_entrypoint()
def main(asof: str = "", validation: bool = False):
    result = daily_canary.remote(asof=asof, validation=validation)
    print("CANARY_RESULT " + json.dumps(result, ensure_ascii=False))
