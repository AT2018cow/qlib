# 09 - Remaining Qlib Wrapper / Execution Issues (2026-10-05)

This note records the issues intentionally **not** fixed in PR #3 because they change backtest semantics, execution assumptions, benchmark definition, or production portfolio behavior and therefore require separate historical validation.

## Scope

These are not currently identified as Qlib core bugs. They are wrapper / integration / execution-model issues in this repository.

PR #3 fixes deterministic software defects and protocol bugs that could be repaired without redefining strategy behavior. The items below are deferred so they can be implemented and validated independently.

---

## R24 — CSI1000 mixed-board price-limit execution is still too coarse

### Current behavior

CSI1000 backtests and retraining-frequency experiments still use a single scalar threshold:

```python
limit_threshold = 0.095
```

This is not sufficient for a mixed A-share universe.

CSI1000 can contain securities subject to different daily price-limit regimes, including approximately:

- Main board: 10%
- ChiNext: 20% after the 2020-08-24 reform
- STAR Market: 20%
- ST securities: special limits
- New listings: no regular price limit for the initial sessions under applicable rules

The repository already contains more detailed board/date logic in `board_rules.py`, but Qlib's standard `Exchange` path used by these backtests still receives one global scalar threshold.

### Risk

A uniform 9.5% threshold can incorrectly mark valid ChiNext / STAR trades as untradable and can distort turnover, fills, and return attribution.

The direction of bias is not guaranteed. It can either suppress profitable trades or suppress losing trades.

### Required fix

Implement an instrument-and-date-aware execution layer for backtests rather than a single scalar threshold.

Possible approaches:

1. Custom Qlib `Exchange` / tradability logic using `limit_threshold(stock, date)`.
2. Precomputed tradability masks injected into execution.
3. A custom execution simulator for the production candidate.

### Validation required

Re-run the production candidate and the 5/20/60 retraining-frequency experiment under the same board-aware execution rules.

Do not compare the new result directly with old scalar-threshold results without labeling the protocol change.

---

## R25 — Research fill price is not aligned with intended next-day execution

### Current behavior

The model score is generated from day T market data and Qlib's strategy consumes that signal on the following trading step.

However, the backtest currently uses:

```python
deal_price = "close"
```

Therefore the effective research convention is approximately:

```text
T close data
→ T score
→ execute on T+1 close
```

The intended live workflow is closer to:

```text
T close data
→ T score
→ execute on T+1 open / early session
```

### Risk

A T+1 close fill includes an additional full trading day's price movement relative to a T+1 open execution assumption.

This creates a target/execution mismatch and can materially affect measured alpha, especially for signals with short-term reversal or intraday drift.

### Required fix

Predeclare the production execution convention.

Recommended candidates:

- T+1 open
- T+1 VWAP over a defined early-session window
- T+1 close only if the actual production process explicitly trades near close

Then configure backtest execution to match that convention as closely as Qlib supports.

### Validation required

Re-run the production candidate under the selected fill convention.

Report both old close-fill and new execution-aligned results during migration.

---

## R26 — Synthetic equal-weight benchmark definition is not yet point-in-time correct

### Current behavior

The custom equal-weight benchmark builder reads instrument rows containing:

```text
symbol, start, end
```

but benchmark construction currently operates primarily on unique symbols and available close data.

The `start` / `end` membership spans are not fully enforced in the return matrix.

Suspended securities are also excluded from the daily valid-return cross section when either the current or previous close is missing.

### Risk

Two separate definition problems exist:

1. **Membership timing**
   - A stock can potentially contribute to historical benchmark returns outside its intended membership span if historical price data exists.

2. **Suspension handling**
   - Daily equal-weight averaging over only securities with calculable returns behaves more like a daily cross-sectional return index than a continuously held equal-weight portfolio.

This may be acceptable as a research reference index, but it is not automatically equivalent to an investable benchmark.

### Required fix

First choose and document the benchmark definition.

