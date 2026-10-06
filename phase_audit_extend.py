"""Cost-aware extension driver for deterministic retraining-phase audits.

This module deliberately leaves freq_experiment.py unchanged because that file's
SHA256 is part of the reproducibility manifest. It reuses already-validated
non-zero phase results when their experiment context exactly matches the passing
gate, runs only missing phases through the canonical driver, then merges and
enriches the final audit artifact.
"""

from pathlib import Path

from freq_experiment import (
    VOL_ROOT,
    _phase_metric_summary,
    app,
    retrain_phase_sensitivity_driver,
    vol,
)


def _load_json(path: Path):
    import json

    if not path.is_file():
        return None
    return json.loads(path.read_text())


def _same_phase_context(payload: dict | None, baseline: dict, *, freq: int,
                        eval_from: str, market: str, topk: int, nd: int) -> bool:
    """Return True only for an artifact that is safe to reuse."""
    if not payload:
        return False
    gate = payload.get("reproducibility_gate") or {}
    return (
        payload.get("protocol") == "retrain_phase_sensitivity_v2_repro"
        and payload.get("market") == market
        and int(payload.get("freq", -1)) == int(freq)
        and int(payload.get("topk", -1)) == int(topk)
        and int(payload.get("n_drop", -1)) == int(nd)
        and payload.get("eval_from") == eval_from
        and payload.get("reproducibility") == baseline.get("reproducibility")
        and gate.get("passed") is True
        and gate == (baseline.get("reproducibility_gate") or {})
    )


def _is_reusable_phase(result: dict | None) -> bool:
    """Accept only a completed deterministic non-zero phase lineage."""
    if not result or result.get("source") != "deterministic_refit":
        return False
    return (
        int(result.get("n_retrains", 0)) > 0
        and bool(result.get("signal_sha256"))
        and bool((result.get("report_artifact") or {}).get("content_sha256"))
        and bool(result.get("performance"))
        and result.get("account_return_max_error") == 0
    )


def _enrich_phase_result(result: dict) -> dict:
    """Add the reporting fields required by the pre-tuner audit."""
    import pandas as pd

    enriched = dict(result)
    perf = enriched.get("performance") or {}
    for key in ("benchmark_max_drawdown", "annual_volatility"):
        if key in perf:
            enriched[key] = perf[key]

    artifact = enriched.get("report_artifact") or {}
    path = Path(artifact.get("path", ""))
    if not path.is_file():
        raise RuntimeError(f"phase report artifact missing: {path}")

    report = pd.read_parquet(path)
    turnover_col = next(
        (name for name in ("turnover", "total_turnover") if name in report.columns),
        None,
    )
    if turnover_col is None:
        raise RuntimeError(f"phase report missing turnover column: {path}")
    turnover = report[turnover_col].dropna()
    cost = report["cost"].dropna() if "cost" in report.columns else None
    if turnover.empty:
        raise RuntimeError(f"phase report has empty turnover series: {path}")
    if cost is None or cost.empty:
        raise RuntimeError(f"phase report has empty cost series: {path}")

    enriched["mean_turnover"] = round(float(turnover.mean()), 6)
    enriched["turnover_source"] = turnover_col
    enriched["total_cost_sum"] = round(float(cost.sum()), 6)
    return enriched


