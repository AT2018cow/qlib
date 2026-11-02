"""Portfolio overlays on frozen G1 signals: S16/S17/S18.

All overlays are post-processing on existing continuous-account reports; no
model is refit.  S16 (trend/vol gate) and S18 (drawdown breaker) are applied
to the best R3 S01 sleeve per market.  S17 combines the three sleeves with
monthly equal-weight or inverse-vol rebalancing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
for entry in (
    REPO_ROOT,
    REPO_ROOT / "quant" / "code",
    REPO_ROOT / "quant" / "code" / "execution",
    REPO_ROOT / "quant" / "code" / "research" / "csi300_csi500",
):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

MARKETS = ("csi300", "csi500", "csi1000")
BENCH = {"csi300": "SH000300", "csi500": "SH000905", "csi1000": "SH000852"}
BEST = {"csi300": ("B", "S01"), "csi500": ("B", "S02"), "csi1000": ("A", "S01")}
S01_BEST = {"csi300": ("B", "S01"), "csi500": ("B", "S01"), "csi1000": ("A", "S01")}
S02_BEST = {"csi300": ("A", "S02"), "csi500": ("A", "S02"), "csi1000": ("A", "S02")}
CURRENT = {"csi300": ("A", "S01"), "csi500": ("A", "S01"), "csi1000": ("A", "S01")}
POLICY_BASES = {"best": BEST, "s01": S01_BEST, "s02": S02_BEST, "current": CURRENT}


def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--baseline-root", default="/tmp/opencode/g1_baseline_all")
    p.add_argument("--provider", default="/tmp/opencode/qlib_cn_sel/qlib_bin")
    p.add_argument("--out", default="/tmp/opencode/g1_overlays")
    p.add_argument("--start", default="2021-02-19")
    p.add_argument("--end", default="2024-02-05")
    p.add_argument("--bench-start", default="2016-01-01")
    p.add_argument("--markets", default="csi300,csi500,csi1000")
    p.add_argument("--rebalance-every", type=int, default=20)
    p.add_argument("--roundtrip-bps", type=float, default=10.0)
    p.add_argument("--policy-base", choices=tuple(POLICY_BASES), default="s01")
    return p.parse_args()


def _load_report(root: Path, market: str, spine: str, cell: str):
    import pandas as pd

    path = root / market / spine / cell / "report.parquet"
    report = pd.read_parquet(path).sort_index()
    need = {"return", "cost", "bench", "turnover"}
    missing = need - set(report.columns)
    if missing:
        raise ValueError(f"{path} missing {sorted(missing)}")
    return report


def _benchmark_close(provider: str, benchmark: str, start: str, end: str):
    import pandas as pd
    import qlib
    from qlib.data import D

    qlib.init(provider_uri=provider, region="cn", kernels=1, skip_if_reg=True)
    frame = D.features([benchmark], ["$close"], start_time=start, end_time=end, freq="day")
    series = frame["$close"]
    if isinstance(series.index, pd.MultiIndex):
        series = series.droplevel(0)
    series.index = pd.to_datetime(series.index)
    return series.sort_index()


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
    out["total_cost"] = out["cost"].cumsum() * 100_000_000
    return out


def apply_trend_gate(report, close, ma_fast=120, ma_slow=250, vol_window=20, vol_cap=0.25):
    """S16: benchmark MA120/MA250 + 20d vol gate, applied next session."""
    import numpy as np
    import pandas as pd

    c = close.reindex(report.index).ffill()
    fast = c.rolling(ma_fast, min_periods=ma_fast).mean()
    slow = c.rolling(ma_slow, min_periods=ma_slow).mean()
    vol = c.pct_change().rolling(vol_window).std() * np.sqrt(238)
    target = pd.Series(1.0, index=c.index)
    target[c < fast] = 0.6
    target[(c < fast) & ((c < slow) | (vol > vol_cap))] = 0.3
    target[fast.isna() | slow.isna()] = 1.0
    weight = target.shift(1).fillna(1.0)
    delta = weight.diff().fillna(0.0)
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
    requires ``recovery_days`` consecutive closes of the overlay NAV above its
    own MA20.  The rule is fixed ex ante and is not tuned on R3.
    """
    import numpy as np
    import pandas as pd

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
    weights = np.zeros(n)

    for i in range(n):
        weights[i] = weight
        gross[i] = weight * base_ret[i]
        total_cost[i] = weight * base_cost[i] + pending_cost
        turnover[i] = weight * float(report["turnover"].iloc[i]) + pending_turnover
        nav *= 1.0 + gross[i] - total_cost[i]
        nav_hist.append(nav)
        peak = max(peak, nav)
        drawdown = nav / peak - 1.0
        if len(nav_hist) >= ma_window:
            ma = float(np.mean(nav_hist[-ma_window:]))
            above = above + 1 if nav > ma else 0
        else:
            above = 0

        if drawdown <= -hard:
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
    weights = weights.fillna(1.0 / net_returns.shape[1])
    return weights


def combine_sleeves(reports, kind: str, rebalance_every: int, cost_bps: float):
    """S17: monthly equal-weight / inverse-vol combination of market sleeves."""
    import numpy as np
    import pandas as pd

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
        if i > 0 and i % rebalance_every == 0:
            new_w = target.iloc[i - 1].to_numpy(dtype=float)
            delta = float(np.abs(new_w - w).sum())
            total_cost[i] += delta * cost_bps / 10000.0
            turnover[i] += delta
            w = new_w
        r = rets.iloc[i].to_numpy(dtype=float)
        c = costs.iloc[i].to_numpy(dtype=float)
        b = benches.iloc[i].to_numpy(dtype=float)
        gross[i] = float(np.dot(w, r))
        total_cost[i] += float(np.dot(w, c))
        bench[i] = float(np.dot(w, b))
        turnover[i] += float(sum(w[j] * float(reports[market]["turnover"].iloc[i]) for j, market in enumerate(reports)))
        denom = 1.0 + gross[i]
        if denom > 0:
            w = w * (1.0 + r) / denom
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
    out["total_cost"] = out["cost"].cumsum() * 100_000_000
    return out


