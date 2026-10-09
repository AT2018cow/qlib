# 25 — CSI1000 Stage-B independent financial and execution audit plan (2026-10-09)

> **Status: BLOCKED at raw-data Gate A as of 2026-10-09.** This is a skeptical, read-only audit, not an endorsement of published Stage-B performance and not an authorization to tune the model. This plan is separate from the frozen handoff doc 24 and all production changes.

## 1. Frozen provenance and access

| Evidence | Pinned value | Availability / verification |
|---|---|---|
| main before audit branch | `dfa2481e9032192f1e135d677019d16f149f9f73` (2026-10-09T04:45:06Z) | GitHub ref checked |
| freeze commit | `7a2676397b0f8e6f69c0bffc98d1764647f644ac` | GitHub revision readable |
| full results | `results/csi1000_stage_b/stage_b_full_51756897fc752304.json` | Git blob `0e85e7bf0e0930978bd534785cdf09ac6815bfa9`, readable; NOT daily data |
| snapshot token | `51756897fc75230493194aef2e48815e4e9f3cc7426cc135f47bb8ba8e0d21e1` | JSON metadata |
| winner ID | `4e908173705c76fee3782be37068672a3a845bd61894202f3152afe3b9d81ef2` | JSON metadata |
| baseline ID | `23b92de05cf36c82998de684d0fbf64d81ee54490d755bd3cf96311c00286785` | JSON metadata |
| full window and reference phases | `2025-01-02..2026-09-30`, phases 0,4,6,10,15 | JSON claims 424 sessions; independently unverified |
| raw Stage-B report bytes | `/vol/csi1000_stage_b/<snapshot>/.../phaseXX/report.parquet` | **Unavailable through GitHub connection**; none of the Stage-B Parquet reports is in the repository tree |
| frozen per-file raw bytes SHA256 | `ranked_candidates[].phase_results[].report_artifact.sha256` | All ten metadata pointers exist; **actual SHA256 unchecked** |
| corresponding `signal_artifact`, `decision_artifact`, calendar and provider OHLC/limits | in frozen Volume/provider snapshot | Metadata pointers exist for signal/decision; underlying bytes not available |

**First target**: winner phase0 report
`/vol/csi1000_stage_b/51756897fc75230493194aef2e48815e4e9f3cc7426cc135f47bb8ba8e0d21e1/phases/4e908173705c76fee3782be37068672a3a845bd61894202f3152afe3b9d81ef2/phase00/report.parquet`.
Expected byte SHA256: `649b26fec1760abeec9923adaa68db7240bfa9fe9f4bb1488e573f9373d39da0`; expected content hash `f9d78bbbbc056d2af4bb15b6a0dc736721af29a19ea6d837281f1b2582f2832e`, 424 rows, nine columns `account,return,total_turnover,turnover,total_cost,cost,value,cash,bench`. **Metadata only**, not independently verified.

**Important nuance:** baseline phase0 artifact resides under `_preflight/.../repeat_b/phases/<baseline>/phase00/`, not the normal phase path; always follow the JSON pointer instead of reconstructing file locations. One unconnected GitHub report `results/freq_experiment/report_csi1000_freq20_...parquet` is NOT substitutable.

## 2. Order of work, evidence and adversarial questions

- **Gate A: provenance/calendar (winner phase0 first).** Obtain report bytes read-only; SHA256(byte stream) must exactly match metadata. Reject missing/misaligned columns, duplicate/unsorted dates, NaN/Inf, nonpositive account, inappropriate negative amounts, extreme returns, missing/holiday sessions, and silent `dropna()`. Compare row dates to actual provider calendar `calendars/day.txt`, not merely 424 or a guessed holiday list. Check the account's initial value and first day's P&L.
- **Gate B: independent accounting/metrics.** Do not import `portfolio_performance.py` or Stage-B helper. Let `A_{-1}=100,000,000`, `g_t=return_t-cost_t`, `a_t=A_t/A_{t-1}-1` and `b_t=bench_t`. Independently reconcile each date `a_t-g_t`. Compute `mean/std(ddof=1)`, `Sharpe=mean(g-rf_d)/sd(g-rf_d)*sqrt(238)` with `rf_d=(1+rf_annual)^(1/238)-1`, official `rf_annual=0`; `IR=mean(g-b)/sd(g-b)*sqrt(238)`; `vol=sd(g)*sqrt(238)`. Strategy NAV `A_t/100m`, benchmark NAV `cumprod(1+b)`, relative NAV quotient. Total return `NAV_final-1`; CAGR `NAV_final^(365.2425 / elapsed_days_since_2025-01-02)-1`; MDD `min(NAV_t/max(1, NAV_0..NAV_t)-1)`, explicitly including initial 1.0. Record all ten original vs recomputed values and daily reconciliations; verify turnover and cost-rate sums separately from currency-denominated `total_cost`, after checking how Qlib defines that field.
- **Gate C: inflation / robustness.** Sensitivity only: annual sessions 238 vs 252, annual rf 0 vs 2%, ddof=1 vs ddof=0; subperiod Sharpe and mean; single-day P&L outliers; benchmark Sharpe, beta and correlation; benchmark/date alignment and corporate-action factor, any missing-loss-day effect, and look-ahead price logic. These are diagnostics, not parameter tuning.
- **Gate D: legal execution and leakage.** Trace frozen T-close signal to T+1-open decisions and fills/holding cash via Qlib decision artifacts and provider prices; Top20/Drop2, open buy cost 5bp, sell 15bp, minimum CNY5, statutory price-limit tick, board/listing dates, ST risk-warning, suspension and unavailable price handling. Sample first and last days, every model retrain boundary, largest win/loss, maximum turnover, limit/suspension dates. Independently verify `Ref($close,-20)` label fully matured before fit, correct purge of train and validation, processor fit cutoffs, point-in-time CSI1000 universe and data-provenance timestamp. Code path plausibility is not trade replay.
- **Expansion:** only after winner phase0 passes A/B, inspect winner 4/6/10/15 and baseline 0/4/6/10/15. Five phases share the same calendar period; they are not five independent market regimes. Do not touch a model fit.

