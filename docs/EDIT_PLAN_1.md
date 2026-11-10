# EDIT_PLAN_1: Dispatch claims table

Status: implementation in progress; backend data and dashboard contract complete.

Replace the list under **Claims in this dispatch** on the queue page with a table. Handlers should be able to compare the claims, their outcomes, when work began, how long completion took and each claim's model cost without opening every claim.

This plan treats "playout" in the request as **Payout**. It builds on the existing dispatch and its saved measurements.

## Design

### Table and behavior

Show these eight columns in this order:

| Column | Content |
| --- | --- |
| Claim | Claim ID linking to its existing claim page. |
| Customer | Customer's name. |
| State | The claim's state, using the existing state labels and colors. |
| Decision | Approved, Declined or Referred using the existing decision chips; `Pending` before a decision. |
| Payout | The recorded decision's payout in USD, such as `$420.00`; a recorded zero is `$0.00`, and a missing decision shows an em dash. An approved payout can still be awaiting sign-off or payment. |
| Started | When this claim first began work in this dispatch, shown as a local date and time; `Not started` before work begins. |
| Time to finish | Elapsed time from this claim's start in this dispatch to its completed outcome, shown in seconds with one decimal place. Unfinished work has an explicit text label. |
| Model cost | This claim's OpenRouter-reported cost attributed to this dispatch, with six decimal places; a known free response shows `$0.000000`, and missing provider cost shows `Unavailable`. |

Keep the backend's existing member order. Each claim appears once, including when it retries. Display a member's error or `Already being resumed` message within its State cell so those explanations remain visible alongside the eight requested columns.

Reuse the existing table styling, state and decision chips, money formatting and claim links. Use semantic table headers with `scope="col"` and associate the table with the **Claims in this dispatch** heading. Keep IDs, amounts and durations in JetBrains Mono. Put the table in the existing horizontal scroll treatment so all eight columns remain reachable on a narrow screen without widening the whole page.

The dispatch start form, run totals, records-built summary and separate claims queue continue to surround the table as they do today.

### Timing definition

**Time to finish means elapsed clock time for one claim in this dispatch.** This is separate from the existing **Active claim time** total, which adds the measured working time of all claims.

- Start with the earliest `work_slices.started_at` attributed to the claim and the current `run_id`. Do not use the customer's submission date, the dispatch's start time or work from an earlier dispatch.
- A declined or referred claim finishes at its recorded `decided_at` timestamp. An approved claim finishes at its recorded `paid_at` timestamp. An approval waiting for sign-off or payment is still unfinished.
- Calculate the completed duration as `finished_at - started_at`. Retries within the same dispatch retain its first start. Elapsed time includes any retry or sign-off wait between those timestamps; active work remains available separately in the existing measurements.
- Before a claim starts, display `Not started` in both timing cells. While it is being worked, show its start and `In progress` as the duration. For an approval still held, show `Awaiting sign-off` or `Awaiting payment` as appropriate. A claim left for a handler shows `Needs handler`; unresolved work in a stopped or finished dispatch shows `Not completed`. A skipped member with no work in this dispatch shows `Not worked` as its duration.
- Finished and stopped dispatches retain their existing snapshot behavior. A later sign-off, payment or new dispatch does not rewrite an older dispatch's table. A row frozen while awaiting sign-off remains an accurate record of that dispatch, while its claim link opens the current claim.
- Historical records can lack the measurements needed to establish a start or completed duration. Show `Unavailable` for a missing historical value instead of manufacturing a zero or substituting the dispatch's duration. Reject an inferred completion outside the dispatch's recorded time range.

Store timestamps in UTC as the backend already does. Display the start in the browser's local time, identify that convention near the table and use a semantic `time` element with the original ISO timestamp.

### Backend and API

Extend the existing required `RunMember` response fields in `backend/models.py` and `dashboard/src/api.ts` with:

| Field | Type | Meaning |
| --- | --- | --- |
| `customer_name` | string | Customer name for the claim. |
| `started_at` | string or null | First recorded work in this dispatch. |
| `finished_at` | string or null | Completed outcome timestamp within this dispatch, when known. |
| `elapsed_seconds` | number or null | Completed duration when both timestamps are known and valid. |

