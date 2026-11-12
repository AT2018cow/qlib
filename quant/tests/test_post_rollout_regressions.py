"""Behavioral counterexamples from the November 6 post-rollout review."""

import copy
import json
from pathlib import Path

import pytest

from daily_pipeline import DailyPipelineError, existing_publication_result, publication_context, run_daily_pipeline
from signal_publication_gate import validate_multi_market_publication
from test_multi_market_publication import CAL, pooled, res
from test_website_simulation import example_series

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("market", ["csi1000", "csi500", "csi300"])
@pytest.mark.parametrize("damage", ["archive", "calendar", "date", "identity", "malformed_sha"])
def test_override_still_checks_every_market_before_publication(market, damage):
    results = pooled(date="2026-10-14")
    bad = results[market]
    if damage == "archive":
        bad["provider_identity"]["archive_sha256"] = "c" * 64
    elif damage == "calendar":
        bad["provider_identity"]["calendar_sha256"] = "d" * 64
    elif damage == "date":
        results[market] = res(market, "2026-10-15")
    elif damage == "identity":
        bad["market"] = "foreign-market"
    else:
        bad["provider_identity"]["archive_sha256"] = "NOT_A_SHA"
    assert validate_multi_market_publication(results, "2026-10-16", CAL, allow_lag=True)[0] == "raise"


def test_override_allows_only_a_coherent_one_session_lag():
    assert validate_multi_market_publication(pooled(date="2026-10-14"), "2026-10-16", CAL)[0] == "raise"
    action, detail = validate_multi_market_publication(pooled(date="2026-10-14"), "2026-10-16", CAL, allow_lag=True)
    assert action == "publish" and "OPERATOR_OVERRIDE" in detail
    assert validate_multi_market_publication(pooled(date="2026-10-13"), "2026-10-16", CAL, allow_lag=True)[0] == "raise"


@pytest.mark.parametrize(
    "kind", ["empty", "settleable", "future", "same_usage", "signal_off_calendar", "not_t_plus_one", "frozen"]
)
def test_real_ranking_only_planner_never_creates_or_executes_stale_orders(tmp_path, kind):
    from test_cron_gate import _modal_helper_from_source
    from paper_portfolio import PaperPortfolio
    from csi1000_mvp_contract import REFERENCE_ST_POLICY

    state = tmp_path / "state.json"
    pp = PaperPortfolio(topk=10, nd=1)
    signal, execution = {
        "empty": (None, None),
        "settleable": ("2026-11-03", "2026-11-04"),
        "future": ("2026-11-06", "2026-11-09"),
        "same_usage": ("2026-11-05", "2026-11-06"),
        "signal_off_calendar": ("2026-11-04", "2026-11-05"),
        "not_t_plus_one": ("2026-11-03", "2026-11-05"),
        "frozen": ("2026-11-04", "2026-11-05"),
    }[kind]
    if signal:
        pp.plan_signal(signal, execution, [("SH600000", 1)], metadata={"execution_st_policy": REFERENCE_ST_POLICY})
    state.write_text(json.dumps(pp.to_dict()))
    before = state.read_bytes()
    fn = _modal_helper_from_source(
        "_apply_paper_portfolio",
        {
            "Path": Path,
            "_reload_volume_strict": lambda: None,
            "_paper_state_path": lambda *_args: state,
            "_current_st_risk_snapshot": lambda *_args: pytest.fail("ranking-only fetched a new ST snapshot"),
        },
    )
    result = {"market": "csi1000", "date": "2026-11-04", "model_fit_asof": "2026-10-26"}
    calendar = ["2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09"]
    if kind == "signal_off_calendar":
        calendar.remove("2026-11-04")
    kwargs = {"verified_calendar": calendar, "ranking_only": True}
    if kind != "frozen":
        with pytest.raises(RuntimeError, match="frozen pending|T\\+1"):
            fn(result, "2026-11-06", topk=10, nd=1, **kwargs)
    else:
        proposal = fn(result, "2026-11-06", topk=10, nd=1, **kwargs)
        assert json.loads(proposal["state_json"]) == pp.to_dict()
        assert proposal["artifact"]["pending_orders"]["execution_date"] == "2026-11-05"
        assert proposal["artifact"]["pending_orders"]["status"] == "awaiting_execution_data"
        assert proposal["artifact"]["execution_report"] is None
        assert proposal["risk_st"]["date"] == "2026-11-05"
    assert state.read_bytes() == before


