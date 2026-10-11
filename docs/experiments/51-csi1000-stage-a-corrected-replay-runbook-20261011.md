# 51 CSI1000 Stage-A frozen-signal corrected replay operator runbook (2026-10-11)

This document implements plan 50 and does not override protocol 47. PR-A is a historical diagnostic only. Github merge does NOT prove Modal deployment, and no Modal experiment is claimed run.

## Verified frozen inputs and blocked checks

GitHub Stage-A summary: results/csi1000_tuner/stage_a_expanded_a2ecd8d1cadf4046.json (blob b3ec82244db87465adc6b733227c1c9752ed213d).
Snapshot token: a2ecd8d1cadf404670762a81b1fd33a377f2ef2aaf27a0d9ac6787366defd65b.
Provider fingerprint: cd2e68f571c6bd19fbb2b79089e891bb1c43b96ab1fcdb4d2b2a9b1fdd9ce5d5.
Roster: 80 x 4 = 320. Baseline id 23b92de05cf36c82998de684d0fbf64d81ee54490d755bd3cf96311c00286785, original rank 48. Winner id 4e908173705c76fee3782be37068672a3a845bd61894202f3152afe3b9d81ef2, original rank 5.
Source Volume: qlib-cn-data, path /vol/csi1000_tuner/stage_a/<Stage-A-token>/<candidate-id>/<fold-id>. Baseline canonical path is repeat_b, the normal top-level candidate path, NOT _repro/repeat_a.
Source Volume, original provider/version and Modal production status remain UNVERIFIED by public repository access.

## Authorized operator preflight (workspace credentials never leave workspace)

Run these commands from a clean, committed repository checkout of research/stage-a-corrected-quote-replay-20261011:

    git fetch origin
    git checkout research/stage-a-corrected-quote-replay-20261011
    git pull --ff-only
    git status --short
    git rev-parse HEAD
    python -m pytest -q tests/test_stage_a_corrected_replay.py
    modal --version
    modal profile current
    modal volume list
    modal volume ls qlib-cn-data csi1000_tuner/stage_a/a2ecd8d1cadf404670762a81b1fd33a377f2ef2aaf27a0d9ac6787366defd65b
    mkdir -p /tmp/csi1000-source-manifests
    modal volume get qlib-cn-data csi1000_tuner/stage_a/provider_snapshot.json /tmp/csi1000-source-manifests/stage_a_provider_snapshot.json
    sha256sum /tmp/csi1000-source-manifests/stage_a_provider_snapshot.json
    modal billing rates
    modal billing report --for "this month" --show-resources

If no original Volume, report BLOCKED_SOURCE_NOT_AVAILABLE; never create an empty replacement source Volume or download a new provider. Check original provider manifest matches frozen summary. Source mount uses Volume.with_mount_options(read_only=True) and only the separate research volume receives writes.

## Step 1: 8 control cells, 2 workers

    modal volume create qlib-csi1000-research-audit
    modal run audit/stage_a_corrected_replay_modal.py --scope controls --max-workers 2
    modal app list
    modal billing report --for today --show-resources

All Baseline and Winner folds must PASS original file SHA, old report/order exact reproduction, 20-session date/ledger gates before interpreting any fixed-result changes. On any BLOCKED result stop instead of expanding. The script refuses to overwrite previously saved results. Record actual logs and times. The application NEVER calls fit or writes to source.

## Step 2: fixed representative high/low pilot, 16 cells total

    modal run audit/stage_a_corrected_replay_modal.py --scope sample --max-workers 2

This deterministic selection includes the same 8 controls plus original rank 1 and rank 80 candidates x 4 folds. Source signals are not changed; only Exchange quotation coverage changes from dynamic codes=csi1000 to static union via execution_quote_codes. Sample is not full ranking.

## Step 3: budget and full 320 cells

Use actual worker-seconds and real workspace pricing to estimate full compute: mean_worker_seconds_per_cell x 320 x (2 x 0.0000131 + 4 x 0.00000222), in USD at the PLAN's illustrative rates (2 CPUs, 4 GiB). Include prior pilot/retries/extras and verify pricing with modal billing rates. Initial user-approved ceiling is $20 TOTAL; request new approval BEFORE exceeding. The --estimated-total-usd flag is a planning gate only, NOT real-time enforced spending cap. Operator must monitor billing and stop if needed.

After controls PASS, run:

    modal run --detach audit/stage_a_corrected_replay_modal.py --scope all --max-workers 8 --estimated-total-usd <ACTUAL_ESTIMATE_NOT_OVER_20>
    modal app list
    modal app logs <actual-app-id>
    modal billing report --for today --show-resources
    modal volume ls qlib-csi1000-research-audit

May increase workers to 16 and at most 24 only after measured cost/error/Volume contention review. DO NOT run overlapping all scopes with the same source/code SHA. Rerun a failed scope only for resume of hash-consistent completed cells; finalized summary path intentionally refuses replacement.

Results are under qlib-csi1000-research-audit/stage_a_corrected/<Stage-A-token>/stage_a_corrected_quote_v1/<git-sha>_<fingerprint>/<candidate>/<fold>/; summaries under summaries/<scope>/. Source results are never overwritten. Per-cell files: legacy_reproduction.json, fixed_metrics.json, order_delta.json, daily_nav_delta.csv, comparison.json, fixed_report.parquet, fixed_decisions.json. Summary files: stage_a_320_corrected_batch.json, stage_a_80_corrected_ranking.json, source_manifest.json.

## Acceptance and preservation

All = exactly expected 320, completed 320, blocked 0, ranking status DIAGNOSTIC_80_RERANK, exactly 80 ranks, LOFO four omitted folds, verified source SHA/bytes/mtime, legacy-vs-fixed costs/orders/NAV. Blocked cells stay explicitly blocked; full rank stays BLOCKED_INCOMPLETE_CELLS if ANY cell fails. Preserve actual Modal app URL/log, independent workspace billing report, code version, original snapshot identity and output SHA, redacting credentials.

Synthetic GitHub CI passing is not sufficient for full PR-A acceptance. Execution remains diagnostic Qlib simulation, NOT independent PIT/full broker fill certification. This does not change production stage_b_winner canonical. Do not rerun existing Stage-B corrected 55-cell audit, train any of 80 candidates, or promote revised historical rankings. Protocol 47 prospective shadow is a separate Winner vs Baseline-only effort, with pre-outcome immutable dual prediction, separate capital/NAV, no backfill and 120/240 forward pairs for independent decision.
