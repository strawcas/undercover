# Pilot implementation plan

## Goal and scope

Extend the implemented claims pilot so the UnderCover Agent automatically works waiting claims in queue order. Handlers follow progress, inspect decisions and policy references, and sign off approved payouts over $1,000. Starting each assessment must no longer require a dashboard action.

Add two terminal commands: one rebuilds the database from `data/`, returning every source claim to Waiting; the other works every waiting claim in order, prints each result, then prints totals. Implementation of this revision is in progress. Checked criteria have been verified; unchecked criteria remain to be implemented and checked.

Keep the existing FastAPI, SQLite, LangChain, React, Vite, Tailwind and TypeScript implementation. Use Python 3.12 with uv and Node 26 as specified in `docs/TECH_STACK.md`. The existing claim context, exact monetary amounts, model tools, policy assessment, payment gate and handler actions are the baseline to preserve.

The source queue is `data/claims.json`, currently containing 25 claims. Rebuilding must load every claim in that file, including later additions, with its linked policies and customers. Read only the existing seed allowlist and backend-owned agent instructions. Never import `data/evals/`, `data/security/`, the claims manual or manual-only company rules. Existing initialization continues to preserve stored work; rebuilding is a separate, explicitly invoked operation.

For this pilot, `data/` remains the intake source: changing its files takes effect on a fresh database or an explicit rebuild. Claims added to the stored waiting queue are picked up automatically. A customer submission screen, live file import, integrations, agent swarms, durable graph execution, sign-in, real payments, customer messages, evaluation scoring and cloud deployment remain outside this revision.

## Design decisions

- **One shared sequential worker.** The normal backend and the terminal processing command use the same queue selection and execution code. Process one claim at a time, finish its run, then take the next eligible claim. The worker calls the existing `run(claim_id, thread_id)` entry point; the agent continues to read and write exclusively through backend HTTP tools.
- **Stable queue order.** Persist a queue position assigned in `data/claims.json` order. Display and process claims in that order. Backfill existing databases in their current displayed order without altering decisions, sign-offs, payments or activity. Later stored arrivals append to the queue. Sorting by a claim identifier must not silently change queue order.
- **Automatic processing by default.** After initialization and interrupted-run recovery, start the worker when the backend HTTP API is ready to accept its tool calls. Continue checking for eligible waiting claims while the backend runs, including when the dashboard is closed or the queue was previously empty. Remove the per-claim Run agent control and its start-run HTTP operation.
- **One process owns a database.** The backend, rebuild command and terminal processing command share an exclusive lock for the resolved database path. Refuse a second owner before changing records or making model calls. A process exit releases ownership; an abandoned lock file alone must not block future use. Support the existing default path and `UNDERCOVER_DB_PATH` consistently.
- **Bounded failure handling.** Attempt a claim at most once per worker session. If a run fails before a decision, keep its error activity, return it to Waiting and continue with later claims. Skip that failed claim for the rest of the session to prevent an endless retry loop. A later backend restart or terminal processing invocation can retry it. Preserve any decision or payment already committed, including when a later operation fails.
- **Local restart recovery.** Stop claiming new work during shutdown and allow the active attempt a bounded time to finish. On the next start, recover abandoned active runs: retry from the beginning only when no decision exists. Never reassess a decided claim or duplicate a payment. This does not add resumable model execution or support multiple backend workers against one database.
- **Backend owns payment enforcement.** Decisions remain approve, decline or refer. Approval up to and including $1,000 permits simulated payment; approval over $1,000 waits for handler sign-off. Waiting for sign-off must not block processing of subsequent claims. A final decision is not eligible for reassessment until an explicit database rebuild.
- **Keep the model configuration.** Use `OPENROUTER_API_KEY` and the existing `MODEL_NAME` override behavior. Both execution modes use the same model factory, instructions, tools and payment rules. Automated checks use injected stub models and temporary databases, with no external model calls.

### Claim lifecycle

| Trigger | Queue state | Decision and payment |
| --- | --- | --- |
| Initialize or explicitly rebuild a source claim | Waiting | No decision or payment |
| Worker atomically claims the next eligible row | In progress | One active attempt with a new thread ID |
| Record a decline or referral | Decided | Zero payout; payment unavailable |
| Record an approval up to and including $1,000 | Decided | Approved; eligible for simulated payment |
| Record an approval over $1,000 | Waiting for sign-off | Approved; payment blocked; worker continues the queue |
| A handler signs off that payout | Decided | Approved; eligible for simulated payment |
| Pay the approved amount | Decided | Exactly one simulated payment recorded |
| Attempt fails before recording a decision | Waiting | Error visible; skipped until a later worker session |
| Rebuild the database explicitly | Waiting for every source claim | Previous decisions, sign-offs, payments, thread IDs and activity removed |

