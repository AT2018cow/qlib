"""S01/S02 continuous-account replay for G1/G2 spines (configurable window).

S01 = current production profile + Top10/Drop1.
S02 = current production profile + Top20/Drop2.

This script never trains. It replays a frozen G1/G2 signal with the same
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
    parser.add_argument("--account", type=float, default=100_000_000.0)
    parser.add_argument("--st-history", type=Path)
    parser.add_argument(
        "--allow-st-reference",
        action="store_true",
        help="explicit reference-only run without dated historical ST evidence",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.st_history is None and not args.allow_st_reference:
        raise ValueError("provide --st-history or explicitly select --allow-st-reference")
    from artifact_io import loads

    st_history = loads(args.st_history.read_text()) if args.st_history else None
    import pandas as pd
    import qlib
    from qlib.backtest import collect_data
    from qlib.data import D

    from board_execution import research_exchange
    from execution_quote_universe import execution_quote_codes
    from portfolio_performance import portfolio_performance
    from research_bundle import build_evaluation_bundle
    from stage0_p0_core import apply_cost_stress
    from research_governance import trial_record, digest
    import hashlib

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
                strategy_class = (
                    "PeriodicTopkDropoutStrategy" if args.rebalance_every > 1 else "DeterministicTopkDropoutStrategy"
                )
                strategy_module = "periodic_strategy" if args.rebalance_every > 1 else "deterministic_strategy"
                trial_identity = {
                    "market": market,
                    "spine": spine,
                    "cell": cell,
                    "signal_sha256": hashlib.sha256(signal_path.read_bytes()).hexdigest(),
                    "exec_start": exec_start,
                    "exec_end": args.exec_end,
                    "account": float(args.account),
                    "open_cost": args.open_cost,
                    "close_cost": args.close_cost,
                    "rebalance_every": args.rebalance_every,
                    "topk": spec["topk"],
                    "dropn": spec["dropn"],
                    "benchmark": benchmark,
                    "strategy_class": strategy_class,
                    "strategy_module": strategy_module,
                    "st_history_sha256": digest(st_history) if st_history is not None else None,
                    "execution_source_sha256": digest(
                        {
                            name: hashlib.sha256(
                                (REPO_ROOT / "quant" / "code" / "execution" / name).read_bytes()
                            ).hexdigest()
                            for name in (
                                "board_execution.py",
                                "deterministic_strategy.py",
                                "execution_limits.py",
                                "order_quantity.py",
                                "periodic_strategy.py",
                                "portfolio_performance.py",
                            )
                        }
                    ),
                }
                with trial_record(out_root / "trials", trial_identity) as trial:
                    strategy_kwargs = {
                        "signal": signal,
                        "topk": spec["topk"],
                        "n_drop": spec["dropn"],
                        "forbid_all_trade_at_limit": False,
                    }
                    if args.rebalance_every > 1:
                        strategy_kwargs["rebalance_every"] = args.rebalance_every
                    exchange_kwargs = research_exchange(
                        exec_start,
                        args.exec_end,
                        codes=codes,
                        st_history=st_history,
                        require_verified_st=not args.allow_st_reference,
                    )
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
                            account=float(args.account),
                            benchmark=benchmark,
                            exchange_kwargs=exchange_kwargs,
                            return_value=result,
                        )
                    )
                    report = result["portfolio_dict"]["1day"][0]
                    if len(report) != len(expected_days) - 1 or not report.index.is_unique:
                        raise ValueError(f"report coverage mismatch for {key}: {len(report)}")
                    metrics = portfolio_performance(report, initial_cash=float(args.account), backtest_start=exec_start)
                    metrics.update(
                        {
                            "market": market,
                            "st_evidence": "DATED_HISTORY" if st_history is not None else "REFERENCE_ONLY",
                            "evaluation_mode": "DAILY_OPEN_SIMULATION",
                            "promotion_eligible": False,
                            "required_gate": "FORWARD_AND_LIQUIDITY_EVIDENCE",
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
                            "cadence": {1: "daily", 5: "weekly", 20: "monthly"}.get(
                                args.rebalance_every, f"every_{args.rebalance_every}_sessions"
                            ),
                            "open_cost": args.open_cost,
                            "close_cost": args.close_cost,
                            "mean_turnover": round(float(report["turnover"].mean()), 6),
                            "total_turnover": round(float(report["turnover"].sum()), 6),
                            "total_cost": round(float(report["cost"].sum()), 6),
                        }
                    )
                    for bps in (10.0, 20.0):
                        try:
                            stressed = portfolio_performance(
                                apply_cost_stress(report, bps, initial_cash=float(args.account)),
                                initial_cash=float(args.account),
                                backtest_start=exec_start,
                            )
                        except ValueError as exc:
                            # At a 1M account, a large cost stress can exhaust the
                            # NAV.  Record the failure instead of aborting the base
                            # metrics; the gate analysis treats it as failed.
                            metrics[f"cagr_cost_plus_{int(bps)}bp"] = None
                            metrics[f"maxdd_cost_plus_{int(bps)}bp"] = None
                            metrics[f"sharpe_cost_plus_{int(bps)}bp"] = None
                            metrics[f"cost_stress_{int(bps)}bp_error"] = str(exc)
                            continue
                        metrics[f"cagr_cost_plus_{int(bps)}bp"] = stressed["strategy_cagr"]
                        metrics[f"maxdd_cost_plus_{int(bps)}bp"] = stressed["strategy_max_drawdown"]
                        metrics[f"sharpe_cost_plus_{int(bps)}bp"] = stressed["sharpe"]
                    cell_dir = out_root / market / spine / cell
                    cell_dir.mkdir(parents=True, exist_ok=True)
                    report.to_parquet(cell_dir / "report.parquet")
                    (cell_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False) + "\n")
                    selection_inputs = {"baseline": [], "overlay_s01": [], "overlay_best": [], "periodic": []}
                    selection_group = "periodic" if args.rebalance_every > 1 else "baseline"
                    selection_inputs[selection_group].append(
                        json.loads(json.dumps(metrics, allow_nan=False, sort_keys=True))
                    )
                    report_payload = {
                        "dates": [str(value.date()) for value in report.index],
                        "gross_return": [float(value) for value in report["return"]],
                        "cost": [float(value) for value in report["cost"]],
                        "benchmark_return": [float(value) for value in report["bench"]],
                        "account": [float(value) for value in report["account"]],
                        "turnover": [float(value) for value in report["turnover"]],
                    }
                    build_evaluation_bundle(
                        cell_dir / "evidence_bundle",
                        g1_bundle_root=signal_path.parent / "evidence_bundle",
                        report_payload=report_payload,
                        ledger_payload={"initial_cash": float(args.account)},
                        execution_contract_sha256=digest(trial_identity),
                        selection_inputs=selection_inputs,
                    )
                    rows.append(metrics)
                    print(
                        f"[baseline] {key} CAGR={metrics['strategy_cagr']:.2%} "
                        f"MDD={metrics['strategy_max_drawdown']:.2%} "
                        f"Sharpe={metrics['sharpe']} turnover={metrics['mean_turnover']:.4f}",
                        flush=True,
                    )
                    trial["metrics_sha256"] = digest(metrics)

    (out_root / "summary.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n")
    print(f"[baseline] wrote {len(rows)} cells to {out_root}", flush=True)


if __name__ == "__main__":
    main()
