"""Modal-free website replay, cache, ledger and publication core.

The Modal launcher keeps only App/Volume/resource decorators and routing.  This
module owns the business semantics used by the website semantic identity:
model fit/predict, incremental ancestor checks, replay inputs, series and
publication validation.  It never imports Modal.
"""

from __future__ import annotations

from pathlib import Path


def make_job(asset, source, market, mode, *, required_replay_mode=None, semantic=None, account=None):
    """Build a worker job carrying release, semantic and account identities."""
    from research_identity import account_contract_manifest, website_semantic_manifest
    from website_simulation import account_contract_for_market

    job = {
        "asset": asset,
        "source_manifest": source,
        "semantic_manifest": semantic if semantic is not None else website_semantic_manifest(),
        "account_contract": (
            account if account is not None else account_contract_manifest(account_contract_for_market(market))
        ),
        "market": market,
        "mode": mode,
    }
    if required_replay_mode is not None:
        job["required_replay_mode"] = required_replay_mode
    return job


def fit_website_model(model, dataset):
    """Close the training recorder before a pooled container handles another market."""
    from qlib.workflow import R

    with R.start(experiment_name="website-simulation"):
        model.fit(dataset)


def _sha256_path(path):
    import hashlib

    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _provider_calendar(asset):
    calendar = asset.get("calendar")
    if isinstance(calendar, list) and calendar:
        return [str(item) for item in calendar]
    if asset.get("local_path"):
        path = Path(asset["local_path"]) / "calendars" / "day.txt"
        if path.is_file():
            return [line.strip() for line in path.read_text().splitlines() if line.strip()]
    return None


def _overlay_base(asset):
    """Return the immediate base identity recorded by the current provider."""
    import json

    base = asset.get("base")
    if isinstance(base, dict) and base.get("calendar_end"):
        return {
            "path": str(base.get("path") or ""),
            "calendar_end": str(base.get("calendar_end") or "")[:10],
            "digest": str(base.get("digest") or base.get("archive_sha256") or ""),
        }
    if not asset.get("local_path"):
        return None
    manifest = Path(asset["local_path"]) / "manifest.json"
    if not manifest.is_file():
        return None
    try:
        payload = json.loads(manifest.read_text())
    except Exception:
        return None
    base = payload.get("base") or {}
    return {
        "path": str(base.get("path") or ""),
        "calendar_end": str(base.get("calendar_end") or "")[:10],
        "digest": str(base.get("digest") or base.get("archive_sha256") or ""),
    }


def _cached_model(
    cache_sha, market, profile, fit_asof, expected_sha, *, load=True, simulation_root=Path("/simulation")
):
    """Load the exact model that generated a prior published signal prefix."""
    import hashlib
    import json
    import pickle

    root = Path(simulation_root) / "model_cache" / market
    if not root.is_dir():
        return None
    for receipt in sorted(root.glob("*/receipt.json")):
        try:
            meta = json.loads(receipt.read_text())
            identity = meta.get("identity") or {}
            if (
                identity.get("cache_sha256") != cache_sha
                or identity.get("market") != market
                or identity.get("profile") != profile
                or identity.get("fit_asof") != fit_asof
                or meta.get("model_sha256") != expected_sha
            ):
                continue
            model_path = receipt.with_name("model.pkl")
            if not model_path.is_file():
                continue
            if hashlib.sha256(model_path.read_bytes()).hexdigest() != expected_sha:
                continue
            if not load:
                return model_path, meta
            return pickle.loads(model_path.read_bytes()), meta
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    return None


