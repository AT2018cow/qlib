"""Zero-fit regime × model-age attribution for retraining-phase sensitivity.

This diagnostic reads existing raw reports and retrain lineage only. It performs no
model training, no signal generation, and no portfolio backtest.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import modal

from phase_regime_age_core import (
    attribution_windows,
    benchmark_regime_by_year,
    decision_summary,
    market_regime_frame,
    model_age_frame,
    pairwise_daily_frame,
)

APP_NAME = "qlib-zero-fit-regime-model-age"
VOL_NAME = "qlib-cn-data"
VOL_ROOT = Path("/vol")
DATA_DIR = VOL_ROOT / "cn_data"

vol = modal.Volume.from_name(VOL_NAME, create_if_missing=False)
image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("numpy==1.26.4", "pandas==2.2.3", "pyarrow")
    .add_local_file(
        "phase_regime_age_core.py",
        remote_path="/root/phase_regime_age_core.py",
        copy=True,
    )
)
app = modal.App(APP_NAME, image=image)


def _load_json(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    return json.loads(path.read_text())


def _frame_sha256(frame) -> str:
    text = frame.sort_index().to_csv(
        index=True,
        float_format="%.17g",
        date_format="%Y-%m-%dT%H:%M:%S.%f",
    )
    return hashlib.sha256(text.encode()).hexdigest()


def _read_calendar() -> list[str]:
    path = DATA_DIR / "calendars" / "day.txt"
    if not path.is_file():
        raise FileNotFoundError(path)
    days = [line.strip()[:10] for line in path.read_text().splitlines() if line.strip()]
    if not days or days != sorted(set(days)):
        raise RuntimeError(f"invalid trading calendar: {path}")
    return days


def _load_report(result: dict):
    import pandas as pd

    artifact = result.get("report_artifact") or {}
    path = Path(artifact.get("path", ""))
    if not path.is_file():
        raise RuntimeError(f"missing phase report: {path}")
    report = pd.read_parquet(path).sort_index()
    expected_rows = int(artifact.get("rows", -1))
    if expected_rows >= 0 and len(report) != expected_rows:
        raise RuntimeError(
            f"report row mismatch: {path} got={len(report)} expected={expected_rows}"
        )
    expected_hash = artifact.get("content_sha256")
    actual_hash = _frame_sha256(report)
    if not expected_hash or actual_hash != expected_hash:
        raise RuntimeError(
            f"report content hash mismatch: {path} got={actual_hash} expected={expected_hash}"
        )
    return report


def _retrain_dates(phase: int, result: dict, baseline_freq_result: dict) -> list[str]:
    chunks = result.get("chunk_predictions") or []
    if phase == 0 and not chunks:
        chunks = baseline_freq_result.get("chunk_predictions") or []
    dates = [str(x["retrain_asof"])[:10] for x in chunks if x.get("retrain_asof")]
    if not dates:
        raise RuntimeError(f"phase {phase} missing retrain lineage")
    return dates


@app.function(
    volumes={str(VOL_ROOT): vol},
    cpu=2,
    memory=4096,
    timeout=30 * 60,
)
def regime_model_age_driver(
    market: str = "csi1000",
    freq: int = 20,
    phases: str = "0,4,6,10,15",
    focus_phase: int = 4,
    regime_years: str = "2022,2023",
    lookback: int = 20,
):
    """Run pure attribution over existing artifacts. Expected model fits: zero."""
    import pandas as pd

    vol.reload()
    requested = sorted({int(x.strip()) for x in phases.split(",") if x.strip()})
    years = tuple(sorted({int(x.strip()) for x in regime_years.split(",") if x.strip()}))
    if not requested:
        raise ValueError("phases is empty")
    if focus_phase not in requested:
        raise ValueError("focus_phase must be included in phases")
    if not years:
        raise ValueError("regime_years is empty")

    sensitivity_path = (
        VOL_ROOT / "freq_phase_sensitivity" / f"phase_{market}_freq{freq}.json"
    )
    attribution_path = (
        VOL_ROOT
        / "freq_phase_attribution"
        / f"phase_{market}_freq{freq}_zero_fit.json"
    )
    baseline_path = VOL_ROOT / "freq_experiment" / f"results_{market}.json"
    if not baseline_path.is_file() and market == "csi1000":
        baseline_path = VOL_ROOT / "freq_experiment" / "results.json"

    sensitivity = _load_json(sensitivity_path)
    prior_attribution = _load_json(attribution_path)
    baseline = _load_json(baseline_path)
    baseline_freq = (baseline.get("results") or {}).get(str(freq)) or {}

    if sensitivity.get("protocol") != "retrain_phase_sensitivity_v2_repro":
        raise RuntimeError("unexpected phase sensitivity protocol")
    if prior_attribution.get("protocol") != "zero_fit_phase_attribution_v1":
        raise RuntimeError("missing prior zero-fit attribution prerequisite")
    if prior_attribution.get("zero_model_fits") is not True:
        raise RuntimeError("prior attribution is not marked zero-fit")
    if sensitivity.get("reproducibility") != prior_attribution.get("reproducibility"):
        raise RuntimeError("phase sensitivity and prior attribution manifest mismatch")
    if sensitivity.get("reproducibility_gate") != prior_attribution.get(
        "reproducibility_gate"
    ):
        raise RuntimeError("phase sensitivity and prior attribution gate mismatch")
    if sensitivity.get("reproducibility") != baseline.get("reproducibility"):
        raise RuntimeError("phase sensitivity and baseline manifest mismatch")

    available = sensitivity.get("results") or {}
    missing = [phase for phase in requested if str(phase) not in available]
    if missing:
        raise RuntimeError(f"requested phases missing: {missing}")

    prior_detail = prior_attribution.get("phases_detail") or {}
    calendar = _read_calendar()
    reports = {}
    ages = {}
    retrain_lineage = {}

    for phase in requested:
        result = available[str(phase)]
        prior = prior_detail.get(str(phase)) or {}
        expected_report_hash = prior.get("report_content_sha256")
        current_report_hash = (result.get("report_artifact") or {}).get("content_sha256")
        if not expected_report_hash or expected_report_hash != current_report_hash:
            raise RuntimeError(
                f"phase {phase} report hash differs from prior zero-fit attribution"
            )
        if prior.get("signal_sha256") != result.get("signal_sha256"):
            raise RuntimeError(
                f"phase {phase} signal hash differs from prior zero-fit attribution"
            )

        report = _load_report(result)
        dates = _retrain_dates(phase, result, baseline_freq)
        age = model_age_frame(
            report.index,
            calendar=calendar,
            retrain_dates=dates,
            freq=freq,
        )
        reports[phase] = report
        ages[phase] = age
        retrain_lineage[str(phase)] = {
            "n_retrains": int(len(dates)),
            "first_retrain_asof": dates[0],
            "last_retrain_asof": dates[-1],
            "model_age_min": int(age["model_age_sessions"].min()),
            "model_age_max": int(age["model_age_sessions"].max()),
        }

    focus_report = reports[focus_phase]
    regime = market_regime_frame(focus_report, lookback=lookback)

    # Benchmark must be identical across all phase reports for market-regime attribution.
    reference_bench = focus_report["bench"].astype(float)
    for phase in requested:
        if phase == focus_phase:
            continue
        candidate = reports[phase]["bench"].astype(float)
        if not reference_bench.index.equals(candidate.index):
            raise RuntimeError(f"phase {phase} benchmark index mismatch")
        if not reference_bench.equals(candidate):
            max_err = float((reference_bench - candidate).abs().max())
            if max_err > 1e-15:
                raise RuntimeError(
                    f"phase {phase} benchmark values differ; max_abs_error={max_err}"
                )

    pairwise = {}
    for phase in requested:
        if phase == focus_phase:
            continue
        daily = pairwise_daily_frame(
            focus_report,
            reports[phase],
            focus_age=ages[focus_phase],
            comparator_age=ages[phase],
            regime=regime,
        )
        pairwise[str(phase)] = attribution_windows(
            daily,
            regime_years=years,
        )

    payload = {
        "protocol": "zero_fit_regime_model_age_v1",
        "purpose": "diagnose_phase_x_regime_and_model_age_mechanism",
        "zero_model_fits": True,
        "zero_signal_generation": True,
        "zero_new_backtests": True,
        "market": market,
        "freq": int(freq),
        "phases": requested,
        "focus_phase": int(focus_phase),
        "regime_years": list(years),
        "regime_lookback_sessions": int(lookback),
        "regime_feature_timing": "execution_day_labels_use_benchmark_data_through_prior_execution_session",
        "reproducibility": sensitivity.get("reproducibility"),
        "reproducibility_gate": sensitivity.get("reproducibility_gate"),
        "source_phase_extension_version": sensitivity.get("extension_version"),
        "source_zero_fit_protocol": prior_attribution.get("protocol"),
        "retrain_lineage": retrain_lineage,
        "benchmark_regime_by_year": benchmark_regime_by_year(regime),
        "pairwise_focus_vs": pairwise,
        "summary": decision_summary(pairwise),
    }

    output_dir = VOL_ROOT / "freq_phase_regime_age"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"phase_{market}_freq{freq}_regime_age_zero_fit.json"
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
    regime_years: str = "2022,2023",
    lookback: int = 20,
):
    result = regime_model_age_driver.remote(
        market=market,
        freq=freq,
        phases=phases,
        focus_phase=focus_phase,
        regime_years=regime_years,
        lookback=lookback,
    )
    payload = result["payload"]

    output_dir = Path("results/freq_phase_regime_age")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"phase_{market}_freq{freq}_regime_age_zero_fit.json"
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))

    print(f"[zero-fit-regime-age] wrote {output_path}")
    print(f"[zero-fit-regime-age] volume copy: {result['output_volume_path']}")
    print(json.dumps(payload["summary"], indent=2, ensure_ascii=False))
