# 12 - Pre-tuner Audit (2026-10-06)

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

For ChiNext and STAR, start with the lower-cost four-phase screen:

```bash
modal run freq_experiment.py::retrain_phase_sensitivity_driver --freq 20 --eval-from 2021-01-04 --market chinext --topk 20 --nd 3 --phases 0,5,10,15
modal run freq_experiment.py::retrain_phase_sensitivity_driver --freq 20 --eval-from 2021-01-04 --market star --topk 50 --nd 2 --phases 0,5,10,15
```

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
and gate payload all match. It validates the saved report content hash, runs only
missing phases through the canonical phase driver, then merges the requested phases.
The merged result also records mean turnover, turnover source, total cost, benchmark
MaxDD, and annual volatility. A `.preextend.json` backup is retained before the
canonical subset run overwrites the phase JSON.


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

## Gate before nested tuning

Do not start the production-aligned nested walk-forward tuner until:

1. the required pool passes `reproducibility_gate_driver`;
2. its corrected v5 baseline artifacts are retained;
3. its planned phase screen/full grid completes on the exact same manifest;
4. any material phase dispersion is investigated rather than selecting the best phase;
5. the STAR production decision is revisited from corrected reproducible evidence rather
   than legacy `no_bootstrap_v1` results.