def main() -> None:
    args = _args()
    markets = [x.strip() for x in args.markets.split(",") if x.strip()]
    for market in markets:
        if market not in MARKETS:
            raise ValueError(f"unknown market {market}")

    from portfolio_performance import portfolio_performance
    from stage0_p0_core import apply_cost_stress

    root = Path(args.baseline_root)
    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    rows = []

    def save(name: str, report, metrics: dict):
        d = out_root / name
        d.mkdir(parents=True, exist_ok=True)
        report.to_parquet(d / "report.parquet")
        (d / "metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False) + "\n")
        rows.append(metrics)
        print(
            f"[overlay] {name} CAGR={metrics['strategy_cagr']:.2%} "
            f"MDD={metrics['strategy_max_drawdown']:.2%} "
            f"Sharpe={metrics['sharpe']} turnover={metrics['mean_turnover']:.4f}",
            flush=True,
        )

    def add_stress(metrics: dict, report) -> None:
        for bps in (10.0, 20.0):
            stressed = portfolio_performance(apply_cost_stress(report, bps), backtest_start=args.start)
            metrics[f"cagr_cost_plus_{int(bps)}bp"] = stressed["strategy_cagr"]
            metrics[f"maxdd_cost_plus_{int(bps)}bp"] = stressed["strategy_max_drawdown"]
            metrics[f"sharpe_cost_plus_{int(bps)}bp"] = stressed["sharpe"]

    reports_best = {}
    reports_current = {}
    reports_policy = {}
    for market in markets:
        spine, cell = BEST[market]
        reports_best[market] = _load_report(root, market, spine, cell)
        spine_c, cell_c = CURRENT[market]
        reports_current[market] = _load_report(root, market, spine_c, cell_c)
        spine_p, cell_p = POLICY_BASES[args.policy_base][market]
        reports_policy[market] = _load_report(root, market, spine_p, cell_p)

    for market in markets:
        report = reports_policy[market]
        close = _benchmark_close(args.provider, BENCH[market], args.bench_start, args.end)
        trend, weight = apply_trend_gate(report, close)
        metrics = portfolio_performance(trend, backtest_start=args.start)
        metrics.update(
            {
                "overlay": "S16_trend_gate",
                "market": market,
                "policy_base": args.policy_base,
                "base": f"{POLICY_BASES[args.policy_base][market][0]}/{POLICY_BASES[args.policy_base][market][1]}",
                "mean_turnover": round(float(trend["turnover"].mean()), 6),
                "mean_exposure": round(float(weight.mean()), 6),
                "min_exposure": round(float(weight.min()), 6),
            }
        )
        add_stress(metrics, trend)
        save(f"S16/{market}", trend, metrics)

        breaker, weight = apply_drawdown_breaker(report)
        metrics = portfolio_performance(breaker, backtest_start=args.start)
        metrics.update(
            {
                "overlay": "S18_drawdown_breaker",
                "market": market,
                "policy_base": args.policy_base,
                "base": f"{POLICY_BASES[args.policy_base][market][0]}/{POLICY_BASES[args.policy_base][market][1]}",
                "mean_turnover": round(float(breaker["turnover"].mean()), 6),
                "mean_exposure": round(float(weight.mean()), 6),
                "min_exposure": round(float(weight.min()), 6),
            }
        )
        add_stress(metrics, breaker)
        save(f"S18/{market}", breaker, metrics)

    for label, sleeves in (("best", reports_best), ("current", reports_current)):
        for kind in ("equal", "riskparity"):
            combo = combine_sleeves(sleeves, kind, args.rebalance_every, args.roundtrip_bps)
            metrics = portfolio_performance(combo, backtest_start=args.start)
            metrics.update(
                {
                    "overlay": f"S17_{label}_{kind}",
                    "market": "multi",
                    "base": (
                        ";".join(f"{m}:{BEST[m][0]}/{BEST[m][1]}" for m in markets)
                        if label == "best"
                        else ";".join(f"{m}:{CURRENT[m][0]}/{CURRENT[m][1]}" for m in markets)
                    ),
                    "mean_turnover": round(float(combo["turnover"].mean()), 6),
                }
            )
            add_stress(metrics, combo)
            save(f"S17/{label}_{kind}", combo, metrics)

    # Account-level breaker on the best equal-weight portfolio.
    best_equal = combine_sleeves(reports_best, "equal", args.rebalance_every, args.roundtrip_bps)
    breaker, weight = apply_drawdown_breaker(best_equal)
    metrics = portfolio_performance(breaker, backtest_start=args.start)
    metrics.update(
        {
            "overlay": "S18_on_S17_best_equal",
            "market": "multi",
            "base": "S17_best_equal",
            "mean_turnover": round(float(breaker["turnover"].mean()), 6),
            "mean_exposure": round(float(weight.mean()), 6),
            "min_exposure": round(float(weight.min()), 6),
        }
    )
    add_stress(metrics, breaker)
    save("S18/S17_best_equal", breaker, metrics)

    (out_root / "summary.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n")
    print(f"[overlay] wrote {len(rows)} overlays to {out_root}", flush=True)


if __name__ == "__main__":
    main()