Saving a decision and executing payment remain separate operations. The agent has no sign-off tool. Handler attribution remains unauthenticated in this local pilot. Payment checks apply regardless of who calls the API.

### Planned terminal commands

These commands become available after implementation. Run them from the repository root. Stop `npm run dev` with Ctrl+C first; both commands refuse to run against a database owned by an active backend or another command.

```powershell
uv run -m backend.reset_db
uv run -m agent
```

`backend.reset_db` resolves the configured database, validates and loads the seed allowlist into a replacement database, then replaces only the selected database after validation succeeds. It prints the resolved database path and restored claim count. Every source claim is Waiting, and prior decisions, sign-offs, simulated payments, activity and attempt state are cleared. A missing database can be created. Invalid input leaves an existing database intact. The command does not start the agent; all restored claims stay waiting until a processing mode starts. Source files and `.env` are not changed.

`uv run -m agent` is a standalone terminal mode. It starts the required local HTTP backend using the configured backend URL, waits for readiness, processes the waiting claims present at the start of that invocation in persisted queue order, then shuts down its local server. It requires no dashboard or separately running `npm run dev`. It refuses to take over an occupied address or database and gives a useful terminal error. Recovered abandoned attempts without decisions are included in the starting queue; decided claims and approvals awaiting sign-off are skipped.

After each attempt finishes, print the claim ID, recorded outcome, USD payout, explanation, policy references and payment/sign-off state. If an attempt fails, print the failure and any decision already committed, then continue. Flush output as work completes. Finish with attempted and decided counts, the approve/decline/refer breakdown, failed-run count and approvals awaiting sign-off. Decided equals approved plus declined plus referred; a failed run after a saved decision can appear in both the decided and failed-run counts.

Illustrative output using synthetic claims:

```text
TEST-001 APPROVE $100.00 - Simulated payment recorded
Explanation: The documented delay and expense meet the policy conditions. Section 3 covers the hotel cost.
Policy sections: 3, 10
TEST-002 DECLINE $0.00 - No payment
Explanation: The documented loss falls under the stated exclusion. Section 4 excludes this expense.
Policy sections: 4
Attempted: 2 | Decided: 2 | Approved: 1 | Declined: 1 | Referred: 0 | Failed runs: 0 | Awaiting sign-off: 0
```

An empty queue prints zero totals and exits successfully without calling a model. Return exit code 0 when every attempted run completes with a decision, 1 for a setup error or any failed run, and 130 for Ctrl+C. Interruption stops new claims, prints available results and partial totals, and leaves incomplete work recoverable. Neither command performs real payments or grants handler sign-off.

### Planned API surface

Keep existing read, decision, activity, handler sign-off and simulated payment APIs, with their unique operation IDs, one-line docstrings and required inputs. `GET /api/claims` returns persisted queue order. The worker reserves claims through backend-owned transactional code; its agent still accesses context and records outcomes through HTTP tools.

Remove `POST /api/claims/{claim_id}/runs` and the dashboard dependency on it. Queue processing starts with the backend or terminal mode. Rebuilding remains a terminal-only operation with no reset HTTP endpoint. No batch-control API is needed for the standalone command.

## Phase 1: Preserve queue order and coordinate database ownership

Extend the existing storage rather than reseeding an established database. Add persisted queue positions and an atomic operation that reserves the next waiting, undecided, inactive claim. Introduce shared process ownership before startup recovery, execution or rebuilding can modify a database.

This establishes one ordering and one owner for both execution modes, so automatic and terminal processing cannot compete or repeat a recorded decision.

Success criteria:

- [x] `uv run pytest backend/tests/test_seed.py backend/tests/test_queue.py -q` passes using temporary databases and no model calls.
- [x] Fresh initialization assigns positions in source-file order. Synthetic IDs whose lexical order differs from source order prove that API display and worker selection follow the same persisted positions.
- [x] Existing databases migrate in current displayed order without changing saved decisions, exact monetary amounts, sign-offs, payments or events. Repeated initialization preserves both records and positions.
- [x] Concurrent reservation attempts cannot claim the same row, create more than one active assessment, select an already decided claim or skip queue order. Decided and awaiting-sign-off rows do not block later waiting claims.
- [x] A new waiting row appended after the queue has emptied is selectable next. A worker session can exclude its failed attempts without excluding later arrivals.
- [x] Temporary-path ownership tests reject a second backend, command or reset against the same database, permit separate databases, and permit a new owner after a prior process exits unexpectedly.
- [x] `npm run check:python` passes with at least 80% coverage and no relaxed checks.

## Phase 2: Start and drain the queue automatically

