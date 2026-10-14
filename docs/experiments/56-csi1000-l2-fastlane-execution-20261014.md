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

## Current implementational status / exact safe commands

Implemented **today**: \`audit/csi1000_l2_fastlane.py\` (read-only planning,
full 16 short-fold roster and 320-fit preflight) and
\`tests/test_csi1000_l2_fastlane.py\` (synthetic negative controls).

**Not yet implemented**: an isolated real-fit L2 Modal runner, short-fold
qualification parser, run-scoped immutable checkpointing/actual-receipt
verification, live billing sensor, and a reviewed foreground auto-coordinator.
Consequently there is **no valid \`modal run --stage l2\` command yet**.

After the runner is implemented and independently reviewed, the operator run
plan will require just:
- One genuine short+long smoke with one worker.
- One \`--resume --through-completion\` foreground invocation with runtime
  internal waves and fail-closed budget check (actual flag names will come
  from that new runner's \`--help\` and tests, not this aspirational syntax).

This document is the approved *workflow intent to review*, not an approval
to pay or a claim that any new training is done. Do not use L1-C results or
historically observed return numbers to alter L2 Stage-A proposal formation.
