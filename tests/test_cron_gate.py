"""Unit tests for modal_qlib_cn_a10g._publication_decision (trading-day gate + staleness).

Covers the replacement of the old ">4 natural days" mechanical rule with a
trading-day-lag rule, including the Golden Week reopen case that the old rule
would have silently broken (10-08 with data through 09-30 = 8 natural days).

Watched by .github/workflows/csi1000-tuner-core.yml (push to main).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signal_publication_gate import (
    _csi1000_canonical_publication_decision,
    _publication_decision,
)


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


def test_csi1000_canonical_lag0_required():
    cal = _cal(["2026-10-08", "2026-10-09"])
    action, detail = _csi1000_canonical_publication_decision(
        cal, "2026-10-08", "2026-10-09"
    )
    assert action == "publish"
    assert "lag=0" in detail


def test_csi1000_canonical_lag1_rejected():
    cal = _cal(["2026-10-08", "2026-10-09", "2026-10-12"])
    action, detail = _csi1000_canonical_publication_decision(
        cal, "2026-10-08", "2026-10-12"
    )
    assert action == "raise"
    assert "got lag=1" in detail


def test_csi1000_canonical_calendar_unavailable_rejected():
    action, detail = _csi1000_canonical_publication_decision(
        None, "2026-10-08", "2026-10-09"
    )
    assert action == "raise"
    assert "verified trading calendar" in detail


def test_csi1000_canonical_unknown_data_date_rejected():
    cal = _cal(["2026-10-08", "2026-10-09"])
    action, _ = _csi1000_canonical_publication_decision(
        cal, "2026-10-07", "2026-10-09"
    )
    assert action == "raise"


def test_modal_image_bundles_mvp_contract_runtime_module():
    """Regression: Modal executes from /root, not the source repository root.

    The new daily_standalone and daily_cron gates import this root-level helper;
    the pyqlib wheel installs only the qlib package, not this project module.
    """
    import ast
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "modal_qlib_cn_a10g.py").read_text()
    tree = ast.parse(source)
    copies = [
        arg.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run_commands"
        for arg in node.args
        if isinstance(arg, ast.Constant)
        and isinstance(arg.value, str)
        and arg.value.startswith("cp /root/qlib/")
    ]
    assert any(
        "/root/qlib/csi1000_mvp_contract.py" in command.split()
        and command.rstrip().endswith("/root/")
        for command in copies
    ), "Modal runtime missing csi1000_mvp_contract.py: daily_standalone cannot import gate"


def test_reference_paper_planner_skips_st_fetch_and_marks_uncertainty(tmp_path, monkeypatch):
    """Exercise the actual Modal plan function without loading cloud dependencies."""
    import ast
    import json
    import sys
    import types
    from pathlib import Path

    import csi1000_mvp_contract as contract

    source_file = Path(__file__).resolve().parents[1] / "modal_qlib_cn_a10g.py"
    tree = ast.parse(source_file.read_text())
    node = next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "_apply_paper_portfolio")

    class StubPaperPortfolio:
        def __init__(self, *, topk, nd, initial_cash):
            self.topk, self.nd = topk, nd
            self.cash = initial_cash
            self.positions = {}
            self.history = []
            self.pending_signal = None

        def plan_signal(self, *, signal_date, execution_date, ranking, metadata):
            self.pending_signal = {
                "signal_date": signal_date,
                "execution_date": execution_date,
                "planned_buy": ["SH600000"],
                "planned_sell": [],
                "metadata": metadata,
            }
            return {
                "signal_date": signal_date,
                "execution_date": execution_date,
                "buy": ["SH600000"],
                "sell": [],
            }

        def to_dict(self):
            return {"pending_signal": self.pending_signal}

    stub_module = types.ModuleType("paper_portfolio")
    stub_module.PaperPortfolio = StubPaperPortfolio
    monkeypatch.setitem(sys.modules, "paper_portfolio", stub_module)
    monkeypatch.setattr(contract, "require_reference_preopen",
                        lambda date: date + "T07:01:00+08:00")
    ns = {
        "Path": Path,
        "vol": types.SimpleNamespace(reload=lambda: None),
        "_paper_state_path": lambda market, lineage: tmp_path / "state.json",
        "_current_st_risk_snapshot": lambda *args: (
            (_ for _ in ()).throw(AssertionError("ST feed must never be called"))
        ),
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]),
                 str(source_file), "exec"), ns)
    proposal = ns["_apply_paper_portfolio"](
        {
            "market": "csi1000",
            "date": "2026-10-12",
            "ranking_full": [["SH600000", 0.5]],
            "model_fit_asof": "2026-10-12",
            "train_end": "2026-09-01",
            "valid_end": "2026-09-11",
            "production_lineage": {"profile": "stage_b_winner"},
        },
        "2026-10-13", topk=20, nd=2,
        lineage="stage_b_winner_canonical",
        verified_calendar={"2026-10-12", "2026-10-13"},
    )
    artifact = json.loads(proposal["artifact_json"])
    assert artifact["execution_st_policy"] == contract.REFERENCE_ST_POLICY
    assert artifact["pending_orders"]["execution_date"] == "2026-10-13"
    state = json.loads(proposal["state_json"])
    meta = state["pending_signal"]["metadata"]
    assert meta["execution_st_policy"] == contract.REFERENCE_ST_POLICY
    assert meta["execution_st_snapshot"] is None
    assert meta["reference_plan_created_at_cst"] == "2026-10-13T07:01:00+08:00"
    assert not (tmp_path / "state.json").exists(), "must not persist pre-push"


def test_reference_execution_context_requires_explicit_mode_and_keeps_price_gates():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "modal_qlib_cn_a10g.py").read_text()
    context = src.split("def _paper_execution_context(", 1)[1].split(
        "\ndef _apply_paper_portfolio(", 1
    )[0]
    cron = src.split("def daily_cron():", 1)[1].split(
        "\n# ===================== 第六步", 1
    )[0]
    assert 'execution_st_policy == REFERENCE_ST_POLICY' in context
    assert 'st_symbols = set()' in context
    assert 'snapshot.get("status") != "SOURCE_SNAPSHOT_AVAILABLE"' in context
    assert 'compute_limit_masks(' in context
    assert 'certify_full_session_halts(' in context
    assert '"$open", "Ref($close,1)", "$close", "$factor"' in context
    assert '_current_st_risk_snapshot(res["display_top20"])' not in cron
    assert '"status": "NOT_CHECKED_REFERENCE_ONLY"' in cron
    assert '"top20_st_count": None' in cron
