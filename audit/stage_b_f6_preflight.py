"""F6 frozen-input preflight for winner/baseline phases 0, 4, 6, 10, 15.

Read-only, stdlib-only. Verify original exported report bytes against BOTH
the frozen full-result metadata and the separate GitHub export manifest.
For original signals and decisions, verify SHA256 only when actual source bytes
are available. Never substitute repaired winner phase0 assets for other phases
or treat a missing input as a successful F6 execution replay.

No Qlib/Modal/training/production changes or Volume writes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

WINNER = "4e908173705c76fee3782be37068672a3a845bd61894202f3152afe3b9d81ef2"
BASELINE = "23b92de05cf36c82998de684d0fbf64d81ee54490d755bd3cf96311c00286785"
PHASES = (0, 4, 6, 10, 15)
SNAPSHOT = "51756897fc75230493194aef2e48815e4e9f3cc7426cc135f47bb8ba8e0d21e1"
PREFIX = "/vol/csi1000_stage_b/" + SNAPSHOT + "/"
FROZEN = "results/csi1000_stage_b/stage_b_full_51756897fc752304.json"
EXPORT_ROOT = "results/csi1000_stage_b/audit_reports"
ORIGINAL_PHASE0 = "audit/evidence/winner_phase0/raw"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for buf in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(buf)
    return digest.hexdigest()


def require(condition, reason):
    if not condition:
        raise ValueError("F6 frozen input gate FAIL: " + reason)


def source_suffix(path: str, *, phase: int, candidate_id: str) -> Path:
    """Honor the exact frozen path; never reconstruct baseline preflight paths."""
    require(isinstance(path, str) and path.startswith(PREFIX),
            "source outside frozen result snapshot")
    subpath = path[len(PREFIX):]
    relative = Path(subpath)
    require(not relative.is_absolute() and ".." not in relative.parts,
            "unsafe snapshot artifact path")
    require(len(relative.parts) >= 4 and relative.parts[-3] == candidate_id
            and relative.parts[-2] == f"phase{phase:02d}" and
            relative.parts[-4] == "phases",
            "candidate/phase path mismatch")
    if candidate_id == BASELINE and phase == 0:
        require(len(relative.parts) >= 7 and
                relative.parts[0] == "_preflight" and "repeat_b" in relative.parts,
                "baseline phase0 must use frozen preflight repeat_b pointer")
    else:
        require(relative.parts[0] == "phases",
                "ordinary phase unexpectedly uses another artifact namespace")
    return relative


def select_phases(payload: dict):
    require(payload["manifest"]["snapshot_token"] == SNAPSHOT,
            "frozen result snapshot drift")
    candidates = payload["ranked_candidates"]
    output = {}
    for name, cid in (("winner", WINNER), ("baseline", BASELINE)):
        matches = [c for c in candidates if c["candidate_id"] == cid]
        require(len(matches) == 1, f"{name}: missing/ambiguous frozen candidate")
        phases = matches[0]["phase_results"]
        require(sorted(p["phase"] for p in phases) == list(PHASES),
                f"{name}: phase count/order drift")
        output[name] = {p["phase"]: p for p in phases}
        for p in phases:
            require(p["candidate_id"] == cid and p["snapshot_token"] == SNAPSHOT,
                    f"{name} phase{p['phase']:02d}: frozen identity drift")
    return output


def validate_report_export(path: Path, exported: dict, frozen_phase: dict) -> dict:
    expected = frozen_phase["report_artifact"]
    require(exported["volume_source_path"] == expected["path"],
            "GitHub export points to different frozen phase source")
    require(exported["expected_sha256"] == expected["sha256"] and
            exported["actual_sha256"] == expected["sha256"] and
            exported["match"] is True,
            "export SHA does not agree with frozen result")
    require(path.is_file(), "frozen report export missing: " + exported["file"])
    actual = sha256_file(path)
    require(actual == expected["sha256"],
            "report source bytes differ from frozen SHA: " + exported["file"])
    require(path.stat().st_size == exported["size_bytes"],
            "report source byte count drift: " + exported["file"])
    return {"status": "VERIFIED_FROZEN_BYTES", "sha256": actual,
            "size_bytes": path.stat().st_size, "export": exported["file"]}


def check_optional(path: Path | None, expected: str, label: str) -> dict:
    if path is None or not path.is_file():
        return {"status": "BLOCKED_MISSING_ORIGINAL_SOURCE", "expected_sha256": expected}
    actual = sha256_file(path)
    require(actual == expected, f"{label}: original source SHA mismatch")
    return {"status": "VERIFIED_FROZEN_BYTES", "expected_sha256": expected,
            "actual_sha256": actual, "size_bytes": path.stat().st_size}


def preflight(repo_root: Path, *, snapshot_root: Path | None = None) -> dict:
    """Never claim all ten fixed replays were executed; this is INPUT preflight."""
    repo = Path(repo_root)
    payload = json.loads((repo / FROZEN).read_text(encoding="utf-8"))
    exported = json.loads((repo / EXPORT_ROOT / "manifest.json").read_text(encoding="utf-8"))
    require(exported["source_snapshot"] == SNAPSHOT and
            exported["result_json"] == FROZEN and
            exported["winner_candidate_id"] == WINNER and
            exported["baseline_candidate_id"] == BASELINE,
            "original report export manifest identity drift")
    files = exported["files"]
    require(len(files) == 10, "expected exactly 10 original report exports")
    by_source = {v["volume_source_path"]: v for v in files}
    require(len(by_source) == 10, "duplicate report export source pointer")
    rows = []
    phases = select_phases(payload)
    for candidate, cid in (("winner", WINNER), ("baseline", BASELINE)):
        for phase_id in PHASES:
            p = phases[candidate][phase_id]
            sources = {
                kind: p[kind + "_artifact"]
                for kind in ("report", "signal", "decision")
            }
            relative = {}
            for kind, artifact in sources.items():
                src = source_suffix(artifact["path"],
                                    phase=phase_id, candidate_id=cid)
                require(src.name == {"report": "report.parquet", "signal": "signal.parquet",
                                     "decision": "decisions.json"}[kind],
                        kind + ": wrong original file name")
                relative[kind] = src
            require(relative["report"].parent == relative["signal"].parent ==
                    relative["decision"].parent,
                    "phase original artifacts in different source directories")
            report_entry = by_source.get(sources["report"]["path"])
            require(report_entry is not None, "missing frozen report export manifest entry")
            path = repo / EXPORT_ROOT / report_entry["file"]
            # Export path must be contained under the expected report directory.
            require(path.resolve().is_relative_to((repo / EXPORT_ROOT).resolve()),
                    "report export path escapes repository")
            report = validate_report_export(path, report_entry, p)
            if snapshot_root is not None:
                root = Path(snapshot_root).resolve()
                path_sig = (root / relative["signal"]).resolve()
                path_dec = (root / relative["decision"]).resolve()
                require(path_sig.is_relative_to(root) and path_dec.is_relative_to(root),
                        "snapshot input escaped root")
            elif candidate == "winner" and phase_id == 0:
                # Special case: original phase0 source bytes were exported and
                # committed separately before the F1 correction.
                path_sig = repo / ORIGINAL_PHASE0 / "signal.parquet"
                path_dec = repo / ORIGINAL_PHASE0 / "decisions.json"
            else:
                path_sig = path_dec = None
            signal = check_optional(path_sig, sources["signal"]["sha256"],
                                    candidate + f" phase{phase_id:02d} signal")
            decisions = check_optional(path_dec, sources["decision"]["sha256"],
                                       candidate + f" phase{phase_id:02d} decisions")
            status = ("READY_FOR_FROZEN_SIGNAL_REPLAY" if
                      signal["status"] == decisions["status"] == "VERIFIED_FROZEN_BYTES"
                      else "BLOCKED_MISSING_ORIGINAL_SIGNAL_OR_DECISIONS")
            rows.append({
                "candidate": candidate, "candidate_id": cid, "phase": phase_id,
                "source_directory_relative_to_snapshot": str(relative["report"].parent),
                "report": report, "signal": signal, "decisions": decisions,
                "frozen_decision_count": sources["decision"]["decision_count"],
                "frozen_order_count": sources["decision"]["order_count"],
                "input_readiness": status,
                "f6_fixed_replay": "NOT_EXECUTED",
                "f6_independent_fixed_ledger": "NOT_EXECUTED",
                "real_world_execution": "BLOCKED",
            })
    require(len(rows) == 10, "missing candidate/phase pair")
    require({r["report"]["export"] for r in rows} ==
            {v["file"] for v in files}, "unused or repeated frozen export")
    ready = sum(r["input_readiness"] == "READY_FOR_FROZEN_SIGNAL_REPLAY" for r in rows)
    return {
        "schema": "stage_b_f6_frozen_input_preflight_v1",
        "snapshot": SNAPSHOT, "frozen_result_json_sha256": sha256_file(repo / FROZEN),
        "provider_live_fingerprint_check": "NOT_PERFORMED",
        "total_phases": len(rows), "report_hashes_verified": len(rows),
        "source_pairs_ready": ready, "source_pairs_blocked": 10 - ready,
        "nine_unreplayed_phases_status": "BLOCKED_UNTIL_ORIGINAL_INPUTS_AND_EXECUTION_REPLAY",
        "overall_f6": "BLOCKED",
        "rows": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path,
                        default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--snapshot-root", type=Path, default=None,
                        help="optional read-only extracted snapshot root; never writes to it")
    parser.add_argument("--require-complete", action="store_true",
                        help="nonzero exit if not all ten ORIGINAL signals+decisions are available")
    parser.add_argument("--output-json", type=Path, default=None,
                        help="optional NEW report file outside /vol; never overwrite")
    args = parser.parse_args()
    output = preflight(args.repo_root.resolve(), snapshot_root=args.snapshot_root)
    if args.require_complete:
        require(output["source_pairs_ready"] == 10, "some original phase sources unavailable")
    serialized = json.dumps(output, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.output_json is None:
        print(serialized, end="")
    else:
        target = args.output_json.resolve()
        require(not target.is_relative_to(Path("/vol")), "cannot write under /vol")
        require(not target.exists(), "existing output cannot be overwritten")
        require(not target.is_relative_to(args.repo_root.resolve()),
                "do not write verification outputs into checked-in source checkout")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8") as stream:
            stream.write(serialized)


if __name__ == "__main__":
    main()
