"""Fail-closed daily publication contract for the CSI1000 research MVP.

This module deliberately has no Qlib, Modal, pandas or network dependency so
the production boundary can be exercised in lightweight CI.  It validates only
forward-MVP concerns: provider identity, point-in-time CSI1000 membership,
20-session label maturity, frozen model lineage, published Top20 lineage and
paper/risk state.  It does not score historical candidates or tune parameters.
"""
from __future__ import annotations

from bisect import bisect_left
import csv
from datetime import date
import hashlib
import io
import json
import math
from pathlib import Path
import re

MVP_CONTRACT_VERSION = "csi1000_mvp_daily_v1"
_SYMBOL = re.compile(r"^(SH|SZ|BJ)\d{6}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_LINEAGE = (
    "candidate_id",
    "profile",
    "model_config_sha256",
    "stage_b_snapshot_token",
    "contract_version",
)


def _iso_day(value, label: str) -> str:
    value = str(value)
    if date.fromisoformat(value).isoformat() != value:
        raise ValueError(f"{label} must be YYYY-MM-DD: {value!r}")
    return value


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stable_json_sha256(obj) -> str:
    return _sha256_bytes(
        json.dumps(
            obj,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
            default=str,
        ).encode("utf-8")
    )


def sha256_file(path: str | Path, *, chunk_size: int = 1 << 20) -> str:
    """Stream a file hash without materializing a large provider archive."""
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_sha(value, label: str) -> str:
    value = str(value)
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA256")
    return value


def pit_universe_snapshot(
    instruments_file: str | Path,
    asof: str,
    *,
    expected_count: int = 1000,
) -> dict:
    """Return the active CSI1000 membership at *asof* from span records.

    The source is qlib's instruments/csi1000.txt.  Membership is evaluated from
    the start/end span on each row; using today's whole file without the as-of
    condition would silently introduce survivorship leakage.
    """
    asof = _iso_day(asof, "asof")
    path = Path(instruments_file)
    raw = path.read_bytes()
    active: set[str] = set()
    active_rows: set[str] = set()

    for line_no, raw_line in enumerate(raw.decode("utf-8").splitlines(), 1):
        if not raw_line.strip():
            continue
        parts = raw_line.split("\t")
        if len(parts) != 3:
            raise ValueError(f"malformed CSI1000 instrument row {line_no}")
        symbol = parts[0].strip().upper()
        if not _SYMBOL.fullmatch(symbol):
            raise ValueError(f"invalid CSI1000 symbol on row {line_no}: {symbol!r}")
        start = _iso_day(parts[1].strip()[:10], f"row {line_no} start")
        end = _iso_day(parts[2].strip()[:10], f"row {line_no} end")
        if start > end:
            raise ValueError(f"reversed CSI1000 span on row {line_no}")
        if start <= asof <= end:
            # Multiple disjoint historical spans are valid, overlapping active
            # spans for the same instrument are not.
            if symbol in active_rows:
                raise ValueError(f"duplicate active CSI1000 span for {symbol} on {asof}")
            active_rows.add(symbol)
            active.add(symbol)

    if expected_count < 1:
        raise ValueError("expected_count must be positive")
    if len(active) != expected_count:
        raise ValueError(
            f"CSI1000 PIT membership count {len(active)} != expected {expected_count} on {asof}"
        )
    members = sorted(active)
    membership_sha = _sha256_bytes(("\n".join(members) + "\n").encode("utf-8"))
    return {
        "status": "PASS",
        "asof": asof,
        "expected_count": expected_count,
        "member_count": len(members),
        "membership_sha256": membership_sha,
        "source_sha256": _sha256_bytes(raw),
        # Kept for the in-process hard gate. build_mvp_metadata deliberately
        # strips this 1000-name list from the public sidecar.
        "active_members": members,
    }


