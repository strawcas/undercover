# Plan: a swarm of claim agents and a scoreboard

Build `docs/SWARM.md` on the existing durable claim agent in six phases. Start with the default model and measurements, replace the queue agent, add dashboard starts, then add evaluations and the scoreboard. Implementation is in progress; completed criteria are checked below.

**Course sidenote from the brief:** For consistency with the course, read `labs/week2day4.ipynb` and build the dispatcher in the same style as the lab. Where the lab and this brief differ, follow the brief. The notebook has been read for this plan. Use its `StateGraph`, listing node, conditional fan-out with `Send`, results reducer and `max_concurrency`; retain the product's separate claim threads and the brief's narrower retry policy. Do not use the lab's harder dataset, shared swarm thread or optional dispatcher checkpointer.

## Design

### Modules and dependencies

The current implementation has a synchronous claim graph, one service-wide saver, a model-driven queue agent, run caps and a to-do table. Extend that implementation rather than replacing the claim graph or borrowing code from a later course build.

| Module | Responsibility and dependencies |
| --- | --- |
| `agent/api.py` | The agent process's only HTTP client for the backend. Add one function per operation needed for run records, measurements, evaluation copies and startup recovery. Never request the answer key or scoreboard comparisons. |
| `agent/tools.py` | Keep the seven claim tools and their `ToolRuntime` context and graph state. Add the current execution's run id to `ClaimContext`; remove queue tools and `RunContext`. Run identity is supplied afresh on resume, rather than recovered from an earlier run's checkpoint. |
| `agent/harness.py` | Own model configuration, the process's checkpoint pool, claim graph construction, measurements, claim tracing and start/resume behavior. Expose one worker entry point taking a claim id and run id; it chooses start or carry-on from the backend's status and builds a graph with its own saver. Retain single-claim execution. |
| `agent/dispatcher.py` | The fifth agent part. Own a synchronous, model-free `StateGraph`, its dispatch state, selection, `Send` fan-out, worker retries and result collection. Its worker calls only the harness entry point to work a claim, never graph internals. The same graph works ordinary queue claims and a specified evaluation's copies. |
| `agent/service.py` | Accept dashboard starts through the backend, reserve the single dispatch/evaluation slot, schedule synchronous background work, finalize runs and recover stopped runs at startup. Continue accepting sign-off resumes during a run. Own evaluation checkpoint cleanup through the harness. |
| `agent/console.py`, `agent/__main__.py` | Keep report formatting and `uv run -m agent CLM-1001`. Remove queue invocation and `--cap`; a missing claim id prints usage and starts no work. |
| `backend/database.py` | Own SQLAlchemy table definitions, database/pool configuration, seeding and shared claim views. Add run membership, measurements, evaluation sources and separate labels; explicitly drop the retired to-do table before its parent tables when reseeding an existing database. |
| `backend/runs.py` | Own run lifecycle, membership queries, measurement persistence and summaries assembled from backend records. Move the existing run behavior out of `backend/main.py` before extending it. |
| `backend/evaluations.py` | Own set selection, creation of isolated copies and scoreboard queries. Use a small `backend/scoring.py` for the pure lab scoring formulas; only backend evaluation code joins labels to results. |
| `backend/main.py`, `backend/models.py` | Define typed HTTP contracts and thin route handlers. Extend claim filtering and sign-off enforcement for evaluation copies. Every operation retains its operation id, one-line docstring and required inputs. |
| `backend/agent_service.py` | The backend's only client for the agent service. Forward starts with N and the evaluation selection, propagate busy refusals and report an unreachable service. Keep the existing sign-off notification behavior. |
| `dashboard/src/api.ts` | The dashboard's only backend client, with strict types and one function per operation. |
| Dashboard pages and components | Extend `QueuePage` and replace `RunPanel`'s to-do display. Add `ScoreboardPage` and an evaluation detail page, sharing start controls and formatting where needed. Keep `ClaimPage` for original claims and evaluation copies. |

