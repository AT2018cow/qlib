"""Cost-aware extension driver for deterministic retraining-phase audits.

The canonical phase audit must run inside the Modal app defined by
freq_experiment.py because that driver calls sibling Modal functions
(prepare.remote and freq_window.map). This module therefore keeps orchestration
local: remote helpers only inspect/merge the shared Volume, while the local
entrypoint launches the canonical app with a normal `modal run` subprocess.

freq_experiment.py is intentionally left unchanged because its SHA256 is part
of the passing reproducibility manifest.
"""

from pathlib import Path

import modal

APP_NAME = "phase-audit-extend"
VOL_NAME = "qlib-cn-data"
VOL_ROOT = Path("/vol")

vol = modal.Volume.from_name(VOL_NAME, create_if_missing=True)
image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("numpy==1.26.4", "pandas==2.2.3", "pyarrow")
)
app = modal.App(APP_NAME, image=image)


def _load_json(path):
    import json

    path = Path(path)
    if not path.is_file():
        return None
    return json.loads(path.read_text())


def _baseline_path(market):
    path = VOL_ROOT / "freq_experiment" / f"results_{market}.json"
    if not path.is_file() and market == "csi1000":
        path = VOL_ROOT / "freq_experiment" / "results.json"
    return path


def _validate_baseline(baseline, *, freq, market, topk, nd):
    if not baseline:
        raise RuntimeError("missing reproducibility-gate baseline")
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
    if str(freq) not in (baseline.get("results") or {}):
        raise RuntimeError(f"baseline missing freq={freq}")


def _same_phase_context(payload, baseline, *, freq, eval_from, market, topk, nd):
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


def _is_reusable_phase(result):
    if not result or result.get("source") != "deterministic_refit":
        return False
    return (
        int(result.get("n_retrains", 0)) > 0
        and bool(result.get("signal_sha256"))
        and bool((result.get("report_artifact") or {}).get("content_sha256"))
        and bool(result.get("performance"))
        and result.get("account_return_max_error") == 0
    )


def _frame_sha256(frame):
    """Match freq_experiment._frame_sha256 without importing its Modal app."""
    import hashlib

    text = frame.sort_index().to_csv(
        index=True,
        float_format="%.17g",
        date_format="%Y-%m-%dT%H:%M:%S.%f",
    )
    return hashlib.sha256(text.encode()).hexdigest()


def _enrich_phase_result(result):
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
    expected_content_hash = artifact.get("content_sha256")
    if not expected_content_hash or _frame_sha256(report) != expected_content_hash:
        raise RuntimeError(f"phase report content hash mismatch: {path}")

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


def _phase_metric_summary(results, field):
    import numpy as np

    vals = [float(v[field]) for v in results.values() if v.get(field) is not None]
    if not vals:
        return {
            "n": 0,
            "min": None,
            "q25": None,
            "median": None,
            "q75": None,
            "max": None,
        }
    arr = np.asarray(vals, dtype=float)
    return {
        "n": int(len(arr)),
        "min": round(float(np.min(arr)), 6),
        "q25": round(float(np.quantile(arr, 0.25)), 6),
        "median": round(float(np.median(arr)), 6),
        "q75": round(float(np.quantile(arr, 0.75)), 6),
        "max": round(float(np.max(arr)), 6),
    }


@app.function(volumes={str(VOL_ROOT): vol}, timeout=15 * 60)
def prepare_phase_extension(freq: int, eval_from: str, market: str,
                            topk: int, nd: int, phases: str):
    """Choose and snapshot reusable results before the canonical subset run."""
    import json

    vol.reload()
    requested = sorted({int(x.strip()) for x in phases.split(",") if x.strip()})
    if not requested:
        raise ValueError("phases is empty")
    if requested[0] < 0 or requested[-1] >= freq:
        raise ValueError(f"phases must be within 0..{freq - 1}")

    baseline_path = _baseline_path(market)
    baseline = _load_json(baseline_path)
    _validate_baseline(
        baseline, freq=freq, market=market, topk=topk, nd=nd
    )

    phase_root = VOL_ROOT / "freq_phase_sensitivity"
    phase_path = phase_root / f"phase_{market}_freq{freq}.json"
    preextend_path = phase_root / f"phase_{market}_freq{freq}.preextend.json"

    candidates = []
    for priority, path in enumerate((phase_path, preextend_path)):
        payload = _load_json(path)
        if not _same_phase_context(
            payload,
            baseline,
            freq=freq,
            eval_from=eval_from,
            market=market,
            topk=topk,
            nd=nd,
        ):
            continue
        results = payload.get("results") or {}
        reusable = sorted(
            phase
            for phase in requested
            if phase != 0 and _is_reusable_phase(results.get(str(phase)))
        )
        candidates.append((len(reusable), -priority, path, payload, reusable))

    if candidates:
        _, _, source_path, source_payload, reusable = max(candidates, key=lambda x: (x[0], x[1]))
    else:
        source_path, source_payload, reusable = None, None, []

    missing = [phase for phase in requested if phase != 0 and phase not in reusable]
    run_phases = sorted(set([0] + missing))

    phase_root.mkdir(parents=True, exist_ok=True)
    reuse_snapshot_path = phase_root / f"phase_{market}_freq{freq}.reuse_source.json"
    if source_payload is not None:
        reuse_snapshot_path.write_text(
            json.dumps(source_payload, indent=2, ensure_ascii=False)
        )
    elif reuse_snapshot_path.exists():
        reuse_snapshot_path.unlink()

    plan = {
        "requested_phases": requested,
        "reused_phases": reusable,
        "computed_phases": missing,
        "validation_phases": [0],
        "run_phases": run_phases,
        "reuse_source": str(source_path) if source_path else None,
        "reuse_snapshot": str(reuse_snapshot_path) if source_payload is not None else None,
        "baseline_reproducibility": baseline.get("reproducibility"),
        "baseline_gate": baseline.get("reproducibility_gate"),
    }
    plan_path = phase_root / f"phase_{market}_freq{freq}.extend_plan.json"
    plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False))
    vol.commit()
    return plan


