"""Validation helpers for ChiNext/STAR pre-tuner gate + phase screens."""

from __future__ import annotations

FROZEN_AUDIT_CONFIGS = {
    "chinext": {"topk": 20, "nd": 3},
    "star": {"topk": 50, "nd": 2},
}

SCREEN_PHASES = [0, 5, 10, 15]

_REQUIRED_METRICS = (
    "strategy_cagr",
    "benchmark_cagr",
    "relative_excess_cagr",
    "strategy_max_drawdown",
    "benchmark_max_drawdown",
    "relative_max_drawdown",
    "sharpe",
    "information_ratio",
    "annual_volatility",
    "account_return_max_error",
)


def frozen_audit_config(market: str) -> dict:
    if market not in FROZEN_AUDIT_CONFIGS:
        raise ValueError(
            f"market must be one of {sorted(FROZEN_AUDIT_CONFIGS)}; got {market!r}"
        )
    return dict(FROZEN_AUDIT_CONFIGS[market])


def _require_metrics(result: dict, *, label: str) -> None:
    missing = [key for key in _REQUIRED_METRICS if result.get(key) is None]
    if missing:
        raise RuntimeError(f"{label}: missing canonical metrics {missing}")
    if abs(float(result["account_return_max_error"])) > 1e-12:
        raise RuntimeError(
            f"{label}: account_return_max_error={result['account_return_max_error']}"
        )


def validate_gate_payload(
    payload: dict,
    *,
    market: str,
    freq: int,
    eval_from: str,
    topk: int,
    nd: int,
) -> dict:
    """Fail closed unless a market-scoped v5 double-fit gate fully passed."""
    if payload.get("protocol") != "continuous_account_board_aware_v5_repro":
        raise RuntimeError("gate: unexpected protocol")
    if payload.get("market") != market:
        raise RuntimeError("gate: market mismatch")
    if int(payload.get("topk", -1)) != int(topk):
        raise RuntimeError("gate: topk mismatch")
    if int(payload.get("n_drop", -1)) != int(nd):
        raise RuntimeError("gate: n_drop mismatch")
    if not str(payload.get("window", "")).startswith(f"{eval_from}~"):
        raise RuntimeError("gate: eval window mismatch")

    repro = payload.get("reproducibility") or {}
    if repro.get("market") != market:
        raise RuntimeError("gate: reproducibility market mismatch")
    if not repro.get("provider_fingerprint"):
        raise RuntimeError("gate: missing provider fingerprint")
    if not repro.get("snapshot_token"):
        raise RuntimeError("gate: missing snapshot token")
    if not repro.get("freq_experiment_source_sha256"):
        raise RuntimeError("gate: missing canonical source hash")

    gate = payload.get("reproducibility_gate") or {}
    required_true = (
        "passed",
        "prediction_chunks_match",
        "signal_hash_match",
        "report_hash_match",
    )
    failed = [key for key in required_true if gate.get(key) is not True]
    if failed:
        raise RuntimeError(f"gate: reproducibility checks failed/missing {failed}")
    if gate.get("gate_version") != "phase0_double_fit_v1":
        raise RuntimeError("gate: unexpected gate version")

    signal_a = gate.get("repeat_a_signal_sha256")
    signal_b = gate.get("repeat_b_signal_sha256")
    report_a = gate.get("repeat_a_report_content_sha256")
    report_b = gate.get("repeat_b_report_content_sha256")
    if not signal_a or signal_a != signal_b:
        raise RuntimeError("gate: repeat signal hashes differ")
    if not report_a or report_a != report_b:
        raise RuntimeError("gate: repeat report hashes differ")

    result = (payload.get("results") or {}).get(str(freq))
    if not result:
        raise RuntimeError(f"gate: missing freq={freq} canonical result")
    _require_metrics(result, label="gate phase0")

    if result.get("signal_sha256") != signal_a:
        raise RuntimeError("gate: canonical signal hash differs from repeats")
    if int(result.get("n_retrains", -1)) != int(
        gate.get("n_retrains_per_repeat", -2)
    ):
        raise RuntimeError("gate: retrain count mismatch")
    artifact = result.get("report_artifact") or {}
    if artifact.get("content_sha256") != report_a:
        raise RuntimeError("gate: canonical report hash differs from repeats")

    return result


