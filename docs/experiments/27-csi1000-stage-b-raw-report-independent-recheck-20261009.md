# 27 — CSI1000 Stage-B independent audit update: GitHub raw Parquets (2026-10-09)

> **Raw report SHA256: PASS (10/10). Frozen numeric metric recalculation: PASS (10/10). Daily internal financial ledger: PASS (10/10). Historical provider calendar and execution verification: BLOCKED. OVERALL: BLOCKED.** This supersedes the raw-evidence availability finding in initial report 26, but does **not** overrule its unverified execution risks.

## Evidence provenance and independence

- GitHub `main@5418956d9858663613d9e9eaedf614d97da019c1` added ten authentic-looking binary Parquets under `results/csi1000_stage_b/audit_reports/` and a file mapping manifest, with no changes to the frozen full-results JSON.
- Frozen original result commit remains `7a2676397b0f8e6f69c0bffc98d1764647f644ac`, snapshot `51756897fc75230493194aef2e48815e4e9f3cc7426cc135f47bb8ba8e0d21e1`.
- All ten original files were fetched as Base64 from GitHub and decoded to raw bytes **independently**. Independent SHA256 code was checked against the standard `abc` SHA256 test vector; **10/10 actual byte hashes matched** the `report_artifact.sha256` in the frozen result and export manifest, not only the manifest's assertion.
- The raw data were decoded using a separate JavaScript implementation of Parquet (Thrift compact footer/page headers, Snappy decompression, dictionary/RLE decoding) and explicit arithmetic. This did **not** import `portfolio_performance.py`, Stage-B audit helpers, Qlib, pandas, or the production/research runner. The corresponding Python read-only regression tool is `audit/independent_stage_b_metrics.py`; `audit/run_stage_b_github_export.py` and the optional GitHub Action were added for independently repeating the check in an authorized checkout. No GitHub Actions run has yet been observed, so **CI passing is not claimed**.
- Exact per-phase independent values, source SHA256 pointers, saved values, absolute errors, and tolerances: `audit/evidence/csi1000_stage_b_github_recheck_20261009.json`. The originally frozen evidence/metrics remain in `audit/evidence/csi1000_stage_b_frozen_manifest_20261009.json`.

## Actual-source integrity findings

- Winner phases `0,4,6,10,15` and baseline phases `0,4,6,10,15` each have exactly **424 dates**, 2025-01-02 to 2026-09-30. Date indexes are increasing and unique, all ten indexes coincide, with zero weekend dates; all decoded column values were finite, with zero Parquet null-definition entries. These facts do not substitute for verifying **the frozen provider's original trading calendar** or historical universe.
- Nine report columns are `account,return,total_turnover,turnover,total_cost,cost,value,cash,bench`. All numeric source metrics checked against frozen JSON match within prescribed rounding tolerances: 6-decimal metric comparisons absolute error <= `1e-6`; 8-decimal daily-mean comparisons <= `1e-8`.
- For every daily bar, independent `A_t/A_(t-1)-1` (starting from initial CNY100m) reconciles with `return-cost`, maximum absolute error ~`1.11e-16`. `account - cash - value` absolute error <= `1.49e-8` CNY.
- Cumulative absolute `total_cost` and `total_turnover` daily differences divided by prior account reconcile with `cost` and `turnover` report rates, respectively. This is an **internal ledger identity**, not independent order-by-order proof that fee schedules or fills were correct.
- Phase0 winner: `Sharpe=1.276261947632427`, `IR=0.6875763247331149`, annual vol `0.2146660500785635`, strategy CAGR `0.29317813686960914`, MDD `-0.12126777424584445`, total return `0.5647021525309217`; mean daily net `0.0011511349208564185`, daily std `0.013914733298116025`, fractional daily cost sum `0.023593264939625692`; all agree with saved values to rounding. Last cumulative *absolute* cost value is ~CNY 2,570,970.46 (different units from the fractional sum).

## Saved versus independently recalculated values