@app.function(volumes={str(VOL_ROOT): vol}, timeout=15 * 60)
def merge_phase_extension(freq: int, eval_from: str, market: str,
                          topk: int, nd: int, phases: str):
    """Merge the canonical fresh subset with the snapshotted reusable phases."""
    import json

    vol.reload()
    requested = sorted({int(x.strip()) for x in phases.split(",") if x.strip()})
    phase_root = VOL_ROOT / "freq_phase_sensitivity"
    phase_path = phase_root / f"phase_{market}_freq{freq}.json"
    plan_path = phase_root / f"phase_{market}_freq{freq}.extend_plan.json"

    plan = _load_json(plan_path)
    if not plan or plan.get("requested_phases") != requested:
        raise RuntimeError("phase extension plan missing or does not match request")

    baseline = _load_json(_baseline_path(market))
    _validate_baseline(
        baseline, freq=freq, market=market, topk=topk, nd=nd
    )
    if plan.get("baseline_reproducibility") != baseline.get("reproducibility"):
        raise RuntimeError("baseline reproducibility changed during phase extension")
    if plan.get("baseline_gate") != baseline.get("reproducibility_gate"):
        raise RuntimeError("reproducibility gate changed during phase extension")

    fresh = _load_json(phase_path)
    if not _same_phase_context(
        fresh,
        baseline,
        freq=freq,
        eval_from=eval_from,
        market=market,
        topk=topk,
        nd=nd,
    ):
        raise RuntimeError("fresh canonical phase artifact context mismatch")

    fresh_results = fresh.get("results") or {}
    run_phases = plan.get("run_phases") or []
    if sorted(int(x) for x in fresh.get("phases", [])) != sorted(run_phases):
        raise RuntimeError(
            f"fresh canonical phases mismatch: got={fresh.get('phases')} expected={run_phases}"
        )

    reuse = None
    if plan.get("reuse_snapshot"):
        reuse = _load_json(Path(plan["reuse_snapshot"]))
        if not _same_phase_context(
            reuse,
            baseline,
            freq=freq,
            eval_from=eval_from,
            market=market,
            topk=topk,
            nd=nd,
        ):
            raise RuntimeError("reusable phase snapshot context mismatch")
    reuse_results = (reuse or {}).get("results") or {}

    reusable = [int(x) for x in plan.get("reused_phases", [])]
    missing = [int(x) for x in plan.get("computed_phases", [])]
    merged_results = {}
    for phase in requested:
        key = str(phase)
        result = reuse_results.get(key) if phase in reusable else fresh_results.get(key)
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
        "total_cost_sum",
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
            "extension_version": "local_orchestration_v2",
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
    phase_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    vol.commit()
    return payload


@app.local_entrypoint()
def extend_phase_sensitivity_driver(
    freq: int = 20,
    eval_from: str = "2021-01-04",
    market: str = "csi1000",
    topk: int = 20,
    nd: int = 2,
    phases: str = "0,4,6,10,15",
):
    """Orchestrate the extension locally so each Modal app hydrates normally."""
    import json
    import shutil
    import subprocess

    plan = prepare_phase_extension.remote(
        freq=freq,
        eval_from=eval_from,
        market=market,
        topk=topk,
        nd=nd,
        phases=phases,
    )
    print(
        "[phase-extend] "
        f"requested={plan['requested_phases']} "
        f"reusable={plan['reused_phases']} "
        f"compute={plan['computed_phases']} "
        "validation=[0]"
    )

    modal_bin = shutil.which("modal")
    if not modal_bin:
        raise RuntimeError("modal CLI not found in local PATH")

    canonical_phases = ",".join(str(x) for x in plan["run_phases"])
    command = [
        modal_bin,
        "run",
        "freq_experiment.py::retrain_phase_sensitivity_driver",
        "--freq",
        str(freq),
        "--eval-from",
        eval_from,
        "--market",
        market,
        "--topk",
        str(topk),
        "--nd",
        str(nd),
        "--phases",
        canonical_phases,
    ]
    print(
        "[phase-extend] launching canonical app for phases="
        f"{canonical_phases}; this is the only model-fitting step"
    )
    subprocess.run(
        command,
        cwd=str(Path(__file__).resolve().parent),
        check=True,
    )

    result = merge_phase_extension.remote(
        freq=freq,
        eval_from=eval_from,
        market=market,
        topk=topk,
        nd=nd,
        phases=phases,
    )
    print(json.dumps(result.get("summary", {}), indent=2))
    print(
        f"reused={result.get('reused_phases')} "
        f"computed={result.get('computed_phases')} "
        f"new_model_fits={result.get('new_model_fits')}"
    )