def validate_screen_payload(
    payload: dict,
    gate_payload: dict,
    *,
    market: str,
    freq: int,
    eval_from: str,
    topk: int,
    nd: int,
) -> dict:
    """Validate the frozen 0/5/10/15 screen against the passing gate."""
    gate_result = validate_gate_payload(
        gate_payload,
        market=market,
        freq=freq,
        eval_from=eval_from,
        topk=topk,
        nd=nd,
    )

    if payload.get("protocol") != "retrain_phase_sensitivity_v2_repro":
        raise RuntimeError("screen: unexpected protocol")
    if payload.get("market") != market:
        raise RuntimeError("screen: market mismatch")
    if int(payload.get("freq", -1)) != int(freq):
        raise RuntimeError("screen: freq mismatch")
    if int(payload.get("topk", -1)) != int(topk):
        raise RuntimeError("screen: topk mismatch")
    if int(payload.get("n_drop", -1)) != int(nd):
        raise RuntimeError("screen: n_drop mismatch")
    if payload.get("eval_from") != eval_from:
        raise RuntimeError("screen: eval_from mismatch")
    if [int(x) for x in payload.get("phases", [])] != SCREEN_PHASES:
        raise RuntimeError(
            f"screen: expected phases={SCREEN_PHASES}, got={payload.get('phases')}"
        )
    if payload.get("complete_phase_grid") is not False:
        raise RuntimeError("screen: this audit must remain a 4-phase screen")
    if payload.get("phase0_reused_from_repro_gate") is not True:
        raise RuntimeError("screen: phase0 was not reused from gate")

    if payload.get("reproducibility") != gate_payload.get("reproducibility"):
        raise RuntimeError("screen: reproducibility manifest differs from gate")
    if payload.get("reproducibility_gate") != gate_payload.get(
        "reproducibility_gate"
    ):
        raise RuntimeError("screen: gate payload differs from baseline")

    results = payload.get("results") or {}
    expected_keys = {str(x) for x in SCREEN_PHASES}
    if set(results) != expected_keys:
        raise RuntimeError(
            f"screen: result phases={sorted(results)} expected={sorted(expected_keys)}"
        )

    phase0 = results["0"]
    if phase0.get("source") != "repro_gate_baseline_reuse":
        raise RuntimeError("screen: phase0 source is not gate reuse")
    if phase0.get("signal_sha256") != gate_result.get("signal_sha256"):
        raise RuntimeError("screen: phase0 signal differs from gate")
    if (phase0.get("report_artifact") or {}).get("content_sha256") != (
        gate_result.get("report_artifact") or {}
    ).get("content_sha256"):
        raise RuntimeError("screen: phase0 report differs from gate")

    for phase in SCREEN_PHASES:
        result = results[str(phase)]
        _require_metrics(result, label=f"screen phase {phase}")
        for extra in ("mean_turnover", "total_cost_sum"):
            if result.get(extra) is None:
                raise RuntimeError(f"screen phase {phase}: missing {extra}")
        if phase != 0:
            if result.get("source") != "deterministic_refit":
                raise RuntimeError(
                    f"screen phase {phase}: expected deterministic_refit source"
                )
            if int(result.get("n_retrains", 0)) <= 0:
                raise RuntimeError(f"screen phase {phase}: missing retrain lineage")
            if not result.get("chunk_predictions"):
                raise RuntimeError(f"screen phase {phase}: missing chunk hashes")
            if not result.get("signal_sha256"):
                raise RuntimeError(f"screen phase {phase}: missing signal hash")

    return results


def compact_audit_summary(gate_payload: dict, screen_payload: dict, *, freq: int) -> dict:
    gate = gate_payload["reproducibility_gate"]
    baseline = gate_payload["results"][str(freq)]
    rows = {}
    for phase in SCREEN_PHASES:
        result = screen_payload["results"][str(phase)]
        rows[str(phase)] = {
            "strategy_cagr": result["strategy_cagr"],
            "benchmark_cagr": result["benchmark_cagr"],
            "relative_excess_cagr": result["relative_excess_cagr"],
            "strategy_max_drawdown": result["strategy_max_drawdown"],
            "sharpe": result["sharpe"],
            "information_ratio": result["information_ratio"],
            "annual_volatility": result["annual_volatility"],
            "mean_turnover": result["mean_turnover"],
            "total_cost_sum": result["total_cost_sum"],
        }
    return {
        "market": gate_payload["market"],
        "freq": int(freq),
        "gate_passed": gate["passed"],
        "gate_version": gate["gate_version"],
        "snapshot_token": gate_payload["reproducibility"]["snapshot_token"],
        "provider_fingerprint": gate_payload["reproducibility"]["provider_fingerprint"],
        "phase0_signal_sha256": baseline["signal_sha256"],
        "screen_phases": list(SCREEN_PHASES),
        "new_model_fits": screen_payload.get("new_model_fits"),
        "summary": screen_payload.get("summary"),
        "phases": rows,
    }
