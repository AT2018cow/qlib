"""Stage 2 (L1) offline control plane for CSI300/CSI500 research.

Zero-training TopK/DropN combination matrix over the 12 frozen models
(2 Stage 0 anchors + 10 Stage 1 models) whose 424-day prediction signals are
already SHA-verified on research Volume v2:

- 2 markets x 12 models x 8 NEW combos x 2 phases = 384 new ``l1cell`` jobs
  (pure F1 replay, no model training anywhere in this stage).
- Each ``l1cell`` stores only ``fixed_report.parquet`` + ``decisions.json``
  (``STAGE2_ARTIFACTS``); the source ``signal.parquet`` is never copied and is
  pinned inside ``receipt.json`` via ``source_signal_sha256`` /
  ``source_signal_relpath``.
- Merging 384 new cells with the 96 prior Stage 0+1 cells yields 480 cells =
  per market 12 models x 10 combos x 2 phases = 120 ranked strategies, plus a
  diversified Top 4 finalist shortlist for the Stage 3 five-phase final.

Pure-Python planners/renderers only: no Modal import, no remote calls, no
model fitting. Replay/stress arithmetic is reused from :mod:`stage0_p0_core`.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

import stage0_p0_core as p0
from audit.csi1000_layered_search_core import require, sha256_file, stable_sha
from csi1000_stage_b_core import (
    RESERVED_EXECUTION_END,
    RESERVED_EXECUTION_START,
    RESERVED_SIGNAL_ANCHOR,
)
from stage1_m1_core import STAGE0_CODE_SHA, STAGE0_PREFIX, STAGE0_RUN_ID, STAGE1_NEW_MODELS

RESEARCH_VOLUME = p0.RESEARCH_VOLUME
SOURCE_VOLUME = p0.SOURCE_VOLUME
MARKETS = p0.MARKETS
PHASES = p0.PHASES
N_EXECUTION_DAYS = p0.N_EXECUTION_DAYS
EXPECTED_PROVIDER_FINGERPRINT = p0.EXPECTED_PROVIDER_FINGERPRINT
EXPECTED_SNAPSHOT_TOKEN = p0.EXPECTED_SNAPSHOT_TOKEN
EXPECTED_CALENDAR_DAYS = p0.EXPECTED_CALENDAR_DAYS
EXPECTED_CALENDAR_END = p0.EXPECTED_CALENDAR_END
quote_sha_of = p0.quote_sha_of

# Verified Stage 1 export (same v2 research Volume, merged to main 2026-10-15).
STAGE1_RUN_ID = "stage1_m1_20261015_a1"
STAGE1_CODE_SHA = "f9365d3c67658bc830ce0fac6fbf748d997584f7"
STAGE1_PREFIX = f"csi300_csi500_research/stage1_m1/{STAGE1_RUN_ID}/{STAGE1_CODE_SHA[:12]}"

# 12 frozen models: 2 Stage 0 anchors (label horizon 20 by construction) + 10 Stage 1.
ALL_12_MODELS: dict[str, dict[str, Any]] = {}
for _key, _spec in p0.MODELS.items():
    ALL_12_MODELS[_key] = {**_spec, "label_horizon": 20, "source_stage": "stage0_p0"}
for _key, _spec in STAGE1_NEW_MODELS.items():
    ALL_12_MODELS[_key] = {**_spec, "source_stage": "stage1_m1"}
require(
    len(ALL_12_MODELS) == 12 and set(ALL_12_MODELS) == set(p0.MODELS) | set(STAGE1_NEW_MODELS),
    "STAGE2_L1 model merge must cover 12 frozen models",
)

# Plan-verbatim combination matrix (8 new this stage + 2 reused from Stage 0/1).
STAGE2_NEW_COMBOS = (
    ("top5_drop1", 5, 1),
    ("top8_drop1", 8, 1),
    ("top10_drop2", 10, 2),
    ("top15_drop1", 15, 1),
    ("top15_drop2", 15, 2),
    ("top20_drop1", 20, 1),
    ("top30_drop2", 30, 2),
    ("top30_drop3", 30, 3),
)
ALL_10_COMBOS = (
    ("top5_drop1", 5, 1),
    ("top8_drop1", 8, 1),
    ("top10_drop1", 10, 1),
    ("top10_drop2", 10, 2),
    ("top15_drop1", 15, 1),
    ("top15_drop2", 15, 2),
    ("top20_drop1", 20, 1),
    ("top20_drop2", 20, 2),
    ("top30_drop2", 30, 2),
    ("top30_drop3", 30, 3),
)
PRIOR_COMBOS = ("top20_drop2", "top10_drop1")

# Zero-signal-copy manifest: l1cells publish only these two artifacts.
STAGE2_ARTIFACTS = {"l1cell": ("fixed_report.parquet", "decisions.json")}

RUN_ID_PATTERN = re.compile(r"stage2_l1_[A-Za-z0-9_]{4,50}")
HEX40_PATTERN = re.compile(r"[a-f0-9]{40}")
HEX64_PATTERN = re.compile(r"[a-f0-9]{64}")


def _combo_specs() -> dict[str, tuple[int, int]]:
    return {key: (topk, n_drop) for key, topk, n_drop in ALL_10_COMBOS}


def prefix_for(run_id: str, code_sha: str) -> str:
    """Isolated research prefix bound to the full immutable code SHA."""
    require(bool(RUN_ID_PATTERN.fullmatch(run_id or "")), "STAGE2_L1 invalid run ID")
    require(bool(HEX40_PATTERN.fullmatch(code_sha or "")), "STAGE2_L1 needs full immutable code SHA")
    return f"csi300_csi500_research/stage2_l1/{run_id}/{code_sha[:12]}"


def source_signal_relpath(market: str, model_key: str, phase: int) -> str:
    """Canonical Volume-relative path of a frozen 424-day model signal.

    Stage 0 anchors live under the verified Stage 0 prefix, Stage 1 models
    under the verified Stage 1 prefix. Both are the Top20/Drop2 cell signal
    artifacts whose SHA256 is sealed in that cell's ``receipt.json``.
    """
    require(market in MARKETS, "STAGE2_L1 unknown market")
    require(model_key in ALL_12_MODELS, "STAGE2_L1 unknown model")
    require(phase in (0, 10), "STAGE2_L1 invalid phase")
    spec = ALL_12_MODELS[model_key]
    prefix = STAGE0_PREFIX if spec["source_stage"] == "stage0_p0" else STAGE1_PREFIX
    short = spec["candidate_id"][:8]
    return f"{prefix}/cells/cell/{market}/{model_key}/{short}/top20_drop2/phase{phase:02d}/signal.parquet"


def source_signal_path(volume_root: str | Path, market: str, model_key: str, phase: int) -> Path:
    """Absolute path of the frozen signal inside a mounted research Volume."""
    return Path(volume_root) / source_signal_relpath(market, model_key, phase)


def source_signal_receipt_path(volume_root: str | Path, market: str, model_key: str, phase: int) -> Path:
    """Receipt that seals the source signal SHA (sibling of the signal file)."""
    return source_signal_path(volume_root, market, model_key, phase).parent / "receipt.json"


def job_id(job: dict[str, Any]) -> str:
    """Immutable l1cell identity; model/combo/phase are all in the key."""
    require(job.get("kind") == "l1cell", "STAGE2_L1 unknown job type")
    require(bool(HEX64_PATTERN.fullmatch(job.get("candidate_id", "") or "")), "STAGE2_L1 invalid candidate ID")
    require(job.get("market") in MARKETS, "STAGE2_L1 unknown market")
    require(job.get("model_key") in ALL_12_MODELS, "STAGE2_L1 unknown model")
    require(job.get("phase") in (0, 10), "STAGE2_L1 invalid phase")
    combos = _combo_specs()
    require(job.get("combo_key") in combos, "STAGE2_L1 unknown combo")
    require(
        (int(job.get("topk", -1)), int(job.get("n_drop", -1))) == combos[job["combo_key"]],
        "STAGE2_L1 combo geometry mismatch",
    )
    model_short = job["candidate_id"][:8]
    return f"l1cell/{job['market']}/{job['model_key']}/{model_short}/{job['combo_key']}/phase{job['phase']:02d}"


def receipt_path(root: Path, job: dict[str, Any]) -> Path:
    """Receipt directory: every l1cell lives under cells/l1cell/..."""
    return root / "cells" / job_id(job)


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
    """Return the verified l1cell receipt; incomplete/mismatched files block.

    Additionally enforces the zero-copy signal pin: the receipt must echo the
    exact ``source_signal_sha256`` / ``source_signal_relpath`` of the job.
    """
    directory = receipt_path(root, job)
    if not directory.exists():
        return None
    path = directory / "receipt.json"
    require(path.is_file(), "STAGE2_L1 incomplete published receipt")
    record = json.loads(path.read_text(encoding="utf-8"))
    for key, expected in identity.items():
        require(record.get(key) == expected, f"STAGE2_L1 receipt identity drift: {key}")
    require(record.get("job_sha256") == stable_sha(job), "STAGE2_L1 receipt job spec mismatch")
    require(
        record.get("status") == "PASS" and record.get("kind") == job["kind"],
        "STAGE2_L1 receipt is not a PASS of the requested kind",
    )
    require(
        record.get("source_signal_sha256") == job.get("source_signal_sha256"),
        "STAGE2_L1 receipt source signal SHA drift",
    )
    require(
        record.get("source_signal_relpath") == job.get("source_signal_relpath"),
        "STAGE2_L1 receipt source signal relpath drift",
    )
    names = STAGE2_ARTIFACTS[job["kind"]]
    hashes = record.get("artifact_sha256")
    require(isinstance(hashes, dict) and set(hashes) == set(names), "STAGE2_L1 receipt artifact manifest incomplete")
    for name in names:
        target = directory / name
        require(target.is_file() and sha256_file(target) == hashes[name], f"STAGE2_L1 artifact SHA drift: {name}")
    return record


def publish_receipt(
    root: Path, job: dict[str, Any], identity: dict[str, str], artifacts: dict[str, Any], details: dict
) -> dict:
    """Atomic temp-dir + rename publication; existing files are never overwritten."""
    import os as _os
    import shutil as _shutil

    dest = receipt_path(root, job)
    require(set(artifacts) == set(STAGE2_ARTIFACTS[job["kind"]]), "STAGE2_L1 wrong artifact list")
    require(
        details.get("source_signal_sha256") == job.get("source_signal_sha256")
        and details.get("source_signal_relpath") == job.get("source_signal_relpath"),
        "STAGE2_L1 details must pin the source signal identity",
    )
    if dest.exists():
        existing = checked_receipt(root, job, identity)
        require(existing is not None, "STAGE2_L1 existing incomplete receipt")
        return existing
    tmp = dest.parent / (dest.name + ".tmp")
    require(not tmp.exists(), "STAGE2_L1 stale temp artifact; manual evidence check required")
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
        protected = set(identity) | {"status", "kind", "job_sha256", "artifact_sha256"}
        require(not protected.intersection(details), "STAGE2_L1 receipt cannot override signed identity fields")
        (tmp / "receipt.json").write_text(
            json.dumps(record, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        _os.replace(tmp, dest)
        return record
    except BaseException:
        if tmp.exists():
            _shutil.rmtree(tmp)
        raise


def _base_job(run_id: str, code_sha: str) -> dict[str, Any]:
    prefix_for(run_id, code_sha)
    return {
        "run_id": run_id,
        "code_sha": code_sha,
        "provider_fingerprint": "",
        "source_snapshot_sha256": "",
        "quote_sha256": "",
        "source_signal_sha256": "",
    }


def build_stage2_cell_jobs(run_id: str, code_sha: str) -> list[dict[str, Any]]:
    """Build all 2 markets x 12 models x 8 new combos x 2 phases = 384 l1cells."""
    jobs: list[dict[str, Any]] = []
    for market in ("csi300", "csi500"):
        for model_key, spec in ALL_12_MODELS.items():
            for combo_key, topk, n_drop in STAGE2_NEW_COMBOS:
                for phase in PHASES:
                    jobs.append(
                        {
                            **_base_job(run_id, code_sha),
                            "kind": "l1cell",
                            "market": market,
                            "benchmark": MARKETS[market]["benchmark"],
                            "model_key": model_key,
                            "candidate_id": spec["candidate_id"],
                            "label_horizon": int(spec["label_horizon"]),
                            "source_stage": spec["source_stage"],
                            "combo_key": combo_key,
                            "topk": topk,
                            "n_drop": n_drop,
                            "phase": phase,
                            "source_signal_relpath": source_signal_relpath(market, model_key, phase),
                        }
                    )
    require(len(jobs) == 384, f"STAGE2_L1 roster has {len(jobs)} jobs, need 384")
    keys = [stable_sha(job) for job in jobs]
    require(len(set(keys)) == len(keys), "STAGE2_L1 duplicate l1cell jobs")
    return jobs


def bind_stage2_job(
    job: dict[str, Any],
    *,
    provider_fingerprint: str,
    source_snapshot_sha256: str,
    quote_sha256: str,
    source_signal_sha256: str,
) -> dict[str, Any]:
    """Bind measured identities (source + frozen signal SHA) to a planned job."""
    require(bool(HEX64_PATTERN.fullmatch(provider_fingerprint or "")), "STAGE2_L1 bad provider fingerprint")
    require(bool(HEX64_PATTERN.fullmatch(source_snapshot_sha256 or "")), "STAGE2_L1 bad snapshot SHA")
    require(bool(HEX64_PATTERN.fullmatch(quote_sha256 or "")), "STAGE2_L1 bad quote SHA")
    require(bool(HEX64_PATTERN.fullmatch(source_signal_sha256 or "")), "STAGE2_L1 bad source signal SHA")
    require(job.get("kind") == "l1cell", "STAGE2_L1 bind supports l1cell only")
    require(
        job.get("source_signal_relpath") == source_signal_relpath(job["market"], job["model_key"], int(job["phase"])),
        "STAGE2_L1 source signal relpath drift",
    )
    return {
        **job,
        "provider_fingerprint": provider_fingerprint,
        "source_snapshot_sha256": source_snapshot_sha256,
        "quote_sha256": quote_sha256,
        "source_signal_sha256": source_signal_sha256,
    }


def load_prior_96_cells(
    volume_root: str | Path,
    stage0_prefix: str = STAGE0_PREFIX,
    stage1_prefix: str = STAGE1_PREFIX,
) -> list[dict[str, Any]]:
    """Load the verified 96 prior cells (16 Stage 0 anchors + 80 Stage 1 cells).

    Reads ``<stage1_prefix>/stage1_m1_summary.json`` and re-verifies every
    cell's ``receipt.json`` (status, kind, job SHA, sealed artifact hashes) plus
    the on-disk bytes of ``fixed_report.parquet`` / ``decisions.json`` /
    ``signal.parquet``. Fails closed on any missing or drifted byte.
    """
    root = Path(volume_root)
    summary_path = root / stage1_prefix / "stage1_m1_summary.json"
    require(summary_path.is_file(), "STAGE2_L1 Stage 1 summary missing")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    require(summary.get("status") == "PASS", "STAGE2_L1 Stage 1 summary is not PASS")
    require(
        summary.get("run_id") == STAGE1_RUN_ID and summary.get("code_sha") == STAGE1_CODE_SHA,
        "STAGE2_L1 Stage 1 lineage drift",
    )
    require(
        int(summary.get("completed_cells", -1)) == 80
        and int(summary.get("anchor_cells", -1)) == 16
        and int(summary.get("total_cells", -1)) == 96,
        "STAGE2_L1 Stage 1 summary coverage drift",
    )
    cells = summary.get("cells")
    require(isinstance(cells, list) and len(cells) == 96, "STAGE2_L1 Stage 1 must carry exactly 96 cells")
    combos = _combo_specs()
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, int]] = set()
    for cell in cells:
        anchor = bool(cell.get("anchor"))
        prefix = stage0_prefix if anchor else stage1_prefix
        market = cell.get("market")
        model_key = cell.get("model_key")
        combo_key = cell.get("combo_key")
        phase = int(cell.get("phase", -1))
        require(market in MARKETS and model_key in ALL_12_MODELS and phase in (0, 10), "STAGE2_L1 prior cell keys")
        require(
            combo_key in combos and (int(cell["topk"]), int(cell["n_drop"])) == combos[combo_key],
            "STAGE2_L1 prior combo drift",
        )
        require(bool(HEX64_PATTERN.fullmatch(cell.get("candidate_id", "") or "")), "STAGE2_L1 prior candidate ID")
        key = (market, model_key, combo_key, phase)
        require(key not in seen, f"STAGE2_L1 duplicate prior cell: {key}")
        seen.add(key)
        spec = ALL_12_MODELS[model_key]
        require(cell.get("candidate_id") == spec["candidate_id"], "STAGE2_L1 prior model identity drift")
        require(cell.get("benchmark") == MARKETS[market]["benchmark"], "STAGE2_L1 prior benchmark drift")
        expected_stage = "stage0_p0" if anchor else "stage1_m1"
        require(spec["source_stage"] == expected_stage, "STAGE2_L1 prior stage flag drift")
        short = spec["candidate_id"][:8]
        cdir = root / prefix / "cells" / f"cell/{market}/{model_key}/{short}/{combo_key}/phase{phase:02d}"
        rec_file = cdir / "receipt.json"
        require(rec_file.is_file(), f"STAGE2_L1 prior receipt missing: {cdir}")
        rec = json.loads(rec_file.read_text(encoding="utf-8"))
        require(rec.get("status") == "PASS" and rec.get("kind") == "cell", "STAGE2_L1 prior receipt not PASS/cell")
        require(rec.get("job_sha256") == cell.get("job_sha256"), "STAGE2_L1 prior receipt job SHA drift")
        expected_run = STAGE0_RUN_ID if anchor else STAGE1_RUN_ID
        expected_sha = STAGE0_CODE_SHA if anchor else STAGE1_CODE_SHA
        require(
            rec.get("run_id") == expected_run and rec.get("code_sha") == expected_sha,
            "STAGE2_L1 prior receipt lineage drift",
        )
        hashes = rec.get("artifact_sha256")
        require(
            isinstance(hashes, dict) and set(hashes) == set(p0.ARTIFACTS["cell"]),
            "STAGE2_L1 prior receipt artifact manifest incomplete",
        )
        require(cell.get("artifact_sha256") == hashes, "STAGE2_L1 prior summary artifact manifest drift")
        for name, digest in hashes.items():
            target = cdir / name
            require(target.is_file() and sha256_file(target) == digest, f"STAGE2_L1 prior bytes drift: {target}")
        out.append(
            {
                "market": market,
                "benchmark": cell["benchmark"],
                "model_key": model_key,
                "candidate_id": cell["candidate_id"],
                "label_horizon": int(spec["label_horizon"]),
                "anchor": anchor,
                "source_stage": expected_stage,
                "combo_key": combo_key,
                "topk": int(cell["topk"]),
                "n_drop": int(cell["n_drop"]),
                "phase": phase,
                "metrics": cell["metrics"],
                "stressed": cell["stressed"],
                "artifact_sha256": hashes,
                "job_sha256": cell["job_sha256"],
                "receipt_sha256": sha256_file(rec_file),
                "provider_fingerprint": rec.get("provider_fingerprint", ""),
            }
        )
    require(len(out) == 96, "STAGE2_L1 prior coverage must be 96 cells")
    for market in MARKETS:
        require(len([c for c in out if c["market"] == market]) == 48, f"STAGE2_L1 prior coverage drift: {market}")
    require(
        {(c["model_key"], c["combo_key"]) for c in out} == {(m, k) for m in ALL_12_MODELS for k in PRIOR_COMBOS},
        "STAGE2_L1 prior grid must be 12 models x 2 prior combos",
    )
    return sorted(out, key=lambda row: (row["market"], row["model_key"], row["combo_key"], row["phase"]))


def rank_market_120_strategies(cells_240: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank one market's 12 models x 10 combos = 120 strategies from 240 cells.

    Expects exactly 240 cells (120 (model, combo) pairs x 2 phases). Sort key is
    ``(worst_relative_excess_cagr, mean_relative_excess_cagr, mean_ir,
    mean_sharpe, worst_max_drawdown)`` descending; drawdowns are negative so
    descending prefers the shallowest loss.
    """
    require(isinstance(cells_240, list) and len(cells_240) == 240, "STAGE2_L1 ranking needs exactly 240 cells")
    by_pair: dict[tuple[str, str], dict[int, dict]] = {}
    markets = {cell["market"] for cell in cells_240}
    require(len(markets) == 1, "STAGE2_L1 ranking must stay within one market")
    for cell in cells_240:
        require(cell["model_key"] in ALL_12_MODELS, "STAGE2_L1 ranking unknown model")
        require(cell["combo_key"] in _combo_specs(), "STAGE2_L1 ranking unknown combo")
        require(cell["phase"] in (0, 10), "STAGE2_L1 ranking phase invalid")
        key = (cell["model_key"], cell["combo_key"])
        require(cell["phase"] not in by_pair.setdefault(key, {}), "STAGE2_L1 duplicate ranking cell")
        by_pair[key][cell["phase"]] = cell
    require(
        len(by_pair) == 120 and all(set(v) == {0, 10} for v in by_pair.values()), "STAGE2_L1 ranking pairs incomplete"
    )
    require({m for m, _ in by_pair} == set(ALL_12_MODELS), "STAGE2_L1 ranking must cover all 12 models")
    require({k for _, k in by_pair} == set(_combo_specs()), "STAGE2_L1 ranking must cover all 10 combos")
    rows = []
    for (model_key, combo_key), phases in by_pair.items():
        m0, m10 = phases[0]["metrics"], phases[10]["metrics"]
        s0, s10 = phases[0]["stressed"], phases[10]["stressed"]
        rows.append(
            {
                "model_key": model_key,
                "combo_key": combo_key,
                "label_horizon": int(ALL_12_MODELS[model_key]["label_horizon"]),
                "phase0_relative_excess_cagr": float(m0["relative_excess_cagr"]),
                "phase10_relative_excess_cagr": float(m10["relative_excess_cagr"]),
                "worst_relative_excess_cagr": min(
                    float(m0["relative_excess_cagr"]), float(m10["relative_excess_cagr"])
                ),
                "mean_relative_excess_cagr": 0.5
                * (float(m0["relative_excess_cagr"]) + float(m10["relative_excess_cagr"])),
                "worst_strategy_cagr": min(float(m0["strategy_cagr"]), float(m10["strategy_cagr"])),
                "mean_strategy_cagr": 0.5 * (float(m0["strategy_cagr"]) + float(m10["strategy_cagr"])),
                "mean_sharpe": 0.5 * (float(m0["sharpe"]) + float(m10["sharpe"])),
                "mean_ir": 0.5 * (float(m0["information_ratio"]) + float(m10["information_ratio"])),
                "worst_max_drawdown": min(float(m0["strategy_max_drawdown"]), float(m10["strategy_max_drawdown"])),
                "worst_stressed10bp_total_return": min(
                    float(s0["10bp"]["strategy_total_return"]), float(s10["10bp"]["strategy_total_return"])
                ),
            }
        )
    rows.sort(
        key=lambda r: (
            r["worst_relative_excess_cagr"],
            r["mean_relative_excess_cagr"],
            r["mean_ir"],
            r["mean_sharpe"],
            r["worst_max_drawdown"],
        ),
        reverse=True,
    )
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    return rows


