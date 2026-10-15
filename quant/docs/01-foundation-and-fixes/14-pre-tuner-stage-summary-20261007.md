# 14 - Pre-Tuner Stage Summary and Next-Stage Baseline (2026-10-07)

> **Current-state override (2026-10-08):** use [21-next-conversation-handoff-star-chinext-20261008.md](21-next-conversation-handoff-star-chinext-20261008.md) for current project status and next-step planning. The corrected STAR/ChiNext evidence in this document remains useful, but the old three-pool parallel plan is no longer current. CSI1000 is frozen as the selected production line; ChiNext daily publication is paused; STAR still requires reproducibility repair before model evaluation.

## 0. Purpose and authority

This document is the stage-boundary summary for the Qlib A-share stock-selection project
immediately before the next model-improvement / tuning phase.

It consolidates:

- the execution, metric, reproducibility, and audit code changes completed through PR #13;
- the corrected CSI1000, ChiNext, and STAR evidence obtained under the current protocol;
- conclusions that are now considered reliable;
- historical conclusions that have been superseded and must not be reused;
- unresolved risks;
- the agreed next-stage direction: **three pools continue in parallel, but asymmetrically**.

Repository state reviewed before this document was written:

```text
main = 4f634a301decba32828a519b167a118876b16269
```

For future work, read this document first. Use the following as technical references when
more detail is needed:

- `10-execution-attribution.md`
- `11-portfolio-performance-metrics.md`
- `12-pre-tuner-audit.md`
- `13-handoff-after-pr8-20261006.md`

Documents 01-09 and the older "final audit" remain historical records. Their performance
numbers may use legacy execution, metric, data, or reproducibility assumptions and are
**not authoritative for the current strategy** unless explicitly revalidated here.

> Research-only. Nothing in this repository is investment advice, and historical
> backtests do not imply future profitability.

---

## 1. Current research / production-aligned baseline

The current baseline is intentionally simple and is not yet the final optimized strategy:

```text
features/model        Alpha158 + LightGBM
label horizon         20 trading sessions
retrain frequency     20 trading sessions
signal timing         T close
execution             T+1 open
account               continuous; no reset between rebalance windows
portfolio             TopkDropoutStrategy
price-limit handling  board/date aware; statutory CNY tick logic
high-open overlay     disabled
```

Core production behavior remains:

```text
T close: rank candidates
T+1 open: rebalance according to latest ranking
hold continuously
rerun daily
retrain on the configured 20-session schedule
```

The three frozen pre-tuner pool configurations are:

| Pool | Frozen baseline | Benchmark | Current interpretation |
|---|---:|---|---|
| CSI1000 | top20 / n_drop=2 | SH000852 price index | validated core baseline |
| ChiNext | top20 / n_drop=3 | SZ399998 project equal-weight benchmark | baseline reproducible but weak |
| STAR | top50 / n_drop=2 | SZ399997 project equal-weight benchmark | reproducibility unresolved |

These are audit baselines, not claims of optimality.

---

## 2. Completed engineering corrections

### 2.1 Execution attribution and removal of legacy oracle behavior

The legacy 13-14% annualized result was decomposed before tuning.

Confirmed issues:

- the old scalar execution path used execution-day `$change`, which contains same-day
  close information;
- symmetric price-limit blocking interacted with that future-informed value;
- the 5% high-open overlay was not a clean T+1-open production protocol and did not
  provide a defensible source of alpha;
- real board/date-aware limits did not explain the historical performance collapse.

Current rule:

> Never try to recover the old 13-14% result by restoring legacy execution behavior.

### 2.2 Canonical portfolio metrics

`portfolio_performance.py` defines the current metric contract:

- strategy daily net return = `return - cost`;
- strategy NAV uses the true after-cost account path when available;
- strategy and benchmark CAGR are geometric;
- relative excess CAGR = CAGR of strategy NAV / benchmark NAV;
- MaxDD is computed from the true compounded NAV including initial NAV = 1;
- Sharpe uses after-cost strategy daily returns;
- IR uses after-cost active daily returns.

Legacy arithmetic annualized excess and arithmetic cumulative-excess drawdown are only
diagnostics and must not be used as the main decision metrics.

### 2.3 Board/date-aware execution and statutory tick handling

The execution audit added / fixed:

- board-aware price-limit rules for main board / ChiNext / STAR / BSE;
- date-aware ChiNext reform behavior;
- listing-session exceptions;
- statutory 0.01-CNY tick calculations using reconstructed original RMB prices via
  Qlib `$factor`;
- aligned research and paper execution semantics;
- retained raw daily reports for independent recomputation.

