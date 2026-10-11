# 52 Forward Shadow PR-B preflight handoff (2026-10-11)

Parent binding decision protocol: docs/experiments/47-csi1000-forward-shadow-strategy-decision-protocol-20261011.md. This PR is ONLY structural preparation; NOT A-line integration, NOT an active Shadow experiment, and cannot be approved as a deployed dual-paper experiment.

Exactly two frozen legs:
- stage_b_winner, stage_b_winner_canonical (production remains unchanged), candidate 4e908173705c76fee3782be37068672a3a845bd61894202f3152afe3b9d81ef2.
- baseline_control, future independent stage_b_baseline_shadow_v1, candidate 23b92de05cf36c82998de684d0fbf64d81ee54490d755bd3cf96311c00286785.

audit/forward_shadow_pair_preflight.py checks pre-registered identity/config hash, earliest eligible date (2026-10-12), both legs, same signal_data_date/feature-as-of/calendar/provider/PIT hash, two distinct CNY 100m paper account IDs, nonfuture model fit, externally provided outcome time, previous pair hash chain, and no realized return fields in prediction. Returns STRUCTURAL_PREFLIGHT_ONLY_NEEDS_IMMUTABLE_RECEIPT_AND_SETTLEMENT intentionally. It cannot authenticate the outcome time passed by the operator, actual label-maturity 20-session calendar, raw predictor bytes, immutable external receipt, individual PaperPortfolio ledger state or Modal deployment. Those MUST be added and independently audited BEFORE A-line publication; a local JSON hash is not proof of pre-outcome existence.

Required next integration engineering (separate reviewed patch):
1. A-line approved production extension creates and verifies both fresh model predictions from the SAME as-of timestamp; do NOT replay old Stage-B history. If first pre-outcome paired prediction cannot precede 2026-10-12 observed results, start later. Training checkpoints and seeds/label maturity/PIT universe must be fully recorded. No automatic changes to CANONICAL_PROFILE, existing publisher, legacy paper state, or website.
2. Bind to authenticated append-only timestamped storage with external verifiable receipt before outcome. Bind complete ranked signal, hash, PIT membership snapshot, T-close/T+1-open pending order SHA, external prior chain hash. Reject incomplete pair, duplicate date, post-outcome/backfilled pair, wrong SHA/as-of/calendar.
3. Create separate baseline PaperPortfolio initial_cash=100_000_000 and separate state directory + lineage; never copy winner cash, holdings or pending state. Winner still uses only its pre-existing canonical publication. Use frozen Top20/Drop2, same 5bp/15bp/5 CNY fees, ST/IPO/limits/halts/quotes/factors and PR62/63 validated ledger; independently settle each at actual next eligible open and verified close; missing bars must not be fabricated.
4. Daily P0-only monitoring of pair count, source integrity, paper accounting and rejected/late pairs; no interim strategy selection/scores before prespecified 120/240 valid paired sessions. 95% all-calendar coverage required, 5-session moving block bootstrap 10k seed 20261011; alpha 0.01/0.04, point estimate >=+3.0 annual arithmetic pp, baseline LCB>0, +10bp stress still positive, MDD and turnover risk veto per #47.
5. Human reviewed PR-B deployment evidence must contain Modal app/environment, pre-outcome immutable receipts, two account snapshots, exact model config/checkpoint hashes, real trading-session smoke, PR62/63 accounting negative controls, and rollback. GitHub merge alone is not deployment or sample-out-of-time validity.

Run CI syntax tests: python -m pytest -q tests/test_forward_shadow_pair_preflight.py. Do not activate until A-line signoff. Default strategy remains KEEP_CANONICAL_PENDING_FORWARD.


## GitHub submission and external time proof (operator → reviewer; REQUIRED, 2026-10-11)

**At the current PR #65 stage there is NO deployed two-leg Shadow, NO authentic forward paired prediction, and NO settlement to upload.** A synthetic test fixture must never be renamed `forward_result`. Evidence submission is split into *preflight now*, *prospective receipt after real integration*, and *prespecified 120/240-session independent decision review*. Only the preflight can be produced by this PR alone.

### A. PR #65 structural preflight bundle — can be committed now

Create `results/csi1000_strategy_selection_evidence/pr65/<PREFLIGHT_CODE_SHA>/preflight/` and commit:
- `preflight_operator.json`: real git SHA, exact command `python -m pytest -q tests/test_forward_shadow_pair_preflight.py`, UTC runtime, Python/pytest versions, CI URL/run ID and observed PASS/FAIL counts, protocol 47 git SHA/blob SHA, candidate/config frozen hashes, operator and environment, `deployed=false`, `prospective_pairs=0`, `paper_settlements=0`, `decision=KEEP_CANONICAL_PENDING_FORWARD`. Never invent data.
- `preflight_tests.log`: actual stdout/stderr from the local test invocation and any CI/negative-control failures. Include a `SHA256SUMS.txt` generated from the submitted bytes and a `BLOCKERS.md` naming missing A-line integration, externally time-stamped append-only receipt and independent baseline account.
- If any actual integration smoke is later added, its exact code SHA, negative controls and audit output belong in a separate versioned `integration-smoke/` directory and **must be labeled simulation/test**, not historical signal or prospective return.

The preflight validator returns `STRUCTURAL_PREFLIGHT_ONLY_NEEDS_IMMUTABLE_RECEIPT_AND_SETTLEMENT`; no PASS here is authorization to deploy, to publish a realized pair, or to change Winner.

