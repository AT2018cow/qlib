# 21 - Next Conversation Handoff: STAR + ChiNext (2026-10-08)

## 0. Purpose

This document is the starting point for the next conversation.
The next research objective is **STAR (科创板) + ChiNext (创业板)**.
CSI1000 is already frozen and should be treated as a completed selection problem unless a concrete implementation or data-integrity defect is discovered.

Reviewed against repository `main` at:

```text
1eaebfebe98a9a72ffebc3196dd7e40826639467
Merge PR #31: fix GitHub Pages CSS packaging and guard deployed assets
```

The documentation-hygiene PR that adds this file changes documentation/public copy only; it does not change model, research, cron, or execution code.

---

## 1. Current production state

### CSI1000

- Current canonical model: frozen Stage-B winner.
- Stage A: 80-candidate expansion; current winner entered Stage B from Stage-A rank 5.
- Stage B: 11 candidates × 5 retraining phases (`0 / 4 / 6 / 10 / 15`).
- Winner finished Stage-B rank 1.
- Winner Stage-B relative excess CAGR:
  - worst: **+9.16%**
  - median: **+14.25%**
  - positive phases: **5 / 5**
  - median IR: **0.641**
  - median Sharpe: **1.276**
  - worst strategy MaxDD: **-14.65%**

Frozen production protocol:

```text
features             Alpha158
model                LightGBM
target               raw 20-session forward return
train start          2016-01-01
validation           252 sessions
purge                20 sessions
retrain              every 20 sessions
retrain origin       2026-09-18
signal               T close
execution            T+1 open
portfolio            Top20 / Drop2
tie break            score desc, instrument asc
price limits         board/date aware
high-open overlay    disabled
```

Exact winner parameters and research identifiers remain frozen in `csi1000_production_config.py` and docs 19/20.

### Production topology

- Scheduled production publishes **CSI1000 only**.
- Old baseline/winner shadow topology is superseded.
- Stage-B winner uses a fresh paper state and does not inherit the pre-promotion baseline account.
- ChiNext daily publication is paused.
- Public website displays CSI1000 only.
- Public website dark responsive layout and Pages CSS packaging were fixed through PRs #29–#31.

Operational caution: this conversation verified repository state and CI, but did **not** independently inspect the user's live Modal workspace after the final merges. Do not claim the deployed app is running unless the next conversation verifies it.

---

## 2. CSI1000 governance: do not reopen

The Stage-B confirmation tail (`2025-01-02` through `2026-09-30`, 424 execution sessions) has been consumed.
It is no longer fresh out-of-sample data.

Do not:

- retune CSI1000 using Stage-B results;
- add a new selection gate because one Stage-B phase looks better/worse;
- switch back to the old baseline because of short forward noise;
- describe the frozen winner as a global optimum or guaranteed future CAGR.

Acceptable reasons to reopen CSI1000 are limited to concrete implementation/data-integrity defects that invalidate the frozen evidence.

---

## 3. ChiNext current evidence

Current corrected status comes from the reproducibility/canonical audit, not the older Batch A/B/C results.

- Reproducibility gate: **PASS**.
- Frozen configuration used for the corrected screen: top20 / nd3.
- Sampled retraining phases: `0 / 5 / 10 / 15`.
- Relative CAGR by phase:
  - phase 0: **-1.00%**
  - phase 5: **-3.72%**
  - phase 10: **-8.99%**
  - phase 15: **-10.82%**
- Therefore the old frozen ChiNext model is **not** a current production candidate.
- Old +11% level Batch C / rolling results are historical and must not be used as current evidence.
- Scheduled daily publication and website display are paused.

Interpretation: ChiNext infrastructure is usable, but the old model is weak under the corrected protocol.
The next conversation may develop a **new ChiNext model**, but it must use a new, versioned research contract rather than silently modifying the old frozen line.

---

## 4. STAR current evidence

STAR must be handled differently from ChiNext.

- Corrected prediction reproducibility gate: **FAIL**.
- Failure occurs at prediction-chunk equality / deterministic lineage validation.
- Therefore current STAR alpha is **unknown**.
- Do not interpret earlier negative STAR runs as proof that STAR has no alpha.
- Do not begin model selection or profitability ranking until reproducibility is repaired.

Priority order for STAR:

1. reproduce the corrected failure;
2. localize whether the mismatch comes from data lineage, feature construction, universe membership, model nondeterminism, or chunk aggregation;
3. make identical reruns produce identical prediction/signals;
4. freeze a STAR research snapshot/manifest;
5. only then evaluate models/portfolios.

---

## 5. Shared execution rules for STAR / ChiNext research

