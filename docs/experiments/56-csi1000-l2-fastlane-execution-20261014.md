# 56 — CSI1000 L2 fast-lane: one smoke, then bounded automatic continuation (2026-10-14)

Status: **design + deterministic offline planning PR; not a reviewed training runner and not paid-run GO.**

The goal is to avoid L1-style human permission requests after every successful small
batch. L2 actually calls LightGBM fit(), so smoke and batch semantics cannot be
copied blindly from L1 frozen-signal replay. Preserve contract 53 §4 candidate
space, research separation and all pre-registered product gates.

## Frozen inputs and permitted candidate scope

- Use \`python -m audit.csi1000_l2_fastlane --repo-root . --output <new-plan-path>\`
  **only for a PLAN**, never as a
  training runner. It delegates to the already-implemented
  \`stage_a_proposals(".")\`, which checks the original Stage-A 80 and Stage-B
  11 identities. Freeze seed **20261012**, source file SHA256 and plan SHA256
  before the first paid fit.
- **Four** diverse Stage-A models from the 69 not in Stage-B; **four** deterministic
  *new* proposals limited to the original seven LightGBM hyperparameters.
  Alpha158, raw20 label, freq20 retrain, seed, costs, fixed F1 quote path,
  PIT universe and common 424-session execution window remain unchanged.
- Four new proposals x four original independently purged Stage-A folds =
  **16 genuinely new fit calls including the short-fold smoke**. Record fold
  train/valid/test as-of, embargo/label maturity, checkpoint/model config SHA,
  prediction SHA, fees, cost and all four-fold robustness measures. Original
  Stage-A model folds are *screening evidence only*; none are a 424-session
  training/checkpoint substitute.
- Recompute all four new proposals' fold results; rank using worst, Q25,
  median and leave-one-fold-out evidence relative to same-fold frozen Baseline.
  Select **0–2** new proposals for long validation only if the pre-reviewed
  hard quality/coverage rule is met. Freeze the selection rule **before running
  the short-fold jobs**. No retrospective change to thresholds or proposals.
- Long validation is four existing Stage-A proposals plus at most two
  short-fold-qualified new proposals; **up to six models x phases [0,10]**.
  Fixed Top20/Drop2 is the *model-only* test to avoid mixing L1's Top10/Drop1
  portfolio selection into an independently pre-registered L2 model search.
  Require full rolling retraining, signal/NAV/fee provenance and 424-session
  phase-paired P0 Baseline. Model/fold/phase checkpoint and frozen predictions
  are never borrowed across distinct configs or schedules.
- Before the first long retrain, call
  \`make_long_job_roster(plan, verified_calendar, qualified_new_ids)\` and
  independently confirm the full planned calendar. **16 short-fold fits plus
  all long fits <=320, including the two smoke jobs (not additional fits)**.
  The runtime fit counter MUST include failures, cancellations and any retry.
  A missed checkpoint may only be recomputed if enough remaining budget is
  available and the previous completion is definitively not durable.

## Execution: two operator invocations, not seven manual authorizations

1. **Preflight (no paid Modal work)**: review, merge, test *actual* L2
   worker code; independently verify frozen input SHA, same live data/provider,
   F1 pricing/execution, Alpha158/raw20 purge and Label+20 maturity; pre-register
   candidate/config SHA, selection rule, fit & USD caps, research Volume only,
   operator-owned foreground timeout. Existing Stage-A/Stage-B scripts write to
   the legacy source Volume and the historic Stage-B worker assumes its own
   frozen 11-model roster and retries=2; do **not** directly dispatch either as
   the L2 runner or enable hidden extra fits.
2. **One paid L2 smoke invocation** containing **two distinct training paths**
   in sequence: one *new* proposal x one purged short fold and one
   *existing* L2 model x one first long-phase0 retrain job. 1 worker,
   zero automatic retries, each under its reviewed memory/CPU/timeout limit.
   Verify generated model checkpoint/prediction/report SHA, matured labels,
   real fit receipt, as-of timestamp, read-only source and durable write in
   isolated research Volume. These two valid cells become ordinary jobs in
   the eventual roster, not disposable duplicate fits. The smoke gate is PASS
   only if both paths are validated and the true CLI exit code is zero.
3. **One full-run, single attached coordinator after smoke PASS**:
   resume/cross-verify the two smoke artifacts; automatically execute all
   remaining new proposal folds, finalize their four-fold ranking and eligible
   0–2 shortlist, freeze actual long job roster and calendar fit budget, then
   process all long retrains and both portfolio phases (where qualified).
   Use **bounded internally checkpointed waves** and monotonic durable
   counters. After a successful wave, the same coordinator progresses without
   another manual PR comment or budget signoff. Start with up to **4 workers**
   only if smoke-measured memory/cost and quota permit; otherwise automatically
   keep 1–2 workers. L2 train worker resources must be independently benchmarked
   (historic Stage-B profile: 8 Modal CPU units, 16 GiB per worker; not the L1
   2-vCPU/4GiB replay profile). Increase beyond 4 only by separate review.
4. **If an entire continuous session cannot fit** the *verified* frontend
   tool timeout, do not launch a long non-detached run and hope for the best.
   Instead use a deliberately limited number of foreground sessions with
   the same code SHA, plan SHA and run ID; the coordinator resumes only
   verified immutable artifacts. This is logistical splitting, not a
   repeated manual-approval process. No \`--detach\`, \`nohup\`, \`&\` or two
   coordinators dispatching to the same prefix.
5. **Automatic STOP**, not partial winner selection, if any as-of/purge/PIT,
   signal/checkpoint/provider/quote SHA, 424-day coverage, metrics/NAV,
   train-count, model-identity or billing cap gate fails; also STOP on first
   deterministic fit error, confirmed OOM, unrecoverable timeout or read/write
   collision. No blind retries or silently reduced scope. Publish an
   \`INCOMPLETE\` evidence package with first failing job, log and costs.

## The minimum release gates still apply

- Hard ceilings: **320 new fits total for L2**, **USD 12 L2 actual spend**
  (plus original USD30 global and total-fit/time limits unless explicitly
  amended), at most 6 long challengers/12 long-phase lineages and 2 L2
  finalists. The fit cap counts all failed and repeated fits. Estimate full
  expenditure from real smoke consumption; internally update spent and
  outstanding reservations after each wave, stop when room is insufficient.
  Do not require human P90 spreadsheets, manual cost ledger edits or repeated
  approval between healthy waves. This amends **L2 administration only**,
  not the hard ceilings or evidence provenance obligations.
- Report all four new short-fold results, all existing model identities,
  discarded proposals, L2 long-validation outcomes, original 2-phase
  coarse screen and phase-paired Baseline, every fit receipt, SHA256SUMS,
  operator App IDs, actual billing, warnings/failures, total fit count,
  and the complete scoreboard. Emit \`HISTORICALLY_CONSUMED\`.
- At most two L2 entrants are *provisional*. Freeze final L4 configuration
  manifest once L2/L3 are done or there is a recorded reason to skip them.
  L4 still needs five full phases and seven product gates. No production
  Winner changes, no website/paper promotion and no automatic L4.

## Actual implemented runner and operator instructions

Current code: audit/csi1000_l2_execution.py (immutable receipt/budget and
four-fold shortlist), audit/csi1000_l2_fastlane.py (plan and fit-calendar cap),
audit/csi1000_l2_modal.py (real LightGBM fit and F1 replay), plus two
dedicated synthetic-negative-control test files.

**No paid L2 smoke before PR #75 independent pytest, Black, pyflakes and
runtime code review, clean merge, frozen provider/P0 source and cost quote.**

### Free offline preflight

~~~bash
set -euo pipefail
python -m pytest -q \
  tests/test_csi1000_l2_fastlane.py \
  tests/test_csi1000_l2_execution.py \
  tests/test_csi1000_layered_search_selection.py
python -m black -l 120 --check \
  audit/csi1000_l2_fastlane.py audit/csi1000_l2_execution.py \
  audit/csi1000_l2_modal.py \
  tests/test_csi1000_l2_fastlane.py tests/test_csi1000_l2_execution.py
python -m pyflakes audit/csi1000_l2_fastlane.py \
  audit/csi1000_l2_execution.py audit/csi1000_l2_modal.py \
  tests/test_csi1000_l2_fastlane.py tests/test_csi1000_l2_execution.py
python -m py_compile audit/csi1000_l2_fastlane.py \
  audit/csi1000_l2_execution.py audit/csi1000_l2_modal.py
python -m audit.csi1000_l2_fastlane \
  --repo-root . --output /tmp/csi1000_l2_frozen_plan.json
modal run audit/csi1000_l2_modal.py --help
git status --porcelain
git rev-parse HEAD
~~~

The P0 export root must contain summaries/p0/stage_p0_summary.json and
the full signed cell artifacts. Compute the original P0 prefix from that
summary's real run_id and code_sha, not the L1-C prefix. Freeze plan SHA,
Stage-A 80/Stage-B 11 input SHA and exact merged code SHA.

### One actual paid Smoke — two reusable fits

~~~bash
export L2_RUN_ID=l2_20261014_a1
export P0_EXPORT=/absolute/path/to/full-signed-p0-export
export P0_PREFIX='csi1000_layered_search/csi1000_layered_search_v1/<real-P0-run-id>/p0/<P0-code-SHA-first12>'
# Choose a conservative value using live modal billing rates and
# known Stage-A/B CPU+RAM seconds. Do not copy this as an empty string.
export L2_USD_PER_FIT_RESERVE='<conservative-USD-per-fit-attempt>'
set -o pipefail
modal run audit/csi1000_l2_modal.py \
  --mode smoke \
  --run-id "$L2_RUN_ID" \
  --p0-summary "$P0_EXPORT/summaries/p0/stage_p0_summary.json" \
  --p0-prefix "$P0_PREFIX" \
  --max-workers 1 \
  --usd-per-fit-reserve "$L2_USD_PER_FIT_RESERVE" \
  2>&1 | tee /tmp/csi1000-l2-smoke.log
~~~

Success requires genuine exit 0 and L2_SMOKE_PASS, with one new
candidate's purged short fold and one existing candidate's first
phase-0 long retrain, both SHA-verified, durable and counted toward the
complete roster. Record App ID, exact startup/build/worker health, real
billing and source/provider fingerprint; STOP on any mismatch or failure.

### One foreground automatic full-run invocation

~~~bash
# Use actual billed usage from the smoke App, never an assumed price.
export L2_SMOKE_ACTUAL_USD='<real-smoke-App-bill-in-USD>'
set -o pipefail
modal run audit/csi1000_l2_modal.py \
  --mode complete \
  --run-id "$L2_RUN_ID" \
  --p0-summary "$P0_EXPORT/summaries/p0/stage_p0_summary.json" \
  --p0-prefix "$P0_PREFIX" \
  --max-workers 4 \
  --usd-per-fit-reserve "$L2_USD_PER_FIT_RESERVE" \
  --smoke-actual-usd "$L2_SMOKE_ACTUAL_USD" \
  --nonfit-reserve-usd 0.5 \
  2>&1 | tee /tmp/csi1000-l2-complete.log
~~~

This same single foreground coordinator:

1. Revalidates and reuses the two Smoke fit receipts without retraining.
2. Completes the remaining 15 new-model short-fold fits, internally in
   verified waves of at most four, without manual signoffs.
3. Independently validates the 16/16 full-fold shortlist, freezes a
   maximum of two new candidates, then computes the true long-training
   calendar for four existing plus eligible new candidates.
4. Prechecks actual proposed fit count and conservative reserved spend
   against 320 new fits (including failures/retries) and USD12 L2 layer
   ceiling; if either is inadequate, STOP, never silently trim candidates.
5. Completes all permitted Phase0/10 rolling retrain fit jobs, stores
   checkpoint/prediction SHA, constructs each 424-day full signal,
   backtests fixed F1 Top20/Drop2, verifies same-phase P0 NAV/benchmark,
   and produces the full ranked summary. All successful internal waves
   continue without additional human authorization.

A successful final log may say L2_COMPLETE but still marks
bill_is_operator_verified=false. The built-in spending check is a
*conservative forward reservation* (current operator-supplied per-fit
cost + at least USD0.50 non-fit overhead), **NOT a live-billing API**.
After completion, reconcile all Modal charges and all failed attempts
before independent L2 acceptance. A workspace-level spend cap may be
configured separately when authorized; do not change existing workspace
settings without approval.

**Foreground only:** do not use --detach, nohup, shell &, or concurrent
coordinators. Require the OpenCode frontend timeout to cover the complete
expected build/queue/fit/replay/audit wall time. If impossible, use the
fewest feasible additional attached --mode complete invocations at the
same run/SHA/prefix. Resumption only accepts exact byte-verified job
receipts; an attempted-but-failed fit is never retried without an explicit
failure investigation.

### Final L2 evidence review

Require: all 16 new short-fold results, 0–2 independently selected new
models, all expected real long fit receipts (including 1 from Smoke),
the two pre-registered phases per candidate, all file SHA,
source/provider and F1 quote identity, 424-day same-phase P0 pairing,
0 blocked, actual fit-attempt count and actual paid spend <=USD12.
Mark all observations HISTORICALLY_CONSUMED. L2 qualifies at most two
candidates; L3, L4 and any production Winner release need separate
decisions. No paid activity has been performed by this PR.
