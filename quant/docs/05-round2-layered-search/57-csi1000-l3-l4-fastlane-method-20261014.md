# 57 — CSI1000 L3 execution and L4 handoff (no runtime financial gates)

2026-10-14, PR #77 protocol implementation. This document is an operator runbook, not an authorization to launch Modal or promote a production Winner.

## Frozen science and interpretation

L3 anchor: original Baseline model ID 23b92de05cf36c82998de684d0fbf64d81ee54490d755bd3cf96311c00286785, Top20/Drop2, unchanged Alpha158 and F1 quote/P0 source.

Stage 1 has raw20/freq10 on phases 0/5 and raw20/freq40 on phases 0/20. Stage 2 can run only after at least one Stage 1 coarse PASS: raw10/freq20 and raw40/freq20, both phases 0/10. No extra hyperparameter or portfolio search; every structural retrain calls real LightGBM fit and generates new checkpoints/predictions. Label 10/40 maturity and purge are independently checked; no reuse of raw20 checkpoints. At most one L3 final candidate.

The comparison method is **ordinal phase perturbation**, not same integer phase/model-age equivalence: challenger first and second variants pair to P0 Baseline phases 0 and 10 respectively. Each pair MUST have exactly matching *daily execution dates*, benchmark series, signed quote/source SHA and 424 days. Signal generation retains SIGNAL_ANCHOR (2024-12-31) for the first T+1 execution, trims all earlier dates and excludes the 2026-09-30 terminal signal. Record the ordinal mapping and HISTORICALLY_CONSUMED selection-bias status.

There is **no L3 or L4 USD spending gate** and no billing ledger, 125% fit cost quote, per-wave USD reservation or compulsory billing check. The previously published financial limits for L3/L4/global spending are superseded by the 2026-10-14 contract amendment. The **250 attempted fit L3 cap**, including failures, is retained as a frozen trial-scope/data-provenance limit, NOT a financial limit. L4 retains the originally preregistered six-configuration/30 five-phase-cell limit and global scientific fit scope. Production Winner is frozen until a separate release decision.

## Mandatory high-concurrency storage preparation (Volume v2)

For 80 concurrent training containers, **do not** write their fit receipts
to the previous shared Layered Search research Volume (which can be a v1
Volume). Current Modal documentation cautions that v1 concurrent writers
can conflict; v2 is designed for hundreds of writers to **distinct paths**.
PR #77 mounts the original qlib-csi1000-layered-search at
/p0-evidence as **read-only**, and all new L3 receipts at /results on an
independent volume **qlib-csi1000-l3-v2**. The Modal SDK explicitly
requests version=2 and refuses a version-mismatched existing volume.

Once, before the new L3 smoke, on the SAME Modal workspace/environment
as the accepted P0 source:

    modal volume list
    # If absent, create the dedicated v2 volume exactly once:
    modal volume create --version 2 qlib-csi1000-l3-v2
    modal volume list --json

Verify the volume type, the workspace/environment, and that the old P0
source Volume still exists. Do not create a v1 volume under the v2 name
or copy old P0 into the new write volume. Do not delete the original P0
research Volume. All concurrent fit jobs publish only to unique
run/code/phase/as-of paths with full SHA validation; attempts and stage
markers are written by sequential, single coordinator actions.

## Modal compute and controls

Start from the already successful L2 CPU-based LightGBM fit container: **8 Modal CPUs and 16384 MiB RAM per fit**, timeout 7200 s, zero retries. No GPU: this workload has no benchmark proving GPUs outperform CPU. L3 permits --max-workers 1 through 80, with a maximum of one fit input per requested container, not 80 fit threads per instance. A request for 80 simultaneous workers can require **640 vCPUs / approximately 1.25 TiB RAM** and may exceed real Modal workspace quota or provider I/O throughput. A 16–32 worker fallback may be faster in practice under quotas. User chooses after 1-worker smoke; the system cannot guarantee 80 resources are granted. Only a **single foreground** dispatcher is allowed; never use --detach, nohup, background shell or concurrent coordinators on the same prefix.

Verified internal waves are at most --max-workers fits wide, including 80 when requested. --max-fit-waves N counts completed VERIFIED waves, pauses before the next unfinished wave, outputs L3_INCOMPLETE_SAFE_PAUSE (not L3_COMPLETE), and preserves all SHA-signed receipts for identical run/SHA resume. A stopped or lost fit attempt without a durable receipt is blocked, not automatically retried. No manual approval between healthy waves.

Pre-launch **estimate only**: previous L2 smoke measured roughly USD 0.0267 per fit; approximately 196 planned L3 fits would be near USD 5.2 if runtime matched L2, excluding variable startup/phase/queue costs. Frequencies change training duration, so this is not a spend guarantee and no dispatch logic uses it. Optional final total bill may be noted by operator without modifying code.

