"""Website NAV must include the first session and preserve actual inventory."""

import json

import pytest

from paper_portfolio import PaperPortfolio
from website_simulation import build_website_series, validate_website_series, write_immutable


def example_series():
    pp = PaperPortfolio(topk=10, nd=1)
    sessions = []
    for signal, day, opening, close in [
        ("2026-09-30", "2026-10-08", 10.0, 11.0),
        ("2026-10-08", "2026-10-09", 11.0, 10.5),
    ]:
        pp.plan_signal(signal, day, [("SH600000", 0.1)])
        sessions.append(
            pp.execute_pending(
                day,
                {"SH600000": opening},
                factors={"SH600000": 1.0},
                close_prices={"SH600000": close},
                strict_valuation=True,
            )
        )
    fits = [
        {
            "fit_asof": "2026-09-30",
            "signal_end": "2026-10-09",
            "profile": "59b93929_top10_drop1_10d",
            "model_sha256": "c" * 64,
            "data_prefix_sha256": "d" * 64,
            "training_contract": {
                "asof": "2026-09-30",
                "train_end": "2025-08-15",
                "valid_end": "2026-09-14",
                "horizon": 10,
            },
        }
    ]
    return (
        build_website_series(
            "csi300",
            {"sessions": sessions, "gaps": []},
            calendar=["2026-09-30", "2026-10-08", "2026-10-09"],
            source_sha="a" * 64,
            provider_sha="b" * 64,
            fits=fits,
        ),
        sessions,
    )


def test_first_open_fill_cost_and_first_close_are_included():
    series, sessions = example_series()
    fill = sessions[0]["executed_buy"][0]
    shares = fill["shares"]
    expected_fee = max(shares * 10 * 0.0005, 5)
    expected_first_nav = 1_000_000 + shares * (11 - 10) - expected_fee
    assert fill["cost"] == pytest.approx(expected_fee)
    assert series["points"][0]["portfolio_value_close"] == pytest.approx(expected_first_nav)
    assert series["points"][0]["cumulative_return"] == pytest.approx(expected_first_nav / 1_000_000 - 1)
    assert series["points"][0]["date"] == "2026-10-08"
    sell_fee = max(shares * 11 * 0.0015, 5)
    assert series["points"][1]["portfolio_value_close"] == pytest.approx(1_000_000 + shares - expected_fee - sell_fee)
    assert series["total_fees"] == pytest.approx(expected_fee + sell_fee)


@pytest.mark.parametrize(
    "damage",
    [
        "first_capital",
        "cash_boundary",
        "missing_session",
        "wrong_market",
        "nonfinite",
        "future_fit",
        "wrong_profile",
        "changed_total",
    ],
)
def test_corrupted_website_series_is_rejected(damage):
    raw, _ = example_series()
    if damage == "first_capital":
        raw["initial_cash"] = 100_000_000
    elif damage == "cash_boundary":
        raw["points"][1]["opening_state"]["cash"] += 1
    elif damage == "missing_session":
        raw["points"].pop(0)
    elif damage == "wrong_market":
        raw["market"] = "csi500"
    elif damage == "nonfinite":
        raw["points"][0]["nav"] = float("nan")
    elif damage == "future_fit":
        raw["fit_receipts"][0]["training_contract"]["valid_end"] = "2026-10-08"
    elif damage == "wrong_profile":
        raw["account_identity"]["profile"] = "c98856b4_top10_drop1"
    else:
        raw["cumulative_return"] += 0.01
    with pytest.raises(ValueError):
        validate_website_series(raw)


def test_quote_gaps_cannot_be_promoted_to_website_values():
    _, sessions = example_series()
    with pytest.raises(ValueError, match="missing held quotes"):
        build_website_series(
            "csi300",
            {"sessions": sessions, "gaps": [{}]},
            calendar=[],
            source_sha="a" * 64,
            provider_sha="b" * 64,
            fits=[],
        )


def test_exact_snapshot_retry_and_conflicting_retry(tmp_path):
    path = tmp_path / "snapshot.json"
    assert write_immutable(path, "original") == write_immutable(path, b"original")
    with pytest.raises(ValueError, match="immutable"):
        write_immutable(path, "modified")
    assert path.read_text() == "original"