Continue using `claim_status`, `decision`, `payout`, `llm_cost` and `active_seconds` from the existing response. A member marked `finished` by the dispatcher can still have a claim awaiting sign-off, so that membership label alone must not establish claim completion.

The implementation uses existing records rather than adding tables or requiring a reseed:

- Extend the grouped work-slice aggregation in `backend/runs.py` to obtain each member's earliest start alongside its active seconds. Keep all measurement queries scoped to the run.
- Extend the bulk outcome query to include customer names and the decision/payment timestamps from the existing shared claim view and its underlying tables. Keep the computation of dispatch timing in `runs.py`.
- Include the new identity and timing values in the existing `run_claims.result` JSON snapshots when the run closes. Older snapshots are read compatibly, obtaining missing identity/measurement data in bulk while preserving their saved outcome. Unavailable historical timing stays null.
- Return complete rows through the existing run endpoints. Avoid a separate request or query per claim, and preserve the fixed query count at 1 and 25 members.
- Keep evaluation responses valid because `Comparison` inherits `RunMember`. Add the required response fields to shared test fixtures without changing the Scoreboard's visible columns or scoring formulas.

### Module responsibilities and dependencies

| Module | Responsibility |
| --- | --- |
| `backend/runs.py` | Assemble each run member's customer, outcome, timestamps and measurements; preserve closed-run snapshots. |
| `backend/models.py` | Define the required response fields, including explicitly nullable timing fields. |
| `dashboard/src/api.ts` | Remain the dashboard's only backend client and declare the matching strict TypeScript contract. |
| `dashboard/src/components/RunPanel.tsx` | Load the dispatch with the existing polling hook, retain the controls and summary, and render a small table component receiving the member data. |
| `dashboard/src/format.ts` | Supply the existing date/time, seconds, money and model-cost formatting; add a formatter only if the table needs a shared formatting behavior that is missing. |
| `dashboard/src/styles.css` | Reuse the current table and scroll styles, adding only the layout rules needed for this table. |
| Backend, dashboard and e2e tests | Verify data attribution, timing semantics, display and updates with offline fixtures and stand-ins. |

The dependency path stays `RunPanel -> api.ts -> backend run operations -> database records`. This design follows `CODE_STANDARDS.md`: each module keeps its own job, queries stay in the backend, the dashboard has one API client, framework components and existing helpers are reused, and agent instructions do not change.

## Phase 1: Expose accurate member identity and timing

Extend the run response and its existing queries first so the table can consume one complete response. Derive timing from persisted records and freeze it with the outcome, preserving the distinction between waiting for a handler and a completed claim.

- [x] Backend tests show every member has `customer_name`, `started_at`, `finished_at` and `elapsed_seconds`, with nullable fields present in JSON.
- [x] A claim whose first work is at `10:00:00Z` and completed outcome is at `10:00:42.500Z` returns `42.5` elapsed seconds, regardless of the dispatch's start time.
- [x] Tests cover paid approvals, declines, referrals, an approval awaiting sign-off, an approval awaiting payment, selected/unstarted members, skipped members and claims left for a handler.
- [x] A retry within the dispatch keeps the earliest start; a later dispatch uses only its own work. Cost and active time remain attributed to the correct run.
- [x] Closed-run tests prove that a later sign-off/payment or dispatch does not alter the earlier row's outcome or timing.
- [x] Existing snapshots without the new fields remain readable. Missing historical timing returns null, and timestamps outside the dispatch's interval do not produce a misleading completed duration.
- [x] The existing query-count test passes for 1 and 25 members without increasing its threshold. SQLite tests exercise the shared SQLAlchemy queries; the query design also supports Postgres.
- [x] `uv run pytest backend/tests/test_runs.py` and the affected measurement/evaluation tests pass with offline fixtures.

## Phase 2: Align the dashboard contract and formatting

Add the required fields to TypeScript and update shared fixtures before rendering them. Reuse the existing formatters so payouts and tiny model costs stay consistent with the rest of the product.