@app.function(volumes={str(VOL_ROOT): vol}, cpu=2, memory=4096, timeout=24 * 3600)
def extend_phase_sensitivity_driver(
    freq: int = 20,
    eval_from: str = "2021-01-04",
    market: str = "csi1000",
    topk: int = 20,
    nd: int = 2,
    phases: str = "0,4,6,10,15",
):
    """Extend a passing phase audit without refitting phases already on disk.

    Phase 0 is always sent through the canonical driver as a zero-fit validation
    path. Existing non-zero phases are reused only when the frozen experiment
    context and reproducibility gate exactly match the baseline. Missing phases
    are fitted by retrain_phase_sensitivity_driver, after which all requested
    results are merged into the canonical phase JSON.
    """
    import json

    requested = sorted({int(x.strip()) for x in phases.split(",") if x.strip()})
    if not requested:
        raise ValueError("phases is empty")
    if requested[0] < 0 or requested[-1] >= freq:
        raise ValueError(f"phases must be within 0..{freq - 1}")

    phase_root = VOL_ROOT / "freq_phase_sensitivity"
    phase_path = phase_root / f"phase_{market}_freq{freq}.json"
    baseline_path = VOL_ROOT / "freq_experiment" / f"results_{market}.json"
    if not baseline_path.is_file() and market == "csi1000":
        baseline_path = VOL_ROOT / "freq_experiment" / "results.json"
    baseline = _load_json(baseline_path)
    if not baseline:
        raise RuntimeError(
            f"missing baseline result {baseline_path}; run reproducibility_gate_driver first"
        )
    if baseline.get("protocol") != "continuous_account_board_aware_v5_repro":
        raise RuntimeError("baseline protocol is not v5 reproducible protocol")
    if baseline.get("market") != market:
        raise RuntimeError("baseline market mismatch")
    if int(baseline.get("topk", -1)) != int(topk) or int(
        baseline.get("n_drop", -1)
    ) != int(nd):
        raise RuntimeError("baseline topk/n_drop mismatch")
    if not (baseline.get("reproducibility_gate") or {}).get("passed"):
        raise RuntimeError("phase extension requires a passing reproducibility gate")

    existing = _load_json(phase_path)
    context_ok = _same_phase_context(
        existing,
        baseline,
        freq=freq,
        eval_from=eval_from,
        market=market,
        topk=topk,
        nd=nd,
    )

    reusable = []
    if context_ok:
        existing_results = existing.get("results") or {}
        reusable = [
            phase
            for phase in requested
            if phase != 0 and _is_reusable_phase(existing_results.get(str(phase)))
        ]
    missing = [phase for phase in requested if phase != 0 and phase not in reusable]

    if existing and reusable:
        phase_root.mkdir(parents=True, exist_ok=True)
        backup_path = phase_root / f"phase_{market}_freq{freq}.preextend.json"
        backup_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False))
        vol.commit()

    # Always include phase 0: the canonical driver uses it as a no-fit gate/manifest
    # validation path. Only missing non-zero phases trigger model fits.
    run_phases = sorted(set([0] + missing))
    print(
        f"[phase-extend] requested={requested} reusable={reusable} "
        f"compute={missing} validation=[0]"
    )
    fresh = retrain_phase_sensitivity_driver.remote(
        freq=freq,
        eval_from=eval_from,
        market=market,
        topk=topk,
        nd=nd,
        phases=",".join(str(x) for x in run_phases),
        require_repro_gate=True,
    )
    vol.reload()

    if fresh.get("reproducibility") != baseline.get("reproducibility"):
        raise RuntimeError("canonical phase run no longer matches gate reproducibility manifest")
    if context_ok and existing.get("reproducibility") != fresh.get("reproducibility"):
        raise RuntimeError("existing phase artifact manifest differs from fresh canonical run")

    fresh_results = fresh.get("results") or {}
    existing_results = (existing or {}).get("results") or {}
    merged_results = {}
    for phase in requested:
        key = str(phase)
        if phase in reusable:
            result = existing_results[key]
        else:
            result = fresh_results.get(key)
        if result is None:
            raise RuntimeError(f"missing requested phase result after extension: {phase}")
        merged_results[key] = _enrich_phase_result(result)

    metric_fields = [
        "strategy_cagr",
        "benchmark_cagr",
        "relative_excess_cagr",
        "strategy_max_drawdown",
        "benchmark_max_drawdown",
        "relative_max_drawdown",
        "sharpe",
        "information_ratio",
        "annual_volatility",
        "mean_turnover",
    ]
    summary = {
        field: _phase_metric_summary(merged_results, field) for field in metric_fields
    }
    summary["strategy_cagr_range_pp"] = round(
        (summary["strategy_cagr"]["max"] - summary["strategy_cagr"]["min"]) * 100,
        3,
    )
    summary["relative_excess_cagr_range_pp"] = round(
        (
            summary["relative_excess_cagr"]["max"]
            - summary["relative_excess_cagr"]["min"]
        )
        * 100,
        3,
    )

    payload = dict(fresh)
    payload.update(
        {
            "phases": requested,
            "complete_phase_grid": requested == list(range(freq)),
            "phase0_reused_from_repro_gate": 0 in requested,
            "extension_version": "reuse_missing_phases_v1",
            "reused_phases": reusable,
            "computed_phases": missing,
            "validation_phases": [0],
            "new_model_fits": sum(
                int(merged_results[str(phase)]["n_retrains"]) for phase in missing
            ),
            "results": merged_results,
            "summary": summary,
        }
    )
    phase_root.mkdir(parents=True, exist_ok=True)
    phase_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    vol.commit()
    print(
        f"[phase-extend] merged phases={requested}; reused={reusable}; "
        f"computed={missing}; new_model_fits={payload['new_model_fits']}"
    )
    return payload