Dependency direction stays dashboard -> backend -> agent service -> dispatcher -> harness -> `agent/api.py` -> backend. The harness uses tools, and tools use the API. The dispatcher does not become a tool. Shared helpers live in the module that owns their job; no module reaches into another module's private helpers.

This design follows `docs/CODE_STANDARDS.md`: one client per service, SQLAlchemy Core for both databases, `ToolRuntime` for claim context, LangGraph for fan-out/retries/checkpoints, and React hooks and shared components for refresh and forms. Keep the claim instructions goal-based and unchanged. There are no dispatcher instructions because code chooses the work and builds the summary.

### Default model, cost and time

- With `MODEL_NAME` unset, use `openai/gpt-6-luna` with reasoning effort `low`. Any explicit `MODEL_NAME` uses that model with no reasoning effort set, even if it names the default model. Resolve this once for a run and record the model and nullable reasoning effort with it.
- In the claim graph's model node, record each received model reply's input, output and total tokens and OpenRouter's reported `usage.cost`, as exposed in the reply's metadata in the lab. Do not estimate cost from token prices or round small model costs to cents when storing them. Missing provider measurements remain explicitly unavailable rather than silently becoming a zero-cost call.
- A model-call record identifies the claim, attempt/thread, current run or explicit null, and provider reply id. Replaying a measurement write for the same reply is idempotent; making another real model call produces another chargeable record. Record received replies even if a later tool or claim attempt fails.
- Record active execution slices and their start/finish times across fresh attempts, resumes and errors. Use monotonic elapsed time, persisting progress measurements as steps complete. Close a slice on a sign-off pause and start another when work resumes. Queued time, process downtime and the wait for a handler are excluded; dispatcher backoff appears in run elapsed time rather than active execution time. Preserve the last recorded duration for a slice interrupted by a process kill so downtime is never counted on restart.
- A run's model cost and active claim time sum only records carrying that run's id, including failed attempts. A claim resumed in dispatch B keeps its attempt/thread from A, but new work belongs to B. A direct CLI invocation or a standalone sign-off resume has null run identity. Claim lifetime totals can sum all its records without changing any run's totals.
- Keep total active claim time distinct from run wall time, measured from run start to finish or stop. At N=25, wall time should be much less than the sum of active claim times. Show model costs with enough precision to distinguish small runs, and payouts in the brand's normal dollar format.

### Runs, backend records and API boundaries

Replace caps and model-written to-dos with run records of kind `dispatch` or `evaluation`. Each records status (`running`, `finished`, `stopped`), N, model, reasoning effort, selection when applicable, start/end timestamps and a backend-built summary. A run membership table records each selected claim once, its order, progress and final result; retries do not add duplicate members. Keep completed run results stable when an original claim is later resumed or signed off.

Use separate tables for model-call measurements and active work slices. Keep evaluation source claims and handler labels in separate tables, apart from working claims. A working evaluation copy carries its evaluation id and source mapping, but its claim payload contains no handler labels. All new tables and queries work on SQLite and Postgres in the existing `undercover` schema.

Planned operations, extending the existing naming convention:

| Operation | Route | Purpose |
| --- | --- | --- |
| `start_dispatch` | `POST /api/dispatches` | Dashboard sends required `concurrency` from 1 to 25; backend forwards it to the service. |
| `start_evaluation` | `POST /api/evaluations` | Dashboard sends required `set_name`, `tiny` and `concurrency`; backend forwards them to the service. |
| `start_run` | `POST /api/runs` | Service creates the accepted run in background with required kind, N, model, reasoning effort and selection fields. Evaluation creation also makes its copies in one transaction. Fields that do not apply are required with explicit null values. |
| `get_latest_run`, `get_run` | `GET /api/runs/latest`, `GET /api/runs/{run_id}` | Read status, membership, summary, model cost, active claim time and wall time. The queue's latest-run view selects dispatches, while the scoreboard lists evaluations. |
| `record_run_claim` | `PUT /api/runs/{run_id}/claims/{claim_id}` | Record selection/progress/result without free-form to-do text. Validate membership and keep repeated writes safe. |
| `record_model_call`, `record_work_slice` | `POST /api/claims/{claim_id}/model-calls`, `PUT /api/claims/{claim_id}/work-slices/{slice_id}` | Persist reported usage and cumulative active elapsed time with explicit run identity, including null for standalone work. |
| `finish_run` | `POST /api/runs/{run_id}/finish` | Backend derives the completed summary and evaluation scores from records; callers supply no summary text or scores. |
| `stop_runs` | `POST /api/runs/stop` | At service startup, mark every abandoned running dispatch/evaluation as stopped and return the evaluation copies needing checkpoint cleanup. |
| `list_evaluations`, `get_evaluation` | `GET /api/evaluations`, `GET /api/evaluations/{run_id}` | Dashboard reads newest-first scores and per-claim comparisons. The agent's service never calls these label-bearing operations. |

The agent service exposes start routes matching `start_dispatch` and `start_evaluation`, alongside health and claim resume. It reserves the run slot atomically before returning 202, and creates the backend run and copies in the background. A second dispatch or evaluation gets 409, including while the first is accepted but has not yet created its run. Release the slot on success or failure. The backend writes no run or copies before calling the service; an unreachable service returns a clear 503 and creates nothing.

Startup must wait for the concurrently started backend to be ready, mark abandoned runs stopped, and clear stopped evaluation checkpoints before accepting starts. Use the existing API client for readiness and recovery calls. Background work stays synchronous and runs away from the ASGI event loop, preserving Windows compatibility and responsive health/sign-off requests.

### Dispatcher, retries and checkpoint ownership

Follow the notebook's graph: `START` -> list eligible claims -> one `Send` to the worker per claim -> collect results -> `END`. The results list uses an additive reducer. Invoke with `max_concurrency=N`; compile without a dispatcher checkpointer. List ordinary waiting and in-progress claims in submitted-date order, using claim id as the tie-breaker. Do not select held payouts, completed claims, claims left for a handler, evaluation copies or a claim already reserved for a sign-off resume. Recheck eligibility when the worker takes ownership.

The harness entry point takes a process-local reservation for the claim and either starts a waiting claim on a new thread/attempt or resumes an in-progress claim on its existing thread/attempt. Sign-off resumes use the same reservation, including the interval between accepting the resume and starting background work. A dispatch skips a claim already owned by that resume. Release reservations when work returns, pauses or raises; backend decision/payment idempotency remains the final protection.

Attach a worker `RetryPolicy(max_attempts=3)` with exponential backoff and jitter, matching the lab's outer retry budget, but set `retry_on` only to LangChain's `ModelRateLimitError` and `ModelTimeoutError`. Let those exceptions escape the harness before settlement, so the backend leaves the claim in progress and the next worker invocation resumes its saved thread. Preserve the current model client's bounded request retries. The worker's `error_handler` converts exhausted transient retries into an in-progress result, records the problem, and lets other workers finish. Do not convert Ctrl-C or process termination into an ordinary failed attempt.

Other errors use the existing failure rules: if no decision exists, fail the attempt and immediately try the returned waiting claim once more within the same worker slot. A second failed attempt leaves it for a handler. Respect attempts already used in earlier runs. After a decision, preserve the decision and any payment; an unpaid approval stays in progress for recovery. At completion, reconcile membership with backend records so the summary includes every selected claim, including failed and unresolved ones. An empty dispatch finishes cleanly with zero totals.

Create one `PostgresSaver` per active claim execution on a single process-wide `ConnectionPool`, with at most four connections. Initialize checkpoint tables once at startup, not in each worker. Borrow connections only for checkpoint operations and let callers wait for the pool; never reserve a connection for a whole claim. The backend likewise retains its four-connection limit. Keep synchronous checkpointing and `durability="sync"`. Preserve each claim/thread's Langfuse session and fresh tracing context, adding `langfuse_tags: [run_id]` for run work. A later resume gets its current run's tag.

