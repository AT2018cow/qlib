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

After controls PASS **and after the 16-cell sample plus quote/PIT evidence gate is accepted**, run with an attached, foreground OpenCode command. **Do not use the Modal `--detach` flag.** In the operator's OpenCode environment a detached invocation may be interrupted when the calling tool times out. Preconfigure the OpenCode tool/session timeout to exceed the estimated total run time (image startup, queueing, all 320 cells and finalization); keep the session attached and wait for its actual exit status. If an adequate tool timeout or durable attached session cannot be provided, **do not launch**; arrange a separately approved long-duration foreground execution or bounded-batch design instead. Do not use `nohup`, shell `&`, or other untracked background workarounds.

    set -o pipefail
    modal run audit/stage_a_corrected_replay_modal.py --scope all --max-workers 8 --estimated-total-usd <ACTUAL_ESTIMATE_NOT_OVER_20> 2>&1 | tee /tmp/csi1000-stage-a-320-modal.log
    # Wait for the foreground command to finish; preserve its actual exit code.
    modal app list
    modal app logs <actual-app-id>
    modal billing report --for today --show-resources
    modal volume ls qlib-csi1000-research-audit

Retain the **actual** OpenCode launch command, configured timeout, CLI exit code, foreground transcript, server-side Modal log/app ID and start/end UTC timestamps. A launcher timeout or lost terminal session is `RUN_STATUS_UNKNOWN_NEEDS_RECONCILIATION`, **not** `PASS`; verify completed immutable output SHAs before any resume. The prior 8/16-cell successes do not imply that OpenCode's default timeout can accommodate the full batch.

May increase workers to 16 and at most 24 only after measured cost/error/Volume contention review. DO NOT run overlapping all scopes with the same source/code SHA. Rerun a failed scope only for resume of hash-consistent completed cells; finalized summary path intentionally refuses replacement.

Results are under qlib-csi1000-research-audit/stage_a_corrected/<Stage-A-token>/stage_a_corrected_quote_v1/<git-sha>_<fingerprint>/<candidate>/<fold>/; summaries under summaries/<scope>/. Source results are never overwritten. Per-cell files: legacy_reproduction.json, fixed_metrics.json, order_delta.json, daily_nav_delta.csv, comparison.json, fixed_report.parquet, fixed_decisions.json. Summary files: stage_a_320_corrected_batch.json, stage_a_80_corrected_ranking.json, source_manifest.json.

## Acceptance and preservation

All = exactly expected 320, completed 320, blocked 0, ranking status DIAGNOSTIC_80_RERANK, exactly 80 ranks, LOFO four omitted folds, verified source SHA/bytes/mtime, legacy-vs-fixed costs/orders/NAV. Blocked cells stay explicitly blocked; full rank stays BLOCKED_INCOMPLETE_CELLS if ANY cell fails. Preserve actual Modal app URL/log, independent workspace billing report, code version, original snapshot identity and output SHA, redacting credentials.

Synthetic GitHub CI passing is not sufficient for full PR-A acceptance. Execution remains diagnostic Qlib simulation, NOT independent PIT/full broker fill certification. This does not change production stage_b_winner canonical. Do not rerun existing Stage-B corrected 55-cell audit, train any of 80 candidates, or promote revised historical rankings. Protocol 47 prospective shadow is a separate Winner vs Baseline-only effort, with pre-outcome immutable dual prediction, separate capital/NAV, no backfill and 120/240 forward pairs for independent decision.


## GitHub evidence submission contract (operator → reviewer; REQUIRED, 2026-10-11)

**The operator runs Modal; the reviewer audits GitHub evidence.** No source Volume access, real Modal experiment, or production deployment is assumed on the review side. Code/CI success is **not** experiment acceptance. Attach the following to a new commit/branch and link its immutable commit SHA in PR #64; never paste secrets in issues or logs.

### Mandatory GitHub directory and upload schedule