def _incremental_prior(
    cache_sha,
    asset,
    market,
    end,
    calendar,
    *,
    semantic_sha,
    account_sha,
    simulation_root=Path("/simulation"),
):
    """Find and validate the immediately preceding same-source replay result.

    Incremental mode is intentionally restricted to exactly one new execution
    session.  A missed day, a missing model cache, or a non-ancestor overlay
    falls back to the full replay instead of splicing arbitrary history.
    """
    import json
    import pandas as pd
    from csi1000_production_config import (
        CANONICAL_ND_BY_MARKET,
        CANONICAL_PROFILES_BY_MARKET,
        CANONICAL_TOPK_BY_MARKET,
    )
    from october_backfill_plan import fit_blocks
    from website_simulation import FIRST_EXECUTION, FIRST_SIGNAL, validate_website_series

    if end not in calendar:
        return None
    end_index = calendar.index(end)
    if end_index < 1:
        return None
    previous = calendar[end_index - 1]
    source_root = Path(simulation_root) / cache_sha
    if not source_root.is_dir():
        return None
    current_digest = asset["digest"].split(":")[1]
    base = _overlay_base(asset)
    if base is None or base["calendar_end"] != previous:
        return None
    base_digest = str(base.get("digest") or "")
    blocks = fit_blocks(calendar, FIRST_EXECUTION, end)
    block = next((item for item in blocks if item["fit_asof"] <= end <= item["signal_end"]), None)
    if block is None:
        return None
    candidates = []
    for asset_dir in sorted(source_root.iterdir()):
        if not asset_dir.is_dir() or asset_dir.name == current_digest:
            continue
        result_dir = asset_dir / market / "full"
        result_path = result_dir / "result.json"
        final_path = result_dir / "final_state.json"
        signal_path = result_dir / "signal.parquet"
        if not (result_path.is_file() and final_path.is_file() and signal_path.is_file()):
            continue
        try:
            payload = json.loads(result_path.read_text())
            series = payload.get("series") or {}
            if (
                payload.get("status") != "COMPLETED"
                or payload.get("market") != market
                or payload.get("website_semantic_sha256") != semantic_sha
                or payload.get("account_contract_sha256") != account_sha
                or payload.get("asset_digest") != f"sha256:{asset_dir.name}"
                or payload.get("provider_calendar_end") != previous
                or (base_digest and payload.get("asset_digest") != f"sha256:{base_digest}")
                or series.get("market") != market
                or series.get("website_semantic_sha256") != semantic_sha
                or series.get("account_contract_sha256") != account_sha
                or series.get("provider_archive_sha256") != asset_dir.name
                or series.get("latest_date") != previous
            ):
                continue
            # Plan A: a provider-source change is a lineage boundary.  Never
            # splice an overlay series onto a chenditc series (or vice versa);
            # replay the whole account under the current source instead.
            if payload.get("provider_source") != asset.get("source"):
                continue
            validate_website_series(series, market=market)
            identity = series.get("account_identity") or {}
            if (
                identity.get("profile") != CANONICAL_PROFILES_BY_MARKET[market]
                or identity.get("topk") != CANONICAL_TOPK_BY_MARKET[market]
                or identity.get("nd") != CANONICAL_ND_BY_MARKET[market]
            ):
                continue
            state = json.loads(final_path.read_text())
            if (
                state.get("version") != 2
                or state.get("last_execution_date") != previous
                or state.get("pending_signal") is not None
                or (state.get("config") or {}).get("topk") != CANONICAL_TOPK_BY_MARKET[market]
                or (state.get("config") or {}).get("nd") != CANONICAL_ND_BY_MARKET[market]
                or (state.get("config") or {}).get("initial_cash") != series.get("initial_cash")
            ):
                continue
            for relative, digest in (payload.get("ledger_file_sha256") or {}).items():
                rel = Path(relative)
                if rel.is_absolute() or ".." in rel.parts or _sha256_path(result_dir / rel) != digest:
                    raise ValueError(f"prior ledger hash mismatch: {relative}")
            signal = pd.read_parquet(signal_path)["score"].sort_index()
            if list(signal.index.names) != ["datetime", "instrument"] or signal.index.has_duplicates:
                raise ValueError("prior signal schema is invalid")
            dates = sorted({str(day)[:10] for day in signal.index.get_level_values(0)})
            if dates != [day for day in calendar if FIRST_SIGNAL <= day <= previous]:
                raise ValueError("prior signal prefix is incomplete")
            prior_fits = series.get("fit_receipts") or []
            if block["fit_asof"] <= previous:
                covering = [fit for fit in prior_fits if fit.get("fit_asof") == block["fit_asof"]]
                if len(covering) != 1:
                    raise ValueError("prior fit does not cover the next signal day")
                if (
                    _cached_model(
                        cache_sha,
                        market,
                        CANONICAL_PROFILES_BY_MARKET[market],
                        block["fit_asof"],
                        covering[0]["model_sha256"],
                        load=False,
                        simulation_root=simulation_root,
                    )
                    is None
                ):
                    raise ValueError("prior model cache is unavailable")
            candidates.append(
                {
                    "previous": previous,
                    "result_dir": result_dir,
                    "signal_path": signal_path,
                    "final_state": state,
                    "payload": payload,
                    "asset_digest": payload["asset_digest"],
                    "new_fit_required": block["fit_asof"] > previous,
                    "fit_asof": block["fit_asof"],
                }
            )
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            print(f"[website] incremental prefix rejected {asset_dir.name[:12]} {market}: {exc}", flush=True)
            continue
    if len(candidates) > 1:
        print(f"[website] {market} has multiple incremental prefixes; using full replay", flush=True)
        return None
    return candidates[0] if candidates else None