Recommended options:

**Option A — Point-in-time reference index**
- Enforce each instrument's `start/end` membership dates.
- Reweight across active constituents according to a documented rule.

**Option B — Investable equal-weight portfolio**
- Define rebalance schedule.
- Preserve weights through suspension according to explicit mark-to-market rules.
- Apply transaction costs if it is intended as a tradable comparator.

### Validation required

Rebuild the benchmark and re-run any historical experiments that use it.

The old and new benchmark series should be versioned separately.

---

## R27 — Live model cache still lacks full data-value revision fingerprinting

### Fixed in PR #3

The live cache now invalidates on stable runtime-lineage changes including:

- pyqlib version
- LightGBM version
- Alpha158 source fingerprint
- live retraining helper fingerprint
- audit helper fingerprint

This prevents stale models from surviving code or dependency semantic changes.

### Remaining gap

The cache does **not** yet fingerprint historical OHLCV value revisions.

If the upstream provider changes an old historical value while the latest calendar date and model configuration remain unchanged, the cached model may still be reused until the normal retraining cadence triggers.

### Risk

The cached model may have been trained on historical values that differ from the current local dataset.

The model pickle SHA only proves file integrity. It does not prove consistency with the currently mounted historical data.

### Required fix

Add a stable data-release fingerprint that does not force retraining merely because one new daily bar arrives.

Candidate designs:

1. Upstream release asset hash / release identifier.
2. Hash of a manifest containing file sizes + checksums for historical files excluding the newest bar.
3. Rolling historical checksum over a stable cutoff date.
4. Explicit provider version metadata written during data download.

### Validation required

Test three cases:

- new daily bar only → cache remains reusable;
- historical value revision → cache invalidates;
- code/dependency change → cache invalidates.

---

## R28 — Daily ranking output is still not a stateful executable portfolio

### Current behavior

The daily production path publishes a ranking CSV and chart.

It does not currently maintain the complete live state required to reproduce `TopkDropoutStrategy` behavior, including:

- current holdings
- cash
- holding age
- actual fills
- failed / partial orders
- suspended positions
- price-limit-blocked orders
- corporate actions
- previous rebalance state

Therefore:

```text
daily Top20 ranking != actual TopkDropoutStrategy portfolio
```

### Risk

A user following the published ranking manually can produce a materially different portfolio from the backtested strategy.

The difference grows over time because `n_drop` explicitly depends on current holdings.

### Required fix

Add a persistent paper-portfolio / order layer.

Minimum state:

```text
portfolio_date
cash
positions
shares
cost_basis
holding_days
pending_orders
last_signal_date
model_fit_asof
strategy_config
```

The daily pipeline should then emit:

```text
target ranking
current holdings
sell candidates
buy candidates
blocked orders
executed orders
post-trade portfolio
```

### Validation required

Run a forward paper portfolio before using the output as an executable trading instruction.

Backfill simulation should confirm that the stateful implementation reproduces Qlib strategy decisions under equivalent execution assumptions.

---

## Priority

Recommended implementation order:

| Priority | Item | Reason |
|---|---|---|
| P0 | R25 execution price alignment | Directly changes measured strategy return and live realism |
| P0 | R24 board-aware execution | Directly changes tradability and turnover in CSI1000 |
| P0 | R28 stateful portfolio/order layer | Required before ranking output can be treated as actionable strategy output |
| P1 | R26 benchmark definition | Important for excess-return interpretation, but does not change raw strategy P&L |
| P1 | R27 historical data fingerprint | Production reproducibility / lineage hardening |

## Governance

These items should not be silently folded into the historical record.

For each change:

1. Freeze the previous protocol and result artifact.
2. Record the exact execution / benchmark / cache definition change.
3. Re-run only the experiments whose interpretation depends on that change.
4. Keep old results labeled as legacy protocol rather than deleting them.
5. Do not present improved historical numbers as new independent out-of-sample evidence.

