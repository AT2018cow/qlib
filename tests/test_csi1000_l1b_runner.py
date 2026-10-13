"""PR-S1 L1-B tests: roster math, reuse compatibility, dispatch gates, ledger scope.

No Modal, no private provider, no training. Export trees are synthetic but
fully self-consistent (real file bytes, real SHA walks).
"""

import hashlib
import json
from pathlib import Path

import pytest

from audit import csi1000_layered_search_core as core
from audit import csi1000_layered_search_modal as runner
from audit import csi1000_layered_search_reconcile as rec

QUOTE = "f" * 64
PROVIDER = "1" * 64


def _hex(tag):
    return hashlib.sha256(tag.encode()).hexdigest()


def _record(cell, signal_sha):
    nav = 101_000_000.0
    return {
        "status": "PASS",
        "cell_key": cell["cell_key"],
        "candidate_id": cell["candidate_id"],
        "phase": cell["phase"],
        "topk": cell["topk"],
        "dropn": cell["dropn"],
        "engine": core.PROTOCOL_VERSION,
        "execution": core.EXECUTION_SCHEMA,
        "quote_path": core.QUOTE_PATH,
        "quote_sha256": QUOTE,
        "signal_sha256": signal_sha,
        "source_sha256": _hex("src" + cell["cell_key"]),
        "frozen_report_sha256": _hex("rep" + cell["candidate_id"] + str(cell["phase"])),
        "frozen_decision_sha256": _hex("dec" + cell["candidate_id"] + str(cell["phase"])),
        "cache_key": core.replay_cache_key(signal_sha, cell["topk"], cell["dropn"], QUOTE),
        "output_sha256": _hex("out" + cell["cell_key"]),
        "legacy_reproduction": "PASS" if (cell["topk"], cell["dropn"]) == (20, 2) else "NOT_APPLICABLE",
        "corrected_reference": "PASS",
        "final_nav_cny": nav,
        "total_cost_cny": 5000.0,
        "total_cost_rate_sum": 0.001,
        "total_turnover_cny": 1e9,
        "mean_turnover": 0.02,
        "n_days": 424,
        "order_count": 100,
        "ledger_ok": True,
        "coverage_ok": True,
        "data_ok": True,
        "training_performed": False,
        "promotion_authorized": False,
        "provider_fingerprint": "p" * 64,
        "provider_snapshot_sha256": PROVIDER,
        "metrics": {"n_days": 424},
    }


def _frozen_manifest(cells):
    seen = {}
    for c in cells:
        seen[(c["candidate_id"], c["phase"])] = {
            "candidate_id": c["candidate_id"],
            "phase": c["phase"],
            "signal_sha256": _hex("sig" + c["candidate_id"] + str(c["phase"])),
            "report_sha256": _hex("rep" + c["candidate_id"] + str(c["phase"])),
            "decision_sha256": _hex("dec" + c["candidate_id"] + str(c["phase"])),
        }
    for cid in core.STAGE_B_11_MODELS:
        for phase in (0, 4, 6, 10, 15):
            seen.setdefault(
                (cid, phase),
                {
                    "candidate_id": cid,
                    "phase": phase,
                    "signal_sha256": _hex("x" + cid + str(phase)),
                    "report_sha256": _hex("y" + cid + str(phase)),
                    "decision_sha256": _hex("z" + cid + str(phase)),
                },
            )
    return {"sources": list(seen.values())}


