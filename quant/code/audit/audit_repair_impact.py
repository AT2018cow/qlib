"""Read-only local impact inventory; never rewrite historical strategy artifacts."""

from __future__ import annotations

import argparse
import hashlib
import math
from pathlib import Path
import sys

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE))
from quant_bootstrap import ensure_quant_import_paths

ensure_quant_import_paths()

import artifact_io as json
import numpy as np
import pandas as pd
from account_boundary import account_identity
from forward_performance import build_forward_performance
from merge_forward_performance import attach_backfill_boundary, merge_forward_performance
from paper_accounting import verify_ledger
from order_quantity import quantity_rule
from portfolio_performance import portfolio_performance
from stage0_p0_core import apply_cost_stress


def audit_website_series(signals, market):
    """Validate the independent reconstruction and every current derived copy."""
    from website_simulation import validate_website_series

    signals = Path(signals)
    path = signals / f"{market}_website_performance.json"
    if not path.is_file():
        return None
    series = validate_website_series(json.loads(path.read_text()), market=market)
    hashes = {}
    for kind in ("website", "forward", "backfill"):
        derived = signals / f"{market}_{kind}_performance.json"
        if json.loads(derived.read_text()) != series:
            raise ValueError(f"website derived output mismatch: {derived.name}")
        hashes[derived.name] = hashlib.sha256(derived.read_bytes()).hexdigest()
    return series, hashes


def report_initial_capital(path, report, root, archive_contracts):
    """Read declared capital; never infer it from the first day's closing NAV."""
    declarations = []
    path, root = Path(path), Path(root)
    relative = str(path.relative_to(root))
    if "initial_cash" in report.attrs:
        declarations.append(("parquet:initial_cash", report.attrs["initial_cash"]))
    for name in ("account_contract.json", "metrics.json"):
        sidecar = path.parent / name
        if not sidecar.is_file():
            continue
        contract = json.loads(sidecar.read_text())
        if not isinstance(contract, dict):
            raise ValueError(f"invalid account metadata: {name}")
        if "report_sha256" in contract and contract["report_sha256"] != hashlib.sha256(path.read_bytes()).hexdigest():
            raise ValueError("account contract report fingerprint mismatch")
        fields = ("initial_cash", "account") if name == "metrics.json" else ("initial_cash",)
        for field in fields:
            if field in contract:
                declarations.append((f"{name}:{field}", contract[field]))
    if relative in archive_contracts:
        contract = archive_contracts[relative]
        if contract["report_sha256"] != hashlib.sha256(path.read_bytes()).hexdigest():
            raise ValueError("archived account contract report fingerprint mismatch")
        declarations.append(("audit_account_contracts.json", contract["initial_cash"]))
    if not declarations:
        raise ValueError("missing declared starting capital")
    for source, value in declarations:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"invalid declared starting capital: {source}")
    initial_cash = float(declarations[0][1])
    if any(not math.isclose(float(value), initial_cash, rel_tol=0, abs_tol=1e-8) for _, value in declarations):
        raise ValueError("conflicting starting capital declarations")
    return initial_cash, [source for source, _ in declarations]


