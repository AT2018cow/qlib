# 15 - Next-Conversation Work Handoff (2026-10-07)

## 0. Purpose

This document is the **startup handoff for the next ChatGPT conversation**.

Repository:

```text
https://github.com/AT2018cow/qlib
```

Authoritative main at handoff:

```text
fad5e05b67818e61ffd503f8314a3e6043478f8f
Merge PR #14: sync project documentation to current pre-tuner baseline
```

PR #14 is already merged. It synchronized the project-level docs, `AGENTS.md`, and
website methodology with the corrected pre-tuner evidence. Do not spend the next
conversation repeating that documentation cleanup.

For current research conclusions:

1. read this document first;
2. then read `14-pre-tuner-stage-summary-20261007.md`;
3. use `12-pre-tuner-audit.md` for detailed audit/reproducibility protocol;
4. consult docs 10/11 only for execution-attribution and metric definitions.

Older handoffs/final-audit docs remain historical provenance and are no longer the
research authority.

---

## 1. Project goal

The practical goal is to build a stock-selection system that has a credible chance of
producing robust future profits.

The project should therefore optimize for:

- out-of-sample stock-ranking quality;
- robust portfolio performance after cost;
- stability across time, retraining calendars, and universes;
- production-correct execution;
- reproducibility.

It should **not** optimize for:

- the largest historical headline CAGR;
- one lucky retraining phase;
- legacy execution assumptions;
- completing expensive audits that do not change a production decision.

---

## 2. Current canonical research protocol

The corrected baseline is:

```text
features/model        Alpha158 + LightGBM
label                 20-trading-day forward return
retrain frequency     20 trading sessions
signal timing         T close
execution             T+1 open
account               continuous; no window reset
portfolio             TopkDropoutStrategy
price limits          board/date aware
price tick            statutory 0.01 CNY on reconstructed original-price scale
5% high-open overlay  disabled in canonical research
metrics               canonical geometric portfolio metrics
```

Canonical portfolio metrics come from `portfolio_performance.py`:

- strategy net return = `return - cost`;
- strategy NAV uses the true after-cost account path when available;
- CAGR is geometric;
- relative excess CAGR = CAGR(strategy NAV / benchmark NAV);
- MaxDD is computed on the true compounded NAV;
- Sharpe uses after-cost strategy returns;
- IR uses after-cost active returns;
- `account_return_max_error` is required as an account-consistency check.

The corrected execution convention is:

```text
T close score -> T+1 open execution
```

Do not reintroduce the legacy same-day-close execution oracle to recover old returns.

---

## 3. Reproducibility contract

LightGBM uses explicit deterministic controls:

```text
seed                  = 0
data_random_seed      = 1
feature_fraction_seed = 2
bagging_seed          = 3
drop_seed             = 4
objective_seed        = 5
extra_seed            = 6
deterministic         = true
force_col_wise        = true
```

The reproducibility manifest records:

- provider prefix fingerprint;
- provider snapshot token;
- model-config SHA;
- runtime versions;
- `freq_experiment.py` source SHA;
- per-retrain prediction hashes;
- full signal hash;
- canonical report-content hash.

The double-fit phase-0 gate requires exact equality of prediction chunks, full signal,
and report content.

Do not casually modify `freq_experiment.py`: its source SHA is part of the gate
identity. If a deliberate new protocol requires changing it, version the protocol and
re-run the relevant gates.

---

## 4. Major work completed during the previous conversation

### 4.1 Correct deterministic CSI1000 phase audit

The trusted CSI1000 phase-0 gate passed exactly.

Authoritative phase-0 metrics:

| Metric | Value |
|---|---:|
| Strategy CAGR | 6.84% |
| Benchmark CAGR | 1.47% |
| Relative excess CAGR | 5.29% |
| Strategy MaxDD | -24.31% |
| Benchmark MaxDD | -46.71% |
| Sharpe | 0.550 |
| IR | 0.158 |
| Annual volatility | 13.43% |
| account_return_max_error | 0 |

The deterministic phase screen eventually covered:

```text
0, 4, 6, 10, 15
```

| Phase | Strategy CAGR | Relative CAGR | Sharpe | Strategy MaxDD |
|---:|---:|---:|---:|---:|
| 0 | 6.84% | 5.29% | 0.55 | -24.31% |
| 4 | 5.69% | 4.16% | 0.43 | -30.77% |
| 6 | 10.55% | 8.95% | 0.81 | -22.44% |
| 10 | 8.92% | 7.34% | 0.70 | -24.56% |
| 15 | 9.98% | 8.39% | 0.74 | -21.77% |

Correct conclusion:

> CSI1000 has material retraining-calendar sensitivity even under deterministic
> training.

Incorrect conclusion to avoid:

> Larger phase number means older model.

Phase is a **calendar offset**, not monotonic model age.

The corrected five-phase relative-CAGR range is approximately:

```text
4.16% .. 8.95%
spread ≈ 4.79 percentage points
```

### 4.2 Incremental phase audit infrastructure

`phase_audit_extend.py` was added so already completed deterministic phases can be
reused and only missing phases need new fits.

A Modal orchestration bug was discovered and fixed:

- cross-app `.remote()`, raw-function, and remote-subprocess attempts caused hydration
  / nested-remote failures;
- the final design uses a **local entrypoint** that launches the canonical
  `freq_experiment.py` app through normal `modal run`;
- Volume helpers only inspect, snapshot, validate, and merge artifacts.

Do not regress to cross-app raw-function execution.

### 4.3 Zero-fit phase attribution

PR #11 added:

```text
phase_attribution_core.py
phase_attribution_diagnostic.py
```

It performs zero model fits.

Main findings for weak CSI1000 phase 4:

- differential transaction costs explain only a minority of the gap;
- median cost explanation is about 6.5%;
- phase 4 turnover is higher overall;
- T+1..T+5 immediately after retraining is **not** a turnover spike;
- 2022-2023 account for roughly 84%-94%+ of the negative pairwise gap.

Therefore:

> The weak phase is primarily a gross signal-path difference, not a cost or immediate
> post-retrain turnover effect.

### 4.4 Zero-fit regime × model-age attribution

PR #12 added:

```text
phase_regime_age_core.py
phase_regime_age_diagnostic.py
```

It tested:

- model age;
- 20-day benchmark trend;
- 20-day annualized volatility;
- prior drawdown;
- composite stress;
- age × regime interactions.

No consistent mechanism explained the 2022-2023 pairwise gap across comparators.

Correct wording:

> Under the tested model-age and preregistered coarse market-regime dimensions, the
> 2022-2023 phase-4 disadvantage remains unresolved.

Do not claim either:

- phase 4 is bad in all conditions; or
- phase 4 is proven non-structural.

### 4.5 Full CSI1000 20-phase grid deliberately deferred

A corrected full 20-phase grid would require roughly another thousand fits.

Decision:

> Do not run it now.

Reason:

- material calendar sensitivity is already established;
- a full grid would refine the distribution but not directly improve alpha;
- compute is better spent on stronger/robust models.

Revisit the full grid for:

- final production-risk certification;
- precise lower-tail calendar-risk estimation;
- direct staggered/multi-phase ensemble decisions.

---

## 5. Satellite-pool audit completed through PR #13

PR #13 added:

```text
satellite_audit_core.py
satellite_pre_tuner_audit.py
```

The fail-closed flow is:

```text
canonical reproducibility gate
  -> strict gate validation
  -> canonical 0/5/10/15 screen
  -> strict screen/gate validation
  -> export results
```

Frozen settings:

```text
ChiNext  top20 / nd3
STAR     top50 / nd2
freq     20
screen   0,5,10,15
```

### 5.1 ChiNext

ChiNext gate PASS:

```text
prediction chunks match = true
signal hash match       = true
report hash match       = true
account consistency     = exact
```

Corrected four-phase screen:

| Phase | Strategy CAGR | Relative CAGR | Sharpe | IR | MaxDD |
|---:|---:|---:|---:|---:|---:|
| 0 | 7.93% | -1.00% | 0.397 | -0.019 | -44.82% |
| 5 | 4.96% | -3.72% | 0.309 | -0.168 | -47.20% |
| 10 | -0.78% | -8.99% | 0.121 | -0.585 | -42.77% |
| 15 | -2.78% | -10.82% | 0.066 | -0.641 | -49.58% |

Four-phase median relative CAGR:

```text
approximately -6.35%
```

Correct conclusion:

> The current frozen Alpha158 + raw-20d + freq20 + top20/nd3 ChiNext baseline is too
> weak for the primary expensive tuner.

Incorrect conclusions:

- ChiNext universe has no alpha;
- ChiNext can never work;
- phase 15 is worse because it is always older than phase 0.

ChiNext remains a **bounded rescue / alternative-target research line**.

### 5.2 STAR

STAR failed the prediction-chunk reproducibility gate.

Therefore:

```text
trusted corrected phase-0 baseline  unavailable
4-phase screen                      not run
corrected alpha conclusion          unavailable
production/tuning eligibility       blocked
```

Correct conclusion:

> STAR is reproducibility unresolved / blocked.

Do not write:

- STAR is unprofitable;
- STAR strategy is not viable;
- legacy STAR rejection is confirmed.

Because CSI1000 and ChiNext passed with the same deterministic LightGBM policy, a
STAR-only failure should first be investigated as a STAR-specific data/order/pool-lineage
problem.

---

## 6. Current three-pool strategy for the next stage

The agreed direction is to **continue all three pools asymmetrically**.

```text
                         shared research framework
                                  |
                 +----------------+----------------+
                 |                |                |
              CSI1000          ChiNext           STAR
                 |                |                |
         full production-      bounded          repair
         aligned tuner         rescue           reproducibility
                 |                |                |
                 +----------------+----------------+
                                  |
                        common robustness review
```

### CSI1000

Role:

```text
primary full tuning line
largest compute budget
```

### ChiNext

Role:

```text
bounded rescue / alternative-target line
small controlled compute budget
```

### STAR

Role:

```text
reproducibility/data-lineage repair
no performance tuning before exact gate PASS
```

Three-pool parallelism does **not** mean:

- one shared optimal model;
- equal budgets;
- one summed CAGR objective;
- equal production readiness.

---

## 7. Recommended next implementation stage

The next conversation should move from:

```text
"Is the baseline trustworthy?"
```

to:

```text
"Which model / target / portfolio choices improve robust out-of-sample selection?"
```

### 7.1 Build one shared multi-pool tuning framework

The framework should support per-market modes:

```text
csi1000:
  mode = full_tuner

chinext:
  mode = bounded_rescue

star:
  mode = blocked_until_repro_gate
```

Do not hard-code three completely separate research stacks.

Shared infrastructure should cover:

- purged temporal folds;
- deterministic model configuration;
- candidate parameter generation;
- saved prediction/signal artifacts;
- canonical portfolio metrics;
- fold-level and phase-level robustness summaries;
- resumable / cost-aware execution;
- market-scoped manifests.

### 7.2 CSI1000 Stage A: LightGBM tuner

Initially freeze:

```text
universe           CSI1000
label              raw 20-day target
retrain frequency  20
portfolio          top20 / nd2
execution          T+1 open
price limits       board/date aware
metrics            canonical
```

Tune only LightGBM parameters first.

Original planning envelope:

```text
roughly 60-100 parameter candidates
x 4-5 cheap purged temporal folds
then robust full rolling evaluation for top candidates
```

Do not automatically commit to the maximum size; implement the framework so the first
run can be a smaller smoke/screen before expanding.

Candidate ranking should include:

- median relative performance across folds;
- q25 / lower-tail behavior;
- worst fold;
- positive-fold ratio;
- Sharpe;
- IR;
- MaxDD;
- turnover / cost;
- selected fixed calendar-phase robustness.

Do **not** select by one phase or one test-period CAGR.

### 7.3 ChiNext bounded rescue

Do not launch the full CSI1000-sized search.

Use a small representative candidate set to answer:

> Is there any credible way to move the current frozen baseline from consistently
> negative relative performance to useful positive evidence?

Possible rescue axes:

- selected LightGBM parameter families;
- benchmark-relative target;
- residual / neutralized target;
- lower-turnover portfolio settings;
- TopK / n_drop changes only after signal quality warrants them.

If a bounded screen remains weak, defer ChiNext rather than brute-force it.

### 7.4 STAR reproducibility repair

Before any alpha search:

1. persist a structured gate-failure artifact;
2. record the first mismatching retrain date;
3. compare repeat-A / repeat-B ordered training indices;
4. fingerprint training rows, validation rows, feature columns, labels, and prediction
   index/order;
5. inspect STAR pool construction / membership ordering;
6. inspect any synthetic-benchmark side effects that may alter dataset preparation;
7. rerun the exact gate.

Only after exact gate PASS:

```text
run STAR 0/5/10/15 screen
then decide whether it enters rescue/full tuning
```

---

## 8. Suggested implementation order in the next conversation

A practical order is:

### Step 1 - repository/status check

Before coding:

```text
confirm main HEAD
confirm no new experiment commits after this handoff
read docs 15 + 14 + 12
read AGENTS.md
```

### Step 2 - design the tuner scoring contract first

Before launching a parameter search, write down and test the exact candidate-ranking
logic.

Avoid building a tuner that searches first and decides later how to rank.

### Step 3 - implement shared multi-pool tuner scaffolding

The first PR should preferably contain infrastructure and tests, not a huge compute run.

It should make the three market modes explicit.

### Step 4 - add STAR structured failure diagnostics

This may be a separate small PR if cleaner.

It should not block CSI1000 tuner development, but STAR must not silently disappear from
the project.

