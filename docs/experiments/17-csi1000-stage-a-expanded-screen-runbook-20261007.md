# 17 - CSI1000 Stage-A 80-Candidate Expansion Runbook (2026-10-07)

## 0. Purpose

The accepted 12-candidate smoke screen under Stage-A v3 exposed one remaining
audit gap: the pushed summary contains aggregate fold statistics but not the
compact per-fold metrics needed for leave-one-fold-out (LOFO) ranking and
candidate stability review.

This runbook fixes that gap **without changing the Stage-A computation lineage**.

The validated lineage is:

```text
protocol          csi1000_lgb_stage_a_v3
snapshot token    a2ecd8d1cadf404670762a81b1fd33a377f2ef2aaf27a0d9ac6787366defd65b
provider cutoff   2026-09-30
smoke candidates  12
folds             4
```

The smoke screen completed the baseline plus 11 non-baseline candidates. Those
11 candidates therefore already have:

```text
11 candidates x 4 folds = 44 canonical candidate-fold artifacts
```

available for reuse.

---

## 1. Why the audit fix is a sidecar

Do **not** add the new audit fields directly to `csi1000_tuner.py` before the
80-candidate expansion.

The Stage-A runtime manifest fingerprints:

```text
csi1000_tuner.py
csi1000_tuner_core.py
deterministic_strategy.py
```

Changing any of those computational files changes the snapshot token. A new token
would prevent the 44 completed smoke artifacts from being reused under the
validated `a2ecd8d1...` lineage.

PR #19 therefore adds a read-only sidecar:

```text
csi1000_tuner_audit.py
```

It reads the Modal Volume but never writes to it and is not part of the Stage-A
snapshot fingerprint.

---

## 2. Pre-expansion reuse plan

Before launching the 80-candidate screen, inventory the existing artifacts:

```bash
modal run csi1000_tuner_audit.py \
  --mode plan \
  --snapshot-token a2ecd8d1cadf404670762a81b1fd33a377f2ef2aaf27a0d9ac6787366defd65b \
  --candidate-count 80
```

The command exports:

```text
results/csi1000_tuner/
stage_a_expanded_reuse_plan_a2ecd8d1cadf4046.json
```

The expected accounting before expansion is:

```text
target candidates                               80
non-baseline candidate-fold jobs       79 x 4 = 316
prechecked reusable smoke folds        11 x 4 = 44
new non-baseline fits                           272
fresh baseline reproducibility gate      4 x 2 = 8
expected total new fits                         280
worst case if no candidate reuse                324
```

The audit-side precheck verifies candidate/fold/snapshot identity, canonical
artifact locations, required numeric metrics, and artifact byte SHA256 values.
The tuner remains authoritative: during the actual expanded run it performs its
stricter signal/report content and canonical-metric validation before accepting
an artifact for resume.

Do not start the expansion if the plan finds fewer than 44 reusable
non-baseline candidate-fold artifacts unless the missing/rejected artifacts are
understood.

---

## 3. Launch the registered 80-candidate expansion

Use the existing v3 tuner unchanged:

```bash
modal run csi1000_tuner.py --expanded --candidate-count 80
```

Do **not** pass `--force-data`.

The expanded candidate generator is deterministic and retains all 12 smoke
candidates before adding 68 new candidates from the registered bounded domain.
With `resume=True` (the default), the 44 completed non-baseline smoke
candidate-folds are offered to the normal verified-resume path.

The baseline is intentionally different: it is always fit twice again as the
fresh 4-fold reproducibility gate for the expanded run. Its previous canonical
fold artifacts are not counted as expansion reuse.

Do not change any of these files between the reuse-plan step and the expanded
run:

```text
csi1000_tuner.py
csi1000_tuner_core.py
deterministic_strategy.py
```

Also do not refresh the provider data. Either action changes the snapshot token
or lineage and invalidates the planned reuse.

---

## 4. Export per-fold audit data after expansion

After the 80-candidate run completes, export the compact candidate-fold metrics
and verify planned reuse:

```bash
modal run csi1000_tuner_audit.py \
  --mode export \
  --snapshot-token a2ecd8d1cadf404670762a81b1fd33a377f2ef2aaf27a0d9ac6787366defd65b \
  --candidate-count 80 \
  --plan-path results/csi1000_tuner/stage_a_expanded_reuse_plan_a2ecd8d1cadf4046.json
```

This exports:

```text
results/csi1000_tuner/
stage_a_expanded_audit_a2ecd8d1cadf4046.json
```

The audit output contains, for every candidate and fold:

- model parameters;
- best iteration;
- strategy / benchmark / relative total return and CAGR;
- strategy / benchmark / relative MaxDD;
- Sharpe and information ratio;
- daily return diagnostics;
- account consistency error;
- mean turnover and total cost;
- signal, decision, and report content hashes.

It also recomputes:

- the full 4-fold Stage-A ranking from the compact metrics;
- four leave-one-fold-out rankings;
- artifact completeness for all 80 x 4 candidate-fold results.

When a pre-expansion plan is supplied, it additionally checks that every
planned reusable `result.json` retained the same Volume mtime. The verified
resume path reads but does not rewrite a reused result; a changed mtime therefore
flags a refit/rewrite that should be investigated.

Expected reuse verification:

```text
planned reusable non-baseline folds   44
unchanged result artifacts             44
rewritten/missing                       0
```

---

## 5. Review criteria before Stage B

The 80-candidate screen is still a proxy screen. Do not promote candidates by
rank #1 alone.

Review at least:

1. baseline percentile among all 80 candidates;
2. top-10 separation in worst-fold, q25, and median relative excess;
3. whether strong candidates form repeated parameter families rather than one
   isolated random winner;
4. LOFO rank stability across the four three-fold subsets;
5. positive-fold ratio;
6. MaxDD, turnover, and cost;
7. whether candidate superiority survives outside one exceptional fold.

A candidate that ranks highly in the full four-fold result but collapses when
one fold is omitted is not yet a robust Stage-B promotion.

The `stage_b_preview` emitted by the tuner remains a preview only. Final
promotion to full rolling / fixed-phase robustness should be decided after this
expanded-screen audit.
