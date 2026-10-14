# 59 — CSI1000 L4 five-phase runner, frozen-source reuse, operator runbook

**Status: research PR only. No paid Modal work or production release authorized before code review and merge.**

## Why L4 needs ZERO new model fits

The original frozen Stage-B result contains all 11 real LightGBM
models at all five freq20 phases **0, 4, 6, 10, 15**.
The L4 shortlist uses only three of those models, so **all fifteen**
model-phase frozen signals are already available and source-SHA signed.
L4 therefore checks each original signal/report/decision SHA, verifies
the associated real retrain-chunk model hashes, then runs **20 independent
real F1 fee/NAV replays** (four trading strategies x five phases). Never
retrain, silently substitute, or impute any source. The official Baseline
Top20/Drop2 and the Baseline MODEL Top10/Drop1 challenger share frozen
model predictions but have distinct trades, fees, decisions and NAV.
No automatic USD limits or financial ledgers for L4.

Manifest: docs/experiments/58-csi1000-l4-finalists-frozen-20261014.json

Canonical frozen array SHA256:
b6bd3b3d687d017fb6586d68e88ab71691c2225eb4545e853560a776c9cae1c8

Four configs, in order: official Baseline MODEL + Top20/Drop2;
c98856b4 MODEL + Top10/Drop1; Baseline MODEL + Top10/Drop1;
a419047a MODEL + Top10/Drop1. L2/L3 contribute zero. All share
raw20/freq20 and the original 424 F1 execution days; source Phase0/10
P0 replay must match the original signed P0 daily bench/account/fees,
not merely its headline total return.

Five-phase product gates are those in contract 53 §1.2: worst and median
improvement, breadth of phase beats, MDD degradation, median turnover,
+10bp cost stress, and signed data/ledger/coverage checks; +20bp is
supplementary. All evidence is HISTORICALLY_CONSUMED, not unseen OOS.
No live production Winner change.

## Modal workspace, concurrency and Volume

Use the same Modal workspace/environment as accepted P0 and Stage-B.
Original qlib source Volume and qlib-csi1000-layered-search P0 evidence
Volume are mounted read-only. Create exactly one new dedicated v2 write
Volume; only if absent (verify with volume list):

    modal volume list
    modal volume create --version 2 qlib-csi1000-l4-v2
    modal volume list --json

One F1 replay worker requests 2 CPU/4096 MiB; there are only 20 total
cells. Permit --max-workers 1 through 80, but 16 is the practical
initial complete-run setting. More than 20 cannot speed 20 cells.
No background/detach, no two coordinators using one prefix.

## Free preflight, after independent PR review and merge

    set -euo pipefail
    git switch main
    git pull --ff-only origin main
    git rev-parse HEAD
    test -z "$(git status --porcelain)"
    python -m pytest -q tests/test_csi1000_l4_final.py tests/test_csi1000_layered_search_selection.py
    python -m black -l 120 --check audit/csi1000_l4_core.py audit/csi1000_l4_modal.py tests/test_csi1000_l4_final.py
    python -m pyflakes audit/csi1000_l4_core.py audit/csi1000_l4_modal.py tests/test_csi1000_l4_final.py
    python -m py_compile audit/csi1000_l4_core.py audit/csi1000_l4_modal.py
    modal run audit/csi1000_l4_modal.py --help

Set the actual existing full signed P0 export, which the L3 operator
previously reported at /tmp/pr71-v2/p0evidence; do not invent an export
or mix a different P0 run:

    export L4_RUN_ID=l4_20261014_a1
    export P0_EXPORT=/tmp/pr71-v2/p0evidence
    export P0_PREFIX=csi1000_layered_search/csi1000_layered_search_v1/p0_20261013_c1/p0/cffb9cd56d52
    test -f "$P0_EXPORT/summaries/p0/stage_p0_summary.json"
    test -f "$P0_EXPORT/SHA256SUMS.txt"

