"""Real frozen-signal P0/L1-A replay for the CSI1000 layered search (PR-S0).

Reuses the audited Stage-B F1 corrected-replay path: the frozen Stage-B
``signal.parquet`` is replayed with Qlib, first reproducing the legacy
Top20/Drop2 report/decisions byte-for-byte (P0 gate), then re-executing the
same frozen signal under the requested (TopK, DropN) combo with the static F1
quote union. Orders, fees and NAV come from the real backtest output; the
independent metric arithmetic reuses ``audit.independent_stage_b_metrics``.

Cost stress (+10bp/+20bp) reuses the established arithmetic method from
``audit.verify_stage_b_fixed_ledger``: extra cost on frozen fills' notional,
explicitly NOT a re-simulation with new fills.

Qlib is imported lazily inside :func:`backtest_signal` so planning, manifest
and offline validation work without a provider. No training (no ``fit`` call
anywhere in this file), no source writes, no production changes.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from audit.csi1000_layered_search_core import replay_cache_key

SOURCE_MISSING = "STOP_SOURCE_UNAVAILABLE"
REPRO_FAIL = "STOP_P0"
DAYS = 424

FROZEN_RESULT = "quant/data/round1_stage_ab/csi1000_stage_b/stage_b_full_51756897fc752304.json"


def _fail(code: str, why: str) -> ValueError:
    return ValueError(f"{code}: {why}")


def sha_bytes(path: str | Path) -> str:
    """SHA256 of a file's bytes."""
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_frozen_result(repo_root: str | Path) -> Dict[str, Any]:
    """Load the frozen Stage-B result; missing file is STOP_SOURCE_UNAVAILABLE."""
    path = Path(repo_root) / FROZEN_RESULT
    if not path.is_file():
        raise _fail(SOURCE_MISSING, f"frozen Stage-B result unavailable: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def lookup_phase(result: Dict[str, Any], candidate_id: str, phase: int) -> Dict[str, Any]:
    """Find one frozen (candidate, phase) row; absent data is STOP_SOURCE_UNAVAILABLE."""
    for c in result.get("ranked_candidates", []):
        if c.get("candidate_id") == candidate_id:
            for p in c.get("phase_results", []):
                if int(p.get("phase", -1)) == int(phase):
                    return p
    raise _fail(SOURCE_MISSING, f"frozen phase row absent: {candidate_id[:8]} phase{int(phase):02d}")


def resolve_phase_sources(
    snapshot_root: str | Path, phase_row: Dict[str, Any], candidate_id: str, phase: int
) -> Dict[str, Any]:
    """Resolve and SHA-verify the frozen report/signal/decision bytes.

    Missing files raise STOP_SOURCE_UNAVAILABLE (never substitute another
    phase or a retrained signal). SHA mismatches raise a BLOCKED conflict.
    """
    from audit.replay_stage_b_f1 import _checked
    from audit.stage_b_f6_preflight import source_suffix

    root = Path(snapshot_root).resolve()
    if not root.is_dir():
        raise _fail(SOURCE_MISSING, f"original snapshot root unavailable: {root}")
    paths = {}
    for kind, filename in (("report", "report.parquet"), ("signal", "signal.parquet"), ("decision", "decisions.json")):
        meta = phase_row.get(kind + "_artifact") or {}
        if not meta.get("path") or not meta.get("sha256"):
            raise _fail(SOURCE_MISSING, f"frozen {kind} pointer missing for {candidate_id[:8]} phase{phase:02d}")
        relative = source_suffix(meta["path"], phase=int(phase), candidate_id=candidate_id)
        if relative.name != filename:
            raise _fail(SOURCE_MISSING, f"frozen {kind} file name drift: {relative.name}")
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise _fail(SOURCE_MISSING, "source path escaped frozen snapshot root")
        if not path.is_file():
            raise _fail(SOURCE_MISSING, f"missing {kind}: {candidate_id[:8]} phase{phase:02d}")
        if sha_bytes(path) != meta["sha256"]:
            raise ValueError(f"LAYERED_REPLAY_BLOCKED: frozen source bytes differ: {path}")
        _checked(path, meta["sha256"])
        paths[kind] = path
    if not (paths["report"].parent == paths["signal"].parent == paths["decision"].parent):
        raise _fail(SOURCE_MISSING, "phase sources do not share one original directory")
    return {
        "report": paths["report"],
        "signal": paths["signal"],
        "decision": paths["decision"],
        "report_sha256": sha_bytes(paths["report"]),
        "signal_sha256": sha_bytes(paths["signal"]),
        "decision_sha256": sha_bytes(paths["decision"]),
    }


def static_quote_union(market: str, start: str, end: str, provider: Any) -> Tuple[List[str], str]:
    """Static F1 quote union + its SHA256 (codes only, never future signals)."""
    from execution_quote_universe import execution_quote_codes

    codes = execution_quote_codes(market, start, end, provider=provider)
    if not codes:
        raise _fail(SOURCE_MISSING, "static quote union empty")
    return codes, hashlib.sha256(("\n".join(codes) + "\n").encode()).hexdigest()


def backtest_signal(
    signal: Any, topk: int, dropn: int, codes: Any, start: str, end: str
) -> Tuple[Any, List[Dict[str, Any]]]:
    """Real Qlib replay of a frozen signal under one (TopK, DropN) combo.

    Qlib imports stay inside this function so the rest of the module works
    without a provider. Never trains: only ``collect_data`` + strategy replay.
    """
    if not isinstance(topk, int) or not isinstance(dropn, int) or dropn < 1 or dropn > topk:
        raise ValueError(f"LAYERED_REPLAY_BLOCKED: invalid combo TopK={topk} DropN={dropn}")
    from audit.replay_stage_b_f1 import _audit_decisions
    from board_execution import research_exchange
    from csi1000_stage_b_core import BENCHMARK
    from qlib.backtest import collect_data

    result: Dict[str, Any] = {}
    decisions = list(
        collect_data(
            strategy={
                "class": "DeterministicTopkDropoutStrategy",
                "module_path": "deterministic_strategy",
                "kwargs": {"signal": signal, "topk": topk, "n_drop": dropn, "forbid_all_trade_at_limit": False},
            },
            executor={
                "class": "SimulatorExecutor",
                "module_path": "qlib.backtest.executor",
                "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True, "track_data": True},
            },
            start_time=start,
            end_time=end,
            account=100_000_000,
            benchmark=BENCHMARK,
            exchange_kwargs=research_exchange(start, end, codes=codes),
            return_value=result,
        )
    )
    return result["portfolio_dict"]["1day"][0], _audit_decisions(decisions)


def report_metrics(report: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Independent metric arithmetic on a Qlib report; missing NAV columns block."""
    import pandas as pd

    from audit.independent_stage_b_metrics import REQUIRED, metrics

    missing = [c for c in REQUIRED if c not in report.columns]
    if missing:
        raise ValueError(f"LAYERED_REPLAY_BLOCKED: report missing NAV columns {missing}")
    if len(report) != DAYS or not pd.DatetimeIndex(report.index).is_unique:
        raise ValueError(f"LAYERED_REPLAY_BLOCKED: calendar coverage is not {DAYS} unique days")
    for col in REQUIRED:
        vals = report[col].to_numpy(dtype=float)
        if not all(math.isfinite(v) for v in vals):
            raise ValueError(f"LAYERED_REPLAY_BLOCKED: non-finite report column {col}")
    if not all(float(v) > 0 for v in report["account"].to_numpy(dtype=float)):
        raise ValueError("LAYERED_REPLAY_BLOCKED: non-positive account NAV")
    dates = list(pd.DatetimeIndex(report.index).date)
    inputs = {key: report[key].astype(float).to_list() for key in REQUIRED}
    numbers, _, diagnostics, _ = metrics(dates, inputs)
    if diagnostics["max_value_plus_cash_discrepancy"] > 1.0:
        raise ValueError("LAYERED_REPLAY_BLOCKED: cash+value does not reconcile to NAV")
    return numbers, diagnostics


def stress_advantage_pp(chall_report: Any, base_rel_cagr: float, extra_bps: float = 10.0) -> Tuple[float | None, str]:
    """Arithmetic cost stress on frozen fills' notional (established method).

    Stressed NAV = NAV minus cumulative daily notional * bps/10000, with costs
    moved through cash so cash+value still reconciles. Returns the stressed
    challenger relative CAGR minus the baseline relative CAGR, in pp. This is
    NOT a re-simulation: no new fills, no resizing, no slippage forecast.
    """
    import pandas as pd

    from audit.independent_stage_b_metrics import REQUIRED, metrics

    if not isinstance(base_rel_cagr, (int, float)) or not math.isfinite(base_rel_cagr):
        return None, "BLOCKED_BASELINE_CAGR_UNKNOWN"
    nav = chall_report["account"].to_numpy(dtype=float)
    turnover_rate = chall_report["turnover"].to_numpy(dtype=float)
    prior = [100_000_000.0] + list(nav[:-1])
    notionals = [t * p for t, p in zip(turnover_rate, prior)]
    adjustment = 0.0
    stressed_nav, stressed_cash, stressed_cost, stressed_total = [], [], [], []
    for i in range(len(nav)):
        adjustment += notionals[i] * extra_bps / 10000.0
        stressed_nav.append(nav[i] - adjustment)
        stressed_cash.append(float(chall_report["cash"].iloc[i]) - adjustment)
        rate_add = notionals[i] * extra_bps / 10000.0 / prior[i] if prior[i] else 0.0
        stressed_cost.append(float(chall_report["cost"].iloc[i]) + rate_add)
        # Cumulative fees are CNY, NOT the sum of unitless daily fee rates.
        stressed_total.append(float(chall_report["total_cost"].iloc[i]) + adjustment)
    if min(stressed_nav) <= 0:
        return None, "BLOCKED_NEGATIVE_STRESSED_NAV"
    dates = list(pd.DatetimeIndex(chall_report.index).date)
    values = {k: chall_report[k].astype(float).to_list() for k in REQUIRED}
    values["account"] = stressed_nav
    values["cash"] = stressed_cash
    values["cost"] = stressed_cost
    values["total_cost"] = stressed_total
    numbers, _, _, _ = metrics(dates, values)
    return (numbers["relative_excess_cagr"] - base_rel_cagr) * 100.0, "OK"


def verify_corrected_f1_reference(
    repo_root: str | Path,
    candidate_id: str,
    phase: int,
    computed_metrics: Dict[str, Any],
    computed_orders: int,
    quote_sha256: str,
    signal_sha256: str,
) -> Dict[str, Any]:
    """Compare corrected Top20/Drop2 controls with the frozen 55-cell F1 audit.

    This is an independent historical result reference, not a replacement for
    the original private signal and a daily NAV/accounting reconstruction.
    """
    ref_path = Path(repo_root) / "quant/data/round1_audit_evidence/stage_b_corrected_rerank/batch_result.json"
    if not ref_path.is_file():
        raise _fail(SOURCE_MISSING, "Stage-B corrected F1 audit result unavailable")
    result = json.loads(ref_path.read_text(encoding="utf-8"))
    expected_fp = result.get("original_provider_fingerprint")
    frozen = load_frozen_result(repo_root).get("manifest", {})
    if not expected_fp or expected_fp != frozen.get("provider_fingerprint"):
        raise _fail(REPRO_FAIL, "provider fingerprint differs from F1 corrected audit")
    if result.get("static_quote_union_sha256") != quote_sha256:
        raise _fail(REPRO_FAIL, "F1 static quote union differs from frozen corrected audit")
    matches = [
        r
        for r in result.get("phase_comparisons", [])
        if r.get("candidate_id") == candidate_id and int(r.get("phase", -1)) == int(phase)
    ]
    if len(matches) != 1:
        raise _fail(REPRO_FAIL, "not exactly one corrected reference for candidate/phase")
    ref = matches[0]
    if ref.get("frozen_signal_sha256") != signal_sha256:
        raise _fail(REPRO_FAIL, "corrected reference signal bytes differ")
    if int(ref["fixed_orders"]) != computed_orders:
        raise _fail(REPRO_FAIL, f"corrected orders mismatch: {computed_orders} != {ref['fixed_orders']}")
    expected = ref.get("fixed_metrics")
    if not isinstance(expected, dict):
        raise _fail(REPRO_FAIL, "corrected reference metrics absent")
    for metric in (
        "n_days",
        "strategy_total_return",
        "strategy_cagr",
        "relative_excess_cagr",
        "strategy_max_drawdown",
        "mean_turnover",
        "total_cost_sum",
    ):
        if metric not in expected or metric not in computed_metrics:
            raise _fail(REPRO_FAIL, f"corrected metric {metric} missing")
        got, want = float(computed_metrics[metric]), float(expected[metric])
        if (
            not math.isfinite(got)
            or not math.isfinite(want)
            or not math.isclose(got, want, rel_tol=1e-10, abs_tol=1e-10)
        ):
            raise _fail(REPRO_FAIL, f"corrected metric {metric} mismatch: {got} != {want}")
    return {"status": "PASS", "reference_sha256": sha_bytes(ref_path), "reference_orders": computed_orders}


def execute_cell(
    *,
    cell: Dict[str, Any],
    sources: Dict[str, Any],
    signal: Any,
    frozen_report: Any,
    frozen_decisions: List[Dict[str, Any]],
    quote_codes: List[str],
    quote_sha: str,
    market_codes: Any,
    runner: Callable[..., Tuple[Any, List[Dict[str, Any]]]] | None = None,
    reference_validator: Callable[..., Dict[str, Any]] | None = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Execute one cell: legacy reproduction (baseline combos) + fixed replay.

    Returns (record, artifacts) where artifacts maps filenames to bytes-ready
    objects (DataFrames stay as frames for the caller to persist). The record
    carries only real SHAs measured here; output_sha256 is filled by
    :func:`publish_cell` after files land on disk.
    """
    from audit.replay_stage_b_f1 import _compare_original_decisions, _compare_original_report

    run = runner or backtest_signal
    topk, dropn = int(cell["topk"]), int(cell["dropn"])
    is_baseline_combo = topk == 20 and dropn == 2
    from csi1000_stage_b_core import RESERVED_EXECUTION_END, RESERVED_EXECUTION_START

    start, end = RESERVED_EXECUTION_START, RESERVED_EXECUTION_END
    if is_baseline_combo:
        legacy_report, legacy_orders = run(signal, 20, 2, market_codes, start, end)
        try:
            _compare_original_report(frozen_report, legacy_report)
            _compare_original_decisions(frozen_decisions, legacy_orders)
        except ValueError as exc:
            raise ValueError(f"{REPRO_FAIL}: legacy reproduction failed for {cell['cell_key']}: {exc}") from exc
        legacy_status = "PASS"
    else:
        legacy_status = "NOT_APPLICABLE"
    fixed_report, fixed_orders = run(signal, topk, dropn, quote_codes, start, end)
    if not frozen_report.index.equals(fixed_report.index):
        raise ValueError(f"{REPRO_FAIL}: fixed replay changed the frozen date window")
    if len(fixed_orders) != DAYS:
        raise _fail(REPRO_FAIL, "fixed replay decision coverage incomplete")
    numbers, diagnostics = report_metrics(fixed_report)
    order_count = sum(len(d.get("orders", [])) for d in fixed_orders)
    reference = (
        (reference_validator or verify_corrected_f1_reference)(
            Path(__file__).resolve().parents[4],
            cell["candidate_id"],
            int(cell["phase"]),
            numbers,
            order_count,
            quote_sha,
            sources["signal_sha256"],
        )
        if is_baseline_combo
        else {"status": "NOT_APPLICABLE"}
    )
    if diagnostics["max_account_return_delta"] > 1e-10 or diagnostics["max_cost_rate_delta"] > 1e-10:
        raise _fail(REPRO_FAIL, "daily return or fee reconciliation exceeds tolerance")
    record = {
        "status": "PASS",
        "cell_key": cell["cell_key"],
        "candidate_id": cell["candidate_id"],
        "phase": int(cell["phase"]),
        "topk": topk,
        "dropn": dropn,
        "engine": "csi1000_layered_search_v1",
        "execution": "tclose_to_t1open",
        "quote_path": "static_quote_union_f1",
        "quote_sha256": quote_sha,
        "signal_sha256": sources["signal_sha256"],
        "source_sha256": hashlib.sha256(
            (sources["report_sha256"] + sources["signal_sha256"] + sources["decision_sha256"]).encode()
        ).hexdigest(),
        "frozen_report_sha256": sources["report_sha256"],
        "frozen_decision_sha256": sources["decision_sha256"],
        "cache_key": replay_cache_key(sources["signal_sha256"], topk, dropn, quote_sha),
        "output_sha256": "",
        "legacy_reproduction": legacy_status,
        "final_nav_cny": float(fixed_report["account"].iloc[-1]),
        "total_cost_cny": float(fixed_report["total_cost"].iloc[-1]),
        "total_cost_rate_sum": float(numbers["total_cost_sum"]),
        "corrected_reference": reference["status"],
        "corrected_reference_sha256": reference.get("reference_sha256"),
        "total_turnover_cny": float(fixed_report["total_turnover"].iloc[-1]),
        "mean_turnover": float(numbers["mean_turnover"]),
        "n_days": int(numbers["n_days"]),
        "order_count": int(order_count),
        "ledger_ok": diagnostics["max_value_plus_cash_discrepancy"] <= 0.01
        and diagnostics["max_account_return_delta"] <= 1e-10
        and diagnostics["max_cost_rate_delta"] <= 1e-10,
        "coverage_ok": len(fixed_report) == DAYS and fixed_report.index.is_unique,
        "data_ok": bool(quote_codes) and len(fixed_orders) == DAYS,
        "training_performed": False,
        "promotion_authorized": False,
        "metrics": {k: (float(v) if isinstance(v, float) else v) for k, v in numbers.items()},
    }
    artifacts = {"fixed_report": fixed_report, "fixed_orders": fixed_orders}
    return record, artifacts


def daily_frame(fixed_report: Any) -> Any:
    """Derived daily NAV/cost/turnover frame (small, publishable)."""
    import pandas as pd

    out = pd.DataFrame(index=fixed_report.index)
    for col in ("account", "cash", "value", "return", "cost", "total_cost", "total_turnover", "turnover", "bench"):
        out[col] = fixed_report[col].astype(float)
    out.index.name = "datetime"
    return out


def publish_cell(
    out_root: str | Path, record: Dict[str, Any], artifacts: Dict[str, Any], cache_key: str
) -> Dict[str, Any]:
    """Atomically publish one cell; conflicts never overwrite.

    Writes to a temp directory then renames. An existing cell directory with
    identical output bytes is RESUME_VERIFIED; any SHA mismatch is
    FAIL_CONFLICT and raises without touching the original.
    """
    key = record["cell_key"]
    if not record.get("cache_key"):
        record = {
            **record,
            "cache_key": replay_cache_key(
                record["signal_sha256"], record["topk"], record["dropn"], record["quote_sha256"]
            ),
        }
    if cache_key and cache_key != record["cache_key"]:
        raise ValueError(f"LAYERED_REPLAY_BLOCKED: FAIL_CONFLICT for {key}: caller cache key differs")
    cache_key = record["cache_key"]
    record = {**record, "cache_key": cache_key}
    dest = Path(out_root) / key
    if dest.exists():
        existing = json.loads((dest / "cell.json").read_text(encoding="utf-8"))
        if existing.get("cache_key") != cache_key or existing.get("source_sha256") != record["source_sha256"]:
            raise ValueError(f"LAYERED_REPLAY_BLOCKED: FAIL_CONFLICT for {key}: refusing to overwrite")
        artifact_hashes = existing.get("artifact_sha256")
        required = ("daily_nav.csv", "fixed_report.parquet", "fixed_decisions.json", "metrics.json")
        if not isinstance(artifact_hashes, dict) or set(artifact_hashes) != set(required):
            raise ValueError(f"LAYERED_REPLAY_BLOCKED: FAIL_CONFLICT for {key}: artifact manifest incomplete")
        for name in required:
            if not (dest / name).is_file() or sha_bytes(dest / name) != artifact_hashes[name]:
                raise ValueError(f"LAYERED_REPLAY_BLOCKED: FAIL_CONFLICT for {key}: {name} bytes drifted")
        if existing.get("output_sha256") != artifact_hashes["daily_nav.csv"]:
            raise ValueError(f"LAYERED_REPLAY_BLOCKED: FAIL_CONFLICT for {key}: NAV SHA drifted")
        return {**existing, "resumed": "RESUME_VERIFIED"}
    tmp = dest.parent / (key + ".tmp")
    if tmp.exists():
        import shutil

        shutil.rmtree(tmp)
    tmp.mkdir(parents=True, exist_ok=False)
    daily_frame(artifacts["fixed_report"]).to_csv(tmp / "daily_nav.csv")
    artifacts["fixed_report"].to_parquet(tmp / "fixed_report.parquet")
    (tmp / "fixed_decisions.json").write_text(
        json.dumps(artifacts["fixed_orders"], indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    (tmp / "metrics.json").write_text(
        json.dumps(record["metrics"], indent=2, allow_nan=False, sort_keys=True) + "\n", encoding="utf-8"
    )
    artifact_sha256 = {
        name: sha_bytes(tmp / name)
        for name in ("daily_nav.csv", "fixed_report.parquet", "fixed_decisions.json", "metrics.json")
    }
    out_sha = artifact_sha256["daily_nav.csv"]
    final = {**record, "output_sha256": out_sha, "artifact_sha256": artifact_sha256}
    (tmp / "cell.json").write_text(
        json.dumps(final, indent=2, allow_nan=False, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(tmp, dest)
    return final


def paired_rows(
    chall_cells: List[Dict[str, Any]],
    base_cells: List[Dict[str, Any]],
    report_loader: Callable[[Dict[str, Any]], Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], float]:
    """Build paired decision rows from executed cell records (recomputed, never trusted).

    The challenger ``net_relative_cagr_pp`` row field is already the paired
    improvement (challenger minus same-phase baseline, in pp); baseline rows
    carry 0.0 so :func:`core._paired_deltas` re-derives the same improvement.
    Cost stress is recomputed here from the challenger's daily frame plus the
    baseline relative CAGR. Returns (challenger_rows, baseline_rows,
    turnover_ratio).
    """
    base_by_phase = {c["phase"]: c for c in base_cells}
    chall_rows, base_rows = [], []
    for c in chall_cells:
        b = base_by_phase.get(c["phase"])
        if b is None:
            raise ValueError(f"LAYERED_REPLAY_BLOCKED: baseline cell missing for phase {c['phase']}")
        if not b.get("mean_turnover") or b["mean_turnover"] <= 0:
            raise ValueError("LAYERED_REPLAY_BLOCKED: baseline turnover invalid")
        report = report_loader(c)
        adv, _ = stress_advantage_pp(report, b["metrics"]["relative_excess_cagr"], 10.0)
        adv20, _ = stress_advantage_pp(report, b["metrics"]["relative_excess_cagr"], 20.0)
        chall_rows.append(
            {
                "phase": c["phase"],
                "net_relative_cagr_pp": (c["metrics"]["relative_excess_cagr"] - b["metrics"]["relative_excess_cagr"])
                * 100.0,
                "net_total_return": c["metrics"]["strategy_total_return"],
                "mdd_pp": abs(float(c["metrics"]["strategy_max_drawdown"])) * 100.0,
                "n_days": c["n_days"],
                "turnover": c["mean_turnover"],
                "coverage_ok": c["coverage_ok"],
                "ledger_ok": c["ledger_ok"],
                "data_ok": c["data_ok"],
                "plus10bp_advantage_pp": adv,
                "plus20bp_advantage_pp": adv20,
            }
        )
        base_rows.append(
            {
                "phase": b["phase"],
                "net_relative_cagr_pp": 0.0,
                "net_total_return": b["metrics"]["strategy_total_return"],
                "mdd_pp": abs(float(b["metrics"]["strategy_max_drawdown"])) * 100.0,
                "n_days": b["n_days"],
                "turnover": b["mean_turnover"],
                "coverage_ok": b["coverage_ok"],
                "ledger_ok": b["ledger_ok"],
                "data_ok": b["data_ok"],
                "plus10bp_advantage_pp": 0.0,
            }
        )
    # Both legs must have all required phases. Compute independent medians
    # rather than a max of per-phase ratios; §1.2 explicitly uses medians.
    from statistics import median

    if not chall_rows or not base_rows:
        raise ValueError("LAYERED_REPLAY_BLOCKED: empty paired turnover evidence")
    med_base = median(row["turnover"] for row in base_rows)
    med_chall = median(row["turnover"] for row in chall_rows)
    if not math.isfinite(med_base) or med_base <= 0 or not math.isfinite(med_chall) or med_chall < 0:
        raise ValueError("LAYERED_REPLAY_BLOCKED: median turnover invalid")
    return chall_rows, base_rows, med_chall / med_base
