# Durable state implementation plan

## Goal and scope

Implement `docs/DURABLE.md` on top of the current harness. Move business records to Supabase when configured, add the payout calculator and Langfuse tracing, and replace the claim helper with a durable LangGraph workflow. A held approval waits for a handler and then resumes to pay; an interrupted assessment resumes its saved work without using another attempt. The queue agent remains on `create_agent`.

**Course sidenote:** For consistency with the course, read `labs/week2day3.ipynb` and build the solution in the same style as the lab. Where the lab and this brief differ, follow the brief. The notebook has been read for this plan, together with the payout and tracing examples in `labs/week2day1.ipynb` and `labs/week2day1_payout.py`. Read notebook source only; do not execute evaluation, cleanup or model-call cells.

Implementation is in progress. Check criteria only after verification, retaining explicit pending live checks. Leave `docs/PLAN.md` and `docs/PLAN_HARNESS.md` as historical plans. Preserve the student's existing notebook changes and `docs/DURABLE.md`.

## Starting point and approach

The current backend stores claims, source records, events, runs, attempts, to-dos, section indexes and evidence in SQLite. Queries and transaction handling use SQLite directly, including `BEGIN IMMEDIATE`, `PRAGMA`, `INSERT OR REPLACE` and `rowid` ordering. The claim helper uses `create_agent` with decision/payment tools and a forty-call limit. Recovery closes active runs and counts unfinished assessments as failures. A saved decision or payment always conflicts with a repeated request. Sign-off currently saves attribution and makes payment available; the dashboard offers a separate payment button.

Those behaviors must change together. A Postgres checkpointer alone would not help while backend recovery invalidates its attempt, and replaying a graph node would still fail after a committed decision. Keep the existing financial gate, evidence validation, finite queue scopes, two-failure handoff, safe diagnostics and observational logging while replacing the relevant lifecycle rules.

Read-only Supabase inspection during planning returned no tables in the `undercover` schema. Inspect it again before implementation writes. The product must not change the lab's `lab` schema, `public`, or unrelated project objects.

### Storage and process boundaries

- Use SQLAlchemy Core for all backend business queries, with shared table definitions and validated domain records. Replace SQLite-specific SQL throughout storage, claims, runs, references and their callers rather than maintaining two query implementations. Keep cents/Decimal arithmetic and stable explicit ordering.
- Select Supabase only from an actual `DATABASE_URL` environment variable. The development launchers and terminal entry points load `.env` explicitly without overriding terminal settings; importing product modules or creating test apps must not load it. Without `DATABASE_URL`, retain `UNDERCOVER_DB_PATH` and SQLite. A configured but unreachable Postgres database fails visibly; it never silently falls back to SQLite.
- Use the session pooler, synchronous psycopg and `search_path=undercover` for backend and checkpointer connections. Create the product schema before table/checkpointer setup. Use synchronous `PostgresSaver` with `durability="sync"`; use a synchronous file-backed SQLite saver for local mode and restart tests. Add the required graph, SQLAlchemy, pool and SQLite-checkpointer dependencies explicitly to the appropriate root dependency groups.
- The backend owns business data and transactions. The agent accesses claims, rules, evidence and financial operations through HTTP; its direct database access is limited to LangGraph checkpoints. The dashboard continues to call only the backend.
- Run one agent service process with one sequential execution worker. Both CLI queue work and sign-off resumptions use it; the CLI must not become another independent graph owner. Use bounded pools: backend at most four connections, agent checkpointer at most four, and any separate setup/reset process at most four, including overflow and dedicated locks. The CLI and dashboard need no database pools. Close connections on shutdown and reload; never hold a business transaction over HTTP/model calls or a human pause.
- Replace local-file ownership assumptions for Postgres with database-coordinated startup/reset exclusion and atomic execution ownership. Persist a renewable execution lease/generation on the attempt; reclaim only an expired or explicitly released owner, and fence writes from the previous generation even when the resumed thread ID is unchanged. Keep local file protection for SQLite reset where applicable. This supports safe service restart and duplicate notifications, without introducing a dispatcher or agent swarm.

Normal initialization preserves existing saved records. Existing SQLite databases migrate additively and preserve exact money, order, evidence and outcomes. A new Supabase database is seeded from the permitted source allowlist; automatic transfer of historical local work is outside this brief. Switching database targets must not overwrite the old SQLite file. Database identity checks must identify the configured target without exposing credentials and work across the backend, service and CLI.

