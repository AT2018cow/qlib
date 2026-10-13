# CSI1000 layered search — operator README (L1-B smoke-first update, 2026-10-13)

This document accompanies [contract 53](53-csi1000-budgeted-layered-strategy-search-contract-20261012.md).
**For L1-B only, its 2026-10-13 amendment supersedes legacy mandatory USD30/$6 gates,
operator P90 quote, charge-booking ledger and per-batch budget approval.** Retain all
P0/L1-A artifacts and billing history; do not reset or rewrite them. No Modal
job is launched merely by merging this PR. Production remains unchanged.

## Known completed research and protected invariants

- P0 **4/4** and L1-A **32/32** evidence exist and must be independently exported
  and verified. **Do not replay these cells.** L1-B contains 144 cumulative cells:
  **32 SHA-verified reused + 112 truly new**.
- Existing canonical Baseline/Winner and frozen Stage-B sources remain unchanged.
  The original source Modal Volume is mounted read-only; only a separate research
  Volume and versioned run prefix receive outputs.
- The L1-B prior-run prefix is derived from the signed L1-A run ID, stage and
  code SHA. All five reuse files per cell must match, and live provider,
  static F1 quote union and 55-row frozen-source hashes must match the export.
  The existing reviewer-approved cross-code equivalence reference is still required.
- L1-C remains gated until at least two different model/portfolio configurations
  meet the predefined **two-phase coarse screen**; this is historically consumed
  selection, **not new OOS**. No experimental result automatically goes live.
- L2/L3 real-fit implementation and L4 final selection still need separate
  executable reviews. The older P0/L1-A cost CLI remains for history and
  compatibility; **L1-B does not call it**.

## 1. Free, local preflight (before any Modal spend)

On a clean worktree at the exact code SHA intended for **both** smoke and
subsequent batches, run and record true exit codes:

```bash
git rev-parse HEAD
git status --porcelain
python -m compileall -q audit/csi1000_layered_search_*.py
python -m pytest -q -p no:cacheprovider \
  tests/test_csi1000_layered_search_core.py \
  tests/test_csi1000_layered_search_replay.py \
  tests/test_csi1000_layered_search_selection.py \
  tests/test_csi1000_l1b_runner.py
python -m black -l 120 --check \
  audit/csi1000_layered_search_core.py \
  audit/csi1000_layered_search_modal.py \
  audit/csi1000_layered_search_reconcile.py \
  tests/test_csi1000_l1b_runner.py
python -m pyflakes \
  audit/csi1000_layered_search_core.py \
  audit/csi1000_layered_search_modal.py \
  audit/csi1000_layered_search_reconcile.py \
  tests/test_csi1000_l1b_runner.py
python audit/csi1000_layered_search_modal.py --inventory
modal run audit/csi1000_layered_search_modal.py --help
```

Verify that the complete **signed P0 and L1-A exported bundles** are available
locally (summary plus source manifest, full cells, SHA256SUMS and frozen
11×5 source list). Do not substitute incomplete summary-only JSON. Determine
the exact remote old L1-A prefix from its signed run ID/stage/code SHA.
Record a genuine review/equivalence reference for reusing outputs across code
versions; inventing a reference is not a valid substitute for evidence.

## 2. First billable Modal action: ONE-cell smoke

Run only after the above checks are green. Choose one new L1-B `run_id`;
**keep that run ID, HEAD and research prefix for every later batch**. With the
`modal run` launcher **attached in the foreground**, and a tool timeout long
enough for build, queue, one replay and finalization:

```bash
set -o pipefail
modal run audit/csi1000_layered_search_modal.py \
  --stage l1-b --run-id L1B_REPLACE_WITH_FIXED_RUN_ID \
  --max-workers 1 --batch-limit 1 --smoke \
  --p0-summary /path/to/complete/p0/summaries/p0/stage_p0_summary.json \
  --reuse-summary /path/to/complete/l1a/summaries/l1-a/stage_l1-a_summary.json \
  --reuse-prefix 'csi1000_layered_search/PROTOCOL/OLD_RUN/l1-a/OLD_CODE_SHA12' \
  --equivalence-ref 'REAL_REVIEWED_EQUIVALENCE_REFERENCE' \
  2>&1 | tee /tmp/csi1000-l1b-smoke.log
```