def select_top4_diverse_finalists(
    ranked_120: list[dict[str, Any]], max_per_model: int = 2, top_n: int = 4
) -> list[dict[str, Any]]:
    """Pick the Top ``top_n`` challenge strategies with model diversity.

    Walks the 120-row ranking from best to worst and admits at most
    ``max_per_model`` combos per ``model_key`` so a single fine-tuned model
    cannot occupy the whole Stage 3 shortlist; at least two distinct models
    end up represented with the default (2, 4).
    """
    require(isinstance(ranked_120, list) and len(ranked_120) == 120, "STAGE2_L1 finalist selection needs 120 rows")
    require(isinstance(max_per_model, int) and max_per_model >= 1, "STAGE2_L1 max_per_model must be >= 1")
    require(isinstance(top_n, int) and 2 <= top_n <= 12, "STAGE2_L1 top_n must be within 2..12")
    picked: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for row in ranked_120:
        if counts.get(row["model_key"], 0) >= max_per_model:
            continue
        counts[row["model_key"]] = counts.get(row["model_key"], 0) + 1
        picked.append({**row, "finalist_rank": len(picked) + 1})
        if len(picked) == top_n:
            break
    require(len(picked) == top_n, "STAGE2_L1 insufficient diverse finalists in ranking")
    require(len({row["model_key"] for row in picked}) >= 2, "STAGE2_L1 finalists must span >= 2 models")
    return picked