### Claim graph and recovery

Follow the lab's visible state/nodes/edges structure:

```text
START -> agent -> tools -> agent
           |
           +-> decide -> END (decline or refer)
                 |
                 +-> approval -> pay -> END
                 +-------------> pay -> END
```

The `agent` node reasons with claim/reference/payout tools. When it stops asking for tools, `decide` obtains and validates the structured outcome, explanation, calculation and evidence references and saves the decision through the backend. A correctable validation error or backend refusal becomes a tool/model message and routes back to reasoning. Unexpected infrastructure failures follow the attempt-failure policy. Every approval goes through `pay`; the backend's returned status determines whether `approval` is needed. The graph never compares a payout with a hard-coded approval limit.

The graph state carries typed messages, claim/run/attempt/thread identifiers, the validated decision, effective instructions and persistent model/tool usage counters. Keep runtime HTTP clients, checkpointers, cancellation signals and clocks outside serialized state and pass them explicitly. The nodes replace the claim middleware's responsibilities: activity, refusal messages, provider retries, context bounds and limits. Queue middleware remains for queue framework concerns. `run(claim_id, thread_id)` stays available as the shared claim workflow entry point.

Use no more than thirty model requests per assessment, including structured-decision calls and provider retries, across all resumptions. Reserve usage durably before issuing requests so a crash cannot restore an exhausted budget. Retain bounded HTTP/tool calls and provider timeouts/retries. Human waiting and process downtime do not consume an assessment deadline; persist remaining active-work allowance and issue a fresh execution lease when resuming, without resetting call counts. True active-work timeout is an error, not an interruption.

| Event | Saved state and next action |
| --- | --- |
| Explicit CLI command admits a new claim | In progress; new attempt/thread and clean graph state |
| Graph approves at or below the backend limit | Save decision, run payment node, then finish the attempt |
| Backend says approval needs sign-off | Save an `interrupt()` checkpoint; Waiting for sign-off; release execution ownership and continue other queue claims |
| Handler signs off | Commit handler, amount and timestamp; In progress until payment, even if the service is unavailable |
| Agent receives a saved sign-off notification | Re-read backend state, reacquire the same attempt/thread, resume with `Command(resume=...)`, then pay |
| Crash, shutdown or Ctrl+C | Suspend execution; preserve checkpoints, attempt number, failure count and unfinished queue scope |
| Next explicit run after interruption | Resume saved steps with the same thread/attempt before admitting fresh claims |
| An undecided assessment fails with an error | Finalize failed attempt; retry with a new thread and clean state, up to two failures total |
| Error after a committed decision/payment | Preserve it; reconcile unfinished payment/finalization without reassessing the claim |
| Payment completes or decision is decline/refer | Complete the attempt once; Decided |

`interrupt()` must propagate as LangGraph control flow, never become an assessment failure. Its node can execute again on resume, so keep payment in its own node and verify saved sign-off rather than trusting notification data. Handle a sign-off arriving before the interrupt checkpoint finishes: the service must reconcile the saved backend flag after checkpointing and avoid losing that wake-up.

Separate queue completion from a paused claim's lifetime. A queue may finish with approvals awaiting sign-off; those claim attempts remain resumable after their parent queue has finished. Resume/payment authorization must therefore not require an old queue to remain `running`. Frozen completed-run summaries stay snapshots; current claim data shows subsequent sign-off and payment.

When a queue is stopped, the next CLI invocation finishes its saved snapshot, admitted IDs, to-dos and remaining cap before creating new work. Reconstruct the queue agent from those backend records; its model conversation need not be checkpointed. Treat that invocation as continuation of the old scope, report it clearly, and do not automatically open a second fresh snapshot afterward. Reject incompatible explicit named/cap arguments with a useful explanation. Signed-off or interrupted claim continuations use their existing admission slot. Unsigned held claims remain paused and do not block the remaining snapshot. Starting the app/service alone still admits no fresh waiting claims.

### Financial replay, sign-off and reseeding

