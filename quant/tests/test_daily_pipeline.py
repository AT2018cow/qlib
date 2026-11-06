"""Daily scheduling, independent recovery, and deployment identity boundaries."""

import ast
import importlib.util
import pickle
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


@pytest.mark.parametrize(
    "signal_status", ["ALREADY_PUBLISHED", "RECOVERED", "SKIPPED_STALE_DATA", "SKIPPED_NON_TRADING_DAY"]
)
def test_actual_cron_checks_website_after_signal_fast_path(monkeypatch, signal_status):
    import modal_qlib_cn_a10g as production

    seen = []

    def signals(allow_lag):
        assert allow_lag is False
        seen.append("signals")
        return {"status": signal_status}

    def website():
        seen.append("website")
        return {"status": "ALREADY_PUBLISHED"}

    monkeypatch.setattr(production, "_run_daily_signal_cycle", signals)
    monkeypatch.setattr(production, "_update_website_returns", website)
    result = production.daily_cron.local()
    assert seen == ["signals", "website"]
    assert result["stages"]["signals"]["result"]["status"] == signal_status
    assert result["stages"]["website"]["result"]["status"] == "ALREADY_PUBLISHED"


def test_backup_returns_the_same_pipeline_receipt(monkeypatch):
    import modal_qlib_cn_a10g as production

    receipt = {"status": "COMPLETED", "stages": {"website": {"status": "SUCCEEDED"}}}
    monkeypatch.setattr(production, "daily_cron", SimpleNamespace(remote=lambda: receipt))
    assert production.daily_cron_second_chance.local() == receipt


@pytest.mark.parametrize("receipt", [None, {"status": "FAILED"}, {"status": "PUBLISHED", "source_sha256": "b" * 64}])
def test_orchestrator_rejects_missing_or_mismatched_website_receipt(monkeypatch, receipt):
    import modal_qlib_cn_a10g as production
    import research_identity

    monkeypatch.setattr(research_identity, "source_manifest", lambda kind: {"sha256": "a" * 64})

    def remote(**kwargs):
        assert kwargs == {"expected_source_sha256": "a" * 64}
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


def test_only_main_and_eight_oclock_backup_are_scheduled():
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
        ("daily_cron", "0 7 * * 1-5", {"timezone": "Asia/Shanghai"}),
        ("daily_cron_second_chance", "0 8 * * 1-5", {"timezone": "Asia/Shanghai"}),
    ]
