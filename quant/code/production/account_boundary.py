"""Explicit identity and cash/inventory continuity for published NAV segments."""

from __future__ import annotations

import math
from csi1000_production_config import (
    CANONICAL_PAPER_LINEAGES_BY_MARKET,
    CANONICAL_PROFILES_BY_MARKET,
    CANONICAL_TOPK_BY_MARKET,
    CANONICAL_ND_BY_MARKET,
    PAPER_INITIAL_CASH,
    profile_manifest,
)


def account_identity(market):
    profile = CANONICAL_PROFILES_BY_MARKET[market]
    return {
        "market": market,
        "paper_lineage": CANONICAL_PAPER_LINEAGES_BY_MARKET[market],
        "profile": profile,
        "initial_cash": PAPER_INITIAL_CASH,
        "horizon": profile_manifest(profile)["label_horizon"],
        "topk": CANONICAL_TOPK_BY_MARKET[market],
        "nd": CANONICAL_ND_BY_MARKET[market],
    }


def validate_state(state):
    if not isinstance(state, dict) or not isinstance(state.get("shares"), dict):
        raise ValueError("missing cash/inventory boundary state")
    values = [float(state["cash"]), *[float(x) for x in state["shares"].values()]]
    if not all(math.isfinite(x) for x in values) or values[0] < -0.01 or any(x <= 0 for x in values[1:]):
        raise ValueError("invalid cash/inventory boundary state")
    return state


def reconcile_states(previous, opening):
    validate_state(previous)
    validate_state(opening)
    if (
        abs(float(previous["cash"]) - float(opening["cash"])) > 0.011
        or set(previous["shares"]) != set(opening["shares"])
        or any(
            not math.isclose(float(previous["shares"][s]), float(opening["shares"][s]), rel_tol=1e-9, abs_tol=1e-7)
            for s in previous["shares"]
        )
    ):
        raise ValueError("account cash/inventory discontinuity at segment boundary")
