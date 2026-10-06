# 13 - Work Handoff After PR #8 (2026-10-06)

> **Superseded handoff (2026-10-07):** This handoff ended at PR #8 and is preserved for audit history. PR #9-#13 and subsequent experiments materially changed the project state. New work must start from [14-pre-tuner-stage-summary-20261007.md](14-pre-tuner-stage-summary-20261007.md), then consult this file only for PR #8-era context.


## Purpose

This document is the handoff point for continuing the Qlib stock-selection audit/tuning work in a new conversation.

Repository:

```text
https://github.com/AT2018cow/qlib
```

Current main commit at handoff:

```text
2b640786afe1d6656c7e4554eb6148aafe22aa62
```

The next conversation should start by reading this document together with:

- `docs/experiments/10-execution-attribution.md`
- `docs/experiments/11-portfolio-performance-metrics.md`
- `docs/experiments/12-pre-tuner-audit.md`

Do **not** restart the audit from old v2/v3 assumptions.

---

## 1. Current project objective

The program uses Qlib historical price/volume features to rank A-share candidates for the next trading day, but positions are not necessarily sold the next day.

The intended production behavior is:

```text
T close: generate scores
T+1 open: execute portfolio adjustment
hold positions continuously
rerun daily
rebalance according to the latest ranking/portfolio rules
```

The current research baseline uses:

```text
model: LightGBM + Alpha158
label horizon: 20 trading days
retrain frequency: 20 trading sessions
execution: T+1 open
portfolio: TopkDropoutStrategy
price limits: board/date-aware
continuous account: yes
high-open 5% overlay: disabled
```

This is a baseline, not an optimized final strategy.

---

## 2. Important completed fixes and PR history

### PR #5 - execution attribution

The old 13-14% annualized result was decomposed using a frozen signal path.

Main conclusions:

- the old scalar execution protocol used execution-day `$change`, which contains same-day close information;
- old symmetric limit blocking interacted with that oracle and created additional future-informed execution benefit;
- real board/date-aware limits did **not** cause the large performance collapse;
- allowing ChiNext/STAR was not inherently harmful;
- the 5% high-open rule reduced historical performance and was not a clean T+1-open execution protocol.

Do not attempt to recover the old 13-14% by restoring the legacy execution behavior.

### PR #6 - canonical portfolio metrics

Canonical portfolio metrics were added in `portfolio_performance.py`.

Use these definitions:

- strategy net return = `return - cost`;
- strategy NAV = Qlib after-cost `account / initial_cash` when available;
- strategy CAGR = geometric CAGR of the true account path;
- benchmark CAGR = geometric CAGR of benchmark returns;
- relative excess CAGR = CAGR of `strategy NAV / benchmark NAV`;
- portfolio MaxDD = drawdown of true compounded NAV, including initial NAV=1;
- Sharpe = after-cost strategy daily return mean/std annualized;
- information ratio = after-cost active return mean/std annualized.

The old `mean(active_return) * 238` and arithmetic cumulative-excess drawdown are retained only as explicitly named legacy diagnostics.

### PR #7 - pre-tuner execution audit

Key corrections:

1. statutory 0.01-CNY limit-price rounding is applied on original RMB prices reconstructed with Qlib `$factor`, not directly on normalized adjusted prices;
2. paper execution is aligned with the research C baseline;
3. production paper no longer applies the 5% high-open overlay;
4. raw daily portfolio reports and signal artifacts are retained for independent recomputation;
5. retraining phase sensitivity infrastructure was added.

The CNY tick correction had only a small effect on the CSI1000 baseline, so it did not invalidate the broader strategy conclusion.

### PR #8 - reproducibility gate before multi-pool retest

PR #8 is **merged**.

Merge commit:

```text
b2e23c3917701f0a649c29f9cfa7e7d4d5c41070
```

A follow-up fix was committed:

```text
95970a88f221cf74d204401214e191cdd95fe8f1
fix: tolerate warm-worker Volume reload failure in snapshot check
```

PR #8 added:

- deterministic LightGBM CPU policy shared by research and production;
- explicit seeds;
- `deterministic=true`;
- `force_col_wise=true`;
- provider snapshot fingerprint;
- runtime/model/source hashes;
- per-retrain prediction SHA256;
- full signal SHA256;
- canonical portfolio-report content SHA256;
- a double-fit phase-0 reproducibility gate;
- market-scoped result artifacts;
- frozen pre-tuner baseline configs for CSI1000 / ChiNext / STAR.

---

## 3. Frozen three-pool pre-tuner baseline configs

These are audit baselines only. They are **not** claims of optimality.

