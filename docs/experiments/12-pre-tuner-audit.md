# 12 - Pre-tuner Audit (2026-10-06)

## Scope

This audit is intentionally before any tuner redesign. It changes execution correctness and robustness evidence only; it does not search or deploy new model parameters.

## 1. Statutory limit-price tick on original CNY scale

Qlib CN daily prices are normalized adjusted prices. The statutory 0.01 CNY price tick therefore must not be applied directly to `$open` or `Ref($close,1)`.

`BoardAwareExchange` now subscribes to `$factor` and reconstructs the original-RMB scale:

```text
raw_open = adjusted_open / factor
raw_reference = adjusted_previous_close / current_factor
```

The exact 10/20/30/5% board threshold is applied to `raw_reference`, then rounded half-up to the 0.01 CNY tick before comparing with `raw_open`.

Invalid/missing factors on rows that require a statutory limit calculation fail closed with an exception rather than silently reverting to normalized-price rounding.

Protocol versions after this correction:

- continuous frequency: `continuous_account_board_aware_v4_cny_tick`
- Batch C: `board_aware_open_bootstrap_v4_cny_tick`
- execution attribution: `execution_attribution_v2_cny_tick`

Any v3 board-aware result remains historical evidence but is superseded for execution-sensitive conclusions.

## 2. Research / paper execution parity

The canonical production/research baseline is:

```text
T close signal -> T+1 open execution
board/date-aware statutory price limits
direction-aware limit semantics
no 5% high-open overlay
```

The paper portfolio now requests `$factor` and uses the same limit-mask inputs as the research exchange. The previous `high_open_block=0.05` call has been removed from the paper baseline.

The 5% high-open rule remains available only as an attribution diagnostic because it conditions on the realised opening print and then assumes execution at that same open.

## 3. Three-pool corrected rerun + retraining phase sensitivity

The pre-tuner gate covers all three pools whose prior conclusions matter:

| market | frozen portfolio config | benchmark | reason |
|---|---|---|---|
| `csi1000` | top20 / nd2 | SH000852 | production core |
| `chinext` | top20 / nd3 | SZ399998 equal-weight | production satellite |
| `star` | top50 / nd2 | SZ399997 equal-weight | previously rejected on legacy weak results; must be recomputed under corrected execution |

The STAR configuration is intentionally the historical terminal configuration (`top50/nd2`); this audit does not retune it. The old `rolling5y_star_t50nd2.json` artifact used `no_bootstrap_v1` and is not sufficient to decide whether STAR should remain excluded from production.

Fresh chenditc bundles do not contain the synthetic `SZ399998/SZ399997` benchmarks. `freq_experiment.prepare(..., market=...)` now rebuilds the requested custom pool and its point-in-time equal-weight benchmark after every forced data refresh, so ChiNext/STAR reruns do not depend on stale Modal Volume residue.

`retrain_phase_sensitivity_driver` evaluates all calendar phases for a fixed retraining interval without selecting a best phase.

For `freq=20`, phase 0 reproduces the historical frequency experiment anchor: first retrain exactly on `eval_from`. Phase `p` means the active lineage was first retrained `p` trading sessions before `eval_from`. Every phase is evaluated over the exact same execution window beginning on `eval_from + 1 trading session`.

Corrected baselines:

```bash
modal run freq_experiment.py::freq_driver --freqs 20 --eval-from 2021-01-04 --market csi1000 --topk 20 --nd 2
modal run freq_experiment.py::freq_driver --freqs 20 --eval-from 2021-01-04 --market chinext --topk 20 --nd 3
modal run freq_experiment.py::freq_driver --freqs 20 --eval-from 2021-01-04 --market star --topk 50 --nd 2
```

Full 20-phase audits:

```bash
modal run freq_experiment.py::retrain_phase_sensitivity_driver --freq 20 --eval-from 2021-01-04 --market csi1000 --topk 20 --nd 2 --phases all
modal run freq_experiment.py::retrain_phase_sensitivity_driver --freq 20 --eval-from 2021-01-04 --market chinext --topk 20 --nd 3 --phases all
modal run freq_experiment.py::retrain_phase_sensitivity_driver --freq 20 --eval-from 2021-01-04 --market star --topk 50 --nd 2 --phases all
```

For staged compute, a subset can be supplied, for example `--phases 0,5,10,15`; subset output is explicitly marked `complete_phase_grid=false` and must not be treated as the final phase audit.

The full output reports min / q25 / median / q75 / max across phases for:

- strategy CAGR
- relative excess CAGR
- strategy MaxDD
- relative MaxDD
- Sharpe
- information ratio

It also reports the full range in percentage points for strategy CAGR and relative excess CAGR.

This experiment is **audit-only**. The best phase must not be selected for production because that would itself be a tuning step.

## 4. Reproducible raw reports

Standard frequency runs now persist one market-specific summary plus the complete Qlib daily portfolio report:

```text
/vol/freq_experiment/results_csi1000.json
/vol/freq_experiment/results_chinext.json
/vol/freq_experiment/results_star.json
/vol/freq_experiment/reports/
```

For backward compatibility, CSI1000 also updates `/vol/freq_experiment/results.json`. ChiNext/STAR never overwrite that alias.

Phase sensitivity persists one report per phase under:

```text
/vol/freq_phase_sensitivity/reports/
```

Each result records the report path, row count, columns, and SHA256 of the Parquet bytes. This allows CAGR / MaxDD / Sharpe / IR to be independently recomputed from the daily `account`, `return`, `cost`, and `bench` series.

## 5. What this audit does not fix

- historical point-in-time ST status remains incomplete;
- CSI1000 benchmark is still a price-index benchmark, so relative CAGR / IR should be described as relative to the CSI1000 price index rather than pure total-return alpha;
- this audit does not tune LightGBM, TopK/n_drop, target, or ensemble parameters.

## Gate before nested tuning

Do not start the production-aligned nested walk-forward tuner until:

1. corrected v4 baseline runs have been regenerated for CSI1000, ChiNext, and STAR with the frozen configs above;
2. the full 20-phase audit has completed for all three pools;
3. raw report artifacts and summary JSON have been retained;
4. any large phase dispersion has been investigated rather than selecting the best-looking phase;
5. the STAR production decision has been revisited from the corrected v4 continuous-account evidence rather than the legacy `no_bootstrap_v1` result.
