# 11 - Canonical Portfolio Performance Metrics (2026-10-06)

## Why

Several historical experiments used arithmetic approximations such as:

```text
mean(daily active return) * 238
cumsum(active return) drawdown
```

Those are useful diagnostics but they are **not** the same as portfolio CAGR or portfolio maximum drawdown.

This repository now uses `portfolio_performance.py` as the canonical implementation for continuous-account performance reporting.

## Canonical definitions

- Strategy net return: Qlib `return - cost`.
- Strategy NAV: Qlib after-cost `account / initial_cash` when available.
- Strategy CAGR: compound growth of the true account NAV over elapsed calendar years.
- Benchmark CAGR: compound growth of daily benchmark returns.
- Relative excess CAGR: CAGR of `strategy NAV / benchmark NAV`.
- Portfolio MaxDD: maximum percentage drawdown of strategy NAV, including the initial NAV=1 peak.
- Sharpe: annualized mean/std of after-cost strategy daily returns (rf=0 by default).
- Information ratio: annualized mean/std of after-cost active daily returns.

The helper also reports `account_return_max_error`, which checks the Qlib account path against `return - cost`.

## Batch C caveat

Batch C resets the account every quarter. Therefore its 23 quarterly windows do **not** define a single five-year continuous account.

Batch C may report correct metrics for each individual quarterly window, but the summary intentionally sets:

```json
{
  "continuous_account_cagr": null,
  "continuous_account_max_drawdown": null
}
```

The old quarterly-mean-times-four statistic is retained only as `ann_excess_approx_legacy_arithmetic` for historical comparison.

## Continuous frequency / execution-attribution experiments

These use one continuous account and therefore report:

- `strategy_cagr`
- `benchmark_cagr`
- `relative_excess_cagr`
- `strategy_max_drawdown`
- `benchmark_max_drawdown`
- `relative_max_drawdown`
- `sharpe`
- `information_ratio`
- `annual_volatility`

Legacy arithmetic fields remain explicitly prefixed with `legacy_` so old experiment interpretation can be audited without confusing them with the canonical metrics.

## Result governance

1. Do not compare a legacy arithmetic annualized excess directly with a new relative excess CAGR without labeling the metric change.
2. Do not call cumulative active-return drawdown “portfolio MaxDD”.
3. Do not infer a five-year continuous CAGR from quarterly-reset Batch C windows.
4. Future tuner redesign should use these canonical metrics for reporting; objective selection should be defined separately and frozen before a new tuning campaign.
