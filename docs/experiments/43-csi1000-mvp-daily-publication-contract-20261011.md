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

Before the first trading-day publication after a code change:

1. confirm `main` contains the intended deployment commit;
2. confirm the Modal deployment still registers `daily_cron` at 07:00
   Asia/Shanghai and uses the canonical profile;
3. confirm the latest successful publication commit contains all four
   same-date artifacts;
4. confirm the Pages deployment triggered from that signal commit and the
   latest date renders the MVP contract/risk state;
5. inspect the paper artifact: pending signal date = provider data date,
   execution date = usage date, and any prior execution report is carried
   separately.

If publication fails, do not write a replacement signal manually.  Fix the
failed current gate, redeploy if code changed, and rerun the same usage date.
The same-date immutability check will either accept byte-identical existing
files and fill only missing files, or stop on content drift.

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