All uppercase placeholders MUST be replaced from the verified evidence before
running; do not paste as executable identifiers. **No** `--ledger`,
`--cost-quote`, `--estimated-total-usd` or separate per-batch approval is
required for L1-B. These legacy CLI options are ignored for this stage.

The smoke uses **the same image and remote replay function as longer batches**:
2 vCPU, **4096 MiB RAM per replay container**, a 3600-second per-cell timeout
and **zero automatic retries**. The smoke also verifies 55 sources, current
provider/quote, 32 old cells before calculating the one new cell and checks all
33 cumulative artifacts/metrics. Success must yield `SMOKE_PASS` in the CLI
and `smoke_pass.json` in the new research prefix. A failed/blocked smoke does
not authorize a long run. The code checks the persisted marker and corresponding
cell's `cell.json` hash at the start of every longer L1-B batch.

**If image build/import/Cython/volume mounting fails, stop.** Diagnose the exact
first failure and App ID before a second attempt; do not blindly retry or launch
a large batch. Do not mistake `modal run ... --help` for a real smoke.

## 3. After SMOKE_PASS: bounded batches, moderate concurrency

Start with 2 workers and 8 **new** cells, same arguments as above **minus
`--smoke`**:

```bash
modal run audit/csi1000_layered_search_modal.py \
  --stage l1-b --run-id L1B_REPLACE_WITH_FIXED_RUN_ID \
  --max-workers 2 --batch-limit 8 \
  --p0-summary /path/to/complete/p0/summaries/p0/stage_p0_summary.json \
  --reuse-summary /path/to/complete/l1a/summaries/l1-a/stage_l1-a_summary.json \
  --reuse-prefix 'csi1000_layered_search/PROTOCOL/OLD_RUN/l1-a/OLD_CODE_SHA12' \
  --equivalence-ref 'REAL_REVIEWED_EQUIVALENCE_REFERENCE'
```

When these workers succeed without abnormal memory pressure, OOM, repeated
builds or oversized queue delays, use **4 workers** with **16–32 new cells**
per batch. At 4 workers the nominal replay allocation is **8 vCPU/16 GiB**
total; at 8 workers, **16 vCPU/32 GiB**. Eight is the absolute software cap,
**not** the default; increase only with evidence of stable actual load and
usefulness. Reduce batch size if your OpenCode tool timeout may expire before
all waves finalize. **Never use `modal run --detach`, `nohup`, shell `&`
or untracked background processes.**

The runner resumes only previously SHA-verified completed new cells, never
replays the 32 old overlap cells, and refuses mixed output SHA. The first smoke
cell is part of the 112, **not an additional 113th calculation**. Normal
intermediate results are `INCOMPLETE_BATCH`; the final cumulative result
must be **144/144, 32 reused / 112 computed**, independently verifiable with
`selection.screen_l1('l1-b', ...)` and its 71 challenger configurations.

Keep a lightweight run record: SHA, run ID, App ID, batch size, worker count,
actual elapsed time, exit code, completed/blocked counts, container OOM/timeout
and original errors, plus a readily available cost overview if unusually high.
**Do not pause between healthy batches for P90 estimates, billing snapshots,
manual ledger booking or fresh budget approval.** Stop if the same fault
recurs, a source SHA changes, a finished cell would be recomputed, builds
repeat unexpectedly or observed spend becomes unexpectedly large. Review
the cause before continuing; no infinite retries.

## 4. Historical incident (P0, already resolved)

The previous P0 image builds hit setuptools-scm / version metadata issues.
The corrected Modal image does **not** install pyqlib from its project root:
it copies sources, sets a temporary image-only `qlib/_version.py`, compiles
the `rolling` and `expanding` Cython modules from `/tmp`, and smoke-imports
Qlib. Both original failed App records and their historical charges should
remain intact. Do not rerun P0 or L1-A just to test image packaging.

## 5. Decision boundary

Passing the two-phase L1-B screen is not sufficient evidence for production
promotion. No L1-C if fewer than two distinct qualifying configurations;
no change to canonical production Winner or website/paper release by this PR.
