"""Production-image dry run for the fail-closed daily provider overlay.

Run from the repository root in an attached foreground session::

    modal run quant/code/ops/modal_data_overlay_dry_run.py

The app uses a dedicated temporary Volume.  It never mounts or writes the
production ``qlib-cn-data`` Volume, never publishes a signal, and never touches
the paper accounts.
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
from modal_qlib_cn_a10g import image

BASE_ARCHIVE_SHA256 = "dc917293925635189e982f47d35b960c3db8a9c3f11312c937673c8afdec21aa"
BASE_URL = "https://github.com/chenditc/investment_data/releases/download/2026-11-04/qlib_bin.tar.gz"
EXPECTED_1104_TOP10 = [
    "SZ300131",
    "SH603516",
    "SH688449",
    "SH688127",
    "SH688362",
    "SZ002859",
    "SZ301217",
    "SZ002484",
    "SH688372",
    "SH688182",
]
EXPECTED_1104_SCORES = [
    0.40929718035362844,
    0.4072689148225596,
    0.36113328436832115,
    0.35333355363174185,
    0.33858064784969694,
    0.33110911528051684,
    0.3218944032696024,
    0.3194477980082387,
    0.3157085932335569,
    0.31241401099219734,
]

app = modal.App("qlib-data-overlay-dryrun", image=image)
volume = modal.Volume.from_name("qlib-overlay-dryrun", create_if_missing=True)


@app.function(volumes={"/vol": volume}, cpu=8, memory=16384, timeout=7200)
def build_overlay():
    import hashlib
    import shutil

    import requests

    from data_ingest.baostock_source import BaoStockSource
    from data_ingest.pipeline import OverlayPipeline
    from data_ingest.sina import SinaSource
    from provider_archive import safe_extract_provider_archive

    base_root = Path("/vol/base")
    base_root.mkdir(parents=True, exist_ok=True)
    base_dir = base_root / "chenditc_bin"
    archive = base_root / "qlib_bin.tar.gz"
    if not base_dir.is_dir():
        if not archive.is_file():
            print("[dryrun] downloading frozen base ...", flush=True)
            with requests.get(BASE_URL, stream=True, timeout=900) as response:
                response.raise_for_status()
                with archive.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1 << 20):
                        handle.write(chunk)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if digest != BASE_ARCHIVE_SHA256:
            raise RuntimeError(f"base archive SHA mismatch: {digest}")
        extract = base_root / "extract"
        if extract.exists():
            shutil.rmtree(extract)
        root = Path(safe_extract_provider_archive(archive, extract))
        shutil.move(str(root), str(base_dir))
        print(f"[dryrun] base ready: {base_dir}", flush=True)

    out = Path("/vol/overlay_20261106")
    if out.exists():
        shutil.rmtree(out)
    pipeline = OverlayPipeline(
        sina=SinaSource(workers=6),
        baostock=BaoStockSource(),
        crosscheck_count=20,
        require_weights=True,
    )
    manifest = pipeline.build(
        base_dir,
        out,
        asof="2026-11-06",
        dates=["2026-11-05", "2026-11-06"],
        markets=("csi300", "csi500", "csi1000"),
        base_archive_sha256=BASE_ARCHIVE_SHA256,
        require_qlib_smoke=True,
    )
    volume.commit()
    return {
        "status": manifest["status"],
        "target": manifest["target"],
        "memberships": {key: value["count"] for key, value in manifest["memberships"].items()},
        "overlay": {
            key: manifest["overlay"][key]
            for key in ("feature_dirs_copied", "bins_appended", "row_symbols", "calendar_len")
        },
        "gate": manifest["gate"],
    }


def _run_model(provider: str, market: str, profile: str, topk: int, nd: int) -> dict:
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
        "kwargs": {"uri": "file:/tmp/mlruns", "default_exp_name": f"dryrun-{market}"},
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


@app.function(volumes={"/vol": volume}, cpu=20, memory=32768, timeout=7200)
def evaluate():
    import json

    volume.reload()
    base = "/vol/base/chenditc_bin"
    overlay = "/vol/overlay_20261106"
    manifest = json.loads((Path(overlay) / "manifest.json").read_text())
    results = {
        "manifest_status": manifest["status"],
        "repro_1104": _run_model(base, "csi1000", "c98856b4_top10_drop1", 10, 1),
        "csi1000_1106": _run_model(overlay, "csi1000", "c98856b4_top10_drop1", 10, 1),
        "csi500_1106": _run_model(overlay, "csi500", "raw10d_c98856b4_top20_drop2_20d", 20, 2),
        "csi300_1106": _run_model(overlay, "csi300", "59b93929_top10_drop1_10d", 10, 1),
    }
    actual = [row["instrument"] for row in results["repro_1104"]["display_top"]]
    actual_scores = [row["score"] for row in results["repro_1104"]["display_top"]]
    score_ok = len(actual_scores) == len(EXPECTED_1104_SCORES) and all(
        abs(left - right) <= 1e-9 for left, right in zip(actual_scores, EXPECTED_1104_SCORES)
    )
    results["repro_status"] = "PASS" if actual == EXPECTED_1104_TOP10 and score_ok else "FAIL"
    payload = json.dumps(results, ensure_ascii=False, indent=2)
    (Path("/vol") / "dryrun_result.json").write_text(payload + "\n")
    volume.commit()
    return results


@app.local_entrypoint()
def main():
    build = build_overlay.remote()
    result = evaluate.remote()
    result["build"] = build
    print("OVERLAY_DRYRUN " + json.dumps(result, ensure_ascii=False))