| Market | Frozen baseline | Benchmark | Current role |
|---|---|---|---|
| CSI1000 | top20 / n_drop=2 | SH000852 | production core |
| ChiNext | top20 / n_drop=3 | SZ399998 equal-weight | production/research satellite |
| STAR | top50 / n_drop=2 | SZ399997 equal-weight | previously rejected using legacy weak evidence; must be recalculated |

STAR must not remain excluded merely because the old `rolling5y_star_t50nd2.json` result was poor. That result used legacy `no_bootstrap_v1` logic and predates the corrected execution/reproducibility stack.

---

## 4. CSI1000 results before the reproducibility gate

### v3 canonical metrics

The first corrected canonical-metric run reported approximately:

```text
Strategy CAGR          7.54%
Benchmark CAGR         1.47%
Relative excess CAGR   5.98%
Portfolio MaxDD       -24.2%
Sharpe                  0.60
IR                      0.19
```

This established that the strategy was not merely a 3-4% total-return strategy. The earlier 3-4% figure came from an arithmetic active-return metric, not portfolio CAGR.

### v4 CNY-tick baseline

After correcting statutory tick rounding:

```text
Strategy CAGR          7.41%
Relative excess CAGR   5.86%
Sharpe                  0.59
Portfolio MaxDD       -24.4%
```

The tick fix changed performance only slightly.

### old v4 20-phase audit

The first 20-phase audit reported a very wide range, including approximately:

```text
relative CAGR min     1.41%  (phase 4)
relative CAGR max     9.33%  (phase 6)
median               ~5.99%
```

However, the same experiment's phase 0 produced:

```text
Strategy CAGR          6.41%
Relative CAGR          4.87%
```

while the standalone v4 phase-0 baseline was:

```text
Strategy CAGR          7.41%
Relative CAGR          5.86%
```

Those should have represented the same phase/configuration/window.

Therefore the old v4 20-phase dispersion is **not valid evidence of pure calendar-phase sensitivity**. It mixed phase effects with a reproducibility problem.

Do not quote the 1.41%-9.33% range as a clean phase-risk range.

---

## 5. Current authoritative CSI1000 result: v5 reproducibility gate PASS

The reproducibility gate has now been run in Modal and pushed to GitHub.

Commit:

```text
2b640786afe1d6656c7e4554eb6148aafe22aa62
data: reproducibility gate PASS (v5_repro protocol)
```

Artifact:

```text
results/freq_experiment/results.json
results/freq_experiment/results_csi1000.json
results/freq_experiment/report_csi1000_freq20_2021-01-05_2026-09-30.parquet
results/freq_experiment/signal_csi1000_freq20_phase00_2021-01-04_2026-09-29.parquet
```

Protocol:

```text
continuous_account_board_aware_v5_repro
```

### Gate status

```text
passed: true
gate_version: phase0_double_fit_v1
prediction_chunks_match: true
signal_hash_match: true
report_hash_match: true
n_retrains_per_repeat: 70
```

Signal SHA256 for both repeats:

```text
0e077cdd969a48d23ea87d6570db60a01966251dcc7d40f2d791a40d7b1cbefb
```

Canonical report-content SHA256 for both repeats:

```text
a5a0128719b8bca0b062e736064d94e0086aa5e842b854994d397a8834d67dbf
```

Provider fingerprint:

```text
cd2e68f571c6bd19fbb2b79089e891bb1c43b96ab1fcdb4d2b2a9b1fdd9ce5d5
```

Snapshot token:

```text
57b2c7752dbb4b92366fab104575c0fd5cf8fbf8b7b1b37a8e111daafa7acbd7
```

Runtime recorded in the result:

```text
Python     3.11.12
pyqlib     0.9.8.dev32
LightGBM   4.7.0
NumPy      1.26.4
pandas     2.2.3
```

### Current authoritative phase-0 performance

```text
Strategy CAGR          6.8369%
Benchmark CAGR         1.4697%
Relative excess CAGR   5.2895%
Strategy MaxDD        -24.3053%
Benchmark MaxDD       -46.7084%
Sharpe                  0.5499
Information ratio       0.1575
Annual volatility      13.4292%
Total strategy return  46.1044%
Relative total return  34.3806%
Account consistency     0.0 error
```

This v5 deterministic result is the **current trusted CSI1000 baseline**.

It supersedes the v3/v4 phase-0 numbers for future comparisons.

---

## 6. Interpretation of the v5 baseline

The deterministic baseline is lower than the earlier v4 standalone baseline:

```text
v4 strategy CAGR       7.41%
v5 strategy CAGR       6.84%

v4 relative CAGR       5.86%
v5 relative CAGR       5.29%
```

The approximate reduction is ~0.57 percentage points in both strategy and relative CAGR.

This is large enough that future tuning/phase studies must use the v5 reproducible path, but it does **not** invalidate the strategy:

- strategy CAGR remains positive;
- relative CAGR remains >5%;
- MaxDD remains materially smaller than the benchmark's historical drawdown;
- reproducibility is now proven at the signal and report level.

Do not mix v4 and v5 results in one optimization table.

---

## 7. Current recommended compute plan

Modal cost matters. The previous full CSI1000 20-phase run required roughly:

```text
~70 retrains per phase
20 phases
~1400 model fits
~25 workers x 8 CPU ~= 200 CPU
~25 workers x 24 GB ~= 600 GB RAM
multiple hours
```

A full phase grid should **not** be the default next step.

### CSI1000 next step

The reproducibility gate already passed.

Do **not** immediately rerun all 19 remaining phases.

First run a targeted diagnostic using the old extreme phases:

```bash
modal run freq_experiment.py::retrain_phase_sensitivity_driver \
  --freq 20 \
  --eval-from 2021-01-04 \
  --market csi1000 \
  --topk 20 \
  --nd 2 \
  --phases 0,4,6
```

Phase 0 is reused from the passing gate, so only phases 4 and 6 need new model fits.

Approximate incremental cost:

```text
2 phases x ~70 retrains ~= 140 fits
```

Purpose:

- phase 4 was the old worst phase;
- phase 6 was the old best phase;
- this quickly tests whether the old 1.4%-9.3% dispersion survives deterministic training.

Decision rule:

- if phases 4 and 6 converge reasonably near the v5 baseline, treat much of the old dispersion as training/reproducibility noise and do not run the full 20-phase grid;
- if phases 4 and 6 remain very far apart, add phases 10 and 15 before considering any full grid.

Optional second diagnostic:

```bash
modal run freq_experiment.py::retrain_phase_sensitivity_driver \
  --freq 20 \
  --eval-from 2021-01-04 \
  --market csi1000 \
  --topk 20 \
  --nd 2 \
  --phases 0,4,6,10,15
```

Do not select the historically best phase for production.

### ChiNext next step

No v5 reproducibility-gate artifact was found in GitHub at handoff.

Run:

```bash
modal run freq_experiment.py::reproducibility_gate_driver \
  --freq 20 --eval-from 2021-01-04 \
  --market chinext --topk 20 --nd 3
```

Only if PASS, run a low-cost phase screen:

```bash
modal run freq_experiment.py::retrain_phase_sensitivity_driver \
  --freq 20 --eval-from 2021-01-04 \
  --market chinext --topk 20 --nd 3 \
  --phases 0,5,10,15
```

Phase 0 is reused; the screen costs roughly three extra phase lineages.

### STAR next step

No v5 reproducibility-gate artifact was found in GitHub at handoff.

Run:

```bash
modal run freq_experiment.py::reproducibility_gate_driver \
  --freq 20 --eval-from 2021-01-04 \
  --market star --topk 50 --nd 2
```

Only if PASS, run:

```bash
modal run freq_experiment.py::retrain_phase_sensitivity_driver \
  --freq 20 --eval-from 2021-01-04 \
  --market star --topk 50 --nd 2 \
  --phases 0,5,10,15
```

This STAR rerun is important because the previous decision to exclude STAR relied on legacy results that may not be comparable to the corrected/reproducible stack.

---

## 8. What NOT to compute yet

Do not spend Modal budget on the following before the targeted diagnostics above:

- another full 20-phase CSI1000 grid;
- full 20-phase ChiNext grid;
- full 20-phase STAR grid;
- `--tune 200` with the current legacy tuner;
- simultaneous LGB x TopK x n_drop x target x phase brute-force search.

The old full-grid audit was useful for finding the reproducibility problem, but a new 1330-fit grid is not required merely to proceed.

---

## 9. Planned tuning sequence after the pre-tuner audit

Once reproducibility is proven for the relevant pools and targeted phase screening is understood, move to a new production-aligned nested walk-forward tuner.

The intended order is:

### Stage A - LightGBM hyperparameters

Freeze:

```text
universe
20d label
20-session retrain
current portfolio config
board-aware T+1-open execution
canonical metrics
```

Search only model parameters.

Do not use one static 2025 validation year as the sole optimizer.

Use purged temporal folds and a production-aligned validation design.

A practical compute design is:

```text
60-100 candidate parameter sets
x 4-5 relatively cheap temporal folds
=> ~300-500 fits for screening
```

Then take only the top ~5-10 robust candidates into full rolling-retrain evaluation.

### Stage B - TopK / n_drop

After the model is frozen, test portfolio parameters.

Example search space:

```text
topk = 10 / 20 / 30 / 50
n_drop = 1 / 2 / 3
```

Do not retrain the model for every TopK/n_drop pair when the same saved signal path can be reused.

### Stage C - target

