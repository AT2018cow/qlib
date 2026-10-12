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

## P0 image-build failure and re-attempt guard (2026-10-13)

- The first P0 attempt was **BLOCKED before any replay cell was dispatched**:
  the git-less source image ran `make prerequisite` before installing pyqlib,
  and setuptools-scm raised `LookupError: unable to detect version`.
  No P0 result exists from that attempt.
- The operator reported a real billed image-build charge of
  **USD 0.00052688** (App ID `ap-xVpkAvkIqizi3X3lHMKHMg`),
  booked to P0 with billing evidence. Check the actual local
  persisted ledger and saved bill again before any new attempt;
  these claims in this document are not a substitute for billing verification.
- PR-S0 now mirrors the working Stage-A image: install pyqlib
  non-editably with `pip install . --no-build-isolation --no-deps`
  so source-tree `qlib/_version.py` is populated, then invoke
  the two-extension direct Cython build in-place. Smoke-import both
  compiled extensions. Image-build/runtime compatibility still needs
  a genuine subsequent Modal test; static/pytest green does not prove it.
- Re-run the offline suite and independent clean-commit check first,
  confirm the previous provisional estimate has been cleared by
  real billing, then refresh the rate SHA/P90 quote for the **new**
  image and report the residual P0 cap (less than USD 2).
  Keep the **same persistent ledger** and use a **new run_id**;
  never silently reset booked spend or reuse an old image/code SHA.
- A new `modal run` constitutes new billable activity and is **not**
  authorized by the code fix itself. Obtain explicit operator budget
  approval and remain in the attached foreground. If anything fails,
  stop and book the App's actual build/queue/helper costs before
  deciding on another attempt.

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
