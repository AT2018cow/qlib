# 16 - CSI1000 Stage-A LightGBM Tuning Contract (2026-10-07)

## 0. Scope

This document narrows the next-stage implementation to **CSI1000 model tuning only**.

The current frozen production-aligned dimensions remain:

```text
universe           CSI1000
features           Alpha158
target             raw 20-trading-day forward return
retrain frequency  20 sessions
portfolio          top20 / n_drop=2
tie-break           score desc / instrument asc
signal timing      T close
execution          T+1 open
account semantics  canonical
price limits       board/date aware
metrics            portfolio_compound_v1
```

Stage A changes only a bounded LightGBM parameter subset.

The corrected implementation uses protocol `csi1000_lgb_stage_a_v2`. Any artifact
created under the merged PR #15 `v1` implementation is superseded because v1 used a
60-session one-fit fold and the pre-fix ranking/reproducibility contract.


PR #17 does **not** change the Stage-A model/backtest protocol. It upgrades only the
baseline reproducibility gate to `baseline_double_fit_4fold_v2`: model configuration,
best iteration, and prediction signal remain exact-match requirements, while raw
backtest reports are compared by exact structure, tight numeric tolerance, and
recomputed canonical metrics. Raw report content hashes remain recorded audit evidence
but are no longer required to be bitwise identical.

ChiNext rescue and STAR reproducibility repair remain valid later workstreams, but they
are intentionally outside this implementation.

---

## 1. Selection rule is fixed before compute

Candidate selection is lexicographic rather than a hand-tuned scalar score.

For the cheap fold screen, higher is better in this order:

1. worst-fold relative excess CAGR;
2. q25 relative excess CAGR across folds;
3. median relative excess CAGR;
4. positive-fold ratio;
5. median information ratio;
6. median Sharpe;
7. worst strategy MaxDD;
8. lower median turnover;
9. lower median total cost.

Worst-fold comes first because with only four folds a linearly interpolated q25 can
still be positive when one fold is catastrophic. This ordering prevents three strong
folds from masking one severe failure.

Equal ranking vectors are broken deterministically by candidate content hash.

A single best fold is never a winner rule.

For final candidate ranking, the fixed CSI1000 phase set

```text
0, 4, 6, 10, 15
```

is evaluated first using the same lower-tail-first logic. Temporal-fold statistics then
act as the secondary ranking vector.

A historically best phase is never selected for production.

---

## 2. Stage-A cheap folds

The smoke/expanded screen is a **proxy screen**, not a final portfolio backtest.

Four independent folds are spread deterministically from 2021 through the end of 2024.

Each fold:

- uses expanding training history from 2016;
- uses a trailing 252-session validation block;
- enforces the 20-session label-maturity purge at train -> validation and
  validation -> signal boundaries;
- fits one model;
- evaluates exactly 20 execution sessions, matching one production freq20 model lifespan;
- uses the canonical CSI1000 T-close -> T+1-open backtest;
- starts an independent account for that fold.

Fold NAVs must **not** be concatenated into a headline CAGR.

The recent tail after the 2024 screening cutoff is retained outside the cheap fold
screen. The purpose is to avoid using every recent session while generating/ranking
the large candidate pool.

Top candidates must still pass the full rolling freq20 evaluation.

---

## 3. Tunable LightGBM subset

Stage A tunes only:

```text
learning_rate
colsample_bytree
lambda_l1
lambda_l2
max_depth
num_leaves
min_data_in_leaf
```

The following are not Stage-A search axes:

- target;
- Alpha158 feature set;
- retraining frequency;
- TopK / n_drop;
- execution convention;
- row bagging;
- deterministic seeds;
- portfolio-cost assumptions.

The existing deterministic controls remain enforced after each candidate overlay.

---

## 4. Smoke screen

The first run is frozen at 12 representative candidates.

It includes the current baseline explicitly and spans:

- lower tree complexity;
- stronger minimum-leaf regularization;
- lower learning rates;
- feature fractions from 0.7 to 1.0;
- regularization from near-zero through the current high-L1/high-L2 baseline.

Result grid:

```text
12 candidates x 4 folds = 48 candidate-fold results
```

Before the normal screen, the frozen baseline is independently fit twice on all four
folds. The second repeat becomes the retained canonical baseline, so the first smoke
run has the following worst-case compute envelope:

```text
baseline reproducibility gate  4 folds x 2 repeats = 8 fits
remaining 11 candidates        11 x 4             = 44 fits
maximum new fits                                   = 52
```