| Cohort | Phase | Saved Sharpe | Recalculated Sharpe | Saved IR | Recalculated IR | Saved CAGR | Recalculated CAGR | SHA256 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Winner | 0 | 1.276262 | 1.276261948 | 0.687576 | 0.687576325 | 29.3178% | 29.317814% | PASS |
| Winner | 4 | 1.334093 | 1.334092569 | 0.640982 | 0.640982212 | 30.3561% | 30.356122% | PASS |
| Winner | 6 | 1.109734 | 1.109733838 | 0.390042 | 0.390041633 | 23.5523% | 23.552335% | PASS |
| Winner | 10 | 1.395196 | 1.395195891 | 0.733641 | 0.733641447 | 30.7289% | 30.728884% | PASS |
| Winner | 15 | 1.177063 | 1.177062658 | 0.365065 | 0.365065458 | 24.8637% | 24.863697% | PASS |
| Baseline | 0 | 1.122058 | 1.122057878 | 0.356593 | 0.356593138 | 23.0149% | 23.014869% | PASS |
| Baseline | 4 | 0.848922 | 0.848922128 | -0.112234 | -0.112234187 | 12.8643% | 12.864337% | PASS |
| Baseline | 6 | 1.523323 | 1.523323125 | 0.848577 | 0.848577020 | 33.2183% | 33.218292% | PASS |
| Baseline | 10 | 1.332491 | 1.332490875 | 0.606024 | 0.606024164 | 28.9060% | 28.906032% | PASS |
| Baseline | 15 | 1.450837 | 1.450837314 | 0.766764 | 0.766764173 | 33.1489% | 33.148895% | PASS |

Complete per-field comparisons including annual volatility, returns, drawdowns, turnover and cost are machine-readable in the JSON evidence file. Max absolute six-decimal comparison error among these checks was `4.993741172659716e-7` (below `1e-6`); maximum eight-decimal daily-mean error was `4.92085641852491e-9` (below `1e-8`).

## Remaining failure-mode investigations (not waived)

1. **Frozen calendar missing:** all ten date sequences coincide, but a copy of original `/vol/cn_data/calendars/day.txt` from the same provider fingerprint is needed before formally declaring calendar completeness PASS. A consistent omission across all ten files would not be caught by cross-file comparison.
2. **Signal/decision and fills missing:** an account/report perfect equality may occur even if pricing and tradability assumptions are unreal. Retrieve original `signal_artifact` and `decision_artifact` bytes and samples of holdings/orders/provider OHLC/factors; verify previous-day T-close signal, T+1-open execution and independent fee calculations.
3. **Time-aware ST/price limits:** Stage-B runner does not explicitly supply `st_symbols` into `research_exchange`; prove actual ST exposure and effect on possible fills before calling this a bug.
4. **Suspension/missing prices:** verify treatment of execution-day close-derived availability, opening price gaps and revised adjustments; ensure no future data used before open and no silent omission of extreme losing days.
5. **Labels/market universe:** verify 20-session label maturation, purge, model and feature fitting timestamps, point-in-time CSI1000 constituents against original provider snapshot, not merely code guards.
6. **Selection and future credibility:** five phases are retraining-calendar perturbations over the **same consumed tail**, not independent OOS cycles. They cannot establish a future Sharpe guarantee.

## Updated verdict and cost

| Domain | Decision | Reason |
|---|---|---|
| Raw report bytes SHA256 | **PASS** | Ten independent SHA256 computations against frozen manifest |
| Internal report schema, dates and finite values | **PASS** | Ten matching unique 424-date sequences, no weekend/null/NaN/Inf; **external provider calendar not checked** |
| Given-rows saved numeric Sharpe/IR/CAGR/vol/MDD/cost arithmetic | **PASS** | Recomputed independently for winner and baseline 5 phases each |
| Formal `metric_formula` per plan 25 | **BLOCKED** | Preregistered formal acceptance also requires original provider calendar |
| Internal `daily_account` ledger | **PASS** | Daily account/net/cash/value/cost/turnover identities |
| `execution_integrity` | **BLOCKED** | Historical signal/orders/fills/prices/universe not available |
| Future returns credibility | **INCONCLUSIVE** | Historical confirmation and model selection already consumed |
| **Overall** | **BLOCKED** | No end-to-end execution integrity verification |

No defect has been demonstrated that requires a repair PR. No model tuning, Modal invocation or production changes occurred during this verification. The small optional GitHub Actions job is confined to this audit branch; do not interpret its existence as an executed/passing check.
