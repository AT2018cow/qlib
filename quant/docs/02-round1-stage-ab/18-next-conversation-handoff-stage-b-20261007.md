# 18 - Next-Conversation Handoff: CSI1000 Stage B (2026-10-07)

## 0. Purpose and authority

This is the startup handoff for the next ChatGPT conversation.

Repository:

```text
https://github.com/AT2018cow/qlib
```

This document **supersedes**
`15-next-conversation-handoff-20261007.md` for current CSI1000 work.

At the start of PR #20, authoritative `main` was:

```text
6db2dbc5aefeebb517f82fd37b7ebde59b53a951
data: expanded 80-candidate Stage-A results + audit export
```

PR #20 is the Stage-B implementation PR. After it is merged, the next
conversation must first verify the actual merge commit on `main` rather than
assuming the base SHA above is still current.

Read in this order:

1. this handoff;
2. `docs/experiments/17-csi1000-stage-a-expanded-screen-runbook-20261007.md`;
3. `docs/experiments/16-csi1000-stage-a-tuning-contract-20261007.md`;
4. `csi1000_stage_b_core.py`;
5. `csi1000_stage_b.py`.

Do not reopen frozen Stage-A choices unless there is concrete evidence of an
implementation or data-integrity defect.

### Current authoritative state (2026-10-08)

Latest committed research result on `main`:

```text
6c957f8809a1668c11ad63e6dd2b63c28d193f08
results: Stage-B preflight gate PASS (44 fits, run e56441cbfd5b4242)
```

Stage-B implementation hardening through PR #24 is merged. The accepted
Stage-B runtime snapshot is:

```text
51756897fc75230493194aef2e48815e4e9f3cc7426cc135f47bb8ba8e0d21e1
```

The engineering preflight has completed and passed. **The next action is no
longer another preflight. It is the full Stage-B grid.**

---

## 1. Canonical research protocol

Current CSI1000 protocol:

```text
features/model        Alpha158 + LightGBM
label                 20-trading-day forward return
retrain frequency     20 trading sessions
signal timing         T close
execution             T+1 open
account               continuous
portfolio             deterministic TopK dropout, topk=20, n_drop=2
tie break             score descending, instrument ascending
price limits          board/date aware
price tick            statutory CNY 0.01 on reconstructed original-price scale
5% high-open overlay  disabled
metrics               portfolio_compound_v1 canonical geometric metrics
train start           2016-01-01
validation            252 sessions
purge horizon         20 sessions
```

LightGBM deterministic controls remain:

```text
seed                  0
data_random_seed      1
feature_fraction_seed 2
bagging_seed          3
drop_seed             4
objective_seed        5
extra_seed            6
deterministic         true
force_col_wise        true
num_threads           20
```

Do not change model thread count during Stage B. It is part of the already
validated training semantics even though it is not a tuned hyperparameter.

---

## 2. Why deterministic execution exists

The original Stage-A reproducibility failure had identical LightGBM prediction
hashes but materially different portfolio returns.

Root cause:

- Qlib `Position.get_stock_list()` is set-derived and therefore can be
  hash-order dependent;
- upstream `TopkDropoutStrategy` did not specify an explicit secondary key for
  exact score ties;
- tree models can generate exact score ties.

PR #18 added `DeterministicTopkDropoutStrategy`, scoped to this CSI1000
research:

```text
score order          descending
exact score tie      instrument ascending
holding iteration    instrument ascending
sell iteration       instrument ascending
buy iteration        deterministic score rank
random modes         unsupported
```

The gate records exact prediction/signal and decision-order hashes. Raw report
bytes may differ at machine precision, so reports are compared structurally and
numerically with:

```text
rtol = 1e-10
atol = 1e-12
account normalized by 1e8 before float comparison
```

Do not weaken this gate.

---

## 3. Stage-A lineage and completed evidence

Frozen Stage-A lineage:

```text
protocol          csi1000_lgb_stage_a_v3
snapshot token    a2ecd8d1cadf404670762a81b1fd33a377f2ef2aaf27a0d9ac6787366defd65b
provider cutoff   2026-09-30
provider fp       cd2e68f571c6bd19fbb2b79089e891bb1c43b96ab1fcdb4d2b2a9b1fdd9ce5d5
strategy          deterministic_topk_dropout_v1
```

Completed sequence:

1. baseline-only v3 preflight: PASS;
2. 12-candidate smoke: PASS;
3. pre-expansion reuse plan: 44 reusable non-baseline candidate-fold artifacts;
4. 80-candidate expanded Stage A: complete, 320/320 candidate-fold results;
5. post-run audit: complete, full rank + four LOFO ranks.