The installed LangGraph is 1.2.12 and supports node error handlers; the installed model integration exposes the required LangChain error classes. Implementation should recheck those APIs against the installed versions. The retry/error-handler lifecycle is documented in [LangGraph fault tolerance](https://docs.langchain.com/oss/python/langgraph/fault-tolerance), and the retry configuration is in the [RetryPolicy reference](https://reference.langchain.com/python/langgraph/types/RetryPolicy). Backend writes must remain idempotent because retries re-execute node functions.

### Evaluations and scoring

Seeding loads two labelled sets: `evaluation`, with the 25 original queue claims and `data/evals/decisions.json`, and `held_out`, with the 25 claims and labels from `held_out_claims.json` and `held_out_decisions.json`. Both label files mark five claims `tiny`. The brief authorizes reading these files for this change. Store full pristine source claims separately so evaluation inputs never inherit a queue claim's agent decision, attempts or payment.

The backend chooses the full set or the five label-marked tiny sources, creates fresh waiting copies, and rewrites each copy's embedded `claim_id` as well as its database key. Keep source mapping and expected decisions outside the payload available to claim operations. Policies, historic customer claims, documents and payout calculations are served through the same operations as ordinary work. Neither claim history nor any other claim tool may expose labels or other evaluation results.

Run the copies through the same dispatcher, harness, tools and graph. Dispatch selection always excludes them. A held copy retains its approval and normal held state, but is complete for evaluation purposes; no handler can sign it off. The service deletes its saved progress after evaluation completion, and cleans up all remaining evaluation threads after failure or startup recovery, once workers have stopped. Preserve claims, steps, measurements and comparison results. Stopped evaluations stay stopped and never resume in a later dispatch.

Score every selected member, including an unstarted or undecided copy. For scoring, an absent decision is `refer` with payout zero; retain the actual missing-decision/error state in the detail view. With C selected claims:

| Measure | Calculation |
| --- | --- |
| `decision_accuracy` | Matching decisions / C. |
| `accuracy` | Matching decision and absolute payout difference at most $1.00 / C. |
| `cost_of_mistakes` | Sum the lab's per-claim mistake costs below. |
| `straight_through` | Agent decisions other than referral / C. |
| `llm_cost`, model cost per claim | This evaluation's recorded model cost, including failed attempts; divide by C for the average. Preserve any unavailable measurement explicitly. |
| `seconds_per_claim` | This evaluation's total recorded active claim seconds / C, including failed attempts and resumes. |
| `seconds` | Run wall time from start to finish or stop; never the sum of parallel claim times. |

For a matching approval, mistake cost is the absolute payout difference, including differences within the accuracy tolerance. Matching declines/referrals cost zero. For differing decisions, an agent decline costs $1,500, an agent approval costs its recorded payout even if unpaid, and an agent referral costs $50. Name these constants once in the scoring module.

The scoreboard lists set, full/tiny selection, N, model, reasoning effort (display null as "None"), status, dates and every measure, newest first. A detail page puts handler and agent decisions/payouts side by side, shows the agent's reason and cost/time, identifies misses with text as well as color, and links to each copy's normal claim page. The queue's dispatch panel shows member progress, model cost, active claim time, elapsed run time and a backend-built summary: approval/decline/referral counts, each held payout and amount, and every claim left for a handler or still in progress.

## Delivery and verification

During implementation, complete phases in order. For each phase, add meaningful tests, verify every criterion, obtain a fresh subagent review against `docs/CODE_STANDARDS.md` and the diff, fix blocking findings, tick this plan, update `docs/STATUS.md` and commit with hooks enabled. Do not push. At the end, obtain the required whole-branch review against `main`, fix its findings and run `npm run check` again.

All automated checks use SQLite, stub models, in-memory checkpointing and service/Langfuse stand-ins, with no network or `.env` dependency. Concurrency tests use synchronization barriers and counters; time tests use a controllable clock, not timing-sensitive sleeps. Check pages with the Playwright MCP server as well as automated end-to-end tests. No real-model runs occur in phases 1-5; phase 6 uses exactly the brief's live sequence after one reseed.

## Phase 1: Default model and claim measurements

Build the measurement records and API first, then instrument the existing claim graph and execution paths. This establishes costs and timing before concurrency makes attribution harder. Preserve the existing queue temporarily while its replacement is built.

- [x] Stub configuration tests show the unset default is `openai/gpt-6-luna` with `low`, while every explicit `MODEL_NAME` has no reasoning effort. Model configuration is resolved consistently for execution and run records.
- [x] Seeded SQLite tables and SQLAlchemy definitions hold model-call usage and work slices, with explicit nullable run identity and no change to payout/sign-off rules.
- [x] Stub replies with known provider ids, token counts and sub-cent costs produce the exact stored values. Repeating the same measurement write creates one record; a distinct actual reply creates another. Missing metadata is identifiable rather than reported as free usage.
- [x] Fake-clock tests cover an ordinary completion, a failed attempt followed by success, a held approval and its later payment, and a stopped/resumed claim. Handler wait and process downtime add no active time.
- [x] Attribution tests split one saved thread across runs A and B and standalone work: each run contains only its own usage/time, including failures; null-run work changes neither total.
- [x] Existing graph, refusal, payment, sign-off, restart and model-call-limit tests pass. OpenAPI checks pass for the new measurement operations, including required nullable inputs.
- [x] `npm run check` passes; the phase is reviewed, `docs/STATUS.md` records the new default and measurements, and the phase is committed.

## Phase 2: Replace the queue agent with the dispatcher

Introduce backend run membership and deterministic summaries, the model-free graph and the single harness worker boundary. Refactor checkpoint ownership before testing concurrent workers. Remove the retired queue agent, middleware, instructions, tools, caps and to-do storage together with their callers and obsolete tests; update the instructions page and run panel to consume the replacement read contracts. Dashboard start controls follow in phase 3.

- [x] Barrier-based dispatcher tests at N=1, 3 and 25 show no more than N active workers, no missing or duplicate members, submitted-date ordering with a deterministic tie-breaker, and a completed empty dispatch with zero totals. Waiting and in-progress claims are eligible; held, decided, handler-owned and evaluation claims are not.
- [x] A stub rate limit and a stub timeout each resume the same claim/thread/attempt from its saved step and record one decision/payment. Exhausting the worker retries leaves it in progress, records the problem and still completes all other workers and the run.
- [x] An ordinary first-attempt failure is retried fresh within the same dispatch, with a new thread and attempt 2. A second failure leaves it for a handler. Previously used attempts are respected, and finishing without a decision follows the same failure rule.
- [x] Recovery tests interrupt before a first checkpoint, mid-claim, after decision/payment writes and around a sign-off pause. Work carries on without duplicate decisions/payments; a failure after a decision never resets the claim or decision.
- [x] Concurrent sign-off-resume and dispatch tests exercise the shared reservation, including accepted-but-not-yet-running background work. Only one executes that claim, the dispatcher reports the skip, and reservations are released on return, pause and failure.
- [ ] Pool tests show every active claim gets a distinct saver on the same process pool, setup happens once, maximum pool size stays four, and 25 workers wait for connections without connection failures. Claim tools remain sequential within each graph.
- [x] Run tests prove membership is unique across retries and summary counts, held claim ids/amounts, claims left for handlers and unresolved claims come from backend records. Summaries cannot be supplied by a caller; earlier run measurements/results do not change when a later run resumes a claim.
- [x] Langfuse recorder tests retain claim/thread sessions and root claim traces, adding the current run id through `langfuse_tags` without carrying an earlier run's tag into a resume.
- [x] `uv run -m agent CLM-1001` remains covered with a stub model; no-id and `--cap` invocations print usage and start nothing. The instructions response/page contains only the existing claim instructions and document contents.
- [x] A seed test begins with an old to-do table and verifies reseeding removes it, including the Postgres drop path. Repository searches find no production queue agent, queue instructions, `write_todos`, to-do schema or old queue-mode callers. Replace the obsolete to-do end-to-end test while retaining its independent failed-claim coverage.
- [x] `npm run check` passes; the phase is reviewed, documented in `docs/STATUS.md` and committed.

## Phase 3: Start dispatches from the dashboard

Add the service's admission/lifecycle handling and the backend forwarding route, then expose a start form on the queue. This puts run creation behind the service that owns execution and makes recovery and unavailable-service behavior visible to handlers.

- [x] API/service tests accept N=1 and N=25, reject N=0 and N=26 with 422, and return 202 without waiting for claim work. The backend forwards exactly N and writes no run before the service accepts.
- [x] Concurrent start tests prove only one dispatch/evaluation slot can be reserved, including before backend run creation. A second start gets 409; completion and background failure release the slot.
- [x] An unreachable service produces a clear 503 and no new backend run. A backend run-creation failure in background releases the slot and is logged rather than leaving the service permanently busy.
- [x] Startup tests mark all abandoned runs stopped, preserve ordinary claims' attempts/threads for the next dispatch and finalize their recorded measurements without counting downtime. Startup with the backend initially unavailable waits for readiness before recovery and admission.
- [x] Service tests show synchronous dispatch work runs away from the event loop; health and sign-off resumes remain responsive during a dispatch. Dev startup and restart work on Windows.
- [x] Vitest covers the default N=3, valid limits, pending/busy controls, 409 and 503 messages, refreshed member states, totals and the records-built completion summary.
- [x] End-to-end tests start a dispatch from the queue and assert the service stand-in receives N. Playing its run through backend APIs updates member links, progress, totals and summary without reloading the page.
- [x] Through Playwright MCP, inspect the queue with a stand-in run at `http://localhost:5173`: usable labeled controls, N=3 initially, busy/unavailable feedback, linked claims and summary, in light/dark mode and at a narrow width. There are no unexpected browser console errors.
- [x] `npm run check` passes; the phase is reviewed, documented in `docs/STATUS.md` and committed.

## Phase 4: Isolated evaluations and backend scoring

Seed the two permitted answer sets into isolated tables, create evaluation copies through accepted runs and run them with the existing dispatcher. Add backend comparisons and scoring before their UI so both privacy of the answer key and mathematical correctness can be tested independently.

- [x] Seed tests find 25 sources/labels and five tiny labels in each set, resolve their policy/customer references, and still return exactly 25 ordinary queue claims. A second seed clears evaluation copies, runs, measurements and scoreboard records.
- [x] Full and tiny run-creation tests select exactly 25 or five pristine source claims. Each copy has a globally new database id and matching embedded `claim_id`; no prior queue decision, payment, attempt or thread is copied.
- [x] Tests snapshot the ordinary queue records/counts before and after both kinds of evaluation and prove they are unchanged. Ordinary dispatch selection, customer history and queue counts never include evaluation copies or held-out sources.
- [x] Tests inspect `get_claim`, every claim tool response and stub model prompts for answer-key fields or joins: none expose the evaluated claim's handler decision, payout, reason, reference, scoring metadata or label-only tiny flag. Legitimate historical claim outcomes remain available. The service's backend-call recorder shows no request for labels or scoreboard comparisons.
- [x] Both evaluation modes exercise the same dispatcher and harness worker boundary at their requested N, with the same transient and ordinary failure behavior as a dispatch. Run records retain set, tiny/full choice, N, model and reasoning effort.
- [x] A held copy counts as completed without sign-off, has no payment, and its checkpoint is deleted. Backend sign-off on any evaluation copy returns 409 and sends no resume notification. The corresponding original claim's sign-off still works.
- [x] Finished, transient-exhausted, failed and startup-stopped evaluations clear their saved copy threads after workers stop while retaining steps, decisions, cost and timing. A stopped evaluation remains stopped; later dispatches never resume its copies.
- [x] Hand-calculated scoring fixtures cover matching decisions, the inclusive $1 boundary, a $1.01 miss, wrongful decline ($1,500), wrongful unpaid approval (its payout), needless referral ($50), differing amounts on matching approvals and an absent decision counted as referral.
- [x] A stopped run's denominators include all selected claims, including unstarted copies. Run/claim cost and active time include failed attempts; cost/time averages and wall time match a controllable-clock fixture, and zero-work states cause no division errors.
- [x] OpenAPI checks cover every new backend and service operation. `npm run check` passes; the phase is reviewed, documented in `docs/STATUS.md` and committed.

## Phase 5: Scoreboard and evaluation details

Add the scoreboard start form, list and comparison page using the proven backend contracts. Reuse the normal claim page for copies, clearly identifying them as evaluation claims and suppressing sign-off controls.

- [x] `/scoreboard` is linked from the header. Its form selects evaluation/held-out, full/tiny and N from 1 to 25, initially 3. The shared admission rules prevent starting either kind of run while one is active.
- [x] Vitest covers empty/loading/error states, live refresh, pending and busy starts, unavailable service, newest-first evaluations, running/finished/stopped states and every required measure, model and reasoning effort (including None).
- [x] Evaluation detail tests show expected and actual decision/payout side by side, agent reason, per-claim cost/active time, missing-decision outcomes and readable miss indicators, with working links to each copy.
- [x] Claim-page tests and browser inspection show an evaluation label and no sign-off form on held copies. Original claims retain their existing sign-off form and payment messaging.
- [x] End-to-end tests start an evaluation from the scoreboard and verify the service stand-in receives set, tiny and N. The test plays out copied claims and measurements through the API; the scoreboard and comparison page then show the backend's calculated results without a reload.
- [x] End-to-end coverage also exercises a busy response across dispatch/evaluation starts, a stopped evaluation, a held copy with no sign-off and unchanged queue counts.
- [x] Through Playwright MCP, inspect scoreboard, detail and copied-claim pages with stand-in records in light/dark mode and at a narrow width. Fonts, surfaces, payout formatting and status colors follow `docs/BRAND.md`; misses remain understandable without color. No unexpected console errors occur.
- [x] `npm run check` passes; the phase is reviewed, documented in `docs/STATUS.md` and committed.

## Phase 6: Live recovery, concurrency and operational handover

Update the product docs for the completed behavior, then carry out only the live runs specified in `docs/SWARM.md`. These replace the old five-claim live cap for this build. Use the default model with `MODEL_NAME` unset and working OpenRouter, Supabase and Langfuse configuration; record results without exposing keys.

- [ ] Run `npm run check` with `.env` temporarily moved out of the way and restore it afterward, even if a check fails. All Python, TypeScript and end-to-end checks pass offline with the existing coverage thresholds and rules unchanged.
- [ ] Run `npm run seed` once before this live sequence, then `npm run dev`. Verify 25 waiting claims and an empty scoreboard. Do not reseed between the following runs.
- [ ] In the queue through Playwright MCP, start a dispatch at N=3 and capture claim attempts, thread ids, start times and partial progress. Stop it mid-work by force-killing every agent process associated with port 8002, including uvicorn's reloader and worker. On Windows identify the listener with `Get-NetTCPConnection` and inspect its process ancestry with `Get-CimInstance Win32_Process` before using `Stop-Process` on the verified agent process ids; the interrupted processes are part of this planned recovery test.
- [ ] Confirm `npm run dev` stops its other services, restart it, and verify the first dispatch is stopped. Start N=25 through the dashboard: previously in-progress claims keep their attempts and threads, remaining waiting claims start, and decisions/payments remain unique.
- [ ] Use overlapping claim start/finish records to demonstrate concurrency up to 25 for the available unfinished claims. Report the N=25 run wall time and sum of active claim times, showing wall time well below the sum. Compare claims worked from the start at N=3 and N=25; if slower at N=25, use measurements/traces to report model, tool and checkpoint/pool wait time while keeping four connections per process.
- [ ] During the dispatch, sample `pg_stat_activity` through the read-only Supabase MCP server: no more than 12 connections whose user is `postgres`, with at most four per application process and no connection-related claim failures. Record the observed maximum.
- [ ] Verify held payouts remain unpaid. Sign off a held original claim through the dashboard and observe payment within five seconds, retaining handler and timestamp. Uninterrupted in-progress work and run totals remain consistent.
- [ ] Run the full evaluation set at N=25 and the held-out tiny set at N=5 through the scoreboard. Both appear with every measure and linked comparisons. Snapshot queue counts before and after each evaluation and confirm they are identical; held copies are complete with cleared checkpoints and no sign-off controls.
- [ ] Query Langfuse's API by the N=25 dispatch id tag and find its claim traces together, while each retains its claim/thread session. Record representative trace/session ids and any tracing delay in `docs/STATUS.md`.
- [ ] Update only SPEC's working-the-queue and team-visibility sections, leaving its Rules unchanged. Update `docs/TECH_STACK.md`, `docs/CODE_STANDARDS.md` rules 10 and 13, and `docs/PROCESS.md` for the fifth agent part, run behavior, isolated evaluation seeding and this live sequence replacing the old cap. `docs/STATUS.md` explains reseeding clears the scoreboard, dashboard starts, single-claim CLI use, retry/restart behavior and observed live results.
- [ ] Complete the final fresh-subagent review of the whole branch against `main`, fix blocking findings, rerun `npm run check`, record review/check/live evidence, tick the completed criteria and commit with hooks enabled. Do not mark an unavailable external verification as passed; document its exact remaining requirement.

## Scope

Keep the existing claim decision tools, instructions, manual/policy interpretation, payout rules, sign-off limit and durable graph behavior. This build adds no sign-in, real payments, customer messages, new AWS resources, cloud agent transport or later-day features. Do not change course notebooks or copy later briefs into `docs/`. Implementation begins only when the student asks to implement this plan.

Phase 2 checkpoint-pool verification: distinct per-execution savers, setup-once configuration and the four-connection limit are covered offline; the 25-worker Postgres wait/no-connection-failure observation remains part of phase 6. LangGraph 1.2.12 needs the narrowly guarded handled-future workaround for upstream issue https://github.com/langchain-ai/langgraph/issues/8277.

Phase 3 browser evidence: isolated SQLite/agent stand-in preview at localhost:5173; default N=3, accepted start, busy disable, member links and record summary checked at 1280px light and 390px dark. Simulated service503 displayed the expected message. No unexpected console errors; screenshots in ignored .playwright-mcp/. Review fixed plain-text upstream errors and verified responsiveness on a single ASGI event loop.

Phase4 verification: all seven claim tools exercised with sentinel handler labels absent from model prompts; successful held copies, exhausted transients, two ordinary failures and startup-stopped copies delete all checkpoint attempts while retaining backend records. Hand-calculated scores include missing/unstarted copies, failed-attempt cost/time, inclusive dollar tolerance and zero-work selection. Run reads use a fixed query count at1 and25 members. Fresh review found no code blockers and requested the added cleanup regressions.

Phase5 browser evidence: scoreboard, comparisons and held copied-claim pages inspected at1280px light and390px dark with a SQLite stand-in evaluation. All measures visible; queue records identical before/after; held copy has zero sign-off controls. InstrumentSans/black panels confirmed in dark mode; document width390px at390px viewport. Fresh review requested and verified in-place polling assertions and a consistent two-member fixture.