### Step 5 - run a small CSI1000 Stage-A smoke screen

Validate:

- fold chronology;
- no label leakage;
- reproducibility;
- metrics;
- artifact retention;
- cost accounting;
- resumability.

Only then expand the candidate count.

### Step 6 - run ChiNext bounded rescue

Keep the compute cap explicit.

### Step 7 - repair/retest STAR gate

If PASS, run the same 0/5/10/15 robustness screen before any tuner.

---

## 9. Important files for the next conversation

### Canonical research / metrics

```text
freq_experiment.py
portfolio_performance.py
board_rules.py
qlib_live_retrain.py
qlib_audit_fixes.py
```

### Phase/reproducibility infrastructure

```text
phase_audit_extend.py
phase_attribution_core.py
phase_attribution_diagnostic.py
phase_regime_age_core.py
phase_regime_age_diagnostic.py
satellite_audit_core.py
satellite_pre_tuner_audit.py
```

### Current authoritative result artifacts

CSI1000:

```text
results/freq_experiment/results_csi1000.json
results/freq_phase_sensitivity/phase_csi1000_freq20.json
results/freq_phase_attribution/phase_csi1000_freq20_zero_fit.json
results/freq_phase_regime_age/phase_csi1000_freq20_regime_age_zero_fit.json
```

ChiNext:

```text
results/freq_experiment/results_chinext.json
results/freq_phase_sensitivity/phase_chinext_freq20.json
results/satellite_pre_tuner/chinext_freq20_gate_screen.json
```

STAR:

```text
no passing corrected gate/screen artifact at handoff
```

### Current documentation authority

```text
docs/experiments/15-next-conversation-handoff-20261007.md
docs/experiments/14-pre-tuner-stage-summary-20261007.md
docs/experiments/12-pre-tuner-audit.md
docs/experiments/10-execution-attribution.md
docs/experiments/11-portfolio-performance-metrics.md
AGENTS.md
```

---

## 10. Things that are established and should not be reopened without new evidence

Do not casually reopen:

- 20-session maturity purge / chronology guards;
- T-close -> T+1-open research execution;
- continuous account;
- canonical portfolio metrics;
- direction-aware board/date statutory limits;
- statutory CNY tick reconstruction;
- no 5% high-open overlay in canonical research;
- deterministic LightGBM policy;
- CSI1000 exact reproducibility gate;
- ChiNext exact reproducibility gate;
- material CSI1000 calendar-phase sensitivity;
- current frozen ChiNext baseline is weak;
- STAR is blocked until reproducibility is fixed.

---

## 11. Historical results that are superseded

Do not use these as current evidence:

### Legacy 13-14% annualized baseline

Execution attribution showed future-informed execution behavior.

### Old 11%+ CSI1000 / ChiNext headline results

They predate the corrected execution, canonical metrics, and deterministic gate.

### Old CSI1000 20-phase 1.41%-9.33% range

It was not a clean phase-risk estimate because phase 0 did not reproduce the standalone
lineage exactly.

### "Phase number = model age"

False.

### "ChiNext has no alpha"

Not established. Only the current frozen baseline is weak.

### "STAR is unprofitable"

Not established. STAR's corrected alpha is unknown because the gate failed.

---

## 12. Compute discipline

Do not immediately run:

- full 20-phase grids;
- legacy `--tune 200`;
- simultaneous LightGBM × target × TopK × n_drop × phase brute force;
- a large tuner before a smoke-screen validates the framework.

Prefer:

```text
small validated screen
-> inspect artifacts
-> expand only if useful
```

The purpose is to spend compute on decisions that can improve robustness or alpha.

---

## 13. Production/publication state vs research evidence

The repository's cron / website may still publish CSI1000 and ChiNext paper-ranking
feeds.

This is **publication infrastructure**, not a statement that both pools currently have
equivalent research approval.

Keep these concepts separate:

```text
deployed ranking feed
!=
corrected alpha certification
!=
production capital allocation
```

Website/project docs were synchronized in merged PR #14 to make this distinction clear.

---

## 14. What the next assistant should do first

On the first turn of the next conversation:

1. inspect current `main` and confirm there are no newer experiment results;
2. read this handoff, doc 14, doc 12, and `AGENTS.md`;
3. summarize the three market modes back to the user;
4. propose the first implementation PR for the shared production-aligned tuner /
   evaluation framework;
5. keep STAR repair as an explicit parallel workstream;
6. do not launch expensive Modal experiments until the scoring/fold protocol has been
   reviewed.

The next stage begins from:

```text
CSI1000 = primary tuner
ChiNext = bounded rescue
STAR    = reproducibility repair
```

That is the current project boundary.