def _metadata_files(mode="OPERATOR_OVERRIDE"):
    publication = {
        "usage_date": "2026-11-06",
        "signal_data_date": "2026-11-04",
        "status": "PASS",
        "canonical_freshness_gate": f"{mode} lag=1",
    }
    return {
        f"quant/data/signals/2026-11-06_mvp_metadata{suffix}.json": json.dumps(
            {
                "publication": publication,
                "risk": {
                    "st": {
                        "status": "SOURCE_SNAPSHOT_AVAILABLE",
                        "date": "2026-11-06",
                        "st_symbols": ["SH600000"],
                        "source": "test:dated-st",
                    }
                },
            }
        )
        for suffix in ("", "_csi300", "_csi500")
    }


def test_immutable_legacy_override_recovery_keeps_its_mode_and_is_not_green():
    from types import SimpleNamespace

    files = _metadata_files()
    journal = SimpleNamespace(read_payloads=lambda record: (files, {}))
    record = {"signal_date": "2026-11-06"}
    frozen = copy.deepcopy(files)
    for recovered in (False, True):
        result = existing_publication_result(record, journal, recovered=recovered)
        assert result["status"].endswith("_OPERATOR_OVERRIDE")
        assert result["signal_data_date"] == "2026-11-04" and result["lag_sessions"] == 1
        assert result["risk_st"]["st_symbols"] == ["SH600000"]
        seen = []
        with pytest.raises(DailyPipelineError) as caught:
            run_daily_pipeline(lambda: result, lambda: seen.append("website") or {"status": "PUBLISHED"})
        assert seen == ["website"] and files == frozen
        assert caught.value.stages["signals"]["status"] == "INCOMPLETE_STALE_DATA"
        assert caught.value.stages["website"]["status"] == "SUCCEEDED"
    record["publication_context"] = {**publication_context(files, record["signal_date"]), "lag_sessions": 0}
    with pytest.raises(ValueError, match="provenance"):
        existing_publication_result(record, journal)


@pytest.mark.parametrize("result", [None, {}, {"status": "FAILED"}, {"status": "UNRECOGNIZED"}])
def test_missing_or_unknown_stage_receipts_cannot_be_successful(result):
    with pytest.raises(DailyPipelineError) as caught:
        run_daily_pipeline(lambda: result, lambda: {"status": "PUBLISHED"})
    assert caught.value.stages["signals"]["status"] == "FAILED"
    assert caught.value.stages["website"]["status"] == "SUCCEEDED"


@pytest.mark.parametrize("damage", ["different_date", "different_mode", "wrong_lag"])
def test_journal_context_cannot_hide_incoherent_or_false_lag(damage):
    files = _metadata_files()
    path = sorted(files)[0]
    data = json.loads(files[path])
    if damage == "different_date":
        data["publication"]["signal_data_date"] = "2026-11-03"
    elif damage == "different_mode":
        data["publication"]["canonical_freshness_gate"] = "fresh lag=0"
    else:
        data["publication"]["lag_sessions"] = 0
    files[path] = json.dumps(data)
    with pytest.raises(ValueError):
        publication_context(files, "2026-11-06")


def test_forward_window_exposes_both_capital_bases_and_needs_a_prefix():
    from forward_performance import build_forward_performance
    from merge_forward_performance import merge_forward_performance
    from test_forward_performance import _artifact

    raw = build_forward_performance([_artifact("2026-10-12", 750_000, 760_000)])
    assert raw["nav_basis"]["value"] == 750_000
    assert raw["nav_basis"]["account_initial_cash"] == 1_000_000
    assert raw["cumulative_return"] == pytest.approx(760_000 / 750_000 - 1)
    assert raw["account_cumulative_return"] == pytest.approx(-0.24)
    with pytest.raises(ValueError, match="prefix"):
        merge_forward_performance(None, raw)


def test_same_market_legacy_is_skipped_while_real_cross_market_is_rejected():
    from forward_performance import build_forward_performance
    from test_forward_performance import _artifact

    legacy = _artifact("2026-10-12", 100, 101, lineage="c98856b4_top10_drop1_canonical")
    raw = build_forward_performance([legacy, _artifact("2026-10-12", 100, 101)])
    assert raw["skipped_lineages"] == {legacy["paper_lineage"]: 1}
    foreign = _artifact("2026-10-12", 100, 101, lineage="59b93929_top10_drop1_canonical")
    with pytest.raises(ValueError, match="cross-market"):
        build_forward_performance([foreign])
    foreign["usage_date"] = "2026-10-09"
    assert build_forward_performance([foreign])["points"] == []


