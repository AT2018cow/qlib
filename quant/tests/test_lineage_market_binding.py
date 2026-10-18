"""R06 behavioral tests: paper lineage must be bound to its market.

The 2026-10-18 review found set-membership lineage checks that let a csi1000
canonical lineage pass for csi300/csi500 artifacts.  These tests pin the
one-to-one market->lineage binding and the explicit legacy opt-in.
"""

import pytest

from csi1000_mvp_contract import summarize_paper_state
from csi1000_production_config import (
    CANONICAL_PAPER_LINEAGES_BY_MARKET,
    CANONICAL_PROFILE,
    WINNER_PROFILE,
    profile_manifest,
)
from forward_performance import build_forward_performance


def make_paper(market, lineage, profile):
    return {
        "market": market,
        "paper_lineage": lineage,
        "production_lineage": profile_manifest(profile),
        "signal_data_date": "2026-10-15",
        "usage_date": "2026-10-16",
        "pending_orders": {
            "signal_date": "2026-10-15",
            "execution_date": "2026-10-16",
            "buy": ["SH600000"],
            "sell": [],
        },
        "execution_report": None,
        "n_positions": 0,
    }


def summary(paper, market):
    return summarize_paper_state(
        paper,
        signal_data_date="2026-10-15",
        usage_date="2026-10-16",
        expected_lineage=profile_manifest(
            CANONICAL_PROFILE
            if market == "csi1000"
            else {
                "csi500": "raw10d_c98856b4_top10_drop1",
                "csi300": "59b93929_top10_drop1",
            }[market]
        ),
        market=market,
    )


def test_current_canonical_lineage_passes_for_its_own_market():
    paper = make_paper("csi500", CANONICAL_PAPER_LINEAGES_BY_MARKET["csi500"], "raw10d_c98856b4_top10_drop1")
    result = summary(paper, "csi500")
    assert result["status"] == "planned"


def test_cross_market_canonical_lineage_is_rejected():
    # csi1000 canonical lineage smuggled into a csi500 artifact.
    paper = make_paper("csi500", "c98856b4_top10_drop1_canonical", CANONICAL_PROFILE)
    with pytest.raises(ValueError, match="not canonical for market"):
        summary(paper, "csi500")


def test_legacy_winner_lineage_requires_explicit_opt_in():
    paper = make_paper("csi1000", "stage_b_winner_canonical", WINNER_PROFILE)
    with pytest.raises(ValueError, match="not canonical for market"):
        summary(paper, "csi1000")
    result = summarize_paper_state(
        paper,
        signal_data_date="2026-10-15",
        usage_date="2026-10-16",
        expected_lineage=profile_manifest(WINNER_PROFILE),
        market="csi1000",
        allow_legacy_winner=True,
    )
    assert result["status"] == "planned"


def test_legacy_opt_in_is_limited_to_csi1000():
    paper = make_paper("csi500", "stage_b_winner_canonical", WINNER_PROFILE)
    with pytest.raises(ValueError, match="not canonical for market"):
        summarize_paper_state(
            paper,
            signal_data_date="2026-10-15",
            usage_date="2026-10-16",
            expected_lineage=profile_manifest(WINNER_PROFILE),
            market="csi500",
            allow_legacy_winner=True,
        )


def test_unknown_market_is_rejected():
    paper = make_paper("chinext", "c98856b4_top10_drop1_canonical", CANONICAL_PROFILE)
    with pytest.raises(ValueError, match="unknown paper market"):
        summarize_paper_state(
            paper,
            signal_data_date="2026-10-15",
            usage_date="2026-10-16",
            expected_lineage=profile_manifest(CANONICAL_PROFILE),
            market="chinext",
        )


def test_forward_series_rejects_cross_market_lineage():
    artifact = {
        "market": "csi300",
        "paper_lineage": "raw10d_c98856b4_top10_drop1_canonical",
        "usage_date": "2026-10-16",
        "execution_report": None,
    }
    with pytest.raises(ValueError, match="cross-market paper lineage mismatch"):
        build_forward_performance([artifact], market="csi300")


def test_forward_series_skips_legacy_lineage_as_not_current():
    artifact = {
        "market": "csi1000",
        "paper_lineage": "stage_b_winner_canonical",
        "usage_date": "2026-10-16",
        "execution_report": None,
    }
    result = build_forward_performance([artifact])
    assert result["status"] == "awaiting_first_valuation"
