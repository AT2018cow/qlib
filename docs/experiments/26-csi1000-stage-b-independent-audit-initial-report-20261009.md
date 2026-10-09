# 26 — CSI1000 Stage-B independent audit: initial read-only findings (2026-10-09)

**Verdict: BLOCKED**. No Stage-B raw report bytes were obtainable through the connected GitHub repository; **zero** of ten original Parquet byte SHA256 hashes have been reverified. Published Sharpe/CAGR values have **not** been independently reconstructed. Do not cite this audit as validation of published returns.

## Execution log and provenance

- Pinned repository state prior to audit branch: `main@dfa2481e9032192f1e135d677019d16f149f9f73`. Main merged audit handoff PR #43 at 2026-10-09T04:45:06Z.
- Frozen historical result: `7a2676397b0f8e6f69c0bffc98d1764647f644ac:results/csi1000_stage_b/stage_b_full_51756897fc752304.json`, Git blob `0e85e7bf0e0930978bd534785cdf09ac6815bfa9`.
- Evidence manifest: `audit/evidence/csi1000_stage_b_frozen_manifest_20261009.json`: original frozen metrics, all ten paths, SHA256 byte expectations, content hashes, candidate IDs and independent verification flags. **Values are copied from original result JSON solely as targets to falsify.**
- Original Parquets live under frozen research Volume `/vol/csi1000_stage_b/<snapshot>/...` and do not appear in the repo tree; live Volume is not exposed by the current GitHub access. The separate `results/freq_experiment/` file is not Stage-B evidence.
- Independent executable: `audit/independent_stage_b_metrics.py`, with no imports from production metric, runner or Qlib. A frozen calendar is mandatory for full formula PASS; uses exact file bytes SHA256, rejects missing/duplicate/NaN rows, reconciles account/net returns and Qlib's cumulative absolute costs/turnover, recomputes historical metrics. Synthetic tests: `tests/test_independent_stage_b_metrics.py`.
- Local isolated unit run (not Stage-B): 3 synthetic tests passed on account return, SHA tampering, missing calendar gate; no parquet data/production credentials were consumed. Results do **not** prove any Stage-B outcome.

## Static adversarial review: grounded facts vs unresolved risks

| Finding | Source / evidence | Nature and consequence |
|---|---|---|
| After-cost Sharpe uses `return - cost`, `ddof=1`, annual 238; IR uses daily strategy minus benchmark; CAGR uses actual calendar elapsed time | `portfolio_performance.py` | **Confirmed formula definition**. Using 252 instead of 238 changes Sharpe and volatility even if no bug. Must compare original convention first. |
| `report[['return','cost','bench']].dropna()` silently shortens sample if any source column is NaN | `portfolio_performance.py`, before Sharpe | **Unresolved inflation risk**. 424 summary rows are not independent proof of complete dates; original report needed to quantify. |
| Qlib report `total_cost` and `total_turnover` are **cumulative currency totals**, while `cost` and `turnover` are daily normalized rates. Account `= cash + value` | `qlib/backtest/account.py:250-291`; `qlib/backtest/report.py:189-215` | **Verified source semantics**. Audit must difference cumulative amounts and divide by prior account, not sum `total_cost` records. Monetary cost must reconcile to 5/15bp/min CNY5 orders. |
| Frozen Stage-B runner calls `research_exchange` with `deal_price=open` and buy/sell 0.0005/0.0015, min 5 | `csi1000_stage_b.py:448-497`; `board_execution.py:269-301` | **Configured intention only**, not proof that a particular order was legally fillable or incurred the right cost. |
| Stage-B runner does **not pass** `st_symbols`; exchange defaults to an empty list | `csi1000_stage_b.py:488-492`; `board_execution.py:269-299`; `board_execution.py:115-126` | **Specific conditional execution risk**: historical ST stocks with reduced limits may be misclassified if they occur in universe/order flow. Need actual universe, ST histories and sampled orders to prove impact; **not currently a confirmed error in returns**. |
| Board suspension mask uses execution day's `$close` to form limit masks | `board_execution.py:222-244`; `qlib/backtest/exchange.py` | **Timing/availability concern**, not proven look-ahead. Must sample days with missing open, present close or vice versa, and verify information available at decision/execution time. |
| Deterministic strategy explicitly requests a signal from previous calendar step (`shift=1`) and computes T+1 trade orders | `deterministic_strategy.py:49-53, 108-176` | **Expected scheduling in static code**. Need actual per-day signal timestamps, prices and decisions before claiming no leakage. |
| Stage-B retrain code restricts validation cutoff using `LABEL_HORIZON=20`; calls `last_matured_sample` and `purge_cfg_splits` | `csi1000_stage_b.py:793-852`; `qlib_audit_fixes.py:24-81` | **Static guard exists**. Need frozen calendar, actual feature handlers, fitted processors and data snapshots to establish there was no label/feature or universe leakage. |
| Frozen report's `account_return_max_error=0` | `ranked_candidates[].phase_results[]` | **Self-reported consistency**, not an independent ledger or execution result. |
| Five winner phases use the same 2025–2026 tail | Handoff 24 and frozen manifest | **Selection/regime risk**. Backtest formula PASS does not imply credible future Sharpe. |

