"""Unit tests for modal_qlib_cn_a10g._publication_decision (trading-day gate + staleness).

Covers the replacement of the old ">4 natural days" mechanical rule with a
trading-day-lag rule, including the Golden Week reopen case that the old rule
would have silently broken (10-08 with data through 09-30 = 8 natural days).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modal_qlib_cn_a10g import _publication_decision


def test_cron_schedule_registered():
    """Guard: the daily_cron decorator must still carry the cron schedule.

    2026-09-29 incident: a refactor edit inserted a helper function before
    @app.function and its oldString included the `schedule=modal.Cron(...)`
    line while the newString omitted it — silently deleting the schedule.
    py_compile passes without a schedule; nothing else would notice, and the
    next morning's cron simply never fires. This test pins the line."""
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "modal_qlib_cn_a10g.py")).read()
    assert 'schedule=modal.Cron("0 7 * * 1-5", timezone="Asia/Shanghai")' in src, \
        "daily_cron schedule line missing — cron would silently never fire!"
    assert "nonpreemptible=True" in src
    assert 'modal.Secret.from_name(_GH_SECRET_NAME := "github-push")' in src


def _cal(dates):
    return set(dates)


def test_normal_tuesday_lag0():
    cal = _cal(["2026-09-21", "2026-09-22", "2026-09-23"])
    action, _ = _publication_decision(cal, "2026-09-22", "2026-09-23")
    assert action == "publish"


def test_monday_after_friday_session_lag0():
    cal = _cal(["2026-09-25", "2026-09-28", "2026-09-29"])
    action, _ = _publication_decision(cal, "2026-09-25", "2026-09-28")
    assert action == "publish"


def test_monday_after_friday_holiday_lag0():
    """2026-09-28 case: Mid-Autumn closed 09-25; data through 09-24 is lag 0."""
    cal = _cal(["2026-09-24", "2026-09-28", "2026-09-29"])
    action, detail = _publication_decision(cal, "2026-09-24", "2026-09-28")
    assert action == "publish"
    assert "lag=0" in detail


def test_golden_week_reopen_lag0():
    """The case the old >4-natural-days rule would have broken: 10-08 reopen,
    data through 09-30 = 8 natural days behind but 0 trading days behind."""
    cal = _cal(["2026-09-30", "2026-10-08", "2026-10-09"])
    action, detail = _publication_decision(cal, "2026-09-30", "2026-10-08")
    assert action == "publish"
    assert "lag=0" in detail


def test_holiday_today_skip():
    """Golden Week itself: no publication on 10-01."""
    cal = _cal(["2026-09-30", "2026-10-08"])  # 真实日历只含交易日，10-01 不在
    action, detail = _publication_decision(cal, "2026-09-30", "2026-10-01")
    assert action == "skip"


def test_one_missed_release_cycle_publish_with_warning():
    """Data one session behind (source missed one release) -> still publish."""
    cal = _cal(["2026-09-18", "2026-09-21", "2026-09-22"])
    action, detail = _publication_decision(cal, "2026-09-18", "2026-09-22")
    assert action == "publish"
    assert "滞后 1 个交易日" in detail


def test_two_missed_cycles_raise():
    """Source likely dead: two full sessions missing -> raise (Modal alert)."""
    cal = _cal(["2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18"])
    action, _ = _publication_decision(cal, "2026-09-15", "2026-09-18")
    assert action == "raise"


def test_data_date_not_in_calendar_fail_open():
    cal = _cal(["2026-09-28", "2026-09-29"])
    action, detail = _publication_decision(cal, "2026-09-26", "2026-09-28")
    assert action == "publish"
    assert "口径差异" in detail


def test_calendar_none_short_gap_fail_open():
    """Degraded mode (calendar unavailable): short gap still publishes."""
    action, detail = _publication_decision(None, "2026-09-24", "2026-09-28", fallback_days=4)
    assert action == "publish"
    assert "fail-open" in detail


def test_calendar_none_extreme_gap_raise():
    """Degraded mode: >12 natural days is beyond even Spring-Festival-level gaps."""
    action, _ = _publication_decision(None, "2026-09-10", "2026-09-28", fallback_days=18)
    assert action == "raise"


def test_calendar_none_spring_festival_level_gap_publish():
    """Degraded mode: worst legitimate gap (~10 natural days) must NOT raise."""
    action, _ = _publication_decision(None, "2026-02-13", "2026-02-23", fallback_days=10)
    assert action == "publish"


def test_same_day_data_rejected():
    cal = _cal(["2026-10-08", "2026-10-09"])
    action, detail = _publication_decision(cal, "2026-10-08", "2026-10-08")
    assert action == "raise"
    assert "必须早于" in detail


def test_future_data_rejected():
    cal = _cal(["2026-10-08", "2026-10-09"])
    action, _ = _publication_decision(cal, "2026-10-09", "2026-10-08")
    assert action == "raise"
