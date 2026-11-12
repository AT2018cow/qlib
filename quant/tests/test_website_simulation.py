"""Website NAV must include the first session and preserve actual inventory."""

import ast
import json
from pathlib import Path

import pytest

from paper_portfolio import PaperPortfolio
from research_identity import account_contract_manifest, website_semantic_manifest
from website_simulation import (
    account_contract_for_market,
    build_website_series,
    validate_website_series,
    write_immutable,
)


def example_series(market="csi300"):
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
            semantic_sha=website_semantic_manifest()["sha256"],
            account_sha=account_contract_manifest(account_contract_for_market(market))["sha256"],
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
            semantic_sha=website_semantic_manifest()["sha256"],
            account_sha=account_contract_manifest(account_contract_for_market("csi300"))["sha256"],
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


@pytest.mark.parametrize("worker_name", ["build_market", "build_market_incremental"])
def test_cloud_worker_rejects_changed_source_before_accessing_volume(worker_name):
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "code/ops/website_simulation_modal.py"
    spec = importlib.util.spec_from_file_location("website_simulation_modal_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(ValueError, match="source manifest changed"):
        getattr(module, worker_name).local({"source_manifest": {}})


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
    import hashlib

    results = []
    for market, profile in CANONICAL_PROFILES_BY_MARKET.items():
        raw, _ = example_series(market)
        release_sha = "e" * 64 if market == "csi500" else "a" * 64
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
        raw["source_manifest_sha256"] = release_sha
        raw["release_source_sha256"] = release_sha
        cache_sha = hashlib.sha256(("cache-" + market).encode()).hexdigest()
        result_dir = tmp_path / cache_sha / ("b" * 64) / market / "full"
        result_dir.mkdir(parents=True)
        final_state = b'{"state": "fixed"}\n'
        (result_dir / "final_state.json").write_bytes(final_state)
        result = {
            "status": "COMPLETED",
            "market": market,
            "source_sha256": release_sha,
            "website_semantic_sha256": raw["website_semantic_sha256"],
            "account_contract_sha256": raw["account_contract_sha256"],
            "semantic_manifest": website_semantic_manifest(),
            "account_contract": account_contract_manifest(account_contract_for_market(market)),
            "asset_digest": "sha256:" + "b" * 64,
            "cache_sha256": cache_sha,
            "ledger_file_sha256": {"final_state.json": hashlib.sha256(final_state).hexdigest()},
            "series": raw,
        }
        (result_dir / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
        results.append(result)
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
    receipts = list(tmp_path.rglob("publication.json"))
    assert len(receipts) == 1
    assert json.loads(receipts[0].read_text())["market_release_sha256"] == {
        "csi300": "a" * 64,
        "csi500": "e" * 64,
        "csi1000": "a" * 64,
    }
    assert module.publish_results(results, {}, asset, token="TEST_ONLY")["status"] == "ALREADY_PUBLISHED"
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
    # The website incremental prefix protocol reads the same base fields from a
    # cached chenditc provider manifest as it does from an overlay manifest.
    (current_dir / "manifest.json").write_text(
        json.dumps(
            {
                "status": "PASS",
                "source": "chenditc",
                "base": {
                    "path": "provider:chenditc/2026-10-09",
                    "calendar_end": "2026-10-09",
                    "digest": prior_digest,
                },
            }
        )
    )
    current_digest = hashlib.sha256((current_dir / "manifest.json").read_bytes()).hexdigest()

    from research_identity import website_cache_key

    semantic_sha = series_semantic = website_semantic_manifest()["sha256"]
    account_sha = account_contract_manifest(account_contract_for_market("csi300"))["sha256"]
    cache_sha = website_cache_key(semantic_sha, account_sha)
    series, sessions = example_series()
    series["source_manifest_sha256"] = source_sha
    series["release_source_sha256"] = source_sha
    series["website_semantic_sha256"] = semantic_sha
    series["account_contract_sha256"] = account_sha
    series["provider_archive_sha256"] = prior_digest
    fake_model = b"deterministic fake model"
    model_sha = hashlib.sha256(fake_model).hexdigest()
    series["fit_receipts"][0]["model_sha256"] = model_sha
    for point in series["points"]:
        point["model_sha256"] = model_sha

    result_dir = simulation / cache_sha / prior_digest / "csi300" / "full"
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
        "website_semantic_sha256": semantic_sha,
        "account_contract_sha256": account_sha,
        "asset_digest": f"sha256:{prior_digest}",
        "provider_source": "chenditc",
        "provider_calendar_end": "2026-10-09",
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
                    "cache_sha256": cache_sha,
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
    asset = {"local_path": str(current_dir), "digest": f"sha256:{current_digest}", "source": "chenditc"}
    prefix = module._incremental_prior(
        cache_sha,
        asset,
        "csi300",
        "2026-10-12",
        calendar,
        semantic_sha=semantic_sha,
        account_sha=account_sha,
    )
    assert prefix is not None
    assert prefix["previous"] == "2026-10-09"
    assert prefix["result_dir"] == result_dir

    # R02: the real _latest_overlay_asset() archive shape has no local_path.
    archive_dir = overlays / "archives" / "2026-10-12"
    archive_dir.mkdir(parents=True)
    (archive_dir / "calendar.txt").write_text("\n".join(calendar) + "\n")
    (archive_dir / "manifest.json").write_text(
        json.dumps({"status": "PASS", "base": {"calendar_end": "2026-10-09", "digest": prior_digest}})
    )
    archive_file = archive_dir / f"{current_digest}.tar.gz"
    archive_file.write_bytes(b"abc")
    receipt = {
        "schema_version": 1,
        "status": "PASS",
        "date": "2026-10-12",
        "calendar_end": "2026-10-12",
        "archive": {
            "path": f"archives/2026-10-12/{current_digest}.tar.gz",
            "sha256": current_digest,
            "size": 3,
            "content_sha256": "e" * 64,
        },
        "manifest": {"sha256": "f" * 64},
        "base": {"calendar_end": "2026-10-09", "digest": prior_digest},
    }
    (archive_dir / "receipt.json").write_text(json.dumps(receipt))
    archive_asset = module._latest_overlay_asset(expected_end="2026-10-12")
    assert archive_asset is not None
    assert archive_asset["kind"] == "archive"
    assert "local_path" not in archive_asset
    assert archive_asset["base"]["digest"] == prior_digest
    payload["provider_source"] = "overlay"
    (result_dir / "result.json").write_text(json.dumps(payload))
    archive_prefix = module._incremental_prior(
        cache_sha,
        archive_asset,
        "csi300",
        "2026-10-12",
        calendar,
        semantic_sha=semantic_sha,
        account_sha=account_sha,
    )
    assert archive_prefix is not None
    assert archive_prefix["previous"] == "2026-10-09"
    payload["provider_source"] = "chenditc"
    (result_dir / "result.json").write_text(json.dumps(payload))

    # Plan A: both directions are lineage boundaries.
    overlay_asset = {**asset, "source": "overlay"}
    assert (
        module._incremental_prior(
            cache_sha,
            overlay_asset,
            "csi300",
            "2026-10-12",
            calendar,
            semantic_sha=semantic_sha,
            account_sha=account_sha,
        )
        is None
    )
    payload["provider_source"] = "overlay"
    (result_dir / "result.json").write_text(json.dumps(payload))
    assert (
        module._incremental_prior(
            cache_sha,
            overlay_asset,
            "csi300",
            "2026-10-12",
            calendar,
            semantic_sha=semantic_sha,
            account_sha=account_sha,
        )
        is not None
    )
    payload["provider_source"] = "chenditc"
    (result_dir / "result.json").write_text(json.dumps(payload))

    # PC02: a release-only change (resource/schedule/orchestration) must keep
    # the semantic ancestor; semantic/account changes must reject it.
    payload["source_sha256"] = "9" * 64
    series["source_manifest_sha256"] = "9" * 64
    series["release_source_sha256"] = "9" * 64
    (result_dir / "result.json").write_text(json.dumps(payload))
    assert (
        module._incremental_prior(
            cache_sha,
            asset,
            "csi300",
            "2026-10-12",
            calendar,
            semantic_sha=semantic_sha,
            account_sha=account_sha,
        )
        is not None
    )
    payload["website_semantic_sha256"] = "8" * 64
    (result_dir / "result.json").write_text(json.dumps(payload))
    assert (
        module._incremental_prior(
            cache_sha,
            asset,
            "csi300",
            "2026-10-12",
            calendar,
            semantic_sha=semantic_sha,
            account_sha=account_sha,
        )
        is None
    )
    payload["website_semantic_sha256"] = semantic_sha
    payload["account_contract_sha256"] = "7" * 64
    (result_dir / "result.json").write_text(json.dumps(payload))
    assert (
        module._incremental_prior(
            cache_sha,
            asset,
            "csi300",
            "2026-10-12",
            calendar,
            semantic_sha=semantic_sha,
            account_sha=account_sha,
        )
        is None
    )
    payload["account_contract_sha256"] = account_sha
    (result_dir / "result.json").write_text(json.dumps(payload))

    (result_dir / "signal.parquet").unlink()
    signal.iloc[:-1].to_parquet(result_dir / "signal.parquet")
    payload["ledger_file_sha256"]["signal.parquet"] = hashlib.sha256(
        (result_dir / "signal.parquet").read_bytes()
    ).hexdigest()
    (result_dir / "result.json").write_text(json.dumps(payload))
    assert (
        module._incremental_prior(
            cache_sha,
            asset,
            "csi300",
            "2026-10-12",
            calendar,
            semantic_sha=semantic_sha,
            account_sha=account_sha,
        )
        is None
    )


def test_latest_asset_resolves_archive_then_directory(tmp_path, monkeypatch):
    import importlib.util
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "code/ops/website_simulation_modal.py"
    spec = importlib.util.spec_from_file_location("website_simulation_asset_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    data = tmp_path / "data"
    overlay = data / "cn_data_overlay"
    archive_dir = overlay / "archives" / "2026-11-09"
    archive_dir.mkdir(parents=True)
    (archive_dir / "calendar.txt").write_text("2026-11-06\n2026-11-09\n")
    (archive_dir / "manifest.json").write_text(json.dumps({"status": "PASS", "base": {}}))
    archive = archive_dir / ("d" * 64 + ".tar.gz")
    archive.write_bytes(b"abc")
    receipt = {
        "schema_version": 1,
        "status": "PASS",
        "date": "2026-11-09",
        "calendar_end": "2026-11-09",
        "archive": {
            "path": f"archives/2026-11-09/{'d' * 64}.tar.gz",
            "sha256": "d" * 64,
            "size": 3,
            "content_sha256": "e" * 64,
        },
        "manifest": {"sha256": "f" * 64},
        "base": {"calendar_end": "2026-11-06", "digest": "a" * 64},
    }
    (archive_dir / "receipt.json").write_text(json.dumps(receipt))

    legacy = overlay / "2026-11-10"
    (legacy / "calendars").mkdir(parents=True)
    (legacy / "calendars" / "day.txt").write_text("2026-11-09\n2026-11-10\n")
    (legacy / "manifest.json").write_text(json.dumps({"status": "PASS", "base": {}}))

    real_path = Path

    def redirected(value):
        text = str(value)
        if text == "/data" or text.startswith("/data/"):
            return data / text[len("/data/") :]
        return real_path(value)

    monkeypatch.setattr(module, "Path", redirected)
    archived = module.latest_asset(expected_end="2026-11-09")
    assert archived["kind"] == "archive"
    assert archived["digest"] == "sha256:" + "d" * 64
    assert archived["calendar"] == ["2026-11-06", "2026-11-09"]
    directory = module.latest_asset(expected_end="2026-11-10")
    assert directory["kind"] == "directory"
    assert directory["calendar_end"] == "2026-11-10"
    with pytest.raises(ValueError, match="no PASS self-hosted provider"):
        module.latest_asset(expected_end="2026-11-11")


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


def test_download_provider_archive_roundtrip(tmp_path):
    import hashlib
    import importlib.util
    from pathlib import Path

    import numpy as np

    from data_ingest.store import create_provider_archive

    path = Path(__file__).resolve().parents[1] / "code/research/regime_reselection/october_backfill_modal.py"
    spec = importlib.util.spec_from_file_location("october_backfill_asset_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    provider = tmp_path / "provider"
    (provider / "calendars").mkdir(parents=True)
    (provider / "calendars" / "day.txt").write_text("2026-11-09\n2026-11-10\n")
    feature = provider / "features" / "sh600000"
    feature.mkdir(parents=True)
    np.array([0.0, 10.0, 10.5], dtype="<f").tofile(feature / "close.day.bin")
    (provider / "manifest.json").write_text("{}")
    archive = tmp_path / "provider.tar.gz"
    meta = create_provider_archive(provider, archive)
    asset = {
        "archive_path": str(archive),
        "digest": f"sha256:{meta['sha256']}",
        "size": meta["size"],
        "source": "overlay",
    }
    local = module.download_provider(asset)
    assert (local / "calendars" / "day.txt").is_file()
    assert (local / "features" / "sh600000" / "close.day.bin").is_file()
    # Cached re-entry returns the same extracted path.
    assert module.download_provider(asset) == local


def test_overlay_base_prefers_archive_receipt_identity():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "code/ops/website_simulation_modal.py"
    spec = importlib.util.spec_from_file_location("website_simulation_base_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    asset = {
        "digest": "sha256:" + "c" * 64,
        "source": "overlay",
        "base": {"path": "archives/2026-11-09/x.tar.gz", "calendar_end": "2026-11-09", "digest": "d" * 64},
    }
    assert module._overlay_base(asset) == {
        "path": "archives/2026-11-09/x.tar.gz",
        "calendar_end": "2026-11-09",
        "digest": "d" * 64,
    }


class _FakeSampler:
    instances = []

    def __init__(self, **kwargs):
        self.started = False
        self.stopped = False
        self.stop_count = 0
        _FakeSampler.instances.append(self)

    def start(self):
        self.started = True
        return self

    def stop(self):
        self.stopped = True
        self.stop_count += 1
        return {"status": "ok", "samples": 1}

    def summary(self):
        return {"status": "ok", "samples": 1}


def _website_module():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "code/ops/website_simulation_modal.py"
    spec = importlib.util.spec_from_file_location("website_sampler_lifecycle_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("worker_name", ["build_market", "build_market_incremental"])
def test_website_worker_stops_sampler_on_success_and_cache_hit(monkeypatch, worker_name):
    import resource_sampler

    module = _website_module()
    _FakeSampler.instances = []
    monkeypatch.setattr(resource_sampler, "ResourceSampler", _FakeSampler)
    for result in ({"status": "COMPLETED"}, {"status": "COMPLETED", "cached": True}):
        monkeypatch.setattr(module, "_build_market_impl", lambda job, sampler=None, result=result: result)
        assert getattr(module, worker_name).local({"market": "csi300"}) == result
    assert len(_FakeSampler.instances) == 2
    assert all(instance.started and instance.stopped for instance in _FakeSampler.instances)


@pytest.mark.parametrize("worker_name", ["build_market", "build_market_incremental"])
def test_website_worker_stops_sampler_on_failure(monkeypatch, worker_name):
    import resource_sampler

    module = _website_module()
    _FakeSampler.instances = []

    def fail(job, sampler=None):
        raise RuntimeError("replay failed")

    monkeypatch.setattr(resource_sampler, "ResourceSampler", _FakeSampler)
    monkeypatch.setattr(module, "_build_market_impl", fail)
    with pytest.raises(RuntimeError, match="replay failed"):
        getattr(module, worker_name).local({"market": "csi300"})
    assert len(_FakeSampler.instances) == 1
    assert _FakeSampler.instances[0].started and _FakeSampler.instances[0].stopped


def _function_resources(function_name):
    path = Path(__file__).resolve().parents[1] / "code/ops/website_simulation_modal.py"
    tree = ast.parse(path.read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == function_name)
    decorator = next(
        item
        for item in function.decorator_list
        if isinstance(item, ast.Call) and isinstance(item.func, ast.Attribute) and item.func.attr == "function"
    )
    return {
        keyword.arg: keyword.value.value for keyword in decorator.keywords if isinstance(keyword.value, ast.Constant)
    }


def test_full_and_incremental_workers_have_separate_resource_contracts():
    full = _function_resources("build_market")
    incremental = _function_resources("build_market_incremental")
    assert (full["cpu"], full["memory"], full["max_containers"], full["retries"]) == (8, 16384, 3, 0)
    assert (
        incremental["cpu"],
        incremental["memory"],
        incremental["max_containers"],
        incremental["retries"],
    ) == (4, 12288, 3, 0)


def test_incremental_worker_fails_closed_instead_of_running_full():
    module = _website_module()
    job = {"market": "csi1000", "required_replay_mode": "INCREMENTAL"}
    module._validate_required_replay_mode(job, "INCREMENTAL")
    with pytest.raises(ValueError, match="incremental worker requires"):
        module._validate_required_replay_mode(job, "FULL")


def test_incremental_worker_can_reuse_a_validated_cached_full_result():
    module = _website_module()
    job = {"market": "csi1000", "required_replay_mode": "INCREMENTAL"}
    module._validate_required_replay_mode(job, "FULL", cached=True)


def test_archive_asset_skips_smoke_and_reaches_workers_without_local_path(monkeypatch):
    import types

    module = _website_module()
    source_sha = "a" * 64
    archive_asset = {
        "kind": "archive",
        "archive_path": "/data/cn_data_overlay/archives/2026-11-10/x.tar.gz",
        "digest": "sha256:" + "b" * 64,
        "size": 3,
        "source": "overlay",
        "calendar_end": "2026-11-10",
        "calendar": ["2026-11-09", "2026-11-10"],
        "base": {"calendar_end": "2026-11-09", "digest": "c" * 64},
    }
    monkeypatch.setattr(module, "data_volume_readonly", types.SimpleNamespace(reload=lambda: None))
    monkeypatch.setattr(module, "simulation", types.SimpleNamespace(reload=lambda: None))
    monkeypatch.setattr(module, "latest_asset", lambda expected_end=None: archive_asset)
    monkeypatch.setattr(module, "checked_publication_receipt", lambda **kwargs: None)
    monkeypatch.setattr(module, "_incremental_prior", lambda *args, **kwargs: {"previous": "2026-11-09"})
    monkeypatch.setattr(
        module,
        "build_market",
        types.SimpleNamespace(
            remote=lambda job: pytest.fail("incremental path must skip the smoke replay"),
            map=lambda jobs, **kwargs: pytest.fail("incremental path must not use the full worker"),
        ),
    )
    incremental_jobs = []

    def incremental_map(jobs, **kwargs):
        incremental_jobs.extend(jobs)
        return [{"market": job["market"], "asset": job["asset"]} for job in jobs]

    monkeypatch.setattr(
        module,
        "build_market_incremental",
        types.SimpleNamespace(map=incremental_map),
    )
    published = {}

    def publish(results, source, asset, *, token):
        published.update({"results": results, "source": source, "asset": asset})
        return {"status": "PUBLISHED"}

    monkeypatch.setattr(module, "publish_results", publish)
    monkeypatch.setattr(
        module,
        "source_manifest",
        lambda kind: {"sha256": source_sha, "schema_version": 2, "kind": kind, "files": {}},
        raising=False,
    )
    # daily() imports source_manifest from research_identity inside the function.
    import research_identity

    monkeypatch.setattr(
        research_identity,
        "source_manifest",
        lambda kind: {"sha256": source_sha, "schema_version": 2, "kind": kind, "files": {}},
    )
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    result = module.daily.local(expected_source_sha256=source_sha, expected_provider_end="2026-11-10")
    assert result["status"] == "PUBLISHED"
    assert published["asset"] is archive_asset
    assert "local_path" not in published["asset"]
    assert {item["market"] for item in published["results"]} == {"csi300", "csi500", "csi1000"}
    assert all(item["asset"] is archive_asset for item in published["results"])
    assert len(incremental_jobs) == 3
    assert all(job["required_replay_mode"] == "INCREMENTAL" for job in incremental_jobs)


def test_missing_incremental_prefix_uses_smoke_and_full_workers(monkeypatch):
    import types

    module = _website_module()
    source_sha = "a" * 64
    asset = {
        "digest": "sha256:" + "b" * 64,
        "calendar": ["2026-11-09", "2026-11-10"],
        "calendar_end": "2026-11-10",
    }
    monkeypatch.setattr(module, "data_volume_readonly", types.SimpleNamespace(reload=lambda: None))
    events = []
    monkeypatch.setattr(module, "simulation", types.SimpleNamespace(reload=lambda: events.append("reload")))
    monkeypatch.setattr(module, "latest_asset", lambda expected_end=None: asset)
    monkeypatch.setattr(module, "checked_publication_receipt", lambda **kwargs: None)
    monkeypatch.setattr(module, "_incremental_prior", lambda *args, **kwargs: None)
    full_calls = {"remote": [], "map": []}

    def full_remote(job):
        full_calls["remote"].append(job)
        return {"status": "SMOKE_PASS"}

    def full_map(jobs, **kwargs):
        events.append("map")
        full_calls["map"].extend(jobs)
        return [{"market": job["market"]} for job in jobs]

    monkeypatch.setattr(
        module,
        "build_market",
        types.SimpleNamespace(remote=full_remote, map=full_map),
    )
    monkeypatch.setattr(
        module,
        "build_market_incremental",
        types.SimpleNamespace(map=lambda *args, **kwargs: pytest.fail("full path used incremental worker")),
    )

    def publish_results(results, source, asset, token):
        events.append("publish")
        return {"status": "PUBLISHED"}

    monkeypatch.setattr(module, "publish_results", publish_results)
    import research_identity

    monkeypatch.setattr(
        research_identity,
        "source_manifest",
        lambda kind: {"sha256": source_sha, "schema_version": 2, "kind": kind, "files": {}},
    )
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    result = module.daily.local(expected_source_sha256=source_sha, expected_provider_end="2026-11-10")

    assert result["status"] == "PUBLISHED"
    assert [job["mode"] for job in full_calls["remote"]] == ["smoke", "smoke", "smoke"]
    assert [job["market"] for job in full_calls["remote"]] == ["csi300", "csi500", "csi1000"]
    assert len(full_calls["map"]) == 3
    assert all("required_replay_mode" not in job for job in full_calls["map"])
    assert events == ["reload", "map", "reload", "publish"]


def test_every_website_data_mount_is_read_only():
    path = Path(__file__).resolve().parents[1] / "code/ops/website_simulation_modal.py"
    tree = ast.parse(path.read_text())
    mounted = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            for keyword in decorator.keywords:
                if keyword.arg != "volumes" or not isinstance(keyword.value, ast.Dict):
                    continue
                for key, value in zip(keyword.value.keys, keyword.value.values):
                    if isinstance(key, ast.Constant) and key.value == "/data":
                        mounted.append((node.name, getattr(value, "id", None)))
    assert mounted, "website app must mount the production provider"
    assert all(volume == "data_volume_readonly" for _, volume in mounted), mounted
    assert "data_volume_readonly = data_volume.with_mount_options(read_only=True)" in path.read_text()


def test_legacy_website_series_remains_readable_but_cannot_seed_incremental():
    raw, _ = example_series()
    for key in (
        "release_source_sha256",
        "website_semantic_sha256",
        "account_contract_sha256",
        "identity_schema_version",
    ):
        raw.pop(key)
    validate_website_series(raw, market="csi300")
    raw["identity_schema_version"] = 2
    with pytest.raises(ValueError, match="identity missing"):
        validate_website_series(raw, market="csi300")


def _core_job(tmp_path, monkeypatch, *, required_replay_mode=None):
    import sys

    regime = Path(__file__).resolve().parents[1] / "code/research/regime_reselection"
    if str(regime) not in sys.path:
        sys.path.insert(0, str(regime))
    import october_backfill_plan
    import provider_asset
    import qlib_runtime
    import website_replay_core as core
    from research_identity import source_manifest, website_cache_key

    provider = tmp_path / "provider"
    (provider / "calendars").mkdir(parents=True)
    calendar = ["2026-09-30", "2026-11-06"]
    (provider / "calendars/day.txt").write_text("\n".join(calendar) + "\n")
    monkeypatch.setattr(provider_asset, "download_provider", lambda asset: provider)
    monkeypatch.setattr(qlib_runtime, "cn_trading_calendar", lambda: calendar)
    monkeypatch.setattr(
        october_backfill_plan,
        "fit_blocks",
        lambda calendar, start, end: [{"fit_asof": "2026-09-30", "signal_end": "2026-11-06"}],
    )
    semantic = website_semantic_manifest()
    account = account_contract_manifest(account_contract_for_market("csi300"))
    cache_sha = website_cache_key(semantic["sha256"], account["sha256"])
    asset = {"digest": "sha256:" + "b" * 64, "source": "overlay", "calendar_end": "2026-11-06"}
    job = {
        "asset": asset,
        "source_manifest": source_manifest("training"),
        "semantic_manifest": semantic,
        "account_contract": account,
        "market": "csi300",
        "mode": "full",
    }
    if required_replay_mode is not None:
        job["required_replay_mode"] = required_replay_mode
    root = tmp_path / cache_sha / asset["digest"].split(":")[1]
    root.mkdir(parents=True)
    (root / "smoke.json").write_text(
        json.dumps(
            {
                "status": "SMOKE_PASS",
                "website_semantic_sha256": semantic["sha256"],
                "account_contract_sha256": account["sha256"],
                "asset_digest": asset["digest"],
                "prefix_prediction_exact": True,
            }
        )
    )
    return core, job, cache_sha, semantic["sha256"], account["sha256"], root


def test_build_market_impl_passes_cache_identity_to_the_real_incremental_probe(tmp_path, monkeypatch):
    core, job, cache_sha, semantic_sha, account_sha, _root = _core_job(tmp_path, monkeypatch)
    captured = {}

    def fake_prior(cache, asset, market, end, calendar, *, semantic_sha, account_sha, simulation_root):
        captured.update(
            cache=cache,
            asset=asset,
            market=market,
            end=end,
            semantic_sha=semantic_sha,
            account_sha=account_sha,
            simulation_root=simulation_root,
        )
        raise RuntimeError("STOP_PR01")

    monkeypatch.setattr(core, "_incremental_prior", fake_prior)
    with pytest.raises(RuntimeError, match="STOP_PR01"):
        core._build_market_impl(job, simulation_root=tmp_path)
    assert captured["cache"] == cache_sha
    assert captured["semantic_sha"] == semantic_sha
    assert captured["account_sha"] == account_sha
    assert captured["market"] == "csi300"
    assert captured["end"] == "2026-11-06"
    assert captured["simulation_root"] == tmp_path


def test_build_market_impl_reaches_full_branch_when_no_ancestor(tmp_path, monkeypatch):
    core, job, cache_sha, semantic_sha, account_sha, _root = _core_job(tmp_path, monkeypatch)
    captured = {}

    def fake_prior(cache, asset, market, end, calendar, *, semantic_sha, account_sha, simulation_root):
        captured.update(cache=cache, semantic_sha=semantic_sha, account_sha=account_sha)
        return None

    import qlib

    monkeypatch.setattr(core, "_incremental_prior", fake_prior)
    monkeypatch.setattr(qlib, "init", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("FULL_REACHED")))
    with pytest.raises(RuntimeError, match="FULL_REACHED"):
        core._build_market_impl(job, simulation_root=tmp_path)
    assert captured == {"cache": cache_sha, "semantic_sha": semantic_sha, "account_sha": account_sha}


def test_build_market_impl_reaches_incremental_branch_when_ancestor_exists(tmp_path, monkeypatch):
    core, job, cache_sha, semantic_sha, account_sha, _root = _core_job(tmp_path, monkeypatch)

    def fake_prior(cache, asset, market, end, calendar, *, semantic_sha, account_sha, simulation_root):
        return {
            "previous": "2026-11-05",
            "signal_path": tmp_path / "prior.parquet",
            "payload": {"series": {"fit_receipts": []}},
            "asset_digest": "sha256:" + "c" * 64,
            "final_state": {},
            "result_dir": tmp_path,
            "new_fit_required": False,
            "fit_asof": "2026-09-30",
        }

    import pandas as pd

    monkeypatch.setattr(core, "_incremental_prior", fake_prior)
    monkeypatch.setattr(
        pd,
        "read_parquet",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("INCREMENTAL_REACHED")),
    )
    with pytest.raises(RuntimeError, match="INCREMENTAL_REACHED"):
        core._build_market_impl(job, simulation_root=tmp_path)


def test_incremental_worker_without_ancestor_fails_closed_at_real_call_site(tmp_path, monkeypatch):
    core, job, *_ = _core_job(tmp_path, monkeypatch, required_replay_mode="INCREMENTAL")

    def fake_prior(cache, asset, market, end, calendar, *, semantic_sha, account_sha, simulation_root):
        return None

    monkeypatch.setattr(core, "_incremental_prior", fake_prior)
    with pytest.raises(ValueError, match="incremental worker requires"):
        core._build_market_impl(job, simulation_root=tmp_path)


def test_daily_short_circuit_rejects_invalid_receipt_before_replay(monkeypatch):
    import types

    module = _website_module()
    source = __import__("research_identity").source_manifest("training")["sha256"]
    asset = {
        "digest": "sha256:" + "b" * 64,
        "calendar": ["2026-11-09", "2026-11-10"],
        "calendar_end": "2026-11-10",
    }
    monkeypatch.setattr(module, "data_volume_readonly", types.SimpleNamespace(reload=lambda: None))
    monkeypatch.setattr(module, "simulation", types.SimpleNamespace(reload=lambda: None))
    monkeypatch.setattr(module, "latest_asset", lambda expected_end=None: asset)
    monkeypatch.setattr(
        module,
        "checked_publication_receipt",
        lambda **kwargs: (_ for _ in ()).throw(ValueError("forged receipt")),
    )
    with pytest.raises(ValueError, match="forged receipt"):
        module.daily.local(expected_source_sha256=source, expected_provider_end="2026-11-10")


def test_status_reports_invalid_receipt_instead_of_published(monkeypatch):
    import types

    import research_identity

    module = _website_module()
    expected_source = "a" * 64
    monkeypatch.setattr(module, "simulation", types.SimpleNamespace(reload=lambda: None))
    monkeypatch.setattr(research_identity, "source_manifest", lambda kind: {"sha256": expected_source})
    monkeypatch.setattr(
        module,
        "checked_publication_receipt",
        lambda **kwargs: (_ for _ in ()).throw(ValueError("forged receipt")),
    )
    result = module.status.local(expected_source, "sha256:" + "b" * 64, "2026-11-10")
    assert result["status"] == "INVALID"
    assert "forged receipt" in result["reason"]


def test_status_rejects_a_different_deployed_website_release(monkeypatch):
    import types

    import research_identity

    module = _website_module()
    monkeypatch.setattr(module, "simulation", types.SimpleNamespace(reload=lambda: None))
    monkeypatch.setattr(research_identity, "source_manifest", lambda kind: {"sha256": "b" * 64})
    monkeypatch.setattr(
        module,
        "checked_publication_receipt",
        lambda **kwargs: pytest.fail("a release mismatch must be rejected before reading a receipt"),
    )
    result = module.status.local("a" * 64, "sha256:" + "c" * 64, "2026-11-10")
    assert result["status"] == "INVALID"
    assert "different source identities" in result["reason"]


def test_fixed_fixture_old_filtered_and_new_raw_selection_keep_account_path_exact():
    import hashlib

    ranking_full = [("SH600000", 0.9), ("SH600001", 0.8), ("SH600002", 0.7)]
    old_public = [row for row in ranking_full if row[0] != "SH600000"][:2]
    new_public = ranking_full[:2]
    assert [row[0] for row in old_public] != [row[0] for row in new_public]

    def run():
        portfolio = PaperPortfolio(topk=2, nd=1)
        portfolio.plan_signal("2026-09-30", "2026-10-08", ranking_full)
        report = portfolio.execute_pending(
            "2026-10-08",
            {"SH600000": 10.0, "SH600001": 20.0, "SH600002": 30.0},
            factors={"SH600000": 1.0, "SH600001": 1.0, "SH600002": 1.0},
            close_prices={"SH600000": 10.5, "SH600001": 20.5, "SH600002": 30.5},
            strict_valuation=True,
        )
        state = portfolio.to_dict()
        return {
            "ranking_sha256": hashlib.sha256(json.dumps(ranking_full).encode()).hexdigest(),
            "trades": report["executed_buy"],
            "fees": report["cost"],
            "holdings": report["position_marks"],
            "cash": report["cash"],
            "nav": report["portfolio_value_close"],
            "state": state,
        }

    old_account = run()
    new_account = run()
    assert old_account == new_account
    assert old_account["trades"] and old_account["fees"] > 0
    assert old_account["cash"] >= 0 and old_account["nav"] > 0
    assert [row[0] for row in new_public] == ["SH600000", "SH600001"]
    assert [row[0] for row in old_public] == ["SH600001", "SH600002"]