Matching decision/payment replays return the stored claim with success and no new financial or lifecycle event. Compare all validated business fields, including exact amount, explanation/calculation and evidence references; generated timestamps are not request fields. Reject different decisions or amounts. A replay must belong to the recorded claim/attempt; it is not permission for a superseded execution to make a new write. Recognize completed matching writes before applying active-attempt admission checks. Update endpoint response models, financial reconciliation, graph tools and dashboard callers together to consume the claim response. Reconcile stored state after an ambiguous HTTP response, retain generation fencing, and enforce atomic conditional writes/uniqueness under concurrent requests.

Sign-off remains a separate operation and never pays. Commit attribution and In progress state before scheduling a bounded HTTP notification to `UNDERCOVER_AGENT`, default `http://127.0.0.1:8002`. Return without waiting for graph execution or payment. Notification failure cannot roll back sign-off; the saved signed-off/unpaid state is enough for the next explicit run to recover it. Duplicate notifications coalesce into the same claim continuation. Include the saved sign-off handler and time in the payment record, copied by the backend rather than accepted from the model. A payout over the backend limit without sign-off remains blocked on every payment path.

Keep `uv run -m backend.reset_db` as the explicit reseed operation. Validate the full seed input before changing records. In Postgres, reset only owned product tables/checkpoints with the app and agent stopped, under exclusive maintenance ownership; repopulate business records transactionally and clear the matching checkpoints. Preserve checkpointer schema/migration metadata as needed. Retain safe replacement for SQLite, clear its associated checkpoints too, and use a new dataset generation so an old process or thread cannot act on newly seeded claims. Never drop another schema or log `DATABASE_URL`.

## Phase 1: Bring in payout rules and claim tracing

Start with the experiment's two improvements before the database or graph refactor. Move the payout logic into backend-owned modules using stored policy, plan, add-on and company settings, with exact cents/Decimal arithmetic. Add a required-input payout operation and claim tool; model-selected covered lines remain the model's responsibility. Use the same calculator after the graph migration.

Add Langfuse's LangChain callback integration around each claim execution. Use the stable session ID `claim_id:thread_id` and claim/run/attempt metadata, retaining the session across pauses and process restarts. Missing keys disable tracing locally; configured live runs trace all claim executions. Trace export and flush are observational and cannot fail a successful business operation. Automated tests inject a local observer and block network exports.

Success criteria:

- [x] Payout unit/API tests cover every benefit maximum, trip-cost caps, item quantities/pairs/sets, electronics add-on, aggregate $100 cash cap, hotel rooms/nights, meals per traveler/day, third-party payments before deductibles, zero floor and cent rounding, matching `labs/week2day1_payout.py` semantics without importing it at runtime.
- [x] `POST /api/claims/{claim_id}/payout-calculation` has a unique `operation_id`, one-line docstring and fully required typed inputs, including explicit zero/default values for otherwise unused fields. It returns calculation steps, exact payout and `needs_sign_off`, taking the approval limit from stored company settings. Invalid benefit/amount/quantity/duration inputs fail validation.
- [x] Synthetic settings tests prove $1,000 does not need sign-off and $1,000.01 does, and that changing the stored limit changes calculator results and payment enforcement consistently. Calculating never records a decision or payment.
- [x] Stub-agent tests verify the tool uses backend context through HTTP and the canonical instructions require the calculated payout for approvals. No runtime import or read reaches notebooks, evaluation data or hidden answer keys.
- [x] Observer tests group two invocations of the same claim/thread in one session and separate different threads; callback, export and flush failures preserve business success and emit safe diagnostics.
- [x] `npm run check:python` passes with existing coverage thresholds; review changed workflows and callers and record results in `docs/STATUS.md` before committing with hooks enabled.

## Phase 2: Move backend persistence to SQLAlchemy and Supabase

Convert the entire backend query surface and migration/rebuild paths together, preserving existing contracts wherever the brief does not change them. Add explicit ordering fields for runs, attempts and evidence where code currently relies on SQLite `rowid`. Introduce database configuration and ownership shared by commands and the service. This phase prepares durable lifecycle fields without starting graph work.

Success criteria:

