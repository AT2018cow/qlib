"""Daily scheduling, independent recovery, and deployment identity boundaries."""

import ast
import importlib.util
import json
import pickle
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from daily_pipeline import DailyPipelineError, run_daily_pipeline


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("failed_stage", ["signals", "website"])
def test_failed_stage_does_not_hide_or_undo_other_publication(failed_stage):
    published = set()
    attempts = {"signals": 0, "website": 0}
    writes = {"signals": 0, "website": 0}

    def runner(name):
        def run():
            attempts[name] += 1
            if name == failed_stage and attempts[name] == 1:
                raise OSError("transient failure")
            if name in published:
                return {"status": "ALREADY_PUBLISHED"}
            published.add(name)
            writes[name] += 1
            return {"status": "PUBLISHED"}

        return run

    signals, website = runner("signals"), runner("website")
    with pytest.raises(DailyPipelineError) as caught:
        run_daily_pipeline(signals, website)
    assert published == ({"signals", "website"} - {failed_stage})
    assert caught.value.stages[failed_stage]["status"] == "FAILED"
    # Modal transports exceptions; partial results must survive serialization.
    recovered = pickle.loads(pickle.dumps(caught.value))
    assert recovered.stages == caught.value.stages
    assert isinstance(caught.value.__cause__, OSError)

    result = run_daily_pipeline(signals, website)
    assert result["status"] == "COMPLETED"
    assert published == {"signals", "website"}
    assert writes == {"signals": 1, "website": 1}
    assert result["stages"][failed_stage]["result"]["status"] == "PUBLISHED"
    assert result["stages"][next(n for n in published if n != failed_stage)]["result"]["status"] == "ALREADY_PUBLISHED"


def _provider_receipt(asof: str) -> dict:
    return {
        "status": "PUBLISHED",
        "date": asof,
        "digest": "a" * 64,
        "calendar_end": asof,
        "receipt_sha256": "b" * 64,
        "gate": "PASS",
    }


def _committed_journal(tmp_path: Path, signal_date: str) -> None:
    from publication_journal import FilesystemStorage, PublicationJournal

    storage = FilesystemStorage(tmp_path)
    journal = PublicationJournal(storage)
    record = journal.prepare(signal_date, "test-holder", {}, {})
    journal.mark(record, "GITHUB_PUBLISHED")
    journal.mark(record, "VOLUME_COMMITTED")


def _fixed_postclose(monkeypatch, session_date="2026-11-10", usage_date="2026-11-11"):
    import daily_pipeline
    import modal_qlib_cn_a10g as production

    monkeypatch.setattr(
        daily_pipeline,
        "resolve_postclose_dates",
        lambda *args, **kwargs: (session_date, usage_date),
    )
    monkeypatch.setattr(production, "_trading_days", lambda: [session_date, usage_date])
    return session_date, usage_date


