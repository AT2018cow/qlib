"""Acceptance regressions without provider, Modal, broker or look-ahead data."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from forward_performance import CANONICAL_LINEAGE, build_forward_performance
from paper_halt_evidence import certify_full_session_halts
from paper_portfolio import PaperPortfolio
from accept_forward_nav import acceptance

SYMBOL = "SZ000001"
CAL = ["2026-10-12", "2026-10-13"]


def _make_fixture(tmp_path):
    signals = tmp_path / "signals"
    signals.mkdir()
    pp = PaperPortfolio(topk=1, nd=0, initial_cash=10000)
    artifacts = []
    for day, signal, op, cl in (("2026-10-12", "2026-10-09", 10, 11),
                                ("2026-10-13", "2026-10-12", 11, 12)):
        pp.plan_signal(signal, day, [(SYMBOL, 1.0)])
        report = pp.execute_pending(day, {SYMBOL: op},
                                    close_prices={SYMBOL: cl}, factors={SYMBOL: 1},
                                    strict_valuation=True)
        artifact = {
            "market": "csi1000",
            "paper_lineage": CANONICAL_LINEAGE,
            "usage_date": day,
            "trading_calendar": {
                "source": "akshare.tool_trade_date_hist_sina",
                "through": day,
                "dates": [d for d in CAL if d <= day],
            },
            "execution_report": report,
        }
        (signals / f"{day}_paper_portfolio.json").write_text(json.dumps(artifact))
        artifacts.append(artifact)
    forward = tmp_path / "forward.json"
    forward.write_text(json.dumps(build_forward_performance(artifacts)))
    cal = tmp_path / "calendar.csv"
    cal.write_text("date\n2026-10-12\n2026-10-13\n")
    quotes = tmp_path / "quotes.csv"
    quotes.write_text(
        "date,instrument,open,close,factor\n"
        f"2026-10-12,{SYMBOL},10,11,1\n"
        f"2026-10-13,{SYMBOL},11,12,1\n"
    )
    return signals, forward, cal, quotes, artifacts


def _audit(src):
    signals, forward, cal, quotes = src[:4]
    return acceptance(signals, forward, calendar_path=cal, quotes_path=quotes,
                      calendar_source="operator-verified exchange calendar",
                      quote_source="independently-adjusted daily OHLC/factors")


def test_readonly_acceptance_passes_realistic_two_session_fixture(tmp_path):
    data = _make_fixture(tmp_path)
    result = _audit(data)
    assert result["status"] == "PASS_ACCOUNTING_CALENDAR_AND_EXTERNAL_QUOTES"
    assert result["audited_days"] == 2
    assert result["compared_quote_fields"] >= 5


def test_independent_close_mismatch_rejects_nav(tmp_path):
    data = _make_fixture(tmp_path)
    quotes = data[3]
    quotes.write_text(quotes.read_text().replace(",11,12,1", ",11,20,1"))
    with pytest.raises(ValueError, match="independent quote mismatch"):
        _audit(data)


def test_independent_calendar_missing_day_fails(tmp_path):
    data = _make_fixture(tmp_path)
    data[2].write_text("date\n2026-10-12\n")
    with pytest.raises(ValueError, match="independent exchange calendar mismatch"):
        _audit(data)


def test_public_json_tamper_detected(tmp_path):
    data = _make_fixture(tmp_path)
    parsed = json.loads(data[1].read_text())
    parsed["cumulative_return"] = 999
    data[1].write_text(json.dumps(parsed))
    with pytest.raises(ValueError, match="published forward JSON disagrees"):
        _audit(data)


def test_sources_absent_do_not_return_false_acceptance(tmp_path):
    data = _make_fixture(tmp_path)
    result = acceptance(data[0], data[1])
    assert result["status"] == "BLOCKED_INDEPENDENT_SOURCES"


def test_no_actual_forward_bar_yields_not_started_not_pass(tmp_path):
    signals = tmp_path / "signals"
    signals.mkdir()
    f = tmp_path / "forward.json"
    f.write_text(json.dumps(build_forward_performance([])))
    assert acceptance(signals, f)["status"] == "NOT_STARTED"


def test_dated_whole_session_halt_needs_explicit_end_and_excludes_intraday():
    valid = {
        "代码": "000001", "停牌时间": "2026-10-13",
        "停牌截止时间": "2026-10-13", "停牌期限": "1天",
        "停牌原因": "公告停牌",
    }
    evidence = certify_full_session_halts(
        "2026-10-13", {SYMBOL}, [valid],
        fetched_utc="2026-10-14T00:00:00+00:00",
    )
    assert evidence[SYMBOL]["scope"] == "FULL_SESSION"
    for changed in ({"停牌截止时间": None}, {"停牌期限": "盘中停牌"},
                    {"停牌时间": "2026-10-14"}):
        with pytest.raises(ValueError, match="missing independent full-session"):
            certify_full_session_halts(
                "2026-10-13", {SYMBOL}, [{**valid, **changed}],
                fetched_utc="2026-10-14T00:00:00+00:00",
            )


def test_halt_carry_provenance_independently_reconciles_and_cannot_be_forged():
    pp = PaperPortfolio(topk=1, nd=0, initial_cash=10000)
    pp.plan_signal("2026-10-09", "2026-10-12", [(SYMBOL, 1)])
    pp.execute_pending("2026-10-12", {SYMBOL: 10}, close_prices={SYMBOL: 11},
                       factors={SYMBOL: 1}, strict_valuation=True)
    pp.plan_signal("2026-10-12", "2026-10-13", [(SYMBOL, 1)])
    dated = {
        "date": "2026-10-13", "scope": "FULL_SESSION",
        "source": "dated external suspension evidence",
        "start_date": "2026-10-13", "end_date": "2026-10-13",
    }
    from paper_accounting import verify_ledger
    report = pp.execute_pending(
        "2026-10-13", {}, close_prices={}, factors={},
        strict_valuation=True, confirmed_halts={SYMBOL},
        halt_evidence={SYMBOL: dated},
    )
    assert report["position_marks"][SYMBOL]["reason"] == "CERTIFIED_HALT_CARRY"
    assert report["opening_marks"][SYMBOL]["reason"] == "CERTIFIED_HALT_CARRY"
    assert verify_ledger(report)
    report["halt_evidence"][SYMBOL]["date"] = "2026-10-12"
    with pytest.raises(ValueError, match="invalid dated halt evidence"):
        verify_ledger(report)