- [x] Existing seed, queue, references, runs, API, ownership, financial and reset regressions pass on temporary SQLite databases using SQLAlchemy Core. No duplicate SQLite/Postgres business-query implementations remain.
- [x] Migration fixtures containing old decisions, sign-offs, payments, failures, to-dos and evidence retain their exact values and ordering over repeated initialization. No invented reference provenance or silent reseed occurs.
- [x] Configuration tests prove injected SQLite storage overrides ambient settings, imports do not read `.env`, configured Postgres never falls back silently, and backend/service identities match the actual target without exposing URLs/passwords.
- [x] Pool tests assert at most four connections per database-using process and no unbounded overflow; disposal releases them. Concurrent admission/financial/reset tests retain atomic behavior and stale-owner rejection. Review the corresponding Postgres locking strategy explicitly; SQLite tests alone do not prove it.
- [ ] On Supabase, read-only MCP inspection verifies business tables are confined to `undercover`; initialization and reseeding restore the permitted queue in source order. Repeated startup preserves stored records. Inspect schema, transaction and uniqueness behavior against the real database without model calls.
- [x] Reset tests validate before destructive changes, preserve the existing database on invalid seed data, reject active owners, and clear business state plus applicable checkpoints without affecting `lab`, other schemas, source files or another configured database.
- [x] `npm run check:python` passes. Review complete changed transactions and callers, including rollback/cleanup failures, update status and commit the phase.

## Phase 3: Build the synchronous checkpointed claim graph

Replace the claim `create_agent` call with named `StateGraph` nodes and conditional edges. Make decision persistence and payment explicit workflow steps. Remove model-facing `pay_claim` and move decision recording into `decide`; update claim instructions and visible read-only prompts accordingly. Retain claim/reference/payout tools, exact evidence storage, refusal feedback and current provider support/defaults.

Success criteria:

- [x] Graph tests with a stub model cover approval below/equal/above the backend limit, decline, referral, a corrected refused decision/tool call, and missing evidence. All approvals reach `pay`; decline/referral never do. The model has no payment or sign-off tool.
- [x] An over-limit approval saves one decision and checkpointed interrupt, makes zero payments and releases execution capacity so another claim can proceed. The graph branches on backend state, not a copied limit.
- [x] After backend sign-off is saved, `Command(resume=...)` on the same thread pays once without another assessment/model decision, and payment includes handler identity and sign-off time. A forged/early resume cannot authorize payment.
- [x] File-backed SQLite checkpoint tests close/recreate the graph and saver between invocations and resume completed steps. Adapter tests verify synchronous `PostgresSaver` setup/settings and `durability="sync"` on initial, crash-resume and interrupt-resume invocation paths.
- [x] Persisted usage tests bound the assessment to thirty model requests across retries/resumes, count structured-decision calls, retain tool/history limits and provider timeouts, and ensure paused time does not exhaust active-work time. Exhausted budgets produce a real assessment failure.
- [x] Every graph step/refusal/retry is visible through claim activity. Observational event/tracing failures cannot stop committed decisions or payments; mandatory evidence and lifecycle failures remain visible. LangGraph interrupts are never caught as ordinary errors.
- [x] `npm run check:python` passes. Review nodes, routing, serialized state, tool adapters and all callers; update status and commit.

## Phase 4: Resume attempts and make financial steps replay-safe

Change attempt/run finalization and startup/shutdown recovery to distinguish suspended work, human waiting, failed assessment and completion. Add execution ownership and resumable discovery APIs with the existing API conventions. Keep backend state authoritative, including the case where a financial operation committed but its graph checkpoint did not.

Success criteria:

- [x] Stop a stub-backed subprocess after an early persisted graph step, recreate the process and continue with the same attempt number, thread, evidence and call counters. Saved steps are not repeated; an unfinished node may replay. Failure counts stay unchanged.
- [x] Repeat crash injection after decision/payment commit but before node checkpoint, and after graph completion but before attempt finalization. Each case converges to exactly one decision/payment and one finalization without a new assessment.
- [x] Matching decision/payment repeats return HTTP success and the stored claim, preserving original timestamps, attribution and mandatory events. A different decision, calculation/reference set or payment amount returns 409. Concurrent duplicates produce one stored result.
- [x] Backend restart and service restart preserve suspended/held attempts; resume renews execution ownership without changing assessment identity. Old-generation writes remain fenced, even with the same thread ID. Missing initial checkpoints start only the still-reserved attempt, not another one.
- [x] Pre-upgrade records without checkpoints remain readable. Preserve historical outcomes and attribution; initialize payment-only continuation for an existing held/unpaid approval without a new assessment. Never claim to restore an older conversation that was not saved.
- [x] A genuine undecided error retries from clean state with a new thread and increments failures once. Two such errors produce Handler review. Ctrl+C, kill, a human pause, lost response and observational failure do not consume that allowance. Previously committed outcomes survive all failure paths.
- [x] A stopped queue continues its stored scope/cap/to-dos, resumes existing claims first and completes its missed-work check before finishing. Continuation does not admit later arrivals, increase the distinct-claim cap or start a second snapshot. Completed queues can still have their held claims resumed separately.
- [x] Combined primary/cleanup failures, cleanup-only failures, interruption during cleanup and post-commit errors preserve stored state and intended exit codes. Diagnostic tests retain useful references/code locations and exclude credential/provider/customer sentinels from public errors and logs.
- [x] `npm run check:python` passes. Trace admission through recovery, decision/payment and attempt/run finalization, retain existing regressions individually, record review results and commit.

