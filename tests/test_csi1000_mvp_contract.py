"""MVP publication-contract gates; no historical re-ranking or tuning."""
from __future__ import annotations

from datetime import date, timedelta
import csv
import io
import json

import pytest

from csi1000_mvp_contract import (
    build_mvp_metadata,
    pit_universe_snapshot,
    validate_label_maturity,
)


def _calendar(n=900):
    d = date(2023, 1, 2)
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _symbols():
    # Synthetic but valid Qlib-style symbols; exact count is the contract point.
    return [f"SH{600000 + i:06d}" for i in range(1000)]


def _write_universe(tmp_path, cal):
    path = tmp_path / "csi1000.txt"
    path.write_text(
        "".join(f"{s}\t{cal[0]}\t{cal[-1]}\n" for s in _symbols()),
        encoding="utf-8",
    )
    return path


def _top20(symbols):
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["rank", "instrument", "score"])
    for rank, symbol in enumerate(symbols[:20], 1):
        writer.writerow([rank, symbol, 1.0 - rank / 100])
    return buf.getvalue()


def _lineage():
    return {
        "candidate_id": "a" * 64,
        "profile": "stage_b_winner",
        "model_config_sha256": "b" * 64,
        "stage_b_snapshot_token": "c" * 64,
        "contract_version": "csi1000_stage_b_forward_v1",
    }


def _fixture(tmp_path):
    cal = _calendar()
    data_date = cal[-1]
    usage_date = (date.fromisoformat(data_date) + timedelta(days=3)).isoformat()
    # The pure contract only requires usage > data; canonical trading-session
    # adjacency is separately pinned by signal_publication_gate tests.
    pit = pit_universe_snapshot(_write_universe(tmp_path, cal), data_date)
    fit_i = len(cal) - 1
    training = {
        "fit_asof": cal[fit_i],
        "horizon": 20,
        "train_start": cal[10],
        "train_end": cal[fit_i - 20 - 252 - 21],
        "valid_start": cal[fit_i - 20 - 252],
        "valid_end": cal[fit_i - 21],
    }
    maturity = validate_label_maturity(cal, training, data_date)
    symbols = _symbols()
    lineage = _lineage()
    paper = {
        "market": "csi1000",
        "paper_lineage": "stage_b_winner_canonical",
        "production_lineage": dict(lineage),
        "signal_data_date": data_date,
        "usage_date": usage_date,
        "pending_orders": {
            "signal_date": data_date,
            "execution_date": usage_date,
            "buy": symbols[:20],
            "sell": [],
        },
        "execution_report": None,
        "n_positions": 0,
    }
    provider = {
        "name": "chenditc",
        "source_url": "https://example.test/cn_data.tar.gz",
        "archive_sha256": "d" * 64,
        "calendar_sha256": "e" * 64,
        "calendar_end": data_date,
    }
    risk = {
        "st": {"status": "SOURCE_SNAPSHOT_AVAILABLE", "top20_st": []},
        "usage_day_suspension": {"status": "NOT_INDEPENDENTLY_VERIFIED_PREOPEN"},
        "tradeability_certified": False,
    }
    kwargs = dict(
        signal_csv=_top20(symbols),
        ranking_full=[[s, 1.0 - i / 10000] for i, s in enumerate(symbols)],
        signal_data_date=data_date,
        usage_date=usage_date,
        provider=provider,
        pit_universe=pit,
        label_maturity=maturity,
        production_lineage=lineage,
        expected_lineage=dict(lineage),
        paper=paper,
        risk=risk,
        publication_detail="canonical freshness PASS",
    )
    return kwargs


def test_mvp_contract_happy_path(tmp_path):
    meta = build_mvp_metadata(**_fixture(tmp_path))
    assert meta["publication"]["status"] == "PASS"
    assert meta["pit_universe"]["member_count"] == 1000
    assert "active_members" not in meta["pit_universe"]
    assert meta["label_maturity"]["horizon_sessions"] == 20
    assert len(meta["signal_lineage"]["display_top20_instruments"]) == 20
    assert meta["signal_lineage"]["paper_ranking_count"] == 1000
    assert meta["paper"]["real_fill_execution"] == "NOT_CERTIFIED_PAPER_ONLY"
    assert meta["risk"]["tradeability_certified"] is False


def test_pit_membership_is_asof_and_exact_count(tmp_path):
    cal = _calendar()
    path = _write_universe(tmp_path, cal)
    text = path.read_text()
    path.write_text(text.replace(f"\t{cal[0]}\t{cal[-1]}\n", f"\t{cal[0]}\t{cal[-2]}\n", 1))
    with pytest.raises(ValueError, match="membership count 999"):
        pit_universe_snapshot(path, cal[-1])


def test_non_pit_top20_fails_closed(tmp_path):
    kwargs = _fixture(tmp_path)
    rows = kwargs["signal_csv"].splitlines()
    rows[1] = rows[1].replace(_symbols()[0], "SZ000001")
    kwargs["signal_csv"] = "\n".join(rows) + "\n"
    with pytest.raises(ValueError, match="non-PIT"):
        build_mvp_metadata(**kwargs)


def test_unmatured_validation_label_fails_closed(tmp_path):
    cal = _calendar()
    data_date = cal[-1]
    training = {
        "fit_asof": data_date,
        "horizon": 20,
        "train_start": cal[10],
        "train_end": cal[300],
        "valid_start": cal[400],
        # only 10 sessions before fit_asof, so 20-session labels are not mature
        "valid_end": cal[-11],
    }
    with pytest.raises(ValueError, match="validation label tail"):
        validate_label_maturity(cal, training, data_date)


def test_wrong_model_hash_fails_closed(tmp_path):
    kwargs = _fixture(tmp_path)
    kwargs["production_lineage"] = dict(kwargs["production_lineage"])
    kwargs["production_lineage"]["model_config_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="lineage mismatch"):
        build_mvp_metadata(**kwargs)


def test_missing_risk_boundary_fails_closed(tmp_path):
    kwargs = _fixture(tmp_path)
    kwargs["risk"] = {"tradeability_certified": False}
    with pytest.raises(ValueError, match="ST/suspension"):
        build_mvp_metadata(**kwargs)


def test_paper_date_drift_fails_closed(tmp_path):
    kwargs = _fixture(tmp_path)
    kwargs["paper"] = json.loads(json.dumps(kwargs["paper"]))
    kwargs["paper"]["pending_orders"]["signal_date"] = "2026-01-01"
    with pytest.raises(ValueError, match="pending order dates"):
        build_mvp_metadata(**kwargs)