## Frozen metric matrix — **not recomputed**

All recalculated fields and all numerical deltas are **N/A**, not zero. This table is only a reconciliation target; complete original target values including return, relative CAGR, cost and volatility are preserved in the evidence JSON.

| Cohort | Phase | Saved Sharpe | Saved IR | Saved annual vol | Saved CAGR | Saved max DD | Independently recomputed |
|---|---:|---:|---:|---:|---:|---:|---|
| winner | 0 | 1.276262 | 0.687576 | 0.214666 | 0.293178 | -0.121268 | N/A — BLOCKED |
| winner | 4 | 1.334093 | 0.640982 | 0.210544 | 0.303561 | -0.105777 | N/A — BLOCKED |
| winner | 6 | 1.109734 | 0.390042 | 0.205029 | 0.235523 | -0.116128 | N/A — BLOCKED |
| winner | 10 | 1.395196 | 0.733641 | 0.202383 | 0.307289 | -0.120782 | N/A — BLOCKED |
| winner | 15 | 1.177063 | 0.365065 | 0.201244 | 0.248637 | -0.146542 | N/A — BLOCKED |
| baseline | 0 | 1.122058 | 0.356593 | 0.197482 | 0.230149 | -0.096486 | N/A — BLOCKED |
| baseline | 4 | 0.848922 | -0.112234 | 0.153262 | 0.128643 | -0.128443 | N/A — BLOCKED |
| baseline | 6 | 1.523323 | 0.848577 | 0.196792 | 0.332183 | -0.117009 | N/A — BLOCKED |
| baseline | 10 | 1.332491 | 0.606024 | 0.201240 | 0.289060 | -0.128344 | N/A — BLOCKED |
| baseline | 15 | 1.450837 | 0.766764 | 0.207490 | 0.331489 | -0.102897 | N/A — BLOCKED |

One should not confuse a best-case phase with the frozen all-phase selection rule: baseline phase6 has higher recorded Sharpe than winner phase6, and the reverse holds in other phases. **No unverified numeric differences are interpreted as proof.**

## Budget and unblocking criterion

Current stage spent no model-fit/GPU/Modal-production calls, accessed only repository text/JSON, and ran three CPU-only *synthetic* unit tests. After a read-only export of the original frozen Parquet bytes and provider calendar, phase0 byte SHA256 + independent 424-row financial check should take little CPU; expand to ten files (4,240 stated rows total). Independent fill-level adjudication also needs signal/decision history, T/T+1 price/factor series, universe and ST/listing history; do not silently skip it. See plan 25 for precise tolerances and formulas.

### Per-domain verdicts

| Scope | Verdict | Reason |
|---|---|---|
| `metric_formula` | **BLOCKED** | No original report bytes; can't check SHA256, 424 dates, NaNs or recompute Sharpe/IR/CAGR/MDD |
| `daily_account` | **BLOCKED** | No original daily account/cash/value/cumulative fee path |
| `execution_integrity` | **BLOCKED** | No frozen signal/decisions/fill-price history and point-in-time universe |
| `future_returns` | **INCONCLUSIVE** | Consumed selection/confirmation interval, no independently fresh forward validation |
| `overall` | **BLOCKED** | Basic prerequisites absent |

**No confirmed result-raising bug; no correction PR yet.** This document/branch is an audit-plan and independent diagnostic PR only. Never treat a code-level suspicion as a validated defect in the frozen Stage-B metrics.