## Phase 5: Connect sign-off to the agent service and dashboard

Add the small agent web service as the fourth agent component, with typed health, queue-start and claim-resume operations. Use a sequential worker for synchronous graph/queue execution so HTTP acknowledgments and the backend remain responsive. `npm run dev` starts backend, agent service and dashboard using Windows-compatible scripts. `uv run -m agent` remains the queue command, attaching to the matching service or starting temporary local services when the app is absent; share workflow code rather than duplicating it.

Save sign-off before notifying the service. The worker validates/re-reads stored state, coalesces duplicate requests and gives signed-off continuations priority at the next safe claim boundary. Do not impose a five-second live acceptance guarantee on a worker already executing another synchronous claim; perform that timed sign-off check when the capped queue has returned and its worker is free.

Update only the dashboard behavior this needs: Awaiting sign-off -> In progress -> Paid, polling throughout pending payment, and saved handler/time beside the payment. Retain manual simulated-payment controls only for claims without an agent-owned pending payment; they must not race or replace the graph's normal payment step.

Success criteria:

- [x] Service API tests verify fast acknowledgment, required typed inputs, unique operation IDs/docstrings, health/database identity matching, one active execution owner, duplicate resume coalescing, and clear failures for an unrelated backend/service or incompatible scope.
- [x] Sign-off tests prove the transaction committed before the HTTP notification can run, response does not wait for payment, and unreachable/slow service leaves attribution and In progress state saved. A later explicit command discovers and pays the claim once.
- [x] Race tests cover sign-off before the interrupt checkpoint, repeated notifications, notification concurrent with CLI recovery and service failure after acknowledgment. Stored flags and checkpoints retain recoverability; no duplicate assessment/payment occurs.
- [x] Starting/reloading the three services, refreshing the dashboard and polling alone admit no fresh claims. A new CLI command resumes stopped/signed-off work first; full, capped, alias and named modes retain truthful output, cap enforcement and interruption exit behavior.
- [x] Component tests verify continued polling while signed-off/unpaid, paid handler/time rendering, no premature Decided/payment action, notification-failure recovery text, and cleanup on navigation/unmount. `npm run check:ts` passes without `any` or lowered thresholds.
- [x] Playwright E2E starts a stand-in agent service on port 8003 and points only its isolated test backend at it. A dashboard sign-off sends the saved claim to that stand-in once, preserves handler/time and updates pending-payment UI; it never contacts port 8002, Supabase, Langfuse or a model. Python tests prove the actual resume/payment behavior.
- [x] In Playwright MCP, inspect the sign-off journey, refreshed records, read-only instructions and activity at desktop and 390-pixel widths in light/dark modes. Confirm keyboard access, readable USD amounts and no uncaught browser errors.
- [ ] `npm run check` passes with stub models/temporary databases. Review backend notification, service admission/worker/shutdown, CLI ownership/reporting and dashboard callers; update status and commit.

## Phase 6: Verify offline operation, live durability and handover

Run the full offline suite independently of developer secrets, then the brief's limited live acceptance. Update `docs/SPEC.md` queue/harness description while leaving its Rules unchanged; update `docs/TECH_STACK.md`, `docs/PROCESS.md`, `docs/STATUS.md`, the short README and `.env.example` for actual startup, environment selection, graph/service behavior, recovery, tracing, connection budgets and reseeding. Update `docs/CODE_STANDARDS.md` only as needed to reconcile claim graph nodes and the fourth service component with the existing orchestration rules.