New work should inherit the corrected execution/audit framework unless there is a separately documented reason to change it:

- T-close scoring -> T+1-open execution;
- point-in-time train/valid/test boundaries;
- forward-label maturity / purge enforced;
- deterministic LightGBM or equivalent reproducibility controls;
- deterministic ranking tie-break;
- board/date-aware price limits;
- listing-day exemptions handled from historical membership/listing information;
- continuous account and canonical portfolio metrics;
- costs applied consistently;
- no post-hoc use of unavailable execution-day information;
- research publication is not automatically production approval.

Board-specific implementation already exists in `board_rules.py` / `board_execution.py`; inspect and test it before adding new logic.

---

## 6. Data-snooping boundary for the next stage

Much of the history through `2026-09-30` has already been inspected in earlier STAR/ChiNext work.
Therefore it cannot honestly become a brand-new untouched holdout simply by changing the model.

For the next model generation:

1. explicitly version the research protocol before broad search;
2. separate development/model-selection folds from any future confirmation period;
3. record which historical periods/candidates have already influenced design decisions;
4. do not call a re-used historical period fresh OOS;
5. reserve future forward data for the strongest confirmation when enough sessions accumulate.

A new model can still be researched on historical data, but its evidence must be described as development / cross-validation evidence until a genuinely untouched confirmation sample exists.

---

## 7. Recommended next-conversation work order

### A. First: repository and runtime sanity

- Read this doc and `14-pre-tuner-stage-summary-20261007.md`.
- Verify current `main` and current tests.
- Verify the production CSI1000 code remains untouched.
- If any Modal execution is planned, verify the authorized runtime environment without writing environment-specific identifiers into the repository.

### B. STAR: repair reproducibility before alpha research

- Re-run the corrected STAR reproducibility gate on a frozen data snapshot.
- Compare prediction chunks, full-signal hash, universe membership and model/runtime manifest.
- Add the smallest regression test that reproduces the mismatch.
- Repair only the identified determinism/data-lineage defect.
- Re-run the gate twice independently.
- Do not tune STAR until it passes.

### C. ChiNext: define a new model hypothesis

- Treat the old top20/nd3 LightGBM line as a historical control, not a production candidate.
- Decide what is actually being changed: model class, label, features, weighting, training window, or portfolio construction.
- Keep the search space bounded and pre-declared.
- Use the corrected execution/metric stack from the start.
- Include the old frozen ChiNext model only as a control where useful.

### D. After both lines are reproducible

- Build pool-specific candidate screens.
- Prefer robustness across calendar phases / time folds over one best single backtest.
- Freeze the selection rule before final comparison.
- Only after a model passes its own validation contract should production/web re-enablement be discussed.

---

## 8. Public website / documentation state

Current public website requirements:

- CSI1000 only;
- dark responsive desktop/mobile layout;
- historical artifacts remain read-only;
- ChiNext marked paused;
- public methodology describes research evidence and risks, not private environment details;
- internal audit identifiers are kept in research/source manifests rather than displayed on the public page.

GitHub Pages packaging must include HTML, JS and CSS. PR #31 added a guard after an earlier deployment omitted the stylesheet.

---

## 9. Repository hygiene

This is a public repository.

Do not commit:

- access credentials or credential values;
- private workspace/profile names;
- account balances or billing details;
- developer-specific filesystem paths;
- private contact details;
- screenshots/logs containing the above.

Use generic terms such as “authorized production environment” and “research environment” in public docs.

---

## 10. Key files for the next conversation

```text
docs/experiments/21-next-conversation-handoff-star-chinext-20261008.md  <- start here
docs/experiments/14-pre-tuner-stage-summary-20261007.md                 <- corrected STAR/ChiNext evidence
docs/experiments/12-pre-tuner-audit.md                                 <- reproducibility/audit base
docs/experiments/16-csi1000-stage-a-tuning-contract-20261007.md        <- selection-contract example
docs/experiments/19-csi1000-stage-b-final-audit-forward-contract-20261008.md
docs/experiments/20-csi1000-stage-b-winner-canonical-promotion-20261008.md
board_rules.py
board_execution.py
satellite_audit_core.py
satellite_pre_tuner_audit.py
freq_experiment.py
modal_qlib_cn_a10g.py
tests/
```

## 11. Final handoff statement

CSI1000 research selection is complete and frozen.
The next conversation should spend its research budget on:

1. **STAR reproducibility repair first**;
2. **a newly versioned ChiNext model-development contract second**;
3. only then candidate comparison and possible future production re-entry.

Do not use the next conversation to re-optimize CSI1000 against already-consumed Stage-B evidence.
