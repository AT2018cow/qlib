"""Per-market G1 candidate selection scorecard (three independent accounts).

Reads the frozen baseline/overlay/periodic summaries and applies the
pre-registered R3 gates within each market.  It never combines markets and
never refits a model.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

MARKETS = ("csi300", "csi500", "csi1000")
BENCH = {"csi300": "SH000300", "csi500": "SH000905", "csi1000": "SH000852"}


def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--root", default="quant/data/current_window_backtest")
    p.add_argument("--out", default="quant/data/current_window_backtest/g1_per_market_selection_20261102.json")
    return p.parse_args()


def _load(path: Path):
    if not path.is_file():
        return []
    return json.loads(path.read_text())


def _base_id(spine: str, cell: str) -> str:
    return f"{cell}/{spine}"


def _calmar(cagr, mdd):
    if cagr is None or mdd in (None, 0):
        return None
    return float(cagr) / abs(float(mdd))


def _candidate(
    *,
    market: str,
    cid: str,
    base: str,
    overlay: str,
    cadence: str,
    cagr,
    mdd,
    sharpe,
    turnover,
    cagr10,
    mdd10,
    sharpe10,
    cagr20,
    benchmark_cagr,
    benchmark_mdd,
    baseline_mdd,
    baseline_calmar,
    baseline_sharpe,
) -> dict:
    calmar = _calmar(cagr, mdd)
    calmar10 = _calmar(cagr10, mdd10)
    d1_abs = (float(mdd) - float(baseline_mdd)) * 100.0 >= 12.0 if mdd is not None else False
    d1_bench = float(mdd) >= float(benchmark_mdd) + 0.03
    d1 = bool(d1_abs or d1_bench)
    d3 = (
        cagr is not None
        and calmar is not None
        and sharpe is not None
        and float(cagr) > float(benchmark_cagr) + 0.08
        and calmar > float(baseline_calmar)
        and float(sharpe) > float(baseline_sharpe)
    )
    d7_10 = (
        cagr10 is not None
        and calmar10 is not None
        and sharpe10 is not None
        and float(cagr10) > float(benchmark_cagr) + 0.08
        and calmar10 > float(baseline_calmar)
        and float(sharpe10) > float(baseline_sharpe)
    )
    d7_20 = cagr20 is not None and float(cagr20) > 0.0
    if d1 and d3 and d7_10 and d7_20:
        tier = 1
    elif d1 and d3 and d7_10:
        tier = 2
    elif d1:
        tier = 3
    else:
        tier = 4
    return {
        "market": market,
        "id": cid,
        "base": base,
        "overlay": overlay,
        "cadence": cadence,
        "cagr": cagr,
        "mdd": mdd,
        "sharpe": sharpe,
        "calmar": None if calmar is None else round(calmar, 6),
        "cagr10": cagr10,
        "mdd10": mdd10,
        "sharpe10": sharpe10,
        "cagr20": cagr20,
        "mean_turnover": turnover,
        "d1_abs_12pp": d1_abs,
        "d1_bench_plus3": d1_bench,
        "d1": d1,
        "d3_r3": bool(d3),
        "d7_10": bool(d7_10),
        "d7_20": bool(d7_20),
        "tier": tier,
    }


def main() -> None:
    args = _args()
    root = Path(args.root)
    baseline = _load(root / "g1_baseline_summary.json")
    overlay_s01 = _load(root / "g1_overlay_summary_s01.json")
    overlay_best = _load(root / "g1_overlay_summary_best.json")
    periodic = _load(root / "g1_periodic_summary.json")

    base_by = {(r["market"], r["spine"], r["cell"]): r for r in baseline}
    if not base_by:
        raise ValueError("baseline summary is empty")
    bench = {}
    for market in MARKETS:
        b = base_by.get((market, "A", "S01"))
        if b is None:
            raise ValueError(f"missing S01/A baseline for {market}")
        bench[market] = (float(b["benchmark_cagr"]), float(b["benchmark_max_drawdown"]))

    candidates: dict[str, dict] = {}

    def add(rec: dict) -> None:
        if rec["market"] not in MARKETS:
            return
        candidates.setdefault(f"{rec['market']}|{rec['id']}", rec)

    for r in baseline:
        m = r["market"]
        b_cagr, b_mdd = bench[m]
        base = base_by[(m, "A", "S01")]
        add(
            _candidate(
                market=m,
                cid=_base_id(r["spine"], r["cell"]),
                base=_base_id(r["spine"], r["cell"]),
                overlay="none",
                cadence="daily",
                cagr=r["strategy_cagr"],
                mdd=r["strategy_max_drawdown"],
                sharpe=r["sharpe"],
                turnover=r["mean_turnover"],
                cagr10=r["cagr_cost_plus_10bp"],
                mdd10=r["maxdd_cost_plus_10bp"],
                sharpe10=r["sharpe_cost_plus_10bp"],
                cagr20=r["cagr_cost_plus_20bp"],
                benchmark_cagr=b_cagr,
                benchmark_mdd=b_mdd,
                baseline_mdd=base["strategy_max_drawdown"],
                baseline_calmar=_calmar(base["strategy_cagr"], base["strategy_max_drawdown"]),
                baseline_sharpe=base["sharpe"],
            )
        )

    for summary in (overlay_s01, overlay_best):
        for r in summary:
            m = r.get("market")
            if m not in MARKETS:
                continue
            b_cagr, b_mdd = bench[m]
            base = base_by[(m, "A", "S01")]
            overlay = r["overlay"].replace("_trend_gate", "").replace("_drawdown_breaker", "")
            cid = f"{overlay}[{r['base']}]"
            add(
                _candidate(
                    market=m,
                    cid=cid,
                    base=r["base"],
                    overlay=overlay,
                    cadence="daily",
                    cagr=r["strategy_cagr"],
                    mdd=r["strategy_max_drawdown"],
                    sharpe=r["sharpe"],
                    turnover=r["mean_turnover"],
                    cagr10=r.get("cagr_cost_plus_10bp"),
                    mdd10=r.get("maxdd_cost_plus_10bp"),
                    sharpe10=r.get("sharpe_cost_plus_10bp"),
                    cagr20=r.get("cagr_cost_plus_20bp"),
                    benchmark_cagr=b_cagr,
                    benchmark_mdd=b_mdd,
                    baseline_mdd=base["strategy_max_drawdown"],
                    baseline_calmar=_calmar(base["strategy_cagr"], base["strategy_max_drawdown"]),
                    baseline_sharpe=base["sharpe"],
                )
            )

    for r in periodic:
        m = r["market"]
        if m not in MARKETS or r["cadence"] == "daily":
            continue
        b_cagr, b_mdd = bench[m]
        base = base_by[(m, "A", "S01")]
        base_id = _base_id(r["spine"], r["cell"])
        cid = f"{r['cadence']}[{base_id}]"
        add(
            _candidate(
                market=m,
                cid=cid,
                base=base_id,
                overlay="periodic",
                cadence=r["cadence"],
                cagr=r["strategy_cagr"],
                mdd=r["strategy_max_drawdown"],
                sharpe=r["sharpe"],
                turnover=r["mean_turnover"],
                cagr10=r.get("cagr_cost_plus_10bp"),
                mdd10=r.get("maxdd_cost_plus_10bp"),
                sharpe10=r.get("sharpe_cost_plus_10bp"),
                cagr20=r.get("cagr_cost_plus_20bp"),
                benchmark_cagr=b_cagr,
                benchmark_mdd=b_mdd,
                baseline_mdd=base["strategy_max_drawdown"],
                baseline_calmar=_calmar(base["strategy_cagr"], base["strategy_max_drawdown"]),
                baseline_sharpe=base["sharpe"],
            )
        )

    rows = list(candidates.values())
    order = {m: i for i, m in enumerate(MARKETS)}
    rows.sort(key=lambda r: (order[r["market"]], r["tier"], -(r["cagr10"] or -9), -(r["calmar"] or -9)))
    out = {
        "kind": "g1_per_market_selection",
        "account": "100_000_000 CNY per market, independent accounts",
        "window": "2021-02-19..2024-02-05",
        "holdout": "2025-01-02..2026-09-30 sealed",
        "markets": {},
        "note": "R3 screen only; D2/D3 seven-year and holdout are not evaluated.",
    }
    for market in MARKETS:
        market_rows = [r for r in rows if r["market"] == market]
        out["markets"][market] = {
            "benchmark": BENCH[market],
            "champion": market_rows[0]["id"] if market_rows else None,
            "backup": market_rows[1]["id"] if len(market_rows) > 1 else None,
            "candidates": market_rows,
        }
        print(f"\n[{market}] champion={out['markets'][market]['champion']}")
        for r in market_rows:
            print(
                f"  tier{r['tier']} {r['id']:<22} CAGR={r['cagr']:.2%} MDD={r['mdd']:.2%} "
                f"Sharpe={r['sharpe']:.3f} +10={r['cagr10']:.2%} +20={r['cagr20']:.2%} "
                f"D1={r['d1']} D3={r['d3_r3']} D7={r['d7_10']}/{r['d7_20']}"
            )

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