## Offline preflight before any paid work

Review and merge PR #77 only AFTER its latest exact HEAD passes all offline gates, then check a clean main SHA and run:

    set -euo pipefail
    git rev-parse HEAD
    test -z "$(git status --porcelain)"
    python -m audit.csi1000_l3_core --inventory
    python -m audit.csi1000_l3_core --dry-run
    python -m pytest -q tests/test_csi1000_l3_runner.py tests/test_csi1000_l3_runtime_guards.py tests/test_csi1000_layered_search_selection.py
    python -m black -l 120 --check audit/csi1000_l3_core.py audit/csi1000_l3_modal.py tests/test_csi1000_l3_runner.py tests/test_csi1000_l3_runtime_guards.py
    python -m pyflakes audit/csi1000_l3_core.py audit/csi1000_l3_modal.py
    python -m py_compile audit/csi1000_l3_core.py audit/csi1000_l3_modal.py
    modal run audit/csi1000_l3_modal.py --help

Obtain the *complete previously signed P0 local export*, including the 4 original cells. Do not use L1/L2 as P0 or create an empty substitute. Set the following variables from the verified P0 run:

    export L3_RUN_ID=l3_20261014_a1
    export P0_EXPORT=/absolute/path/to/complete-signed-P0-export
    export P0_PREFIX=csi1000_layered_search/csi1000_layered_search_v1/p0_20261013_c1/p0/cffb9cd56d52
    test -f "$P0_EXPORT/summaries/p0/stage_p0_summary.json"

## One genuine two-fit smoke

    set -o pipefail
    modal run audit/csi1000_l3_modal.py \
      --mode smoke --run-id "$L3_RUN_ID" \
      --p0-summary "$P0_EXPORT/summaries/p0/stage_p0_summary.json" \
      --p0-prefix "$P0_PREFIX" \
      --max-workers 1 \
      2>&1 | tee /tmp/csi1000-l3-smoke.log

Require true EXIT 0 and L3_SMOKE_PASS 2/2 actual new fits. These are two *distinct* real model fits: one raw20/freq10 phase0 initial slice and one raw20/freq40 phase0 initial slice. Verify checkpoint/prediction/source/quote/PIT/receipt SHA, no OOM/timeout/collision, clean worktree and recorded App ID. Smoke fits belong to the final roster and are byte-verified on resume, not re-fit.

## Continuous L3 execution after healthy smoke

    set -o pipefail
    modal run audit/csi1000_l3_modal.py \
      --mode complete --run-id "$L3_RUN_ID" \
      --p0-summary "$P0_EXPORT/summaries/p0/stage_p0_summary.json" \
      --p0-prefix "$P0_PREFIX" \
      --max-workers 80 \
      2>&1 | tee /tmp/csi1000-l3-complete.log

With permitted workspace capacity, this runs stage1, scores both frequency variants vs source P0 in the frozen 424-day window, and ONLY IF a stage1 coarse PASS runs stage2 automatically and produces the terminal one-or-zero L3 nomination. It uses distinct immutable l3_screen_stage1.json, l3_screen_final.json and l3_decision.json. Failed stage1 yields a legitimate 0-nominee terminal result. It does not require another approval to progress from stage1 to stage2.

If frontend cannot keep the dispatcher attached long enough, add --max-fit-waves 1 or 2 to the complete invocation and repeat the SAME command with the SAME SHA, run ID and P0 prefix after each L3_INCOMPLETE_SAFE_PAUSE. One wave has up to 80 fits with --max-workers 80. A healthy foreground run with no --max-fit-waves is preferred; no detach. If 80 is unavailable, STOP cleanly, check quota and choose a validated smaller worker count. Do not run overlapping dispatchers.

## Deliverables and L4 boundary

Report all true unique training attempts (including failures), all retrain/phase receipt SHAs, true status L3_COMPLETE plus L3_DONE only at fully finished terminal state, 424 daily reports for every screened variant, paired source P0 NAV/benchmark hashes, all stage1/stage2 improvement and discard labels, earliest stop reason, 0–1 final L3 candidate with full config SHA, and HISTORICALLY_CONSUMED. Product Winner untouched.

**L4 five-phase Modal runner is NOT part of PR #77 and has not been executed or approved.** After independent L3 review, invoke the existing offline freeze_finalists routine once to choose up to five challengers plus the original official Baseline from L1 Top3, L2 0, L3 0–1. A future separately reviewed L4 runner must use five fixed phases per frequency, the same source and identity gates, **no USD runtime control**, and no production promotion without another explicit release PR.