def validate_label_maturity(
    calendar: list[str],
    training: dict,
    signal_data_date: str,
) -> dict:
    """Prove that train/validation Ref(...,-20) labels mature before next stage."""
    if not calendar or calendar != sorted(set(calendar)):
        raise ValueError("provider calendar must be strictly ordered and unique")
    normalized = [_iso_day(x, "calendar date") for x in calendar]
    signal_data_date = _iso_day(signal_data_date, "signal_data_date")
    required = (
        "fit_asof",
        "horizon",
        "train_start",
        "train_end",
        "valid_start",
        "valid_end",
    )
    missing = [k for k in required if k not in training]
    if missing:
        raise ValueError(f"training contract missing {missing}")

    horizon = int(training["horizon"])
    if horizon != 20:
        raise ValueError(f"CSI1000 MVP requires 20-session label horizon, got {horizon}")
    days = {k: _iso_day(training[k], k) for k in required if k != "horizon"}
    if not (
        days["train_start"] <= days["train_end"] < days["valid_start"]
        <= days["valid_end"] < days["fit_asof"] <= signal_data_date
    ):
        raise ValueError("training split order is invalid")

    index = {d: i for i, d in enumerate(normalized)}
    resolved_days = dict(days)
    # Stage-B's frozen 2016-01-01 training boundary is a New Year's Day
    # holiday, not a sampled bar. Qlib's date slice starts at the first actual
    # session >= that boundary. Reproduce exactly that one documented rule,
    # while never snapping missing train/valid ends, validation starts, fit-asof,
    # or arbitrary training starts (which could conceal a damaged calendar).
    if days["train_start"] == "2016-01-01":
        if "2016-01-01" in index:
            raise ValueError("provider calendar incorrectly includes the 2016-01-01 market holiday")
        start_i = bisect_left(normalized, days["train_start"])
        if start_i >= len(normalized) or normalized[start_i] != "2016-01-04":
            raise ValueError(
                "frozen train_start=2016-01-01 must resolve to verified "
                "first trading session 2016-01-04"
            )
        resolved_days["train_start"] = normalized[start_i]

    absent = [d for d in resolved_days.values() if d not in index]
    if signal_data_date not in index:
        absent.append(signal_data_date)
    if absent:
        raise ValueError(f"training dates missing from provider calendar: {sorted(set(absent))}")
    if index[resolved_days["train_start"]] > index[days["train_end"]]:
        raise ValueError("effective training window is empty after calendar boundary resolution")

    train_obs_i = index[days["train_end"]] + horizon
    valid_obs_i = index[days["valid_end"]] + horizon
    if train_obs_i >= index[days["valid_start"]]:
        raise ValueError("training label tail leaks into validation")
    if valid_obs_i >= index[days["fit_asof"]]:
        raise ValueError("validation label tail leaks into model fit as-of")
    if train_obs_i >= len(normalized) or valid_obs_i >= len(normalized):
        raise ValueError("label maturity observation exceeds provider calendar")

    return {
        "status": "PASS",
        "horizon_sessions": horizon,
        "fit_asof": days["fit_asof"],
        "train_start": days["train_start"],
        "train_start_effective": resolved_days["train_start"],
        "train_start_resolution": (
            "FROZEN_HOLIDAY_TO_NEXT_SESSION"
            if resolved_days["train_start"] != days["train_start"]
            else "EXACT_SESSION"
        ),
        "train_end": days["train_end"],
        "train_label_last_observation": normalized[train_obs_i],
        "valid_start": days["valid_start"],
        "valid_end": days["valid_end"],
        "valid_label_last_observation": normalized[valid_obs_i],
        "signal_data_date": signal_data_date,
    }


