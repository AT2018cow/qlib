"""Causal arithmetic portfolio diagnostics; order-level promotion requires replay."""

from __future__ import annotations


def _validate_report(report):
    import numpy as np

    if report.empty or report.index.has_duplicates or not report.index.is_monotonic_increasing:
        raise ValueError("overlay needs a nonempty ordered unique index")
    if not np.isfinite(report[["return", "cost", "bench", "turnover"]].to_numpy(dtype=float)).all():
        raise ValueError("missing/non-finite overlay input")
    if (report["cost"] < 0).any() or (report["turnover"] < 0).any() or (report["return"] - report["cost"] <= -1).any():
        raise ValueError("invalid overlay cost, turnover or return")


def _base_report(report, gross_return, cost, turnover, bench=None):
    import pandas as pd

    out = pd.DataFrame(index=report.index)
    out["return"] = gross_return
    out["cost"] = cost
    out["bench"] = report["bench"] if bench is None else bench
    out["turnover"] = turnover
    net = out["return"] - out["cost"]
    out["account"] = 100_000_000 * (1.0 + net).cumprod()
    # These fields are required by the established arithmetic cost-stress path;
    # the overlay return stream does not model a separate cash ledger.
    out["cash"] = 0.0
    prior = out["account"].shift(1).fillna(100_000_000)
    out["total_cost"] = (out["cost"] * prior).cumsum()
    out.attrs.update(
        evaluation_mode="ARITHMETIC_DIAGNOSTIC",
        promotion_eligible=False,
        required_gate="ORDER_LEVEL_REPLAY_AND_LIQUIDITY_EVIDENCE",
    )
    return out


def apply_trend_gate(report, close, ma_fast=120, ma_slow=250, vol_window=20, vol_cap=0.25):
    """S16: benchmark MA120/MA250 + 20d vol gate, applied next session."""
    import numpy as np
    import pandas as pd

    _validate_report(report)
    if (
        close.index.has_duplicates
        or not close.index.is_monotonic_increasing
        or not np.isfinite(close.to_numpy()).all()
        or (close <= 0).any()
    ):
        raise ValueError("invalid benchmark close history")
    if not report.index.isin(close.index).all():
        raise ValueError("benchmark missing evaluation sessions")
    c = close
    fast = c.rolling(ma_fast, min_periods=ma_fast).mean()
    slow = c.rolling(ma_slow, min_periods=ma_slow).mean()
    vol = c.pct_change().rolling(vol_window).std() * np.sqrt(238)
    target = pd.Series(1.0, index=c.index)
    target[c < fast] = 0.6
    target[(c < fast) & ((c < slow) | (vol > vol_cap))] = 0.3
    target[fast.isna() | slow.isna()] = 1.0
    all_weights = target.shift(1).fillna(1.0)
    weight = all_weights.reindex(report.index)
    # Include any initial adjustment from the base sleeve's 100% exposure.
    delta = weight.diff()
    delta.iloc[0] = weight.iloc[0] - 1.0
    overlay_cost = pd.Series(0.0, index=weight.index)
    overlay_cost[delta < 0] = (-delta[delta < 0]) * 0.0015
    overlay_cost[delta > 0] = delta[delta > 0] * 0.0005
    out = _base_report(
        report,
        gross_return=weight * report["return"],
        cost=weight * report["cost"] + overlay_cost,
        turnover=weight * report["turnover"] + delta.abs(),
    )
    return out, weight


def apply_drawdown_breaker(report, soft=0.12, hard=0.18, recovery_days=5, ma_window=20):
    """S18: account drawdown breaker with a 5-session MA20 recovery gate.

    Soft breach -> 50% exposure; hard breach -> cash.  Recovery from cash/half
    requires ``recovery_days`` consecutive closes of the independent base
    sleeve's shadow NAV above MA20. Recovery resets the risk high-water mark
    to the current overlay NAV. All decisions take effect next session.
    """
    import numpy as np
    import pandas as pd

    _validate_report(report)
    if not (0 < soft < hard < 1) or recovery_days < 1 or ma_window < 2:
        raise ValueError("invalid breaker parameters")
    base_ret = report["return"].to_numpy(dtype=float)
    base_cost = report["cost"].to_numpy(dtype=float)
    n = len(report)
    gross = np.zeros(n)
    total_cost = np.zeros(n)
    turnover = np.zeros(n)
    nav = 1.0
    peak = 1.0
    weight = 1.0
    pending_cost = 0.0
    pending_turnover = 0.0
    above = 0
    nav_hist = []
    shadow = 1.0
    weights = np.zeros(n)

    for i in range(n):
        weights[i] = weight
        gross[i] = weight * base_ret[i]
        total_cost[i] = weight * base_cost[i] + pending_cost
        turnover[i] = weight * float(report["turnover"].iloc[i]) + pending_turnover
        nav *= 1.0 + gross[i] - total_cost[i]
        if not np.isfinite(nav) or nav <= 0:
            raise ValueError("overlay NAV exhausted")
        shadow *= 1.0 + base_ret[i] - base_cost[i]
        nav_hist.append(shadow)
        peak = max(peak, nav)
        drawdown = nav / peak - 1.0
        if len(nav_hist) >= ma_window:
            ma = float(np.mean(nav_hist[-ma_window:]))
            above = above + 1 if shadow > ma else 0
        else:
            above = 0

        if weight < 1.0 and above >= recovery_days:
            target = 1.0
            peak = nav
            above = 0
        elif drawdown <= -hard:
            target = 0.0
        elif drawdown <= -soft:
            target = 0.5 if (weight > 0.0 or above >= recovery_days) else 0.0
        elif weight < 1.0 and above >= recovery_days:
            target = 1.0
        else:
            target = weight
        delta = abs(target - weight)
        if delta > 0:
            pending_turnover = delta
            pending_cost = delta * (0.0015 if target < weight else 0.0005)
        else:
            pending_turnover = 0.0
            pending_cost = 0.0
        weight = target

    out = _base_report(
        report,
        gross_return=pd.Series(gross, index=report.index),
        cost=pd.Series(total_cost, index=report.index),
        turnover=pd.Series(turnover, index=report.index),
    )
    return out, pd.Series(weights, index=report.index)