def _export_tree(root, stage, cells, run_id, code_sha):
    """Minimal signed export: summary + manifest + per-cell bytes + checksums."""
    records = []
    for cell in cells:
        signal = _hex("sig" + cell["candidate_id"] + str(cell["phase"]))
        record = _record(cell, signal)
        d = root / "cells" / cell["cell_key"]
        d.mkdir(parents=True, exist_ok=True)
        artifacts = {}
        for name, body in (
            ("daily_nav.csv", "a,b\n1,2\n"),
            ("fixed_report.parquet", "parquet-bytes"),
            ("fixed_decisions.json", "[]"),
            ("metrics.json", "{}"),
        ):
            (d / name).write_bytes(body.encode())
            artifacts[name] = hashlib.sha256(body.encode()).hexdigest()
        record["artifact_sha256"] = artifacts
        record["output_sha256"] = artifacts["daily_nav.csv"]
        (d / "cell.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        records.append(record)
    (root / "source_manifest_final.json").write_text(
        json.dumps({"cells": [{"cell_key": r["cell_key"]} for r in records]}, indent=2) + "\n", encoding="utf-8"
    )
    (root / "frozen_source_11x5.json").write_text(
        json.dumps(_frozen_manifest(cells), indent=2) + "\n", encoding="utf-8"
    )
    summary = {
        "protocol": core.PROTOCOL_VERSION,
        "stage": stage,
        "run_id": run_id,
        "code_sha": code_sha,
        "expected": len(records),
        "completed": len(records),
        "blocked": 0,
        "training_performed": False,
        "promotion_authorized": False,
        "source_volume_read_only": True,
        "verified_cells": records,
    }
    name = "stage_p0_summary.json" if stage == "p0" else "stage_l1-a_summary.json"
    (root / "summaries" / stage / name).parent.mkdir(parents=True, exist_ok=True)
    (root / "summaries" / stage / name).write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    core.write_sha256sums(root)
    return root / "summaries" / stage / name


def _ctx(records, code_sha, prior_code_sha, ref=""):
    return {
        "quote_sha": QUOTE,
        "provider_snapshot_sha256": PROVIDER,
        "code_sha": code_sha,
        "prior_run_id": "l1a-old",
        "prior_code_sha": prior_code_sha,
        "verified_output_sha": {r["cell_key"]: r["output_sha256"] for r in records},
        "version_equivalence_ref": ref,
    }


def _l1a_records():
    cells = core.enumerate_cells("l1-a")
    return [dict(_record(c, _hex("sig" + c["candidate_id"] + str(c["phase"])))) for c in cells]


def test_l1b_roster_math():
    a = {c["cell_key"] for c in core.enumerate_cells("l1-a")}
    b = {c["cell_key"] for c in core.enumerate_cells("l1-b")}
    assert len(a) == 32 and len(b) == 144
    assert len(set(a) & set(b)) == 32 and len(set(b) - set(a)) == 112


def test_reuse_accept_identical_code():
    records = _l1a_records()
    manifest = core.manifest_source_index(_frozen_manifest(core.enumerate_cells("l1-a")))
    plan = core.plan_l1b_reuse(records, manifest, _ctx(records, "a" * 40, "a" * 40))
    assert len(plan["overlap_keys"]) == 32 and len(plan["new_keys"]) == 112 and len(plan["new_cells"]) == 112
    assert all(a["equivalence"] == "IDENTICAL_CODE" for a in plan["attestations"])
    assert len(plan["reuse_records"]) == 32


def test_reuse_refuses_without_equivalence_ref():
    records = _l1a_records()
    manifest = core.manifest_source_index(_frozen_manifest(core.enumerate_cells("l1-a")))
    with pytest.raises(ValueError, match="equivalence ref"):
        core.plan_l1b_reuse(records, manifest, _ctx(records, "b" * 40, "a" * 40))


def test_reuse_accepts_reviewed_equivalence():
    records = _l1a_records()
    manifest = core.manifest_source_index(_frozen_manifest(core.enumerate_cells("l1-a")))
    plan = core.plan_l1b_reuse(records, manifest, _ctx(records, "b" * 40, "a" * 40, ref="PR68-review-1"))
    assert all(a["equivalence"] == "REVIEWED_EQUIVALENT:PR68-review-1" for a in plan["attestations"])


def test_reuse_refuses_tampered_cache_and_source():
    records = _l1a_records()
    manifest = core.manifest_source_index(_frozen_manifest(core.enumerate_cells("l1-a")))
    bad = [dict(r) for r in records]
    bad[0]["cache_key"] = "0" * 64
    with pytest.raises(ValueError, match="cache key"):
        core.plan_l1b_reuse(bad, manifest, _ctx(records, "a" * 40, "a" * 40))
    bad2 = [dict(r) for r in records]
    bad2[0]["signal_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="drift"):
        core.plan_l1b_reuse(bad2, manifest, _ctx(records, "a" * 40, "a" * 40))
    bad3 = [dict(r) for r in records]
    bad3[0]["output_sha256"] = "0" * 64
    ctx = _ctx(records, "a" * 40, "a" * 40)
    with pytest.raises(ValueError, match="re-measured"):
        core.plan_l1b_reuse(bad3, manifest, ctx)


def test_reuse_refuses_quote_and_engine_drift():
    records = _l1a_records()
    manifest = core.manifest_source_index(_frozen_manifest(core.enumerate_cells("l1-a")))
    bad = [dict(r) for r in records]
    bad[0]["quote_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="quote"):
        core.plan_l1b_reuse(bad, manifest, _ctx(records, "a" * 40, "a" * 40))
    bad2 = [dict(r) for r in records]
    bad2[0]["engine"] = "other-engine"
    with pytest.raises(ValueError, match="engine"):
        core.plan_l1b_reuse(bad2, manifest, _ctx(records, "a" * 40, "a" * 40))


def test_reuse_missing_overlap_is_stop_not_recompute():
    records = _l1a_records()[:31]
    manifest = core.manifest_source_index(_frozen_manifest(core.enumerate_cells("l1-a")))
    with pytest.raises(ValueError, match="lacks overlap"):
        core.plan_l1b_reuse(records, manifest, _ctx(records, "a" * 40, "a" * 40))


def test_dispatch_l1b_guards(tmp_path):
    base = ["--stage", "l1-b", "--run-id", "run-01", "--estimated-total-usd", "1", "--ledger", str(tmp_path / "l.json")]
    with pytest.raises(ValueError, match="STOP_P0"):
        runner.validate_args(runner.build_cli().parse_args(base))
    p0root, l1aroot = tmp_path / "p0exp", tmp_path / "l1aexp"
    p0sum = _export_tree(p0root, "p0", core.enumerate_cells("p0"), "p0-old", "a" * 40)
    with pytest.raises(ValueError, match="STOP_L1"):
        runner.validate_args(runner.build_cli().parse_args(base + ["--p0-summary", str(p0sum)]))
    l1asum = _export_tree(l1aroot, "l1-a", core.enumerate_cells("l1-a"), "l1a-old", "a" * 40)
    cells = runner.validate_for_dispatch(
        "l1-b",
        "run-01",
        2,
        0,
        1.0,
        "",
        str(tmp_path / "l.json"),
        str(p0sum),
        str(l1asum),
        "ref-1",
        runner.result_prefix("l1a-old", "l1-a", "a" * 40),
        "a" * 40,
    )
    assert len(cells) == 112
    part = runner.validate_for_dispatch(
        "l1-b",
        "run-01",
        2,
        5,
        1.0,
        "",
        str(tmp_path / "l.json"),
        str(p0sum),
        str(l1asum),
        "ref-1",
        runner.result_prefix("l1a-old", "l1-a", "a" * 40),
        "a" * 40,
    )
    assert len(part) == 112  # full eligible roster; batch clipping occurs after resume inventory
    assert len(core.select_pending_batch(part, set(), 5)) == 5
    with pytest.raises(ValueError, match="batch-limit"):
        runner.validate_for_dispatch(
            "l1-b",
            "run-01",
            2,
            113,
            1.0,
            "",
            str(tmp_path / "l.json"),
            str(p0sum),
            str(l1asum),
            "ref-1",
            runner.result_prefix("l1a-old", "l1-a", "a" * 40),
            "a" * 40,
        )
    with pytest.raises(ValueError, match="reuse-prefix|reuse prefix"):
        runner.validate_for_dispatch(
            "l1-b",
            "run-01",
            2,
            0,
            1.0,
            "",
            str(tmp_path / "l.json"),
            str(p0sum),
            str(l1asum),
            "ref-1",
            "../evil",
            "a" * 40,
        )
    with pytest.raises(ValueError, match="equivalence"):
        runner.validate_for_dispatch(
            "l1-b",
            "run-01",
            2,
            0,
            1.0,
            "",
            str(tmp_path / "l.json"),
            str(p0sum),
            str(l1asum),
            "",
            runner.result_prefix("l1a-old", "l1-a", "a" * 40),
            "b" * 40,
        )
    with pytest.raises(ValueError, match="resume-from-manifest"):
        runner.validate_for_dispatch(
            "l1-b",
            "run-01",
            2,
            0,
            1.0,
            "some-manifest.json",
            str(tmp_path / "l.json"),
            str(p0sum),
            str(l1asum),
            "ref-1",
            runner.result_prefix("l1a-old", "l1-a", "a" * 40),
            "a" * 40,
        )


def test_dispatch_l1c_still_refused(tmp_path):
    with pytest.raises(ValueError, match="later reviewed PR"):
        runner.validate_args(
            runner.build_cli().parse_args(
                [
                    "--stage",
                    "l1-c",
                    "--run-id",
                    "run-01",
                    "--estimated-total-usd",
                    "1",
                    "--ledger",
                    str(tmp_path / "l.json"),
                ]
            )
        )

    with pytest.raises(ValueError, match="later reviewed PR"):
        runner.validate_args(
            runner.build_cli().parse_args(
                [
                    "--stage",
                    "l2",
                    "--run-id",
                    "run-01",
                    "--estimated-total-usd",
                    "1",
                    "--ledger",
                    str(tmp_path / "l.json"),
                ]
            )
        )


def test_l1_budget_remaining_and_pending(tmp_path):
    ledger = core.BudgetLedger()
    ledger.record_actual("l1", 0.06532158, app_id="app-l1a", run_id="l1a-old")
    assert ledger.remaining_layer("l1") == pytest.approx(5.934678, abs=1e-6)
    assert ledger.preflight("l1", 5.0) == "OK_CONTINUE"
    assert ledger.preflight("l1", 5.95) == "STOP_BUDGET"
    ledger.record_estimate("l1", 1.0, run_id="l1b-new")
    assert ledger.preflight("l1", 0.5) == "STOP_COST_UNKNOWN"  # actuals not booked yet


def test_attestation_check_in_reconcile():
    good = [
        {
            "cell_key": "k",
            "prior_run_id": "r",
            "prior_code_sha": "a" * 40,
            "output_sha256": "b" * 64,
            "source_sha256": "c" * 64,
            "quote_sha256": "d" * 64,
            "engine": core.PROTOCOL_VERSION,
            "equivalence": "IDENTICAL_CODE",
            "verified_utc": "t",
        }
    ]
    assert rec.check_reuse_attestations(good, {"k"})["attestations"] == 1
    with pytest.raises(ValueError):
        rec.check_reuse_attestations([{**good[0], "equivalence": ""}], {"k"})
    with pytest.raises(ValueError):
        rec.check_reuse_attestations(good, {"k", "other"})


def test_interrupted_batch_summary_shape():
    summary = {
        "protocol": core.PROTOCOL_VERSION,
        "stage": "l1-b",
        "run_id": "r",
        "code_sha": "a" * 40,
        "expected": 144,
        "completed": 100,
        "blocked": 44,
        "training_performed": False,
        "promotion_authorized": False,
        "source_volume_read_only": True,
        "verified_cells": [],
        "blocked_cells": [],
    }
    with pytest.raises(ValueError, match="verified cell list"):
        rec.check_stage_summary(summary)


def _frame_for_metrics(drift, seed):
    import math

    import pandas as pd

    from audit.independent_stage_b_metrics import REQUIRED, metrics

    n = 424
    account, cash, rows = 100_000_000.0, 100_000_000.0, {c: [] for c in REQUIRED}
    total_cost, total_turn = 0.0, 0.0
    for i in range(n):
        wave = math.sin(i / 9.0 + seed) * 0.0004
        daily = drift + wave
        account *= 1 + daily
        total_cost += account * 0.00005
        total_turn += account * 0.02
        rows["account"].append(account)
        rows["cash"].append(cash)
        rows["value"].append(account - cash)
        rows["return"].append(daily + 0.00005)
        rows["cost"].append(0.00005)
        rows["total_cost"].append(total_cost)
        rows["total_turnover"].append(total_turn)
        rows["turnover"].append(0.02)
        rows["bench"].append(0.0001)
    frame = pd.DataFrame(rows, index=pd.date_range("2025-01-02", periods=n, freq="B"))
    frame.index.name = "datetime"
    numbers, _, _, _ = metrics(list(frame.index.date), {k: frame[k].astype(float).to_list() for k in REQUIRED})
    return frame, {k: (float(v) if isinstance(v, float) else v) for k, v in numbers.items()}


def _persisted_cell(root, cell, metrics, signal_sha):
    record = _record(cell, signal_sha)
    record["metrics"] = metrics
    record["mean_turnover"] = float(metrics["mean_turnover"])
    d = root / cell["cell_key"]
    d.mkdir(parents=True, exist_ok=True)
    artifacts = {}
    for name, body in (("daily_nav.csv", "a,b\n1,2\n"), ("fixed_decisions.json", "[]"), ("metrics.json", "{}")):
        (d / name).write_bytes(body.encode())
        artifacts[name] = hashlib.sha256(body.encode()).hexdigest()
    frame, _ = _frame_for_metrics(0.0006, 1.0)
    frame.to_parquet(d / "fixed_report.parquet")
    artifacts["fixed_report.parquet"] = hashlib.sha256((d / "fixed_report.parquet").read_bytes()).hexdigest()
    # Recompute metrics from the persisted parquet so independent re-checks agree.
    import pandas as pd

    from audit.independent_stage_b_metrics import REQUIRED, metrics as _metrics

    reread = pd.read_parquet(d / "fixed_report.parquet")
    numbers, _, _, _ = _metrics(list(reread.index.date), {k: reread[k].astype(float).to_list() for k in REQUIRED})
    record["metrics"] = {k: (float(v) if isinstance(v, float) else v) for k, v in numbers.items()}
    record["mean_turnover"] = float(numbers["mean_turnover"])
    record["artifact_sha256"] = artifacts
    record["output_sha256"] = artifacts["daily_nav.csv"]
    record["final_nav_cny"] = float(reread["account"].iloc[-1])
    record["total_cost_cny"] = float(numbers["total_cost_sum"])
    (d / "cell.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record


def test_materialize_copies_verified_cells(tmp_path):
    old = tmp_path / "old"
    specs, records = [], []
    for cell in core.enumerate_cells("l1-a")[:4]:
        signal = _hex("sig" + cell["candidate_id"] + str(cell["phase"]))
        rec = _record(cell, signal)
        d = old / "cells" / cell["cell_key"]
        d.mkdir(parents=True, exist_ok=True)
        arts = {}
        for name, body in (
            ("daily_nav.csv", "nav"),
            ("fixed_report.parquet", "rp"),
            ("fixed_decisions.json", "[]"),
            ("metrics.json", "{}"),
        ):
            (d / name).write_bytes(body.encode())
            arts[name] = hashlib.sha256(body.encode()).hexdigest()
        rec["artifact_sha256"] = arts
        rec["output_sha256"] = arts["daily_nav.csv"]
        (d / "cell.json").write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        records.append(rec)
        specs.append(
            {
                "cell_key": rec["cell_key"],
                "source_sha256": rec["source_sha256"],
                "output_sha256": rec["output_sha256"],
                "cache_key": rec["cache_key"],
            }
        )
    out = core.materialize_reuse_cells(tmp_path, "new/prefix", "old", specs)
    assert [r["cell_key"] for r in out] == [s["cell_key"] for s in specs]
    for rec in records:
        assert (tmp_path / "new/prefix/cells" / rec["cell_key"] / "daily_nav.csv").read_bytes() == b"nav"


def test_materialize_aborts_before_any_copy_on_tamper(tmp_path):
    old = tmp_path / "old"
    specs = []
    for cell in core.enumerate_cells("l1-a")[:3]:
        signal = _hex("sig" + cell["candidate_id"] + str(cell["phase"]))
        rec = _record(cell, signal)
        d = old / "cells" / cell["cell_key"]
        d.mkdir(parents=True, exist_ok=True)
        arts = {}
        for name, body in (
            ("daily_nav.csv", "nav"),
            ("fixed_report.parquet", "rp"),
            ("fixed_decisions.json", "[]"),
            ("metrics.json", "{}"),
        ):
            (d / name).write_bytes(body.encode())
            arts[name] = hashlib.sha256(body.encode()).hexdigest()
        rec["artifact_sha256"] = arts
        rec["output_sha256"] = arts["daily_nav.csv"]
        (d / "cell.json").write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        specs.append(
            {
                "cell_key": rec["cell_key"],
                "source_sha256": rec["source_sha256"],
                "output_sha256": rec["output_sha256"],
                "cache_key": rec["cache_key"],
            }
        )
    victim = old / "cells" / specs[1]["cell_key"] / "fixed_report.parquet"
    victim.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="bytes drift"):
        core.materialize_reuse_cells(tmp_path, "new/prefix", "old", specs)
    assert not (tmp_path / "new/prefix/cells").exists()


def test_materialize_resume_and_conflict(tmp_path):
    old = tmp_path / "old"
    cell = core.enumerate_cells("l1-a")[0]
    signal = _hex("sig" + cell["candidate_id"] + str(cell["phase"]))
    rec = _record(cell, signal)
    d = old / "cells" / cell["cell_key"]
    d.mkdir(parents=True, exist_ok=True)
    arts = {}
    for name, body in (
        ("daily_nav.csv", "nav"),
        ("fixed_report.parquet", "rp"),
        ("fixed_decisions.json", "[]"),
        ("metrics.json", "{}"),
    ):
        (d / name).write_bytes(body.encode())
        arts[name] = hashlib.sha256(body.encode()).hexdigest()
    rec["artifact_sha256"] = arts
    rec["output_sha256"] = arts["daily_nav.csv"]
    (d / "cell.json").write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    spec = {
        "cell_key": rec["cell_key"],
        "source_sha256": rec["source_sha256"],
        "output_sha256": rec["output_sha256"],
        "cache_key": rec["cache_key"],
    }
    core.materialize_reuse_cells(tmp_path, "new/prefix", "old", [spec])
    # Identical rerun resumes without error.
    core.materialize_reuse_cells(tmp_path, "new/prefix", "old", [spec])
    # Diverged destination refuses.
    (tmp_path / "new/prefix/cells" / rec["cell_key"] / "daily_nav.csv").write_bytes(b"evil")
    with pytest.raises(ValueError, match="bytes drift|conflict"):
        core.materialize_reuse_cells(tmp_path, "new/prefix", "old", [spec])


def test_live_provider_match():
    assert core.check_live_provider_match("a" * 64, "b" * 64, "a" * 64, "b" * 64)["status"] == "PROVIDER_MATCH"
    with pytest.raises(ValueError, match="snapshot differs"):
        core.check_live_provider_match("c" * 64, "b" * 64, "a" * 64, "b" * 64)
    with pytest.raises(ValueError, match="quote union differs"):
        core.check_live_provider_match("a" * 64, "c" * 64, "a" * 64, "b" * 64)
    with pytest.raises(ValueError, match="missing"):
        core.check_live_provider_match("", "b" * 64, "a" * 64, "b" * 64)


def test_dispatch_order_verify_before_compute():
    text = Path("audit/csi1000_layered_search_modal.py").read_text(encoding="utf-8")
    body = text[text.index("def run_search") :]
    assert body.index("verify_reuse_remote.remote") < body.index(".map(")
    assert body.index("check_live_provider_match") < body.index(".map(")
    assert "planned = list(cells)" in body


def test_screen_l1b_end_to_end_144(tmp_path):
    from audit import csi1000_layered_search_selection as selection

    research = tmp_path / "cells"
    baseroot = tmp_path / "base"
    for cell in core.enumerate_cells("l1-b"):
        signal = _hex("sig" + cell["candidate_id"] + str(cell["phase"]))
        frame, numbers = _frame_for_metrics(0.0004 + (hash(cell["cell_key"]) % 5) * 0.0001, 2.0)
        rec = _record(cell, signal)
        rec["metrics"] = numbers
        rec["mean_turnover"] = float(numbers["mean_turnover"])
        rec["final_nav_cny"] = float(frame["account"].iloc[-1])
        rec["total_cost_cny"] = float(numbers["total_cost_sum"])
        d = research / cell["cell_key"]
        d.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(d / "fixed_report.parquet")
        arts = {"fixed_report.parquet": hashlib.sha256((d / "fixed_report.parquet").read_bytes()).hexdigest()}
        for name, body in (("daily_nav.csv", "a,b\n1,2\n"), ("fixed_decisions.json", "[]"), ("metrics.json", "{}")):
            (d / name).write_bytes(body.encode())
            arts[name] = hashlib.sha256(body.encode()).hexdigest()
        rec["artifact_sha256"] = arts
        rec["output_sha256"] = arts["daily_nav.csv"]
        (d / "cell.json").write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for cell in core.enumerate_cells("p0"):
        if cell["candidate_id"] != core.BASELINE_CANDIDATE:
            continue
        signal = _hex("sig" + cell["candidate_id"] + str(cell["phase"]))
        frame, numbers = _frame_for_metrics(0.0003, 3.0)
        rec = _record(cell, signal)
        rec["metrics"] = numbers
        rec["mean_turnover"] = float(numbers["mean_turnover"])
        rec["final_nav_cny"] = float(frame["account"].iloc[-1])
        rec["total_cost_cny"] = float(numbers["total_cost_sum"])
        d = baseroot / cell["cell_key"]
        d.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(d / "fixed_report.parquet")
        arts = {"fixed_report.parquet": hashlib.sha256((d / "fixed_report.parquet").read_bytes()).hexdigest()}
        for name, body in (("daily_nav.csv", "a,b\n1,2\n"), ("fixed_decisions.json", "[]"), ("metrics.json", "{}")):
            (d / name).write_bytes(body.encode())
            arts[name] = hashlib.sha256(body.encode()).hexdigest()
        rec["artifact_sha256"] = arts
        rec["output_sha256"] = arts["daily_nav.csv"]
        (d / "cell.json").write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    out = selection.screen_l1("l1-b", research, baseroot)
    assert out["status"] == "AUDITED_HISTORICALLY_CONSUMED"
    assert out["expected_cells"] == 144
    assert len(out["all_ranked_trials"]) == 71
    assert all("passed_coarse" in r for r in out["all_ranked_trials"])


def test_l1b_pass_summary_strictness():
    cells = core.enumerate_cells("l1-b")
    records = []
    for cell in cells:
        signal = _hex("sig" + cell["candidate_id"] + str(cell["phase"]))
        record = _record(cell, signal)
        records.append(record)
    overlap = {c["cell_key"] for c in core.enumerate_cells("l1-b")} & {
        c["cell_key"] for c in core.enumerate_cells("l1-a")
    }
    assert len(overlap) == 32
    by_key = {r["cell_key"]: r for r in records}
    attest = [
        {
            "cell_key": k,
            "prior_run_id": "r",
            "prior_code_sha": "a" * 40,
            "output_sha256": by_key[k]["output_sha256"],
            "source_sha256": by_key[k]["source_sha256"],
            "quote_sha256": QUOTE,
            "engine": core.PROTOCOL_VERSION,
            "equivalence": "IDENTICAL_CODE",
            "verified_utc": "t",
        }
        for k in sorted(overlap)
    ]

    def summary_with(verified, attestations):
        return {
            "protocol": core.PROTOCOL_VERSION,
            "stage": "l1-b",
            "run_id": "r",
            "code_sha": "a" * 40,
            "expected": 144,
            "completed": 144,
            "blocked": 0,
            "remaining": 0,
            "reused": 32,
            "computed_new": 112,
            "training_performed": False,
            "promotion_authorized": False,
            "source_volume_read_only": True,
            "status": "PASS",
            "verified_cells": verified,
            "blocked_cells": [],
            "reuse_attestations": attestations,
        }

    assert rec.check_stage_summary(summary_with(records, attest))["attestations"] == 32
    with pytest.raises(ValueError, match="32-cell overlap"):
        rec.check_stage_summary(summary_with(records, attest[:31]))
    new_only_key = sorted(
        {c["cell_key"] for c in core.enumerate_cells("l1-b")} - {c["cell_key"] for c in core.enumerate_cells("l1-a")}
    )[0]
    forged = attest[:31] + [dict(attest[31], cell_key=new_only_key)]
    with pytest.raises(ValueError, match="32-cell overlap"):
        rec.check_stage_summary(summary_with(records, forged))


def test_l1b_batching_advances_across_all_112_new_cells():
    """A small-batch replay must progress, not repeat its original first N."""
    eligible = sorted(core.plan_l1b_reuse(
        _l1a_records(),
        core.manifest_source_index(_frozen_manifest(core.enumerate_cells("l1-a"))),
        _ctx(_l1a_records(), "a" * 40, "a" * 40),
    )["new_cells"], key=lambda c: c["cell_key"])
    assert len(eligible) == 112
    reused = {c["cell_key"] for c in core.enumerate_cells("l1-a")}
    completed = set(reused)
    seen = set()
    sizes = []
    while True:
        batch = core.select_pending_batch(eligible, completed, 5)
        if not batch:
            break
        keys = {c["cell_key"] for c in batch}
        assert len(keys) == len(batch) and keys.isdisjoint(completed)
        assert keys.isdisjoint(reused)
        sizes.append(len(keys))
        completed.update(keys)
        seen.update(keys)
    assert len(seen) == 112 and sizes == [5] * 22 + [2]
    assert core.select_pending_batch(eligible, completed, 5) == []


def test_l1b_prefix_bound_to_signed_l1a_run(tmp_path):
    p0 = _export_tree(tmp_path / "p0exp", "p0", core.enumerate_cells("p0"), "p0-old", "a" * 40)
    l1a = _export_tree(
        tmp_path / "l1aexp", "l1-a", core.enumerate_cells("l1-a"), "l1a-old", "a" * 40
    )
    args = ("l1-b", "new-run", 2, 5, 1.0, "", str(tmp_path / "ledger.json"),
            str(p0), str(l1a), "reviewed-PR72")
    exact = runner.result_prefix("l1a-old", "l1-a", "a" * 40)
    assert len(runner.validate_for_dispatch(*args, exact, "b" * 40)) == 112
    with pytest.raises(ValueError, match="audited L1-A"):
        runner.validate_for_dispatch(*args, exact.replace("l1a-old", "other-old"), "b" * 40)


def test_materialize_detects_self_consistent_non_nav_drift(tmp_path):
    cell = core.enumerate_cells("l1-a")[0]
    root = tmp_path / "old"
    _export_tree(root, "l1-a", [cell], "l1a-old", "a" * 40)
    record = json.loads((root / "cells" / cell["cell_key"] / "cell.json").read_text())
    spec = {k: record[k] for k in ("cell_key", "source_sha256", "output_sha256", "cache_key")}
    core.materialize_reuse_cells(tmp_path, "new", "old", [spec])
    dest = tmp_path / "new" / "cells" / cell["cell_key"]
    (dest / "fixed_report.parquet").write_bytes(b"different-but-internally-consistent")
    tampered = json.loads((dest / "cell.json").read_text())
    tampered["artifact_sha256"]["fixed_report.parquet"] = hashlib.sha256(
        (dest / "fixed_report.parquet").read_bytes()
    ).hexdigest()
    (dest / "cell.json").write_text(json.dumps(tampered, sort_keys=True))
    # The destination's own hashes are internally consistent, but source != dest.
    with pytest.raises(ValueError, match="reuse destination bytes drift"):
        core.materialize_reuse_cells(tmp_path, "new", "old", [spec])


def test_full_frozen_source_digest_rejects_one_changed_byte():
    source = _frozen_manifest(core.enumerate_cells("l1-a"))["sources"]
    left = core.frozen_source_digest(source)
    changed = [dict(x) for x in source]
    changed[0]["report_sha256"] = _hex("different frozen report")
    assert core.frozen_source_digest(changed) != left
    with pytest.raises(ValueError, match="55"):
        core.frozen_source_digest(changed[:-1])


def test_real_finalizer_call_binds_attestations_by_name():
    import ast

    tree = ast.parse(Path("audit/csi1000_layered_search_modal.py").read_text(encoding="utf-8"))
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "remote"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "finalize_remote"
    ]
    assert len(calls) == 1
    assert len(calls[0].args) == 5
    assert any(k.arg == "attestations" and isinstance(k.value, ast.Name)
               and k.value.id == "attestations" for k in calls[0].keywords)


def test_l1b_incomplete_batch_and_attestation_linkage_are_audited():
    roster = core.enumerate_cells("l1-b")
    overlap = {c["cell_key"] for c in core.enumerate_cells("l1-a")}
    selected = [c for c in roster if c["cell_key"] in overlap]
    selected += [c for c in roster if c["cell_key"] not in overlap][:2]
    records = [_record(c, _hex("sig" + c["candidate_id"] + str(c["phase"]))) for c in selected]
    by_key = {r["cell_key"]: r for r in records}
    attest = [
        {"cell_key": k, "prior_run_id": "l1a-old", "prior_code_sha": "a" * 40,
         "source_sha256": by_key[k]["source_sha256"],
         "output_sha256": by_key[k]["output_sha256"],
         "quote_sha256": by_key[k]["quote_sha256"],
         "engine": by_key[k]["engine"], "equivalence": "REVIEWED_EQUIVALENT:PR72",
         "verified_utc": "2026-10-13T00:00:00Z"}
        for k in sorted(overlap)
    ]
    incomplete = {
        "stage": "l1-b", "status": "INCOMPLETE_BATCH",
        "expected": 144, "completed": 34, "blocked": 0, "remaining": 110,
        "verified_cells": records, "blocked_cells": [], "reuse_attestations": attest,
        "reused": 32, "computed_new": 2,
        "training_performed": False, "promotion_authorized": False,
        "source_volume_read_only": True,
    }
    assert rec.check_stage_summary(incomplete)["status"] == "INCOMPLETE_BATCH"
    with pytest.raises(ValueError, match="counters"):
        rec.check_stage_summary({**incomplete, "remaining": 109})
    bad_attest = [dict(a) for a in attest]
    bad_attest[0]["output_sha256"] = _hex("different NAV")
    with pytest.raises(ValueError, match="differs from cell"):
        rec.check_stage_summary({**incomplete, "reuse_attestations": bad_attest})
    with pytest.raises(ValueError, match="duplicate verified"):
        rec.check_stage_summary({**incomplete, "verified_cells": records[:-1] + [records[0]]})


def test_finalize_only_path_does_not_replay():
    """All-complete recovery must still publish without recomputing a cell."""
    text = Path("audit/csi1000_layered_search_modal.py").read_text(encoding="utf-8")
    entrypoint = text.split("def run_search(", 1)[1].split("\ndef _write_inflight_ledger(", 1)[0]
    assert 'stage == "l1-b" and not cells and len(completed) == len(enumerate_cells("l1-b"))' in entrypoint
    assert "[]\n            if finalize_only\n            else list(" in entrypoint
