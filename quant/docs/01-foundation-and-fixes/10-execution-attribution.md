# 10 - Frozen-Signal Execution Attribution (2026-10-05)

## Purpose

The post-R24 rerun showed a large gap between the legacy continuous-account result and the new board-aware execution result. This experiment isolates the execution-layer causes **without retraining different models for each protocol**.

For a fixed retraining frequency, the model is trained once per retraining point and the resulting full signal path is frozen. All execution protocols consume that same signal.

## Protocols

| Code | Execution rule | Purpose |
|---|---|---|
| REF | Legacy Qlib scalar `limit_threshold=0.095`, T+1 open, symmetric limit blocking | Reproduce the historical v2 reference as closely as possible. This uses execution-day full-day `$change` and is diagnostic only. |
| A | Same legacy `$change` oracle, but current direction-aware buy/sell limit semantics | Separate strategy direction semantics from the oracle itself. |
| B | Uniform 9.5% threshold calculated from execution-day open / previous close | Remove the same-day-close oracle while keeping the old one-size-fits-all threshold. |
| C | Current board/date-aware statutory execution | Production research baseline: exact board limits, listing exemptions, T+1 open. |
| D | C + buy block when opening gap > 5% | Measure the existing paper-risk overlay. This is a diagnostic conditional on the observed open, not automatically an implementable opening-auction protocol. |
| E | C + exclude ChiNext and STAR after scoring | Test whether allowing those boards explains the performance gap. The model is **not retrained**. |

## Interpretation

The important deltas are:

- **REF -> A**: effect of old symmetric limit blocking vs current direction-aware limit handling.
- **A -> B**: same-day-close execution oracle / information-timing effect.
- **B -> C**: uniform 9.5% policy -> actual board/date-aware statutory rules.
- **C -> D**: value of a 5% high-open buy overlay.
- **C -> E**: contribution of allowing ChiNext/STAR candidates under the same frozen signal.

Do not interpret REF or A as valid production protocols. They intentionally preserve the legacy use of execution-day `$change` in order to measure how much of the old result depended on that information.

## Run

20-session production baseline:

```bash
modal run freq_experiment.py::execution_attribution_driver \
  --freq 20 \
  --eval-from 2021-01-04 \
  --market csi1000 \
  --topk 20 \
  --nd 2
```

60-session comparison:

```bash
modal run freq_experiment.py::execution_attribution_driver \
  --freq 60 \
  --eval-from 2021-01-04 \
  --market csi1000 \
  --topk 20 \
  --nd 2
```

Output:

```text
/vol/execution_attribution/attrib_csi1000_freq20.json
/vol/execution_attribution/attrib_csi1000_freq60.json
```

Each protocol now reports canonical compounded metrics:

- strategy CAGR;
- benchmark CAGR;
- relative excess CAGR (primary attribution metric);
- strategy / benchmark / relative maximum drawdown;
- Sharpe;
- information ratio;
- annualized volatility;
- cost sum and turnover diagnostics.

The old daily-mean × 238 active-return annualization is retained only under explicitly named
`legacy_*` fields for audit compatibility. Attribution deltas in `attribution_pp` use
**relative excess CAGR**; the former arithmetic deltas are retained separately as
`legacy_attribution_arithmetic_pp`.

## Result governance

1. Use the same provider snapshot for every protocol in one attribution run.
2. Do not tune thresholds based on this comparison and then call the same history independent OOS.
3. If D or E appears materially better under relative excess CAGR, also inspect strategy CAGR, MaxDD and Sharpe before treating it as a useful risk-overlay hypothesis.
4. Historical Batch C and frequency artifacts produced under the scalar-limit protocol remain legacy evidence.
5. The committed pre-fix Batch C artifacts are v3 historical baselines; the next corrected rerun will use `board_aware_open_bootstrap_v5_repro`.
6. The committed pre-fix frequency artifact is `continuous_account_board_aware_v3`; the next corrected rerun will use `continuous_account_board_aware_v5_repro`.


## Pre-tuner execution correction (2026-10-06)

`BoardAwareExchange` now reconstructs original-RMB prices with Qlib `$factor`
before applying the statutory 0.01 CNY limit-price tick.  Earlier v3 artifacts
rounded normalized adjusted prices directly and are therefore superseded for
execution-sensitive conclusions.

The 5% high-open protocol remains attribution-only.  The production paper
baseline no longer applies that overlay, so protocol C and paper execution use
the same board-aware T+1-open rule.
