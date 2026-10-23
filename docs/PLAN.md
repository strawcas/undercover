# Pilot implementation plan

## Goal and scope

Build the claims pilot described in `docs/SPEC.md`: a local claims platform where a handler can run the UnderCover Agent on a claim, follow its work, inspect its decision and policy references, and sign off an approved payout over $1,000. The backend must enforce that payment rule independently of the agent.

The starting point is the health-check skeleton in `backend/`, `agent/`, `dashboard/` and `e2e/`. Keep the existing FastAPI, SQLite, LangChain, React, Vite and TypeScript stack and checks. Add Tailwind for the dashboard. Use Python 3.12 with uv and Node 26 as specified in `docs/TECH_STACK.md`.

The pilot covers the 25 claims in `data/claims.json`. Read only the seed files needed for those claims and the policy wording. Do not open or import `data/evals/`, `data/security/` or the claims manual. Although the policy and customer files also contain records for other claims, expose only records linked to the pilot queue. Do not use the manual-derived repeat-claim or meal-allowance settings in `company.json` as decision rules.

Exclude sign-in, real payments, customer messages, evaluation scoring, dispatchers, durable agent execution and cloud deployment. Referral records the need for human judgment or more information; collecting documents and resolving referrals are outside this pilot.

## Design decisions

- **Backend owns the records.** Use a small SQLite repository, with JSON fields for nested source records where useful. Persist claim state, decisions, sign-offs, simulated payments and activity events. Seed explicitly named files once without overwriting later work.
- **Agent uses HTTP only.** The agent fetches its instructions and claim context from backend APIs, and uses backend tools to record decisions, payments and activity. It never opens the database or business-data files.
- **One claim per run.** A handler starts a claim from the dashboard. The backend creates a thread ID, marks the claim in progress and starts a background task that calls `run(claim_id, thread_id)`. This is a local pilot task, without a durable worker or swarm.
- **Poll for progress.** Poll claim state and ordered activity events while work is active. This provides visible progress without a separate streaming service. Show observable actions and tool results, not hidden model reasoning.
- **Separate decision, workflow and payment.** An approved claim remains approved when it needs sign-off. A referral is a decision, distinct from approval awaiting sign-off. Payment is its own operation and record.
- **Keep money exact.** Store monetary values as integer cents, validate API amounts to two decimal places and show USD with two decimal places. Compare the payout, rather than the amount originally claimed, with the approval limit.

### Claim lifecycle

| Trigger | Queue state | Decision and payment |
| --- | --- | --- |
| Seed a claim | Waiting | No decision or payment |
| Start an agent run | In progress | Reject another start while this run is active |
| Record a decline or referral | Decided | Zero payout; payment unavailable |
| Record an approval up to and including $1,000 | Decided | Approved; eligible for simulated payment |
| Record an approval over $1,000 | Waiting for sign-off | Approved; payment blocked |
| A handler signs off that payout | Decided | Approved; eligible for simulated payment |
| Pay the approved amount | Decided | One simulated payment recorded |

Saving a decision does not execute payment. The agent calls the separate payment operation for an eligible approval. After sign-off, the handler can use the dashboard's payment action. The dashboard also permits payment of an eligible approval if the agent stopped before paying it.

Sign-off records the selected handler, time and exact approved payout. The agent has no sign-off tool. With no sign-in, selecting a handler is attribution within the shared pilot, not authenticated identity. Regardless of the caller, the payment API checks for a stored sign-off before any payout over $1,000.

### Planned API surface

All routes have unique `operation_id` values, one-line docstrings and no optional inputs. Request models define required fields explicitly; operations that need only a path ID do not require a redundant body. All routes live under `/api`, alongside the existing health route.