### B. Real dual-Shadow evidence — only after an approved PR-B *integration* and operator deployment

For **each genuine eligible `usage_date`** create a versioned, non-overwriting path:
`results/csi1000_strategy_selection_evidence/pr65/<DEPLOY_CODE_SHA>/forward/YYYY-MM-DD/`.
Commit only after a real event has occurred, with clear `NOT_STARTED`, `PREDICTION_RECEIPTED`, `SETTLEMENT_VERIFIED` or `INVALID_PAIR` status; no retrospective repair of prediction timestamps or missing legs.

Required **public review/index bytes** for each day (all secret fields removed):
- `prediction_manifest.json`: exactly Winner and Baseline, each immutable candidate/config/checkpoint/signal/ranking/top20/pending-order SHA256; both `signal_data_date`, `usage_date`, `feature_asof`, `train_end/model_fit_asof`, label-maturity proof, provider/PIT-universe/calendar fingerprints, UTC prediction creation and publication times, code SHA, separate paper account/lineage IDs and initial CNY 100m, `previous_pair_hash`, deterministic `pair_hash`, status, and source object IDs.
- `pre_outcome_receipt.json`: **independently verifiable append-only external** record/URL, object version/ETag/content SHA, server-observed UTC time **strictly before the independently recorded first observable result/fill**, previous-chain linkage and a redacted proof of the independent outcome cutoff. A local JSON timestamp or GitHub commit after outcome visibility is **not** proof of prospective prediction.
- `settlement_manifest.json` (only after genuine settlement): independently accounted paper ledger hashes for each leg, observation/bar timestamps, raw account/position/transaction/valuation artifact hashes, separate starting cash and per-leg state, error/no-fill/halt flags, cash+holding-value=NAV reconciliation, incremental research paper results source IDs and the linked prediction/receipt hashes. If awaiting bars, mark `PENDING_NOT_SETTLED` and **do not** fabricate a NAV.
- `rejection_or_gap.json` for rejected/missed/pending sessions, with actual trading-session calendar entry, error category and no-backfill cause. Do not silently remove a weak or missed day.
- `SHA256SUMS.txt` and `operator_environment.json` covering exact Modal app/deployment SHA vs GitHub code SHA, provider identity, real commands, scoped/redacted logs and spend attributable to Shadow. If time/receipt proof cannot be published, mark `BLOCKED_PROSPECTIVE_PROOF` instead of `PASS`.

**Raw evidence retention:** keep complete pre-outcome dual ranked-signal bytes, authenticated external receipts, paper pending orders, full independent account journals, executed/unfilled order records, bar/factor/halt evidence, immutable per-day snapshots and rejection history in reviewer-accessible controlled storage with a **complete SHA index**. For the public GitHub repository, publish hashes, dates, coverage, non-return integrity outcomes and reproducible access/provenance instructions; never publish Modal tokens, secrets or proprietary provider dumps. Hash-only public manifests establish traceability, **not full independent content audit** without later approved access to underlying bytes.

**No early strategy performance peek:** before the first predeclared 120-valid-pair look, public GitHub daily receipts/metadata must not calculate or disclose comparative performance, daily baseline-minus-winner returns, partial Sharpe/rank, or surprise endpoint. Full ledger values remain immutable in controlled audit storage until the authorized look. If 120 sessions not reached or calendar coverage <95%, publish `INSUFFICIENT_FORWARD_EVIDENCE`/`INVALID_COVERAGE`, not a winner.

### C. Decision review packet — after 120 valid paired sessions, optionally 240

Only at the registered analysis checkpoints, submit under `.../look_120/` or `.../look_240/`:
- `calendar_coverage.json` (all eligible sessions, missing/late/invalid/rejected dates, >=95% gate), `paired_settlement_index.json` (all verified daily manifest + receipt hashes), `independent_account_reconciliation.json` (each leg's starting capital, cash/shares/NAV/fees/no-fill and 2 independent lineages, reproducibility steps), and `source_hashes.json`.
- `statistics.json` with date range, 120/240 count, 238×paired-mean **arithmetic** annualized percentage-point difference, moving-block length 5, 10,000 resamples, seed 20261011 + exact numpy/PCG64 version, **99% one-sided bound at 120** or **96% at 240**, base/+10bp/+20bp cost stress, 3 pp materiality, MDD 5pp and turnover 1.25x gates, pending/ST/IPO/halt/limit exceptions, and explicit `PROPOSE_SWITCH` vs `KEEP_CANONICAL` recommendation.
- `negative_controls.json`, redacted real Modal run logs/receipts, independent reviewer signoff, rollback plan and `SHA256SUMS.txt`; release of complete previously frozen paired daily ledger/return inputs **for authorized independent audit**, subject to public data restrictions.

Even if all metrics pass, the current canonical is unchanged until a **separate, explicitly human-approved strategy-switch PR**. If a mandatory receipt/bar/ledger is unavailable, state the blocker instead of presenting a favorable model-choice conclusion. GitHub post-settlement submissions are review copies of externally **pre-outcome** originals, never the registration itself.

### Push / reviewer notification

Commit the above paths to a separate append-only evidence branch/PR or to the relevant PR branch **after freezing the actual code SHA and timestamps**. Include the immutable GitHub commit URL plus external receipt registry/Modal App link in a **comment on PR #65** for independent review. Do not update past-day receipts, overwrite files, mix Stage-A retrospective files into these directories, or change the live Winner publisher. Do not submit real forward records at all until the missing integration/deployment and pre-outcome external recorder have been reviewed and verified.
