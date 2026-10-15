"""Zero-fit Stage-A CSI1000 frozen-signal diagnostic replay (PR-A).

Source files/provider are immutable inputs. Only Exchange quote coverage changes;
PIT signal candidates, fees, holdings policy, dates and starting capital do not.
Historical diagnosis ONLY: no production approval or new out-of-sample claim.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

SNAPSHOT = "a2ecd8d1cadf404670762a81b1fd33a377f2ef2aaf27a0d9ac6787366defd65b"
BASELINE = "23b92de05cf36c82998de684d0fbf64d81ee54490d755bd3cf96311c00286785"
WINNER = "4e908173705c76fee3782be37068672a3a845bd61894202f3152afe3b9d81ef2"
SUMMARY = "quant/data/round1_stage_ab/csi1000_tuner/stage_a_expanded_a2ecd8d1cadf4046.json"
SUMMARY_BLOB_SHA1 = "b3ec82244db87465adc6b733227c1c9752ed213d"
VERSION = "stage_a_corrected_quote_v1"
CAPITAL = 100_000_000


def require(ok, why):
    if not ok:
        raise ValueError("STAGE_A_BLOCKED: " + why)


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_json(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, ensure_ascii=False, allow_nan=False, sort_keys=True) + "\n", encoding="utf-8")


def load_summary(path):
    raw = Path(path).read_bytes()
    git_blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    require(git_blob == SUMMARY_BLOB_SHA1, "GitHub frozen Stage-A summary blob changed")
    return json.loads(raw)


def safe_output(source_root, provider, output):
    source_root, provider, output = [Path(x).resolve() for x in (source_root, provider, output)]
    for protected in (source_root, provider):
        require(output != protected and not output.is_relative_to(protected), "output inside immutable input")
    require(output != Path("/vol") and not output.is_relative_to(Path("/vol")), "write into original /vol")
    return output


def roster(summary, scope):
    require(scope in ("controls", "sample", "all"), "unsupported scope")
    require(summary.get("manifest", {}).get("snapshot_token") == SNAPSHOT, "Stage-A token drift")
    require(summary.get("protocol") == "csi1000_lgb_stage_a_v3", "protocol drift")
    candidates = summary.get("ranked_candidates", [])
    require(len(candidates) == 80 and len({c["candidate_id"] for c in candidates}) == 80, "80 candidate roster missing")
    folds = summary.get("folds", [])
    require([f["fold_id"] for f in folds] == ["fold1", "fold2", "fold3", "fold4"], "fold geometry changed")
    chosen = (candidates if scope == "all" else
              [c for c in candidates if c["candidate_id"] in (BASELINE, WINNER) or
               (scope == "sample" and c["rank"] in (1, 80))])
    require(len(chosen) == {"controls": 2, "sample": 4, "all": 80}[scope], "frozen scope candidate set unavailable")
    return [(c, f) for c in chosen for f in folds]


def check_provider(snapshot_file, manifest):
    require(Path(snapshot_file).is_file(), "original provider_snapshot.json unavailable")
    original = read_json(snapshot_file)
    require(original.get("snapshot_token") == SNAPSHOT, "provider snapshot token mismatch")
    require(original.get("provider_fingerprint") == manifest["provider_fingerprint"], "provider fingerprint mismatch")
    require(original == manifest, "provider manifest fields disagree with frozen GitHub summary")
    return sha256(snapshot_file)


def checked_sources(root, candidate, fold):
    """Do not trust a path from result.json: derive its canonical original directory."""
    source = Path(root).resolve()
    cid, fid = candidate["candidate_id"], fold["fold_id"]
    directory = (source / "csi1000_tuner" / "stage_a" / SNAPSHOT / cid / fid).resolve()
    require(directory.is_relative_to(source), "source path escaped original mount")
    result_path = directory / "result.json"
    require(result_path.is_file(), f"missing result.json {cid}/{fid}")
    payload = read_json(result_path)
    require(payload.get("snapshot_token") == SNAPSHOT and
            payload.get("protocol") == "csi1000_lgb_stage_a_v3" and
            payload.get("candidate_id") == cid and payload.get("candidate") == candidate["candidate"] and
            payload.get("fold_id") == fid and payload.get("fold") == fold,
            f"result identity/fold/provider mismatch {cid}/{fid}")
    require(payload.get("artifact_namespace") is None, "repeat_a or alternate namespace is NOT canonical")
    paths = {"result": result_path}
    hashes = {"result": sha256(result_path)}
    for kind, name in (("signal", "signal.parquet"), ("report", "report.parquet"), ("decision", "decisions.json")):
        meta = payload.get(f"{kind}_artifact")
        require(isinstance(meta, dict) and isinstance(meta.get("sha256"), str), f"missing {kind} manifest")
        path = directory / name
        require(path.is_file() and not path.is_symlink(), f"missing or symlink {name} {cid}/{fid}")
        # The historical absolute pointer must also denote THIS candidate/fold, not repeat_a.
        expected_suffix = f"/{SNAPSHOT}/{cid}/{fid}/{name}"
        require(str(meta.get("path", "")).endswith(expected_suffix), f"bad original path {kind}")
        actual = sha256(path)
        require(actual == meta["sha256"], f"source SHA mismatch {kind} {cid}/{fid}")
        paths[kind], hashes[kind] = path, actual
    return payload, paths, hashes


def validate_signal(frame, fold, meta):
    import pandas as pd
    require(list(frame.columns) == ["score"] and len(frame) == int(meta["rows"]), "signal schema/rows drift")
    require(isinstance(frame.index, pd.MultiIndex) and frame.index.nlevels == 2, "signal index not (datetime,instrument)")
    require(not frame.index.has_duplicates, "duplicate signal rows")
    require(list(frame.index.names) == ["datetime", "instrument"], "signal index names changed")
    dates = pd.DatetimeIndex(frame.index.get_level_values("datetime"))
    require(dates.min().strftime("%Y-%m-%d") == fold["signal"][0] and
            dates.max().strftime("%Y-%m-%d") == fold["signal"][1], "signal clock drift")
    require(frame["score"].notna().all() and frame["score"].map(math.isfinite).all(), "invalid signal scores")


def _decisions(decisions):
    rows = []
    for i, d in enumerate(decisions):
        orders = [{"order_index": j, "stock_id": str(o.stock_id), "direction": int(o.direction),
                   "amount": float(o.amount), "deal_amount": float(o.deal_amount),
                   "factor": None if o.factor is None else float(o.factor),
                   "start_time": str(o.start_time), "end_time": str(o.end_time)}
                  for j, o in enumerate(d.get_decision())]
        rows.append({"decision_index": i, "start_time": str(d.start_time),
                     "end_time": str(d.end_time), "orders": orders})
    return rows


def backtest(signal, fold, codes):
    from qlib.backtest import collect_data
    from board_execution import research_exchange
    result = {}
    decisions = list(collect_data(
        strategy={"class": "DeterministicTopkDropoutStrategy", "module_path": "deterministic_strategy",
                  "kwargs": {"signal": signal, "topk": 20, "n_drop": 2, "forbid_all_trade_at_limit": False}},
        executor={"class": "SimulatorExecutor", "module_path": "qlib.backtest.executor",
                  "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True, "track_data": True}},
        start_time=fold["execution"][0], end_time=fold["execution"][1],
        account=CAPITAL, benchmark="SH000852",
        exchange_kwargs=research_exchange(*fold["execution"], codes=codes), return_value=result))
    return result["portfolio_dict"]["1day"][0], _decisions(decisions)


def compare_report(expected, actual):
    import numpy as np
    require(expected.index.equals(actual.index) and list(expected.columns) == list(actual.columns), "legacy report calendar/schema drift")
    for col in expected.columns:
        a = expected[col].to_numpy(dtype=float)
        b = actual[col].to_numpy(dtype=float)
        require(np.array_equal(np.isnan(a), np.isnan(b)), "legacy report NaN drift: " + col)
        tol = 0.01 if col in ("account", "cash", "value", "total_cost", "total_turnover") else 1e-10
        require(np.allclose(a, b, rtol=0, atol=tol, equal_nan=True), "legacy report numerical drift: " + col)


def compare_decisions(old, new):
    require(len(old) == len(new), "legacy decision days drift")
    for a, b in zip(old, new):
        require((a["start_time"], a["end_time"]) == (b["start_time"], b["end_time"]), "legacy decision time drift")
        require(len(a["orders"]) == len(b["orders"]), "legacy order count drift")
        for x, y in zip(a["orders"], b["orders"]):
            for k in ("stock_id", "direction", "start_time", "end_time"):
                require(x[k] == y[k], "legacy order identity drift: " + k)
            for k in ("amount", "deal_amount", "factor"):
                require((x[k] is None and y[k] is None) or
                        (x[k] is not None and y[k] is not None and math.isclose(float(x[k]), float(y[k]), rel_tol=1e-10, abs_tol=1e-7)),
                        "legacy quantity/factor drift: " + k)


def metric_summary(report, fold):
    from portfolio_performance import portfolio_performance
    require(len(report) == 20 and report.index.is_unique, "not 20 execution sessions")
    for col in ("account", "cash", "value", "return", "cost", "bench"):
        require(col in report.columns and report[col].notna().all(), "missing report column/bars: " + col)
    import numpy as np
    require(np.isfinite(report[["account", "cash", "value", "return", "cost", "bench"]].to_numpy(dtype=float)).all(), "nonfinite report")
    require(float(np.max(np.abs(report["account"] - report["cash"] - report["value"]))) <= 0.05,
            "account != cash + holdings value")
    perf = portfolio_performance(report, initial_cash=CAPITAL, backtest_start=fold["execution"][0])
    require(perf["n_days"] == 20 and perf["account_return_max_error"] is not None and
            abs(perf["account_return_max_error"]) <= 1e-10, "NAV / return-cost inconsistent")
    key = "turnover" if "turnover" in report.columns else "total_turnover"
    require(key in report.columns, "turnover missing")
    perf.update(mean_turnover=round(float(report[key].mean()), 6), total_cost_sum=round(float(report["cost"].sum()), 6))
    require(perf["mean_turnover"] >= 0 and perf["total_cost_sum"] >= 0, "negative turnover/cost")
    return perf


def check_original_metrics(payload, old_metrics):
    for key, actual in old_metrics.items():
        if key in payload:
            original = payload[key]
            if isinstance(actual, (int, float)) and original is not None:
                require(math.isclose(float(original), float(actual), rel_tol=0, abs_tol=1e-6), "frozen result metric drift: " + key)
            else:
                require(original == actual, "frozen result metric drift: " + key)


def run_cell(root, provider_uri, snapshot_file, summary, candidate, fold, output):
    """Run one completely isolated cell; caller catches blocked cells explicitly."""
    import numpy as np
    import pandas as pd
    safe_output(root, provider_uri, output)
    require(not Path(output).exists(), "result collision; never overwrite")
    snapshot_sha = check_provider(snapshot_file, summary["manifest"])
    payload, paths, hashes = checked_sources(root, candidate, fold)
    old = pd.read_parquet(paths["report"])
    sig = pd.read_parquet(paths["signal"])
    old_orders = read_json(paths["decision"])
    validate_signal(sig, fold, payload["signal_artifact"])
    require(len(old_orders) == 20, "source decision coverage !=20")
    from qlib.data import D
    cal = pd.DatetimeIndex(D.calendar(start_time=fold["execution"][0], end_time=fold["execution"][1], freq="day"))
    require(len(cal) == 20 and old.index.equals(cal), "original provider calendar disagrees with frozen 20-bar report")
    require(str(old.index[0].date()) == fold["execution"][0] and
            str(old.index[-1].date()) == fold["execution"][1], "source report dates drift")
    require(len(old_orders) == payload["decision_artifact"]["decision_count"] and
            sum(len(row["orders"]) for row in old_orders) == payload["decision_artifact"]["order_count"], "original order metadata drift")
    original_metrics = metric_summary(old, fold)
    check_original_metrics(payload, original_metrics)
    from execution_quote_universe import execution_quote_codes
    from qlib.data import D
    static = execution_quote_codes("csi1000", *fold["execution"])
    require(bool(static), "quote union empty")
    # Price union must not alter signal membership: only exchange_kwargs.codes differs.
    legacy, legacy_orders = backtest(sig["score"], fold, "csi1000")
    compare_report(old, legacy)
    compare_decisions(old_orders, legacy_orders)
    check_original_metrics(payload, metric_summary(legacy, fold))
    fixed, fixed_orders = backtest(sig["score"], fold, static)
    require(old.index.equals(fixed.index), "fixed calendar drift")
    fixed_m = metric_summary(fixed, fold)
    order_delta = []
    for date, a, b in zip(old.index, legacy_orders, fixed_orders):
        if a["orders"] != b["orders"]:
            order_delta.append({"date": str(date.date()), "legacy": a["orders"], "fixed": b["orders"]})
    change = fixed["account"].to_numpy(dtype=float) - old["account"].to_numpy(dtype=float)
    changed_nav = [str(old.index[i].date()) for i, n in enumerate(change) if abs(n) > 0.01]
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    fixed.to_parquet(output / "fixed_report.parquet")
    save_json(output / "fixed_decisions.json", fixed_orders)
    pd.DataFrame({"date": [str(t.date()) for t in old.index], "legacy_nav_cny": old["account"].to_numpy(),
                  "fixed_nav_cny": fixed["account"].to_numpy(), "delta_cny": change}).to_csv(output / "daily_nav_delta.csv", index=False)
    save_json(output / "order_delta.json", order_delta)
    save_json(output / "legacy_reproduction.json", {"status": "PASS", "original_metrics": original_metrics,
             "source_sha256": hashes, "provider_manifest_sha256": snapshot_sha})
    save_json(output / "fixed_metrics.json", fixed_m)
    artifacts = {p.name: sha256(p) for p in sorted(output.iterdir()) if p.is_file()}
    record = {"status": "PASS", "run_version": VERSION, "candidate_id": candidate["candidate_id"],
              "fold_id": fold["fold_id"], "source_sha256": hashes,
              "source_stat": {k: {"bytes": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns} for k, p in paths.items()},
              "provider_manifest_sha256": snapshot_sha,
              "quote_codes_count": len(static), "quote_codes_sha256": hashlib.sha256(("\n".join(static) + "\n").encode()).hexdigest(),
              "original_metrics": original_metrics, "fixed_metrics": fixed_m,
              "changed_order_days": len(order_delta), "changed_nav_days": len(changed_nav),
              "first_changed_nav_day": changed_nav[0] if changed_nav else None,
              "fixed_orders": sum(len(x["orders"]) for x in fixed_orders), "original_orders": sum(len(x["orders"]) for x in old_orders),
              "output_sha256": artifacts, "training_performed": False,
              "promotion_authorized": False, "historical_tail_consumed": True,
              "external_execution_certified": False}
    save_json(output / "comparison.json", record)
    return record


def checked_resume(root, snapshot_file, summary, candidate, fold, output):
    path = Path(output)
    record = read_json(path / "comparison.json")
    require(record.get("status") == "PASS" and record.get("run_version") == VERSION and
            (record.get("candidate_id"), record.get("fold_id")) == (candidate["candidate_id"], fold["fold_id"]), "resume identity/version mismatch")
    check_provider(snapshot_file, summary["manifest"])
    _, paths, sources = checked_sources(root, candidate, fold)
    require(record["source_sha256"] == sources and record["provider_manifest_sha256"] == sha256(snapshot_file), "resume source hashes drift")
    require(record.get("source_stat") == {k: {"bytes": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns} for k, p in paths.items()}, "resume source size/mtime drift")
    for name, digest in record["output_sha256"].items():
        require((path / name).is_file() and sha256(path / name) == digest, "resume output SHA drift: " + name)
    require(set(record["output_sha256"]) == {"fixed_report.parquet", "fixed_decisions.json", "daily_nav_delta.csv", "order_delta.json", "legacy_reproduction.json", "fixed_metrics.json"}, "resume files drift")
    return record


def aggregate(summary, records, scope):
    """Never produce a partial ranking disguised as 80/80."""
    expected = {(c["candidate_id"], f["fold_id"]) for c, f in roster(summary, scope)}
    seen = set()
    good = {}
    for row in records:
        key = (row["candidate_id"], row["fold_id"])
        require(key in expected and key not in seen, "duplicate/unregistered result")
        seen.add(key)
        if row["status"] == "PASS":
            good[key] = row
    chosen_ids = {cid for cid, _ in expected}
    missing = expected - seen
    require(not missing, "missing cell status records: " + repr(sorted(missing)[:8]))
    batch = {"audit": "HISTORICAL_DIAGNOSTIC_ONLY", "snapshot_token": SNAPSHOT, "scope": scope,
             "expected": len(expected), "completed": len(good), "blocked": len(expected) - len(good),
             "training_performed": False, "promotion_authorized": False, "historical_tail_consumed": True,
             "cells": sorted(records, key=lambda r: (r["candidate_id"], r["fold_id"]))}
    ranking = {"status": "BLOCKED_INCOMPLETE_CELLS", "promotion_authorized": False, "rows": []}
    if len(good) == len(expected):
        from csi1000_tuner_core import rank_candidates, summarize_candidate, SCREEN_RANK_SPECS, _result_summary
        # Reconstruct legacy summary as negative control before permitting new ranking.
        ranked = []
        for orig in summary["ranked_candidates"]:
            cid = orig["candidate_id"]
            if cid not in chosen_ids:
                continue
            rows = [{"fold_id": fid, **good[(cid, fid)]["fixed_metrics"]} for fid in ("fold1", "fold2", "fold3", "fold4")]
            source_rows = [{"fold_id": fid, **good[(cid, fid)]["original_metrics"]} for fid in ("fold1", "fold2", "fold3", "fold4")]
            original_recomputed = summarize_candidate(orig["candidate"], source_rows)["fold_summary"]
            for field in original_recomputed:
                if field in ("n", "ids"):
                    require(original_recomputed[field] == orig["fold_summary"][field], "original fold summary drift")
                else:
                    require(math.isclose(original_recomputed[field], orig["fold_summary"][field], rel_tol=0, abs_tol=1e-7), "original Stage-A ranking input drift: " + field)
            ranked.append(summarize_candidate(orig["candidate"], rows))
        corrected = rank_candidates(ranked, stage="screen")
        old_rank = {r["candidate_id"]: r["rank"] for r in summary["ranked_candidates"]}
        lofo = {}
        if scope == "all":
            for omitted in ("fold1", "fold2", "fold3", "fold4"):
                scores = []
                for orig in summary["ranked_candidates"]:
                    cid = orig["candidate_id"]
                    keep = [{"fold_id": fid, **good[(cid, fid)]["fixed_metrics"]} for fid in ("fold1", "fold2", "fold3", "fold4") if fid != omitted]
                    part = _result_summary(keep, id_field="fold_id")
                    key = tuple(float(part[field]) * direction for field, direction in SCREEN_RANK_SPECS)
                    scores.append((cid, key))
                # Frozen lexicographic priority, with the same content-hash tie break.
                scores.sort(key=lambda x: x[0])
                scores.sort(key=lambda x: x[1], reverse=True)
                lofo[omitted] = {cid: i + 1 for i, (cid, _) in enumerate(scores)}
        ranking = {"status": "DIAGNOSTIC_80_RERANK" if scope == "all" else "PILOT_ONLY_NO_FULL_RANK",
                   "lofo_omitted_fold_ranks": lofo,
                   "promotion_authorized": False, "rows": [{"candidate_id": c["candidate_id"], "original_rank": old_rank[c["candidate_id"]],
                   "corrected_rank": c["rank"] if scope == "all" else None, "fixed_four_fold": c["fold_summary"],
                   "params": c["candidate"]["model_params"]} for c in corrected]}
    return batch, ranking


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[3])
    p.add_argument("--source-root", type=Path, required=True, help="original qlib-cn-data mount root")
    p.add_argument("--provider-uri", type=Path, required=True, help="original Qlib cn_data")
    p.add_argument("--provider-snapshot-file", type=Path, required=True)
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--scope", choices=("controls", "sample", "all"), default="controls")
    a = p.parse_args()
    summary = load_summary(a.repo_root / SUMMARY)
    safe_output(a.source_root, a.provider_uri, a.out_dir)
    require(not a.out_dir.exists(), "output already exists")
    check_provider(a.provider_snapshot_file, summary["manifest"])
    import qlib
    qlib.init(provider_uri=str(a.provider_uri), region="cn")
    from qlib.data import D
    jobs = roster(summary, a.scope)
    outcomes = []
    for c, f in jobs:
        cid, fid = c["candidate_id"], f["fold_id"]
        try:
            result = run_cell(a.source_root, a.provider_uri, a.provider_snapshot_file, summary, c, f, a.out_dir / cid / fid)
        except Exception as exc:
            result = {"status": "BLOCKED", "candidate_id": cid, "fold_id": fid,
                      "reason": str(exc), "training_performed": False}
        outcomes.append(result)
        print(f"{len(outcomes)}/{len(jobs)} {cid[:12]} {fid}: {result['status']}", flush=True)
    batch, ranking = aggregate(summary, outcomes, a.scope)
    a.out_dir.mkdir(parents=True, exist_ok=True)
    save_json(a.out_dir / "stage_a_320_corrected_batch.json", batch)
    save_json(a.out_dir / "stage_a_80_corrected_ranking.json", ranking)
    save_json(a.out_dir / "source_manifest.json", {"stage_a_snapshot": SNAPSHOT, "provider_manifest_sha256": sha256(a.provider_snapshot_file),
             "manifest": summary["manifest"], "cells": [{"candidate_id": x["candidate_id"], "fold_id": x["fold_id"],
             "source_sha256": x.get("source_sha256"), "status": x["status"]} for x in outcomes]})
    print(json.dumps({"completed": batch["completed"], "blocked": batch["blocked"], "ranking": ranking["status"]}))
    if batch["blocked"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