@pytest.mark.parametrize(
    "signal_status", ["ALREADY_PUBLISHED", "RECOVERED", "SKIPPED_STALE_DATA", "SKIPPED_NON_TRADING_DAY"]
)
def test_actual_cron_checks_website_after_signal_fast_path(tmp_path, monkeypatch, signal_status):
    import modal_qlib_cn_a10g as production

    session_date, usage_date = _fixed_postclose(monkeypatch)
    seen = []
    monkeypatch.setattr(production, "VOL_ROOT", tmp_path)
    monkeypatch.setattr(production, "vol", SimpleNamespace(reload=lambda: None, commit=lambda: None))

    def signals(allow_lag, **kwargs):
        assert allow_lag is False
        assert kwargs == {"signal_date_override": usage_date, "data_asof_override": session_date}
        seen.append("signals")
        return {"status": signal_status}

    def website(expected_provider_end=None, st_snapshot=None):
        assert expected_provider_end == session_date
        assert st_snapshot is None
        seen.append("website")
        return {"status": "ALREADY_PUBLISHED", "latest_date": session_date}

    monkeypatch.setattr(production, "_run_daily_signal_cycle", signals)
    monkeypatch.setattr(production, "_update_website_returns", website)
    monkeypatch.setattr(
        production,
        "ensure_overlay",
        SimpleNamespace(remote=lambda asof, refresh_memberships=False: _provider_receipt(asof)),
    )
    _committed_journal(tmp_path, usage_date)
    marker_path = tmp_path / "postclose_status" / f"{usage_date}.json"
    if signal_status == "SKIPPED_STALE_DATA":
        with pytest.raises(DailyPipelineError) as caught:
            production.daily_cron.local()
        result = {"stages": caught.value.stages}
        assert result["stages"]["signals"]["status"] == "INCOMPLETE_STALE_DATA"
        failed = json.loads(marker_path.read_text())
        assert failed["status"] == "FAILED"
        assert failed["schema_version"] == 2
        assert failed["session_date"] == session_date
        assert failed["usage_date"] == usage_date
    else:
        result = production.daily_cron.local()
        assert result["stages"]["signals"]["result"]["status"] == signal_status
        assert result["stages"]["website"]["result"]["status"] == "ALREADY_PUBLISHED"
        marker = json.loads(marker_path.read_text())
        assert marker["status"] == "COMPLETED"
        assert marker["schema_version"] == 2
        assert marker["provider"]["digest"] == "a" * 64
        assert marker["session_date"] == session_date
        assert marker["usage_date"] == usage_date
        assert marker["provider_calendar_end"] == session_date
        assert marker["website_latest_date"] == session_date
        assert marker["journal_signal_date"] == usage_date
    assert seen == ["signals", "website"]


def test_postclose_cycle_uses_the_self_hosted_provider(tmp_path, monkeypatch):
    import modal_qlib_cn_a10g as production

    session_date, usage_date = _fixed_postclose(monkeypatch)
    seen = []
    provider_calls = []
    monkeypatch.setattr(production, "VOL_ROOT", tmp_path)
    monkeypatch.setattr(production, "vol", SimpleNamespace(reload=lambda: None, commit=lambda: None))

    def ensure_provider(asof, refresh_memberships=False):
        provider_calls.append((asof, refresh_memberships))
        return _provider_receipt(asof)

    monkeypatch.setattr(production, "ensure_overlay", SimpleNamespace(remote=ensure_provider))

    def signals(allow_lag, **kwargs):
        seen.append(("signals", allow_lag, kwargs))
        return {
            "status": "ALREADY_PUBLISHED",
            "risk_st": {
                "status": "SOURCE_SNAPSHOT_AVAILABLE",
                "date": usage_date,
                "st_symbols": ["SH600000", "SH600002"],
                "top20_st": ["SH600000"],
                "source": "akshare.stock_zh_a_st_em",
                "fetched_at_cst": "2026-11-11T18:30:00+08:00",
            },
        }

    def website(expected_provider_end=None, st_snapshot=None):
        seen.append(("website", expected_provider_end, st_snapshot))
        return {"status": "ALREADY_PUBLISHED", "latest_date": session_date}

    monkeypatch.setattr(production, "_run_daily_signal_cycle", signals)
    monkeypatch.setattr(production, "_update_website_returns", website)
    _committed_journal(tmp_path, usage_date)
    result = production.daily_cron.local()
    assert result["status"] == "COMPLETED"
    assert provider_calls == [(session_date, False)]
    assert seen[0][0] == "signals"
    assert seen[0][1] is False
    assert seen[0][2]["data_asof_override"] == session_date
    assert seen[1] == (
        "website",
        session_date,
        {
            "date": usage_date,
            "symbols": ["SH600000", "SH600002"],
            "source": "akshare.stock_zh_a_st_em",
            "fetched_at_cst": "2026-11-11T18:30:00+08:00",
        },
    )
    marker = json.loads((tmp_path / "postclose_status" / f"{usage_date}.json").read_text())
    assert marker["status"] == "COMPLETED"
    assert marker["provider"]["digest"] == "a" * 64
    assert marker["session_date"] == session_date
    assert marker["usage_date"] == usage_date


