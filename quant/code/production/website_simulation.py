"""Causal, independently versioned website accounts from the October 8 open.

Historical ST and auction liquidity are unverified. These accounts are
disclosed simulations, separate from the immutable scheduled paper ledger.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

from csi1000_production_config import (
    CANONICAL_ND_BY_MARKET,
    CANONICAL_PROFILES_BY_MARKET,
    CANONICAL_TOPK_BY_MARKET,
    profile_manifest,
)
from paper_accounting import verify_ledger
from account_boundary import reconcile_states

FIRST_EXECUTION = "2026-10-08"
FIRST_SIGNAL = "2026-09-30"
INITIAL_CASH = 1_000_000
MODE = "CAUSAL_SIMULATION"
ST_POLICY = "ST_HISTORY_UNVERIFIED_REFERENCE_ONLY"


def training_prefix_fingerprint(provider, market, cutoff):
    """Cover every provider feature, including Alpha158's VWAP inputs."""
    from qlib_live_retrain import provider_training_fingerprint

    fields = sorted({p.name[: -len(".day.bin")] for p in (Path(provider) / "features").glob("*/*.day.bin")})
    if not fields:
        raise ValueError("missing website training features")
    return provider_training_fingerprint(provider, market, cutoff, fields=fields)


def boundary(cash, positions):
    return {"cash": float(cash), "shares": {s: float(p["shares"]) for s, p in positions.items()}}


def build_website_series(market, result, *, calendar, source_sha, provider_sha, fits):
    """Derive close-to-initial-capital returns from verified session ledgers."""
    if result["gaps"]:
        raise ValueError("website simulation cannot publish missing held quotes")
    if not result["sessions"]:
        raise ValueError("website simulation needs completed sessions")
    profile = CANONICAL_PROFILES_BY_MARKET[market]
    identity = {
        "market": market,
        "profile": profile,
        "paper_lineage": f"{profile}_website_oct08_v2",
        "initial_cash": INITIAL_CASH,
        "topk": CANONICAL_TOPK_BY_MARKET[market],
        "nd": CANONICAL_ND_BY_MARKET[market],
        "horizon": profile_manifest(profile)["label_horizon"],
    }
    points, previous, previous_nav = [], boundary(INITIAL_CASH, {}), float(INITIAL_CASH)
    for report in result["sessions"]:
        verify_ledger(report)
        opening = boundary(report["opening_cash"], report["opening_positions"])
        reconcile_states(previous, opening)
        closing = boundary(report["cash"], report["position_marks"])
        nav = float(report["portfolio_value_close"])
        if not math.isfinite(nav) or nav <= 0:
            raise ValueError("invalid website NAV")
        points.append(
            {
                "date": report["date"],
                "nav": nav / INITIAL_CASH,
                "daily_return": nav / previous_nav - 1,
                "cumulative_return": nav / INITIAL_CASH - 1,
                "portfolio_value_close": nav,
                "cash": float(report["cash"]),
                "cost": float(report["cost"]),
                "data_mode": MODE,
                "opening_state": opening,
                "closing_state": closing,
            }
        )
        previous, previous_nav = closing, nav
    payload = {
        "schema_version": 3,
        "status": "active",
        "data_mode": MODE,
        "start_date": FIRST_EXECUTION,
        "inception_date": FIRST_EXECUTION,
        "initial_cash": INITIAL_CASH,
        "initial_nav": 1.0,
        "market": market,
        "account_identity": identity,
        "latest_date": points[-1]["date"] if points else None,
        "cumulative_return": points[-1]["cumulative_return"] if points else None,
        "points": points,
        "trading_dates": [d for d in calendar if FIRST_EXECUTION <= d <= points[-1]["date"]],
        "execution_st_policy": ST_POLICY,
        "auction_liquidity_verified": False,
        "return_interpretation": "CAUSAL_HISTORICAL_SIMULATION_REFERENCE_ONLY",
        "source_manifest_sha256": source_sha,
        "provider_archive_sha256": provider_sha,
        "fit_receipts": fits,
        "total_fees": sum(p["cost"] for p in points),
        "broker_fills_certified": False,
        "live_trading_approved": False,
    }
    validate_website_series(payload, market=market)
    return payload