def test_training_cache_tracks_vwap_revisions_but_not_future_appends(tmp_path):
    import struct
    from website_simulation import training_prefix_fingerprint
    from qlib_live_retrain import provider_training_fingerprint

    for directory in ["calendars", "instruments", "features/sh600000"]:
        (tmp_path / directory).mkdir(parents=True)
    (tmp_path / "calendars/day.txt").write_text("2026-09-30\n2026-10-08\n")
    (tmp_path / "instruments/csi300.txt").write_text("SH600000\t2026-09-30\t2026-10-08\n")
    vwap = tmp_path / "features/sh600000/vwap.day.bin"
    vwap.write_bytes(struct.pack("<fff", 0, 10, 11))
    original = training_prefix_fingerprint(tmp_path, "csi300", "2026-09-30")
    production_original = provider_training_fingerprint(tmp_path, "csi300", "2026-09-30")
    vwap.write_bytes(struct.pack("<fff", 0, 10, 12))
    assert training_prefix_fingerprint(tmp_path, "csi300", "2026-09-30") == original
    assert provider_training_fingerprint(tmp_path, "csi300", "2026-09-30") == production_original
    vwap.write_bytes(struct.pack("<fff", 0, 10.5, 12))
    assert training_prefix_fingerprint(tmp_path, "csi300", "2026-09-30") != original
    assert provider_training_fingerprint(tmp_path, "csi300", "2026-09-30") != production_original


def test_build_and_merge_cli_select_corrected_account_without_splicing(tmp_path, monkeypatch):
    import build_forward_performance as builder
    import merge_forward_performance as merger

    raw, _ = example_series()
    signals = tmp_path / "signals"
    signals.mkdir()
    (signals / "csi300_website_performance.json").write_text(json.dumps(raw))
    # A previous, inconsistent derived prefix must not survive into the website.
    old = signals / "csi300_backfill_performance.json"
    old.write_text('{"invalid_old_prefix":true}')
    live, final = tmp_path / "live.json", tmp_path / "final.json"
    monkeypatch.setattr("sys.argv", ["builder", "--market", "csi300", "--signals", str(signals), "--output", str(live)])
    builder.main()
    monkeypatch.setattr("sys.argv", ["merger", "--prefix", str(old), "--live", str(live), "--output", str(final)])
    merger.main()
    assert json.loads(final.read_text()) == raw


def test_audit_reads_new_continuous_account_and_rejects_stale_derived_output(tmp_path):
    from audit_repair_impact import audit_website_series

    series, _ = example_series()
    for kind in ("website", "forward", "backfill"):
        (tmp_path / f"csi300_{kind}_performance.json").write_text(json.dumps(series))
    actual, hashes = audit_website_series(tmp_path, "csi300")
    assert actual == series and len(hashes) == 3
    assert actual["start_date"] == "2026-10-08"
    (tmp_path / "csi300_forward_performance.json").write_text('{"obsolete_return":0.5}')
    with pytest.raises(ValueError, match="website derived output mismatch"):
        audit_website_series(tmp_path, "csi300")


