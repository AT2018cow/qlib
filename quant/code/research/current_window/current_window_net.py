"""Canonical after-cost performance for current-window and bear-block research."""

from __future__ import annotations

import pandas as pd

from finite_values import finite_number, validate_dates
from portfolio_performance import portfolio_performance

METRIC_SCHEMA_VERSION = "current_window_net_v1"
CORRECTION_UNIT_REGISTRY_VERSION = "current_window_20261016_20261029_v1"
EXPECTED_CORRECTION_UNITS = (
    ("csi1000", "baseline_control", 20, 2),
    ("csi1000", "baseline_control", 10, 1),
    ("csi1000", "c98856b4_top10_drop1", 10, 1),
    ("csi1000", "c98856b4_top10_drop1", 20, 2),
    ("csi1000", "stage_b_winner", 20, 2),
    ("csi300", "59b93929_top10_drop1", 10, 1),
    ("csi300", "59b93929_top10_drop1", 20, 2),
    ("csi300", "baseline_control", 20, 2),
    ("csi300", "baseline_control", 10, 1),
    ("csi500", "baseline_control", 20, 2),
    ("csi500", "baseline_control", 10, 1),
    ("csi500", "raw10d_c98856b4_top10_drop1", 10, 1),
    ("csi500", "raw10d_c98856b4_top10_drop1", 20, 2),
)
BEAR_IDENTITY_FIELDS = (
    "market",
    "profile",
    "provider_fingerprint",
    "calendar_sha256",
    "strategy_contract_sha256",
)


def _lower_sha(value, label):
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{label} must be a lowercase SHA256")
    return value


def validate_bear_identity(identity):
    if not isinstance(identity, dict) or set(identity) != set(BEAR_IDENTITY_FIELDS):
        raise ValueError("bear block identity fields differ from the canonical schema")
    for field in ("market", "profile"):
        if not isinstance(identity.get(field), str) or not identity[field]:
            raise ValueError(f"bear block identity is missing {field}")
    for field in ("provider_fingerprint", "calendar_sha256", "strategy_contract_sha256"):
        _lower_sha(identity.get(field), field)
    return dict(identity)


def bear_research_identity(
    *,
    market,
    profile,
    candidate_id,
    model_config_sha256,
    label_horizon,
    topk,
    dropn,
    provider_fingerprint,
    calendar_sha256,
    account_start,
):
    """Build the shared block identity from the executable research contract."""
    from strategy_release import CANONICAL_CONTRACT_VERSION, strategy_contract_sha256

    strategy_sha = strategy_contract_sha256(
        {
            "contract_version": CANONICAL_CONTRACT_VERSION,
            "market": market,
            "profile": profile,
            "candidate_id": candidate_id,
            "model_config_sha256": model_config_sha256,
            "label_expression": f"Ref($close, -{int(label_horizon)})/$close - 1",
            "label_horizon": int(label_horizon),
            "pit_universe": market,
            "topk": int(topk),
            "dropn": int(dropn),
            "retrain_every_sessions": 20,
            "execution_timing": "T_CLOSE_TO_T1_OPEN",
            "board_limits": "cn_equity_board_date_aware_v2",
            "listing_exemptions": "board_listing_date_rules",
            "lot_rule": "cn_equity_board_lot_v1",
            "liquidity_policy": "missing_quote_fail_closed",
            "fees": {"open_cost": 0.0005, "close_cost": 0.0015, "min_cost": 5.0},
            "initial_cash": 100_000_000,
            "account_start": account_start,
        }
    )
    return validate_bear_identity(
        {
            "market": market,
            "profile": profile,
            "provider_fingerprint": provider_fingerprint,
            "calendar_sha256": calendar_sha256,
            "strategy_contract_sha256": strategy_sha,
        }
    )


