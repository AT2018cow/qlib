"""Offline reconcile for the CSI1000 layered search (PR-S0).

Recomputes paired improvements, fees and STOP/decision gates from committed
cell records (never trusting incoming PASS claims), verifies SHA256SUMS, and
emits decision.json. Pure Python; never contacts Modal, never trains, never
modifies production. ``--write-demo`` produces synthetic pass/fail bundles so
reviewers can audit the gates without any billed run or private data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audit.csi1000_layered_search_core import (
    GLOBAL_CAP_USD,
    GLOBAL_FIT_CAP,
    L1_CELL_CAP,
    L4_CELL_CAP,
    L4_CONFIG_CAP,
    STOP_CODES,
    check_cell_record,
    decide_product,
)

PLACEHOLDER_IDS = ("", "pending", "null", "unknown")


def require(ok: bool, why: str) -> None:
    if not ok:
        raise ValueError("RECONCILE_BLOCKED: " + why)


def verify_sha256sums(root: str | Path) -> Dict[str, Any]:
    """Verify SHA256SUMS.txt against actual bytes (excluding itself)."""
    root = Path(root)
    sums = root / "SHA256SUMS.txt"
    require(sums.is_file(), f"SHA256SUMS.txt missing under {root}")
    bad: List[str] = []
    n = 0
    for line in sums.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, _, rel = line.partition("  ")
        target = root / rel.strip()
        require(target.is_file(), f"listed file missing: {rel}")
        h = hashlib.sha256()
        with target.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        n += 1
        if h.hexdigest() != digest.strip():
            bad.append(rel.strip())
    require(not bad, f"SHA mismatch: {bad}")
    return {"files": n, "status": "SHA_VERIFIED"}


def check_reuse_attestations(attestations: List[Dict[str, Any]], expected_keys: set) -> Dict[str, Any]:
    """Every reused cell needs a complete attestation; silent reuse is refused."""
    keys = [a.get("cell_key") for a in attestations]
    require(len(set(keys)) == len(keys), "duplicate reuse attestations")
    require(set(keys) == set(expected_keys), "reuse attestations do not cover the overlap roster")
    for a in attestations:
        for field in (
            "cell_key",
            "prior_run_id",
            "prior_code_sha",
            "output_sha256",
            "source_sha256",
            "quote_sha256",
            "engine",
            "equivalence",
            "verified_utc",
        ):
            require(a.get(field), f"reuse attestation missing {field}: {a.get('cell_key')}")
        require(
            a["equivalence"] == "IDENTICAL_CODE" or a["equivalence"].startswith("REVIEWED_EQUIVALENT:"),
            f"reuse equivalence undeclared: {a.get('cell_key')}",
        )
    return {"attestations": len(attestations)}


def check_stage_summary(summary: Dict[str, Any]) -> Dict[str, Any]:
    """Validate one stage summary's completeness and no-overwrite invariants."""
    require(summary.get("training_performed") in (False,), "training flag must be false for PR-S0 stages")
    require(summary.get("promotion_authorized") is False, "promotion must never be pre-authorized")
    require(summary.get("source_volume_read_only") is True, "source must stay read-only")
    expected = int(summary.get("expected", -1))
    completed = int(summary.get("completed", -1))
    blocked = int(summary.get("blocked", -1))
    require(expected >= 0 and completed >= 0 and blocked >= 0, "summary counters missing")
    require(completed + blocked == expected, "summary counters inconsistent")
    if summary.get("stage") == "l1-c":
        require(expected <= L1_CELL_CAP, "L1 exceeds 264-cell cap")
    verified = summary.get("verified_cells", [])
    require(len(verified) == completed, "verified cell list does not match completed count")
    for record in verified:
        check_cell_record(record)
    for row in summary.get("blocked_cells", []):
        require(row.get("cell_key") and row.get("reason"), "blocked cell without reason")
    reuse = summary.get("reuse_attestations", [])
    reuse_info: Dict[str, Any] = {"attestations": 0}
    if reuse or summary.get("stage") == "l1-b":
        overlap = {c["cell_key"] for c in verified} & {a.get("cell_key") for a in reuse}
        require(len(reuse) == len(overlap) and overlap, "l1-b reuse attestations incomplete")
        reuse_info = check_reuse_attestations(reuse, overlap)
    if summary.get("stage") == "l1-b" and summary.get("status") == "PASS":
        # A PASS l1-b summary is a final claim: the full 144 roster verified
        # with exactly the 32-cell L1-A overlap attested. Anything less is an
        # incomplete batch, never a passing evaluation.
        from audit.csi1000_layered_search_core import enumerate_cells

        overlap_roster = {c["cell_key"] for c in enumerate_cells("l1-b")} & {
            c["cell_key"] for c in enumerate_cells("l1-a")
        }
        require(expected == 144 and completed == 144 and blocked == 0, "l1-b PASS needs 144/144 with 0 blocked")
        require(len(verified) == 144, "l1-b PASS needs 144 verified cells")
        require(
            {a.get("cell_key") for a in reuse} == overlap_roster and len(reuse) == 32,
            "l1-b PASS needs exactly the 32-cell overlap attested",
        )
        reuse_info = check_reuse_attestations(reuse, overlap_roster)
    return {
        "stage": summary.get("stage"),
        "expected": expected,
        "completed": completed,
        "blocked": blocked,
        **reuse_info,
    }