def test_evening_retry_skips_after_completed_marker(tmp_path, monkeypatch):
    import modal_qlib_cn_a10g as production

    session_date, usage_date = _fixed_postclose(monkeypatch)
    marker = tmp_path / "postclose_status" / f"{usage_date}.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"schema_version": 2, "status": "COMPLETED"}))
    monkeypatch.setattr(production, "VOL_ROOT", tmp_path)
    monkeypatch.setattr(production, "vol", SimpleNamespace(reload=lambda: None, commit=lambda: None))
    monkeypatch.setattr(production, "_postclose_marker_valid", lambda *args, **kwargs: (True, "test"))
    monkeypatch.setattr(
        production,
        "daily_cron",
        SimpleNamespace(remote=lambda **kwargs: pytest.fail("completed postclose must not be retried")),
    )
    result = production.daily_evening_retry.local()
    assert result["status"] == "ALREADY_COMPLETED"
    assert result["session_date"] == session_date
    assert result["usage_date"] == usage_date


def test_evening_retry_forwards_only_the_single_coordinator(tmp_path, monkeypatch):
    import modal_qlib_cn_a10g as production

    session_date, usage_date = _fixed_postclose(monkeypatch)
    monkeypatch.setattr(production, "VOL_ROOT", tmp_path)
    monkeypatch.setattr(production, "vol", SimpleNamespace(reload=lambda: None, commit=lambda: None))
    monkeypatch.setattr(production, "_postclose_marker_valid", lambda *args, **kwargs: (False, "missing"))
    calls = {}

    def remote(**kwargs):
        calls.update(kwargs)
        return {"status": "QUEUED"}

    monkeypatch.setattr(production, "daily_cron", SimpleNamespace(remote=remote))
    result = production.daily_evening_retry.local()
    assert result == {"status": "QUEUED"}
    assert calls == {
        "allow_lag": False,
        "signal_date_override": usage_date,
        "data_asof_override": session_date,
        "refresh_provider": True,
    }


def test_postclose_check_fails_closed_when_marker_is_missing(tmp_path, monkeypatch):
    import modal_qlib_cn_a10g as production

    session_date, usage_date = _fixed_postclose(monkeypatch)
    monkeypatch.setattr(production, "VOL_ROOT", tmp_path)
    monkeypatch.setattr(production, "vol", SimpleNamespace(reload=lambda: None, commit=lambda: None))
    with pytest.raises(RuntimeError, match="marker .* is missing"):
        production.daily_postclose_check.local()
    marker = tmp_path / "postclose_status" / f"{usage_date}.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"schema_version": 2, "status": "RUNNING"}))
    monkeypatch.setattr(
        production, "_postclose_marker_valid", lambda *args, **kwargs: (False, "marker is not COMPLETED")
    )
    with pytest.raises(RuntimeError, match="incomplete"):
        production.daily_postclose_check.local()


def test_postclose_check_returns_ok_only_after_full_readonly_verification(tmp_path, monkeypatch):
    import modal_qlib_cn_a10g as production

    session_date, usage_date = _fixed_postclose(monkeypatch)
    marker = tmp_path / "postclose_status" / f"{usage_date}.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "status": "COMPLETED",
                "session_date": session_date,
                "usage_date": usage_date,
                "provider": {"digest": "sha256:" + "a" * 64},
                "signal": {"github_commit": "c" * 40},
                "release_source_sha256": "d" * 64,
                "website_semantic_sha256": "e" * 64,
                "account_contract_sha256": {"csi300": "f" * 64},
            }
        )
    )
    monkeypatch.setattr(production, "VOL_ROOT", tmp_path)
    monkeypatch.setattr(production, "vol", SimpleNamespace(reload=lambda: None, commit=lambda: None))
    monkeypatch.setattr(production, "_postclose_marker_valid", lambda *args, **kwargs: (True, "test"))
    result = production.daily_postclose_check.local()
    assert result["status"] == "OK"
    assert result["session_date"] == session_date
    assert result["usage_date"] == usage_date
    assert result["journal_commit"] == "c" * 40