def test_actual_frozen_pending_passes_audit_and_a_false_t_plus_one_is_rejected():
    from mvp_entry_review import evaluate_paper_artifact

    paper = json.loads((ROOT / "quant/data/signals/2026-11-06_paper_portfolio.json").read_text())
    calendar = paper["trading_calendar"]["dates"]
    result = evaluate_paper_artifact(paper, calendar)
    assert result["static_artifact_contract"] == "PASS_STATIC_ONLY"
    assert result["pending_status"] == "FROZEN_AWAITING_EXECUTION_DATA"
    paper["pending_orders"]["signal_date"] = "2026-11-03"
    result = evaluate_paper_artifact(paper, calendar)
    assert result["static_artifact_contract"] == "FAIL"
    assert "FROZEN_PENDING_NOT_T_PLUS_ONE" in result["errors"]


def test_new_override_metadata_requires_frozen_orders_and_declares_the_mode(tmp_path):
    from datetime import date, timedelta
    from csi1000_mvp_contract import build_mvp_metadata
    from test_csi1000_mvp_contract import _fixture

    kwargs = _fixture(tmp_path)
    kwargs["publication_detail"] = "OPERATOR_OVERRIDE lag=1 ranking-only"
    with pytest.raises(ValueError, match="older frozen"):
        build_mvp_metadata(**kwargs)
    data_day = date.fromisoformat(kwargs["signal_data_date"])
    kwargs["paper"]["pending_orders"].update(
        execution_date=(data_day + timedelta(days=1)).isoformat(), status="awaiting_execution_data"
    )
    meta = build_mvp_metadata(**kwargs)
    assert meta["publication"]["status"] == "PASS_OPERATOR_OVERRIDE"
    assert meta["publication"]["lag_sessions"] == 1
    assert meta["paper"]["pending_execution_date"] < kwargs["usage_date"]


@pytest.mark.parametrize("damage", ["model_sha", "data_sha", "gap", "overlap", "point_model", "point_date", "version"])
def test_fit_receipts_must_cover_each_point_and_bind_its_model(damage):
    from website_simulation import validate_website_series

    raw, _ = example_series()
    if damage in ("model_sha", "data_sha"):
        raw["fit_receipts"][0]["model_sha256" if damage == "model_sha" else "data_prefix_sha256"] = ""
    elif damage == "gap":
        raw["fit_receipts"][0]["signal_end"] = "2026-09-30"
    elif damage == "overlap":
        raw["fit_receipts"].append(copy.deepcopy(raw["fit_receipts"][0]))
    elif damage == "point_model":
        raw["points"][1]["model_sha256"] = "e" * 64
    elif damage == "point_date":
        raw["points"][1]["signal_date"] = raw["points"][1]["date"]
    else:
        raw["fit_receipt_version"] = 99
    with pytest.raises(ValueError):
        validate_website_series(raw)


def test_parent_timeout_covers_all_serial_children_without_automatic_retry_multiplication():
    import ast
    import daily_pipeline as budget

    assert budget.MAIN_TIMEOUT_SECONDS > 3 * budget.SIGNAL_WORKER_TIMEOUT_SECONDS + budget.WEBSITE_STAGE_TIMEOUT_SECONDS
    assert budget.BACKUP_TIMEOUT_SECONDS > 2 * budget.MAIN_TIMEOUT_SECONDS
    assert budget.BACKUP_TIMEOUT_SECONDS <= 24 * 3600
    # Each child has a budget even if all three markets are serialized by the platform.
    assert budget.WEBSITE_STAGE_TIMEOUT_SECONDS > 4 * budget.WEBSITE_WORKER_TIMEOUT_SECONDS
    for relative, names in [
        ("production/modal_qlib_cn_a10g.py", {"daily_cron", "daily_repair_manual", "daily_standalone"}),
        ("ops/website_simulation_modal.py", {"daily", "build_market", "build_market_incremental"}),
    ]:
        tree = ast.parse((ROOT / "quant/code" / relative).read_text())
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in names:
                decorators = [kw for dec in node.decorator_list if isinstance(dec, ast.Call) for kw in dec.keywords]
                retries = [kw.value for kw in decorators if kw.arg == "retries"]
                assert not retries or all(isinstance(value, ast.Constant) and value.value == 0 for value in retries)