- [x] All `RunMember` and inherited comparison fixtures include the new fields, and strict TypeScript checking passes.
- [x] The table can distinguish a missing payout from `$0.00`, and a missing model cost from `$0.000000`.
- [x] Formatting checks cover a local start timestamp and a completed duration of `42.5 s`. Timezone assertions are deterministic rather than depending on the developer's local timezone.
- [x] `npm run check:ts` passes with the existing coverage thresholds.

## Phase 3: Render the eight-column dispatch table

Replace the ordered member list with the table, keeping the dispatch controls and summary in the existing panel. Make unfinished states legible instead of suggesting that every worker return means a resolved claim.

- [ ] `RunPanel` tests find a table associated with **Claims in this dispatch**, with the eight headers in the requested order: Claim, Customer, State, Decision, Payout, Started, Time to finish and Model cost.
- [ ] Each member produces one claim row with its customer, linked ID, state, decision, payout, start, completed duration or pending label, and model cost.
- [ ] Approvals held for sign-off display the approval and payout alongside `Waiting for sign-off` and `Awaiting sign-off`; they do not display a completed duration.
- [ ] Errors and skipped-member explanations remain visible within the table. No-run and no-members messages still render correctly.
- [ ] Dispatch controls, totals and the handler summary continue to render, and the separate queue table retains its existing columns.
- [ ] Unit tests verify the important states and zero/null distinctions. `npm run check:ts` passes.

## Phase 4: Verify live updates with offline integration tests

Use the existing polling behavior and agent-service stand-in to demonstrate the table changing in place. These checks exercise the user flow without changing the student's current queue or calling a model.

- [ ] A dashboard polling test changes a row from selected to in progress to completed without remounting; its start appears when available and its final outcome, duration and cost appear on refresh.
- [ ] Existing slow-request tests still prove that polling does not overlap requests. The table adds no per-claim network requests or separate polling loop.
- [ ] Extend `e2e/run.spec.ts` to locate the dispatch table separately from the queue table, assert its headers and customer, record work and cost through the test API, complete a claim and observe the resulting row without reloading.
- [ ] The e2e test follows the claim link successfully and still verifies the records-built summary. A held or unfinished claim does not show a false completed duration.
- [ ] `npm run e2e` passes against its independent SQLite backend and agent-service stand-in. Evaluation/Scoreboard regressions pass with the extended member contract.

## Phase 5: Review, visual verification and handover

Check the actual table at desktop and phone sizes, then update the product documentation with the final timing definition. Follow the implementation review and commit process in `docs/PROCESS.md`.

- [ ] Inspect the isolated test/preview app through Playwright MCP at desktop width and 390px, in light and dark mode. All columns remain reachable inside the table's scroll area, and the document itself does not overflow horizontally.
- [ ] Verify readable customer names, keyboard-accessible claim links, semantic headers, the local-time note, brand fonts/colors and right-aligned monetary values. Browser console shows no unexpected errors.
- [ ] A fresh reviewing subagent checks each implementation phase against `CODE_STANDARDS.md` and the diff; blocking findings are fixed before committing. Review the complete change against its starting revision before handover.
- [ ] Update `docs/STATUS.md` and the relevant visibility/timing description in `docs/TECH_STACK.md`, and tick this plan's verified criteria. Preserve the existing historical live-run evidence and unresolved Swarm timing checks.
- [ ] Run `npm run check` with offline fixtures and the existing thresholds, using the established temporary `.env` isolation/restoration procedure for the final check. Report actual results and any unverified criterion.
- [ ] Commit the checked implementation phases with hooks enabled. Do not push.

## Scope and execution

Implementation was authorized by the student's request to implement `EDIT_PLAN_1.md`.

The implementation does not need a reseed, schema migration, new paid model run or changes to `.env`. Keep the student's configured Nemotron model and existing business records. Use isolated fixtures for verification; do not repeat the earlier GPT Luna live-verification sequence for this table change.

Changes are limited to the dispatch table and the response data it needs. Sorting controls, filters, CSV export, agent behavior changes, payment-rule changes and later course features are outside this plan.