def test_next_morning_check_uses_the_same_readonly_verifier(tmp_path, monkeypatch):
    import modal_qlib_cn_a10g as production
    import daily_pipeline

    monkeypatch.setattr(
        daily_pipeline,
        "resolve_check_dates",
        lambda *args, **kwargs: ("2026-11-10", "2026-11-11"),
    )
    monkeypatch.setattr(production, "_trading_days", lambda: ["2026-11-10", "2026-11-11"])
    marker = tmp_path / "postclose_status" / "2026-11-11.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"schema_version": 2, "status": "COMPLETED"}))
    monkeypatch.setattr(production, "VOL_ROOT", tmp_path)
    monkeypatch.setattr(production, "vol", SimpleNamespace(reload=lambda: None, commit=lambda: None))
    monkeypatch.setattr(production, "_postclose_marker_valid", lambda *args, **kwargs: (True, "test"))
    result = production.daily_readonly_check.local()
    assert result["status"] == "OK"
    assert result["session_date"] == "2026-11-10"
    assert result["usage_date"] == "2026-11-11"


def test_postclose_marker_rejects_unverified_receipts(tmp_path, monkeypatch):
    import modal_qlib_cn_a10g as production
    import data_ingest.store as store

    monkeypatch.setattr(production, "VOL_ROOT", tmp_path)
    monkeypatch.setattr(production, "vol", SimpleNamespace(reload=lambda: None, commit=lambda: None))
    snapshot = SimpleNamespace(digest="a" * 64, calendar_end="2026-11-10")
    monkeypatch.setattr(store, "load_snapshot", lambda root, date: snapshot)
    monkeypatch.setattr(production, "_code_source_sha", lambda: "c" * 64)
    base = {
        "schema_version": 2,
        "status": "COMPLETED",
        "session_date": "2026-11-10",
        "usage_date": "2026-11-11",
        "provider_calendar_end": "2026-11-10",
        "website_latest_date": "2026-11-10",
        "journal_signal_date": "2026-11-11",
        "provider": {"digest": "a" * 64, "calendar_end": "2026-11-10"},
        "release_source_sha256": "c" * 64,
        "code_source_sha256": "c" * 64,
    }
    # No journal record in an empty volume.
    valid, reason = production._postclose_marker_valid(base, "2026-11-10", "2026-11-11", verify_github=False)
    assert not valid
    assert "journal" in reason or "provider" in reason
    wrong = dict(base, provider={"digest": "d" * 64, "calendar_end": "2026-11-10"})
    valid, reason = production._postclose_marker_valid(wrong, "2026-11-10", "2026-11-11", verify_github=False)
    assert not valid
    assert "provider" in reason
    legacy = {k: v for k, v in base.items() if k != "schema_version"}
    valid, reason = production._postclose_marker_valid(legacy, "2026-11-10", "2026-11-11", verify_github=False)
    assert not valid
    assert "schema" in reason


def test_checkers_are_read_only_and_have_no_publication_secret():
    tree = ast.parse((ROOT / "quant/code/production/modal_qlib_cn_a10g.py").read_text())
    for name in ("daily_postclose_check", "daily_evening_retry", "daily_readonly_check"):
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
        decorator = next(
            d
            for d in node.decorator_list
            if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "function"
        )
        keywords = {kw.arg: kw.value for kw in decorator.keywords}
        volumes = keywords["volumes"].values[0]
        assert isinstance(volumes, ast.Name) and volumes.id == "vol_readonly", name
        assert "secrets" not in keywords, name
        source = ast.get_source_segment((ROOT / "quant/code/production/modal_qlib_cn_a10g.py").read_text(), node)
        for forbidden in ("daily_standalone", "ensure_overlay", "build_market", ".remote("):
            if name == "daily_evening_retry" and forbidden == ".remote(":
                continue
            assert forbidden not in source, f"{name} must not run heavy work: {forbidden}"