def performance_from_report(report: pd.DataFrame, *, initial_cash: float = 100_000_000, backtest_start=None):
    """Return gross/cost/net/account metrics from one Qlib report.

    Qlib's first row is the signal/anchor bar.  It is removed from reported
    daily returns while the account path remains anchored at ``initial_cash``.
    """
    initial_cash = finite_number(initial_cash, "initial_cash", positive=True)
    if not isinstance(report, pd.DataFrame):
        raise ValueError("report must be a pandas DataFrame")
    required = {"return", "cost", "bench", "account"}
    missing = required - set(report.columns)
    if missing:
        raise ValueError(f"report missing columns: {sorted(missing)}")
    rep = report.sort_index()
    if rep.index.has_duplicates:
        raise ValueError("report index contains duplicate dates")
    if len(rep) < 2:
        raise ValueError("report needs an anchor bar and at least one traded session")
    traded = rep.iloc[1:]
    gross = [finite_number(value, "daily gross return") for value in traded["return"].tolist()]
    cost = [finite_number(value, "daily cost", nonnegative=True) for value in traded["cost"].tolist()]
    bench = [finite_number(value, "daily benchmark return") for value in traded["bench"].tolist()]
    net = [g - c for g, c in zip(gross, cost)]
    for value in net:
        finite_number(value, "daily net return")
    perf = portfolio_performance(traded, initial_cash=initial_cash, backtest_start=backtest_start)
    final_account = finite_number(traded["account"].iloc[-1], "final_account", positive=True)
    return {
        "metric_schema_version": METRIC_SCHEMA_VERSION,
        "dates": [str(pd.Timestamp(day).date()) for day in traded.index],
        "daily_gross_return": [round(value, 8) for value in gross],
        "daily_cost": [round(value, 8) for value in cost],
        "daily_net_return": [round(value, 8) for value in net],
        "daily_benchmark": [round(value, 8) for value in bench],
        "daily_account": [finite_number(value, "daily account", positive=True) for value in traded["account"].tolist()],
        # Compatibility field is explicitly the net return.
        "daily_return": [round(value, 8) for value in net],
        "cumulative_return": perf["strategy_total_return"],
        "strategy_total_return_net": perf["strategy_total_return"],
        "benchmark_cumulative_return": perf["benchmark_total_return"],
        "excess_cumulative_return": perf["relative_total_return"],
        "total_cost": round(sum(cost), 8),
        "final_account": final_account,
        "account_return_max_error": perf["account_return_max_error"],
        "strategy_max_drawdown": perf["strategy_max_drawdown"],
        "benchmark_max_drawdown": perf["benchmark_max_drawdown"],
        "metric_version": perf["metric_version"],
        "validation_version": perf["validation_version"],
    }


def bear_block_payload(
    report: pd.DataFrame,
    *,
    identity,
    expected_dates,
    retrain_asof,
    eval_end,
    initial_cash: float = 100_000_000,
    backtest_start=None,
):
    payload = performance_from_report(report, initial_cash=initial_cash, backtest_start=backtest_start)
    validate_dates(payload["dates"], "bear block report dates", expected=expected_dates)
    payload["initial_cash"] = finite_number(initial_cash, "initial_cash", positive=True)
    payload["identity"] = validate_bear_identity(identity)
    payload["retrain_asof"] = retrain_asof
    payload["eval_end"] = eval_end
    if payload["dates"][-1] != eval_end or not isinstance(retrain_asof, str) or retrain_asof >= payload["dates"][0]:
        raise ValueError("bear block window does not match its traded dates")
    payload["verified_net"] = True
    return payload


def combine_bear_blocks(
    blocks,
    *,
    expected_dates,
    expected_identity,
    initial_cash: float = 100_000_000,
    tolerance: float = 1e-6,
):
    """Concatenate verified net-return blocks without gaps or duplicates."""
    if not isinstance(blocks, (list, tuple)) or not blocks:
        raise ValueError("bear blocks are missing")
    expected_identity = validate_bear_identity(expected_identity)
    expected_dates = validate_dates(expected_dates, "expected bear dates")
    dates, gross, cost, net, bench = [], [], [], [], []
    for block in blocks:
        if not isinstance(block, dict) or block.get("verified_net") is not True:
            raise ValueError("bear block is not verified net-return evidence")
        block_dates = validate_dates(block.get("dates"), "bear block dates")
        if validate_bear_identity(block.get("identity")) != expected_identity:
            raise ValueError("bear block identity differs from the coordinator identity")
        retrain_asof = block.get("retrain_asof")
        eval_end = block.get("eval_end")
        if not isinstance(retrain_asof, str) or retrain_asof >= block_dates[0] or eval_end != block_dates[-1]:
            raise ValueError("bear block window does not match its dates")
        if dates and retrain_asof != dates[-1]:
            raise ValueError("bear block retrain boundary is not contiguous")
        if dates and block_dates[0] <= dates[-1]:
            raise ValueError("bear block dates overlap or are out of order")
        block_cash = finite_number(block.get("initial_cash"), "block initial_cash", positive=True)
        if block_cash != initial_cash:
            raise ValueError("bear block initial_cash changed")
        block_final = finite_number(block.get("final_account"), "block final_account", positive=True)
        block_error = finite_number(
            block.get("account_return_max_error"), "block account_return_max_error", nonnegative=True
        )
        if block_error > tolerance:
            raise ValueError("bear block account/return-cost error exceeds tolerance")
        block_net = block.get("daily_net_return")
        if not isinstance(block_net, list) or len(block_net) != len(block_dates):
            raise ValueError("bear block daily_net_return length mismatch")
        expected_final = initial_cash
        block_accounts = block.get("daily_account")
        if not isinstance(block_accounts, list) or len(block_accounts) != len(block_dates):
            raise ValueError("bear block daily_account length mismatch")
        for value, declared_account in zip(block_net, block_accounts):
            expected_final *= 1.0 + finite_number(value, "block daily_net_return")
            if abs(expected_final - finite_number(declared_account, "block daily_account", positive=True)) > max(
                0.01, initial_cash * tolerance
            ):
                raise ValueError("bear block daily account does not match daily net returns")
        if abs(expected_final - block_final) > max(0.01, initial_cash * tolerance):
            raise ValueError("bear block final_account does not match daily net returns")
        for key, target in (
            ("daily_gross_return", gross),
            ("daily_cost", cost),
            ("daily_net_return", net),
            ("daily_benchmark", bench),
        ):
            values = block.get(key)
            if not isinstance(values, list) or len(values) != len(block_dates):
                raise ValueError(f"bear block {key} length mismatch")
            target.extend(finite_number(value, key) for value in values)
        dates.extend(block_dates)
    validate_dates(dates, "combined bear dates", expected=expected_dates)
    for g, c, n in zip(gross, cost, net):
        if abs((g - c) - n) > 1e-12:
            raise ValueError("bear block gross/cost/net mismatch")
    nav = []
    value = 1.0
    for ret in net:
        value *= 1.0 + ret
        nav.append(value)
    account = [initial_cash * item for item in nav]
    report = pd.DataFrame(
        {"return": gross, "cost": cost, "bench": bench, "account": account},
        index=pd.to_datetime(dates),
    )
    perf = portfolio_performance(report, initial_cash=initial_cash, backtest_start=dates[0])
    return {
        "metric_schema_version": METRIC_SCHEMA_VERSION,
        "dates": dates,
        "daily_gross_return": [round(value, 8) for value in gross],
        "daily_cost": [round(value, 8) for value in cost],
        "daily_net_return": [round(value, 8) for value in net],
        "daily_benchmark": [round(value, 8) for value in bench],
        "daily_account": account,
        "strategy_total_return_net": perf["strategy_total_return"],
        "benchmark_cumulative_return": perf["benchmark_total_return"],
        "total_cost": round(sum(cost), 8),
        "final_account": account[-1],
        "account_return_max_error": perf["account_return_max_error"],
        "strategy_max_drawdown": perf["strategy_max_drawdown"],
        "benchmark_max_drawdown": perf["benchmark_max_drawdown"],
        "metric_version": perf["metric_version"],
        "validation_version": perf["validation_version"],
    }


