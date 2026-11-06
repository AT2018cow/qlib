"""Reconstruct independent 1M accounts from October 8 using audited signals."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from prelive_revalidation import prepare_data, replay, write_result, file_sha
from csi1000_production_config import profile_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True, type=Path)
    parser.add_argument("--provider-identity", required=True, type=Path)
    parser.add_argument("--signals", required=True, type=Path)
    parser.add_argument("--training-results", required=True, type=Path)
    parser.add_argument("--register", required=True, type=Path)
    parser.add_argument("--backfill-register", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    register = json.loads(args.register.read_text())
    contract = json.loads(args.backfill_register.read_text())
    identity = json.loads(args.provider_identity.read_text())
    models = {row["market"]: row for row in json.loads(args.training_results.read_text())}
    if set(models) != {"csi300", "csi500", "csi1000"}:
        raise ValueError("three completed market training results required")
    args.out.mkdir(parents=True, exist_ok=False)
    chosen = {"csi300": ["csi300_B_S01"], "csi500": ["csi500_B_S02"], "csi1000": ["csi1000_A_S01", "csi1000_A_S02"]}
    rows, plots = [], {}
    for market, ids in chosen.items():
        trained = models[market]
        implementation_sha = contract.get("training_source_manifest_sha256")
        if implementation_sha is not None and (
            trained.get("source_identity_version") != 2
            or trained.get("source_sha256") != implementation_sha
            or trained.get("source_manifest", {}).get("sha256") != implementation_sha
            or trained.get("driver_sha256") != contract["training_driver_sha256"]
        ):
            raise ValueError("training implementation manifest differs from registered source")
        if (
            trained.get("status") != "COMPLETED"
            or trained.get("first_execution") != "2026-10-08"
            or (
                implementation_sha is None
                and (
                    trained.get("source_identity_version", 1) != 1
                    or trained.get("source_sha256") != contract["training_driver_sha256"]
                )
            )
            or trained.get("asset_digest") != identity["asset_digest"]
        ):
            raise ValueError("training/provider/date/source identity mismatch")
        source = args.signals / market / "signal.parquet"
        if file_sha(source) != trained["signal_sha256"]:
            raise ValueError("downloaded forecast fingerprint mismatch")
        for fit in trained["fits"]:
            if (
                fit.get("status") != "PASS"
                or fit["model_config_sha256"] != profile_manifest(fit["profile"])["model_config_sha256"]
            ):
                raise ValueError("fit receipt/profile mismatch")
            if implementation_sha is not None and (
                fit.get("source_identity_version") != 2 or fit.get("source_manifest_sha256") != implementation_sha
            ):
                raise ValueError("fit implementation source mismatch")
        data = prepare_data(args.provider, [source], market, "2026-09-30", trained["last_signal"])
        if data["days"][1] != "2026-10-08":
            raise ValueError("first execution is not October 8")
        data["provenance"]["asset_digest"] = identity["asset_digest"]
        data["provenance"]["training_results_sha256"] = file_sha(args.training_results)
        for candidate_id in ids:
            candidate = next(c for c in register["candidates"] if c["id"] == candidate_id)
            result = replay(data, candidate, account=1_000_000, slippage_bps=0)
            result["metrics"].update(
                evaluation_mode="BACKFILL_RESEARCH",
                first_execution="2026-10-08",
                initial_cash=1_000_000,
                drawdown_limit_enabled=False,
                single_weight_limit_enabled=False,
                broker_execution="MANUAL_ONLY",
                signal_profile=trained["fits"][0]["profile"],
            )
            write_result(result, args.out / candidate_id)
            (args.out / candidate_id / "inputs.json").write_text(json.dumps(data["provenance"], indent=2) + "\n")
            last = result["report"].iloc[-1]
            rows.append(
                {
                    "candidate": candidate_id,
                    "market": market,
                    "incumbent": candidate["incumbent"],
                    "start": "2026-10-08",
                    "end": result["metrics"]["end"],
                    "sessions": len(result["report"]),
                    "initial_cash": 1_000_000,
                    "final_nav_cny": float(last["account"]),
                    "net_return": result["metrics"]["strategy_total_return"],
                    "max_drawdown": result["metrics"]["strategy_max_drawdown"],
                    "cash": float(last["cash"]),
                    "positions": int(last["n_positions"]),
                    "total_fees": result["metrics"]["total_fees"],
                    "quote_gap_count": result["metrics"]["quote_gap_count"],
                    "evidence": "REFERENCE_ONLY",
                    "live_trading_approved": False,
                }
            )
            plots[candidate_id] = result["report"]["account"] / 1_000_000
            print(
                f"[october] {candidate_id} sessions={len(result['report'])} net={result['metrics']['strategy_total_return']:.2%} final={last['account']:.2f}",
                flush=True,
            )
    payload = {
        "status": "COMPLETED_RESEARCH_BACKFILL",
        "source_digest": identity["asset_digest"],
        "first_execution": "2026-10-08",
        "accounts": rows,
        "broker_fills_certified": False,
        "production_accounts_overwritten": False,
        "canonical_profiles_switched": False,
    }
    (args.out / "summary.json").write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n")
    pd.DataFrame(rows).to_csv(args.out / "summary.csv", index=False)
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(3, 1, figsize=(10, 8), sharex=True)
    for ax, market in zip(axes, ("csi300", "csi500", "csi1000")):
        for key, nav in plots.items():
            if not key.startswith(market):
                continue
            dates = [pd.Timestamp("2026-10-08 09:25")] + [d + pd.Timedelta(hours=15) for d in nav.index]
            ax.plot(dates, [1.0] + list(nav), label=key)
        ax.axhline(1.0, color="grey", linewidth=0.6)
        ax.set_title(market.upper())
        ax.set_ylabel("NAV / initial cash")
        ax.grid(alpha=0.2)
        ax.legend(fontsize=8)
    fig.suptitle("Corrected paper replay: 2026-10-08 to " + rows[0]["end"] + " (reference evidence)")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(args.out / "october-equity.png", dpi=160)


if __name__ == "__main__":
    main()
