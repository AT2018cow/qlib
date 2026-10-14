"""Frozen CSI1000 L4 five-phase final. Pure-Python; zero training, no Modal.

The original Stage-B already produced frozen freq20 phases 0/4/6/10/15
for all three underlying L4 model IDs. L4 strictly checks those actual
Stage-B bytes and runs all four distinct F1 portfolios, not new model fits.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from audit.csi1000_layered_search_core import (
    BASELINE_CANDIDATE,
    decide_product,
    final_verdict,
    require,
    stable_sha,
)
from audit.csi1000_layered_search_selection import freeze_finalists

FROZEN_MANIFEST_PATH = "docs/experiments/58-csi1000-l4-finalists-frozen-20261014.json"
FROZEN_MANIFEST_SHA = "b6bd3b3d687d017fb6586d68e88ab71691c2225eb4545e853560a776c9cae1c8"
FINAL_PHASES = (0, 4, 6, 10, 15)
BASELINE_CONFIG_SHA = "e7d1fab6c5e8309c54bbc34bd7741151421eb67ce447579a61d8cfe72d548191"
CHALLENGER_CONFIG_SHAS = (
    "1ca6fae56e8c4e40c73b1a45866825930aecba36ec529544a50da5408c93d317",
    "95ae81ba885dad4e6eb31838e0bbea55d9281807289efe9ce150cbc12edc74b6",
    "3ddff2cd6138f0adf003ff6d899a496ed2393ba26dddccc6ce7af274532535e0",
)
EXPECTED_MODEL_IDS = {
    BASELINE_CANDIDATE,
    "c98856b460aba687640d422e85a82a545bf5b6ca932e906a180699e0f6cad19b",
    "a419047ab97ec0273ebe67791b6e225e824b2764f5932d73651efd89add5a677",
}


def frozen_l4_manifest(repo_root: str | Path = ".") -> dict:
    """Verify registered lineup and the existing selection engine independently."""
    path = Path(repo_root) / FROZEN_MANIFEST_PATH
    raw = json.loads(path.read_text(encoding="utf-8"))
    require(
        raw.get("stage") == "l4" and raw.get("status") == "FROZEN_FINALIST_PLAN_NOT_EXECUTED", "STOP_L4_MANIFEST_STAGE"
    )
    configs = raw["configurations"]
    require(len(configs) == 4, "STOP_L4_FOUR_CONFIGURATIONS")
    for config in configs:
        candidate = config["candidate_id"]
        require(candidate in EXPECTED_MODEL_IDS, "STOP_L4_MODEL_NOT_PRESELECTED")
        expected = stable_sha({"candidate_id": candidate, "topk": config["topk"], "dropn": config["dropn"]})
        require(config["config_sha256"] == expected, "STOP_L4_CONFIG_SHA")
    require(
        tuple(c["config_sha256"] for c in configs) == (BASELINE_CONFIG_SHA, *CHALLENGER_CONFIG_SHAS),
        "STOP_L4_CHALLENGER_ORDER",
    )
    reproduced = freeze_finalists(configs[1:], configs[0])
    require(reproduced == raw, "STOP_L4_FREEZE_SELECTION_DIVERGED")
    require(raw["finalist_manifest_sha256"] == FROZEN_MANIFEST_SHA, "STOP_L4_FROZEN_MANIFEST_SHA_CHANGED")
    require(raw["historic_data"] == "HISTORICALLY_CONSUMED", "STOP_L4_HISTORIC_RESEARCH")
    return raw


def l4_cell_specs(manifest: dict) -> list[dict]:
    configs = manifest["configurations"]
    require(manifest["finalist_manifest_sha256"] == FROZEN_MANIFEST_SHA, "STOP_L4_PLAN_IDENTITY")
    jobs = [
        {**{k: c[k] for k in ("candidate_id", "topk", "dropn", "config_sha256")}, "phase": phase}
        for c in configs
        for phase in FINAL_PHASES
    ]
    require(
        len(jobs) == 20 and len({(x["config_sha256"], x["phase"]) for x in jobs}) == 20, "STOP_L4_NOT_20_DISTINCT_CELLS"
    )
    return jobs


def check_stage_b_source_coverage(stage_b_result: dict, manifest: dict) -> dict:
    """Confirm all 3×5 authentic frozen Stage-B source signals exist.

    This does NOT claim to SHA-verify the remote bytes: the remote worker must
    resolve and hash every report/signal/decision artifact before using it.
    """
    from audit.csi1000_layered_search_replay import lookup_phase
    from csi1000_stage_b_core import stage_b_candidates, REFERENCE_PHASES

    require(tuple(REFERENCE_PHASES) == FINAL_PHASES, "STOP_L4_PHASE_PROTOCOL_DRIFT")
    selected = set(c["candidate_id"] for c in manifest["configurations"])
    old_candidates = {c["candidate_id"]: c for c in stage_b_candidates()}
    require(selected <= set(old_candidates) and len(selected) == 3, "STOP_L4_MODEL_MISSING_FROM_STAGE_B")
    observed = []
    known = stage_b_result.get("manifest", {}).get("candidate_model_config_sha256", {})
    for candidate in sorted(selected):
        require(bool(re.fullmatch(r"[a-f0-9]{64}", candidate)), "STOP_L4_INVALID_CANDIDATE")
        require(bool(re.fullmatch(r"[a-f0-9]{64}", known.get(candidate, ""))), "STOP_L4_MODEL_CONFIG_SHA_MISSING")
        for phase in FINAL_PHASES:
            row = lookup_phase(stage_b_result, candidate, phase)
            require(
                row["candidate_id"] == candidate
                and row["phase"] == phase
                and row.get("snapshot_token") == stage_b_result["manifest"]["snapshot_token"],
                "STOP_L4_FROZEN_PHASE_IDENTITY",
            )
            chunks = row.get("chunk_predictions", [])
            require(
                len(chunks) == row.get("n_retrains")
                and chunks
                and all(
                    ch.get("model_config_sha256") == known[candidate] and ch.get("prediction_content_sha256")
                    for ch in chunks
                ),
                "STOP_L4_FROZEN_PHASE_MODEL_MISMATCH",
            )
            for kind in ("signal", "report", "decision"):
                artifact = row.get(kind + "_artifact") or {}
                require(
                    bool(artifact.get("sha256")) and bool(artifact.get("path")),
                    f"STOP_L4_SOURCE_POINTER_MISSING_{kind}",
                )
            observed.append(
                {
                    "candidate_id": candidate,
                    "phase": phase,
                    "frozen_signal_sha256": row["signal_artifact"]["sha256"],
                    "frozen_model_config_sha256": known[candidate],
                }
            )
    require(len(observed) == 15, "STOP_L4_SOURCE_COUNT")
    return {"model_count": 3, "source_model_phases": 15, "sources": observed, "source_digest": stable_sha(observed)}


def l4_decision_from_receipts(manifest: dict, receipts: list[dict], report_loader) -> dict:
    """Recompute all five-phase product gates from all 20 verified reports.

    Caller MUST verify complete cell receipts and every byte SHA, then pass
    the actual report_loader. Embedded cell PASS/performance claims alone
    are insufficient. No partial finalist is silently dropped.
    """
    from audit.csi1000_layered_search_replay import paired_rows

    spec = l4_cell_specs(manifest)
    expected = {(x["config_sha256"], x["phase"]) for x in spec}
    keyed = {}
    for row in receipts:
        key = (row["config_sha256"], row["phase"])
        require(key not in keyed and key in expected, "STOP_L4_DUPLICATE_OR_FOREIGN_CELL")
        require(
            row["status"] == "PASS"
            and row["n_days"] == 424
            and row["coverage_ok"] is True
            and row["ledger_ok"] is True
            and row["data_ok"] is True,
            "STOP_L4_UNVERIFIED_CELL",
        )
        keyed[key] = row
    require(set(keyed) == expected, "STOP_L4_INCOMPLETE_FIVE_PHASE_FINAL")
    baseline = [keyed[(BASELINE_CONFIG_SHA, p)] for p in FINAL_PHASES]
    for row in baseline:
        require(
            row["candidate_id"] == BASELINE_CANDIDATE and row["topk"] == 20 and row["dropn"] == 2,
            "STOP_L4_BASELINE_NOT_ORIGINAL_CONTROL",
        )
    summaries = []
    inputs = []
    for cfg in manifest["configurations"][1:]:
        chall = [keyed[(cfg["config_sha256"], p)] for p in FINAL_PHASES]
        for c, b in zip(chall, baseline):
            require(c["phase"] == b["phase"], "STOP_L4_PHASE_MISMATCH")
            cp, bp = report_loader(c), report_loader(b)
            require(cp.index.equals(bp.index) and len(cp) == 424, "STOP_L4_DAILY_INDEX_MISMATCH")
            require("bench" in cp and "bench" in bp and cp["bench"].equals(bp["bench"]), "STOP_L4_BENCHMARK_MISMATCH")
        rows_c, rows_b, ratio = paired_rows(chall, baseline, report_loader)
        verdict, gates = final_verdict(rows_c, rows_b, ratio)
        summaries.append(
            {"candidate_id": cfg["candidate_id"], "config_sha256": cfg["config_sha256"], "verdict": verdict, **gates}
        )
        inputs.append(
            {
                "config_sha": cfg["config_sha256"],
                "challenger_phases": rows_c,
                "baseline_phases": rows_b,
                "turnover_ratio": ratio,
            }
        )
    decision, detail = decide_product(inputs, baseline_eligible=True)
    require(decision != "STOP_PRODUCT_BLOCKED", "STOP_L4_BASELINE_INELIGIBLE")
    return {
        "stage": "l4",
        "status": "L4_RESEARCH_COMPLETE_NOT_RELEASED",
        "decision": decision,
        "decision_detail": detail,
        "challenger_scoreboard": summaries,
        "completed_cells": 20,
        "finalist_manifest_sha256": FROZEN_MANIFEST_SHA,
        "historic_data": "HISTORICALLY_CONSUMED",
        "promotion_authorized": False,
    }
