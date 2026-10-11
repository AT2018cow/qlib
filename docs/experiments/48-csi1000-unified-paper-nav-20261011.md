# 48 — One-engine paper execution / cumulative NAV (2026-10-11)

**Scope:** one PR, one execution policy, one paper account kernel. No model fit,
no Stage-B reranking, no canonical lineage switch, no historical result overwrite.

## Answer to historical vs forward

For **new** historical research, `paper_replay.py` sends frozen PIT rankings,
execution-day adjusted quotes, factors, pre-open ST evidence and dated listing
information into the exact `PaperPortfolio.plan_signal/execute_pending` used
by forward production. The price-limit policy lives in the pure
`execution_limits.py`; Qlib's `BoardAwareExchange` and paper replay use that
same mask function. Position lot rounding, cash, fees, mark-to-close, NAV and
checks therefore have a single paper implementation.

**The original frozen Stage-B 55 Qlib reports are NOT automatically rewritten
or retroactively certified.** Replaying them with this kernel still requires
the original daily full rankings, PIT ST identities, valid quotes and factors
from the provider. A new replay is a separately versioned result, never a
silent replacement for the frozen research record.

## What is checked before public forward NAV may update

1. A new paper order's execution-date ST risk observation is frozen at the
   execution day's 07:00 run, then reused for settlement after the execution
   day's bar becomes available. Failure to load ST data blocks a verified
   post-2026-10-12 settlement; yesterday's current ST listing is not
   reconstructed by querying tomorrow's source.
2. Both historical and forward paths use board-aware, correctly tick-rounded
   open-limit masks. The research/live execution approximation assumes full
   fills at the recorded open **only when tradable**. It intentionally ignores
   queue position, order book depth and participation/partial-fill impact,
   per the small-notional product scope; this is not real broker fill evidence.
3. Strict settlement requires usable closes for all marked holdings, except
   confirmed and explicitly identified suspensions with a last valid close.
   Generic provider gaps are never silently carried into a published NAV.
4. `paper_accounting.verify_ledger` recomputes ending cash from beginning
   cash and all buys/sells/fees; adjusts beginning share inventory for all
   fills; and independently recomputes closing NAV from cash and dated marks.
   Cent-level differences fail the run.
5. `forward_performance.py` only accepts verified paper execution reports,
   rejects duplicates with disagreements and gaps in documented scheduled
   executions, and checks each session's opening cash and inventory against
   the preceding verified closing state. The original 2026-10-12 planned
   start is retained while `inception_date` records the real first verified
   day. The Pages build fails if post-start paper JSON is corrupt.

## No-data and state rules

- Before the first verified settlement the website displays
  `awaiting_first_valuation`, never a synthetic zero or retrospective
  backfill. If any subsequent required report cannot be verified, publication
  of a new return is stopped.
- The website is still *paper simulated*, inclusive of configured buy/sell
  costs, not verified exchange executions or real account P&L.
- Persistent canonical paper capital is deliberately **not reset**. The
  existing code seeds new production state at **CNY 100,000,000**; existing
  states retain the capital saved in their config. This is NOT automatically
  a small-money test account: changing the displayed portfolio's capital
  needs a separately approved fresh-account inception to avoid mixing
  accounts/returns. Absence of depth modeling is an explicit assumption
  rather than proof that all actual fills are available.
- Production needs the real Modal ST snapshot, the provider's next actual
  open/close bar, real paper-state settlement, Pages deployment and a
  browser-level check. CI is fixture-only and cannot certify these external
  dependencies. No post-2026-10-12 live fill exists as of this document date.

## Tests

`tests/test_paper_accounting_replay.py`: historical/forward common kernel,
pre-2026-07 main-board ST 5% limit, evidence required, unknown close
fail-before-mutation, certified halt carry and forged-cash negative control.

`tests/test_forward_performance.py`: inception, accounting, lineage, missing
scheduled settlement, invalid close, inconsistent duplicate and delayed first
valuation. The `unified-paper-nav.yml` workflow also runs existing paper
portfolio tests, static syntax, JS check and a read-only forward JSON build.

## Acceptance / release boundary

Approve only after CI passes, human review of unanticipated state mutations,
and a future provider-day read-only end-to-end smoke. Merge alone must not be
confused with deploying Modal or producing a verified website return.
