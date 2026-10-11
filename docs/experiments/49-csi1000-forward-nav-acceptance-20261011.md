# CSI1000 paper NAV close-out acceptance (PR #63, 2026-10-11)

Scope: the **public forward simulated cumulative-return panel**, without
changing the frozen Stage-B evidence, model selection, 100m CNY paper account,
broker status or depth-free full-fill assumption.

## Three separate acceptance statements

| Status | Evidence | Meaning |
|---|---|---|
| CI / fixture passed | Fast `unified-paper-nav.yml` tests incl. tampering | Only code contract, **not** production |
| NOT_STARTED | No actual post-2026-10-12 paper settlement | No made-up first NAV; correct before first source bar |
| BLOCKED_INDEPENDENT_SOURCES | Paper ledger exists, no separately sourced calendar/quotes | Source correctness cannot be claimed |
| HALT_SOURCE_REVIEW_REQUIRED | Dated full-session halt carries appear | Mark arithmetic checked; human must authenticate halt notice |
| PASS_ACCOUNTING_CALENDAR_AND_EXTERNAL_QUOTES | Full raw settlement ledger, published JSON, independent calendar and adjusted quote/factor CSV reconcile | Audited **paper** cumulative return, never broker fill certification |

## Code and data contract

1. Pre-trade opening NAV is now independently checked as
   `opening_cash + sum(opening_shares * execution_day_open_mark)`. Every
   opening mark is stored in `execution_report.opening_marks`, not inferred
   from closing holdings or an arbitrary inherited initial cash.
2. Post-trade closing NAV uses `cash + sum(position_shares * close_mark)`;
   all simulated buy/sell fills, commissions and share movements must conserve
   the ledger to a cent.
3. On an unavailable held open/close, the cron only carries the previously
   marked price if **both** open and close are missing and a matching, dated
   **full-session** suspension record is returned by
   `akshare.stock_tfp_em(date=YYYYMMDD)`. Store the date range, provider,
   UTC observation time, original reason and canonical row hash in
   `execution_report.halt_evidence`. An empty feed, missing cutoff date,
   intraday halt, partial provider quote or inconsistent evidence **fails
   closed**. This feed might not cover multi-day suspensions, so an observed
   long halt can still block settlement. The pipeline must not convert a
   provider hole into a certified halt.
4. Each new canonical daily paper artifact freezes trading sessions from
   `akshare.tool_trade_date_hist_sina` through its actual publication day.
   The forward builder reconciles those snapshots, demands a report for every
   trading session from the **first actually verified** forward settlement
   through the latest settlement, and also checks pending-order execution
   dates. Calendar snapshots must extend without rewriting older dates.
   No assumption that weekend/holiday days have executions.
5. `scripts/accept_forward_nav.py` compares the public JSON byte-decoded
   content with regeneration from immutable paper reports, then checks the
   observed exchange sessions against an **operator-supplied separate
   exchange calendar**, and finally matches every fill/open/close mark and
   recorded buy factor against an **independent daily bar CSV normalized to
   the same Qlib-adjusted coordinate system**. The operator must inspect
   quote origin/adjustment provenance separately; a user-controlled CSV is
   not an intrinsic independent source certificate.

## Actual production smoke after a genuine forward settlement

The repository/CI **cannot** read private Modal deployments, credentials,
full market data or independently certify the quote source. The first
eligible usage day is **2026-10-12** (Asia/Shanghai). Legacy 2026-10-09
orders may settle at the 2026-10-12 refresh but do not form post-start
returns; the 2026-10-12 pending order ordinarily needs the **subsequent**
provider refresh to produce a first valid close. Do not call a still-pending
order a failed ledger or manufacture a retroactive close.

1. Operator records the deployed `main` SHA, Modal application/deployment
   SHA, schedule, most recent logs, provider fingerprint/last complete bar,
   ST pre-open snapshot, Top20 / `_mvp_metadata.json`, pending orders, state
   idempotency and Pages deployment. Code merge **does not** deploy Modal.
2. Obtain a real `usage_date >= 2026-10-12` canonical daily paper artifact
   with `execution_report.date >= 2026-10-12`, including
   `valuation_verified=true`, opening/closing marks, dated halts if used,
   source calendar and matching `csi1000_forward_performance.json`.
3. Obtain independent CSV inputs as the **actual** observed data; do not
   synthesize or use the same provider export and describe it as independent:
   - `exchange-calendar.csv`: header `date`, one ISO YYYY-MM-DD A-share
     trading session per row, sorted and complete through the latest NAV date;
     source/precision/collection timestamp recorded out of band.
   - `adjusted-bars.csv`: header
     `date,instrument,open,close,factor`, in the *same adjusted coordinate*
     as the Qlib execution report. Source and the independently applied
     split/dividend normalization must be documented. Each traded symbol
     and every opening/closing-marked symbol needs a matching record;
     full-day halt carries are reviewed separately.
4. Run this from a checkout with the relevant real public artifacts:

```bash
python scripts/build_forward_performance.py \
  --signals results/signals --output /tmp/regenerated-forward.json

curl -fsSL https://at2018cow.github.io/qlib/signals/csi1000_forward_performance.json \
  -o /tmp/public-forward.json

python scripts/accept_forward_nav.py \
  --signals results/signals \
  --forward /tmp/public-forward.json \
  --calendar-csv /tmp/exchange-calendar.csv \
  --quotes-csv /tmp/adjusted-bars.csv \
  --calendar-source 'REVIEWED_PROVIDER_AND_FETCH_RECEIPT' \
  --quote-source 'INDEPENDENT_OHLC_FACTOR_PROVIDER_AND_RECEIPT' \
  --output /tmp/csi1000-forward-nav-acceptance.json \
  --require-pass
```

A true verified day and matching external inputs are required for status
`PASS_ACCOUNTING_CALENDAR_AND_EXTERNAL_QUOTES`. Any missing external input
is `BLOCKED_INDEPENDENT_SOURCES`, a pre-valuation run is `NOT_STARTED`,
and real quote/calendar/NAV discrepancies are `FAIL`. `--require-pass`
uses a nonzero exit for anything but the specified PASS. Recorded third-party
source names do not prove data-source independence without an operator
checking the receipts. Certified halt carry marks return
`HALT_SOURCE_REVIEW_REQUIRED` pending separate human evidence review.

Finally, open the **real** deployed site at desktop and mobile widths; confirm
the visible cumulative return and latest valuation date against the accepted
JSON and that invalid/missing data hides numeric returns. The repository's
synthetic Chromium screenshot job cannot substitute for this step. Save the
release SHA, dates, source receipts, commands/logs/JSON, and browser checks
with the operator's release attestation. No change to canonical strategy
selection is authorized here.

## Hard stop

Do not re-open the 55-cell historical Stage-B accounting campaign, promote
Baseline, retrofit simulated historical results into forward cumulative
return, or claim authenticated broker fills. This PR simply ensures the
forward simulated-return accuracy can be checked in one bounded real-data
acceptance procedure.
