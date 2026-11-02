"""S01/S02 continuous-account baseline replay for G1 spines (2021-2024).

S01 = current production profile + Top10/Drop1.
S02 = current production profile + Top20/Drop2.

This script never trains. It replays the frozen G1 signal with the same
board-aware, next-open execution contract used by the production research
stack, then computes compounded metrics with ``portfolio_performance``.
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
    REPO_ROOT / "quant" / "code" / "production",
    REPO_ROOT / "quant" / "code" / "research" / "csi300_csi500",
    REPO_ROOT / "quant" / "code" / "research" / "stage_ab",
):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

MARKETS = {
    "csi300": {"benchmark": "SH000300"},
    "csi500": {"benchmark": "SH000905"},
    "csi1000": {"benchmark": "SH000852"},
}
COMBOS = {
    "S01": {"topk": 10, "dropn": 1},
    "S02": {"topk": 20, "dropn": 2},
    "T50D5": {"topk": 50, "dropn": 5},
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signal-root", default="/tmp/opencode/g1_run_dump/regime_g1_20261102_a1")
    parser.add_argument("--provider", default="/tmp/opencode/qlib_cn_sel/qlib_bin")
    parser.add_argument("--out", default="/tmp/opencode/g1_baseline")
    parser.add_argument("--signal-start", default="2021-02-18")
    parser.add_argument("--exec-end", default="2024-02-05")
    parser.add_argument("--markets", default="csi300,csi500,csi1000")
    parser.add_argument("--spines", default="A")
    parser.add_argument("--cells", default="S01,S02")
    parser.add_argument("--only", default="")
    parser.add_argument("--rebalance-every", type=int, default=1)
    parser.add_argument("--open-cost", type=float, default=0.0005)
    parser.add_argument("--close-cost", type=float, default=0.0015)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    import pandas as pd
    import qlib
    from qlib.backtest import collect_data
    from qlib.data import D

    from board_execution import research_exchange
    from execution_quote_universe import execution_quote_codes
    from portfolio_performance import portfolio_performance
    from stage0_p0_core import apply_cost_stress

    qlib.init(provider_uri=args.provider, region="cn", kernels=1, skip_if_reg=True)
    calendar = [str(x)[:10] for x in D.calendar(start_time=args.signal_start, end_time=args.exec_end, freq="day")]
    if args.signal_start not in calendar:
        raise ValueError(f"signal start missing from calendar: {args.signal_start}")
    start_i = calendar.index(args.signal_start)
    if start_i + 1 >= len(calendar) or calendar[-1] != args.exec_end:
        raise ValueError("execution window is not covered by the local calendar")
    exec_start = calendar[start_i + 1]

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    rows = []
    markets = [x.strip() for x in args.markets.split(",") if x.strip()]
    spines = [x.strip() for x in args.spines.split(",") if x.strip()]
    cells = [x.strip() for x in args.cells.split(",") if x.strip()]
    only = {x.strip() for x in args.only.split(",") if x.strip()}

    for market in markets:
        if market not in MARKETS:
            raise ValueError(f"unknown market {market}")
        benchmark = MARKETS[market]["benchmark"]
        codes = execution_quote_codes(market, exec_start, args.exec_end, provider=D)
        print(
            f"[baseline] {market} codes={len(codes)} benchmark={benchmark} exec={exec_start}..{args.exec_end}",
            flush=True,
        )
        for spine in spines:
            signal_path = Path(args.signal_root) / market / spine / "signal.parquet"
            if not signal_path.is_file():
                raise FileNotFoundError(signal_path)
            signal = pd.read_parquet(signal_path)["score"].sort_index()
            if signal.index.has_duplicates:
                raise ValueError(f"duplicate signal index: {signal_path}")
            if signal.isna().any():
                raise ValueError(f"NaN signal: {signal_path}")
            dates = signal.index.get_level_values(0)
            signal = signal[(dates >= pd.Timestamp(args.signal_start)) & (dates <= pd.Timestamp(args.exec_end))]
            actual_days = sorted({str(x)[:10] for x in signal.index.get_level_values(0).unique()})
            expected_days = calendar[start_i:]
            if actual_days != expected_days:
                raise ValueError(f"signal day coverage mismatch for {market}/{spine}")

            for cell in cells:
                key = f"{market}/{spine}/{cell}"
                if only and key not in only:
                    continue
                spec = COMBOS[cell]
                strategy_kwargs = {
                    "signal": signal,
                    "topk": spec["topk"],
                    "n_drop": spec["dropn"],
                    "forbid_all_trade_at_limit": False,
                }
                if args.rebalance_every > 1:
                    strategy_class = "PeriodicTopkDropoutStrategy"
                    strategy_module = "periodic_strategy"
                    strategy_kwargs["rebalance_every"] = args.rebalance_every
                else:
                    strategy_class = "DeterministicTopkDropoutStrategy"
                    strategy_module = "deterministic_strategy"
                exchange_kwargs = research_exchange(exec_start, args.exec_end, codes=codes)
                exchange_kwargs["exchange"]["kwargs"]["open_cost"] = float(args.open_cost)
                exchange_kwargs["exchange"]["kwargs"]["close_cost"] = float(args.close_cost)
                result = {}
                decisions = list(
                    collect_data(
                        strategy={
                            "class": strategy_class,
                            "module_path": strategy_module,
                            "kwargs": strategy_kwargs,
                        },
                        executor={
                            "class": "SimulatorExecutor",
                            "module_path": "qlib.backtest.executor",
                            "kwargs": {
                                "time_per_step": "day",
                                "generate_portfolio_metrics": True,
                                "track_data": True,
                            },
                        },
                        start_time=exec_start,
                        end_time=args.exec_end,
                        account=100_000_000,
                        benchmark=benchmark,
                        exchange_kwargs=exchange_kwargs,
                        return_value=result,
                    )
                )
                report = result["portfolio_dict"]["1day"][0]
                if len(report) != len(expected_days) - 1 or not report.index.is_unique:
                    raise ValueError(f"report coverage mismatch for {key}: {len(report)}")
                metrics = portfolio_performance(report, backtest_start=exec_start)
                metrics.update(
                    {
                        "market": market,
                        "spine": spine,
                        "cell": cell,
                        "topk": spec["topk"],
                        "dropn": spec["dropn"],
                        "benchmark": benchmark,
                        "signal_start": args.signal_start,
                        "exec_start": exec_start,
                        "exec_end": args.exec_end,
                        "codes": len(codes),
                        "decisions": len(decisions),
                        "rebalance_every": args.rebalance_every,
                        "open_cost": args.open_cost,
                        "close_cost": args.close_cost,
                        "mean_turnover": round(float(report["turnover"].mean()), 6),
                        "total_turnover": round(float(report["turnover"].sum()), 6),
                        "total_cost": round(float(report["cost"].sum()), 6),
                    }
                )
                for bps in (10.0, 20.0):
                    stressed = portfolio_performance(apply_cost_stress(report, bps), backtest_start=exec_start)
                    metrics[f"cagr_cost_plus_{int(bps)}bp"] = stressed["strategy_cagr"]
                    metrics[f"maxdd_cost_plus_{int(bps)}bp"] = stressed["strategy_max_drawdown"]
                    metrics[f"sharpe_cost_plus_{int(bps)}bp"] = stressed["sharpe"]
                cell_dir = out_root / market / spine / cell
                cell_dir.mkdir(parents=True, exist_ok=True)
                report.to_parquet(cell_dir / "report.parquet")
                (cell_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False) + "\n")
                rows.append(metrics)
                print(
                    f"[baseline] {key} CAGR={metrics['strategy_cagr']:.2%} "
                    f"MDD={metrics['strategy_max_drawdown']:.2%} "
                    f"Sharpe={metrics['sharpe']} turnover={metrics['mean_turnover']:.4f}",
                    flush=True,
                )

    (out_root / "summary.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n")
    print(f"[baseline] wrote {len(rows)} cells to {out_root}", flush=True)


if __name__ == "__main__":
    main()
