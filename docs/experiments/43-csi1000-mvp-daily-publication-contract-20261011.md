# CSI1000 research MVP daily publication contract

Status: implementation runbook for the forward research MVP.  The sole
handoff entry remains
`docs/experiments/42-csi1000-mvp-handoff-after-ledger-pass-20261011.md`.

## Scope

This runbook starts after the Audit Closure Register.  Historical audit is
closed.  Do not add historical candidates, re-rank the consumed Stage-B tail,
change the canonical profile, or retune model parameters from this workflow.

The daily MVP gate is limited to:

1. exact prior-session provider freshness;
2. point-in-time CSI1000 membership at the signal-data date;
3. 20-session training/validation label maturity;
4. frozen winner model and full-ranking/Top20 lineage;
5. two-phase paper state;
6. explicit ST/suspension uncertainty;
7. static-site publication and a lightweight end-to-end contract smoke.

## Daily immutable artifacts

For usage date `U`, the canonical cron publishes one commit containing:

- `U_top20_lgb158.csv`
- `U_chart.json`
- `U_paper_portfolio.json`
- `U_mvp_metadata.json`

The metadata sidecar binds the first three artifacts to provider fingerprints,
PIT membership, label maturity, frozen production lineage, the pre-display
paper ranking, and current risk status.  Existing historical artifacts are not
rewritten to manufacture missing metadata.

## Fail-closed gates

Publication must stop if any of these fail:

- the canonical trading calendar is unavailable;
- provider `calendar_end` is not the exact prior trading session;
- the CSI1000 span file does not produce exactly 1000 active members at the
  data date;
- a displayed or paper-ranked symbol is outside that point-in-time universe;
- the stored train/validation split cannot prove all 20-session labels matured
  before the next stage;
- frozen candidate/profile/model hash lineage differs from the canonical
  Stage-B winner;
- paper dates or lineage are not bound to the same signal publication.

ST and execution-day suspension status are different: they are observable
risk boundaries, not an excuse to invent a fill.  If the current ST source is
unavailable, the sidecar says so.  Pre-open suspension/tradeability remains
not independently certified; the website must not translate that state into
"tradable".

## Operational checks

The deployed App name in this repository is `qlib-cn-daily-a10g`.  Use the
authorized Modal environment explicitly; do not infer deployment health from
the source decorator alone.  Current Modal CLI commands for the first
trading-day publication after a code change are:

```bash
# Replace <env> with the authorized Modal environment.
modal app info qlib-cn-daily-a10g --env <env> --json
modal app history qlib-cn-daily-a10g --env <env> --json
modal app logs qlib-cn-daily-a10g --env <env> --since 2h --timestamps

# Data/model preflight only: downloads the current provider package, evaluates
# freshness/PIT/Label20/frozen lineage and returns the ranking payload, but does
# not perform the GitHub publication transaction.
modal run modal_qlib_cn_a10g.py::daily_standalone --env <env> --timestamps

# Deploy the reviewed source. The schedule in source remains 07:00
# Asia/Shanghai; verify it again with "modal app info" after deployment.
modal deploy modal_qlib_cn_a10g.py --env <env> --stream-logs --timestamps

# Real publication smoke. This invokes the same daily_cron path as the
# scheduled job. Same-date output is immutable/idempotent: byte-identical
# artifacts are retained; drift is rejected rather than overwritten.
modal run modal_qlib_cn_a10g.py::daily_cron --env <env> --timestamps
```

For every run, verify all of the following before declaring the daily service
healthy:

1. `main` contains the intended reviewed deployment commit;
2. `modal app info` reports the expected App/functions and the deployed
   source still registers `daily_cron` at 07:00 Asia/Shanghai;
3. the run logs show the canonical freshness gate, PIT count = 1000,
   20-session label maturity and frozen lineage checks passing;
4. the latest successful GitHub publication commit contains all four same-date
   artifacts;
5. the Pages deployment is triggered from that signal publication chain and
   the latest date renders the MVP contract/risk state;
6. the paper artifact has pending signal date = provider data date, execution
   date = usage date, with any earlier execution report kept separately.

If publication fails, do not write a replacement signal manually.  Fix only
the failed current gate.  If source changed, redeploy and re-check
`modal app info/history`; then rerun the same usage date through
`daily_cron`.  The same-date immutability check will accept byte-identical
existing files and fill only missing files, or stop on content drift.  If a
deployment itself is bad, use the deployment history to identify a known-good
version before considering `modal app rollback`; never use rollback to bypass
a failed data/PIT/label/lineage gate.

## Smoke sequence

The lightweight CI smoke uses synthetic point-in-time membership and checks
negative controls for non-PIT symbols, immature labels, wrong model hash,
missing ST/suspension boundary, and paper date drift.

The real-data smoke is intentionally forward-only:

- first leg: one complete prior-session bar -> Top20 + metadata + paper pending
  -> Pages;
- second leg: on the next provider refresh, settle that pending order from the
  recorded execution-day open and publish the resulting paper report.

As of 2026-10-11, the second leg cannot be truthfully completed for the
2026-10-12 forward start before the relevant market data exists.  Keep it
pending rather than fabricating a fill.


## Conditional ST transport-outage fallback (proposed 2026-10-13; forward only)

ST is **enabled by default**. Before planning an execution-day pending
order, `daily_cron` attempts `akshare.stock_zh_a_st_em` twice and verifies
non-empty rows, six-digit security codes, date of capture and capture time
strictly earlier than 09:30 Asia/Shanghai. A valid pre-open snapshot is frozen
with the pending under `execution_st_policy=ST_VERIFIED_PREOPEN`, including
source, matched ST securities, source-row count and timestamp. The next real
provider refresh uses the saved snapshot to apply ST-specific statutory
limits, never a current-day retroactive ST list.

**Only genuine transport failure** (connection aborted/disconnected, timeout,
or 502/503/504 upstream gateway HTTP error) permits a forward
`ST_IGNORED_REFERENCE_ONLY` pending to be created. The ST snapshot remains
`null`; public metadata records `NOT_CHECKED_REFERENCE_ONLY`,
`failure_kind=TRANSPORT`, unknown (null) ST count and the source exception.
It does NOT assert that zero holdings are ST, nor certify trading.
Empty data, missing/invalid columns/codes, unknown HTTP failures, stale or
post-open observations still fail closed.

The reference-mode pending ignores ST-special price limits **only for that
specific execution date**. All other provider freshness, CSI1000 PIT=1000,
Label20, frozen winner lineage, actual adjusted OHLC/factors,
non-ST statutory limits, independently dated whole-session halt/quote
evidence, fees and cash/position ledger verification remain unchanged.

Each settled forward NAV point binds its `execution_st_policy`.
The cumulative research paper curve may combine verified and transport-fallback
days **only with each day's mode and fallback dates disclosed**; it must never
silently blend an unknown/unversioned legacy policy. This is not actual
exchange fill certification. When the ST source recovers, the next day's
pre-open snapshot is used automatically with the original ST rules.

**Temporal gate:** A new paper pending must be created on its actual execution
date, before 09:30 China time. The failed 2026-10-12 run cannot be backfilled
or represented as a pre-open order now. The earliest forward NAV point is the
first genuine future execution session after a passing deployment, and its
true inception date is displayed. Do not rerun old failed dates to manufacture
paper settlements or forward performance. No historical audits or tuning are
reopened.