def parse_top20_csv(csv_text: str) -> list[dict]:
    stream = io.StringIO(csv_text)
    rows = list(csv.DictReader(stream))
    if not rows or list(rows[0]) != ["rank", "instrument", "score"]:
        raise ValueError("Top20 CSV must have rank,instrument,score columns")
    if len(rows) != 20:
        raise ValueError(f"CSI1000 MVP requires exactly 20 displayed rows, got {len(rows)}")
    parsed = []
    seen = set()
    for expected_rank, row in enumerate(rows, 1):
        try:
            rank = int(row["rank"])
            score = float(row["score"])
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid Top20 rank/score") from exc
        symbol = str(row["instrument"]).upper()
        if rank != expected_rank or not _SYMBOL.fullmatch(symbol):
            raise ValueError(f"invalid Top20 row at rank {expected_rank}")
        if symbol in seen:
            raise ValueError(f"duplicate Top20 instrument {symbol}")
        if not math.isfinite(score):
            raise ValueError(f"non-finite Top20 score for {symbol}")
        seen.add(symbol)
        parsed.append({"rank": rank, "instrument": symbol, "score": score})
    return parsed


def validate_production_lineage(actual: dict, expected: dict) -> dict:
    if not isinstance(actual, dict) or not isinstance(expected, dict):
        raise ValueError("production lineage must be objects")
    mismatches = []
    for key in _REQUIRED_LINEAGE:
        if actual.get(key) != expected.get(key):
            mismatches.append(key)
    if mismatches:
        raise ValueError(f"frozen CSI1000 production lineage mismatch: {mismatches}")
    _validate_sha(actual["candidate_id"], "candidate_id")
    _validate_sha(actual["model_config_sha256"], "model_config_sha256")
    _validate_sha(actual["stage_b_snapshot_token"], "stage_b_snapshot_token")
    if actual.get("profile") != "stage_b_winner":
        raise ValueError("canonical CSI1000 MVP profile must remain stage_b_winner")
    return {key: actual[key] for key in _REQUIRED_LINEAGE}


def summarize_paper_state(
    paper: dict,
    *,
    signal_data_date: str,
    usage_date: str,
    expected_lineage: dict,
) -> dict:
    if not isinstance(paper, dict):
        raise ValueError("paper artifact missing")
    if paper.get("market") != "csi1000":
        raise ValueError("paper market is not csi1000")
    if paper.get("paper_lineage") != "stage_b_winner_canonical":
        raise ValueError("paper lineage is not the canonical winner lineage")
    validate_production_lineage(paper.get("production_lineage"), expected_lineage)
    if paper.get("signal_data_date") != signal_data_date or paper.get("usage_date") != usage_date:
        raise ValueError("paper signal/usage dates are not bound to publication")
    pending = paper.get("pending_orders")
    if not isinstance(pending, dict):
        raise ValueError("paper pending_orders missing")
    if pending.get("signal_date") != signal_data_date or pending.get("execution_date") != usage_date:
        raise ValueError("paper pending order dates mismatch publication")
    buys = pending.get("buy")
    sells = pending.get("sell")
    if not isinstance(buys, list) or not isinstance(sells, list):
        raise ValueError("paper pending buy/sell lists missing")
    if not all(isinstance(x, str) and _SYMBOL.fullmatch(x) for x in buys + sells):
        raise ValueError("paper pending order contains invalid instrument")
    if len(set(buys)) != len(buys) or len(set(sells)) != len(sells) or set(buys) & set(sells):
        raise ValueError("paper pending order sets are invalid")

    report = paper.get("execution_report")
    blocked = 0
    if isinstance(report, dict):
        blocked = len(report.get("blocked_buy") or []) + len(report.get("blocked_sell") or [])
    return {
        "status": pending.get("status") or "planned",
        "signal_date": signal_data_date,
        "execution_date": usage_date,
        "planned_buy_count": len(buys),
        "planned_sell_count": len(sells),
        "prior_execution_report": "present" if isinstance(report, dict) else "none",
        "prior_execution_blocked_order_count": blocked,
        "n_positions": int(paper.get("n_positions", 0)),
        "real_fill_execution": "NOT_CERTIFIED_PAPER_ONLY",
    }


