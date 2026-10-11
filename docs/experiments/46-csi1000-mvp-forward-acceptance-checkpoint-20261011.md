# 46 — CSI1000 forward MVP actual-verification checkpoint (2026-10-11 CST)

> **Working line A only.** The entry is [44](44-csi1000-mvp-forward-operations-handoff-20261011.md), bounded by [42](42-csi1000-mvp-handoff-after-ledger-pass-20261011.md) Audit Closure and [43](43-csi1000-mvp-daily-publication-contract-20261011.md) publication runbook. This checkpoint records *observed* results vs things not accessible from this session. It is not a production Modal attestation. All times below are Asia/Shanghai unless stated otherwise.

## Verified GitHub baseline

- At checkpoint, `main` HEAD = `9316751309e35e4906d1d544118cbed4d190001f`; [PR #58](https://github.com/AT2018cow/qlib/pull/58) merged 2026-10-11 07:52:09 CST. [PR #59](https://github.com/AT2018cow/qlib/pull/59) remains open, handoff branch prior-to-this-checkpoint HEAD = `d616cbd77be92f8f4d62e54744659f9c17630b1d`.
- On that exact `main` SHA, [MVP contract #8](https://github.com/AT2018cow/qlib/actions/runs/38096496617) **PASS**, log: `45 passed in 0.23s`; [tuner core #117](https://github.com/AT2018cow/qlib/actions/runs/38096496618) **PASS**; [Pages deployment #60](https://github.com/AT2018cow/qlib/actions/runs/38096496627) **PASS** in GitHub deployment logs at 2026-10-11 07:52:35 CST, environment URL `https://at2018cow.github.io/qlib/`.
- Deployed Pages artifact ID `11685804370`, build-log ZIP SHA256 `731bf53f7fec453eb434a338f36a4082b5e73291981bf8d006f615cb5d8a9b5f`. Artifact was downloaded and inspected; this verifies **packaged site** rather than a real public browser request.
- The only latest available canonical signal dates in `main` and the Pages bundle are usage days `2026-10-09` (signal data `2026-10-08`) and `2026-10-08` (signal data `2026-09-30`). Each has exactly 20 unique Top20 CSV rows and a chart and paper JSON; neither has the new `_mvp_metadata.json` sidecar. The older records are **legacy/incomplete**, not daily-MVP PASS. On 2026-10-09 paper: `paper_lineage=stage_b_winner_canonical`, `execution_report=null`, pending signal `2026-10-08` / execution `2026-10-09`.
- File *content* SHA256, computed from downloaded Pages artifact, are below (not Git blob IDs):

| usage date | artifact | SHA256 |
| --- | --- | --- |
| 2026-10-08 | `top20_lgb158.csv` | `24ff3a7964c68b0ffd3a3b7ba5098911f7fb45b231d811070f786987d6f2095b` |
| 2026-10-08 | `chart.json` | `7aad69d1ed6b72163ea01ceb26c4cb85555f34a1ca04bd79fa08667df073a957` |
| 2026-10-08 | `paper_portfolio.json` | `53f0e8ebf7dcfe1b7d306d9e7b382cc80ef14ace17c3f6cad0983c6dcaf4b772` |
| 2026-10-09 | `top20_lgb158.csv` | `64813f3fcb14768e9b70f81baa9789136f9e81730a998953459038266105218f` |
| 2026-10-09 | `chart.json` | `0abe9f0f5416882c227db4e5f92ee8344ec25480f8a968d2c930407c62752426` |
| 2026-10-09 | `paper_portfolio.json` | `c36edb6d7e1fd09b5ad06b3b251b1bd9b17798a63330bdbcf77709c8a9cfc487` |

- Pages bundle generated `signals/csi1000_forward_performance.json` with `status=awaiting_first_valuation`, `start_date=2026-10-12`, `points=[]`, `latest_date=null`; **PASS no fabricated forward NAV**.
- Real public `github.io` HTTP/DOM/E2E was **NOT_VERIFIED** due to this execution environment's network/browser access restrictions. Deployment success does not prove publicly rendered content; must be checked from an authorized internet-capable browser.

## Upstream source release / real-provider boundary

- As queried via GitHub API, latest upstream [chenditc/investment_data release `2026-10-11`](https://github.com/chenditc/investment_data/releases/tag/2026-10-11) published 2026-10-11 00:35:59 CST; archive `qlib_bin.tar.gz` bytes `567432997`, **GitHub release asset** SHA256 `0c4091fe82b2c9a36eb093248223a59fbe5346001a21b4a594c6b2cff77f7c49`; manifest SHA256 `4b52ec0ae6b24a211986ddfdd23c01ac563a87574d5a77ed93bb47b1f318ba0d`.
- This proves an upstream *release was advertised*, not its actual `calendars/day.txt` end day nor PIT span count nor 20-session label maturity or live cached model. The complete provider archive was **not** independently downloaded/extracted; the production Modal environment was not accessible. Status **NOT_VERIFIED**, not PASS.
- **PASS_EXCHANGE_CALENDAR_ONLY:** independently checked official [SSE 2026-09-17 holiday announcement](https://www.sse.com.cn/disclosure/announcement/general/c/c_20260915_10832273.shtml) and [SZSE 2026-09-17 notice](https://www.szse.cn/www/disclosure/notice/general/t20260917_622911.html): 2026-10-01 through 10-07 closed, 10-08 reopened, 10-10 weekend closed. In the published exchange schedule, 2026-10-09 is the prior trading session for Monday 2026-10-12. Actual **provider** calendar end and completeness of the 2026-10-09 bar are still **NOT_VERIFIED**. Do not use the release *tag* as a data-freshness proof.

## Runtime-blocker identified and minimal corrective PR

- **Source-level blocker, deployment NOT_VERIFIED:** `modal_qlib_cn_a10g.py` installs only package `qlib` and copies top-level helpers from `/root/qlib` into its entrypoint directory `/root/`. The freshly introduced `csi1000_mvp_contract.py` was missing from this explicit copy list; `daily_standalone` imports it while reading the provider. This can break real Modal execution with `ModuleNotFoundError` even while local CI passes.
- [PR #60](https://github.com/AT2018cow/qlib/pull/60) is a **separate reviewed-runtime-fix PR** (not merged into `main` at this checkpoint): image module copy fixed in `81e4ca817b09acc0d8ad1dca10bf0a2c63b96ce5`, AST-based regression added in `78daefd9451dcf494db27de84062d0122734dd47`.
- [PR #60 MVP #9](https://github.com/AT2018cow/qlib/actions/runs/38098493479) **PASS**, `46 passed in 0.29s`; [tuner #118](https://github.com/AT2018cow/qlib/actions/runs/38098493474) **PASS**, `118 passed, 11 subtests passed in 0.51s`. **Static only**, not actual Modal import/deploy PASS.
- No canonical switch or historical input changes. The frozen identity remains `stage_b_winner` / `stage_b_winner_canonical`, candidate `4e908173705c76fee3782be37068672a3a845bd61894202f3152afe3b9d81ef2`, expected model-config SHA256 `0f2cf94d179e7b9982f873588a3afe25fa02991c873954fea11d2db0e025cb88`, snapshot token `51756897fc75230493194aef2e48815e4e9f3cc7426cc135f47bb8ba8e0d21e1`.

## Current acceptance ledger

| Check / planned chain | Actual evidence | Status |
| --- | --- | --- |
| Git main / PR58 post-merge CI | SHA / run links / logs above | PASS |
| Post-merge Pages build/deploy | Successful workflow; verified artifact ZIP | PASS_BUILD_ONLY |
| Latest 2 historical signal file dates, SHA256, Top20 and paper fields | Observed in `main` and Pages bundle | PASS_STATIC_ONLY |
| Current upstream release available / advertised archive identity | Release API tag and SHA256 | PASS_RELEASE_METADATA_ONLY |
| 2026-10-12 prior A-share exchange trading session | SSE + SZSE official notices: 2026-10-09 | PASS_EXCHANGE_CALENDAR_ONLY |
| Actual production provider archive calendar, exact previous session freshness | Modal/provider content not accessible | NOT_VERIFIED |
| 2026-10-12 CSI1000 PIT count 1000 | No actual provider `instruments/csi1000.txt` read | NOT_VERIFIED |
| Actual Label20 train/valid maturation and fit clock | No live cached split/preflight proof | NOT_VERIFIED |
| Actual frozen model, prediction and full paper ranking lineage | Static config pinned; no live smoke | NOT_VERIFIED |
| Modal deployed SHA, cron schedule, last run, `github-push` secret usable | No authorized Modal environment connection | NOT_DIRECTLY_VERIFIED |
| Source decorator for 07:00 Asia/Shanghai weekday cron | `schedule=modal.Cron("0 7 * * 1-5", timezone="Asia/Shanghai")` | PASS_SOURCE_ONLY |
| 2026-10-12 same-date CSV/chart/paper/metadata commit + website | Future date, not yet legitimately published | PENDING_MARKET_DATA |
| 2026-10-12 real paper open/close settlement & quote-union cash/NAV | Market data not yet available | PENDING_MARKET_DATA |
| 2026-10-12 forward NAV, website public DOM/browser E2E | Empty prestart series; public browser inaccessible | PENDING_MARKET_DATA / NOT_VERIFIED |
| Runtime missing helper fix | PR #60 submitted, 46+118 tests pass; still unmerged | BLOCKED_PENDING_REVIEW_DEPLOY |

## Commands for **authorized production Modal operator** (capture redacted logs)

> Execute from a checked-out, reviewed and merged `main` containing the PR #60 fix; do not deploy an unreviewed branch or assume a workspace/environment. `MODAL_ENV` must name the real authorized production environment. `daily_standalone` defaults to `csi1000_profile=None` and paper lineage `canonical`, so **explicitly supply** the frozen profile/lineage in preflight instead of the otherwise generic default shown in [43](43-csi1000-mvp-daily-publication-contract-20261011.md).

```bash
set -euo pipefail
export MODAL_ENV="<authorized-production-env>"       # set by authorized operator
git fetch origin main
git switch main
git pull --ff-only origin main
git rev-parse HEAD                                      # record reviewed deployment SHA
git grep -n 'csi1000_mvp_contract.py' -- modal_qlib_cn_a10g.py
modal app info qlib-cn-daily-a10g --env "$MODAL_ENV" --json | tee modal-info-before.json
modal app history qlib-cn-daily-a10g --env "$MODAL_ENV" --json | tee modal-history-before.json
modal app logs qlib-cn-daily-a10g --env "$MODAL_ENV" --since 24h --timestamps | tee modal-logs-before.log
modal function info qlib-cn-daily-a10g/daily_cron --env "$MODAL_ENV" --json | tee modal-cron-before.json

# Preflight downloads real provider and checks PIT/Label20/lineage. Does not
# write public artifacts or advance persistent paper state.
modal run modal_qlib_cn_a10g.py::daily_standalone --env "$MODAL_ENV" --timestamps \
  --topk 20 --nd 2 --market csi1000 \
  --csi1000-profile stage_b_winner \
  --paper-lineage stage_b_winner_canonical 2>&1 | tee modal-preflight.log

# Deployment requires operator's explicit review/approval first.
modal deploy modal_qlib_cn_a10g.py --env "$MODAL_ENV" --stream-logs --timestamps 2>&1 | tee modal-deploy.log
modal app info qlib-cn-daily-a10g --env "$MODAL_ENV" --json | tee modal-info-after.json
modal app history qlib-cn-daily-a10g --env "$MODAL_ENV" --json | tee modal-history-after.json
modal function info qlib-cn-daily-a10g/daily_cron --env "$MODAL_ENV" --json | tee modal-cron-after.json
```

**Only after 2026-10-12 begins in Asia/Shanghai, independent calendar confirms 2026-10-09 is previous trading session, and provider's 2026-10-09 complete bar/PIT+Label20+hash gates pass**:

```bash
modal run modal_qlib_cn_a10g.py::daily_cron --env "$MODAL_ENV" --timestamps 2>&1 | tee modal-2026-10-12-publication.log
modal app logs qlib-cn-daily-a10g --env "$MODAL_ENV" --since 24h --timestamps | tee modal-2026-10-12-logs.log
git fetch origin main
git log -1 --format='%H %cI %s' origin/main
for artifact in top20_lgb158.csv chart.json paper_portfolio.json mvp_metadata.json; do
  curl -fsSL "https://raw.githubusercontent.com/AT2018cow/qlib/main/results/signals/2026-10-12_${artifact}" -o "2026-10-12_${artifact}"
  sha256sum "2026-10-12_${artifact}"
done
python3 - <<'PY'
import json
m=json.load(open("2026-10-12_mvp_metadata.json"))
p=json.load(open("2026-10-12_paper_portfolio.json"))
assert m["publication"]["usage_date"]=="2026-10-12"
assert m["pit_universe"]["status"]=="PASS" and m["pit_universe"]["member_count"]==1000
assert m["label_maturity"]["status"]=="PASS" and m["label_maturity"]["horizon_sessions"]==20
assert p["signal_data_date"]=="2026-10-09" and p["usage_date"]=="2026-10-12"
assert p["pending_orders"]["execution_date"]=="2026-10-12"
assert p["paper_lineage"]=="stage_b_winner_canonical"
assert p["production_lineage"]["model_config_sha256"]=="0f2cf94d179e7b9982f873588a3afe25fa02991c873954fea11d2db0e025cb88"
print("PASS: published-date/PIT/Label20/paper static sidecar bindings; confirm provider hashes, full ranking and Modal logs separately")
PY
curl -fsS 'https://at2018cow.github.io/qlib/signals/available_dates.json'
curl -fsS 'https://at2018cow.github.io/qlib/signals/2026-10-12_mvp_metadata.json' -o site-metadata.json
sha256sum site-metadata.json
```

On **next real provider refresh once 2026-10-12 execution-day open/close prices exist**, run the scheduled next usage-day cron **once**, inspect previous 2026-10-12 pending execution report in that new day's paper artifact, reconcile bought/sold/blocked/cash/holdings/valuation and `csi1000_forward_performance.json` from Pages. A same-date retry must be byte-identical; drift must **fail closed**, not overwrite. Do not synthesize missing bar, fill, ST, suspension, quote, return, or 2026-10-12 NAV.

**Escalation rule:** current incorrect provider/PIT/Label20/model hash/paper dates/quote-union is a live-MVP P0: stop publication and repair that gate. Historical Stage-B audit and 4 historical ST sells remain closed/deferred by [42](42-csi1000-mvp-handoff-after-ledger-pass-20261011.md); no retuning, strategy switching, forward backfill, or live trades.
