"""G2 v2 gate helpers for the CSI1000 candidate.

The v2 gates are an explicit, post-G2 risk-appetite change.  They are kept
pure and testable so the report and the evaluation cannot drift apart.
"""

from __future__ import annotations


def d2_v2(
    candidate_2018_mdd: float,
    baseline_2018_mdd: float,
    candidate_full_mdd: float,
    baseline_full_mdd: float,
    *,
    mdd_tolerance: float = 0.02,
    full_improvement: float = 0.05,
) -> bool:
    """2018 non-inferiority plus a full-period drawdown compensation.

    candidate_2018_mdd may be at most ``mdd_tolerance`` deeper than the
    baseline, and the full-period MDD must be at least ``full_improvement``
    better (more shallow) than the baseline.
    """
    if mdd_tolerance < 0 or full_improvement < 0:
        raise ValueError("tolerances must be non-negative")
    return (
        candidate_2018_mdd >= baseline_2018_mdd - mdd_tolerance
        and candidate_full_mdd >= baseline_full_mdd + full_improvement
    )


def d7_v2(
    candidate_calmar10: float,
    baseline_calmar: float,
    candidate_sharpe10: float,
    baseline_sharpe: float,
    candidate_cagr20: float,
    *,
    sharpe_tolerance: float = 0.02,
) -> bool:
    """Cost-stress gate with a small Sharpe tolerance and +20bp requirement."""
    if sharpe_tolerance < 0:
        raise ValueError("sharpe_tolerance must be non-negative")
    return (
        candidate_calmar10 >= baseline_calmar
        and candidate_sharpe10 >= baseline_sharpe - sharpe_tolerance
        and candidate_cagr20 > 0.0
    )


def evaluate_s02_v2(base: dict, candidate: dict, cost: dict) -> dict:
    """Evaluate S02 against the frozen S01 baseline using the v2 gates.

    ``base``/``candidate`` are the G2 summary rows; ``cost`` is the actual-cost
    replay row for S02 at +10bp/+20bp.
    """
    d2 = d2_v2(
        candidate["2018_mdd"],
        base["2018_mdd"],
        candidate["mdd"],
        base["mdd"],
    )
    d7 = d7_v2(
        cost["calmar"],
        base["cagr"] / abs(base["mdd"]),
        cost["sharpe"],
        base["sharpe"],
        cost["cagr20"],
    )
    return {
        "d2_v2": bool(d2),
        "d7_v2": bool(d7),
        "d1": bool(candidate["d1"]),
        "d3": bool(candidate["d3"]),
        "d2_margin_pp": round((candidate["2018_mdd"] - (base["2018_mdd"] - 0.02)) * 100, 4),
        "d7_calmar_margin": round(cost["calmar"] - base["cagr"] / abs(base["mdd"]), 6),
        "d7_sharpe_margin": round(cost["sharpe"] - (base["sharpe"] - 0.02), 6),
        "d7_cagr20": round(cost["cagr20"], 6),
    }
