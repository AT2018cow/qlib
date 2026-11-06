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


def test_publication_recovers_lost_response_and_does_not_push_twice(tmp_path, monkeypatch):
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
    published, pushes = {}, []

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
    with pytest.raises(RuntimeError, match="publication succeeded"):
        module.publish_results(results, {}, asset, token="TEST_ONLY")
    monkeypatch.setattr(website_simulation, "write_immutable", original)
    assert module.publish_results(results, {}, asset, token="TEST_ONLY")["status"] == "ALREADY_PUBLISHED"
    assert len(pushes) == 1 and len(published) == 12
    assert len(list(tmp_path.rglob("publication.json"))) == 1
    assert all(json.loads(v)["schema_version"] == 3 for v in published.values())