| Operation | Purpose and response |
| --- | --- |
| `GET /claims` | Return queue rows with identifiers, customer, claim type, claimed amount, workflow state, outcome and payment state |
| `GET /claims/{claim_id}` | Return the full claim, document text, linked policy, plan, customer history, decision, sign-off and payment state |
| `GET /policy-wording` | Return policy wording and its version |
| `GET /agent-instructions` | Return the exact instructions used for new agent runs; no edit route |
| `GET /company` | Return company name, currency and payout approval limit |
| `GET /handlers` | Return handlers available for sign-off |
| `POST /claims/{claim_id}/runs` | Start one run; return `202` with claim ID, thread ID and in-progress state |
| `GET /claims/{claim_id}/events` | Return ordered, timestamped activity events |
| `POST /claims/{claim_id}/events` | Record an event with required thread ID, action, status and detail |
| `POST /claims/{claim_id}/decision` | Record outcome, payout, calculation, two- or three-sentence explanation and relevant policy sections, linked to the active thread |
| `POST /claims/{claim_id}/sign-off` | Record a valid handler's sign-off for the stored approved payout |
| `POST /claims/{claim_id}/pay` | Take the explicit amount and record a simulated payment only when eligible |

Use `404` for unknown records, `422` for invalid request values and `409` for conflicting lifecycle actions, including blocked or duplicate payments. Use database transactions for starting runs and changing decisions, sign-offs or payments so concurrent calls cannot create duplicate work or bypass the payment rule.

## Phase 1: Seed and persist the pilot records

Build the SQLite repository, typed record models and explicit seed loader. Translate the source `open` status into the pilot's waiting state. Load the 25 queue claims, their linked policies and customers, the three plans and add-ons, handlers, relevant company settings and policy wording. Store backend-owned agent instructions for later API access.

This establishes a dependable source of truth before adding agent actions or screens. Use temporary databases in tests and an ignored local database for development.

Success criteria:

- [x] `uv run pytest backend/tests/test_seed.py -q` passes: a fresh database contains exactly the 25 queue claim IDs, all initially waiting, with all required relationships and complete nested items and documents preserved.
- [x] Seed tests verify the three plans, add-ons, three handlers, USD currency, $1,000 limit and policy wording version against the permitted source files.
- [x] Tests verify that only policies and customers referenced by queue claims are imported. The loader uses an explicit file allowlist and never discovers files recursively; file-access tests fail on any access outside that allowlist without reading evaluation files.
- [x] A repeat seed leaves record counts and an existing synthetic decision unchanged. Closing and reopening the same database preserves that decision and its events.
- [x] Money tests round-trip representative cents accurately and reject amounts with unsupported precision; there is no floating-point threshold comparison.
- [x] `npm run check:python` passes with at least 80% Python coverage and no relaxed checks.

## Phase 2: Claims APIs and the payment gate

Build the read APIs, decision recording, sign-off, payment and activity endpoints. Validate decisions and lifecycle transitions in the backend. Keep the policy assessment in the agent; do not build a second claims-adjudication rules engine in Python.

Approvals require a positive payout, while declines and referrals require zero. A payment must equal the stored approved payout, belong to an approved claim and occur only once. For payouts over $1,000 it must also have a valid sign-off for that exact payout. Recorded decisions are final for this pilot, which keeps sign-offs from becoming stale through later edits.

Success criteria:

- [x] `uv run pytest backend/tests/test_api.py backend/tests/test_payments.py -q` passes, using isolated databases and synthetic decisions.
- [x] With the backend running, `Invoke-RestMethod http://localhost:8000/api/claims` returns 25 waiting claims. `Invoke-RestMethod http://localhost:8000/api/claims/CLM-1001` returns the complete claim and linked Premium policy and customer history.
- [x] API tests show that policy wording, read-only instructions, handlers and company settings are available, and no response exposes manual-derived rules or evaluation data.
- [x] Parameterized payment tests allow $999.99 and exactly $1,000 without sign-off. A $1,000.01 approval enters waiting for sign-off; a direct payment request returns `409` and creates no payment.
- [x] For a synthetic $1,250 approval, sign-off by `HND-01` records the handler, amount and timestamp. A subsequent payment request for $1,250 succeeds and records one simulated payment.
- [x] Tests reject unknown or ineligible handlers, sign-off on non-approvals, negative or zero payments, invalid precision, mismatched amounts, payment before a decision, payment on a decline or referral, and duplicate payment. Rejected operations leave payment state unchanged.
- [x] Transaction tests verify that competing payment calls produce exactly one payment. A second decision cannot overwrite a recorded outcome or signed-off payout.
- [x] An OpenAPI test verifies unique operation IDs, route docstrings and required inputs for every operation. Unknown claim IDs return `404`; malformed decision payloads return `422`.
- [x] `npm run check:python` passes with at least 80% coverage.