def _load_prior_ledger(result_dir):
    import json
    import pandas as pd

    ledger = result_dir / "ledger"
    return {
        "report": pd.read_parquet(ledger / "report.parquet"),
        "sessions": [json.loads(line) for line in (ledger / "sessions.jsonl").read_text().splitlines() if line.strip()],
        "orders": pd.read_parquet(ledger / "orders.parquet").to_dict("records"),
        "metrics": json.loads((ledger / "metrics.json").read_text()),
        "gaps": json.loads((ledger / "gaps.json").read_text()),
        "final_state": json.loads((result_dir / "final_state.json").read_text()),
    }


def _predict_days(model, cfg, provider, calendar, days):
    """Predict recent days from a bounded window; Alpha158 features are causal."""
    import copy
    import numpy as np
    import pandas as pd
    from qlib.data.dataset import Dataset
    from qlib.utils import init_instance_by_config

    first = days[0]
    start = calendar[max(0, calendar.index(first) - 130)]
    light = copy.deepcopy(cfg)
    handler = light["task"]["dataset"]["kwargs"]["handler"]["kwargs"]
    handler["start_time"] = start
    handler["end_time"] = days[-1]
    handler["fit_start_time"] = start
    handler["fit_end_time"] = days[-1]
    light["task"]["dataset"]["kwargs"]["segments"] = {"test": [first, days[-1]]}
    dataset = init_instance_by_config(light["task"]["dataset"], accept_types=Dataset)
    pred = model.predict(dataset, segment="test")
    if isinstance(pred, pd.DataFrame):
        pred = pred.iloc[:, 0]
    pred = pred.sort_index()
    actual = sorted({str(day)[:10] for day in pred.index.get_level_values(0)})
    if (
        pred.index.has_duplicates
        or not np.isfinite(pred.to_numpy()).all()
        or actual != days
        or list(pred.index.names) != ["datetime", "instrument"]
    ):
        raise ValueError("incremental prediction coverage/finiteness mismatch")
    return pred


def _validate_required_replay_mode(job, actual, *, cached=False):
    """Keep the lightweight worker from silently executing a full replay."""
    required = (job or {}).get("required_replay_mode")
    if required not in (None, "INCREMENTAL"):
        raise ValueError(f"unknown required replay mode: {required}")
    if actual not in ("FULL", "INCREMENTAL"):
        raise ValueError(f"unknown actual replay mode: {actual}")
    if required is not None and actual != required and not cached:
        raise ValueError(f"incremental worker requires {required}, got {actual}")


def market_name(job):
    return str((job or {}).get("market") or "unknown")


