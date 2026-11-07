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
from modal_qlib_cn_a10g import image, vol as data_volume
from daily_pipeline import WEBSITE_STAGE_TIMEOUT_SECONDS, WEBSITE_WORKER_TIMEOUT_SECONDS

app = modal.App("qlib-website-nav", image=image)
simulation = modal.Volume.from_name("qlib-website-simulation", create_if_missing=True)


def _latest_overlay_asset():
    import hashlib
    import json

    root = Path("/data/cn_data_overlay")
    if not root.is_dir():
        return None
    for path in reversed(sorted(root.iterdir())):
        manifest_path = path / "manifest.json"
        calendar_path = path / "calendars" / "day.txt"
        if not manifest_path.is_file() or not calendar_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text())
        except Exception:
            continue
        if manifest.get("status") != "PASS":
            continue
        calendar = [line.strip() for line in calendar_path.read_text().splitlines() if line.strip()]
        if not calendar:
            continue
        digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        return {
            "local_path": str(path),
            "digest": f"sha256:{digest}",
            "size": 0,
            "release": f"overlay-{calendar[-1]}",
            "published_at": manifest.get("generated_at_utc", ""),
            "source": "overlay",
        }
    return None


def latest_asset():
    overlay = _latest_overlay_asset()
    if overlay is not None:
        return overlay
    import requests

    response = requests.get("https://api.github.com/repos/chenditc/investment_data/releases/latest", timeout=30)
    response.raise_for_status()
    release = response.json()
    asset = next(a for a in release["assets"] if a["name"] == "qlib_bin.tar.gz")
    if not asset.get("digest", "").startswith("sha256:"):
        raise ValueError("upstream release must declare an immutable SHA256")
    return {
        "url": asset["browser_download_url"],
        "digest": asset["digest"],
        "size": asset["size"],
        "release": release["tag_name"],
        "published_at": release["published_at"],
    }


def make_job(asset, source, market, mode):
    return {"asset": asset, "source_manifest": source, "market": market, "mode": mode}


def fit_website_model(model, dataset):
    """Close the training recorder before a pooled container handles another market."""
    from qlib.workflow import R

    with R.start(experiment_name="website-simulation"):
        model.fit(dataset)