Only after model/portfolio construction is frozen, compare target definitions such as:

- raw 20d future return;
- benchmark-relative return;
- residual / neutralized return;
- rank-oriented alternatives if implemented carefully.

### Stage D - ensemble

Test ensembles only after the single-model baseline is stable.

The objective is not to recover the old contaminated 13-14% result.

A more credible goal would be improving the current reproducible baseline while also improving or preserving:

- median relative excess CAGR;
- Sharpe;
- IR;
- MaxDD;
- worst-fold behavior;
- positive-fold ratio;
- turnover.

---

## 10. Current evaluation principles

For any future experiment, report at least:

```text
Strategy CAGR
Benchmark CAGR
Relative excess CAGR
Strategy MaxDD
Benchmark MaxDD
Relative MaxDD
Sharpe
Information Ratio
Annual volatility
Turnover
account_return_max_error
```

For model/tuning selection, do not simply maximize one historical CAGR.

Prefer stable performance across temporal folds / selected retraining phases.

Never select the best historical phase itself as a production parameter.

---

## 11. Known remaining limitations

These are not blockers to the immediate targeted diagnostics, but they must remain visible.

### Historical PIT ST status

Point-in-time ST coverage is still incomplete.

Therefore the historical 5% ST price-limit treatment is not perfect for every stock/date.

### CSI1000 benchmark basis

CSI1000 uses `SH000852`, a price-index benchmark.

Therefore:

```text
relative excess CAGR
IR
```

should be described as relative to the CSI1000 price index, not automatically as pure total-return alpha.

### Custom pool benchmarks

ChiNext and STAR use project-generated point-in-time equal-weight benchmark series:

```text
chinext -> SZ399998
star    -> SZ399997
```

Their relative metrics should be interpreted against those pool-specific benchmarks.

---

## 12. Code/logic conclusions already considered reliable

Unless new evidence contradicts them, do not reopen these from scratch:

- 20d label maturity purge is implemented;
- train/validation/test chronology is guarded;
- signal at T executes at T+1 open;
- continuous-account frequency backtest does not reset at every retrain;
- canonical portfolio CAGR / MaxDD / Sharpe / IR formulas are correct;
- Qlib account path agrees with `return - cost`;
- board/date-aware limit handling is direction-aware;
- statutory tick rounding uses original CNY price scale through `$factor`;
- 5% high-open overlay is not part of the production baseline;
- research and paper execution semantics were aligned;
- LightGBM reproducibility is now explicitly controlled and CSI1000 phase-0 double-fit reproducibility has passed.

---

## 13. Immediate next-conversation checklist

At the beginning of the next conversation:

1. Read this document and `docs/experiments/12-pre-tuner-audit.md`.
2. Confirm current `main` and whether new result commits appeared after:
   `2b640786afe1d6656c7e4554eb6148aafe22aa62`.
3. Do **not** create another PR before checking whether the requested experiment has already been run/pushed.
4. If no newer CSI1000 phase result exists, recommend/run only the targeted deterministic audit:
   `--phases 0,4,6`.
5. Review those phase results before deciding whether to add phases 10/15 or abandon a full-grid rerun.
6. Then run ChiNext and STAR reproducibility gates and low-cost phase screens.
7. Only after those pre-tuner checks decide the design of the production-aligned nested walk-forward tuner.
8. The next tuning implementation should be a **new PR after PR #8**, not a continuation of PR #8 itself, because PR #8 is already merged.

---

## 14. Key GitHub references

PR #5:
```text
execution attribution / frozen-signal protocol decomposition
```

PR #6:
```text
canonical portfolio CAGR / MaxDD / Sharpe / IR
```

PR #7:
```text
CNY tick execution correction + pre-tuner audit infrastructure
```

PR #8:
```text
reproducibility gate + deterministic LightGBM + three-pool audit preparation
merged
```

Current authoritative CSI1000 v5 result commit:

```text
2b640786afe1d6656c7e4554eb6148aafe22aa62
```

---

## 15. Bottom line

The project is now at a materially better point than the old 13-14% backtest era:

- known execution look-ahead has been removed;
- portfolio metrics are now correctly compounded;
- statutory price-limit rounding is corrected;
- paper/research execution is aligned;
- raw reports and signals are retained;
- deterministic model training has been introduced;
- CSI1000 phase-0 reproducibility has passed exactly.

The current trusted CSI1000 baseline is approximately:

```text
Strategy CAGR          6.84%
Relative excess CAGR   5.29%
Sharpe                  0.55
MaxDD                  -24.3%
```

The next goal is **not** to brute-force more history. The next goal is to use targeted,
cost-aware diagnostics to determine whether retraining-phase sensitivity is genuinely
large, then build a production-aligned nested walk-forward tuner without reintroducing
selection bias.
