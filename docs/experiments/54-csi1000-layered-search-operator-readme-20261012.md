# CSI1000 layered search — operator checklist (code only, NOT EXECUTED)

This is an operational companion to contract 53. It is not a budget approval, proof of a P0 run or release authorization.

## Existing selection code that must be reused

- csi1000_tuner_core.py, csi1000_tuner.py: deterministic Stage-A 80 candidates, 4 folds, ranking and training.
- csi1000_stage_b_core.py, csi1000_stage_b.py: Stage-B 11 candidate long-run rolling fits and five-phase ranking.
- audit/replay_stage_b_f1.py and audit/run_stage_b_corrected_rerank.py: fixed-quote audit and independent Stage-B accounting.
- csi1000_production_config.py: frozen Baseline/Winner production identities; unchanged by PR-S0.

## New files on PR #68

- audit/csi1000_layered_search_core.py: frozen contract, budgets, paired comparisons and human actual-charge booking CLI.
- audit/csi1000_layered_search_replay.py: original frozen-signal Qlib replay, corrected F1 reference comparison, full artifact SHA.
- audit/csi1000_layered_search_modal.py: attached P0/L1-A, 55-source readonly inventory, separate research volume, bounded resume and finalization.
- audit/csi1000_layered_search_reconcile.py: offline evidence and decision checking.
- audit/csi1000_layered_search_selection.py: offline L1 ranking, deterministic L2 four-plus-four proposals, conditional L3 *plan*, final shortlist and L4 verdict arithmetic.

**Important:** L2 and L3 here are deterministic candidate/structure planners, NOT full real-fit cloud runners. Real PIT label, purge, rolling fit and five-phase execution remain BLOCKED until separate executable code review. Do not treat the old Stage-B worker as a safe source-writing substitute.

## Non-billable static/local verification

Run from the root of a clean research branch checkout:

~~~bash
python -m compileall -q audit/csi1000_layered_search_*.py tests/test_csi1000_layered_search_*.py
python -m pytest -q tests/test_csi1000_layered_search_core.py tests/test_csi1000_layered_search_replay.py tests/test_csi1000_layered_search_selection.py
python audit/csi1000_layered_search_core.py --check-frozen
python audit/csi1000_layered_search_selection.py --stage l2-propose --output /tmp/csi1000_l2_plan.json
python audit/csi1000_layered_search_modal.py --inventory
python audit/csi1000_layered_search_modal.py --help
modal run audit/csi1000_layered_search_modal.py --help
~~~

Capture genuine exit codes; do not infer tests passed merely because tests were written.

## Before any actual P0

Obtain explicit separate authorization for the new USD 30 research envelope and P0 USD 2 limit. Verify original source Volume identity, no source writes, independent research Volume, attached foreground Modal CLI, at most 2 initial P0 workers, and an OpenCode timeout sufficient for build/queue/execution/finalization. Never use modal run --detach, nohup, shell &, or an untracked background process.

Collect current authorized Modal billing rates and real measured worker resource seconds, along with a saved billing report. Supply a local JSON quote of the following schema:

~~~json
{
  "schema": "modal_cost_p90_quote_v1",
  "captured_utc": "REPLACE_WITH_CURRENT_REAL_ISO_UTC",
  "rate_report_sha256": "REPLACE_WITH_ACTUAL_64_HEX_SHA256",
  "cpu_rate_per_second": 0,
  "memory_rate_per_gib_second": 0,
  "p90_cpu_seconds_per_cell": 0,
  "p90_gib_seconds_per_cell": 0,
  "overhead_usd": 0,
  "covered_cells": 4
}
~~~

**The zero prices, zero resource seconds and placeholders above are deliberately invalid.** Do not use them as executable estimates. Fill every field from approved actual cost evidence; check that P90 cost and pending spend stay within each layer and global cap. The remote P0 entrypoint requires --stage p0, --run-id, --max-workers 2, --estimated-total-usd, --cost-quote and a persistent --ledger outside the repository.

## P0 build incident and fast unblocking (2026-10-13)

The two prior P0 attempts **never reached replay**:

- First: `make prerequisite` failed because setuptools-scm could not
  derive the dynamic pyqlib package version in a Modal image without `.git`.
- Second: `pip install . --no-build-isolation --no-deps` failed for the
  same reason under the newer setuptools/vcs-versioning toolchain.
- Operator reported two actual App charges, USD 0.00052688 +
  USD 0.00384060 = **USD 0.00436748** P0 cumulative. No replay
  cells ran. Preserve the existing ledger and billing evidence.

**Current fix:** P0 does **not need to package/install pyqlib** to replay
already frozen signals. The image now copies the repository as before,
writes a build-only `qlib/_version.py`, compiles the two needed Cython
modules from a separate `/tmp` working directory, writes compiled
extensions into the copied source tree and smoke-imports Qlib. This
avoids invoking `setuptools-scm` metadata generation altogether.
The canonical code Git SHA and provider/source SHA are unchanged.

Do not waste cycles on wheel packaging tests (P0 never creates one).
Run the existing short local pytest once and check the Modal CLI. Then,
with the **same** persisted ledger, a refreshed P90 estimate and the
operator's approval, run the actual **four-cell P0** in the attached
foreground (initial maximum two workers) and collect the real results.
Only genuine remote replay verifies the image. If the build fails again,
save its exact traceback and App ID; do not trigger an automatic
unbounded retry. The existing P0 $2 budget remains the software
contract unless separately reviewed.

## After a P0 run, before L1

1. Retain actual Modal App ID, true exit status, UTC timestamps, both server and CLI logs, failures/retries and billed charges.
2. Download the **complete** research output prefix preserving its directory structure, including frozen_source_11x5.json, cells/, source_manifest_final.json, SHA256SUMS.txt and summaries/p0/stage_p0_summary.json. Keep original private signals/provider off public GitHub.
3. Demand 4/4 genuine Baseline/Winner × phase0/10 controls, both legacy and F1-corrected reference, full daily NAV/fee/order artifacts, distinct paper accounts and valid source hashes. Failures are STOP_P0, not PASS.
4. Book the actual bill (failed attempts included) via:
~~~bash
python audit/csi1000_layered_search_core.py --book-actual --ledger /path/outside/repo/cost_ledger.json --layer p0 --run-id ACTUAL_ID --app-id REAL_APP_ID --spent-usd REAL_BILLED_USD --elapsed-hours REAL_HOURS --billing-report /path/to/saved_bill.json
~~~
5. Only then review L1-A (32 cells) budget/timeout. Pass the complete downloaded P0 summary and signed evidence bundle to the guarded L1-A CLI. New Layer 2/3 cloud fits and Layer 4 final runs remain separately gated.

## Interpretation

Historic 2025–2026 returns were consumed already and do not represent new OOS evidence. L1 never fits. Changing label horizon or retrain frequency requires new real fits with matured labels and purge. L4 needs all five predefined phases, relative Baseline advantages and full risk/cost gates. No automatic switch of the canonical production Winner or website/paper release.
