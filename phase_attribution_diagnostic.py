"""Zero-fit retraining-phase attribution diagnostic.

Reads already-produced phase reports from the Modal Volume, verifies report hashes
and canonical portfolio metrics, computes attribution summaries, and writes one
compact JSON result locally via the local entrypoint. No model fitting is invoked.
"""

from __future__ import annotations

import json
from pathlib import Path

import modal

from phase_attribution_core import (
    annual_attribution,
    frame_sha256,
    pairwise_gap_attribution,
    period_attribution,
    retrain_event_study,
)

APP_NAME = "qlib-zero-fit-phase-attribution"
VOL_NAME = "qlib-cn-data"
VOL_ROOT = Path("/vol")

vol = modal.Volume.from_name(VOL_NAME, create_if_missing=False)
image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("numpy==1.26.4", "pandas==2.2.3", "pyarrow")
    .add_local_file(
        "phase_attribution_core.py",
        remote_path="/root/phase_attribution_core.py",
        copy=True,
    )
)
app = modal.App(APP_NAME, image=image)


def _load_json(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    return json.loads(path.read_text())


def _baseline_path(market: str) -> Path:
    path = VOL_ROOT / "freq_experiment" / f"results_{market}.json"
    if not path.is_file() and market == "csi1000":
        path = VOL_ROOT / "freq_experiment" / "results.json"
    return path


def _phase_path(market: str, freq: int) -> Path:
    return VOL_ROOT / "freq_phase_sensitivity" / f"phase_{market}_freq{freq}.json"


def _parse_phases(phases: str, freq: int) -> list[int]:
    values = sorted({int(x.strip()) for x in phases.split(",") if x.strip()})
    if not values:
        raise ValueError("phases is empty")
    if values[0] < 0 or values[-1] >= freq:
        raise ValueError(f"phases must be within 0..{freq - 1}")
    return values


def _load_and_verify_report(result: dict):
    import pandas as pd

    artifact = result.get("report_artifact") or {}
    path = Path(artifact.get("path", ""))
    if not path.is_file():
        raise RuntimeError(f"missing raw report artifact: {path}")
    report = pd.read_parquet(path)
    expected_rows = int(artifact.get("rows", -1))
    if expected_rows >= 0 and len(report) != expected_rows:
        raise RuntimeError(
            f"report row-count mismatch: path={path} got={len(report)} expected={expected_rows}"
        )
    expected_hash = artifact.get("content_sha256")
    actual_hash = frame_sha256(report)
    if not expected_hash or actual_hash != expected_hash:
        raise RuntimeError(
            f"report content hash mismatch: path={path} got={actual_hash} expected={expected_hash}"
        )
    return report


def _assert_canonical_metrics(phase: int, result: dict, period: dict) -> dict:
    checks = {}
    expected = {
        "strategy_cagr": result.get("strategy_cagr"),
        "relative_excess_cagr": result.get("relative_excess_cagr"),
    }
    actual = {
        "strategy_cagr": period["net_strategy_cagr"],
        "relative_excess_cagr": period["relative_net_cagr"],
    }
    for key in expected:
        if expected[key] is None:
            raise RuntimeError(f"phase {phase} missing canonical metric: {key}")
        delta = abs(float(actual[key]) - float(expected[key]))
        checks[key] = {
            "expected": round(float(expected[key]), 8),
            "recomputed": round(float(actual[key]), 8),
            "abs_error": round(delta, 10),
        }
        if round(float(actual[key]), 6) != round(float(expected[key]), 6):
            raise RuntimeError(
                f"phase {phase} canonical {key} mismatch: "
                f"recomputed={actual[key]} expected={expected[key]}"
            )
    return checks


def _retrain_dates_for_phase(
    phase: int, result: dict, baseline_freq_result: dict
) -> list[str]:
    chunks = result.get("chunk_predictions") or []
    if phase == 0 and not chunks:
        chunks = baseline_freq_result.get("chunk_predictions") or []
    dates = [str(x["retrain_asof"]) for x in chunks if x.get("retrain_asof")]
    if not dates:
        raise RuntimeError(f"phase {phase} has no retrain schedule for event study")
    return dates


@app.function(
    volumes={str(VOL_ROOT): vol},
    cpu=2,
    memory=4096,
    timeout=30 * 60,
)
def zero_fit_phase_attribution_driver(
    market: str = "csi1000",
    freq: int = 20,
    phases: str = "0,4,6,10,15",
    focus_phase: int = 4,
    event_window: int = 5,
):
    """Compute attribution from retained raw reports only. Performs zero fits."""
    import numpy as np

    vol.reload()
    phase_list = _parse_phases(phases, freq)
    if focus_phase not in phase_list:
        raise ValueError("focus_phase must be included in phases")

    source = _load_json(_phase_path(market, freq))
    baseline = _load_json(_baseline_path(market))
    baseline_freq = (baseline.get("results") or {}).get(str(freq)) or {}

    if source.get("protocol") != "retrain_phase_sensitivity_v2_repro":
        raise RuntimeError("source phase artifact is not v2_repro protocol")
    if source.get("market") != market or int(source.get("freq", -1)) != int(freq):
        raise RuntimeError("source phase artifact market/freq mismatch")
    if not (source.get("reproducibility_gate") or {}).get("passed"):
        raise RuntimeError("source phase artifact does not carry a passing gate")
    if source.get("reproducibility") != baseline.get("reproducibility"):
        raise RuntimeError("source phase artifact reproducibility differs from baseline")
    if source.get("reproducibility_gate") != baseline.get("reproducibility_gate"):
        raise RuntimeError("source phase artifact gate differs from baseline")

    available = source.get("results") or {}
    missing = [phase for phase in phase_list if str(phase) not in available]
    if missing:
        raise RuntimeError(f"requested phases missing from source artifact: {missing}")

    phase_outputs = {}
    reports = {}
    for phase in phase_list:
        result = available[str(phase)]
        report = _load_and_verify_report(result)
        period = period_attribution(report)
        metric_checks = _assert_canonical_metrics(phase, result, period)
        retrain_dates = _retrain_dates_for_phase(phase, result, baseline_freq)
        event = retrain_event_study(report, retrain_dates, window=event_window)

        reports[phase] = report
        phase_outputs[str(phase)] = {
            "source": result.get("source"),
            "first_retrain_asof": result.get("first_retrain_asof"),
            "n_retrains": int(result.get("n_retrains", len(retrain_dates))),
            "signal_sha256": result.get("signal_sha256"),
            "report_content_sha256": (result.get("report_artifact") or {}).get(
                "content_sha256"
            ),
            "canonical_metric_checks": metric_checks,
            "period": period,
            "annual": annual_attribution(report),
            "retrain_event_study": event,
        }

    comparator_phases = [phase for phase in phase_list if phase != focus_phase]
    pairwise = {
        str(phase): pairwise_gap_attribution(
            reports[focus_phase], reports[phase]
        )
        for phase in comparator_phases
    }

    focus_period = phase_outputs[str(focus_phase)]["period"]
    comparator_turnover = [
        phase_outputs[str(phase)]["period"]["mean_turnover"]
        for phase in comparator_phases
    ]
    comparator_cost = [
        phase_outputs[str(phase)]["period"]["total_cost_sum"]
        for phase in comparator_phases
    ]
    focus_turnover = float(focus_period["mean_turnover"])
    focus_cost = float(focus_period["total_cost_sum"])
    median_turnover = float(np.median(comparator_turnover))
    median_cost = float(np.median(comparator_cost))

    cost_shares = [
        pairwise[str(phase)]["cost_effect_share_of_net_gap"]
        for phase in comparator_phases
        if pairwise[str(phase)]["cost_effect_share_of_net_gap"] is not None
    ]

    payload = {
        "protocol": "zero_fit_phase_attribution_v1",
        "purpose": "diagnose_phase_dispersion_without_model_refits",
        "zero_model_fits": True,
        "market": market,
        "freq": int(freq),
        "phases": phase_list,
        "focus_phase": int(focus_phase),
        "event_window_sessions": int(event_window),
        "source_phase_protocol": source.get("protocol"),
        "source_extension_version": source.get("extension_version"),
        "reproducibility": source.get("reproducibility"),
        "reproducibility_gate": source.get("reproducibility_gate"),
        "phases_detail": phase_outputs,
        "focus_pairwise_vs": pairwise,
        "summary": {
            "focus_relative_net_cagr": focus_period["relative_net_cagr"],
            "focus_mean_turnover": focus_turnover,
            "other_phase_median_mean_turnover": round(median_turnover, 8),
            "focus_turnover_ratio_to_other_median": round(
                focus_turnover / median_turnover, 6
            )
            if median_turnover
            else None,
            "focus_total_cost_sum": focus_cost,
            "other_phase_median_total_cost_sum": round(median_cost, 8),
            "focus_cost_ratio_to_other_median": round(focus_cost / median_cost, 6)
            if median_cost
            else None,
            "pairwise_cost_effect_share_of_net_gap_median": round(
                float(np.median(cost_shares)), 6
            )
            if cost_shares
            else None,
            "pairwise_cost_effect_share_of_net_gap": {
                str(phase): pairwise[str(phase)][
                    "cost_effect_share_of_net_gap"
                ]
                for phase in comparator_phases
            },
            "pairwise_top2_negative_year_share": {
                str(phase): pairwise[str(phase)]["negative_gap_concentration"][
                    "top2_negative_year_share"
                ]
                for phase in comparator_phases
            },
            "pairwise_top2_negative_years": {
                str(phase): pairwise[str(phase)]["negative_gap_concentration"][
                    "top2_negative_years"
                ]
                for phase in comparator_phases
            },
        },
    }

    output_dir = VOL_ROOT / "freq_phase_attribution"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"phase_{market}_freq{freq}_zero_fit.json"
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    vol.commit()

    return {
        "output_volume_path": str(output_path),
        "payload": payload,
    }


@app.local_entrypoint()
def main(
    market: str = "csi1000",
    freq: int = 20,
    phases: str = "0,4,6,10,15",
    focus_phase: int = 4,
    event_window: int = 5,
):
    result = zero_fit_phase_attribution_driver.remote(
        market=market,
        freq=freq,
        phases=phases,
        focus_phase=focus_phase,
        event_window=event_window,
    )
    payload = result["payload"]

    output_dir = Path("results/freq_phase_attribution")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"phase_{market}_freq{freq}_zero_fit.json"
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))

    print(f"[zero-fit] wrote {output_path}")
    print(f"[zero-fit] volume copy: {result['output_volume_path']}")
    print(json.dumps(payload["summary"], indent=2, ensure_ascii=False))
