"""R01 behavioral tests: three-pool publication freshness validation.

The legacy cron only checked the CSI1000 worker result before assembling
CSI500/CSI300 files under the same signal date.  These tests pin the new
fail-closed cross-market gate: per-market lag=0 on a verified calendar,
provider calendar end bound to the data date, and an identical immutable
snapshot (archive + calendar SHA256) across all three pools.
"""

from pathlib import Path

from signal_publication_gate import validate_multi_market_publication

ROOT = Path(__file__).resolve().parents[2]
CAL = ["2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16", "2026-10-19", "2026-10-20"]


def res(market, date, archive="a" * 64, calendar_sha="b" * 64):
    return {
        "market": market,
        "date": date,
        "provider_identity": {
            "calendar_end": date,
            "archive_sha256": archive,
            "calendar_sha256": calendar_sha,
        },
    }


def pooled(date="2026-10-15", **overrides):
    results = {market: res(market, date) for market in ("csi1000", "csi500", "csi300")}
    results.update(overrides)
    return results


def test_three_pools_same_snapshot_publish():
    action, detail = validate_multi_market_publication(pooled(), "2026-10-16", CAL)
    assert action == "publish"
    assert "lag=0" in detail


def test_friday_close_to_monday_signal_passes_lag_zero():
    action, _ = validate_multi_market_publication(pooled(date="2026-10-16"), "2026-10-19", CAL)
    assert action == "publish"


def test_long_holiday_reopen_passes():
    cal = ["2026-09-30", "2026-10-09", "2026-10-12"]
    action, _ = validate_multi_market_publication(pooled(date="2026-09-30"), "2026-10-09", cal)
    assert action == "publish"


def test_lagging_market_is_rejected():
    results = pooled(csi500=res("csi500", "2026-10-14"))
    action, detail = validate_multi_market_publication(results, "2026-10-16", CAL)
    assert action == "raise"
    assert "csi500" in detail and "lag=1" in detail


def test_future_or_same_day_market_data_is_rejected():
    results = pooled(csi300=res("csi300", "2026-10-16"))
    action, detail = validate_multi_market_publication(results, "2026-10-16", CAL)
    assert action == "raise"
    assert "csi300" in detail


def test_data_date_off_calendar_is_rejected():
    results = pooled(date="2026-10-16", csi500=res("csi500", "2026-10-17"))
    action, detail = validate_multi_market_publication(results, "2026-10-19", CAL)
    assert action == "raise"
    assert "csi500" in detail


def test_provider_calendar_end_mismatch_is_rejected():
    bad = res("csi300", "2026-10-15")
    bad["provider_identity"]["calendar_end"] = "2026-10-14"
    action, detail = validate_multi_market_publication(pooled(csi300=bad), "2026-10-16", CAL)
    assert action == "raise"
    assert "calendar_end" in detail


def test_differing_archive_sha_is_rejected():
    results = pooled(csi500=res("csi500", "2026-10-15", archive="c" * 64))
    action, detail = validate_multi_market_publication(results, "2026-10-16", CAL)
    assert action == "raise"
    assert "disagree" in detail


def test_differing_calendar_sha_is_rejected():
    results = pooled(csi300=res("csi300", "2026-10-15", calendar_sha="d" * 64))
    action, detail = validate_multi_market_publication(results, "2026-10-16", CAL)
    assert action == "raise"
    assert "disagree" in detail


def test_missing_provider_identity_or_wrong_market_is_rejected():
    broken = res("csi1000", "2026-10-15")
    broken["provider_identity"] = {}
    action, _ = validate_multi_market_publication(pooled(csi1000=broken), "2026-10-16", CAL)
    assert action == "raise"
    mislabeled = pooled(csi300=res("csi500", "2026-10-15"))
    action, _ = validate_multi_market_publication(mislabeled, "2026-10-16", CAL)
    assert action == "raise"


def test_calendar_unavailable_or_holiday():
    assert validate_multi_market_publication(pooled(), "2026-10-16", None)[0] == "raise"
    assert validate_multi_market_publication(pooled(), "2026-10-17", CAL)[0] == "skip"


def test_daily_cron_validates_all_markets_before_side_effects():
    src = (ROOT / "quant/code/production/modal_qlib_cn_a10g.py").read_text()
    cron = src[src.index("def daily_cron():") : src.index("# ===================== 第六步")]
    gate = cron.index("validate_multi_market_publication(")
    assert gate < cron.index("_apply_paper_portfolio(")
    assert gate < cron.index("journal.prepare(")
    # Serialization is a platform property (max_containers=1), pinned separately;
    # the critical section itself must not acquire any Volume-file lock.
    assert "lease" not in cron
    # B06: strict chain + live-state gate must run before training/publishing,
    # and chain integrity before any recovery write.
    verify_chain = cron.index("journal.verify_chain_integrity()")
    anchor_gate = cron.index("journal.check_paper_state_entry(")
    remote = cron.index("daily_standalone.remote(")
    apply_paper = cron.index("_apply_paper_portfolio(")
    assert verify_chain < cron.index("_complete_journal_publication(")
    assert verify_chain < remote and anchor_gate < remote
    assert verify_chain < apply_paper and anchor_gate < apply_paper