def audit(root):
    root = Path(root)
    signals = root / "quant/data/signals"
    inventory = {
        "paper_reports": 0,
        "paper_errors": [],
        "quantity_errors": [],
        "historical_reports": 0,
        "historical_metric_errors": [],
        "historical_contract_errors": [],
        "historical_account_contracts": {},
        "stress_checked": 0,
        "stress_errors": [],
        "stress_sharpe_changed": 0,
        "max_absolute_stress_sharpe_delta": 0.0,
        "merged_live": {},
        "corrected_stress_metrics": {},
        "pending_external_evidence": [
            "historical_ST",
            "official_limit_prices",
            "opening_auction_liquidity",
            "full_historical_order_replay",
            "new_forward_observations",
        ],
    }
    inputs = {}
    contract_path = root / "quant/data/audit_account_contracts.json"
    archive_contracts = {}
    if contract_path.is_file():
        contract_doc = json.loads(contract_path.read_text())
        if contract_doc.get("schema_version") != 1:
            raise ValueError("unsupported archived account contract schema")
        archive_contracts = contract_doc["reports"]
        inputs[str(contract_path.relative_to(root))] = hashlib.sha256(contract_path.read_bytes()).hexdigest()
    for path in sorted(signals.glob("2026-11-*_paper_portfolio*.json")):
        inputs[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
        artifact = json.loads(path.read_text())
        report = artifact.get("execution_report")
        if not report:
            continue
        inventory["paper_reports"] += 1
        try:
            verify_ledger(report)
        except ValueError as exc:
            inventory["paper_errors"].append({"file": path.name, "reason": str(exc)})
        for fill in report["executed_buy"]:
            physical = float(fill["shares"]) * float(fill["factor"])
            rule = quantity_rule(fill["instrument"], report["date"])
            if (
                physical < rule.minimum - 1e-6
                or physical > rule.maximum + 1e-6
                or abs(physical / rule.step - round(physical / rule.step)) > 1e-6
            ):
                inventory["quantity_errors"].append(
                    {"file": path.name, "instrument": fill["instrument"], "physical_shares": physical}
                )
    for path in sorted((root / "quant/data").rglob("*report.parquet")):
        rel = str(path.relative_to(root))
        inputs[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        report = pd.read_parquet(path)
        if not {"account", "return", "cost", "bench"}.issubset(report):
            continue
        inventory["historical_reports"] += 1
        try:
            initial_cash, sources = report_initial_capital(path, report, root, archive_contracts)
        except (ValueError, OverflowError) as exc:
            inventory["historical_contract_errors"].append({"file": rel, "reason": str(exc)})
            continue
        inventory["historical_account_contracts"][rel] = {"initial_cash": initial_cash, "sources": sources}
        for name in ("account_contract.json", "metrics.json"):
            sidecar = path.parent / name
            if sidecar.is_file():
                inputs[str(sidecar.relative_to(root))] = hashlib.sha256(sidecar.read_bytes()).hexdigest()
        try:
            portfolio_performance(report, initial_cash=initial_cash)
        except (ValueError, OverflowError) as exc:
            inventory["historical_metric_errors"].append({"file": rel, "reason": str(exc)})
            continue
        if not {"turnover", "cash", "total_cost"}.issubset(report):
            continue
        try:
            stressed = apply_cost_stress(report, 10.0, initial_cash=initial_cash)
            corrected = portfolio_performance(stressed, initial_cash=initial_cash)
            inventory["corrected_stress_metrics"][rel] = {
                k: corrected[k]
                for k in (
                    "strategy_total_return",
                    "strategy_cagr",
                    "strategy_max_drawdown",
                    "sharpe",
                    "account_return_max_error",
                )
            }
            # Historical stress risk series was frozen net return minus extra
            # turnover cost; its account path used a different denominator.
            old_net = report["return"] - report["cost"] - report["turnover"] * 0.001
            if len(old_net) > 1 and old_net.std(ddof=1) > 0:
                old_sharpe = old_net.mean() / old_net.std(ddof=1) * np.sqrt(238)
                delta = abs(float(old_sharpe) - corrected["sharpe"])
                inventory["stress_sharpe_changed"] += int(delta > 1e-6)
                inventory["max_absolute_stress_sharpe_delta"] = max(
                    inventory["max_absolute_stress_sharpe_delta"], delta
                )
            inventory["stress_checked"] += 1
        except (ValueError, OverflowError) as exc:
            inventory["stress_errors"].append({"file": rel, "reason": str(exc)})
    for market in ("csi300", "csi500", "csi1000"):
        website = audit_website_series(signals, market)
        if website is not None:
            series, hashes = website
            inputs.update({str((signals / name).relative_to(root)): sha for name, sha in hashes.items()})
            inventory["merged_live"][market] = {
                "latest_date": series["latest_date"],
                "cumulative_return": series["cumulative_return"],
                "data_mode": series["data_mode"],
                "source_manifest_sha256": series["source_manifest_sha256"],
                "st_history_verified": False,
            }
            continue
        suffix = "" if market == "csi1000" else f"_{market}"
        prefix = json.loads((signals / f"{market}_backfill_performance.json").read_text())
        closing = json.loads((signals / f"{prefix['end']}_paper_portfolio{suffix}.json").read_text())
        prefix = attach_backfill_boundary(prefix, closing)
        artifacts = [json.loads(p.read_text()) for p in sorted(signals.glob(f"*_paper_portfolio{suffix}.json"))]
        live = build_forward_performance(
            [a for a in artifacts if a.get("data_mode") != "BACKFILL_RESEARCH"], market=market
        )
        merged = merge_forward_performance(prefix, live)
        inventory["merged_live"][market] = {
            "latest_date": merged["latest_date"],
            "cumulative_return": merged["cumulative_return"],
            "st_fallback_dates": merged.get("st_fallback_dates", []),
        }
    inventory["input_sha256"] = inputs
    inventory["repair_source_sha256"] = {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (
            CODE / "execution/portfolio_performance.py",
            CODE / "execution/execution_limits.py",
            CODE / "execution/order_quantity.py",
            CODE / "production/paper_portfolio.py",
            CODE / "ops/merge_forward_performance.py",
            CODE / "research/csi300_csi500/stage0_p0_core.py",
        )
    }
    return inventory


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(CODE.parents[1])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: v
                for k, v in result.items()
                if k not in ("input_sha256", "corrected_stress_metrics", "repair_source_sha256")
            },
            ensure_ascii=False,
            indent=2,
        )
    )