def test_backup_returns_the_same_pipeline_receipt(monkeypatch):
    import modal_qlib_cn_a10g as production

    receipt = {"status": "COMPLETED", "stages": {"website": {"status": "SUCCEEDED"}}}
    monkeypatch.setattr(production, "daily_cron", SimpleNamespace(remote=lambda: receipt))
    assert production.daily_repair_manual.local() == receipt


@pytest.mark.parametrize("receipt", [None, {"status": "FAILED"}, {"status": "PUBLISHED", "source_sha256": "b" * 64}])
def test_orchestrator_rejects_missing_or_mismatched_website_receipt(monkeypatch, receipt):
    import modal_qlib_cn_a10g as production
    import research_identity

    monkeypatch.setattr(research_identity, "source_manifest", lambda kind: {"sha256": "a" * 64})

    def remote(**kwargs):
        assert kwargs == {"expected_source_sha256": "a" * 64, "expected_provider_end": ""}
        return receipt

    def from_name(app, name):
        assert (app, name) == ("qlib-website-nav", "daily")
        return SimpleNamespace(remote=remote)

    monkeypatch.setattr(production, "modal", SimpleNamespace(Function=SimpleNamespace(from_name=from_name)))
    with pytest.raises(RuntimeError, match="matching successful receipt"):
        production._update_website_returns()


def test_website_worker_refuses_wrong_deployment_before_fetching_data(monkeypatch):
    path = ROOT / "quant/code/ops/website_simulation_modal.py"
    spec = importlib.util.spec_from_file_location("website_unscheduled_test", path)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)

    def forbidden():
        pytest.fail("mismatched deployment accessed the data source")

    monkeypatch.setattr(worker, "latest_asset", forbidden)
    with pytest.raises(ValueError, match="different source identities"):
        worker.daily.local(expected_source_sha256="wrong-deployment")


def test_only_main_retry_and_readonly_checks_are_scheduled():
    schedules = []
    for relative in ["quant/code/production/modal_qlib_cn_a10g.py", "quant/code/ops/website_simulation_modal.py"]:
        tree = ast.parse((ROOT / relative).read_text())
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                for decorator in node.decorator_list:
                    if isinstance(decorator, ast.Call):
                        for kw in decorator.keywords:
                            if kw.arg == "schedule":
                                schedules.append(
                                    (
                                        node.name,
                                        ast.literal_eval(kw.value.args[0]),
                                        {k.arg: ast.literal_eval(k.value) for k in kw.value.keywords},
                                    )
                                )
    assert schedules == [
        ("daily_cron", "0 18 * * 1-5", {"timezone": "Asia/Shanghai"}),
        ("daily_postclose_check", "0 23 * * 1-5", {"timezone": "Asia/Shanghai"}),
        ("daily_evening_retry", "15 23 * * 1-5", {"timezone": "Asia/Shanghai"}),
        ("daily_readonly_check", "0 7 * * 1-5", {"timezone": "Asia/Shanghai"}),
    ]