def check_trial_ledger(path: str | Path) -> Dict[str, Any]:
    """Trial ledger: run-scoped unique keys, real App IDs, booked finite spend."""
    rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    keys = [(r.get("run_id"), r.get("cell_key")) for r in rows]
    require(all(k[0] and k[1] for k in keys), "trial row missing run_id or cell_key")
    require(len(set(keys)) == len(keys), "duplicate (run_id, cell_key) in trial ledger")
    for r in rows:
        require(r.get("cell_key"), "trial row missing cell_key")
        require(r.get("app_id") not in PLACEHOLDER_IDS, f"trial {r.get('cell_key')} has no real App ID")
        require(r.get("status") in ("PASS", "BLOCKED", "RESUME_VERIFIED"), "unknown trial status")
        spend = r.get("spend_usd")
        require(
            isinstance(spend, (int, float)) and math.isfinite(spend) and spend >= 0,
            f"trial {r.get('cell_key')} spend not booked as finite USD",
        )
        fits = int(r.get("fits", 0))
        require(fits >= 0, "trial fits invalid")
    return {"trials": len(rows)}


def build_decision(
    finalists: List[Dict[str, Any]], spent_usd: float, fits_used: int, baseline_eligible: bool = True
) -> Dict[str, Any]:
    """Emit the product decision; verdicts are recomputed inside decide_product."""
    require(isinstance(spent_usd, (int, float)) and math.isfinite(spent_usd) and spent_usd >= 0, "spend unknown")
    require(spent_usd <= GLOBAL_CAP_USD + 1e-9, "spend exceeds USD30 global cap")
    require(isinstance(fits_used, int) and 0 <= fits_used <= GLOBAL_FIT_CAP, "fit count out of range")
    code, detail = decide_product(finalists, baseline_eligible=baseline_eligible)
    require(code in STOP_CODES, "unknown decision code")
    return {
        "decision": code,
        "detail": detail,
        "spent_usd": spent_usd,
        "fits_used": fits_used,
        "l4_configs": len(finalists),
        "l4_cap": L4_CONFIG_CAP,
        "l4_cell_cap": L4_CELL_CAP,
        "historic_tail": "HISTORICALLY_CONSUMED",
        "oos_claim": False,
    }


def build_cli() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="PR-S0 offline reconcile: verify evidence, evaluate STOP/decision gates.")
    p.add_argument("--summary", default="", help="Stage summary JSON to validate.")
    p.add_argument("--ledger", default="", help="trial_ledger.jsonl to validate.")
    p.add_argument("--evidence-dir", default="", help="Directory containing SHA256SUMS.txt to verify.")
    p.add_argument(
        "--finalists", default="", help="Finalists JSON (recomputed challenger/baseline phases) for decision.json."
    )
    p.add_argument("--spent-usd", type=float, default=0.0, help="Total real billed spend in USD.")
    p.add_argument("--fits-used", type=int, default=0, help="Total real fit() calls.")
    p.add_argument("--baseline-ineligible", action="store_true", help="Baseline reference failed its own gates.")
    p.add_argument("--out", default="", help="Write decision.json here.")
    p.add_argument(
        "--write-demo", default="", help="Write synthetic pass/fail audit bundles under DIR and verify them."
    )
    return p


# ---------------------------------------------------------------------------
# Synthetic offline demo (no Qlib, no provider, no secrets)