The CNY tick fix moved CSI1000 only modestly and did not explain the main historical
performance difference.

### 2.4 Deterministic LightGBM and reproducibility manifests

Research and production share explicit LightGBM reproducibility controls:

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

The reproducibility manifest records, among other items:

- provider prefix fingerprint through the evaluation cutoff;
- model-config SHA;
- runtime versions;
- `freq_experiment.py` source SHA;
- provider snapshot token;
- per-retrain prediction SHA;
- full signal SHA;
- canonical report-content SHA.

Reference runtime during the corrected audits:

```text
Python    3.11.12
pyqlib    0.9.8.dev32
LightGBM  4.7.0
NumPy     1.26.4
pandas    2.2.3
```

### 2.5 Double-fit phase-0 gate

`reproducibility_gate_driver` runs phase 0 twice and requires exact equality of:

- every prediction chunk hash;
- concatenated signal hash;
- independent portfolio report canonical content hash;
- provider/runtime/source manifest.

A passing gate is required before a pool may enter phase screening.

### 2.6 Incremental phase audit and Modal orchestration fix

`phase_audit_extend.py` was added so completed deterministic phases can be reused and
only missing phases need new fits.

Important implementation lesson:

- invoking the canonical phase driver from another remote Modal app caused hydration /
  nested-remote crash loops;
- the corrected design uses a local entrypoint to launch the canonical
  `freq_experiment.py` app through normal `modal run` boundaries;
- Volume helpers only inspect, snapshot, validate, and merge artifacts.

The extension path also records turnover / cost and preserves reusable phase results.

### 2.7 Zero-fit attribution diagnostics

Two audit layers were added that perform no new model training:

`phase_attribution_core.py` + `phase_attribution_diagnostic.py`

- gross vs net gap decomposition;
- transaction-cost contribution;
- annual gap attribution;
- retrain T+1..T+5 turnover/cost event study;
- gap-formation milestones.

`phase_regime_age_core.py` + `phase_regime_age_diagnostic.py`

- model age on signal day T for execution T+1;
- fixed 20-day trend / volatility / drawdown regimes;
- stress regime;
- phase gap by age, regime, and age × regime interaction.

These diagnostics are audit tools only and must not be used to choose a historically
favorable phase.

### 2.8 Satellite gate/screen orchestration

`satellite_audit_core.py` + `satellite_pre_tuner_audit.py` implement fail-closed
ChiNext / STAR audit orchestration:

```text
canonical reproducibility gate
  -> strict gate artifact validation
  -> canonical 0/5/10/15 screen
  -> strict gate/screen manifest + hash validation
  -> local JSON export
```

Frozen satellite settings are enforced:

```text
ChiNext: top20 / nd3
STAR:    top50 / nd2
freq:    20
screen:  0,5,10,15
```

---

## 3. CSI1000: current authoritative evidence

### 3.1 v5 reproducibility gate

The CSI1000 phase-0 gate passed exactly.

Key identity checks:

```text
prediction chunks match = true
signal hash match       = true
report hash match       = true
n retrains / repeat     = 70
```

Canonical phase-0 metrics:

| Metric | CSI1000 phase 0 |
|---|---:|
| Strategy CAGR | 6.84% |
| Benchmark CAGR | 1.47% |
| Relative excess CAGR | 5.29% |
| Strategy MaxDD | -24.31% |
| Benchmark MaxDD | -46.71% |
| Sharpe | 0.550 |
| IR | 0.158 |
| Annual volatility | 13.43% |
| Total strategy return | 46.10% |
| Relative total return | 34.38% |
| account_return_max_error | 0 |

This supersedes older v3/v4 phase-0 values.

### 3.2 Deterministic five-phase screen

The completed deterministic screen is:

| Phase | Strategy CAGR | Relative CAGR | Sharpe | Strategy MaxDD |
|---:|---:|---:|---:|---:|
| 0 | 6.84% | 5.29% | 0.55 | -24.31% |
| 4 | 5.69% | 4.16% | 0.43 | -30.77% |
| 6 | 10.55% | 8.95% | 0.81 | -22.44% |
| 10 | 8.92% | 7.34% | 0.70 | -24.56% |
| 15 | 9.98% | 8.39% | 0.74 | -21.77% |

Observed relative-CAGR range:

```text
4.16% .. 8.95%
spread ≈ 4.79 percentage points
```

This proves that meaningful retraining-calendar sensitivity remains even after
deterministic training removed the old reproducibility noise.

Important nuance:

> Phase number is a retraining-calendar offset, not a monotonic model-age variable.
> Do not interpret a larger phase number as "an older model."

