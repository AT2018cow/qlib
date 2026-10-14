"""Regress bootstrap-vs-strict quote identity through the REAL Modal inventory body.

No remote Modal calls or paid resources: the SDK's .local() executes the
decorated inventory function with synthetic provider and signed Stage-B
metadata. This specifically catches the deterministic bug missed by old
cell/manifest-only offline tests.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from audit import csi1000_l4_core as l4
from audit import csi1000_l4_modal as runner
from audit import csi1000_layered_search_replay as replay


def test_inventory_quote_and_source_bootstrap_vs_strict_comparison():
    actual_quote = "b" * 64
    actual_snapshot = "a" * 64
    no_expected = {"quote_sha256": "", "source_snapshot_sha256": ""}
    runner.verify_inventory_derived_identity(no_expected, ["000001.SZ"], actual_quote, actual_snapshot)
    runner.verify_inventory_derived_identity(
        {"quote_sha256": actual_quote, "source_snapshot_sha256": actual_snapshot},
        ["000001.SZ"],
        actual_quote,
        actual_snapshot,
    )
    with pytest.raises(ValueError, match="STOP_L4_QUOTE_SHA"):
        runner.verify_inventory_derived_identity(
            {"quote_sha256": "f" * 64}, ["000001.SZ"], actual_quote, actual_snapshot
        )
    with pytest.raises(ValueError, match="STOP_L4_SOURCE_SNAPSHOT_SHA"):
        runner.verify_inventory_derived_identity(
            {"source_snapshot_sha256": "f" * 64}, ["000001.SZ"], actual_quote, actual_snapshot
        )
    with pytest.raises(ValueError, match="STOP_L4_QUOTE_SHA"):
        runner.verify_inventory_derived_identity(no_expected, [], actual_quote, actual_snapshot)
    with pytest.raises(ValueError, match="STOP_L4_QUOTE_SHA"):
        runner.verify_inventory_derived_identity(no_expected, ["000001.SZ"], "", actual_snapshot)


@pytest.mark.skipif(runner.modal is None, reason="Real Modal SDK required for inventory .local() test")
def test_actual_inventory_remote_local_bootstrap_and_drift(monkeypatch):
    """Run the *actual decorated inventory_remote* body, with no cloud calls."""
    stage_b = replay.load_frozen_result(".")
    manifest = l4.frozen_l4_manifest(".")
    coverage = l4.check_stage_b_source_coverage(stage_b, manifest)
    real_snapshot_token = stage_b["manifest"]["snapshot_token"]
    real_provider = stage_b["manifest"]["provider_fingerprint"]
    snapshot_sha = "a" * 64
    quote_sha = "b" * 64

    qlib = types.ModuleType("qlib")
    qlib.init = lambda **kw: None
    qlib.data = types.ModuleType("qlib.data")
    qlib.data.D = object()
    monkeypatch.setitem(sys.modules, "qlib", qlib)
    monkeypatch.setitem(sys.modules, "qlib.data", qlib.data)
    monkeypatch.setattr(runner, "load_frozen_result", lambda root: stage_b)
    monkeypatch.setattr(runner, "frozen_l4_manifest", lambda root: manifest)
    monkeypatch.setattr(
        runner,
        "_source_checks",
        lambda: {
            "snapshot_token": real_snapshot_token,
            "provider_fingerprint": real_provider,
            "source_snapshot_sha256": snapshot_sha,
        },
    )
    monkeypatch.setattr(runner, "p0_archive", SimpleNamespace(reload=lambda: None))
    monkeypatch.setattr(
        runner,
        "resolve_phase_sources",
        lambda root, row, candidate, phase: {"signal_sha256": row["signal_artifact"]["sha256"]},
    )
    monkeypatch.setattr(
        runner,
        "_verified_original_p0",
        lambda phase, meta: (
            {"signal_sha256": replay.lookup_phase(stage_b, l4.BASELINE_CANDIDATE, phase)["signal_artifact"]["sha256"]},
            Path("/verified-p0-not-read"),
        ),
    )
    monkeypatch.setattr(replay, "static_quote_union", lambda *a, **kw: (["000001.SZ"], quote_sha))

    base = {
        "run_id": "l4_20261014_a1",
        "code_sha": "0" * 40,
        "manifest_sha256": l4.FROZEN_MANIFEST_SHA,
        "provider_fingerprint": real_provider,
        "source_digest": coverage["source_digest"],
        "quote_sha256": "",
        "source_snapshot_sha256": "",
        "p0_prefix": runner.EXPECTED_P0_PREFIX,
    }
    first = runner.inventory_remote.local(base)
    assert first["status"] == "PASS" and first["source_model_phases"] == 15
    assert first["quote_sha256"] == quote_sha
    assert first["source_snapshot_sha256"] == snapshot_sha
    assert first["no_new_model_fit"] is True

    repeated = runner.inventory_remote.local(
        {**base, "quote_sha256": quote_sha, "source_snapshot_sha256": snapshot_sha}
    )
    assert repeated == first

    with pytest.raises(ValueError, match="STOP_L4_QUOTE_SHA"):
        runner.inventory_remote.local({**base, "quote_sha256": "f" * 64})
    with pytest.raises(ValueError, match="STOP_L4_SOURCE_SNAPSHOT_SHA"):
        runner.inventory_remote.local({**base, "source_snapshot_sha256": "f" * 64})

    # Pre-existing source/provider/signed manifest gates remain fail-closed.
    with pytest.raises(ValueError, match="STOP_L4_SOURCE_PROVIDER"):
        runner.inventory_remote.local({**base, "provider_fingerprint": "f" * 64})
    with pytest.raises(ValueError, match="STOP_L4_SOURCE_MANIFEST_SHA"):
        runner.inventory_remote.local({**base, "source_digest": "f" * 64})