STAGE2_SCOREBOARD_COLUMNS = (
    "market",
    "benchmark",
    "model_key",
    "candidate_id",
    "label_horizon",
    "anchor",
    "source_stage",
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
    "source_signal_sha256",
)


def render_stage2_scoreboard(summary: dict[str, Any], csv_path: str | Path) -> Path:
    """Render the 480-cell scoreboard CSV (384 new l1cells + 96 prior cells)."""
    cells = summary.get("cells")
    require(isinstance(cells, list) and len(cells) == 480, "STAGE2_L1 summary must carry exactly 480 cells")
    out = Path(csv_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(STAGE2_SCOREBOARD_COLUMNS))
        writer.writeheader()
        for cell in sorted(cells, key=lambda row: (row["market"], row["model_key"], row["combo_key"], row["phase"])):
            metrics = cell.get("metrics") or {}
            writer.writerow(
                {
                    "market": cell["market"],
                    "benchmark": cell["benchmark"],
                    "model_key": cell["model_key"],
                    "candidate_id": cell["candidate_id"],
                    "label_horizon": cell.get("label_horizon", ""),
                    "anchor": bool(cell.get("anchor", False)),
                    "source_stage": cell.get("source_stage", ""),
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
                    "stressed10bp_strategy_total_return": (cell.get("stressed") or {})
                    .get("10bp", {})
                    .get("strategy_total_return"),
                    "stressed20bp_strategy_total_return": (cell.get("stressed") or {})
                    .get("20bp", {})
                    .get("strategy_total_return"),
                    "account_return_max_error": metrics.get("account_return_max_error"),
                    "source_signal_sha256": cell.get("source_signal_sha256", "")
                    or (cell.get("artifact_sha256") or {}).get("signal.parquet", ""),
                }
            )
    return out