### 3.3 Zero-fit attribution of the weak phase

Phase 4 was compared against 0 / 6 / 10 / 15 without new model fits.

Results:

- transaction-cost differences explain only about 4.9%-20.5% of the pairwise net gap;
- median cost contribution is about 6.5%;
- therefore roughly 80%-95% of the gap is already present in the gross signal path;
- 2022-2023 account for roughly 84%-94%+ of the negative pairwise gap;
- phase 4 has higher overall turnover, but T+1..T+5 after retraining is **not** a
  turnover spike; its event-window turnover is slightly below non-event turnover.

Conclusion:

> Phase 4 weakness is not primarily a transaction-cost or immediate post-retrain
> turnover effect.

### 3.4 Regime × model-age diagnostic

The second zero-fit diagnostic tested whether the 2022-2023 concentration could be
explained by:

- focus/comparator model age;
- 20-day benchmark trend;
- 20-day annualized volatility;
- prior drawdown;
- composite stress;
- model-age × regime interactions.

No consistent mechanism explained the pairwise gaps across comparators.

The correct conclusion is deliberately limited:

> Under the tested model-age and preregistered coarse market-regime dimensions, the
> 2022-2023 phase-4 disadvantage remains unresolved.

Do **not** overstate this as:

- "phase 4 is structurally bad in all conditions"; or
- "phase 4 is proven non-structural."

Phase 4 is not persistently worse in every calendar year, and a more complex
phase × market-state interaction can still exist.

### 3.5 Current CSI1000 decision

CSI1000 is the only pool that currently has both:

1. a passing exact reproducibility gate; and
2. positive relative performance under the corrected baseline.

However, its calendar sensitivity is material.

Therefore:

- it is suitable to enter production-aligned tuning;
- a single favorable phase must never be used as the tuning objective;
- robustness across temporal folds and selected calendar phases must be part of model
  selection;
- the full 20-phase grid is **deferred**, not cancelled.

The full grid becomes worthwhile later when:

- certifying a final production candidate;
- estimating lower-tail calendar risk precisely; or
- comparing single-phase retraining against staggered / multi-phase ensembles.

---

## 4. ChiNext: reproducible baseline, but currently weak

### 4.1 Gate

ChiNext `top20 / nd3` passed the exact v5 double-fit gate:

```text
prediction chunks match = true
signal hash match       = true
report hash match       = true
n retrains / repeat     = 70
account consistency     = exact
```

Therefore the phase screen is valid evidence under the current protocol.

### 4.2 Four-phase screen

Benchmark CAGR for the project equal-weight ChiNext benchmark was 9.02%.

| Phase | Strategy CAGR | Relative CAGR | Sharpe | IR | Strategy MaxDD |
|---:|---:|---:|---:|---:|---:|
| 0 | 7.93% | -1.00% | 0.397 | -0.019 | -44.82% |
| 5 | 4.96% | -3.72% | 0.309 | -0.168 | -47.20% |
| 10 | -0.78% | -8.99% | 0.121 | -0.585 | -42.77% |
| 15 | -2.78% | -10.82% | 0.066 | -0.641 | -49.58% |

Four-phase summary:

```text
median relative CAGR  ≈ -6.35%
best relative CAGR    ≈ -1.00%
worst relative CAGR   ≈ -10.82%
relative range        ≈ 9.83 pp
```

Mean turnover is also high:

```text
phase 0   9.08%
phase 5  11.70%
phase10  10.33%
phase15  13.83%
```

### 4.3 Correct interpretation

Supported:

> The current frozen Alpha158 + LightGBM + raw-20d-label + freq20 + top20/nd3
> ChiNext baseline is not strong enough to enter the primary full tuner.

Not supported:

- "ChiNext as a universe has no alpha";
- "higher phase means an older model, therefore stale models explain the decline";
- "all future ChiNext model families will fail."

The four phases happened to decline in the order 0 -> 5 -> 10 -> 15, but phase is a
calendar offset, not a fixed age ordering.

The older positive ChiNext legacy result is superseded as production evidence. It
predates the corrected execution / metric / reproducibility stack and should not be
used to justify deployment.

### 4.4 Current ChiNext decision

ChiNext remains a research satellite, but only through a **bounded rescue path** rather
than the expensive main tuner.

A future rescue screen may test a small, representative parameter set and/or alternative
targets. If no robust positive evidence appears, stop spending compute on the current
raw-20d baseline.

---

## 5. STAR: reproducibility blocked; alpha conclusion not yet available