def _build_market_impl(
    job,
    sampler=None,
    *,
    simulation_root=Path("/simulation"),
    reload_fn=None,
    commit_fn=None,
):
    import sys

    before_imports = set(sys.modules)
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
    from qlib_runtime import MODEL_CFG, cn_trading_calendar, load_and_patch_cfg
    from october_backfill_plan import fit_blocks
    from provider_asset import download_provider
    from prelive_revalidation import execution_quote_symbols, prepare_data, replay, write_result
    from research_identity import (
        content_sha,
        verify_account_contract,
        verify_loaded_semantic_closure,
        verify_source_manifest,
        verify_website_semantic_manifest,
        website_cache_key,
    )
    from website_simulation import (
        FIRST_EXECUTION,
        FIRST_SIGNAL,
        account_contract_for_market,
        build_website_series,
        validate_website_series,
        write_immutable,
        training_prefix_fingerprint,
    )

    started = time.monotonic()
    release_sha = verify_source_manifest(job["source_manifest"], "training")
    market, mode, asset = job["market"], job["mode"], job["asset"]
    if mode not in ("smoke", "full") or market not in CANONICAL_PROFILES_BY_MARKET:
        raise ValueError("unknown simulation market/mode")
    semantic_sha = verify_website_semantic_manifest(job["semantic_manifest"])
    account_sha = verify_account_contract(job["account_contract"], account_contract_for_market(market))
    cache_sha = website_cache_key(semantic_sha, account_sha)
    loaded = []
    for name, module in sys.modules.items():
        if name in before_imports or module is None:
            continue
        path = getattr(module, "__file__", None)
        if path and Path(path).resolve().is_relative_to((Path(__file__).resolve().parents[3] / "quant/code")):
            loaded.append(path)
    verify_loaded_semantic_closure(loaded)
    if reload_fn is not None:
        reload_fn()
    if commit_fn is None:
        commit_fn = lambda: None
    root = Path(simulation_root) / cache_sha / asset["digest"].split(":")[1]
    smoke_path = root / "smoke.json"
    provider = download_provider(asset)
    # Candidate checks run before materialization; the replay/QLib path needs a
    # local provider.  Keep the archive digest identity unchanged.
    asset = {**asset, "local_path": str(provider)}
    calendar = (provider / "calendars/day.txt").read_text().splitlines()
    verified_calendar = sorted(cn_trading_calendar())
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
            cached.get("website_semantic_sha256") != semantic_sha
            or cached.get("account_contract_sha256") != account_sha
            or cached.get("asset_digest") != asset["digest"]
            or cached["series"]["website_semantic_sha256"] != semantic_sha
            or cached["series"]["account_contract_sha256"] != account_sha
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
        # A lost publish response may leave a fully validated immutable FULL
        # result behind. Reusing it is recovery, not a full replay on this
        # lightweight worker.
        _validate_required_replay_mode(job, cached.get("replay_mode"), cached=True)
        return cached
    incremental = (
        _incremental_prior(
            cache_sha,
            asset,
            market,
            end,
            calendar,
            semantic_sha=semantic_sha,
            account_sha=account_sha,
            simulation_root=simulation_root,
        )
        if mode == "full"
        else None
    )
    _validate_required_replay_mode(job, "INCREMENTAL" if incremental is not None else "FULL")
    if mode == "full" and incremental is None:
        smoke = json.loads(smoke_path.read_text())
        if (
            smoke.get("status") != "SMOKE_PASS"
            or smoke.get("website_semantic_sha256") != semantic_sha
            or smoke.get("account_contract_sha256") != account_sha
            or smoke.get("asset_digest") != asset["digest"]
            or smoke.get("prefix_prediction_exact") is not True
        ):
            raise ValueError("matching successful cloud smoke required before full replay")
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
    candidate = {
        "id": profile,
        "market": market,
        "topk": CANONICAL_TOPK_BY_MARKET[market],
        "dropn": CANONICAL_ND_BY_MARKET[market],
    }
    new_fits = 0

    def fit_block(block):
        nonlocal new_fits
        cfg = load_and_patch_cfg(
            MODEL_CFG["lgb158"],
            smoke=False,
            recent=True,
            long_train=True,
            label20=horizon == 20,
            market=market,
            provider_dir=str(provider),
            csi1000_profile=profile,
            data_dir=str(provider),
            mlruns_dir="/tmp/website-mlruns",
        )
        temporal = configure_asof(
            cfg, calendar[: calendar.index(block["fit_asof"]) + 1], block["fit_asof"], horizon=horizon
        )
        if stable_json_sha256(cfg["task"]["model"]) != profile_manifest(profile)["model_config_sha256"]:
            raise ValueError("canonical model parameters changed")
        fit_identity = {
            "cache_sha256": cache_sha,
            "website_semantic_sha256": semantic_sha,
            "account_contract_sha256": account_sha,
            "market": market,
            "profile": profile,
            "fit_asof": block["fit_asof"],
            "training_contract": temporal,
            "data_prefix_sha256": training_prefix_fingerprint(provider, market, block["fit_asof"]),
            "dependencies": {p: importlib.metadata.version(p) for p in ("lightgbm", "pandas", "numpy", "pyqlib")},
        }
        model_dir = Path(simulation_root) / "model_cache" / market / content_sha(fit_identity)
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
            commit_fn()
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
            # Compare the same signal day from both dataset horizons; the
            # prefix dataset intentionally covers the full test window.
            pd.testing.assert_series_equal(
                pred.loc[[pd.Timestamp(block["fit_asof"])]].sort_index(),
                prefix.loc[[pd.Timestamp(block["fit_asof"])]].sort_index(),
                check_exact=True,
                check_names=False,
            )
        receipt = {
            "fit_asof": block["fit_asof"],
            "signal_end": block["signal_end"],
            "profile": profile,
            "model_sha256": meta["model_sha256"],
            "training_contract": temporal,
            "data_prefix_sha256": fit_identity["data_prefix_sha256"],
        }
        return pred, receipt

    incremental_evidence = None
    if incremental is not None:
        previous = incremental["previous"]
        print(
            f"[website] {market} incremental prefix {previous} -> {end} " f"(asset {incremental['asset_digest'][:19]})",
            flush=True,
        )
        prior_signal = pd.read_parquet(incremental["signal_path"])["score"].sort_index()
        prior_signal = prior_signal[prior_signal.index.get_level_values(0) <= pd.Timestamp(previous)]
        prior_fits = copy.deepcopy(incremental["payload"]["series"]["fit_receipts"])
        block = next(item for item in blocks if item["fit_asof"] <= end <= item["signal_end"])
        if block["fit_asof"] <= previous:
            covering = [fit for fit in prior_fits if fit.get("fit_asof") == block["fit_asof"]]
            if len(covering) != 1:
                raise ValueError("incremental fit receipt does not cover the previous session")
            model, meta = _cached_model(
                cache_sha,
                market,
                profile,
                block["fit_asof"],
                covering[0]["model_sha256"],
                simulation_root=simulation_root,
            )
            if model is None:
                raise ValueError("incremental model cache disappeared after prefix validation")
            cfg = load_and_patch_cfg(
                MODEL_CFG["lgb158"],
                smoke=False,
                recent=True,
                long_train=True,
                label20=horizon == 20,
                market=market,
                provider_dir=str(provider),
                csi1000_profile=profile,
            )
            pred = _predict_days(model, cfg, provider, calendar, [previous, end])
            expected = prior_signal.xs(pd.Timestamp(previous), level="datetime").sort_index()
            actual = pred.xs(pd.Timestamp(previous), level="datetime").sort_index()
            pd.testing.assert_series_equal(actual, expected, check_exact=True, check_names=False)
            for fit in prior_fits:
                if fit["fit_asof"] == block["fit_asof"]:
                    if fit["model_sha256"] != meta["model_sha256"]:
                        raise ValueError("incremental model hash changed at the prefix boundary")
                    fit["signal_end"] = end
            fits = prior_fits
            new_pred = pred.loc[[pd.Timestamp(end)]]
            incremental_evidence = {
                "previous_result": str(incremental["result_dir"]),
                "previous_asset_digest": incremental["asset_digest"],
                "prefix_prediction_exact": True,
                "new_fit": False,
            }
        else:
            if prior_fits and prior_fits[-1]["signal_end"] >= block["fit_asof"]:
                raise ValueError("incremental fit blocks overlap the prior signal")
            pred, receipt = fit_block(block)
            if receipt["signal_end"] != end:
                raise ValueError("incremental fit did not extend through the new session")
            fits = prior_fits + [receipt]
            new_pred = pred.loc[[pd.Timestamp(end)]]
            incremental_evidence = {
                "previous_result": str(incremental["result_dir"]),
                "previous_asset_digest": incremental["asset_digest"],
                "prefix_prediction_exact": None,
                "new_fit": True,
            }
        merged = pd.concat([prior_signal, new_pred.to_frame("score")]).sort_index()
        if merged.index.has_duplicates:
            raise ValueError("incremental signal prefix overlaps the new session")
        print(f"[website] {market} incremental signal ready elapsed={time.monotonic() - started:.1f}s", flush=True)
        signal_dir = root / market / mode
        signal_dir.mkdir(parents=True, exist_ok=True)
        signal_path = signal_dir / "signal.parquet"
        merged.to_parquet(signal_path)
        prior_ledger = _load_prior_ledger(incremental["result_dir"])
        restored_state = prior_ledger["final_state"]
        if restored_state != incremental["final_state"]:
            raise ValueError("incremental ledger checkpoint differs from the validated final state")
        data = prepare_data(
            provider,
            [signal_path],
            market,
            previous,
            end,
            extra_quote_symbols=execution_quote_symbols(restored_state),
        )
        print(f"[website] {market} incremental inputs ready elapsed={time.monotonic() - started:.1f}s", flush=True)
        result = replay(
            data,
            candidate,
            engine_history_limit=120,
            initial_state=restored_state,
            prior=prior_ledger,
        )
        print(f"[website] {market} incremental replay done elapsed={time.monotonic() - started:.1f}s", flush=True)
        replay_mode = "INCREMENTAL"
    else:
        forecasts, fits = [], []
        for block in blocks:
            pred, receipt = fit_block(block)
            forecasts.append(pred.to_frame("score"))
            fits.append(receipt)
        merged = pd.concat(forecasts).sort_index()
        signal_dir = root / market / mode
        signal_dir.mkdir(parents=True, exist_ok=True)
        signal_path = signal_dir / "signal.parquet"
        merged.to_parquet(signal_path)
        data = prepare_data(provider, [signal_path], market, FIRST_SIGNAL, end)
        result = replay(data, candidate, engine_history_limit=120)
        replay_mode = "FULL"
    _validate_required_replay_mode(job, replay_mode)
    series = build_website_series(
        market,
        result,
        calendar=calendar,
        source_sha=release_sha,
        semantic_sha=semantic_sha,
        account_sha=account_sha,
        provider_sha=asset["digest"].split(":")[1],
        fits=fits,
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
        "source_sha256": release_sha,
        "release_source_sha256": release_sha,
        "website_semantic_sha256": semantic_sha,
        "account_contract_sha256": account_sha,
        "cache_sha256": cache_sha,
        "semantic_manifest": job["semantic_manifest"],
        "account_contract": job["account_contract"],
        "asset_digest": asset["digest"],
        "provider_calendar_end": calendar[-1],
        "previous_completed_session": completed[-1],
        "provider_lag_sessions": len([d for d in completed if d > calendar[-1]]),
        "provider_source": asset.get("source", ""),
        "prefix_prediction_exact": mode == "smoke",
        "replay_mode": replay_mode,
        "incremental": incremental_evidence,
        "new_fits": new_fits,
        "series": series,
        "wall_seconds": time.monotonic() - started,
    }
    payload["ledger_file_sha256"] = {
        str(p.relative_to(signal_dir)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(signal_dir.rglob("*"))
        if p.is_file() and p.name != "result.json"
    }
    # Stop before the immutable result is written so the persisted resource
    # summary is the final one, not a snapshot taken while sampling continued.
    payload["resources"] = sampler.stop() if sampler is not None else None
    write_immutable(finished, json.dumps(payload, indent=2, allow_nan=False) + "\n")
    if mode == "smoke":
        write_immutable(
            smoke_path,
            json.dumps(
                {
                    k: payload[k]
                    for k in (
                        "status",
                        "source_sha256",
                        "website_semantic_sha256",
                        "account_contract_sha256",
                        "asset_digest",
                        "prefix_prediction_exact",
                    )
                },
                sort_keys=True,
            ),
        )
    commit_fn()
    print(
        f"[website] {market} {payload['status']} mode={replay_mode} days={len(series['points'])} "
        f"return={series['cumulative_return']:.4%}",
        flush=True,
    )
    return payload


def publish_results(results, source, asset, *, token, simulation_root=Path("/simulation"), commit_fn=None):
    import hashlib
    import json
    import requests
    from research_identity import (
        verify_account_contract,
        verify_source_manifest,
        verify_website_semantic_manifest,
    )
    from website_simulation import account_contract_for_market, validate_website_series, write_immutable
    from github_commit import push_files
    from github_config import GITHUB_REPO, SIGNAL_BRANCH

    source_sha = verify_source_manifest(source, "training")
    if not results:
        raise ValueError("website publication requires replay results")
    if not results[0].get("semantic_manifest"):
        raise ValueError("website semantic manifest is missing")
    semantic_sha = verify_website_semantic_manifest(results[0]["semantic_manifest"])
    if any(result.get("semantic_manifest") != results[0]["semantic_manifest"] for result in results):
        raise ValueError("website markets used different semantic identities")
    account_shas = {}
    if len(results) != 3 or {r["market"] for r in results} != {"csi300", "csi500", "csi1000"}:
        raise ValueError("all three simulation accounts must succeed together")
    if len({r["series"]["latest_date"] for r in results}) != 1:
        raise ValueError("website markets have different data dates")
    files = {}
    market_bindings = {}
    for r in results:
        if r["status"] != "COMPLETED" or r["source_sha256"] != source_sha or r["asset_digest"] != asset["digest"]:
            raise ValueError("website publication source/provider mismatch")
        if r.get("website_semantic_sha256") != semantic_sha:
            raise ValueError("website publication semantic identity mismatch")
        account_sha = verify_account_contract(
            r.get("account_contract"),
            account_contract_for_market(r["market"]),
        )
        if r.get("account_contract_sha256") != account_sha:
            raise ValueError("website publication account identity mismatch")
        account_shas[r["market"]] = account_sha
        s = validate_website_series(r["series"], market=r["market"])
        if (
            s["source_manifest_sha256"] != source_sha
            or s["website_semantic_sha256"] != semantic_sha
            or s["account_contract_sha256"] != account_sha
            or s["provider_archive_sha256"] != asset["digest"].split(":")[1]
        ):
            raise ValueError("website series source/provider differs from publication inputs")
        content = json.dumps(s, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        files[f"quant/data/signals/{r['market']}_website_performance.json"] = content
        files[f"quant/data/signals/{r['market']}_forward_performance.json"] = content
        files[f"quant/data/signals/{r['market']}_backfill_performance.json"] = content
        files[f"quant/data/website_simulation/{source_sha}/{asset['digest'].split(':')[1]}/{r['market']}.json"] = (
            content
        )
        result_dir = Path(simulation_root) / r["cache_sha256"] / asset["digest"].split(":")[1] / r["market"] / "full"
        result_path = result_dir / "result.json"
        ledger = r.get("ledger_file_sha256")
        if not result_path.is_file() or not isinstance(ledger, dict) or not ledger:
            raise ValueError("website immutable result/ledger is missing before receipt creation")
        result_bytes = result_path.read_bytes()
        series_bytes = content.encode()
        market_bindings[r["market"]] = {
            "cache_sha256": r["cache_sha256"],
            "series_sha256": hashlib.sha256(series_bytes).hexdigest(),
            "result_sha256": hashlib.sha256(result_bytes).hexdigest(),
            "ledger_file_sha256": dict(ledger),
            "final_state_sha256": ledger.get("final_state.json"),
        }
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
    receipt_path = Path(simulation_root) / source_sha / asset["digest"].split(":")[1] / "publication.json"
    receipt_body = {
        "schema_version": 2,
        "status": "PUBLISHED",
        "source_sha256": source_sha,
        "website_semantic_sha256": semantic_sha,
        "account_contract_sha256": account_shas,
        "asset_digest": asset["digest"],
        "latest_date": results[0]["series"]["latest_date"],
        "files": len(files),
        "markets": market_bindings,
    }
    if not missing:
        if receipt_path.exists():
            from website_budget import checked_publication_receipt

            checked_publication_receipt(
                simulation_root=simulation_root,
                expected_source_sha256=source_sha,
                expected_semantic_sha256=semantic_sha,
                expected_accounts=account_shas,
                expected_asset_digest=asset["digest"],
                expected_latest_date=results[0]["series"]["latest_date"],
            )
        else:
            write_immutable(
                receipt_path,
                json.dumps({**receipt_body, "status": "RECOVERED_AFTER_PUBLICATION"}, indent=2) + "\n",
            )
            commit_fn()
        return {"status": "ALREADY_PUBLISHED", "source_sha256": source_sha, "files": len(files)}
    commit = push_files(
        token,
        GITHUB_REPO,
        SIGNAL_BRANCH,
        missing,
        f"chore(nav): update three October 8 simulated accounts through {results[0]['series']['latest_date']}",
    )
    receipt = {**receipt_body, "commit": commit}
    write_immutable(receipt_path, json.dumps(receipt, indent=2) + "\n")
    commit_fn()
    return receipt