The expansion was interrupted once by a local/client server failure. The
artifact resume mechanism worked:

```text
original pre-run plan        44 reusable non-baseline folds
interrupted run completed   +56 additional folds
restart-time plan           100 reusable non-baseline folds
final restart audit         100/100 unchanged
historical original audit    44/44 unchanged
```

The canonical plan filename was overwritten by the restart inventory. Git
history at commit `8c161a0d...` preserves the original 44-fold plan and was
used to reconstruct the 44/44 verification.

PR #20 changes future Stage-A reuse plans to labeled immutable filenames so a
restart plan cannot overwrite the original provenance.

---

## 4. Stage-A research conclusion

Baseline finished:

```text
rank                  48 / 80
worst relative CAGR   -0.236337
q25 relative CAGR      0.204956
median relative CAGR   1.087285
positive folds         3 / 4
```

There are 11 candidates with positive relative excess CAGR in all four screen
folds.

Top candidates formed a repeated family rather than a single isolated winner.
The strongest family commonly used:

```text
learning_rate         0.10
num_leaves            31
max_depth             6-8
min_data_in_leaf      100-200
moderate L1/L2
```

The old baseline used much higher tree complexity:

```text
max_depth             8
num_leaves            250
min_data_in_leaf      20
```

Stage A therefore provides strong evidence that the old baseline is not the
best LightGBM configuration for this CSI1000/raw20d setup.

Do **not** run candidate 81 or redesign the search space. Stage A is finished.

---

## 5. Critical LOFO finding

The four-fold audit showed:

```text
worst fold = fold1    78 / 80 candidates
worst fold = fold2     1 / 80
worst fold = fold3     1 / 80
```

Therefore the Stage-A worst-fold-first lexicographic ranking is strongly driven
by the 2021 fold. Fine differences among rank #1/#2/#3 must not be interpreted
as a globally stable ordering.

This is why Stage B exists: it tests the frozen candidates on the previously
untouched recent tail and across fixed retraining-calendar phases.

---

## 6. Frozen Stage-B candidate manifest

Selection rule was fixed before viewing Stage-B results:

> Stage-A full-rank top 10, each also top-10 in at least 3 of 4 LOFO rankings,
> plus the baseline as a separate control.

The tuned promotion slots are:

| Stage-A rank | candidate ID | lr | colsample | L1 | L2 | depth | leaves | min leaf |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | `59b93929bff1...` | .10 | .8 | 10 | 400 | 6 | 31 | 100 |
| 2 | `a419047ab97e...` | .10 | 1.0 | 50 | 200 | 6 | 31 | 200 |
| 3 | `b034ac4645fb...` | .10 | .8 | 50 | 200 | 8 | 31 | 200 |
| 4 | `e74b3bbe2278...` | .10 | .9 | 100 | 400 | 6 | 31 | 200 |
| 5 | `4e908173705c...` | .08 | .8 | 10 | 400 | 6 | 63 | 50 |
| 6 | `17dbfe28fea8...` | .08 | .8 | 10 | 100 | 10 | 127 | 100 |
| 7 | `c04ca4163f77...` | .10 | 1.0 | 50 | 200 | 10 | 127 | 50 |
| 8 | `c98856b460ab...` | .05 | 1.0 | 205.6999 | 200 | 10 | 250 | 200 |
| 9 | `1db146eec9de...` | .10 | 1.0 | 205.6999 | 400 | 8 | 63 | 20 |
| 10 | `40b872dfe452...` | .03 | .7 | 100 | 50 | 10 | 63 | 20 |

Control:

```text
candidate 23b92de05cf3...
Stage-A rank 48
original frozen baseline
```

Exact full IDs and parameter dictionaries live in
`csi1000_stage_b_core.py`; that file is authoritative.

The old Stage-A `stage_b_preview` selected only ranks 1-7 plus baseline because
the helper forced baseline into an 8-slot list. Do not use that preview as the
Stage-B manifest.

---

## 7. Reserved tail and Stage-B phases

The previously untouched tail is frozen:

```text
signal anchor        2024-12-31
execution start      2025-01-02
execution end        2026-09-30
execution sessions   424
```

Stage-B phases are exactly:

```text
0, 4, 6, 10, 15
```

A phase is a retraining-calendar offset, **not model age**.

Each phase requires 22 retraining fits per candidate under the frozen tail.

Full grid:

```text
11 candidates x 5 phases x 22 retrains = 1210 fits
```

---

## 8. PR #20 Stage-B implementation

PR #20 adds:

```text
csi1000_stage_b_core.py
csi1000_stage_b.py
tests/test_csi1000_stage_b_core.py
```

The runner:

- refuses to refresh/download provider data;
- verifies the committed Stage-A expanded result and audit support the frozen
  candidate selection;
- fingerprints Stage-B protocol, provider, source, runtime, Stage-A evidence,
  deterministic strategy, and model config;
- compares the **unoverlaid** Stage-B base model config to Stage-A
  `base_model_config_sha256`, because Stage-A created that hash before applying
  any candidate overlay;
- fingerprints each frozen candidate's overlaid model config separately and
  requires every retrain worker (including resume artifacts) to match that
  candidate-specific hash;
- persists each retrain prediction chunk independently;
- validates chunk artifacts before resume;
- assembles one continuous signal lineage per candidate/phase;
- runs one continuous-account T+1-open deterministic TopK backtest per
  candidate/phase;
- persists signal, decision, report, and result artifacts;
- resumes both retrain chunks and completed candidate/phase results;
- ranks by fixed phase lower-tail robustness, with frozen Stage-A rank only as
  a deterministic secondary tie break.

Stage-B final ranking order is lexicographic:

```text
1  phase relative excess CAGR worst       higher
2  phase relative excess CAGR q25         higher
3  phase relative excess CAGR median      higher
4  positive phase ratio                   higher
5  median information ratio               higher
6  median Sharpe                          higher
7  worst strategy MaxDD                   higher (less negative)
8  median turnover                        lower
9  median total cost                      lower
then frozen Stage-A rank                  lower
```

No single phase may select a winner.

---

## 9. Modal resource policy

Starter currently allows 100 containers at the workspace level.

Do not interpret that as a guarantee that 100 high-resource workers will be
simultaneously schedulable.

Stage A proved the following conservative allocation works in this workspace:

```text
worker CPU          8 Modal physical cores (~16 conventional vCPU)
worker memory       24576 MiB
LightGBM threads    20
```

Stage B deliberately keeps the proven 8-core CPU request and LightGBM
`num_threads=20`, while using a leaner default memory request of 16384 MiB.
The Stage-B horizontal cap defaults to:

```text
max_containers      64
Starter bound       100
retries             2
```

CPU, memory, and container cap are runtime execution options, not
model-selection parameters. PR #20 defaults to 8 physical cores, a 16 GiB
memory request, and 64 containers. They may be adjusted inside a bounded
preflight envelope without changing the frozen candidates or LightGBM
`num_threads=20` semantics:

```bash
modal run --detach csi1000_stage_b.py \
  --preflight-only \
  --worker-cpu 8 \
  --worker-memory-mib 16384 \
  --worker-max-containers 64
```

Allowed runtime envelope:

```text
worker CPU           4 .. 8 Modal physical cores
worker memory        12288 .. 24576 MiB
worker containers    1 .. 100
```

Do not change `num_threads` merely to chase throughput; preserve model
semantics. The baseline phase-0 preflight has 44 model fits, but the two
22-retrain repeats run sequentially. Therefore the preflight can validate
per-worker CPU/memory behavior and fit duration, but cannot demonstrate
40-64-way retrain scaling. The full Stage-B grid has enough backlog to test the
64-container cap. The runner rejects caps above the Starter 100-container
limit.

Use `--detach` for long Modal runs so local/client failure does not
automatically kill remote work.

---

## 10. Stage-B engineering preflight: completed and accepted

The post-PR #24 engineering preflight has already been run and audited.

Committed result:

```text
results/csi1000_stage_b/stage_b_preflight_only_51756897fc752304.json
commit: 6c957f8809a1668c11ad63e6dd2b63c28d193f08
run id: e56441cbfd5b4242
snapshot: 51756897fc75230493194aef2e48815e4e9f3cc7426cc135f47bb8ba8e0d21e1
```

Execution resources:

```text
Modal worker CPU       8 physical cores
worker memory          16384 MiB request
max containers         64
LightGBM num_threads   20
```

Preflight result:

```text
gate                           PASS
baseline candidate             23b92de05cf3...
phase                          0
retrain fits / repeat          22
independent repeats            2
total fits                     44
prediction chunks exact        true
assembled signal exact         true
decision/order exact           true
report structure match         true
canonical metrics exact        true
report semantic columns        9 / 9 PASS
phase artifacts durable        true
worker durability A/B          PASS / PASS
driver durability A/B          PASS / PASS
publish attempt A/B            1 / 1
```

Cross-stage continuity also passed:

```text
provider fingerprint match             true
base model config match                true
LightGBM reproducibility controls      true
deterministic strategy source match    true
Python/Qlib/LGB/NumPy/pandas runtime   true
```

The two repeats produced exact model best-iteration sequences, exact prediction
content hashes, exact assembled signal hash, and exact decision content hash.