def validate_website_series(raw, *, market=None):
    """Fail closed on identity, omitted sessions, capital or account discontinuity."""
    market = market or raw.get("market")
    if (
        market not in CANONICAL_PROFILES_BY_MARKET
        or raw.get("market") != market
        or raw.get("schema_version") != 3
        or raw.get("data_mode") != MODE
        or raw.get("status") != "active"
        or raw.get("start_date") != FIRST_EXECUTION
        or raw.get("inception_date") != FIRST_EXECUTION
        or raw.get("initial_cash") != INITIAL_CASH
        or raw.get("initial_nav") != 1.0
        or raw.get("execution_st_policy") != ST_POLICY
    ):
        raise ValueError("invalid website simulation identity")
    profile = CANONICAL_PROFILES_BY_MARKET[market]
    expected_identity = {
        "market": market,
        "profile": profile,
        "paper_lineage": f"{profile}_website_oct08_v2",
        "initial_cash": INITIAL_CASH,
        "topk": CANONICAL_TOPK_BY_MARKET[market],
        "nd": CANONICAL_ND_BY_MARKET[market],
        "horizon": profile_manifest(profile)["label_horizon"],
    }
    if raw.get("account_identity") != expected_identity:
        raise ValueError("website profile/account mismatch")
    for key in ("source_manifest_sha256", "provider_archive_sha256"):
        digest = raw.get(key)
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("website source/data identity missing")
    points, dates = raw.get("points"), raw.get("trading_dates")
    if (
        not isinstance(points, list)
        or not points
        or not isinstance(dates, list)
        or not dates
        or dates != sorted(set(dates))
        or dates[0] != FIRST_EXECUTION
        or dates != [p["date"] for p in points]
        or raw.get("latest_date") != dates[-1]
    ):
        raise ValueError("website trading session coverage mismatch")
    previous, previous_nav, fees = boundary(INITIAL_CASH, {}), 1.0, 0.0
    for p in points:
        from datetime import date

        date.fromisoformat(p["date"])
        reconcile_states(previous, p.get("opening_state"))
        for key in ("nav", "daily_return", "cumulative_return", "portfolio_value_close", "cost", "cash"):
            value = p.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("non-finite website point")
        if (
            p["nav"] <= 0
            or p["cost"] < 0
            or p["cash"] < -0.01
            or p.get("data_mode") != MODE
            or abs(p["portfolio_value_close"] / INITIAL_CASH - p["nav"]) > 1e-10
            or abs(p["nav"] - 1 - p["cumulative_return"]) > 1e-10
            or abs(p["nav"] / previous_nav - 1 - p["daily_return"]) > 1e-10
            or abs(p["cash"] - p["closing_state"]["cash"]) > 0.011
        ):
            raise ValueError("inconsistent website NAV/return")
        previous, previous_nav = p["closing_state"], p["nav"]
        fees += p["cost"]
    if abs(raw["cumulative_return"] - points[-1]["cumulative_return"]) > 1e-12 or abs(raw["total_fees"] - fees) > 0.011:
        raise ValueError("website cumulative return/fee mismatch")
    fits = raw.get("fit_receipts")
    if not isinstance(fits, list) or not fits or fits[0].get("fit_asof") != FIRST_SIGNAL:
        raise ValueError("missing causal website fit receipts")
    for fit in fits:
        contract = fit.get("training_contract") or {}
        if (
            fit.get("profile") != profile
            or contract.get("asof") != fit.get("fit_asof")
            or contract.get("horizon") != expected_identity["horizon"]
            or not contract.get("train_end")
            or not contract.get("valid_end")
            or not contract["train_end"] < contract["valid_end"] < fit["fit_asof"]
            or fit["signal_end"] < fit["fit_asof"]
        ):
            raise ValueError("non-causal website fit receipt")
    return raw


def write_immutable(path, content):
    """Permit exact retries, reject rewriting a frozen snapshot."""
    path = Path(path)
    payload = content if isinstance(content, bytes) else content.encode()
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError("immutable website snapshot conflict")
        return hashlib.sha256(payload).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)
    return hashlib.sha256(payload).hexdigest()