def _synthetic_frame(
    drift: float, wiggle: float, turnover_rate: float, cost_rate: float, n: int = 424
) -> Dict[str, List[float]]:
    import pandas as pd

    account = 100_000_000.0
    cash = 100_000_000.0
    accounts, cashes, values, rets, costs, turnovers, tcosts, tturns, bench = [], [], [], [], [], [], [], [], []
    total_cost = 0.0
    total_turn = 0.0
    for i in range(n):
        wave = math.sin(i / 9.0) * wiggle + math.sin(i / 41.0) * wiggle * 0.5
        daily = drift + wave
        opening_account = account
        account *= 1 + daily
        total_cost += opening_account * cost_rate
        total_turn += opening_account * turnover_rate
        accounts.append(account)
        cashes.append(cash)
        values.append(account - cash)
        rets.append(daily + cost_rate)
        costs.append(cost_rate)
        turnovers.append(turnover_rate)
        tcosts.append(total_cost)
        tturns.append(total_turn)
        bench.append(0.0001)
    frame = pd.DataFrame(
        {
            "account": accounts,
            "cash": cashes,
            "value": values,
            "return": rets,
            "cost": costs,
            "total_cost": tcosts,
            "total_turnover": tturns,
            "turnover": turnovers,
            "bench": bench,
        },
        index=pd.date_range("2025-01-02", periods=n, freq="B"),
    )
    frame.index.name = "datetime"
    return frame


def _demo_metrics(frame: Dict[str, List[float]]) -> Dict[str, Any]:
    from audit.independent_stage_b_metrics import REQUIRED, metrics

    dates = list(frame.index.date)
    inputs = {k: frame[k].astype(float).to_list() for k in REQUIRED}
    numbers, _, _, _ = metrics(dates, inputs)
    return {k: (float(v) if isinstance(v, float) else v) for k, v in numbers.items()}


def _demo_record(cell_key: str, candidate: str, phase: int, frame: Dict[str, List[float]]) -> Dict[str, Any]:
    numbers = _demo_metrics(frame)
    from audit.csi1000_layered_search_replay import report_metrics

    _, checks = report_metrics(frame)
    if (
        checks["max_account_return_delta"] > 1e-10
        or checks["max_cost_rate_delta"] > 1e-10
        or checks["max_turnover_rate_delta"] > 1e-10
    ):
        raise ValueError("synthetic demonstration violates the real daily accounting contract")
    tag = hashlib.sha256(cell_key.encode()).hexdigest()
    return {
        "status": "PASS",
        "cell_key": cell_key,
        "candidate_id": candidate,
        "phase": phase,
        "topk": 20,
        "dropn": 2,
        "engine": "offline-demo",
        "execution": "tclose_to_t1open",
        "quote_path": "static_quote_union_f1",
        "quote_sha256": tag,
        "signal_sha256": hashlib.sha256(("sig" + cell_key).encode()).hexdigest(),
        "source_sha256": hashlib.sha256(("src" + cell_key).encode()).hexdigest(),
        "frozen_report_sha256": tag,
        "frozen_decision_sha256": tag,
        "cache_key": hashlib.sha256(("ck" + cell_key).encode()).hexdigest(),
        "output_sha256": hashlib.sha256(frame.to_csv().encode()).hexdigest(),
        "legacy_reproduction": "PASS",
        "final_nav_cny": float(frame["account"].iloc[-1]),
        "total_cost_cny": float(frame["total_cost"].iloc[-1]),
        "total_turnover_cny": float(frame["total_turnover"].iloc[-1]),
        "mean_turnover": float(numbers["mean_turnover"]),
        "n_days": 424,
        "order_count": 100,
        "ledger_ok": True,
        "coverage_ok": True,
        "data_ok": True,
        "training_performed": False,
        "promotion_authorized": False,
        "metrics": numbers,
        "_frame": frame,
    }


