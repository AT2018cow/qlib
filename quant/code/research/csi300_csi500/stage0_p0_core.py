"""Stage 0 (P0) offline control plane for CSI300/CSI500 research.

Pure-Python job planning, immutable receipt helpers, cost-stress arithmetic
and scoreboard rendering. No Modal import, no remote calls, no model fitting.

Covers 2 markets (csi300/SH000300, csi500/SH000905) x 2 anchor models
(baseline, c98856b4) x 2 phases (0, 10):

- 176 rolling LightGBM retrain jobs (``retrain`` kind, 22 as-of slices each).
- 16 F1 replay cells (``cell`` kind, Top20/Drop2 and Top10/Drop1 combos).

IMPORTANT (verified 2026-10-15, executable gate, not a doc preference):
the plan text lists ``c98856b4`` model_params as
``{lr 0.05, colsample 0.7, l1 50.0, l2 200.0, depth 6, leaves 31,
min_data 50}``, but those params hash (stable_sha over the LGBModel config
built from ``workflow_config_lightgbm_Alpha158_csi500.yaml`` +
``apply_lgb_reproducibility``) to
``b3c4345e07f1bd5f75bb7aeb505f40d8a20e28648459678ddb0efce0efe3fdf6``,
NOT the frozen ``d49e893a...3d50`` recorded in the plan, in
``quant/code/production/csi1000_production_config.py`` and in the frozen
Stage-B result. The frozen production params
``{lr 0.05, colsample 1.0, l1 205.6999, l2 200.0, depth 10, leaves 250,
min_data 200}`` DO hash to ``d49e893a...3d50``. ``fit_remote`` gates on
``stable_sha(config["task"]["model"]) == job["model_sha256"]``, so this
module stores the SHA-authoritative production params. candidate_id and
model_sha256 stay exactly as planned; the params-text discrepancy is flagged
in the execution report for reviewer adjudication.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any


def _ensure_paths() -> Path:
    """Make repo + quant/code subtree importable from any cwd (local/modal).

    Returns the repository root. Idempotent.
    """
    here = Path(__file__).resolve()
    repo_root = None
    for parent in (here,) + tuple(here.parents):
        if (parent / "qlib" / "__init__.py").is_file() and (parent / "quant" / "code").is_dir():
            repo_root = parent
            break
    if repo_root is None:  # Modal image layout fallback.
        for candidate in (Path("/root/qlib"), Path.cwd()):
            if (candidate / "qlib" / "__init__.py").is_file():
                repo_root = candidate
                break
    if repo_root is None:
        raise ValueError("STAGE0_P0_NOREPO: cannot locate repository root")
    wanted = [
        str(repo_root),
        str(repo_root / "quant" / "code"),
        str(repo_root / "quant" / "code" / "execution"),
        str(repo_root / "quant" / "code" / "production"),
        str(repo_root / "quant" / "code" / "research" / "stage_ab"),
        str(repo_root / "quant" / "code" / "research" / "layered_search"),
        str(repo_root / "quant" / "code" / "research" / "csi300_csi500"),
        str(repo_root / "quant" / "code" / "audit"),
    ]
    for entry in wanted:
        if entry not in sys.path:
            sys.path.insert(0, entry)
    return repo_root


REPO_ROOT = _ensure_paths()

from audit.csi1000_layered_search_core import require, sha256_file, stable_sha  # noqa: E402
from csi1000_stage_b_core import (  # noqa: E402
    LABEL_HORIZON,
    RESERVED_EXECUTION_END,
    RESERVED_EXECUTION_START,
    RESERVED_SIGNAL_ANCHOR,
    RETRAIN_FREQUENCY,
    TRAIN_START,
    VALIDATION_SESSIONS,
    build_phase_jobs,
)

# ---------------------------------------------------------------------------
# Frozen Stage 0 (P0) identities (plan section 2, hashes verified at runtime).
# ---------------------------------------------------------------------------

SOURCE_VOLUME = "qlib-cn-data"
RESEARCH_VOLUME = "qlib-csi300-csi500-research-v2"

EXPECTED_PROVIDER_FINGERPRINT = "cd2e68f571c6bd19fbb2b79089e891bb1c43b96ab1fcdb4d2b2a9b1fdd9ce5d5"
EXPECTED_SNAPSHOT_TOKEN = "51756897fc75230493194aef2e48815e4e9f3cc7426cc135f47bb8ba8e0d21e1"
EXPECTED_CALENDAR_DAYS = 6482
EXPECTED_CALENDAR_END = "2026-09-30"
N_EXECUTION_DAYS = 424

MARKETS = {
    "csi300": {
        "benchmark": "SH000300",
        "expected_quote_count": 338,
        "expected_quote_sha256": "9f3756ea8d2e0ff0bb12c3dce6d81888fe7a5df978a5f5669a4ce07b1a043f5b",
    },
    "csi500": {
        "benchmark": "SH000905",
        "expected_quote_count": 643,
        "expected_quote_sha256": "cfcbc21efaba156173ff3c0aa09afc4f1fda5e94d9046f32bf37aa8b32f5e4fb",
    },
}

MODELS = {
    "baseline": {
        "candidate_id": "23b92de05cf36c82998de684d0fbf64d81ee54490d755bd3cf96311c00286785",
        "model_sha256": "793021af66034ec508848ee032f6d309103291981abb63d11ae779da9c5ee7d3",
        "model_params": {
            "learning_rate": 0.1,
            "colsample_bytree": 0.9,
            "lambda_l1": 205.6999,
            "lambda_l2": 580.9768,
            "max_depth": 8,
            "num_leaves": 250,
            "min_data_in_leaf": 20,
        },
    },
    "c98856b4": {
        "candidate_id": "c98856b460aba687640d422e85a82a545bf5b6ca932e906a180699e0f6cad19b",
        "model_sha256": "d49e893ad37c821a6a092fb17baf7ee29c9ff71289bbeb1205132e34e3854c50",
        # SHA-authoritative frozen production params (see module docstring).
        "model_params": {
            "learning_rate": 0.05,
            "colsample_bytree": 1.0,
            "lambda_l1": 205.6999,
            "lambda_l2": 200.0,
            "max_depth": 10,
            "num_leaves": 250,
            "min_data_in_leaf": 200,
        },
    },
}

COMBOS = (("top20_drop2", 20, 2), ("top10_drop1", 10, 1))
PHASES = (0, 10)

ARTIFACTS = {
    "retrain": ("prediction.parquet", "checkpoint.txt"),
    "cell": ("fixed_report.parquet", "decisions.json", "signal.parquet"),
}

RUN_ID_PATTERN = re.compile(r"stage0_p0_[A-Za-z0-9_]{4,50}")
HEX40_PATTERN = re.compile(r"[a-f0-9]{40}")
HEX64_PATTERN = re.compile(r"[a-f0-9]{64}")


def prefix_for(run_id: str, code_sha: str) -> str:
    """Isolated research prefix bound to the full immutable code SHA."""
    require(bool(RUN_ID_PATTERN.fullmatch(run_id or "")), "STAGE0_P0 invalid run ID")
    require(bool(HEX40_PATTERN.fullmatch(code_sha or "")), "STAGE0_P0 needs full immutable code SHA")
    return f"csi300_csi500_research/stage0_p0/{run_id}/{code_sha[:12]}"


def job_id(job: dict[str, Any]) -> str:
    """Immutable job identity; market/model/combo/phase/as-of are in the key."""
    require(job.get("kind") in ("retrain", "cell"), "STAGE0_P0 unknown job type")
    require(bool(HEX64_PATTERN.fullmatch(job.get("candidate_id", "") or "")), "STAGE0_P0 invalid candidate ID")
    require(job.get("market") in MARKETS, "STAGE0_P0 unknown market")
    require(job.get("model_key") in MODELS, "STAGE0_P0 unknown model")
    require(job.get("phase") in (0, 10), "STAGE0_P0 invalid phase")
    model_short = job["candidate_id"][:8]
    if job["kind"] == "retrain":
        require(
            bool(re.fullmatch(r"\d{4}-\d\d-\d\d", job.get("retrain_asof", "") or "")), "STAGE0_P0 invalid as-of"
        )
        return f"retrain/{job['market']}/{job['model_key']}/{model_short}/phase{job['phase']:02d}/{job['retrain_asof']}"
    require(job.get("combo_key") in ("top20_drop2", "top10_drop1"), "STAGE0_P0 unknown combo")
    return f"cell/{job['market']}/{job['model_key']}/{model_short}/{job['combo_key']}/phase{job['phase']:02d}"


def receipt_path(root: Path, job: dict[str, Any]) -> Path:
    """Receipt directory: fits/ for retrain jobs, cells/ for replay cells."""
    sub = "fits" if job["kind"] == "retrain" else "cells"
    return root / sub / job_id(job)


def _identity(job: dict[str, Any]) -> dict[str, str]:
    return {
        key: job[key]
        for key in (
            "run_id",
            "code_sha",
            "provider_fingerprint",
            "source_snapshot_sha256",
            "quote_sha256",
        )
    }


def checked_receipt(root: Path, job: dict[str, Any], identity: dict[str, str]) -> dict | None:
    """Return the verified immutable receipt; incomplete/mismatched files block.

    Never interpret existence or a timestamp as a cache hit.
    """
    directory = receipt_path(root, job)
    if not directory.exists():
        return None
    path = directory / "receipt.json"
    require(path.is_file(), "STAGE0_P0 incomplete published receipt")
    record = json.loads(path.read_text(encoding="utf-8"))
    for key, expected in identity.items():
        require(record.get(key) == expected, f"STAGE0_P0 receipt identity drift: {key}")
    require(record.get("job_sha256") == stable_sha(job), "STAGE0_P0 receipt job spec mismatch")
    require(
        record.get("status") == "PASS" and record.get("kind") == job["kind"],
        "STAGE0_P0 receipt is not a PASS of the requested kind",
    )
    names = ARTIFACTS[job["kind"]]
    hashes = record.get("artifact_sha256")
    require(isinstance(hashes, dict) and set(hashes) == set(names), "STAGE0_P0 receipt artifact manifest incomplete")
    for name in names:
        target = directory / name
        require(target.is_file() and sha256_file(target) == hashes[name], f"STAGE0_P0 artifact SHA drift: {name}")
    return record


def publish_receipt(
    root: Path, job: dict[str, Any], identity: dict[str, str], artifacts: dict[str, Any], details: dict
) -> dict:
    """Write into a unique temp directory; existing artifacts are never overwritten."""
    dest = receipt_path(root, job)
    require(set(artifacts) == set(ARTIFACTS[job["kind"]]), "STAGE0_P0 wrong artifact list")
    protected = set(identity) | {"status", "kind", "job_sha256", "artifact_sha256"}
    require(not protected.intersection(details), "STAGE0_P0 receipt cannot override signed identity fields")
    if dest.exists():
        existing = checked_receipt(root, job, identity)
        require(existing is not None, "STAGE0_P0 existing incomplete receipt")
        return existing
    tmp = dest.parent / (dest.name + ".tmp")
    require(not tmp.exists(), "STAGE0_P0 stale temp artifact; manual evidence check required")
    tmp.mkdir(parents=True, exist_ok=False)
    try:
        for name, obj in artifacts.items():
            path = tmp / name
            if name.endswith(".parquet"):
                obj.to_parquet(path)
            elif isinstance(obj, bytes):
                path.write_bytes(obj)
            elif isinstance(obj, str):
                path.write_text(obj, encoding="utf-8")
            else:
                path.write_text(json.dumps(obj, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
        hashes = {name: sha256_file(tmp / name) for name in sorted(artifacts)}
        record = {
            "status": "PASS",
            **identity,
            "kind": job["kind"],
            "job_sha256": stable_sha(job),
            "artifact_sha256": hashes,
            **details,
        }
        (tmp / "receipt.json").write_text(
            json.dumps(record, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        os.replace(tmp, dest)
        return record
    except BaseException:
        if tmp.exists():
            shutil.rmtree(tmp)
        raise


# ---------------------------------------------------------------------------
# Offline job planners (176 retrain + 16 cell).
# ---------------------------------------------------------------------------


def _base_job(run_id: str, code_sha: str) -> dict[str, Any]:
    prefix_for(run_id, code_sha)
    return {
        "run_id": run_id,
        "code_sha": code_sha,
        "provider_fingerprint": "",
        "source_snapshot_sha256": "",
        "quote_sha256": "",
    }


def build_stage0_fit_jobs(calendar: list[str], run_id: str, code_sha: str) -> list[dict[str, Any]]:
    """Build all 2 markets x 2 models x 2 phases x 22 as-of = 176 retrain jobs."""
    jobs: list[dict[str, Any]] = []
    for market in ("csi300", "csi500"):
        for model_key in ("baseline", "c98856b4"):
            spec = MODELS[model_key]
            for phase in PHASES:
                for slice_job in build_phase_jobs(calendar, phase):
                    jobs.append(
                        {
                            **_base_job(run_id, code_sha),
                            "kind": "retrain",
                            "market": market,
                            "benchmark": MARKETS[market]["benchmark"],
                            "model_key": model_key,
                            "candidate_id": spec["candidate_id"],
                            "model_params": dict(spec["model_params"]),
                            "model_sha256": spec["model_sha256"],
                            "phase": phase,
                            "retrain_asof": slice_job["retrain_asof"],
                            "signal_end": slice_job["signal_end"],
                        }
                    )
    require(len(jobs) == 176, f"STAGE0_P0 fit roster has {len(jobs)} jobs, need 176")
    keys = [stable_sha(job) for job in jobs]
    require(len(set(keys)) == len(keys), "STAGE0_P0 duplicate fit jobs")
    return jobs


def build_stage0_cell_jobs(run_id: str, code_sha: str) -> list[dict[str, Any]]:
    """Build all 2 markets x 2 models x 2 combos x 2 phases = 16 replay cells."""
    jobs: list[dict[str, Any]] = []
    for market in ("csi300", "csi500"):
        for model_key in ("baseline", "c98856b4"):
            spec = MODELS[model_key]
            for combo_key, topk, n_drop in COMBOS:
                for phase in PHASES:
                    jobs.append(
                        {
                            **_base_job(run_id, code_sha),
                            "kind": "cell",
                            "market": market,
                            "benchmark": MARKETS[market]["benchmark"],
                            "model_key": model_key,
                            "candidate_id": spec["candidate_id"],
                            "combo_key": combo_key,
                            "topk": topk,
                            "n_drop": n_drop,
                            "phase": phase,
                        }
                    )
    require(len(jobs) == 16, f"STAGE0_P0 cell roster has {len(jobs)} jobs, need 16")
    keys = [stable_sha(job) for job in jobs]
    require(len(set(keys)) == len(keys), "STAGE0_P0 duplicate cell jobs")
    return jobs


def bind_job_identity(
    job: dict[str, Any],
    *,
    provider_fingerprint: str,
    source_snapshot_sha256: str,
    quote_sha256: str,
) -> dict[str, Any]:
    """Bind measured source identities to a planned job (deterministic reuse)."""
    require(bool(HEX64_PATTERN.fullmatch(provider_fingerprint or "")), "STAGE0_P0 bad provider fingerprint")
    require(bool(HEX64_PATTERN.fullmatch(source_snapshot_sha256 or "")), "STAGE0_P0 bad snapshot SHA")
    require(bool(HEX64_PATTERN.fullmatch(quote_sha256 or "")), "STAGE0_P0 bad quote SHA")
    return {
        **job,
        "provider_fingerprint": provider_fingerprint,
        "source_snapshot_sha256": source_snapshot_sha256,
        "quote_sha256": quote_sha256,
    }


def quote_sha_of(codes: list[str]) -> str:
    """Static F1 quote-union SHA: SHA256("\\n".join(codes) + "\\n")."""
    require(bool(codes) and all(isinstance(code, str) and code for code in codes), "STAGE0_P0 empty code list")
    require(codes == sorted(codes), "STAGE0_P0 quote codes must be lexically sorted")
    return hashlib.sha256(("\n".join(codes) + "\n").encode()).hexdigest()


# ---------------------------------------------------------------------------
# Qlib replay with an explicit benchmark (trap 3: never hardcode SH000852).
# ---------------------------------------------------------------------------


def backtest_market_signal(
    signal: Any, topk: int, dropn: int, codes: Any, start: str, end: str, benchmark: str
) -> tuple[Any, list[dict[str, Any]]]:
    """Real Qlib replay of a signal under one (TopK, DropN) combo and benchmark.

    Qlib imports stay inside this function so planning, manifests and offline
    validation work without a provider. Never trains.
    """
    if not isinstance(topk, int) or not isinstance(dropn, int) or dropn < 1 or dropn > topk:
        raise ValueError(f"STAGE0_P0_BLOCKED: invalid combo TopK={topk} DropN={dropn}")
    if benchmark not in ("SH000300", "SH000905"):
        raise ValueError(f"STAGE0_P0_BLOCKED: foreign benchmark {benchmark!r}")
    from audit.replay_stage_b_f1 import _audit_decisions
    from board_execution import research_exchange
    from qlib.backtest import collect_data

    result: dict[str, Any] = {}
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
            benchmark=benchmark,
            exchange_kwargs=research_exchange(start, end, codes=codes),
            return_value=result,
        )
    )
    return result["portfolio_dict"]["1day"][0], _audit_decisions(decisions)


def apply_cost_stress(report: Any, extra_bps: float) -> Any:
    """Arithmetic cost stress on frozen fills' notional (established method).

    Stressed NAV = NAV minus cumulative daily notional * bps/10000, with costs
    moved through cash so cash+value still reconciles. NOT a re-simulation:
    no new fills, no resizing, no slippage forecast. Mirrors the arithmetic of
    ``csi1000_layered_search_replay.stress_advantage_pp`` but returns the full
    stressed report frame so ``report_metrics`` can recompute every metric.
    """
    import pandas as pd  # noqa: PLC0415

    if not isinstance(extra_bps, (int, float)) or extra_bps < 0:
        raise ValueError(f"STAGE0_P0_BLOCKED: invalid stress {extra_bps!r}")
    stressed = report.copy()
    nav = report["account"].to_numpy(dtype=float)
    turnover_rate = report["turnover"].to_numpy(dtype=float)
    prior = [100_000_000.0] + list(nav[:-1])
    notionals = [float(t) * float(p) for t, p in zip(turnover_rate, prior)]
    adjustment = 0.0
    stressed_nav, stressed_cash, stressed_cost, stressed_total = [], [], [], []
    for i in range(len(nav)):
        adjustment += notionals[i] * float(extra_bps) / 10000.0
        stressed_nav.append(float(nav[i]) - adjustment)
        stressed_cash.append(float(report["cash"].iloc[i]) - adjustment)
        rate_add = notionals[i] * float(extra_bps) / 10000.0 / prior[i] if prior[i] else 0.0
        stressed_cost.append(float(report["cost"].iloc[i]) + rate_add)
        stressed_total.append(float(report["total_cost"].iloc[i]) + adjustment)
    if min(stressed_nav) <= 0:
        raise ValueError("STAGE0_P0_BLOCKED: negative stressed NAV")
    stressed["account"] = pd.Series(stressed_nav, index=report.index, dtype=float)
    stressed["cash"] = pd.Series(stressed_cash, index=report.index, dtype=float)
    stressed["cost"] = pd.Series(stressed_cost, index=report.index, dtype=float)
    stressed["total_cost"] = pd.Series(stressed_total, index=report.index, dtype=float)
    return stressed


# ---------------------------------------------------------------------------
# Lightweight export rendering (committed to the worktree, never the Volume).
# ---------------------------------------------------------------------------

SCOREBOARD_COLUMNS = (
    "market",
    "benchmark",
    "model_key",
    "candidate_id",
    "combo_key",
    "topk",
    "n_drop",
    "phase",
    "n_days",
    "strategy_total_return",
    "strategy_cagr",
    "benchmark_cagr",
    "relative_excess_cagr",
    "sharpe",
    "information_ratio",
    "strategy_max_drawdown",
    "mean_turnover",
    "total_cost_sum",
    "stressed10bp_strategy_total_return",
    "stressed20bp_strategy_total_return",
    "account_return_max_error",
)


def render_scoreboard(summary: dict[str, Any], csv_path: str | Path) -> Path:
    """Render the 16-cell scoreboard CSV from a stage0_p0_summary dict."""
    cells = summary.get("cells")
    require(isinstance(cells, list) and len(cells) == 16, "STAGE0_P0 summary must carry exactly 16 cells")
    out = Path(csv_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(SCOREBOARD_COLUMNS))
        writer.writeheader()
        for cell in sorted(cells, key=lambda row: (row["market"], row["model_key"], row["combo_key"], row["phase"])):
            metrics = cell.get("metrics") or {}
            writer.writerow(
                {
                    "market": cell["market"],
                    "benchmark": cell["benchmark"],
                    "model_key": cell["model_key"],
                    "candidate_id": cell["candidate_id"],
                    "combo_key": cell["combo_key"],
                    "topk": cell["topk"],
                    "n_drop": cell["n_drop"],
                    "phase": cell["phase"],
                    "n_days": metrics.get("n_days"),
                    "strategy_total_return": metrics.get("strategy_total_return"),
                    "strategy_cagr": metrics.get("strategy_cagr"),
                    "benchmark_cagr": metrics.get("benchmark_cagr"),
                    "relative_excess_cagr": metrics.get("relative_excess_cagr"),
                    "sharpe": metrics.get("sharpe"),
                    "information_ratio": metrics.get("information_ratio"),
                    "strategy_max_drawdown": metrics.get("strategy_max_drawdown"),
                    "mean_turnover": metrics.get("mean_turnover"),
                    "total_cost_sum": metrics.get("total_cost_sum"),
                    "stressed10bp_strategy_total_return": (cell.get("stressed") or {}).get("10bp", {}).get(
                        "strategy_total_return"
                    ),
                    "stressed20bp_strategy_total_return": (cell.get("stressed") or {}).get("20bp", {}).get(
                        "strategy_total_return"
                    ),
                    "account_return_max_error": metrics.get("account_return_max_error"),
                }
            )
    return out