STAGE2_RANKING_COLUMNS = (
    "market",
    "rank",
    "finalist_rank",
    "model_key",
    "label_horizon",
    "combo_key",
    "worst_relative_excess_cagr",
    "mean_relative_excess_cagr",
    "phase0_relative_excess_cagr",
    "phase10_relative_excess_cagr",
    "worst_strategy_cagr",
    "mean_strategy_cagr",
    "mean_sharpe",
    "mean_ir",
    "worst_max_drawdown",
    "worst_stressed10bp_total_return",
)


def render_stage2_rankings(summary: dict[str, Any], csv_path: str | Path) -> Path:
    """Render the 2 x 120 strategy ranking CSV with finalist ranks annotated."""
    rankings = summary.get("rankings")
    finalists = summary.get("finalists")
    require(isinstance(rankings, dict) and set(rankings) == set(MARKETS), "STAGE2_L1 rankings must cover both markets")
    require(
        isinstance(finalists, dict) and set(finalists) == set(MARKETS), "STAGE2_L1 finalists must cover both markets"
    )
    for market in MARKETS:
        require(
            isinstance(rankings[market], list) and len(rankings[market]) == 120, f"STAGE2_L1 {market} ranking drift"
        )
        require(
            isinstance(finalists[market], list) and len(finalists[market]) == 4, f"STAGE2_L1 {market} finalists drift"
        )
    out = Path(csv_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(STAGE2_RANKING_COLUMNS))
        writer.writeheader()
        for market in ("csi300", "csi500"):
            finalist_rank = {int(row["rank"]): int(row["finalist_rank"]) for row in finalists[market]}
            for row in rankings[market]:
                writer.writerow(
                    {
                        **{key: row.get(key, "") for key in STAGE2_RANKING_COLUMNS},
                        "market": market,
                        "finalist_rank": finalist_rank.get(int(row["rank"]), ""),
                    }
                )
    return out