def test_reused_qlib_worker_reinitializes_after_success_and_failed_fit(tmp_path):
    script = r"""
from pathlib import Path
import os, sys
os.environ["MLFLOW_ALLOW_FILE_STORE"] = "true"
os.environ["MLFLOW_DISABLE_AGENT_HINT"] = "1"
sys.path.insert(0, str(Path(sys.argv[2]) / "quant/code"))
from quant_bootstrap import ensure_quant_import_paths
ensure_quant_import_paths()
import qlib
from qlib.workflow import R
from website_simulation_modal import fit_website_model
root = Path(sys.argv[1])
class Model:
    def __init__(self, fail=False): self.fail = fail
    def fit(self, dataset):
        R.log_metrics(loss=1.0)
        if self.fail: raise RuntimeError("training failed")
for i in range(3):
    qlib.init(provider_uri=str(root), region="cn", exp_manager={
        "class": "MLflowExpManager", "module_path": "qlib.workflow.expm",
        "kwargs": {"uri": (root / f"experiments-{i}").as_uri(), "default_exp_name": "website-simulation"}})
    if i == 1:
        try: fit_website_model(Model(fail=True), None)
        except RuntimeError as exc: assert str(exc) == "training failed"
        else: raise AssertionError("training failure hidden")
    else: fit_website_model(Model(), None)
print("REUSED_WORKER_PASS")
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), str(ROOT)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "REUSED_WORKER_PASS" in result.stdout


def test_select_overlay_provider_materializes_archive(tmp_path, monkeypatch):
    import json
    from pathlib import Path

    import numpy as np

    from data_ingest.store import build_receipt, create_provider_archive, publish_archive

    provider = tmp_path / "provider"
    (provider / "calendars").mkdir(parents=True)
    (provider / "calendars" / "day.txt").write_text("2026-11-09\n2026-11-10\n")
    (provider / "instruments").mkdir()
    (provider / "instruments" / "csi300.txt").write_text("SH600000\t2026-11-09\t2026-11-10\n")
    feature = provider / "features" / "sh600000"
    feature.mkdir(parents=True)
    for field in ("open", "high", "low", "close", "volume", "amount", "factor", "vwap", "change", "adjclose"):
        np.hstack([[0.0], [10.0, 10.5]]).astype("<f").tofile(feature / f"{field}.day.bin")
    (provider / "manifest.json").write_text(
        json.dumps(
            {
                "status": "PASS",
                "base": {"calendar_end": "2026-11-09", "path": "provider:base"},
                "target": {"asof": "2026-11-10", "dates": ["2026-11-10"]},
            }
        )
    )
    root = tmp_path / "root"
    archive = tmp_path / "x.tar.gz"
    meta = create_provider_archive(provider, archive)
    import hashlib

    receipt = build_receipt(
        None,
        date="2026-11-10",
        archive=meta,
        manifest_sha256=hashlib.sha256((provider / "manifest.json").read_bytes()).hexdigest(),
        calendar_sha256=hashlib.sha256((provider / "calendars/day.txt").read_bytes()).hexdigest(),
        base={"calendar_end": "2026-11-09", "path": "provider:base"},
        provenance={"builder": "test"},
    )
    root.mkdir()
    publish_archive(
        root,
        receipt,
        archive,
        local_manifest=provider / "manifest.json",
        local_calendar=provider / "calendars" / "day.txt",
    )
    import modal_qlib_cn_a10g as production

    monkeypatch.setattr(production, "OVERLAY_PROVIDER_ROOT", root)
    monkeypatch.setattr(production, "vol", SimpleNamespace(reload=lambda: None, commit=lambda: None))
    data_dir, identity = production._select_overlay_provider("2026-11-10")
    assert (data_dir / "calendars" / "day.txt").read_text().splitlines()[-1] == "2026-11-10"
    assert identity["provider_kind"] == "archive"
    assert identity["archive_sha256"] == meta["sha256"]
    assert identity["calendar_end"] == "2026-11-10"


def test_select_overlay_provider_refuses_missing_snapshot(tmp_path, monkeypatch):
    import modal_qlib_cn_a10g as production

    monkeypatch.setattr(production, "OVERLAY_PROVIDER_ROOT", tmp_path / "empty")
    monkeypatch.setattr(production, "vol", SimpleNamespace(reload=lambda: None, commit=lambda: None))
    (tmp_path / "empty").mkdir()
    with pytest.raises(RuntimeError, match="requested provider missing"):
        production._select_overlay_provider("2026-11-10")


def test_asset_digest_normalizes_bare_and_prefixed_forms():
    import modal_qlib_cn_a10g as production

    bare = "a" * 64
    assert production._asset_digest(bare) == "sha256:" + bare
    assert production._asset_digest("sha256:" + bare) == "sha256:" + bare
    assert production._asset_digest(None) == "sha256:"


def test_postclose_marker_rejects_semantic_account_or_latest_mismatch(tmp_path, monkeypatch):
    import modal_qlib_cn_a10g as production
    import data_ingest.store as store

    session_date, usage_date = "2026-11-10", "2026-11-11"
    _committed_journal(tmp_path, usage_date)
    monkeypatch.setattr(production, "VOL_ROOT", tmp_path)
    monkeypatch.setattr(production, "vol", SimpleNamespace(reload=lambda: None, commit=lambda: None))
    snapshot = SimpleNamespace(digest="a" * 64, calendar_end=session_date)
    monkeypatch.setattr(store, "load_snapshot", lambda root, date: snapshot)
    monkeypatch.setattr(production, "_code_source_sha", lambda: "c" * 64)
    accounts = {market: "f" * 64 for market in ("csi300", "csi500", "csi1000")}
    marker = {
        "schema_version": 2,
        "status": "COMPLETED",
        "session_date": session_date,
        "usage_date": usage_date,
        "provider_calendar_end": session_date,
        "website_latest_date": session_date,
        "journal_signal_date": usage_date,
        "provider": {"digest": "a" * 64, "calendar_end": session_date},
        "release_source_sha256": "c" * 64,
        "code_source_sha256": "c" * 64,
        "website_semantic_sha256": "e" * 64,
        "account_contract_sha256": accounts,
    }
    import json
    from data_ingest.manifest import canonical_json, sha256_text

    record = (
        __import__("publication_journal")
        .PublicationJournal(__import__("publication_journal").FilesystemStorage(tmp_path))
        .load(usage_date)
    )
    marker["signal"] = {"record_sha256": sha256_text(canonical_json(record))}

    status_result = {
        "status": "PUBLISHED",
        "source_sha256": "c" * 64,
        "website_semantic_sha256": "e" * 64,
        "account_contract_sha256": accounts,
        "asset_digest": "sha256:" + "a" * 64,
        "latest_date": session_date,
    }

    def from_name(_app, name):
        assert name == "status"
        return SimpleNamespace(remote=lambda *args: dict(status_result))

    monkeypatch.setattr(production, "modal", SimpleNamespace(Function=SimpleNamespace(from_name=from_name)))
    valid, reason = production._postclose_marker_valid(marker, session_date, usage_date, verify_github=False)
    assert valid, reason
    for field, value in (
        ("website_semantic_sha256", "9" * 64),
        ("account_contract_sha256", {**accounts, "csi300": "9" * 64}),
        ("latest_date", "2026-11-09"),
    ):
        changed = dict(status_result)
        changed[field] = value
        monkeypatch.setattr(
            production,
            "modal",
            SimpleNamespace(
                Function=SimpleNamespace(
                    from_name=lambda _app, _name, changed=changed: SimpleNamespace(remote=lambda *args: dict(changed))
                )
            ),
        )
        valid, reason = production._postclose_marker_valid(marker, session_date, usage_date, verify_github=False)
        assert not valid, field
        expected = {
            "website_semantic_sha256": "semantic",
            "account_contract_sha256": "account",
            "latest_date": "latest",
        }[field]
        assert expected in reason


def test_evening_retry_timeout_covers_queueing_plus_one_full_recovery():
    from daily_pipeline import BACKUP_TIMEOUT_SECONDS, MAIN_TIMEOUT_SECONDS

    tree = ast.parse((ROOT / "quant/code/production/modal_qlib_cn_a10g.py").read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "daily_evening_retry")
    decorator = next(
        d
        for d in node.decorator_list
        if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "function"
    )
    timeout = next(kw.value for kw in decorator.keywords if kw.arg == "timeout")
    value = eval(
        compile(ast.Expression(timeout), str(ROOT), "eval"),
        {"BACKUP_TIMEOUT_SECONDS": BACKUP_TIMEOUT_SECONDS},
    )
    assert value == BACKUP_TIMEOUT_SECONDS
    assert BACKUP_TIMEOUT_SECONDS >= MAIN_TIMEOUT_SECONDS + 30 * 60