Replace per-claim HTTP starts with a backend-managed sequential worker. Retain LangChain `create_agent`, existing HTTP tools and `run(claim_id, thread_id)`. Start model work only after the backend can serve HTTP; polling and shutdown must leave the API responsive.

Reuse the existing activity and finish behavior, adding the session-level failure exclusions and restart rules above. Inject runner and lifecycle controls for deterministic tests so API unit tests do not accidentally start live models. Use one backend process for this pilot; multiple workers and distributed dispatch remain outside scope.

Success criteria:

- [x] `uv run pytest agent/tests backend/tests/test_runs.py backend/tests/test_queue.py -q` passes with scripted models and HTTP clients only.
- [x] A backend started with a temporary queue begins work without a browser, button press or start-run API call. At most one claim is active, and attempts start in persisted queue order.
- [x] The worker idles without repeated model calls after draining the queue and picks up a newly stored waiting claim within the documented polling interval.
- [x] A scripted approval, decline and referral read required context through HTTP and record the expected outcomes, explanations and references. Eligible payment is recorded exactly once.
- [x] A scripted approval over $1,000 remains awaiting sign-off, blocks attempted payment and allows the next waiting claim to run.
- [x] A failed attempt records its error, returns an undecided claim to Waiting, and allows later claims to finish. It is not retried in the same session. A later session retries it once; an error after a committed decision or payment preserves that record.
- [x] Startup readiness and shutdown tests prevent model calls before the API is reachable and new claims after shutdown starts. Restart recovers abandoned attempts without duplicating decisions or payments.
- [x] The removed per-claim start route is absent from OpenAPI; remaining operation IDs, required inputs, thread validation and payment checks still pass their API tests.
- [x] `npm run check:python` passes with at least 80% coverage.

## Phase 3: Rebuild the database from the terminal

Add `backend.reset_db` using the same configuration, source validation and explicit seed allowlist as normal initialization. Build and validate a replacement beside the resolved target, close database handles and replace the target only after success. Hold database ownership throughout the operation and clean up only temporary files created by that invocation.

This gives the student a repeatable fresh queue while preserving ordinary startup's behavior of keeping existing work.

Success criteria:

- [x] `uv run pytest backend/tests/test_reset_db.py -q` covers the command in subprocesses with temporary database paths.
- [x] After synthetic decisions, sign-offs, payments, failures and activity are recorded, `uv run -m backend.reset_db` restores every permitted source claim to Waiting in source order. No old decision, sign-off, payment, active run, thread ID or event remains.
- [x] The command reports the resolved path and source claim count, honors `.env` and `UNDERCOVER_DB_PATH`, creates a missing target and succeeds repeatedly on Windows.
- [x] A synthetic addition to a copied `claims.json`, with valid linked records, appears in the rebuilt queue. Tests derive counts from the permitted source file rather than assuming 25 forever.
- [x] Missing or invalid seed data fails without replacing an existing database. A live owner or competing reset causes a clear nonzero exit without changes.
- [x] File-access tests enforce the seed allowlist without opening evaluation or security files. Only the selected database and owned temporary files can be replaced; business files, secrets and other databases remain intact.
- [x] Rebuilding makes no model calls and starts no queue worker. The restored queue stays waiting until the backend or terminal processing command starts.
- [x] `npm run check:python` passes with at least 80% coverage.

## Phase 4: Work the entire waiting queue from the terminal

Add a thin `agent/__main__.py` entry point so `uv run -m agent` provides a finite execution mode around the same local HTTP backend, queue worker and configured model. Keep the shared queue worker in the backend and the existing `run(claim_id, thread_id)` interface in `agent/main.py`. Share readiness, ownership, interruption and finish behavior with normal automatic processing. Provide a test-only runner injection seam; production commands must not offer a stub-model selector.

The terminal mode uses the waiting queue at invocation as its workload, so it prints a final count and exits. The normal backend continues watching for later arrivals.

Success criteria:

- [ ] `uv run pytest agent/tests/test_cli.py -q` passes using subprocesses or an injected terminal entry point, temporary databases and stub models; no test contacts OpenRouter.
- [ ] Starting only `uv run -m agent` processes all initially waiting claims in persisted order, one at a time, with no dashboard or separately running backend. Its tools reach the local HTTP APIs, and entry-point tests verify that the package invokes the shared queue worker.
- [ ] Captured output prints each decision once, in queue order, including payout, explanation, policy sections and payment/sign-off state. The final totals match persisted records, with correct counts for approvals, declines, referrals, failures and sign-off requirements.
- [ ] An empty queue exits 0 with zero totals and no model calls. A second invocation after a successful drain leaves decisions and payments unchanged and prints zero attempted claims.
- [ ] A failed claim is attempted once, printed as failed and left recoverable while later claims are processed. Exit code 1 and summary counts accurately cover errors both before and after a decision is saved.
- [ ] Missing configuration, an occupied local address or a database already owned by the backend fails clearly before starting an assessment. The command respects the configured database and local backend URL.
- [ ] Ctrl+C stops new attempts, prints partial totals, releases process ownership and shuts down the local server. A later invocation recovers abandoned undecided work without repeating committed decisions or payments.
- [ ] `npm run check:python` passes with at least 80% coverage.

