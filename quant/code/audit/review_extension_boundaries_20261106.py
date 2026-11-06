"""Offline review probes; print observations, without changing production state.

These are diagnostic examples, not regression tests that bless current behavior.
Run from any directory with the repository's Python environment.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
for relative in (
    "production",
    "execution",
    "ops",
    "research/stage_ab",
    "research/csi300_csi500",
    "research/regime_reselection",
):
    sys.path.insert(0, str(CODE / relative))

import pandas as pd

from execution_limits import compute_limit_masks
from merge_forward_performance import merge_forward_performance
from paper_accounting import verify_ledger
from paper_portfolio import PaperPortfolio
from portfolio_performance import portfolio_performance
from run_g1_overlays import apply_drawdown_breaker, apply_trend_gate, combine_sleeves
from stage0_p0_core import apply_cost_stress


def observations():
    result = {}
    idx = pd.date_range("2026-01-01", periods=3)
    report = pd.DataFrame(
        {
            "return": [0.0, -0.5, 1.0],
            "cost": [0.0, 0.0, 0.0],
            "bench": [0.0, float("nan"), 0.0],
            "account": [100.0, 50.0, 100.0],
        },
        index=idx,
    )
    try:
        metrics = portfolio_performance(report, initial_cash=100)
        result["Q01"] = {"actual_drawdown": metrics["strategy_max_drawdown"], "rejected_missing_session": False}
    except ValueError as exc:
        result["Q01"] = {"rejected_missing_session": True, "reason": str(exc)}

    pp = PaperPortfolio(topk=1, nd=1, initial_cash=10000)
    pp.plan_signal("2026-11-02", "2026-11-03", [("SZ000001", 1.0)])
    ledger = pp.execute_pending(
        "2026-11-03", {"SZ000001": 10}, close_prices={"SZ000001": 10}, factors={"SZ000001": 1}, strict_valuation=True
    )
    accepted = []
    for field in ("cash", "opening_cash", "cost", "portfolio_value_pre_trade_open", "portfolio_value_close"):
        altered = copy.deepcopy(ledger)
        altered[field] = float("nan")
        try:
            if verify_ledger(altered):
                accepted.append(field)
        except ValueError:
            pass
    result["Q02"] = {"NaN_fields_accepted": accepted, "expected": []}

    buy, sell = compute_limit_masks(["SZ000001"], ["2026-11-03"], [11.0], [float("nan")], [False], factors=[1.0])
    result["Q03"] = {
        "buy_blocked": bool(buy[0]),
        "sell_blocked": bool(sell[0]),
        "expected": "reject unknown reference or block both",
    }
    _, sell = compute_limit_masks(["SZ000001"], ["2026-11-03"], [1.04], [1.15], [False], factors=[1.0])
    result["Q04"] = {
        "decimal_reference": "1.15 * 0.90 = 1.035 -> 1.04",
        "sell_blocked": bool(sell[0]),
        "expected_sell_blocked": True,
    }

    pp = PaperPortfolio(topk=1, nd=1, initial_cash=20000)
    pp.plan_signal("2026-11-02", "2026-11-03", [("SH688001", 1.0)])
    ledger = pp.execute_pending(
        "2026-11-03", {"SH688001": 150}, close_prices={"SH688001": 150}, factors={"SH688001": 1}, strict_valuation=True
    )
    result["Q05"] = {
        "physical_buy_shares": [f["physical_shares"] for f in ledger["executed_buy"]],
        "expected": "no fill: insufficient budget for minimum 200 shares",
    }

    prefix = {
        "initial_cash": 1e6,
        "points": [{"date": "2026-11-02", "nav": 0.9, "daily_return": -0.1, "cumulative_return": -0.1}],
    }
    live = {
        "status": "active",
        "execution_st_policy": "ST_IGNORED_REFERENCE_ONLY",
        "st_fallback_dates": ["2026-11-03"],
        "points": [
            {"date": "2026-11-03", "portfolio_value_close": 1e6, "execution_st_policy": "ST_IGNORED_REFERENCE_ONLY"}
        ],
    }
    try:
        merge_forward_performance(prefix, live)
        result["Q06"] = {"unproven_reset_accepted": True}
    except ValueError as exc:
        result["Q06"] = {"unproven_reset_accepted": False, "reason": str(exc)}
    from account_boundary import account_identity

    prefix["account_identity"] = live["account_identity"] = account_identity("csi1000")
    state = {"cash": 0.0, "shares": {"SZ000001": 90000.0}}
    prefix["closing_state"] = state
    live["trading_dates"] = ["2026-11-02", "2026-11-03"]
    live["points"][0].update(opening_state=state, closing_state=state)
    merged = merge_forward_performance(prefix, live)
    result["Q07"] = {
        "aggregate_policy": merged.get("return_interpretation"),
        "fallback_dates": merged.get("st_fallback_dates"),
        "point_policy": merged["points"][-1].get("execution_st_policy"),
    }

    idx = pd.bdate_range("2020-01-01", periods=400)
    close = pd.Series([500 - i for i in range(400)], index=idx)
    base = pd.DataFrame({"return": 0.0, "cost": 0.0, "bench": 0.0, "turnover": 0.0}, index=idx[300:])
    _, weight = apply_trend_gate(base, close)
    result["Q08"] = {
        "actual_min_weight": float(weight.min()),
        "actual_max_weight": float(weight.max()),
        "expected": "0.3 exposure with complete falling prehistory",
    }
    base = pd.DataFrame(
        {"return": [-0.2] + [0.1] * 60, "cost": 0.0, "bench": 0.0, "turnover": 0.0},
        index=pd.bdate_range("2026-01-01", periods=61),
    )
    _, weight = apply_drawdown_breaker(base)
    result["Q09"] = {
        "first_weights": weight.iloc[:3].tolist(),
        "last_weights": weight.iloc[-5:].tolist(),
        "expected": "recovery must be reachable under an explicitly defined recovery signal",
    }

    idx = pd.date_range("2026-01-01", periods=2)
    a = pd.DataFrame({"return": [0.0, 0.1], "cost": [0.1, 0.0], "bench": [0.0, 0.0], "turnover": [0.0, 0.0]}, index=idx)
    b = pd.DataFrame({"return": [0.0, 0.0], "cost": [0.0, 0.0], "bench": [0.0, 0.0], "turnover": [0.0, 0.0]}, index=idx)
    combined = combine_sleeves({"a": a, "b": b}, "equal", 20, 0)
    result["Q10"] = {
        "actual_final": float(combined["account"].iloc[-1]),
        "expected_final": 99_500_000.0,
        "derivation": "50M * 0.9 * 1.1 + 50M",
    }
    base = pd.DataFrame(
        {
            "return": [0.0, 0.1],
            "cost": [0.0, 0.0],
            "bench": [0.0, 0.0],
            "account": [100.0, 110.0],
            "cash": [100.0, 110.0],
            "total_cost": [0.0, 0.0],
            "turnover": [1.0, 0.0],
        },
        index=idx,
    )
    stress = apply_cost_stress(base, 100, initial_cash=100)
    result["Q11"] = {
        "stressed_nav": stress["account"].tolist(),
        "reported_second_net_return": float((stress["return"] - stress["cost"]).iloc[1]),
        "actual_second_net_return": float(stress["account"].iloc[1] / stress["account"].iloc[0] - 1),
    }
    return result


if __name__ == "__main__":
    print(json.dumps(observations(), ensure_ascii=False, indent=2, allow_nan=False))
