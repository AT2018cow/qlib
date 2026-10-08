# 20 - CSI1000 Stage-B Winner Canonical Promotion (2026-10-08)

## Status

The Stage-B research decision is unchanged.  The selected model remains:

```text
candidate_id        4e908173705c76fee3782be37068672a3a845bd61894202f3152afe3b9d81ef2
Stage-A rank        5
Stage-B rank        1
model config sha256 0f2cf94d179e7b9982f873588a3afe25fa02991c873954fea11d2db0e025cb88
LightGBM threads    20
```

This document changes only the production deployment topology that was
introduced by PR #25.  It does not reopen Stage A or Stage B, does not add a
new historical selection criterion, and does not retune the model.

## Canonical promotion decision

The prior production Modal app was stopped before the new Stage-B deployment.
There will be no baseline/winner shadow pair.

From the first valid post-promotion publication:

```text
CSI1000 canonical model = Stage-B winner
```

The public/downstream-compatible artifact names remain:

```text
results/signals/<date>_top20_lgb158.csv
results/signals/<date>_chart.json
results/signals/<date>_paper_portfolio.json
```

No `stage_b_winner_shadow` or `stage_b_baseline_shadow` artifacts are
created.

The frozen Stage-B baseline profile remains in
`csi1000_production_config.py` only as an auditable historical control.  It
is not run by the production cron.

## Fresh canonical paper lineage

The pre-promotion canonical paper account belongs to the old baseline
production lineage and MUST NOT be inherited by the Stage-B winner.

The promoted winner therefore uses a new private Modal state key:

```text
paper_lineage = stage_b_winner_canonical
```

while continuing to publish the standard canonical
`<date>_paper_portfolio.json` artifact.

This means the winner starts a fresh paper account with independent cash,
positions and pending orders.  Old artifacts through 2026-10-08 remain
immutable historical records and are not rewritten or deleted.

## Strict production freshness

The frozen execution contract is:

```text
T close signal -> T+1 open execution
```

For the promoted CSI1000 canonical signal, publication is allowed only when:

1. the trading calendar is available and verifiable;
2. `signal_date` is a trading day;
3. `data_date` is a trading day;
4. there are zero trading sessions strictly between `data_date` and
   `signal_date`.

Equivalently:

```text
provider trading-session lag = 0
```

A one-session-stale provider is no longer accepted for CSI1000 canonical,
although the generic publication helper remains unchanged for legacy/satellite
flows.

This is an implementation-integrity gate, not a research change.

## Frozen dimensions

The following remain unchanged:

```text
features             Alpha158
target               raw 20-session forward return
train start           2016-01-01
validation length    252 sessions
retrain frequency    20 sessions
retrain origin       2026-09-18
portfolio            top20 / n_drop=2
execution            T close -> T+1 open
strategy             deterministic top-k dropout
tie-break            score desc / instrument asc
price limits         board/date aware
high-open overlay    disabled
worker CPU           8 physical cores
LightGBM threads     20
```

## Production start condition

Deployment may happen immediately after this change is merged.

The first canonical winner publication is valid only on a run that passes the
strict lag-zero freshness gate.  If the provider has not published the prior
trading session yet, CSI1000 publication must fail closed and wait for the
next scheduled/run attempt rather than publishing a stale signal.

Forward monitoring still records operational quality and performance, but the
winner is already canonical.  Future underperformance alone does not authorize
retuning on the consumed Stage-B tail.