def _target_weights(net_returns, kind: str, window: int = 60):
    import numpy as np
    import pandas as pd

    if kind == "equal":
        return pd.DataFrame(1.0 / net_returns.shape[1], index=net_returns.index, columns=net_returns.columns)
    vol = net_returns.rolling(window, min_periods=window).std().shift(1)
    inv = 1.0 / vol.replace(0.0, np.nan)
    weights = inv.div(inv.sum(axis=1), axis=0)
    # A partially missing row cannot be filled cell-wise: [NaN, 1] would
    # become [.5, 1] and create leverage. Use one explicit fallback per row.
    weights.loc[weights.isna().any(axis=1)] = 1.0 / net_returns.shape[1]
    return weights


def combine_sleeves(reports, kind: str, rebalance_every: int, cost_bps: float):
    """S17: monthly equal-weight / inverse-vol combination of market sleeves."""
    import numpy as np
    import pandas as pd

    if (
        not reports
        or kind not in ("equal", "riskparity")
        or rebalance_every < 1
        or not np.isfinite(cost_bps)
        or cost_bps < 0
    ):
        raise ValueError("invalid sleeve configuration")
    common = next(iter(reports.values())).index
    for report in reports.values():
        _validate_report(report)
        if not report.index.equals(common):
            raise ValueError("sleeve report indexes do not align")
    rets = pd.DataFrame({m: r["return"] for m, r in reports.items()}).sort_index()
    costs = pd.DataFrame({m: r["cost"] for m, r in reports.items()}).sort_index()
    benches = pd.DataFrame({m: r["bench"] for m, r in reports.items()}).sort_index()
    if not rets.index.equals(costs.index) or not rets.index.equals(benches.index):
        raise ValueError("sleeve report indexes do not align")
    net = rets - costs
    target = _target_weights(net, kind)
    n = len(rets)
    w = target.iloc[0].to_numpy(dtype=float)
    gross = np.zeros(n)
    total_cost = np.zeros(n)
    bench = np.zeros(n)
    turnover = np.zeros(n)
    for i in range(n):
        outer_fee = 0.0
        if i > 0 and i % rebalance_every == 0:
            new_w = target.iloc[i].to_numpy(dtype=float)  # target is already shifted one session
            delta = float(np.abs(new_w - w).sum())
            outer_fee = delta * cost_bps / 10000.0
            if outer_fee >= 1:
                raise ValueError("rebalance fee exhausts portfolio")
            total_cost[i] += outer_fee
            turnover[i] += delta
            w = new_w
        r = rets.iloc[i].to_numpy(dtype=float)
        c = costs.iloc[i].to_numpy(dtype=float)
        b = benches.iloc[i].to_numpy(dtype=float)
        # Deduct external fees pro rata before the session's sleeve returns.
        gross[i] = (1 - outer_fee) * float(np.dot(w, r))
        total_cost[i] += (1 - outer_fee) * float(np.dot(w, c))
        bench[i] = float(np.dot(w, b))
        turnover[i] += float(sum(w[j] * float(reports[market]["turnover"].iloc[i]) for j, market in enumerate(reports)))
        denom = float(np.dot(w, 1 + r - c))
        if denom <= 0 or not np.isfinite(denom):
            raise ValueError("combined portfolio exhausted")
        w = w * (1.0 + r - c) / denom
    out = pd.DataFrame(index=rets.index)
    out["return"] = gross
    out["cost"] = total_cost
    out["bench"] = bench
    out["turnover"] = turnover
    net = out["return"] - out["cost"]
    out["account"] = 100_000_000 * (1.0 + net).cumprod()
    # These fields are required by the established arithmetic cost-stress path;
    # the overlay return stream does not model a separate cash ledger.
    out["cash"] = 0.0
    out["total_cost"] = (out["cost"] * out["account"].shift(1).fillna(100_000_000)).cumsum()
    out.attrs.update(
        evaluation_mode="ARITHMETIC_DIAGNOSTIC",
        promotion_eligible=False,
        required_gate="ORDER_LEVEL_REPLAY_AND_LIQUIDITY_EVIDENCE",
    )
    return out