## Phase 5: Show automatic progress in the dashboard

Replace the Run agent panel with plain waiting/processing information. Keep decisions, activity, handler sign-off and simulated payment controls. Poll the queue while the claims page is open, including when no claim is currently active, so new work and the gap between sequential runs cannot make the screen stale. A selected waiting claim must also refresh into processing and completion automatically.

Preserve `docs/BRAND.md`, accessible keyboard operation, narrow layouts and light/dark support. Clean up polling on navigation and unmount; recover visibly from request failures. The backend owns processing throughout.

Success criteria:

- [ ] `npm run check:ts` passes with strict TypeScript, no `any` and at least 80% Vitest coverage.
- [ ] Component tests show waiting -> in progress -> decided and waiting -> in progress -> waiting for sign-off without a start button or start-run request. Another claim beginning after the queue was idle appears automatically.
- [ ] Selected waiting claims refresh without user actions. Queue ordering, counts, new arrivals, errors, final decisions and payment state remain accurate across automatic transitions.
- [ ] Polling tests cover idle gaps, request failure recovery, navigation and unmount without duplicate polling or stale updates.
- [ ] Handler sign-off and separate payment remain usable for an approval over $1,000 while the worker assesses subsequent claims. Rejected and duplicate actions show the backend response.
- [ ] In Playwright MCP with the scripted backend, open the dashboard, follow automatic activity and decisions, and verify instructions remain read only. Refreshing preserves committed records.
- [ ] Inspect desktop and 390-pixel-wide layouts in light and dark modes; automatic status text, activity and handler actions remain readable and keyboard accessible without horizontal overflow or uncaught browser errors.

## Phase 6: Verify both execution modes and document the handover

Extend full-stack and terminal integration coverage to the revised workflow. Use separate temporary databases and stub runners for every automated check. Keep the existing payment, exact-money, persistence and data-access regression coverage. The planning revision itself neither resets the student's database nor starts any model runs.

Update the short README, `.env.example` if settings change, and `docs/STATUS.md` during implementation. Document automatic processing, one backend process, queue order, how to stop the app before terminal commands, rebuild's deletion of previous work, output/exit codes, failure retries and restart limits. Keep implementation details out of ordinary dashboard guidance.

Success criteria:

- [ ] `uv sync`, `npm install` and `npm run dev` work on Windows. Health returns `{"status":"ok"}`, and a stub-backed isolated backend starts processing without a browser.
- [ ] Full-stack tests cover ordered automatic processing, idle-to-active pickup, approval/payment, decline, referral, sign-off while later claims run, the exact $1,000 boundary, direct blocked/duplicate payment calls, failed attempts and restart recovery.
- [ ] A terminal integration test rebuilds a temporary database, drains its queue with stubs, verifies every printed decision and summary count against stored results, then proves a repeat drain processes zero claims.
- [ ] Rebuilding that temporary database again restores all permitted claims to Waiting and clears prior results. Rebuilding while its backend runs is rejected without modifying the database.
- [ ] Repeat the automatic handler journey in Playwright MCP: queue -> activity -> decision -> sign-off when required -> simulated payment, with no Run agent action and no unexpected console errors.
- [ ] `npm run check` exits 0: Ruff, Pyright, pytest, Prettier, ESLint, TypeScript, Vitest and Playwright all pass with the existing coverage thresholds and hooks enabled. All automated models are stubs.
- [ ] Inspect the diff for unrelated changes, secrets, databases, course notebooks, evaluation data and later-day features. No student configuration or working database is reset as part of the build.
- [ ] README and `docs/STATUS.md` describe the implemented commands and their limits accurately. Mark criteria complete only after verification; report check results and remaining limitations.

## Implementation procedure

Implementation of this revised plan is authorized. Preserve the completed pilot and work through the six extension phases in order. For each phase, add tests, verify its criteria, update this plan and `docs/STATUS.md`, and commit with the pre-commit hook enabled.

Use uv and Windows-compatible commands. Preserve unrelated student changes. Never lower checks, bypass hooks, push commits, create AWS resources or read the evaluation answer key. Run `npm run check` after the final phase and report the result. Live model accuracy remains unmeasured unless a separate evaluation is explicitly requested.
