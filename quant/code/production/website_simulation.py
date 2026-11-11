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
FIT_RECEIPT_VERSION = 2
ACCOUNT_CONTRACT_VERSION = 1
MODE = "CAUSAL_SIMULATION"
ST_POLICY = "ST_HISTORY_UNVERIFIED_REFERENCE_ONLY"


def account_contract_document():
    """Describe every business input that can change website account results.

    Values come from the executable defaults/constants rather than a copied
    table, so changing a fee or TopK changes the account identity.
    """
    from inspect import signature

    from order_quantity import EXECUTION_RULE_VERSION
    from paper_portfolio import PaperPortfolio
    from qlib_live_retrain import RETRAIN_EVERY_SESSIONS

    defaults = {
        name: parameter.default
        for name, parameter in signature(PaperPortfolio.__init__).parameters.items()
        if name not in ("self", "topk", "nd", "initial_cash")
    }
    markets = {}
    for market in sorted(CANONICAL_PROFILES_BY_MARKET):
        profile = CANONICAL_PROFILES_BY_MARKET[market]
        manifest = profile_manifest(profile)
        markets[market] = {
            "profile": profile,
            "horizon": manifest["label_horizon"],
            "release_id": manifest.get("release_id"),
            "release_status": manifest.get("release_status"),
            "release_manifest_sha256": manifest.get("release_manifest_sha256"),
            "strategy_contract_sha256": manifest.get("strategy_contract_sha256"),
            "topk": CANONICAL_TOPK_BY_MARKET[market],
            "dropn": CANONICAL_ND_BY_MARKET[market],
            "initial_cash": INITIAL_CASH,
            "start_date": FIRST_EXECUTION,
            "retrain_every_sessions": RETRAIN_EVERY_SESSIONS,
            "execution_rule_version": EXECUTION_RULE_VERSION,
            "fees": {
                "open_cost": defaults["open_cost"],
                "close_cost": defaults["close_cost"],
                "min_cost": defaults["min_cost"],
            },
            "risk_degree": defaults["risk_degree"],
            "hold_thresh": defaults["hold_thresh"],
            "trade_unit": defaults["trade_unit"],
            "high_open_block": defaults["high_open_block"],
        }
    return {"schema_version": ACCOUNT_CONTRACT_VERSION, "markets": markets}


def account_contract_for_market(market):
    if market not in CANONICAL_PROFILES_BY_MARKET:
        raise ValueError(f"unknown website market: {market!r}")
    return {"market": market, **account_contract_document()["markets"][market]}


def training_prefix_fingerprint(provider, market, cutoff):
    """Cover every provider feature, including Alpha158's VWAP inputs."""
    from qlib_live_retrain import provider_training_fingerprint

    fields = sorted({p.name[: -len(".day.bin")] for p in (Path(provider) / "features").glob("*/*.day.bin")})
    if not fields:
        raise ValueError("missing website training features")
    return provider_training_fingerprint(provider, market, cutoff, fields=fields)


def boundary(cash, positions):
    return {"cash": float(cash), "shares": {s: float(p["shares"]) for s, p in positions.items()}}


def fit_bindings(fits, *, profile, horizon, dates):
    """Bind each execution to the unique model that scored its prior session."""
    from datetime import date

    if (
        not dates
        or not isinstance(fits, list)
        or not fits
        or not isinstance(fits[0], dict)
        or fits[0].get("fit_asof") != FIRST_SIGNAL
    ):
        raise ValueError("missing causal website fit receipts")
    previous_end = None
    for fit in fits:
        if not isinstance(fit, dict):
            raise ValueError("invalid website fit receipt")
        for key in ("model_sha256", "data_prefix_sha256"):
            digest = fit.get(key)
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("website fit model/data SHA256 missing")
        contract = fit.get("training_contract") or {}
        if not isinstance(contract, dict):
            raise ValueError("invalid website training contract")
        for value in (fit.get("fit_asof"), fit.get("signal_end"), contract.get("train_end"), contract.get("valid_end")):
            if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
                raise ValueError("invalid website fit date")
        if (
            fit.get("profile") != profile
            or contract.get("asof") != fit["fit_asof"]
            or contract.get("horizon") != horizon
            or not contract["train_end"] < contract["valid_end"] < fit["fit_asof"] <= fit["signal_end"] <= dates[-1]
            or (previous_end is not None and fit["fit_asof"] <= previous_end)
        ):
            raise ValueError("non-causal or overlapping website fit receipt")
        previous_end = fit["signal_end"]
    bindings = []
    for signal_day in [FIRST_SIGNAL, *dates[:-1]]:
        matches = [fit for fit in fits if fit["fit_asof"] <= signal_day <= fit["signal_end"]]
        if len(matches) != 1:
            raise ValueError("website point has no unique fit coverage")
        bindings.append((signal_day, matches[0]))
    return bindings


def build_website_series(market, result, *, calendar, source_sha, semantic_sha, account_sha, provider_sha, fits):
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
        "official_reference_price_verified": False,
        "return_interpretation": "CAUSAL_HISTORICAL_SIMULATION_REFERENCE_ONLY",
        "source_manifest_sha256": source_sha,
        "release_source_sha256": source_sha,
        "website_semantic_sha256": semantic_sha,
        "account_contract_sha256": account_sha,
        "identity_schema_version": 2,
        "provider_archive_sha256": provider_sha,
        "fit_receipts": fits,
        "fit_receipt_version": FIT_RECEIPT_VERSION,
        "total_fees": sum(p["cost"] for p in points),
        "broker_fills_certified": False,
        "live_trading_approved": False,
    }
    for point, (signal_day, fit) in zip(
        points, fit_bindings(fits, profile=profile, horizon=identity["horizon"], dates=payload["trading_dates"])
    ):
        point.update(signal_date=signal_day, fit_asof=fit["fit_asof"], model_sha256=fit["model_sha256"])
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
    identity_schema = raw.get("identity_schema_version", 1)
    required_identity = ["source_manifest_sha256", "provider_archive_sha256"]
    if identity_schema == 2:
        required_identity += [
            "release_source_sha256",
            "website_semantic_sha256",
            "account_contract_sha256",
        ]
    elif identity_schema != 1:
        raise ValueError("website identity schema is unsupported")
    for key in required_identity:
        digest = raw.get(key)
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("website source/data identity missing")
    if identity_schema == 2 and raw["release_source_sha256"] != raw["source_manifest_sha256"]:
        raise ValueError("website release identity aliases disagree")
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
    revision = raw.get("fit_receipt_version", 1)
    if type(revision) is not int or revision not in (1, FIT_RECEIPT_VERSION):
        raise ValueError("unknown website fit receipt version")
    bindings = fit_bindings(raw.get("fit_receipts"), profile=profile, horizon=expected_identity["horizon"], dates=dates)
    if revision == FIT_RECEIPT_VERSION:
        for point, (signal_day, fit) in zip(points, bindings):
            if (
                point.get("signal_date") != signal_day
                or point.get("fit_asof") != fit["fit_asof"]
                or point.get("model_sha256") != fit["model_sha256"]
            ):
                raise ValueError("website point/model fit binding mismatch")
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