## Phase 3: Run the UnderCover Agent and record its work

Replace the hello-world agent with LangChain `create_agent` and backend HTTP tools. Preserve the `run(claim_id, thread_id)` entry point and a test-only stub-model injection seam. Fetch instructions through the API before constructing the agent. Use the existing OpenRouter integration, `OPENROUTER_API_KEY` from `.env`, default `openai/gpt-6-luna` with low reasoning effort, and `MODEL_NAME` overrides such as `openrouter/free`.

Instructions tell the agent to read the complete claim and documents, policy, customer history, plan and policy wording; assess coverage; calculate an eligible payout using the wording's limits, offsets and deductibles; and record approve, decline or refer with a concise explanation and section references. Missing evidence or unresolved judgment produces a referral rather than invented evidence. The agent uses history as context without inventing manual-only rules.

Add the start-run API and a small background-task adapter. Record run start, tool activity, decision, blocked or successful payment and completion or failure. Pollable events are written as work happens. If a run fails before recording a decision, record the failure and return it to waiting for a manual retry. Preserve any decision or payment already committed. This pilot does not resume interrupted runs after a backend restart; document that limitation.

Success criteria:

- [x] `uv run pytest agent/tests backend/tests/test_runs.py -q` passes using scripted model replies and HTTP test clients; no test calls OpenRouter or another model provider.
- [x] A scripted approval run reads all required context through the backend API, records its outcome, calculation, two- or three-sentence explanation and policy references, and records one eligible payment.
- [x] Scripted decline and referral runs finish decided with zero payout and no payment. The referral explanation identifies the missing information or judgment required.
- [x] A scripted approval over $1,000 remains approved and waiting for sign-off. Even when the stub explicitly attempts payment, the backend returns `409`, records no payment and exposes the blocked action in the trace.
- [x] Agent tool tests cover HTTP failures and confirm that no tool imports backend storage or opens business-data files. The agent has no sign-off tool.
- [x] Model-construction tests verify the default model and low reasoning setting, `MODEL_NAME` override behavior and backend-fetched instructions without making a model call or printing secrets.
- [x] `POST /api/claims/CLM-1001/runs` returns `202` with an active thread ID. A second start while active returns `409`; a completed decision cannot be rerun. Events retain the correct claim and thread IDs and appear before the stub run completes.
- [x] A failing stub records a visible failure, leaves no active task stuck in progress and permits a new run when no decision exists. Failed work cannot erase an existing decision or payment.
- [x] `npm run check:python` passes with at least 80% coverage.

## Phase 4: Build the handler dashboard

Replace the health page with the queue, claim details and read-only agent instructions. Use typed API responses, React components where useful and Tailwind. Queue rows show claim ID, customer, type, USD amount and one of the four workflow states. A claim detail view exposes the full story, every item and document, policy and add-ons, customer history, decision, payout calculation and policy references.

Apply `docs/BRAND.md`: Manrope for body and headings, Fraunces for a short accent where appropriate, and JetBrains Mono for identifiers and numbers. Use the prescribed light and dark surfaces, rounded panels without outlines, readable status colors and plain US English. Support the browser's light/dark preference and narrow screens without adding a separate settings feature.

Success criteria:

- [x] `npm run check:ts` passes with strict TypeScript, no `any` and at least 80% Vitest coverage. Component tests cover queue rendering, detail selection, all workflow labels and read-only instructions.
- [x] Component tests cover loading, empty queue, missing claim and failed API requests, with visible recovery actions where appropriate.
- [x] Start `npm run dev`. In the Playwright MCP browser, open `http://localhost:5173`, see 25 queue rows, select `CLM-1001` and verify its story, three items, four documents, policy, plan and customer history against the permitted seed data.
- [x] In the Playwright MCP browser, open Agent instructions and verify that its text matches `GET /api/agent-instructions` and provides no editing control.
- [x] Inspect desktop and 390-pixel-wide views in light and dark modes with Playwright MCP screenshots. Text is readable, panels match the brand, all document text is accessible and controls remain usable without page-wide horizontal overflow.
- [x] Keyboard navigation reaches queue rows and actions, labels are accessible, state is conveyed by text as well as color, and USD values appear in forms such as `$1,250.00`.