This is intentionally small enough to validate:

- fold chronology;
- purge behavior;
- deterministic model config;
- canonical metrics;
- T+1-open execution;
- artifact retention;
- turnover/cost extraction;
- resumability;
- exact baseline double-fit model/prediction reproducibility;
- exact baseline trade-decision/order reproducibility;
- semantic baseline report reproducibility with retained A/B diagnostics.

Do not expand the search if the smoke artifacts fail any of these checks.

---

## 5. Expanded Stage-A screen

Only after the smoke run is accepted:

```text
default target: 80 candidates
hard cap:       100 candidates
folds:          4
```

The candidate set is deterministic:

- all 12 smoke candidates are retained;
- additional candidates come from a bounded registered domain;
- generation uses a fixed seed;
- duplicates are rejected;
- invalid `num_leaves > 2**max_depth` combinations are rejected.

This is a controlled screen, not an unconstrained optimizer.

---

## 6. Promotion to full rolling evaluation

The fold screen promotes the robust top candidates, while always retaining the frozen
baseline as a control.

Initial promotion target:

```text
top 8 including baseline
```

The next implementation step for those candidates is:

```text
full production-aligned freq20 rolling evaluation
-> continuous account
-> retained prediction/signal/report lineage
-> fixed phase robustness 0/4/6/10/15
-> final lower-tail-first ranking
```

Only after that should a candidate be considered stronger than the corrected baseline.

---

## 7. New implementation files

```text
csi1000_tuner_core.py
csi1000_tuner.py
tests/test_csi1000_tuner_core.py
```

`csi1000_tuner_core.py` contains no Qlib or Modal imports. It freezes:

- protocol identity;
- smoke and expanded candidate generation;
- fold construction;
- metric validation;
- candidate summaries;
- deterministic ranking;
- Stage-B promotion.

`csi1000_tuner.py` is the Modal execution layer. It:

- creates one provider snapshot for the run;
- requires a baseline 4-fold double-fit reproducibility gate before candidate ranking;
- uses `DeterministicTopkDropoutStrategy` with explicit score/instrument ordering;
- preserves repeat A under `_repro/baseline_double_fit_4fold_v3/repeat_a/` while
  repeat B remains the canonical baseline artifact;
- records `decisions.json` for every candidate-fold and requires the baseline A/B
  decision content hash to match exactly;
- requires report index/columns/dtypes to match exactly, numeric values to satisfy
  `rtol=1e-10` and `atol=1e-12` (with account normalized by initial cash), and
  recomputed canonical metrics plus turnover/cost to match;
- fingerprints provider/config/runtime/source lineage;
- runs candidate-fold jobs;
- saves signal/report Parquet artifacts and hashes;
- validates exact account consistency;
- reuses valid candidate-fold artifacts on rerun only after independently recomputing
  canonical metrics and turnover/cost from the hash-verified raw report;
- exports a local JSON summary for review.

It does not import or modify `freq_experiment.py`.

---

## 8. Intended commands

Baseline reproducibility preflight only:

```bash
modal run csi1000_tuner.py --preflight-only
```

This performs only the baseline 4 folds x 2 independent fits (8 fits). It does not
launch the other 11 smoke candidates. By default the runner now reuses the committed
provider snapshot when one is already present; pass `--force-data` only when an
intentional provider refresh is desired.

Smoke screen after the preflight is accepted:

```bash
modal run csi1000_tuner.py
```

Expanded screen after smoke acceptance:

```bash
modal run csi1000_tuner.py --expanded --candidate-count 80
```

A rerun on the same snapshot may reuse valid artifacts. A changed provider/source/config
identity produces a different snapshot token and therefore a separate artifact lineage.

---

## 9. Stop conditions before expansion

Do not expand beyond the 12-candidate smoke if any of the following occurs:

- fold chronology or purge assertion fails;
- baseline double-fit model config, best iteration, prediction signal, or decision/order hashes do not match exactly;
- baseline A/B report structure, numeric tolerance, canonical metrics, turnover, or cost comparison fails;
- canonical account consistency is not exact within tolerance;
- a signal/report hash cannot be independently reloaded;
- turnover or cost is missing;
- results materially depend on rerun order;
- the cheap fold ranking is dominated by an obvious execution or artifact bug.

The first objective is a trustworthy model-selection mechanism, not a large parameter
count.