The STAR `top50 / nd2` reproducibility gate failed at prediction-chunk equality.

Therefore:

```text
gate PASS                 = no
trusted phase-0 baseline  = no
4-phase screen            = not run
alpha conclusion          = unavailable
production eligibility    = blocked
```

This is an important correction to shorthand summaries that called STAR "not viable."

The correct statement is:

> STAR is **reproducibility unresolved / production blocked**. The strategy itself has
> not yet been judged under a trustworthy corrected baseline.

Because CSI1000 and ChiNext use the same deterministic LightGBM controls successfully,
a STAR-only failure should first be investigated as a STAR-specific data / ordering /
dataset-construction / prediction-lineage problem rather than immediately blamed on
LightGBM's deterministic setting.

The current run did not commit a structured STAR gate-failure artifact containing the
first mismatching retrain chunk, so the next diagnostic should improve failure capture.

---

## 6. Three-pool status at the stage boundary

| Pool | Reproducibility | Corrected alpha evidence | Next role |
|---|---|---|---|
| CSI1000 | PASS | positive relative baseline; calendar-sensitive | primary full tuning line |
| ChiNext | PASS | frozen 4-phase baseline negative relative in all sampled phases | bounded rescue / alternative-target line |
| STAR | FAIL / unresolved | cannot be judged yet | determinism/data-lineage repair line |

The project therefore does **not** need to become CSI1000-only.

The agreed next-stage direction is **asymmetric three-pool parallelism**:

```text
                     shared research framework
                              |
             +----------------+----------------+
             |                |                |
          CSI1000          ChiNext           STAR
             |                |                |
      production-aligned   bounded rescue   fix gate /
          full tuner          screen        data lineage
             |                |                |
             +----------------+----------------+
                              |
                  common robustness review
```

The pools may eventually require different targets, portfolio settings, or model
parameters. "Three pools in parallel" does not mean one shared optimal configuration.

---

## 7. What is now considered reliable and should not be reopened casually

The following are established unless new contradictory evidence appears:

- 20-session label maturity purge and chronology guards;
- T-close scoring -> T+1-open execution;
- continuous account without per-window reset;
- canonical CAGR / relative CAGR / MaxDD / Sharpe / IR definitions;
- direction-aware board/date price-limit logic;
- statutory CNY tick handling using original-price reconstruction;
- no 5% high-open production overlay;
- research/paper execution alignment;
- explicit deterministic LightGBM policy;
- exact CSI1000 phase-0 double-fit gate;
- exact ChiNext phase-0 double-fit gate;
- material CSI1000 calendar-phase sensitivity;
- current ChiNext frozen baseline is weak under corrected evidence;
- STAR cannot proceed until reproducibility is repaired.

---

## 8. Historical claims that are superseded

Do not use the following as current production evidence:

### 8.1 Old 13-14% annualized baseline

Superseded by execution attribution. It relied on legacy execution behavior containing
future-informed information.

### 8.2 Old README / final-audit 11%+ excess figures

Those belong to an older protocol stack. They are historical context only and must not
override the current v5 reproducible/canonical results.

### 8.3 Old CSI1000 20-phase 1.41%-9.33% range as "pure phase risk"

Invalid as a clean phase distribution because the old phase experiment did not match the
standalone phase-0 lineage exactly. The corrected five-phase result is authoritative for
the current calendar-sensitivity conclusion.

### 8.4 "Phase number = model age"

False. Phase is a calendar offset. Model age changes within every phase.

### 8.5 "STAR is unprofitable"

Not established. STAR is currently **unreproducible**, so its corrected alpha is unknown.

### 8.6 "ChiNext universe has no alpha"

Not established. Only the current frozen baseline has been rejected from the primary
tuning path.

---

## 9. Authoritative artifacts at this stage

### CSI1000

```text
results/freq_experiment/results_csi1000.json
results/freq_phase_sensitivity/phase_csi1000_freq20.json
results/freq_phase_attribution/phase_csi1000_freq20_zero_fit.json
results/freq_phase_regime_age/phase_csi1000_freq20_regime_age_zero_fit.json
```

### ChiNext

```text
results/freq_experiment/results_chinext.json
results/freq_phase_sensitivity/phase_chinext_freq20.json
results/satellite_pre_tuner/chinext_freq20_gate_screen.json
```

### STAR

No passing market-scoped gate/screen artifact exists at this boundary.

The failed gate should be treated as an unresolved audit event rather than a strategy
result.

### Raw reproducibility evidence

Where available, result JSONs retain:

- prediction chunk hashes;
- full signal hashes;
- report byte/content hashes;
- raw report paths;
- runtime / source / provider fingerprints;
- snapshot tokens.