def _quote_replay(missing_held):
    import pandas as pd
    from quant.code.research.regime_reselection.prelive_revalidation import replay

    days = ["2026-09-30", "2026-10-08", "2026-10-09"] if missing_held else ["2026-09-30", "2026-10-08"]
    signal = pd.Series(
        1.0,
        index=pd.MultiIndex.from_product([pd.to_datetime(days[:-1]), ["SH600000"]], names=["datetime", "instrument"]),
    )
    quote = {
        key: {"SH600000": value}
        for key, value in (("$open", 10), ("$close", 10), ("$factor", 1), ("Ref($close,1)", 10), ("$volume", 100_000))
    }
    empty = {key: {} for key in quote}
    quotes = {"2026-10-08": quote, "2026-10-09": empty} if missing_held else {"2026-10-08": empty}
    data = {
        "days": days,
        "signal": signal,
        "quotes": quotes,
        "calendar": days,
        "listing": {},
        "bench": pd.Series(0.0, index=pd.to_datetime(days[1:])),
    }
    return replay(data, {"id": "quote-gap-regression", "topk": 1, "dropn": 0}, progress_every=0)


def test_missing_unheld_candidate_keeps_cash_without_false_valuation_gap():
    result = _quote_replay(False)
    assert result["gaps"] == []
    assert result["sessions"][0]["cash"] == 1_000_000
    assert result["sessions"][0]["position_marks"] == {}
    assert result["sessions"][0]["executed_buy"] == []
    assert len(result["sessions"][0]["blocked_buy"]) == 1


def test_missing_held_quote_is_a_real_gap_and_cannot_reach_website():
    from website_simulation import build_website_series

    result = _quote_replay(True)
    assert {gap["field"] for gap in result["gaps"]} == {"open", "close"}
    with pytest.raises(ValueError, match="missing held quotes"):
        from research_identity import account_contract_manifest, website_semantic_manifest
        from website_simulation import account_contract_for_market

        build_website_series(
            "csi300",
            result,
            calendar=[],
            source_sha="a" * 64,
            semantic_sha=website_semantic_manifest()["sha256"],
            account_sha=account_contract_manifest(account_contract_for_market("csi300"))["sha256"],
            provider_sha="b" * 64,
            fits=[],
            st_history_sha256="e" * 64,
            st_history_source="test:dated-st",
        )


@pytest.mark.parametrize("damage", [None, "fee", "cash", "inventory", "nav", "wrong_account"])
def test_independent_decimal_website_audit_detects_ledger_and_scope_errors(damage):
    from audit_website_nav import reconcile_website

    series, sessions = example_series()
    sessions[0]["opening_marks"] = {}
    sessions[1]["opening_marks"] = {
        symbol: {"shares": pos["shares"], "price": 11} for symbol, pos in sessions[1]["opening_positions"].items()
    }
    if damage == "fee":
        sessions[0]["executed_buy"][0]["cost"] += 1
    elif damage == "cash":
        sessions[1]["opening_cash"] += 1
    elif damage == "inventory":
        sessions[1]["opening_positions"]["SH600000"]["shares"] += 1
    elif damage == "nav":
        series["points"][0]["nav"] += 0.01
    elif damage == "wrong_account":
        series["account_identity"]["paper_lineage"] = "another_account"
    result = reconcile_website(series, sessions, market="csi300")
    assert result["status"] == ("FAIL" if damage else "PARTIAL_NOT_CERTIFIED")
    assert result["ledger_status"] == ("FAIL" if damage else "PASS")
    if damage is None:
        assert result["audited_reports"] == 2 and result["warnings"]


def test_canonical_audit_rejects_wrong_account_before_comparing_unrelated_ledgers():
    from audit_paper_nav import reconcile

    series, _ = example_series()
    result = reconcile([None], forward=series, market="csi300")
    assert result["status"] == "FAIL"
    assert result["audited_reports"] == 0
    assert [issue["code"] for issue in result["issues"]] == ["ACCOUNT_SCOPE_MISMATCH"]


def test_canonical_audit_auto_starts_at_live_segment_and_skips_backfill():
    from audit_paper_nav import reconcile

    artifacts = [
        json.loads(path.read_text()) for path in sorted((ROOT / "quant/data/signals").glob("*_paper_portfolio.json"))
    ]
    result = reconcile(artifacts, market="csi1000")
    assert result["start_date"] == "2026-11-03"
    assert result["audited_reports"] >= 1
    assert result["skipped_artifacts"]["backfill_research"] > 0
    assert not any(issue["code"] == "UNREPORTED_POSITION_CHANGE" for issue in result["issues"])
