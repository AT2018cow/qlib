# 12 - Pre-tuner Audit (2026-10-06)

> **Stage completion update (2026-10-07):** The pre-tuner audit described here has now produced corrected evidence for CSI1000 and ChiNext; STAR remains blocked by a failed prediction-chunk reproducibility gate. The authoritative stage summary and next-stage modes are in [14-pre-tuner-stage-summary-20261007.md](14-pre-tuner-stage-summary-20261007.md).


## Scope

This audit is intentionally before any tuner redesign. It fixes execution correctness,
reproducibility, and baseline evidence only; it does not search or deploy new model
parameters.

## 1. Statutory limit-price tick on original CNY scale

Qlib CN daily prices are normalized adjusted prices. The statutory 0.01 CNY price tick
must therefore be applied after reconstructing the original-RMB price scale with
`$factor`, not directly to normalized `$open` or `Ref($close,1)`.

The corrected board-aware execution remains T-close signal -> T+1 open, direction-aware,
with no 5% high-open production overlay.

Current protocol versions:

- continuous frequency baseline: `continuous_account_board_aware_v5_repro`;
- phase sensitivity: `retrain_phase_sensitivity_v2_repro`;
- Batch C: `board_aware_open_bootstrap_v5_repro`;
- execution attribution: `execution_attribution_v3_repro`.

Earlier v3/v4 artifacts remain historical evidence but are superseded for future
execution-sensitive and phase-sensitivity conclusions.

## 2. Frozen three-pool baseline matrix

The pre-tuner gate covers all three pools whose prior conclusions matter:

| market | frozen portfolio config | benchmark | reason |
|---|---|---|---|
| `csi1000` | top20 / nd2 | SH000852 | production core |
| `chinext` | top20 / nd3 | SZ399998 equal-weight | production satellite |
| `star` | top50 / nd2 | SZ399997 equal-weight | previously rejected using legacy evidence; must be recalculated |

These are frozen **baseline** configurations, not claims of optimality.

Fresh chenditc bundles do not contain the synthetic ChiNext/STAR equal-weight
benchmarks. `prepare(..., market=...)` rebuilds the requested custom pool and benchmark
after a forced refresh. A non-forced prepare only ensures those derived files and does
not download a newer provider snapshot.

Frequency summaries are market-scoped:

```text
/vol/freq_experiment/results_csi1000.json
/vol/freq_experiment/results_chinext.json
/vol/freq_experiment/results_star.json
```

CSI1000 additionally updates the legacy `results.json` alias.

## 3. Why the reproducibility gate is mandatory

The first v4 CSI1000 audit exposed a phase-0 contradiction: the standalone baseline and
phase-audit phase 0 used the same dates and portfolio configuration but produced
different signal/portfolio paths. Therefore the old 20-phase dispersion cannot be
interpreted as pure calendar-phase sensitivity.

LightGBM retraining now uses one shared deterministic CPU policy in research and
production:

```text
seed = 0
data_random_seed = 1
feature_fraction_seed = 2
bagging_seed = 3
drop_seed = 4
objective_seed = 5
extra_seed = 6
deterministic = true
force_col_wise = true
```

The explicit sub-seeds preserve LightGBM's historical default seed choices while the
execution controls remove thread/histogram nondeterminism. Warm Modal workers call
`vol.reload()` before reading data.

The runtime manifest records:

- provider prefix fingerprint through the evaluation cutoff;
- model-config SHA256;
- LightGBM reproducibility parameters;
- Python / pyqlib / LightGBM / NumPy / pandas versions;
- `freq_experiment.py` source SHA256;
- one derived snapshot token used by every worker.

## 4. Reproducibility gate

Run this **before** phase sensitivity:

```bash
modal run freq_experiment.py::reproducibility_gate_driver --freq 20 --eval-from 2021-01-04 --market csi1000 --topk 20 --nd 2
modal run freq_experiment.py::reproducibility_gate_driver --freq 20 --eval-from 2021-01-04 --market chinext --topk 20 --nd 3
modal run freq_experiment.py::reproducibility_gate_driver --freq 20 --eval-from 2021-01-04 --market star --topk 50 --nd 2
```

The gate submits the identical phase-0 lineage twice. It fails unless:

1. every retrain chunk has the same prediction content SHA256 in repeat A and B;
2. the concatenated signal SHA256 is identical;
3. two independent portfolio backtests have the same canonical report-content SHA256;
4. all workers see the same committed provider snapshot token.

A passing gate writes the canonical phase-0 baseline, raw daily report, signal artifact,
chunk hashes, and runtime/provider manifest. This replaces a separate single-pass
baseline run for pre-tuner audit purposes.

## 5. Phase sensitivity after the gate

`retrain_phase_sensitivity_driver` defaults to requiring the passing gate. Before
launching model fits it re-computes the provider/runtime/source manifest and fails if it
differs from the gate manifest.

Phase 0 is loaded from the gate's saved signal artifact, not trained a third time. Thus
phase 0 in the phase JSON is **identical by construction** to the independently
double-fitted baseline that passed the reproducibility gate.

For CSI1000, the previous full-grid result is invalid for pure phase attribution because
it predates this gate; rerun it only after the gate if a full distribution is still
required.

For ChiNext and STAR, use the fail-closed satellite wrapper. It runs the canonical
double-fit gate first, validates the committed gate artifact, and only then launches
the 0/5/10/15 phase screen through the existing cost-aware phase extension path.

Run the pools separately:

```bash
modal run satellite_pre_tuner_audit.py --market chinext
modal run satellite_pre_tuner_audit.py --market star
```

Frozen configurations are enforced by the wrapper:

```text
chinext  top20 / nd3
star     top50 / nd2
freq     20
phases   0,5,10,15
```

The wrapper does **not** import or call the canonical Modal functions cross-app. It
launches the existing CLI entrypoints so `freq_experiment.py` keeps its exact source
identity. The sequence is:

```text
canonical reproducibility gate
  -> strict gate artifact validation
  -> canonical 0/5/10/15 screen
  -> strict gate/screen manifest + hash validation
  -> local JSON export
```

If the gate fails or its artifact is inconsistent, the phase screen is not launched.

Each successful run writes local JSONs suitable for review/push:

```text
results/freq_experiment/results_<market>.json
results/freq_phase_sensitivity/phase_<market>_freq20.json
results/satellite_pre_tuner/<market>_freq20_gate_screen.json
```

The compact summary is convenience output only; the market-scoped gate and phase JSONs
remain authoritative.

The four-phase screen is a **robustness screen**, not a phase-selection exercise. Do
not select phase 5/10/15 for production based on historical performance. Its purpose is
to answer whether the frozen baseline has enough reproducible, calendar-robust evidence
to keep the pool in the production-aligned tuning path.

Current priority after the completed CSI1000 diagnostics is:

1. ChiNext gate + 0/5/10/15 screen;
2. STAR gate + 0/5/10/15 screen;
3. compare the three pools under the same canonical metrics;
4. move into production-aligned model/portfolio tuning for pools that remain credible.

The deferred CSI1000 full 20-phase grid is a later production-risk audit, not a blocker
for these satellite screens or the next tuning stage.

Only expand a pool to all 20 phases when the screen shows material phase risk or a full
phase distribution is needed for a production decision.


When extending a **partially completed** deterministic phase audit on the same frozen
manifest, use the cost-aware extension driver instead of rerunning already-completed
non-zero phases:

```bash
modal run phase_audit_extend.py::extend_phase_sensitivity_driver \
  --freq 20 --eval-from 2021-01-04 \
  --market csi1000 --topk 20 --nd 2 \
  --phases 0,4,6,10,15
```