## Phase 5: Connect processing, live activity and handler actions

Wire Run agent, progress polling, decision display, handler sign-off and simulated payment into the dashboard. Disable actions that conflict with the current state, and display backend rejection messages. Stop active polling after a run finishes and clean up polling when navigating away.

Use the handlers API for the required sign-off selection. After sign-off succeeds, show the separate payment action and its result. The screen must clearly distinguish approval awaiting sign-off, approval eligible for payment, and payment recorded. Visible controls help the handler; backend checks remain authoritative.

Success criteria:

- [x] Component tests with controlled API responses verify waiting -> in progress -> decided, and waiting -> in progress -> waiting for sign-off -> decided. They also verify polling cleanup and recovery from request failures.
- [x] In Playwright MCP with the scripted backend test runner, start a claim and see activity appear while processing, followed by its decision, explanation, calculation and policy sections without reloading the page.
- [x] An approval over $1,000 displays Waiting for sign-off and its approved payout, with no available payment action before sign-off. Select `HND-01`, sign off, record payment and verify handler, timestamp, amount and paid state after refresh.
- [x] A normal approval records an eligible payment; a decline or referral shows its explanation and no sign-off or payment action. If an approved run ends before payment, the handler can record that eligible payment separately.
- [x] Repeated clicks do not create duplicate runs or payments. A backend `409` is shown clearly and the UI refreshes the authoritative claim state.
- [x] The activity view shows ordered actions, tool outcomes and errors with timestamps. It shows no secrets or hidden model reasoning.
- [x] `npm run check:ts` and `npm run e2e` pass using synthetic fixtures and a stub model, with no external model calls.

## Phase 6: Verify the complete pilot and document the handover

Complete end-to-end coverage against a real local backend and temporary seeded SQLite database, using a scripted model instead of a live provider. Keep the test model and test database setup isolated from normal development; do not add a production API for choosing test decisions or resetting data. Preserve the existing quality thresholds and hook behavior.

Update the short README and `docs/STATUS.md` with setup, run commands, required environment variable names, database location, workflow and known pilot limitations. Describe approval and payment separately and make clear that all payments are simulated. Do not claim handler-level decision accuracy: measuring that requires a later, explicitly authorized evaluation.

Success criteria:

- [ ] From the repository root, `uv sync` and `npm install` succeed. `npm run dev` starts the backend and dashboard on Windows; `GET /api/health` returns `{"status":"ok"}` and the queue loads.
- [ ] Full-stack Playwright tests cover approval/payment, decline, referral, approval awaiting sign-off, handler sign-off/payment, direct API payment rejection before sign-off, duplicate actions, failed-run retry, read-only instructions and persisted state after refresh.
- [ ] Persistence tests reopen the same database and verify that decisions, sign-offs, payments and activity survive restart without reseeding over them. Automated tests use temporary storage and never change the development database.
- [ ] Repeat the core handler journey in Playwright MCP and inspect screenshots and the browser console: queue -> claim -> run -> activity -> decision -> sign-off when required -> simulated payment. There are no uncaught browser errors.
- [ ] `npm run check` exits `0`: Ruff formatting and lint, Pyright, pytest with at least 80% Python coverage, Prettier, ESLint, TypeScript, Vitest with at least 80% coverage, and Playwright all pass. Every automated test uses stubs rather than live models.
- [ ] Inspect the diff to confirm that no evaluation files, secrets, local databases, course notebooks or later-day features are included.
- [ ] README and `docs/STATUS.md` accurately describe what is built and how to run it, including the lack of sign-in and durable background execution.
- [ ] All phase criteria are checked only after verification; the final handover reports check results and any remaining limitations.

## Implementation procedure

This document is the planning deliverable. Implementation starts only when the student asks to implement the pilot.

Once authorized, build all six phases in order. For each phase, add tests, verify every success criterion, fix failures, tick the completed criteria here, update `docs/STATUS.md` and commit that phase with the pre-commit hook enabled. Use `uv run` and Windows-compatible scripts. Do not lower checks, bypass hooks, push commits or create AWS resources. Leave unrelated student changes untouched. Run `npm run check` again after the final phase and report its result.