Use `results/csi1000_strategy_selection_evidence/pr64/<REPLAY_CODE_SHA>/<scope>/` where `REPLAY_CODE_SHA` is the **exact clean checkout SHA printed by Modal**, not the current branch HEAD after evidence-only commits. Keep `controls/`, `sample/`, and `all/` separate and append-only. Never amend an already reviewed evidence commit or overwrite an older run. For reruns with a different replay code SHA, use a new directory. Retain the original research Volume prefix (snapshot, protocol version, code SHA and provider fingerprint) in `operator_run.json`.

For **each finalized scope** (including finalized BLOCKED attempts), commit these *three original unmodified* JSON files from `summaries/<scope>/`. If a source/permission/finalizer failure stops execution before any summary exists, submit `operator_run.json` with `BLOCKED_SOURCE_NOT_AVAILABLE` (or the exact failure), real logs and an explicit `summary_files_not_generated` explanation; **do not fabricate missing summary JSON**:
- `stage_a_320_corrected_batch.json` — full attempted-cell table, explicit PASS/BLOCKED reasons, original/fixed metrics and order/NAV changes; for controls expected=8, sample=16, all=320. The filename is fixed even for smaller scopes; the `scope` and `expected` fields determine actual coverage.
- `stage_a_80_corrected_ranking.json` — controls/sample must remain pilot-only, all must contain 80 ranks *only if* 320/320 passed; blocked runs must preserve `BLOCKED_INCOMPLETE_CELLS`.
- `source_manifest.json` — source file SHA256 per cell, provider manifest SHA, provider fingerprint and replay code SHA, including any BLOCKED cells. **Do not upload the original signals/provider** just to prove their hashes.

For **8 controls (4 Baseline + 4 Winner)**, also publish the complete *derived* replay evidence under `cells/<candidate_id>/<fold_id>/`: `comparison.json`, `legacy_reproduction.json`, `fixed_metrics.json`, `order_delta.json`, `daily_nav_delta.csv`, `fixed_report.parquet`, `fixed_decisions.json`. No original `signal.parquet`, original `report.parquet`, or original `decisions.json` in public GitHub.

For **sample/all**, provide `comparison.json`, `legacy_reproduction.json`, `fixed_metrics.json`, `order_delta.json`, `daily_nav_delta.csv` for **every attempted PASS cell** under its `cells/<candidate_id>/<fold_id>/` path. For BLOCKED cells record the exact reason in the batch and `operator_run.json`; do not fabricate missing per-cell files. In `all/` also publish the full 8 control cells' `fixed_report.parquet` and `fixed_decisions.json` and the full corresponding files for any materially changed/outlier cells (identify selected IDs and selection rule in `operator_run.json`). Keep all 320 complete derived reports/decisions in the *separate* research Volume, referenced by output SHA in each comparison record; a reviewer may request selected bytes for additional checks.

**Every scope must additionally contain**:
- `operator_run.json`: actual `git_sha`, `modal_app_id_or_url`, `modal_environment` (non-secret name), UTC start/end, Modal CLI/Python/pyqlib/LightGBM/numpy/pandas versions, `snapshot_token`, `provider_fingerprint`, original provider manifest SHA, research result prefix, scopes/cell counts, worker CPU/GiB/concurrency, real retry/resume/error counts, `training_performed=false`, `promotion_authorized=false`, `source_volume_read_only=true`, actual spending estimate and actual billing evidence references, `submitted_utc`, operator, and all unresolved limitations. Use `null` + explicit reason for unavailable facts, not a plausible placeholder. Include `opencode_foreground_modal=true`, actual tool timeout, `modal_detach_used=false`, launcher return/exit status and any timeout/recovery history.
- `modal_run.log` (or `modal_run_redacted.log` + explicit redaction note): real original control/sample/all app logs, command, timestamps, exception trace and resume events. Never invent log messages.
- `billing_report.json` or `billing_report.csv` from Modal billing (`--json` or `--csv`), with billing period and app IDs; if not permitted, `billing_unavailable.json` with reason and independently reviewed Dashboard screenshot reference. A workspace aggregate may include unrelated spend: identify relevant app/buckets and distinguish estimates from billed amounts.
- `SHA256SUMS.txt` of every committed evidence file **except SHA256SUMS.txt itself**, using repository-relative paths, plus reviewer commands/versions in `operator_run.json`.