@app.function(
    volumes={"/simulation": simulation, "/data": data_volume},
    cpu=20,
    memory=32768,
    timeout=WEBSITE_WORKER_TIMEOUT_SECONDS,
    max_containers=3,
    retries=0,
    scaledown_window=5,
)
def build_market(job):
    import copy
    import hashlib
    import importlib.metadata
    import json
    import pickle
    import time
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import numpy as np
    import pandas as pd
    import qlib
    from qlib.data.dataset import Dataset
    from qlib.model.base import Model
    from qlib.utils import init_instance_by_config
    from csi1000_production_config import (
        CANONICAL_PROFILES_BY_MARKET,
        CANONICAL_TOPK_BY_MARKET,
        CANONICAL_ND_BY_MARKET,
        profile_manifest,
        stable_json_sha256,
    )
    from qlib_live_retrain import configure_asof
    from modal_qlib_cn_a10g import _load_and_patch_cfg, MODEL_CFG, _cn_trading_calendar
    from october_backfill_plan import fit_blocks
    from october_backfill_modal import download_provider
    from prelive_revalidation import prepare_data, replay, write_result
    from research_identity import verify_source_manifest, content_sha
    from website_simulation import (
        FIRST_EXECUTION,
        FIRST_SIGNAL,
        build_website_series,
        validate_website_series,
        write_immutable,
        training_prefix_fingerprint,
    )

    started = time.monotonic()
    source_sha = verify_source_manifest(job["source_manifest"], "training")
    market, mode, asset = job["market"], job["mode"], job["asset"]
    if mode not in ("smoke", "full") or market not in CANONICAL_PROFILES_BY_MARKET:
        raise ValueError("unknown simulation market/mode")
    simulation.reload()
    root = Path("/simulation") / source_sha / asset["digest"].split(":")[1]
    smoke_path = root / "smoke.json"
    if mode == "full":
        smoke = json.loads(smoke_path.read_text())
        if (
            smoke.get("status") != "SMOKE_PASS"
            or smoke.get("source_sha256") != source_sha
            or smoke.get("asset_digest") != asset["digest"]
            or smoke.get("prefix_prediction_exact") is not True
        ):
            raise ValueError("matching successful cloud smoke required before full replay")
    provider = download_provider(asset)
    calendar = (provider / "calendars/day.txt").read_text().splitlines()
    verified_calendar = sorted(_cn_trading_calendar())
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    completed = [
        d
        for d in verified_calendar
        if d < now.date().isoformat() or (d == now.date().isoformat() and (now.hour, now.minute) >= (15, 30))
    ]
    if not completed or calendar[-1] > completed[-1]:
        raise ValueError("provider contains an incomplete or future trading session")
    end = calendar[-1]
    if [d for d in calendar if FIRST_EXECUTION <= d <= end] != [
        d for d in verified_calendar if FIRST_EXECUTION <= d <= end
    ]:
        raise ValueError("provider/external calendar session mismatch")
    blocks = fit_blocks(calendar, FIRST_EXECUTION, end)
    if blocks[0]["fit_asof"] != FIRST_SIGNAL:
        raise ValueError("October 8 first execution requires September 30 close features")
    if mode == "smoke":
        blocks = blocks[:1]
        end = blocks[-1]["signal_end"]
    finished = root / market / mode / "result.json"
    if finished.is_file():
        cached = json.loads(finished.read_text())
        validate_website_series(cached["series"], market=market)
        if (
            cached.get("source_sha256") != source_sha
            or cached.get("asset_digest") != asset["digest"]
            or cached["series"]["source_manifest_sha256"] != source_sha
            or cached["series"]["provider_archive_sha256"] != asset["digest"].split(":")[1]
        ):
            raise ValueError("cached website replay input identity mismatch")
        for relative, digest in cached["ledger_file_sha256"].items():
            path = finished.parent / relative
            if (
                ".." in Path(relative).parts
                or Path(relative).is_absolute()
                or hashlib.sha256(path.read_bytes()).hexdigest() != digest
            ):
                raise ValueError("cached website ledger file hash mismatch")
        verify_source_manifest(job["source_manifest"], "training")
        return cached
    profile = CANONICAL_PROFILES_BY_MARKET[market]
    horizon = profile_manifest(profile)["label_horizon"]
    qlib.init(
        provider_uri=str(provider),
        region="cn",
        kernels=8,
        exp_manager={
            "class": "MLflowExpManager",
            "module_path": "qlib.workflow.expm",
            "kwargs": {"uri": "file:/tmp/website-mlruns", "default_exp_name": "website-simulation"},
        },
    )
    forecasts, fits = [], []
    new_fits = 0
    for block in blocks:
        cfg = _load_and_patch_cfg(
            MODEL_CFG["lgb158"],
            smoke=False,
            recent=True,
            long_train=True,
            label20=horizon == 20,
            market=market,
            provider_dir=str(provider),
            csi1000_profile=profile,
        )
        temporal = configure_asof(
            cfg, calendar[: calendar.index(block["fit_asof"]) + 1], block["fit_asof"], horizon=horizon
        )
        if stable_json_sha256(cfg["task"]["model"]) != profile_manifest(profile)["model_config_sha256"]:
            raise ValueError("canonical model parameters changed")
        fit_identity = {
            "source_sha256": source_sha,
            "market": market,
            "profile": profile,
            "fit_asof": block["fit_asof"],
            "training_contract": temporal,
            "data_prefix_sha256": training_prefix_fingerprint(provider, market, block["fit_asof"]),
            "dependencies": {p: importlib.metadata.version(p) for p in ("lightgbm", "pandas", "numpy", "pyqlib")},
        }
        model_dir = Path("/simulation/model_cache") / market / content_sha(fit_identity)
        model_path, meta_path = model_dir / "model.pkl", model_dir / "receipt.json"
        dk = cfg["task"]["dataset"]["kwargs"]
        prefix_cfg = copy.deepcopy(cfg)
        dk["segments"]["test"] = [block["fit_asof"], block["signal_end"]]
        dk["handler"]["kwargs"]["end_time"] = block["signal_end"]
        dataset = init_instance_by_config(cfg["task"]["dataset"], accept_types=Dataset)
        if model_path.is_file() and meta_path.is_file():
            meta = json.loads(meta_path.read_text())
            if (
                meta["identity"] != fit_identity
                or hashlib.sha256(model_path.read_bytes()).hexdigest() != meta["model_sha256"]
            ):
                raise ValueError("website model cache hash/identity mismatch")
            model = pickle.loads(model_path.read_bytes())
            print(f"[website] cached model {market} {block['fit_asof']}", flush=True)
        else:
            new_fits += 1
            if new_fits > 3:
                raise ValueError("more than three fresh fits require a separately bounded recovery batch")
            print(f"[website] fit {market} {block['fit_asof']}..{block['signal_end']}", flush=True)
            model = init_instance_by_config(cfg["task"]["model"], accept_types=Model)
            fit_website_model(model, dataset)
            model_dir.mkdir(parents=True, exist_ok=True)
            model_path.write_bytes(pickle.dumps(model, protocol=pickle.HIGHEST_PROTOCOL))
            meta = {"identity": fit_identity, "model_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest()}
            meta_path.write_text(json.dumps(meta, sort_keys=True, indent=2))
            simulation.commit()
        verify_source_manifest(job["source_manifest"], "training")
        pred = model.predict(dataset, segment="test")
        if isinstance(pred, pd.DataFrame):
            pred = pred.iloc[:, 0]
        pred = pred.sort_index()
        if (
            pred.index.has_duplicates
            or not np.isfinite(pred.to_numpy()).all()
            or sorted(str(d)[:10] for d in pred.index.get_level_values(0).unique()) != block["signal_days"]
        ):
            raise ValueError("website prediction coverage/finiteness mismatch")
        if mode == "smoke":
            prefix_ds = init_instance_by_config(prefix_cfg["task"]["dataset"], accept_types=Dataset)
            prefix = model.predict(prefix_ds, segment="test")
            if isinstance(prefix, pd.DataFrame):
                prefix = prefix.iloc[:, 0]
            pd.testing.assert_series_equal(
                pred.loc[[pd.Timestamp(block["fit_asof"])]].sort_index(),
                prefix.sort_index(),
                check_exact=True,
                check_names=False,
            )
        forecasts.append(pred.to_frame("score"))
        fits.append(
            {
                "fit_asof": block["fit_asof"],
                "signal_end": block["signal_end"],
                "profile": profile,
                "model_sha256": meta["model_sha256"],
                "training_contract": temporal,
                "data_prefix_sha256": fit_identity["data_prefix_sha256"],
            }
        )
    merged = pd.concat(forecasts).sort_index()
    signal_dir = root / market / mode
    signal_dir.mkdir(parents=True, exist_ok=True)
    signal_path = signal_dir / "signal.parquet"
    merged.to_parquet(signal_path)
    data = prepare_data(provider, [signal_path], market, FIRST_SIGNAL, end)
    candidate = {
        "id": profile,
        "market": market,
        "topk": CANONICAL_TOPK_BY_MARKET[market],
        "dropn": CANONICAL_ND_BY_MARKET[market],
    }
    result = replay(data, candidate, engine_history_limit=120)
    series = build_website_series(
        market, result, calendar=calendar, source_sha=source_sha, provider_sha=asset["digest"].split(":")[1], fits=fits
    )
    verify_source_manifest(job["source_manifest"], "training")
    ledger_dir = signal_dir / "ledger"
    if ledger_dir.exists():
        # A prior interruption left an unpublished, reconstructible staging directory.
        import shutil

        shutil.rmtree(ledger_dir)
    write_result(result, ledger_dir)
    (signal_dir / "final_state.json").write_text(json.dumps(result["final_state"], indent=2, allow_nan=False))
    (signal_dir / "input_manifest.json").write_text(json.dumps(data["provenance"], indent=2))
    payload = {
        "status": "SMOKE_PASS" if mode == "smoke" else "COMPLETED",
        "market": market,
        "source_sha256": source_sha,
        "asset_digest": asset["digest"],
        "provider_calendar_end": calendar[-1],
        "previous_completed_session": completed[-1],
        "provider_lag_sessions": len([d for d in completed if d > calendar[-1]]),
        "prefix_prediction_exact": mode == "smoke",
        "new_fits": new_fits,
        "series": series,
        "wall_seconds": time.monotonic() - started,
    }
    payload["ledger_file_sha256"] = {
        str(p.relative_to(signal_dir)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(signal_dir.rglob("*"))
        if p.is_file() and p.name != "result.json"
    }
    write_immutable(finished, json.dumps(payload, indent=2, allow_nan=False) + "\n")
    if mode == "smoke":
        write_immutable(
            smoke_path,
            json.dumps(
                {k: payload[k] for k in ("status", "source_sha256", "asset_digest", "prefix_prediction_exact")},
                sort_keys=True,
            ),
        )
    simulation.commit()
    print(
        f"[website] {market} {payload['status']} days={len(series['points'])} return={series['cumulative_return']:.4%}",
        flush=True,
    )
    return payload


def publish_results(results, source, asset, *, token):
    import json
    import requests
    from research_identity import verify_source_manifest
    from website_simulation import validate_website_series, write_immutable
    from github_commit import push_files
    from modal_qlib_cn_a10g import GITHUB_REPO, SIGNAL_BRANCH

    source_sha = verify_source_manifest(source, "training")
    if len(results) != 3 or {r["market"] for r in results} != {"csi300", "csi500", "csi1000"}:
        raise ValueError("all three simulation accounts must succeed together")
    if len({r["series"]["latest_date"] for r in results}) != 1:
        raise ValueError("website markets have different data dates")
    files = {}
    for r in results:
        if r["status"] != "COMPLETED" or r["source_sha256"] != source_sha or r["asset_digest"] != asset["digest"]:
            raise ValueError("website publication source/provider mismatch")
        s = validate_website_series(r["series"], market=r["market"])
        if s["source_manifest_sha256"] != source_sha or s["provider_archive_sha256"] != asset["digest"].split(":")[1]:
            raise ValueError("website series source/provider differs from publication inputs")
        content = json.dumps(s, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        files[f"quant/data/signals/{r['market']}_website_performance.json"] = content
        files[f"quant/data/signals/{r['market']}_forward_performance.json"] = content
        files[f"quant/data/signals/{r['market']}_backfill_performance.json"] = content
        files[f"quant/data/website_simulation/{source_sha}/{asset['digest'].split(':')[1]}/{r['market']}.json"] = (
            content
        )
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    missing = {}
    for path, content in files.items():
        response = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}",
            params={"ref": SIGNAL_BRANCH},
            headers=headers,
            timeout=30,
        )
        if response.status_code == 404:
            missing[path] = content
        else:
            response.raise_for_status()
            import base64

            if base64.b64decode(response.json()["content"]) != content.encode():
                if path.startswith("quant/data/website_simulation/"):
                    raise ValueError("published immutable website snapshot conflict")
                previous = json.loads(base64.b64decode(response.json()["content"]))
                previous_date = previous.get("latest_date")
                if previous_date is not None and previous_date != "":
                    from datetime import date

                    if (
                        not isinstance(previous_date, str)
                        or date.fromisoformat(previous_date).isoformat() != previous_date
                    ):
                        raise ValueError("previous website latest_date is invalid")
                if previous_date and previous_date > s["latest_date"]:
                    raise ValueError("refusing to replace newer website data with an older run")
                missing[path] = content
    receipt_path = Path("/simulation") / source_sha / asset["digest"].split(":")[1] / "publication.json"
    if not missing:
        if not receipt_path.exists():
            write_immutable(
                receipt_path,
                json.dumps(
                    {
                        "status": "RECOVERED_AFTER_PUBLICATION",
                        "source_sha256": source_sha,
                        "asset_digest": asset["digest"],
                        "files": len(files),
                    },
                    indent=2,
                ),
            )
            simulation.commit()
        return {"status": "ALREADY_PUBLISHED", "source_sha256": source_sha, "files": len(files)}
    commit = push_files(
        token,
        GITHUB_REPO,
        SIGNAL_BRANCH,
        missing,
        f"chore(nav): update three October 8 simulated accounts through {results[0]['series']['latest_date']}",
    )
    receipt = {
        "status": "PUBLISHED",
        "commit": commit,
        "source_sha256": source_sha,
        "asset_digest": asset["digest"],
        "files": len(files),
    }
    write_immutable(receipt_path, json.dumps(receipt, indent=2) + "\n")
    simulation.commit()
    return receipt


@app.function(
    volumes={"/simulation": simulation, "/data": data_volume},
    secrets=[modal.Secret.from_name("github-push")],
    timeout=WEBSITE_STAGE_TIMEOUT_SECONDS,
    cpu=1,
    memory=2048,
    max_containers=1,
    retries=0,
)
def daily(expected_source_sha256: str):
    import os
    from research_identity import source_manifest

    source = source_manifest("training")
    if expected_source_sha256 != source["sha256"]:
        raise ValueError("signal/website deployments have different source identities")
    asset = latest_asset()
    build_market.remote(make_job(asset, source, "csi300", "smoke"))
    results = list(
        build_market.map(
            [make_job(asset, source, m, "full") for m in ("csi300", "csi500", "csi1000")], order_outputs=False
        )
    )
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