def write_demo(root: str | Path) -> Dict[str, Any]:
    """Write synthetic pass + fail bundles and verify the gates against them."""
    from audit.csi1000_layered_search_core import (
        BASELINE_CANDIDATE,
        WINNER_CANDIDATE,
        build_protocol,
        write_sha256sums,
    )
    from audit.csi1000_layered_search_replay import paired_rows

    root = Path(root)
    out: Dict[str, Any] = {"bundles": {}}
    # PASS bundle: 5-phase challenger beats baseline on every gate.
    pb = root / "pass_bundle"
    cells = pb / "cells"
    chall_records, base_records, frames = [], [], {}
    for phase in [0, 4, 6, 10, 15]:
        bf = _synthetic_frame(0.00040, 0.00060, 0.020, 0.00005)
        cf = _synthetic_frame(0.00090, 0.00065, 0.022, 0.00005)
        for fr, key, candidate in (
            (bf, f"base_p{phase:02d}", BASELINE_CANDIDATE),
            (cf, f"chall_p{phase:02d}", WINNER_CANDIDATE),
        ):
            rec = _demo_record(key, candidate, phase, fr)
            d = cells / key
            d.mkdir(parents=True, exist_ok=True)
            fr.to_csv(d / "daily_nav.csv")
            public = {k: v for k, v in rec.items() if not k.startswith("_")}
            public["output_sha256"] = hashlib.sha256((d / "daily_nav.csv").read_bytes()).hexdigest()
            (d / "cell.json").write_text(json.dumps(public, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            frames[key] = fr
            (base_records if key.startswith("base") else chall_records).append(public)
    loader = lambda c: frames[c["cell_key"]]  # noqa: E731
    chall_rows, base_rows, turnover = paired_rows(chall_records, base_records, loader)
    from audit.csi1000_layered_search_core import final_verdict

    verdict, info = final_verdict(chall_rows, base_rows, turnover)
    (pb / "protocol.json").write_text(
        json.dumps(build_protocol("offline-demo-pass"), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (pb / "paired_verdict.json").write_text(
        json.dumps({"verdict": verdict, "info": info}, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    write_sha256sums(pb)
    out["bundles"]["pass_bundle"] = {"verdict": verdict, "sha": verify_sha256sums(pb), "turnover_ratio": turnover}
    # FAIL bundle: tampered bytes + unbooked spend + fake PASS summary.
    fb = root / "fail_bundle"
    fbcells = fb / "cells"
    fbcells.mkdir(parents=True, exist_ok=True)
    tamper_key = base_records[0]["cell_key"]
    (fbcells / tamper_key).mkdir(parents=True, exist_ok=True)
    frames[tamper_key].to_csv(fbcells / tamper_key / "daily_nav.csv")
    (fbcells / tamper_key / "cell.json").write_text(
        json.dumps(dict(base_records[0]), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (fb / "trial_ledger.jsonl").write_text(
        json.dumps(
            {
                "cell_key": tamper_key,
                "run_id": "demo-fail",
                "status": "PASS",
                "app_id": "",
                "spend_usd": float("nan"),
                "note": "in-flight row never booked",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    fake_pass = {
        "status": "PASS",
        "cell_key": "fake-no-nav",
        "source_sha256": "0" * 64,
        "output_sha256": "0" * 64,
        "quote_sha256": "0" * 64,
        "signal_sha256": "0" * 64,
        "cache_key": "k",
        "training_performed": False,
        "promotion_authorized": False,
        "ledger_ok": True,
        "coverage_ok": True,
        "data_ok": True,
        "n_days": 424,
        "total_cost_cny": 1.0,
    }
    (fb / "protocol.json").write_text(
        json.dumps(build_protocol("offline-demo-fail"), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    write_sha256sums(fb)
    # Tamper AFTER the checksum file is signed, exactly like a post-evidence edit.
    with (fbcells / tamper_key / "daily_nav.csv").open("a", encoding="utf-8") as f:
        f.write("tampered,0,0,0,0,0,0,0,0,0\n")
    failures = []
    for label, fn in (
        ("sha", lambda: verify_sha256sums(fb)),
        ("ledger", lambda: check_trial_ledger(fb / "trial_ledger.jsonl")),
        ("cell", lambda: check_cell_record(fake_pass)),
    ):
        try:
            fn()
            failures.append(f"{label}_NOT_BLOCKED")
        except ValueError:
            pass
    require(not failures, f"fail bundle gates did not all block: {failures}")
    out["bundles"]["fail_bundle"] = {"all_three_gates_blocked": True}
    (root / "demo_report.json").write_text(
        json.dumps(out, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    return out


def main(argv: List[str] | None = None) -> int:
    args = build_cli().parse_args(argv)
    if args.write_demo:
        report = write_demo(args.write_demo)
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
        return 0
    report: Dict[str, Any] = {"protocol": "csi1000_layered_search_v1"}
    if args.summary:
        report["stage"] = check_stage_summary(json.loads(Path(args.summary).read_text(encoding="utf-8")))
    if args.ledger:
        report["ledger"] = check_trial_ledger(args.ledger)
    if args.evidence_dir:
        report["sha"] = verify_sha256sums(args.evidence_dir)
    if args.finalists or args.out:
        finalists = json.loads(Path(args.finalists).read_text(encoding="utf-8")) if args.finalists else []
        decision = build_decision(
            finalists, args.spent_usd, args.fits_used, baseline_eligible=not args.baseline_ineligible
        )
        report["decision"] = decision
        if args.out:
            Path(args.out).write_text(json.dumps(decision, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    if not (args.summary or args.ledger or args.evidence_dir or args.finalists or args.out):
        build_cli().print_help()
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