## One real two-cell F1 Smoke, ZERO new LightGBM fits

    set -o pipefail
    modal run audit/csi1000_l4_modal.py \
      --mode smoke --run-id "$L4_RUN_ID" \
      --p0-summary "$P0_EXPORT/summaries/p0/stage_p0_summary.json" \
      --p0-prefix "$P0_PREFIX" --max-workers 1 \
      2>&1 | tee /tmp/csi1000-l4-smoke.log

Before executing the two Smoke replays, inventory MUST independently
verify all 15 original model-phase signed Stage-B source signal/report/
decision bytes and the existing original signed P0 Phase0/10 cells.
The Smoke then performs the real F1 replay for the official original
Baseline Top20/Drop2 phase0 and the L1 top challenger Top10/Drop1
phase0. Their output bytes and hashes become reusable finished L4 cells.

PASS: real EXIT=0, L4_SMOKE_PASS 2/2 real F1 replays / 0 new LightGBM
fits; two durable signed 424-day receipt/result sets; P0 baseline
day-by-day NAV, return, cost and bench reconciliation PASS. Record
the Modal App ID and all source/quote SHA. Any conflict => STOP,
do not attempt the full run.

## Continuous full 20-cell five-phase run (after Smoke accepted)

    set -o pipefail
    modal run audit/csi1000_l4_modal.py \
      --mode complete --run-id "$L4_RUN_ID" \
      --p0-summary "$P0_EXPORT/summaries/p0/stage_p0_summary.json" \
      --p0-prefix "$P0_PREFIX" --max-workers 16 \
      2>&1 | tee /tmp/csi1000-l4-complete.log

This revalidates/reuses the two Smoke cells and automatically completes
the remaining 18 F1 replays. Same run ID, frozen manifest/code SHA and
P0 export. No per-wave approval. If the attached foreground tool has a
short timeout, add --max-cell-waves 1, then rerun the identical complete
command after L4_INCOMPLETE_SAFE_PAUSE. Do not treat SAFE_PAUSE as terminal.

Terminal SUCCESS: real EXIT=0, L4_COMPLETE 20/20 verified replay cells
0 new fits, L4_DONE, no traceback, no STOP or BLOCKED, and signed
l4_final_decision.json. The final outcome may be either a qualified
PROPOSE_PRODUCT_PROFILE_VERSION or KEEP_BASELINE_FOR_PRODUCT_SELECTION;
NEITHER deploys or changes production automatically. All seven
original product gates must hold for PROPOSE.

## Submission to PR: exact report and files required

Download the entire new L4 Volume v2 output prefix (rather than only
the final JSON). Check modal volume get --help for your Modal CLI.
The output prefix is:

    csi1000_layered_search/csi1000_layered_search_v1/<L4_RUN_ID>/l4/<MERGED_GIT_HEAD_FIRST12>

For every one of the 20 cell directories, retain receipt.json,
fixed_report.parquet, decisions.json and signal.parquet, and independently
verify every receipt artifact_sha256. Also retain smoke_pass.json,
l4_final_decision.json and both full foreground logs. Submit a PR
comment with the following five sections:

1. Exact code SHA, manifest SHA, run ID, new v2 Volume prefix, all App
   IDs, smoke 2/2, final 20/20, real EXIT codes, and 0 new model fits.
2. Frozen Stage-B snapshot token, provider fingerprint, quote SHA,
   all 15 source model-phase signal/report/decision SHA checks,
   original signed P0 Phase0/10 reports, daily F1 reconciliation.
3. Per each of 20 cells: candidate full ID, config SHA, phase,
   TopK/DropN, source signal hash, independent F1 report/decision hashes,
   424-day coverage, NAV/cost/ledger and benchmark.
4. Per each of three challengers, the **full 5-phase** worst and median
   paired delta pp, count of phases beating Baseline, MDD deterioration,
   median turnover ratio, +10bp stress and +20bp comparison, all seven
   gates and all negative/failed verdicts, then final selection result.
5. SHA256SUMS or independent verifier output, first failure if any,
   exact original control and final l4_final_decision.json, explicit
   HISTORICALLY_CONSUMED and promotion_authorized=false. Stage-B
   production Winner remains unchanged.

A separate, user-approved product/release PR is required for any
production Winner change; L4 research alone cannot promote itself.
