"""Portfolio overlays on frozen G1 signals: S16/S17/S18.

All overlays are post-processing on existing continuous-account reports; no
model is refit.  S16 (trend/vol gate) and S18 (drawdown breaker) are applied
to the best R3 S01 sleeve per market.  S17 combines the three sleeves with
monthly equal-weight or inverse-vol rebalancing.
"""

from __future__ import annotations

import argparse
import hashlib
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
INITIAL_CASH = 100_000_000.0


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


from portfolio_overlays import (
    apply_trend_gate,
    apply_drawdown_breaker,
    combine_sleeves,
)


def main() -> None:
    args = _args()
    markets = [x.strip() for x in args.markets.split(",") if x.strip()]
    for market in markets:
        if market not in MARKETS:
            raise ValueError(f"unknown market {market}")

    from portfolio_performance import portfolio_performance
    from research_bundle import build_evaluation_bundle, verify_bundle
    from research_governance import digest
    from stage0_p0_core import apply_cost_stress

    root = Path(args.baseline_root)
    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    rows = []

    def save(name: str, report, metrics: dict, *, source_bundle=None, selection_group=None):
        metrics.update(
            {
                "evaluation_mode": "ARITHMETIC_DIAGNOSTIC",
                "promotion_eligible": False,
                "required_gate": "ORDER_LEVEL_REPLAY_AND_LIQUIDITY_EVIDENCE",
            }
        )
        d = out_root / name
        d.mkdir(parents=True, exist_ok=True)
        report.to_parquet(d / "report.parquet")
        (d / "metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
        if source_bundle is not None:
            source = verify_bundle(source_bundle)
            execution_contract = {
                "schema_version": 1,
                "kind": "g1_overlay_evaluation",
                "market": metrics["market"],
                "overlay": metrics["overlay"],
                "base": metrics["base"],
                "policy_base": metrics.get("policy_base"),
                "window": [args.start, args.end],
                "rebalance_every": args.rebalance_every,
                "roundtrip_bps": args.roundtrip_bps,
                "initial_cash": INITIAL_CASH,
                "source_bundle_manifest_sha256": source["manifest_sha256"],
                "overlay_source_sha256": hashlib.sha256(
                    (REPO_ROOT / "quant/code/execution/portfolio_overlays.py").read_bytes()
                ).hexdigest(),
            }
            execution_sha = digest(execution_contract)
            selection_inputs = {"baseline": [], "overlay_s01": [], "overlay_best": [], "periodic": []}
            if selection_group is not None:
                selection_inputs[selection_group].append(
                    json.loads(json.dumps(metrics, sort_keys=True, allow_nan=False))
                )
            build_evaluation_bundle(
                d / "evidence_bundle",
                g1_bundle_root=source_bundle,
                report_payload={
                    "dates": [str(value.date()) for value in report.index],
                    "gross_return": [float(value) for value in report["return"]],
                    "cost": [float(value) for value in report["cost"]],
                    "benchmark_return": [float(value) for value in report["bench"]],
                    "account": [float(value) for value in report["account"]],
                    "turnover": [float(value) for value in report["turnover"]],
                },
                ledger_payload={"initial_cash": INITIAL_CASH},
                execution_contract_sha256=execution_sha,
                selection_inputs=selection_inputs,
            )
        rows.append(metrics)
        print(
            f"[overlay] {name} CAGR={metrics['strategy_cagr']:.2%} "
            f"MDD={metrics['strategy_max_drawdown']:.2%} "
            f"Sharpe={metrics['sharpe']} turnover={metrics['mean_turnover']:.4f}",
            flush=True,
        )

    def add_stress(metrics: dict, report) -> None:
        for bps in (10.0, 20.0):
            stressed = portfolio_performance(
                apply_cost_stress(report, bps, initial_cash=INITIAL_CASH),
                initial_cash=INITIAL_CASH,
                backtest_start=args.start,
            )
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
        policy_spine, policy_cell = POLICY_BASES[args.policy_base][market]
        source_bundle = root / market / policy_spine / policy_cell / "evidence_bundle"
        selection_group = {"s01": "overlay_s01", "best": "overlay_best"}.get(args.policy_base)
        close = _benchmark_close(args.provider, BENCH[market], args.bench_start, args.end)
        trend, weight = apply_trend_gate(report, close)
        metrics = portfolio_performance(trend, initial_cash=INITIAL_CASH, backtest_start=args.start)
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
        save(
            f"S16/{market}",
            trend,
            metrics,
            source_bundle=source_bundle,
            selection_group=selection_group,
        )

        breaker, weight = apply_drawdown_breaker(report)
        metrics = portfolio_performance(breaker, initial_cash=INITIAL_CASH, backtest_start=args.start)
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
        save(
            f"S18/{market}",
            breaker,
            metrics,
            source_bundle=source_bundle,
            selection_group=selection_group,
        )

    for label, sleeves in (("best", reports_best), ("current", reports_current)):
        for kind in ("equal", "riskparity"):
            combo = combine_sleeves(sleeves, kind, args.rebalance_every, args.roundtrip_bps)
            metrics = portfolio_performance(combo, initial_cash=INITIAL_CASH, backtest_start=args.start)
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
    metrics = portfolio_performance(breaker, initial_cash=INITIAL_CASH, backtest_start=args.start)
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

    (out_root / "summary.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    print(f"[overlay] wrote {len(rows)} overlays to {out_root}", flush=True)


if __name__ == "__main__":
    main()
