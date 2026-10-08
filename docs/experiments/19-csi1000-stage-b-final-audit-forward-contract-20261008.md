# 19 - CSI1000 Stage-B Final Audit and Forward Migration Contract (2026-10-08)

## 0. Status

Stage B is complete and accepted.

Result commit:

```text
7a2676397b0f8e6f69c0bffc98d1764647f644ac
```

Snapshot:

```text
51756897fc75230493194aef2e48815e4e9f3cc7426cc135f47bb8ba8e0d21e1
```

The full result contains all 11 frozen candidates across all five frozen
retraining-calendar phases.  Independent review reproduced the committed
lexicographic Stage-B ranking exactly.

Historical tuning is closed.  The reserved confirmation tail
`2025-01-02..2026-09-30` is consumed confirmation data and MUST NOT be used
to select new hyperparameters, phases, portfolio parameters, or execution
rules.

---

## 1. Frozen Stage-B winner

```text
candidate_id        4e908173705c76fee3782be37068672a3a845bd61894202f3152afe3b9d81ef2
Stage-A rank        5
Stage-B rank        1

learning_rate       0.08
colsample_bytree    0.8
lambda_l1           10
lambda_l2           400
max_depth           6
num_leaves          63
min_data_in_leaf    50

LightGBM threads    20
model config sha256 0f2cf94d179e7b9982f873588a3afe25fa02991c873954fea11d2db0e025cb88
```

The frozen Stage-B baseline control is:

```text
candidate_id        23b92de05cf36c82998de684d0fbf64d81ee54490d755bd3cf96311c00286785
model config sha256 793021af66034ec508848ee032f6d309103291981abb63d11ae779da9c5ee7d3
```

Production code MUST fail closed if either frozen profile no longer hashes to
its accepted Stage-B model config.

---

## 2. Dimensions that remain unchanged

This migration does not reopen any Stage-A or Stage-B decision.  The following
remain frozen:

```text
universe             CSI1000
benchmark            SH000852
features             Alpha158
target               raw 20-session forward return
train start           2016-01-01
validation length    252 sessions
retrain frequency    20 sessions
retrain origin       2026-09-18
portfolio            top20 / n_drop=2
signal timing        T close
execution            T+1 open
strategy             deterministic top-k dropout
tie-break            score desc / instrument asc
price limits         board/date aware
high-open overlay    disabled
cost/accounting      canonical existing production semantics
```

Modal worker CPU and LightGBM model threads are different controls.
The production worker remains 8 physical CPU cores while CSI1000 Stage-B
profiles use `num_threads=20`, matching the accepted research lineage.

---

## 3. Production migration layout

The existing canonical CSI1000 paper state remains the operational continuity
account.

Forward evaluation uses two independent shadow accounts with zero-inception
state:

```text
stage_b_baseline_shadow
stage_b_winner_shadow
```

Both accounts:

- start only when both frozen model streams are available on the same data date;
- use the same provider data, execution date, top20/n_drop=2 policy, costs,
  price-limit masks and paper execution machinery;
- advance as a pair;
- never reuse the historical canonical paper account as the forward control.

The winner additionally publishes a separate ranking/chart stream:

```text
results/signals/<date>_top20_lgb158_stage_b_winner_shadow.csv
results/signals/<date>_chart_stage_b_winner_shadow.json
results/signals/<date>_paper_portfolio_stage_b_winner_shadow.json
```

The paired baseline control publishes:

```text
results/signals/<date>_paper_portfolio_stage_b_baseline_shadow.json
```

A shadow failure must not block canonical production publication.

---

## 4. Production integration gate

Before treating the shadow run as valid forward evidence, verify:

```text
winner candidate ID exact                       PASS
baseline candidate ID exact                     PASS
winner model config SHA256 exact                PASS
baseline model config SHA256 exact              PASS
LightGBM num_threads = 20                       PASS
LightGBM deterministic seed controls exact      PASS
train_start = 2016-01-01                       PASS
validation = 252 sessions                       PASS
label maturity purge horizon = 20               PASS
target = raw_20d                                PASS
TopK = 20 / n_drop = 2                          PASS
T close -> T+1 open                             PASS
board-aware price limits                        PASS
5% high-open overlay disabled                   PASS
score-desc/instrument-asc exact tie-break       PASS
retrain origin/frequency unchanged              PASS
baseline/winner cache lineages distinct         PASS
paper states independent                        PASS
```

For one common provider/as-of, run each frozen profile twice and require exact
agreement for split metadata, prediction content/ranking and planned paper
orders.  This is an implementation reproducibility gate, not another
performance-selection experiment.

---

## 5. Forward review contract

Forward time has only one real retraining timeline.  Do not create or select
alternative retraining phases after observing forward results.

Pre-registered review points:

```text
2 complete retrain boundaries   operational review
60 trading sessions             interim forward review
120 trading sessions            primary forward review
```

The 60-session review is diagnostic.  It does not reopen model selection.
The 120-session review is the first planned point for considering canonical
promotion of the frozen winner.

Monitor at minimum:

- provider/data fingerprint;
- profile/candidate/model-config lineage;
- fit as-of and train/validation boundaries;
- prediction/ranking determinism;
- pending and executed paper orders;
- account reconciliation;
- strategy and benchmark return;
- active return;
- drawdown;
- turnover;
- realized cost;
- cache invalidation/retrain timing.

Implementation failures (config drift, wrong cache, timing errors,
non-deterministic decisions, accounting failure, missing/corrupt artifacts)
may invalidate or restart the forward lineage under an explicitly versioned
fix.

Ordinary poor returns, temporary underperformance versus the baseline, or a
bad retrain period do NOT authorize historical retuning.

---

## 6. Promotion rule

The Stage-B winner is already selected.  Forward monitoring asks whether that
frozen model can operate safely and retain useful behavior on genuinely new
data.

Canonical promotion should therefore occur only after:

1. the production integration gate passes;
2. at least two complete 20-session retrain boundaries have executed correctly;
3. the pre-registered forward reviews show no implementation or risk veto.

Any later model change requires a new explicit research protocol/version.  It
must not reuse the consumed Stage-B tail as fresh selection data.