def test_cloud_worker_rejects_changed_source_before_accessing_volume():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "code/ops/website_simulation_modal.py"
    spec = importlib.util.spec_from_file_location("website_simulation_modal_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(ValueError, match="source manifest changed"):
        module.build_market.local({"source_manifest": {}})


@pytest.mark.parametrize("previous_date", [None, "", "2026-10-08", "2026-10-10", "not-a-date"])
def test_publication_recovers_lost_response_and_does_not_push_twice(tmp_path, monkeypatch, previous_date):
    import base64
    import importlib.util
    from pathlib import Path
    import website_simulation
    from csi1000_production_config import (
        CANONICAL_PROFILES_BY_MARKET,
        CANONICAL_ND_BY_MARKET,
        CANONICAL_TOPK_BY_MARKET,
        profile_manifest,
    )

    path = Path(__file__).resolve().parents[1] / "code/ops/website_simulation_modal.py"
    spec = importlib.util.spec_from_file_location("website_simulation_publish_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "Path", lambda value: tmp_path if value == "/simulation" else Path(value))
    monkeypatch.setattr("research_identity.verify_source_manifest", lambda *_args: "a" * 64)
    monkeypatch.setattr(module.simulation, "commit", lambda: None)
    results = []
    for market, profile in CANONICAL_PROFILES_BY_MARKET.items():
        raw, _ = example_series()
        raw["market"] = raw["account_identity"]["market"] = market
        identity = raw["account_identity"]
        identity.update(
            profile=profile,
            paper_lineage=f"{profile}_website_oct08_v2",
            topk=CANONICAL_TOPK_BY_MARKET[market],
            nd=CANONICAL_ND_BY_MARKET[market],
            horizon=profile_manifest(profile)["label_horizon"],
        )
        raw["fit_receipts"][0]["profile"] = profile
        raw["fit_receipts"][0]["training_contract"]["horizon"] = identity["horizon"]
        results.append(
            {
                "status": "COMPLETED",
                "market": market,
                "source_sha256": "a" * 64,
                "asset_digest": "sha256:" + "b" * 64,
                "series": raw,
            }
        )
    published = {
        f"quant/data/signals/{market}_website_performance.json": json.dumps({"latest_date": previous_date})
        for market in CANONICAL_PROFILES_BY_MARKET
    }
    pushes = []

    class Response:
        def __init__(self, path):
            self.path = path
            self.status_code = 200 if path in published else 404

        def raise_for_status(self):
            assert self.status_code == 200

        def json(self):
            return {"content": base64.b64encode(published[self.path].encode()).decode()}

    monkeypatch.setattr("requests.get", lambda url, **_kw: Response(url.split("/contents/")[1]))

    def push(_token, _repo, _branch, files, _message):
        published.update(files)
        pushes.append(files)
        return "c" * 40

    monkeypatch.setattr("github_commit.push_files", push)
    original = website_simulation.write_immutable

    def interrupt(*_args):
        raise RuntimeError("publication succeeded but local receipt was not committed")

    monkeypatch.setattr(website_simulation, "write_immutable", interrupt)
    asset = {"digest": "sha256:" + "b" * 64}
    if previous_date in ("2026-10-10", "not-a-date"):
        with pytest.raises(ValueError):
            module.publish_results(results, {}, asset, token="TEST_ONLY")
        assert pushes == [] and len(published) == 3
        return
    with pytest.raises(RuntimeError, match="publication succeeded"):
        module.publish_results(results, {}, asset, token="TEST_ONLY")
    monkeypatch.setattr(website_simulation, "write_immutable", original)
    assert module.publish_results(results, {}, asset, token="TEST_ONLY")["status"] == "ALREADY_PUBLISHED"
    assert len(pushes) == 1 and len(published) == 12
    assert len(list(tmp_path.rglob("publication.json"))) == 1
    assert all(json.loads(v)["schema_version"] == 3 for v in published.values())


def test_incremental_prefix_requires_a_complete_ancestor_ledger(tmp_path, monkeypatch):
    import hashlib
    import importlib.util
    from pathlib import Path

    import pandas as pd

    path = Path(__file__).resolve().parents[1] / "code/ops/website_simulation_modal.py"
    spec = importlib.util.spec_from_file_location("website_simulation_prefix_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    simulation = tmp_path / "simulation"
    overlays = tmp_path / "overlays"
    source_sha = "a" * 64
    prior_dir = overlays / "2026-10-09"
    current_dir = overlays / "2026-10-12"
    prior_dir.mkdir(parents=True)
    current_dir.mkdir(parents=True)
    (prior_dir / "manifest.json").write_text("{}")
    prior_digest = hashlib.sha256((prior_dir / "manifest.json").read_bytes()).hexdigest()
    (current_dir / "manifest.json").write_text(json.dumps({"base": {"archive_sha256": prior_digest}}))
    current_digest = hashlib.sha256((current_dir / "manifest.json").read_bytes()).hexdigest()

    series, sessions = example_series()
    series["source_manifest_sha256"] = source_sha
    series["provider_archive_sha256"] = prior_digest
    fake_model = b"deterministic fake model"
    model_sha = hashlib.sha256(fake_model).hexdigest()
    series["fit_receipts"][0]["model_sha256"] = model_sha
    for point in series["points"]:
        point["model_sha256"] = model_sha

    result_dir = simulation / source_sha / prior_digest / "csi300" / "full"
    result_dir.mkdir(parents=True)
    signal = pd.DataFrame(
        {"score": [0.1, 0.2, 0.3]},
        index=pd.MultiIndex.from_product(
            [pd.to_datetime(["2026-09-30", "2026-10-08", "2026-10-09"]), ["SH600000"]],
            names=["datetime", "instrument"],
        ),
    )
    signal.to_parquet(result_dir / "signal.parquet")
    state = PaperPortfolio(topk=10, nd=1).to_dict()
    state["last_execution_date"] = "2026-10-09"
    (result_dir / "final_state.json").write_text(json.dumps(state))
    payload = {
        "status": "COMPLETED",
        "market": "csi300",
        "source_sha256": source_sha,
        "asset_digest": f"sha256:{prior_digest}",
        "series": series,
        "ledger_file_sha256": {
            "signal.parquet": hashlib.sha256((result_dir / "signal.parquet").read_bytes()).hexdigest(),
            "final_state.json": hashlib.sha256((result_dir / "final_state.json").read_bytes()).hexdigest(),
        },
    }
    (result_dir / "result.json").write_text(json.dumps(payload))
    model_dir = simulation / "model_cache" / "csi300" / "cached"
    model_dir.mkdir(parents=True)
    (model_dir / "model.pkl").write_bytes(fake_model)
    (model_dir / "receipt.json").write_text(
        json.dumps(
            {
                "identity": {
                    "source_sha256": source_sha,
                    "market": "csi300",
                    "profile": series["account_identity"]["profile"],
                    "fit_asof": "2026-09-30",
                },
                "model_sha256": model_sha,
            }
        )
    )

    real_path = Path

    def redirected(value):
        text = str(value)
        if text == "/simulation" or text.startswith("/simulation/"):
            return simulation / text[len("/simulation/") :]
        if text == "/data/cn_data_overlay" or text.startswith("/data/cn_data_overlay/"):
            return overlays / text[len("/data/cn_data_overlay/") :]
        return real_path(value)

    monkeypatch.setattr(module, "Path", redirected)
    calendar = ["2026-09-18", "2026-09-30", "2026-10-08", "2026-10-09", "2026-10-12"]
    asset = {"local_path": str(current_dir), "digest": f"sha256:{current_digest}"}
    prefix = module._incremental_prior(source_sha, asset, "csi300", "2026-10-12", calendar)
    assert prefix is not None
    assert prefix["previous"] == "2026-10-09"
    assert prefix["result_dir"] == result_dir

    (result_dir / "signal.parquet").unlink()
    signal.iloc[:-1].to_parquet(result_dir / "signal.parquet")
    payload["ledger_file_sha256"]["signal.parquet"] = hashlib.sha256(
        (result_dir / "signal.parquet").read_bytes()
    ).hexdigest()
    (result_dir / "result.json").write_text(json.dumps(payload))
    assert module._incremental_prior(source_sha, asset, "csi300", "2026-10-12", calendar) is None


def test_incremental_prediction_uses_an_equivalent_bounded_window(tmp_path):
    """Alpha158 is causal: a 130-session inference window must equal full history."""
    import importlib.util
    import struct
    from pathlib import Path

    import numpy as np
    import pandas as pd
    import qlib
    from qlib.data.dataset import DatasetH

    path = Path(__file__).resolve().parents[1] / "code/ops/website_simulation_modal.py"
    spec = importlib.util.spec_from_file_location("website_simulation_window_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2024-01-01", periods=320)]
    (tmp_path / "calendars").mkdir()
    (tmp_path / "instruments").mkdir()
    (tmp_path / "features").mkdir()
    (tmp_path / "calendars" / "day.txt").write_text("\n".join(days) + "\n")
    rng = np.random.default_rng(13)
    for index in range(4):
        name = f"SH60{index:04d}"
        ret = rng.normal(0.0002, 0.012, len(days))
        close = 10 * np.exp(np.cumsum(ret))
        open_ = close * (1 + rng.normal(0, 0.003, len(days)))
        high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.003, len(days))))
        low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.003, len(days))))
        volume = rng.integers(1_000_000, 4_000_000, len(days)).astype(float)
        vwap = (open_ + high + low + close) / 4
        feature_dir = tmp_path / "features" / name.lower()
        feature_dir.mkdir()
        for field, values in [
            ("open", open_),
            ("high", high),
            ("low", low),
            ("close", close),
            ("volume", volume),
            ("vwap", vwap),
            ("factor", np.ones(len(days))),
        ]:
            (feature_dir / f"{field}.day.bin").write_bytes(
                struct.pack("<f", 0.0) + np.asarray(values, dtype="<f4").tobytes()
            )
        for instrument_file in ("all.txt", "csi300.txt"):
            with (tmp_path / "instruments" / instrument_file).open("a") as stream:
                stream.write(f"{name}\t{days[0]}\t{days[-1]}\n")

    qlib.init(provider_uri=str(tmp_path), region="cn", kernels=1)
    previous, target = days[-2], days[-1]
    handler = {
        "class": "Alpha158",
        "module_path": "qlib.contrib.data.handler",
        "kwargs": {
            "instruments": "csi300",
            "start_time": days[0],
            "end_time": target,
            "fit_start_time": days[0],
            "fit_end_time": target,
        },
    }
    cfg = {
        "task": {
            "dataset": {
                "class": "DatasetH",
                "module_path": "qlib.data.dataset",
                "kwargs": {"handler": handler, "segments": {"test": [previous, target]}},
            }
        }
    }

    class SumModel:
        def predict(self, dataset, segment="test"):
            frame = dataset.prepare(segment, col_set="feature", data_key="infer")
            return pd.Series(np.asarray(frame.sum(axis=1), dtype=float), index=frame.index)

    bounded = module._predict_days(SumModel(), cfg, tmp_path, days, [previous, target])
    full = SumModel().predict(DatasetH(handler=handler, segments={"test": [previous, target]}), segment="test")
    pd.testing.assert_series_equal(bounded, full, check_exact=True, check_names=False)