---

## 10. Remaining limitations

Known limitations remain:

- historical point-in-time ST status is incomplete;
- CSI1000 relative metrics use the CSI1000 **price index**, not a total-return index;
- ChiNext and STAR use project-generated equal-weight reference benchmarks rather than
  official investable total-return benchmarks;
- sampled retraining phases are correlated and are not independent statistical samples;
- the full CSI1000 20-phase distribution has not been recomputed under the corrected
  deterministic protocol;
- STAR reproducibility root cause is unresolved;
- no production-aligned LightGBM tuner has yet been run under the corrected stack.

---

## 11. Next-stage plan: asymmetric three-pool parallel work

The next stage should optimize expected real-world robustness, not the prettiest
single backtest.

### 11.1 CSI1000 - primary full tuner

Freeze initially:

```text
universe           CSI1000
label              raw 20-session label
retrain frequency  20
portfolio          top20 / nd2
execution          T+1 open
price limits       board/date aware
metrics            canonical portfolio metrics
```

Stage A should tune LightGBM parameters using purged temporal validation and
production-aligned scoring.

Candidate selection must emphasize:

- median performance across temporal folds;
- lower-tail / worst-fold behavior;
- positive-fold ratio;
- Sharpe and IR;
- MaxDD;
- turnover / cost;
- robustness across selected fixed calendar phases.

Do not rank candidates by one phase's CAGR.

After Stage A:

1. TopK / n_drop tuning using saved signals where possible;
2. target experiments: raw, benchmark-relative, residual/neutralized, carefully designed
   rank-oriented targets;
3. ensemble / staggered-model experiments if justified.

### 11.2 ChiNext - bounded rescue screen

Do not launch the full CSI1000-sized tuner immediately.

Use a small representative screen to answer:

> Can any sensible model family / target configuration move the pool from consistently
> negative relative performance to robustly useful evidence?

Possible rescue axes:

- selected LightGBM parameter families;
- lower-turnover portfolio settings;
- benchmark-relative target;
- residual / neutralized target;
- alternative TopK / n_drop only after signal quality warrants it.

If the bounded screen remains weak, defer ChiNext rather than brute-force it.

### 11.3 STAR - reproducibility repair first

Before any performance tuning:

1. persist a structured failure artifact;
2. identify the first mismatching retrain date;
3. compare repeat-A / repeat-B training and validation index ordering;
4. fingerprint ordered training rows, feature columns, labels, and prediction index;
5. inspect STAR pool construction / membership ordering and any custom benchmark side
   effects;
6. rerun the gate.

Only after an exact gate PASS may STAR run the same 0/5/10/15 screen.

### 11.4 Cross-pool evaluation

A common research framework is desirable, but pool objectives must remain separate.

Do **not** combine the three pools into a naive score such as:

```text
CSI1000 relative CAGR + ChiNext relative CAGR + STAR relative CAGR
```

The benchmark semantics differ, and a simple sum can hide a pool failure.

Instead compare each candidate using per-pool robust metrics, then ask whether a model
family generalizes across universes.

A candidate that is slightly weaker in-sample but stable across pools / folds / phases
may be more valuable than a single-pool historical winner.

---

## 12. Deferred work

Do not spend compute on these by default in the next step:

- full corrected 20-phase CSI1000 grid;
- full 20-phase ChiNext or STAR grids;
- the current legacy `--tune 200` path;
- simultaneous brute-force search across LightGBM × TopK × n_drop × target × phase;
- selecting the historically best phase for production.

Revisit the full CSI1000 phase distribution only for final production-risk certification
or a direct multi-phase ensemble/staggered-retraining decision.

---

## 13. Handoff checklist for the next implementation session

Before writing tuner code:

1. read this document;
2. confirm `main` has no newer audit result that changes pool status;
3. keep `freq_experiment.py` reproducibility semantics stable unless a deliberate new
   protocol version is introduced;
4. preserve exact manifest/hash lineage for all new experiments;
5. implement one shared multi-pool research framework, but allow market-specific modes:
   - CSI1000 = full tuning;
   - ChiNext = bounded rescue;
   - STAR = blocked until reproducibility repair;
6. define candidate ranking from robust fold/phase metrics before running a large search;
7. retain raw predictions/signals/reports needed for independent recomputation;
8. do not reintroduce legacy execution assumptions to recover historical headline returns.

The next stage should move from **"is the baseline trustworthy?"** to
**"which model/target/portfolio choices improve robust out-of-sample stock selection?"**

That is the correct point to resume work.