## 3. Frozen tolerances and verdict policy

1. File hash **exact SHA256 bytes equality**. Schema = nine named fields, all expected trading dates exactly, zero duplicate/missing/nonfinite records unless individually evidenced and quantified; missing evidence = `BLOCKED`, confirmed defective source = `FAIL`.
2. Daily report-vs-account return max absolute difference <= `1e-10`; print differences, not only max. For six-decimal frozen financial metrics require rounded-to-six matches (at most `1e-6` absolute to accommodate last-digit implementation/rounding), and eight-decimal means <= `1e-8`. Unexpected differences are findings; do not widen tolerances retroactively. If zero standard deviation, Sharpe and IR are undefined, not artificially zero.
3. Never equate `sum(cost)` (fractional daily returns) with cash brokerage fees; compare to `total_cost` and cash/positions only after establishing field units. Test fee schedules and minimum fees from order-level provenance.
4. Separate verdicts: `metric_formula` PASS needs ALL ten raw validated daily reports and reconciled metric comparisons. `daily_account` PASS needs daily account value/fee/value/cash reconciliation. `execution_integrity` PASS needs independent time-aligned fill, pricing and label checks. `overall` PASS needs all three with no material unresolved defect. `BLOCKED` means required evidence/permission unavailable; `INCONCLUSIVE` means evidence exists but is insufficient/conflicting; `FAIL` means reproduced material defect. **Future return reliability is not implied by any historical PASS.**
5. Any confirmed error: minimal reproduction, affected days/phases and pre/post-metric deltas first; then separate small repair PR. No change to canonical Stage-B selection/history, parameters, Modal production, cron, paper state or website by this audit PR.

## 4. Estimated resource consumption

| Tier | Execution | Budget |
|---|---|---|
| 0, static | GitHub text/JSON and source review | No GPU, no training, no paid Modal invocation |
| 1, financial | Phase0 Parquet SHA+CPU-only recomputation, expand to 10 reports | 4,240 reported daily rows in aggregate; basic pandas/pyarrow CPU, small RAM; wall time typically seconds/minutes after bytes accessible; file sizes unknown |
| 2, targeted execution | Only read-only signal/decisions/price checks on evidence-based days | Bounded small sample; read-only remote data access if explicitly available; no retraining |
| 3, expensive | Full replay/training | Not authorized |

**Raw access unblock:** provide a read-only export of the frozen report Parquets and their signal/decision counterparts, plus the original provider trading calendar and relevant price/limit snapshot, or grant a safe read-only artifact retrieval capability. Never disclose credentials/private workspace IDs in this public repository.

## 5. Reproducibility and current state

- Baseline source files reviewed: `portfolio_performance.py`, `csi1000_stage_b.py`, `csi1000_stage_b_core.py`, `board_execution.py`, `qlib_audit_fixes.py`, `AGENTS.md`, handoff 24.
- Static watchlist, **NOT confirmed runtime bugs**: `portfolio_performance` drops incomplete `return/cost/bench` rows before calculating; `research_exchange` supplies `deal_price=open` and 5/15bp cost; Stage-B runner calls it without an explicit historical ST-symbol list; `board_execution` therefore defaults that override to empty; source label purge uses `last_matured_sample`. Each requires raw-day, provider/universe and order verification.
- Frozen JSON asserts `account_return_max_error=0`; this is a saved computation, not a newly validated daily-account check.
- **Current verdict:** `metric_formula=BLOCKED`, `daily_account=BLOCKED`, `execution_integrity=BLOCKED`, `overall=BLOCKED` due to missing Volume-backed artifacts. No confirmed discrepancy or repair authorization.