Every real-model trial starts from a reseed of the selected product database and uses `uv run -m agent --cap 5` on the default model. Keep crash recovery within that same capped run; do not reseed between stopping it and resuming it, because that would erase the checkpoint being tested. If a second independent trial is needed, reseed again first. Tests and planning never reset the student's working database.

Success criteria:

- [ ] `npm run check` exits 0 with `.env` temporarily absent and inherited external-service variables removed, with guaranteed restoration even if checks fail. Ruff, Pyright, pytest, Prettier, ESLint, strict TypeScript, Vitest and Playwright pass at existing thresholds with no external network/model access. Record exact totals/coverage.
- [ ] Offline suites explicitly cover payout calculation, interrupt-before-payment, sign-off/resume/pay-once, crash recovery with the same attempt/thread, error retry with a fresh thread, repeated financial requests, completed-parent-run continuation and the port-8003 E2E notification path.
- [ ] On Windows, `uv sync`, `npm install` and `npm run dev` start all three services correctly, with backend health still returning `{"status":"ok"}`. Test both Supabase configuration and SQLite local mode, and ensure stopped/reloaded processes release locks, workers, connections and trace resources.
- [ ] After Supabase reseed, a default-model capped run works five distinct claims, retains evidence/activity/to-dos, leaves the rest Waiting and pauses an over-limit approval. If model choices yield no held approval, record that limitation and use a separate freshly seeded capped trial rather than changing business rules or exceeding the cap.
- [ ] With the agent worker free, use Playwright MCP to sign off the held claim. It becomes In progress then Paid within five seconds; backend/Supabase records show one payment, the approving handler and sign-off timestamp, and unchanged attempt/thread.
- [ ] Kill the live agent after an early checkpoint within the capped trial, restart the service and invoke the CLI continuation. It resumes the same thread and attempt, finishes the interrupted queue within its original cap, and leaves one decision/payment per claim. Capture checkpoint/attempt evidence before and after.
- [ ] During live execution, use the read-only Supabase MCP server to sample `SELECT count(*) FROM pg_stat_activity WHERE usename = 'postgres'`; record no more than twelve connections. Check application pool configuration as well as observations, and close notebook/lab connections before measuring.
- [ ] Through Langfuse's authenticated API, fetch the held claim's `claim_id:thread_id` session and verify traces before and after sign-off. Record session/trace identifiers without credentials or customer payloads. Trace retrieval/ingestion failures remain an explicit unverified acceptance item, not a claimed pass.
- [ ] Review the final diff and complete changed workflows/callers under `docs/PROCESS.md`. Record actual results, live evidence and limitations in status; check off only verified criteria, commit with hooks enabled and report the handover. Do not push, change course notebooks, open evaluation data, build later-day features or create/delete AWS resources.

## Planning baseline checks

On October 30, 2026, `npm run check` passed Python formatting/lint/type checks, all 224 Python tests (97.53% coverage), TypeScript formatting/lint/type checks and all 21 component tests (99.33% statement / 92.72% branch coverage). Playwright passed seven of eight tests, so the full command exited 1.

The failing existing test is `e2e/claims.spec.ts:96`, "retries a failed claim while other claims continue". Its assertion at line 115 did not find "Simulated payment recorded" within 35 seconds. Investigate the backend result, activity delivery and dashboard polling before adapting this regression during implementation; the observation does not establish the cause. No product code or test was changed for this planning task. These baseline results do not verify any durable-state success criterion.

## Technical references

The local brief and labs are the requirements. These official references support implementation details; recheck them against installed package versions during the relevant phase:

- [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts): stable threads, saved pauses, `Command(resume=...)` and replay of interrupted nodes.
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence) and [Postgres checkpointer setup](https://docs.langchain.com/oss/python/langgraph/add-memory): checkpoint-backed state and synchronous saver setup.
- [SQLAlchemy connection pooling](https://docs.sqlalchemy.org/en/20/core/pooling.html): bounded pool sizes and overflow.
- [Langfuse sessions](https://langfuse.com/docs/observability/features/sessions): stable session IDs grouping traces across invocations.

Implement phases in order only after the student requests implementation. For each phase, build with stub-backed tests, verify its criteria, update this checklist and status, review complete changed workflows and callers, and commit with hooks enabled. Preserve all unrelated student work.
