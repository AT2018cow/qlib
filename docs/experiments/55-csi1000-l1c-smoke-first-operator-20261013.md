# CSI1000 L1-C operator runbook (2026-10-13)

This is the PR-S1 L1-C continuation of contract 53 and operator README 54.
**No paid Modal execution before independent offline review, merge, and a
reviewed code-equivalence reference for the actual merged SHA.**
No changes to production Winner. Historic periods are not new OOS data.

## Frozen work scope

L1-C is 11 existing Stage-B models x 12 TopK/DropN configurations x
2 phases: **264 cumulative cells**. Reuse **144** cells from the complete
final L1-B run, with 144 per-cell SHA attestations and 32 internal L1-A
attestations in its prior signed summary. Replay the remaining **120** cells
using frozen Stage-B signals, zero model fitting.

The precondition is the L1-B independent two-phase screen: at least
two distinct model/portfolio combinations passed; reported result is 6
qualified configurations over 4 models. The L1-C runner independently
recomputes `screen_l1("l1-b", ...)` against the exported 144 Parquet cells
and signed P0 baseline, and fails closed without that finding.

## 1. Preflight (no Modal compute)

Use a clean worktree at the *merged* L1-C main commit, not an
unmerged feature branch. Confirm pytest, Black, pyflakes, inventory
and Modal argument help pass. Complete P0 and final L1-B export
directories must be downloaded with original relative paths:
`cells/`, `summaries/`, `source_manifest_final.json`,
`frozen_source_11x5.json`, `SHA256SUMS.txt`.

```bash
git rev-parse HEAD
git status --porcelain
python -m pytest -q tests/test_csi1000_layered_search_core.py tests/test_csi1000_layered_search_replay.py tests/test_csi1000_layered_search_selection.py tests/test_csi1000_l1b_runner.py
python -m black -l 120 --check audit/csi1000_layered_search_core.py audit/csi1000_layered_search_modal.py audit/csi1000_layered_search_reconcile.py tests/test_csi1000_l1b_runner.py
python -m pyflakes audit/csi1000_layered_search_core.py audit/csi1000_layered_search_modal.py audit/csi1000_layered_search_reconcile.py tests/test_csi1000_l1b_runner.py
python audit/csi1000_layered_search_modal.py --inventory
modal run audit/csi1000_layered_search_modal.py --help
```

Inventory should list `l1-c=264`, `l1-c-new=120` and
`l1-c-reused-from-l1-b=144`.

The original final L1-B run is `l1b_20261013_b1` under SHA
`125076b5921735ab27b2d1bafcde93b0f242dcb1`.
Derive its exact research prefix from that signed summary:

```text
csi1000_layered_search/csi1000_layered_search_v1/l1b_20261013_b1/l1-b/125076b59217
```

Do not use the new L1-C code SHA for the old reuse prefix. If the
downloaded L1-B summary names a different SHA/run, STOP and investigate.

## 2. Single-cell real smoke

Before running, set these variables to verified values in the same shell:
- `P0_EXPORT`: absolute path of the complete P0 exported prefix
- `L1B_EXPORT`: absolute path of the complete L1-B exported prefix
- `L1C_EQUIVALENCE_REF`: explicit reviewer-approved **merged-code**
  replay equivalence decision, not a synthetic placeholder
- `L1C_RUN_ID`: a fresh run ID, e.g. `l1c_20261014_a1`

Then execute attached (never add `--detach`):

```bash
set -o pipefail
modal run audit/csi1000_layered_search_modal.py \
  --stage l1-c --run-id "$L1C_RUN_ID" \
  --max-workers 1 --batch-limit 1 --smoke \
  --p0-summary "$P0_EXPORT/summaries/p0/stage_p0_summary.json" \
  --reuse-summary "$L1B_EXPORT/summaries/l1-b/stage_l1-b_summary.json" \
  --reuse-prefix csi1000_layered_search/csi1000_layered_search_v1/l1b_20261013_b1/l1-b/125076b59217 \
  --equivalence-ref "$L1C_EQUIVALENCE_REF"
```

One replay container: **2 vCPU / 4096 MiB**, timeout **3600 seconds**,
zero automatic retries. Verify actual Modal App ID, EXIT=0, no OOM
or timeout, **145/264 completed = 144 reused + 1 new, 0 blocked**,
`INCOMPLETE_BATCH`, and SHA/run/cell-bound `smoke_pass.json`.
Stop if any gate fails; do not transport old L1-B smoke markers.

## 3. Normal batches and completion

Use precisely the same shell variables and code SHA, run ID and
signed source export for all batches. Start with **2 workers x 8
new cells**, with `--smoke` removed:

```bash
set -o pipefail
modal run audit/csi1000_layered_search_modal.py \
  --stage l1-c --run-id "$L1C_RUN_ID" \
  --max-workers 2 --batch-limit 8 \
  --p0-summary "$P0_EXPORT/summaries/p0/stage_p0_summary.json" \
  --reuse-summary "$L1B_EXPORT/summaries/l1-b/stage_l1-b_summary.json" \
  --reuse-prefix csi1000_layered_search/csi1000_layered_search_v1/l1b_20261013_b1/l1-b/125076b59217 \
  --equivalence-ref "$L1C_EQUIVALENCE_REF"
```

Expected first normal batch success: **153/264, 0 blocked**.
If resources and runtime remain stable, use **4 workers x 16-32
new cells** by changing only `--max-workers` and `--batch-limit`.
Use **only one foreground coordinator per run/prefix**. An additional
uncoordinated launcher can race inventory with dispatch and produce
ambiguous cumulative counts. Keep each App ID, exit code and batch
log. Do not blindly retry deterministic failures, OOM or SHA drift.
No mandatory P90 quote, budget preapproval or ledger booking for L1-C.

At the end independently verify **264/264 PASS, 144 attestations,
120 computed, 0 blocked**, source and artifact SHA, recalculated
424-day metrics/NAV and same-phase P0 pairing. Screen all **131**
L1-C challenger configurations with `selection.screen_l1("l1-c", ...)`.
The full experiment is research-only and historically consumed, and
does not authorize any production Winner change.