def require_finite_metric(value, label):
    return finite_number(value, label)


def build_correction_inventory(source_path, *, source_label=None, initial_cash: float = 100_000_000):
    """Deterministically rebuild the 13-unit current-window correction inventory."""
    import hashlib
    import json
    from pathlib import Path

    path = Path(source_path)
    raw = path.read_bytes()
    payload = json.loads(raw)
    if not isinstance(payload, list) or not payload:
        raise ValueError("candidate results must be a non-empty list")
    initial_cash = finite_number(initial_cash, "initial_cash", positive=True)
    seen = set()
    units = []
    for row in payload:
        market = row.get("market")
        profile = row.get("profile")
        topk = row.get("topk")
        nd = row.get("nd")
        key = (market, profile, topk, nd)
        if key in seen:
            raise ValueError("duplicate candidate unit")
        seen.add(key)
        reported = finite_number(row.get("cumulative_return"), "reported cumulative_return")
        final_account = finite_number(row.get("final_account"), "final_account", positive=True)
        total_cost = finite_number(row.get("total_cost"), "total_cost", nonnegative=True)
        account_net = final_account / initial_cash - 1.0
        units.append(
            {
                "market": market,
                "profile": profile,
                "topk": topk,
                "nd": nd,
                "reported_cumulative_return": reported,
                "account_net_return": account_net,
                "difference": reported - account_net,
                "total_cost": total_cost,
                "final_account": final_account,
            }
        )
    expected_units = set(EXPECTED_CORRECTION_UNITS)
    if seen != expected_units:
        missing = sorted(expected_units - seen)
        extra = sorted(seen - expected_units)
        raise ValueError(f"candidate units differ from the registered set: missing={missing} extra={extra}")
    old_order = sorted(
        units,
        key=lambda item: (item["reported_cumulative_return"], item["market"], item["profile"], item["topk"]),
        reverse=True,
    )
    new_order = sorted(
        units,
        key=lambda item: (item["account_net_return"], item["market"], item["profile"], item["topk"]),
        reverse=True,
    )
    return {
        "schema_version": 1,
        "unit_registry_version": CORRECTION_UNIT_REGISTRY_VERSION,
        "expected_units_sha256": hashlib.sha256(
            json.dumps(EXPECTED_CORRECTION_UNITS, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest(),
        "source_path": source_label or path.name,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "metric_schema_version": METRIC_SCHEMA_VERSION,
        "units": units,
        "ranking_changed": [(item["market"], item["profile"], item["topk"]) for item in old_order]
        != [(item["market"], item["profile"], item["topk"]) for item in new_order],
        "old_order": [[item["market"], item["profile"], item["topk"]] for item in old_order],
        "new_order": [[item["market"], item["profile"], item["topk"]] for item in new_order],
    }
