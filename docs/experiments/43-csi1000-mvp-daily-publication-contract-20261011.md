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


## Forward reference-only ST policy (change proposed 2026-10-12; not a historical rewrite)

The approved research website return series is now **reference-only** and
**does not model ST designation or ST-specific price limits**. This is a
forward execution-model assumption, NOT a change to the frozen Stage-B winner
or its score/ranking, PIT=1000, Label20, model lineage, provider fingerprint,
calendar freshness, non-ST price limits, real adjusted OHLC/factors, dated
whole-session suspension evidence, or the cash/NAV accounting engine.

The canonical `daily_cron` does not fetch `akshare.stock_zh_a_st_em`.
Instead every new forward paper order is marked
`execution_st_policy=ST_IGNORED_REFERENCE_ONLY`, with
`execution_st_snapshot=null`, `reference_plan_created_at_cst` and
`risk.st.status=NOT_CHECKED_REFERENCE_ONLY`. The risk count is null, never
fabricated as zero ST securities. The website must say "reference P&L, ST not
modeled, not tradeable-fill certified." A successful data/model publication
contract does not imply ST/risk certification.

**Temporal gate:** A new paper pending must be planned on its execution day,
strictly before 09:30 Asia/Shanghai. If that window was missed, do not run
a later cron to backdate its paper decision. The 2026-10-12 07:00 and 07:49
failed attempts never published a valid pending; because 09:30 passed, do not
manufacture the 2026-10-12 forward anchor. The first future accepted
reference point may occur later than the planned 2026-10-12 baseline, with
the true later inception date surfaced publicly. There is no retroactive
paper settlement, NAV or forward backfill.

Only a pending that explicitly records the reference-only policy may bypass
the ST snapshot requirement at its later real-data settlement. Historical
untagged ST-dependent pending cannot be silently converted. A return series
must not merge different ST assumptions or unknown legacy assumptions.
Original fail-closed source/PIT/label/lineage/quotes/halt gates remain binding.