The extension driver leaves `freq_experiment.py` unchanged so the gate's recorded
source hash remains valid. It reuses an existing non-zero phase only when protocol,
market, frequency, portfolio parameters, evaluation window, reproducibility manifest,
and gate payload all match. Orchestration happens in a Modal `local_entrypoint`:
remote helpers only inspect/snapshot/merge the shared Volume, while the local process
launches the canonical `freq_experiment.py::retrain_phase_sensitivity_driver` with a
normal nested `modal run`. This is intentional because the canonical driver itself
calls sibling Modal functions (`prepare.remote` and `freq_window.map`) and therefore
must execute inside its own Modal app rather than as a raw function inside another
remote container. The merged result also records mean turnover, turnover source,
total cost, benchmark MaxDD, and annual volatility. Reusable phase results are copied
to a dedicated `.reuse_source.json` snapshot before the canonical subset run can
overwrite the phase JSON.


### Zero-fit phase attribution before any full 20-phase grid

When a deterministic phase screen already shows material dispersion, diagnose the
existing retained reports before spending more model fits:

```bash
modal run phase_attribution_diagnostic.py \
  --market csi1000 \
  --freq 20 \
  --phases 0,4,6,10,15 \
  --focus-phase 4 \
  --event-window 5
```

This path performs **zero model fits**. It reads the retained raw Parquet reports,
verifies every report content hash, independently recomputes net strategy CAGR and
relative CAGR, and fails closed if the recomputed canonical metrics do not match the
saved phase results.

The local entrypoint writes:

```text
results/freq_phase_attribution/phase_csi1000_freq20_zero_fit.json
```

For every retained phase it reports full-window gross/net and relative performance,
calendar-year attribution, turnover/cost, and a retrain-event study covering execution
day T+1 through T+5 versus non-event days.

For the focus phase (CSI1000 phase 4 by default), it additionally compares each other
retained phase and reports:

- gross NAV gap versus net NAV gap;
- the log-gap contribution from differential transaction costs and its share of the
  final net gap;
- annual net log-gap contributions and the one/two/three-year concentration of negative
  gaps;
- the first dates when 25% / 50% / 75% of the final gap formed;
- maximum historical advantage and disadvantage.

Interpretation rule:

1. if the phase-4 deficit is mostly cost-driven and concentrated immediately after
   retrains, investigate signal/turnover instability before expanding the grid;
2. if the deficit is concentrated in one or two market years, treat phase risk as
   regime-dependent and inspect those periods first;
3. if the deficit is broad across years and differential costs explain only a minority,
   the five-phase screen has established structural calendar-phase sensitivity and a
   full 20-phase distribution is justified for robust lower-tail/median estimates.

This diagnostic remains audit-only and must not be used to select the historically
best phase.


### Post-attribution result and zero-fit regime × model-age follow-up

The retained 5-phase CSI1000 attribution established the following on the same frozen
v2_repro manifest:

- differential transaction costs explain only about 4.9%–20.5% of phase 4's pairwise
  net gap (median about 6.5%);
- phase 4's average turnover is materially higher, but its T+1 through T+5 retrain
  event window is **not** a turnover spike: event-window turnover is slightly below
  its non-event turnover;
- 2022–2023 account for roughly 84%–94%+ of the negative pairwise gap contribution.

The supported conclusion is therefore **phase × market-regime interaction**. Do not
overstate this as proof that phase 4 is "non-structural": the current evidence shows
that its underperformance is not persistent across calendar years, while a structural
interaction between retraining calendar and fast-changing regimes remains possible.

Before paying for the remaining full 20-phase grid, run the next zero-fit mechanism
diagnostic:

```bash
modal run phase_regime_age_diagnostic.py \
  --market csi1000 \
  --freq 20 \
  --phases 0,4,6,10,15 \
  --focus-phase 4 \
  --regime-years 2022,2023 \
  --lookback 20
```

This path performs **zero model fits, zero signal generation, and zero new backtests**.
It reads only the retained raw reports, the trading calendar, and the existing
chunk-level retrain lineage.