def build_mvp_metadata(
    *,
    signal_csv: str,
    ranking_full: list,
    signal_data_date: str,
    usage_date: str,
    provider: dict,
    pit_universe: dict,
    label_maturity: dict,
    production_lineage: dict,
    expected_lineage: dict,
    paper: dict,
    risk: dict,
    publication_detail: str,
) -> dict:
    """Build the immutable sidecar emitted with each canonical daily signal."""
    signal_data_date = _iso_day(signal_data_date, "signal_data_date")
    usage_date = _iso_day(usage_date, "usage_date")
    if signal_data_date >= usage_date:
        raise ValueError("MVP signal data date must precede usage date")

    top20 = parse_top20_csv(signal_csv)
    if pit_universe.get("status") != "PASS" or pit_universe.get("asof") != signal_data_date:
        raise ValueError("PIT universe was not verified at the signal data date")
    members = set(pit_universe.get("active_members") or [])
    if len(members) != pit_universe.get("member_count"):
        raise ValueError("PIT active member materialization mismatch")
    outside = [r["instrument"] for r in top20 if r["instrument"] not in members]
    if outside:
        raise ValueError(f"display Top20 contains non-PIT members: {outside}")

    if label_maturity.get("status") != "PASS":
        raise ValueError("label maturity did not pass")
    if label_maturity.get("signal_data_date") != signal_data_date:
        raise ValueError("label maturity is bound to a different data date")

    lineage = validate_production_lineage(production_lineage, expected_lineage)
    paper_summary = summarize_paper_state(
        paper,
        signal_data_date=signal_data_date,
        usage_date=usage_date,
        expected_lineage=expected_lineage,
    )

    if not isinstance(ranking_full, list) or len(ranking_full) < 20:
        raise ValueError("full paper ranking is missing")
    clean_ranking = []
    for row in ranking_full:
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            raise ValueError("invalid full ranking row")
        symbol, score = str(row[0]).upper(), float(row[1])
        if not _SYMBOL.fullmatch(symbol) or not math.isfinite(score):
            raise ValueError("invalid full ranking instrument/score")
        if symbol not in members:
            raise ValueError(f"full ranking contains non-PIT member {symbol}")
        clean_ranking.append([symbol, score])
    if len({row[0] for row in clean_ranking}) != len(clean_ranking):
        raise ValueError("duplicate instrument in full ranking")

    if not isinstance(provider, dict):
        raise ValueError("provider identity missing")
    if provider.get("calendar_end") != signal_data_date:
        raise ValueError("provider calendar end is not signal data date")
    for key in ("archive_sha256", "calendar_sha256"):
        _validate_sha(provider.get(key), f"provider {key}")
    if not provider.get("source_url"):
        raise ValueError("provider source URL missing")

    if not isinstance(risk, dict) or "st" not in risk or "usage_day_suspension" not in risk:
        raise ValueError("current ST/suspension risk boundary missing")
    if risk.get("tradeability_certified") is not False:
        raise ValueError("research MVP must not claim tradeability certification")

    public_pit = {k: v for k, v in pit_universe.items() if k != "active_members"}
    return {
        "contract_version": MVP_CONTRACT_VERSION,
        "market": "csi1000",
        "publication": {
            "status": "PASS",
            "signal_data_date": signal_data_date,
            "usage_date": usage_date,
            "canonical_freshness_gate": str(publication_detail),
        },
        "provider": dict(provider),
        "pit_universe": public_pit,
        "label_maturity": dict(label_maturity),
        "production_lineage": lineage,
        "signal_lineage": {
            "display_top20_sha256": _sha256_bytes(signal_csv.encode("utf-8")),
            "display_top20_instruments": [r["instrument"] for r in top20],
            "paper_ranking_full_sha256": stable_json_sha256(clean_ranking),
            "paper_ranking_count": len(clean_ranking),
            "paper_planner_source": "pre_display_limit_filter_full_ranking",
            "display_surface": "board_aware_close_limit_filtered_top20",
        },
        "paper": paper_summary,
        "risk": risk,
    }


def dumps_metadata(metadata: dict) -> str:
    return json.dumps(
        metadata,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"