The raw report content hashes are not byte/content identical, but this is the
already accepted machine-precision behavior. All nine report columns pass the
frozen semantic gate `rtol=1e-10 / atol=1e-12`; the largest observed relative
difference is approximately `1.45e-11`. Canonical portfolio metrics are
identical.

For engineering sanity only, baseline phase-0 canonical metrics were:

```text
strategy total return       43.4313%
strategy CAGR               23.0149%
benchmark total return      24.0726%
benchmark CAGR              13.1869%
relative total return       15.6027%
relative excess CAGR         8.6829%
strategy MaxDD              -9.6486%
Sharpe                       1.122058
information ratio            0.356593
mean turnover                0.056408
total cost sum               0.023430
account return max error     0
```

These return figures **must not** be used to alter candidates, phases, ranking,
or any other Stage-B design choice. The preflight was an engineering gate only.

PR #24's durable artifact transaction is therefore empirically validated on
both repeats: worker-side and driver-side round-trip checks passed on the first
publication attempt.

---

## 11. Next action: run full Stage B

Start from the latest `main`, verify the worktree is clean, and run exactly
one full Stage-B instance:

```bash
git checkout main
git pull --ff-only
git status

modal run --detach csi1000_stage_b.py \
  --worker-cpu 8 \
  --worker-memory-mib 16384 \
  --worker-max-containers 64
```

Do not launch a second concurrent full run against the same canonical artifact
paths.

The accepted preflight's baseline phase-0 result is reused, so the full run needs:

```text
full grid                         1210 fits
minus reused baseline phase0        22 fits
new fits after preflight           1188 fits
preflight itself                     44 fits
total Stage-B compute               1232 fits
```

If interrupted, rerun the **same command** without changing source/provider,
candidate set, phase set, LightGBM parameters, or execution semantics.
Completed retrain chunks and completed candidate/phase artifacts are
hash-validated and reused. PR #24 also cross-container verifies chunks and
phase artifact sets; only a missing/corrupt chunk or affected phase artifact
stage should be repaired.

During the full run, do not react to intermediate returns. The first permitted
research interpretation is after all 11 candidates x 5 phases are complete.

Push the generated:

```text
results/csi1000_stage_b/stage_b_full_<token16>.json
```

for final audit. Before any model-selection conclusion, verify:

```text
11 / 11 frozen candidates complete
5 / 5 phases per candidate
55 candidate-phase results
accepted preflight lineage retained
baseline phase0 reused correctly
all chunk/phase durability checks pass
account_return_max_error within contract
final rank recomputes from frozen phase-first lower-tail contract
```

---

## 12. What to decide after Stage B

Stage-B uses the final previously untouched historical tail. Once results are
viewed, that tail is no longer untouched.

Therefore Stage-B has only two legitimate research outcomes:

1. one or more tuned candidates show robust phase-level improvement over the
   baseline -> freeze the chosen model/configuration and move to
   forward/paper/production validation;
2. no tuned candidate has convincing robust improvement -> retain the baseline
   or conclude this tuning round did not establish a better configuration.

Do **not** respond to a disappointing Stage-B result by changing parameters and
rerunning on the same tail.

After Stage B, historical hyperparameter tuning is closed.

---

## 13. Explicit prohibitions

Do not:

- add more Stage-A LightGBM candidates;
- change the frozen 10 tuned Stage-B candidates based on Stage-B performance;
- change the fixed phases after seeing Stage-B performance;
- refresh provider data before completing Stage B;
- reintroduce upstream nondeterministic `TopkDropoutStrategy`;
- use same-day-close execution;
- enable the 5% high-open overlay in canonical research;
- select one lucky phase;
- treat phase number as model age;
- change LightGBM threads merely for speed;
- overwrite audit reuse plans.

If a genuine implementation defect is discovered, version the protocol,
document the defect, and rerun the necessary gate before trusting new results.

---

## 14. Suggested startup prompt for the next conversation

Use:

> Please take over the CSI1000 Stage-B work in `AT2018cow/qlib`. First read
> `docs/experiments/18-next-conversation-handoff-stage-b-20261007.md`, then
> verify the latest `main` and preflight result commit
> `6c957f8809a1668c11ad63e6dd2b63c28d193f08`. Stage A is frozen and the
> Stage-B engineering preflight has already passed under snapshot
> `51756897fc75230493194aef2e48815e4e9f3cc7426cc135f47bb8ba8e0d21e1`.
> The next action is the full Stage-B run and subsequent audit. Do not reopen
> frozen Stage-A decisions or redesign Stage B unless there is concrete
> implementation/data-integrity evidence.