Model age is defined on the signal day T used for execution on T+1, measured in
trading sessions since the active retrain. For freq=20 the resulting age bins are
0–4, 5–9, 10–14, and 15–19 sessions.

Market-regime labels are deliberately fixed ex ante and use only benchmark information
available through the prior execution session:

- 20-session trend: <= -5%, between -5% and +5%, or >= +5%;
- annualized 20-session volatility: <20%, 20%–30%, or >=30%;
- prior benchmark drawdown: <10%, 10%–20%, or >=20%;
- composite stress: any of down <= -5%, vol >=30%, or drawdown >=20%.

Do not tune these thresholds from the observed phase gap. They are coarse diagnostic
bins, not production parameters.

The diagnostic writes:

```text
results/freq_phase_regime_age/phase_csi1000_freq20_regime_age_zero_fit.json
```

For phase 4 versus each comparator it reports:

- full-sample and 2022–2023 net/gross/cost log-gap;
- attribution by focus model-age bin, comparator age bin, and relative-age state;
- attribution by trend, volatility, drawdown, and composite stress regime;
- interactions between relative model age and market regime;
- both total negative-gap contribution and mean-daily gap intensity, so a regime with
  many observations is not confused with a regime that is intrinsically more adverse.

Decision rule:

1. if phase 4's deficit is concentrated when it is materially older than the
   comparator, the mechanism is consistent with stale-model exposure during regime
   changes; investigate retraining-frequency robustness before a full phase grid;
2. if the deficit is concentrated in stress/downtrend/high-volatility buckets
   regardless of relative age, treat the mechanism primarily as regime sensitivity;
3. if age × regime interactions dominate, tuner validation must explicitly include
   calendar/age robustness across fast regime transitions;
4. if none of these dimensions explains the 2022–2023 concentration, the mechanism
   remains unresolved and a full 20-phase distribution becomes more justified.

This follow-up remains audit-only and must not be used to select a historically
favorable retraining phase.


A full grid remains available:

```bash
modal run freq_experiment.py::retrain_phase_sensitivity_driver --freq 20 --eval-from 2021-01-04 --market csi1000 --topk 20 --nd 2 --phases all
```

The phase experiment remains audit-only: never select the historically best phase for
production.

## 6. Recomputable artifacts

Raw Qlib daily reports are stored as Parquet with both byte SHA256 and canonical
content SHA256. Baseline signals are also stored as Parquet with byte and content
hashes. Result JSON records the full chunk-level prediction lineage.

This allows independent recomputation of CAGR, portfolio MaxDD, Sharpe, IR, and exact
signal identity.

## 7. Remaining limitations

- historical point-in-time ST status remains incomplete;
- CSI1000 relative CAGR / IR are versus the CSI1000 **price index**, not a total-return index;
- this audit does not tune LightGBM hyperparameters, TopK/n_drop, target, or ensemble.

## Gate before next-stage tuning

The original all-pools gate requirement has been replaced by an **asymmetric per-pool**
rule. One unresolved satellite pool does not block a validated core pool.

Current entry conditions:

1. **CSI1000** may enter the primary production-aligned tuner because its corrected v5
   gate passed and its phase sensitivity has been investigated. Candidate selection must
   include temporal-fold and selected-calendar robustness; do not choose the best phase.
2. **ChiNext** may enter only a bounded rescue screen. Its v5 gate passed, but the frozen
   0/5/10/15 baseline had negative relative CAGR in every sampled phase.
3. **STAR** remains blocked from performance tuning until prediction-chunk reproducibility
   is repaired and the exact gate passes.
4. Any new protocol version must retain raw prediction/signal/report artifacts and
   canonical metrics so results remain independently auditable.

A full corrected 20-phase CSI1000 grid is deferred to final production-risk certification
or a direct staggered/multi-phase ensemble decision; it is not a prerequisite for the
next tuner stage.