Full 320 artifacts are **not** necessary for an 8-cell smoke review, and a smoke is never labeled a full rerank. To accept all 320, reviewer must confirm 320 unique candidate-fold IDs, no missing/duplicate/blocking cells, 80 complete corrected ranks, source/output SHA matching, reproducible legacy metrics/orders, no fit or production writes, and diagnostic-only limitations. A GitHub-only hash summary cannot authenticate original Volume bytes independently; the original remains retained under the verified read-only source snapshot and is audited by the authorized operator (or independently supplied securely for byte-check).

### Read-only Modal collection and GitHub push (example; replace exact values)

```bash
# Run from the ORIGINAL clean replay checkout. Capture its SHA BEFORE evidence commits.
REPLAY_CODE_SHA=$(git rev-parse HEAD)
SNAPSHOT=a2ecd8d1cadf404670762a81b1fd33a377f2ef2aaf27a0d9ac6787366defd65b
FP=cd2e68f571c6bd19fbb2b79089e891bb1c43b96ab1fcdb4d2b2a9b1fdd9ce5d5
PREFIX="stage_a_corrected/$SNAPSHOT/stage_a_corrected_quote_v1/${REPLAY_CODE_SHA}_${FP}"
modal volume ls qlib-csi1000-research-audit "$PREFIX"
# Recursive download of NEW research outputs ONLY, to outside Git (includes files that must NOT be committed).
mkdir -p /tmp/csi1000-stage-a-review
modal volume get qlib-csi1000-research-audit "$PREFIX" /tmp/csi1000-stage-a-review/downloaded
# Build the mandatory review subset described above in:
# results/csi1000_strategy_selection_evidence/pr64/$REPLAY_CODE_SHA/<scope>/
# Verify counts/hashes; inspect privacy-sensitive logs before staging. Do NOT git-add the whole download.
git status --short
git add results/csi1000_strategy_selection_evidence/pr64/
git diff --cached --stat
git commit -m "evidence(csi1000): Stage-A corrected replay operator results"
git push
```

**Important SHA/resume trap:** the Modal wrapper keys `controls → sample → all` and hash-checked resume to the *same exact git HEAD*. An evidence commit pushed to the replay code branch **between** controls and all changes HEAD, so the subsequent run cannot find previously passed controls under the new prefix. Either (a) finish scopes at the unchanged clean code SHA and only then commit evidence to the PR #64 branch; or (b) publish preliminary controls evidence through a **separate GitHub evidence branch/PR or immutable evidence commit** without moving the running replay checkout/branch, and post its link in PR #64. If requesting an 8-cell review before the full run, use (b). After final commit, any resume must explicitly check out the original replay SHA on a clean worktree. Never relabel run outputs using the later evidence commit SHA.

Publishing GitHub evidence does not imply that reviewers have authenticated independent market/broker execution; missing raw-source access or a blocked cell remains a separately visible limit. Original Volume and full raw predictions remain immutable, and no previous Stage-A/Stage-B history is rewritten.

### Minimal checksum verification for a copied GitHub review scope

```bash
# Execute AFTER assembling the permitted subset, NOT inside the original source Volume.
EVIDENCE_SCOPE="results/csi1000_strategy_selection_evidence/pr64/$REPLAY_CODE_SHA/controls"
( cd "$EVIDENCE_SCOPE" && find . -type f ! -name SHA256SUMS.txt -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS.txt )
( cd "$EVIDENCE_SCOPE" && sha256sum -c SHA256SUMS.txt )
# Repeat for sample and all, with their real scope paths, BEFORE git add/commit.
```
