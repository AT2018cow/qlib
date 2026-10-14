"""Exercise local L4 coordinator through frozen Stage-B path handoff.

Unlike pure manifest tests, this executes run_l4's actual local entrypoint
with stubbed Modal network operations, so accidentally using /root/qlib
on the host is detected without launching a paid Modal container.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from audit import csi1000_l4_core as l4
from audit import csi1000_l4_modal as runner
from audit import csi1000_layered_search_replay as replay
from audit import csi1000_layered_search_reconcile as reconcile


@pytest.mark.skipif(runner.modal is None, reason="Requires installed real Modal SDK")
def test_frozen_jobs_use_explicit_local_root_and_remote_default(monkeypatch, tmp_path):
    frozen = replay.load_frozen_result(".")
    manifest = l4.frozen_l4_manifest(".")
    calls = []

    def real_local_loader(repo_root):
        calls.append(Path(repo_root))
        if Path(repo_root) == runner.REPO:
            raise PermissionError("local process must never access /root/qlib")
        return frozen

    monkeypatch.setattr(runner, "load_frozen_result", real_local_loader)
    meta = {"run_id": "l4_20261014_b2", "code_sha": "c" * 40}
    jobs = runner._jobs_for_run(meta, manifest, tmp_path)
    assert calls == [tmp_path]
    assert len(jobs) == 20
    assert {job["phase"] for job in jobs} == {0, 4, 6, 10, 15}
    assert all(len(job["expected_signal_sha256"]) == 64 for job in jobs)
    with pytest.raises(PermissionError, match="local process"):
        runner._jobs_for_run(meta, manifest)


@pytest.mark.skipif(runner.modal is None, reason="Requires installed real Modal SDK")
@pytest.mark.parametrize("mode", ["smoke", "complete"])
def test_actual_run_l4_local_entrypoint_follows_context_and_dispatches(monkeypatch, tmp_path, mode):
    """Cover local entrypoint up through smoke or complete, all remote calls stubbed."""
    manifest = l4.frozen_l4_manifest(".")
    frozen = replay.load_frozen_result(".")
    provider = frozen["manifest"]["provider_fingerprint"]
    summary = tmp_path / "summaries" / "p0" / "stage_p0_summary.json"
    summary.parent.mkdir(parents=True)
    old_sha = "cffb9cd56d5259dbb8efa7afacfca15c3ae0f1b8"
    summary.write_text(
        json.dumps(
            {
                "run_id": "p0_20261013_c1",
                "code_sha": old_sha,
                "verified_cells": [{"provider_fingerprint": provider}],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "SHA256SUMS.txt").write_text("# mocked by test\n", encoding="utf-8")
    code_sha = "c" * 40
    snapshot_sha, quote_sha = "a" * 64, "b" * 64

    monkeypatch.setattr(runner, "_actual_sha", lambda: code_sha)
    monkeypatch.setattr(runner, "require_p0_pass", lambda *args: None)
    monkeypatch.setattr(runner, "verify_export_cells", lambda *args: None)
    monkeypatch.setattr(reconcile, "verify_sha256sums", lambda *args: None)

    # Unchanged real frozen result and selection logic still execute.
    def context_loader(repo_root):
        assert Path(repo_root) != runner.REPO, "local code accessed /root/qlib"
        assert Path(repo_root).resolve() == Path.cwd()
        return frozen

    monkeypatch.setattr(runner, "load_frozen_result", context_loader)

    class Remote:
        def __init__(self, fn):
            self.remote = fn

    called = []
    frozen_markers = {}

    def inventory(meta):
        assert meta["source_digest"] == l4.check_stage_b_source_coverage(frozen, manifest)["source_digest"]
        assert meta["quote_sha256"] == "" and meta["source_snapshot_sha256"] == ""
        called.append("inventory")
        return {
            "status": "PASS",
            "source_model_phases": 15,
            "source_snapshot_sha256": snapshot_sha,
            "quote_sha256": quote_sha,
        }

    monkeypatch.setattr(runner, "inventory_remote", Remote(inventory))
    monkeypatch.setattr(
        runner,
        "marker_remote",
        Remote(
            lambda meta, key: (
                None
                if mode == "smoke"
                else {
                    "status": "PASS",
                    "replay_cell_shas": frozen_markers["smoke_hashes"],
                }
            )
        ),
    )

    def dispatched(jobs, workers, remaining):
        assert workers == (1 if mode == "smoke" else 16)
        assert len(jobs) == (2 if mode == "smoke" else 20)
        assert all(
            j["code_sha"] == code_sha and j["quote_sha256"] == quote_sha and j["source_snapshot_sha256"] == snapshot_sha
            for j in jobs
        )
        if mode == "smoke":
            assert jobs[0]["topk"] == 20 and jobs[1]["topk"] == 10
            assert jobs[0]["phase"] == jobs[1]["phase"] == 0
        called.append(("dispatch", len(jobs)))
        return True

    monkeypatch.setattr(runner, "_dispatch", dispatched)
    monkeypatch.setattr(runner, "inspect_remote", Remote(lambda jobs: [{"status": "PASS"} for _ in jobs]))
    monkeypatch.setattr(
        runner, "freeze_remote", Remote(lambda meta, key, payload: frozen_markers.update({key: payload}) or payload)
    )
    if mode == "complete":
        import audit.csi1000_layered_search_core as c

        jobs = [
            {
                **{
                    "run_id": "l4_20261014_b2",
                    "code_sha": code_sha,
                    "manifest_sha256": l4.FROZEN_MANIFEST_SHA,
                    "source_digest": l4.check_stage_b_source_coverage(frozen, manifest)["source_digest"],
                    "provider_fingerprint": provider,
                    "quote_sha256": quote_sha,
                    "source_snapshot_sha256": snapshot_sha,
                    "p0_prefix": runner.EXPECTED_P0_PREFIX,
                },
                **cell,
                "expected_signal_sha256": replay.lookup_phase(frozen, cell["candidate_id"], cell["phase"])[
                    "signal_artifact"
                ]["sha256"],
            }
            for cell in l4.l4_cell_specs(manifest)
        ]
        frozen_markers["smoke_hashes"] = [c.stable_sha(jobs[0]), c.stable_sha(jobs[5])]
        monkeypatch.setattr(
            runner,
            "final_remote",
            Remote(
                lambda meta, jobs: {
                    "status": "L4_RESEARCH_COMPLETE_NOT_RELEASED",
                    "completed_cells": 20,
                    "real_new_model_fits": 0,
                    "decision": "KEEP_BASELINE_FOR_PRODUCT_SELECTION",
                }
            ),
        )

    runner.run_l4(
        mode=mode,
        run_id="l4_20261014_b2",
        p0_summary=str(summary),
        p0_prefix=runner.EXPECTED_P0_PREFIX,
        max_workers=1 if mode == "smoke" else 16,
    )
    assert called[0] == "inventory"
    assert ("dispatch", 2 if mode == "smoke" else 20) in called
    assert "smoke_pass" in frozen_markers if mode == "smoke" else "l4_final_decision" in frozen_markers
