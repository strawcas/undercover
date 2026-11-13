# Plan: configuration versions, an improver and handler sign-in

This plan implements `docs/IMPROVE.md` on the current Swarm build in six phases. Sign-in comes first, followed by configuration versions and settings, the judge, the improver, its dashboard page, and the final dashboard review and live checks. Each phase explains its purpose and has verifiable success criteria.

SIDENOTE: For consistency with the course, read `labs/week2day5.ipynb` and build the judge, the configuration's versions and the improver in the same style as the lab. Where the lab and this brief differ, follow the brief. This sidenote is included here as requested. The notebook's source cells have been read without executing them or changing the student's notebook.

Implementation is authorized and in progress. The checkboxes below record verified work.

## Design

### Starting point and scope

Keep the synchronous claim graph, its seven tools, the dispatcher, durable attempts, payment protections, evaluation copies, frozen run results and measurements. Extend these boundaries instead of replacing the Swarm architecture. Preserve the dispatch table's identity, timing, held-payment labels and fixed-query polling behavior.

Some notes in the brief describe a different Swarm implementation. Here, `backend/database.py` already stores each full handler label in `evaluation_labels.record`, including its reason and reference. Verify those fields in seeding tests and expose them only through the appropriate backend views; do not add a second label store. `agent/api.py` already contains internal run lifecycle operations, but no comparison or scoreboard reads. Retain those internal operations and keep result access exclusive to the improver's separate client.

The student's notebook currently substitutes free models. The brief takes precedence for the product: seed configuration 1 from `backend/instructions/claim.md` with `openai/gpt-6-luna` at low effort, and run the improver on Luna at medium effort. Preserve the lab's first recipe and its comparison formulas. Do not adopt the notebook's shorter claim prompt, in-memory product state, claim tools or optional assignment about alternate policy wording.

This build adds no Week 3 API key, security guard, AWS resources, real payments, customer messages or sign-up. The policy wording and claims manual remain unchanged. The historical Swarm timing checks in `docs/STATUS.md` remain separately identified; this plan does not add live runs to finish them.

### Modules and dependencies

| Module | Responsibility and dependencies |
| --- | --- |
| `backend/database.py` | SQLAlchemy Core tables and seed data for both SQLite and Postgres. Add handler emails, configuration/recipe history, current-version pointers, settings history, round/proposal records, judge results and round model usage. Keep database connections bounded as today. |
| `backend/auth.py` | FastAPI dependencies verifying bearer tokens with PyJWT `PyJWKClient`, the project's JWKS and audience `authenticated`; resolve the handler by email and require senior authority where needed. Read the Supabase address once. |
| `backend/settings.py` | Read and validate platform settings and the file-owned allowed models; record setting changes with handler and time; supply immutable round snapshots. Own the approval-limit lookup currently in `main.py`. |
| `backend/configurations.py` | Configuration and recipe versions, provenance, current pointers and `difflib` diffs. Provide a score-free configuration view for claim execution and separate dashboard history views. |
| `backend/judge.py` | The only OpenRouter Decisions caller. Own Jev, the lab's rubric/questions, endpoint origin, timeout, retries, response mapping and judge usage. It receives comparison inputs from evaluation code. |
| `backend/evaluations.py` | Join labels to copied-claim outcomes, prepare judge inputs, store grading, expose dashboard comparisons and aggregate eligible version scores. Keep claim-side reads free of labels. |
| `backend/improvement_rules.py` | Pure, tested comparisons and keep/rollback explanations using supplied scores and the round's settings. No network or database work. |
| `backend/improvements.py` | Round lifecycle, proposal records, spending checks, the restricted improver report and atomic verdicts. Use configurations, settings, evaluations and the comparison rule through their public responsibilities. |
| `backend/main.py`, `backend/models.py` | Thin routes and explicit request/response models. Use FastAPI dependencies for handler actions; every operation retains its `operation_id`, one-line docstring and required inputs, including explicitly nullable values. |
| `backend/agent_service.py` | The only backend client of the agent service. Extend it with round start, stop and proposal decisions, forwarding the verified handler and passing on refusals. |
| `agent/api.py` | Existing claim and internal run operations, plus fetching a score-free configuration by version. No result, comparison, label or round-report access. |
| `agent/improver_api.py` | The improver's sole backend client, with one function per round/report/proposal/evaluation-budget operation. Its responses never contain held-out claim details or labels. |
| `agent/improver.py` | The sixth agent part: LangChain `create_agent`, its four tools, goal-based instructions wrapping the versioned recipe, approval middleware, durable execution, round usage and tracing. It never imports the service or reads seed files. |
| `agent/harness.py`, `agent/dispatcher.py` | Build claim agents from the run's pinned version. Retain the graph and tools; add round stop/budget admission checks at claim boundaries and preserve fresh execution contexts. |
| Shared agent support | Extract shared checkpoint-pool ownership to `agent/checkpoints.py`, shared model construction to `agent/models.py`, and shared run execution/cleanup to `agent/run_execution.py` as they become needed. The harness and improver use separate savers on the same process pool of at most four connections. Shared code lives here rather than borrowing private harness/service helpers. |
| `agent/service.py` | Admission, background scheduling, named ownership of the one-work slot, round recovery and approval handoffs. Nested round evaluations execute in process through the shared runner; they do not request the slot again. |
| `dashboard/src/supabase.ts` and auth context | The sole Supabase client, password sign-in, session changes and sign-out. The context supplies the current handler/session to protected routes. |
| `dashboard/src/api.ts` | The sole backend client, with typed operations and bearer-token handling for handler actions. Pages never build service URLs or send their own requests. |
| Dashboard pages/components | Protected Configuration, Settings and Improvement pages, alongside the existing queue, claim, Scoreboard and evaluation pages. Share headers, dialogs, diff rendering, status labels, formatting and stable polling. Use SVG for the chart. |

Dependencies flow from routes to domain modules and database records; from the service to agent execution modules; and from agents to their own backend clients. No domain module imports `main.py`, and the improver does not call HTTP endpoints on its own service. Review this design against all code standards before implementing each phase. Use installed framework APIs and avoid circular imports, duplicated paths or alternative service clients.

### Authentication and permissions

The dashboard uses `@supabase/supabase-js` and `signInWithPassword`, with `VITE_SUPABASE_URL` and `VITE_SUPABASE_PUBLISHABLE_KEY`. Every product page requires a session; the sign-in page offers no registration. Restore and refresh sessions through the SDK, show the handler in the header and clear protected content on sign-out or expiration. Centralize access-token attachment in `api.ts`.

The backend verifies signature, expiry and audience, then matches the token email to a seeded handler. A valid token for an unknown handler has no authority. Never accept a handler identity supplied by a browser as proof of permission. The sign-off body no longer selects a handler.

| Handler action | Required authority |
| --- | --- |
| Start a dispatch or Scoreboard evaluation | Any signed-in handler |
| Sign off a held payout | Signed-in handler with `can_approve_over_limit` |
| Start/stop a round, approve/reject either proposal, change a platform setting | Signed-in handler with `can_approve_over_limit` |
| View product pages/settings/history | Any signed-in handler |
| Change appearance | Browser-local preference for the signed-in dashboard |

Dana keeps senior permission. Set Marcus and Priya to `false` in `data/handlers.json` and seed all three emails. Pages explain restrictions to other handlers, while the backend independently returns 403. Missing or invalid sign-in on a handler action returns 401. Internal service writes and claim-tool calls remain without handler sign-in for this build; routing and clients keep them distinct from handler actions.

### Durable records and configuration activation

Configuration versions contain complete instructions, model, reasoning effort, parent version, reason, proposer and approval provenance. Recipe versions contain complete text with equivalent provenance. Keep proposal/rejection history on the round; an unapproved proposal creates no effective version. An approved candidate is available only to its round's evaluations until the backend keeps it. The current configuration changes atomically with the verdict, never when the handler approves testing it. An approved recipe applies to future rounds; the current round keeps the recipe it started with.

Dispatches and standalone evaluations pin the current version when their run is created. Round evaluations pin either the captured baseline or the approved candidate. Persist the version id with model/effort on every run, and build the prompt and model from that version throughout execution. A CLI claim uses the same score-free configuration path. A process restart or later settings change must not silently substitute another configuration inside an existing run.

Seed recipe 1 from a new file beside `backend/instructions/claim.md`, containing the lab's first recipe verbatim. Seed configuration 1 from the current claim instructions; append the existing document contents when constructing its prompt, as today. Use `difflib.unified_diff` on the backend for both configuration and recipe text; show model and effort changes separately. Configuration history includes held-out aggregates, while the claim-agent configuration response never includes them.

Store each round's unique id and fresh checkpoint thread id, base/candidate version ids, recipe id, settings/model-catalog snapshot, progress, proposals and decisions, before/after scores, verdict/reason, timestamps and attributed costs. Rebuilding with `npm run seed` resets versions to 1, clears rounds and evaluations and cannot reuse an old round thread. It does not manage Supabase Auth users.

### Settings and defaults

Persist platform settings in the backend database and seed them from `data/company.json` and a new `data/improvement_settings.json`. Every consumer reads these records rather than keeping a competing in-memory or file setting. Seed the lab's values:

| Setting | Initial value |
| --- | --- |
| Approval limit | $1,000.00 |
| Held-out accuracy floor | 0.60 |
| Held-out judge-grade floor | 1.00 on the 0–3 scale |
| Noise: accuracy / grade / cost / time | 0.10 / 0.15 / 0.15 / 0.15 |
| Tolerance: accuracy / grade / cost / time | 0.10 / 0.15 / 1.00 / 0.25 |
| Spending limit per round | $0.50 |
| Concurrent claim agents in round evaluations | 25, validated from 1 to 25 |

Accuracy and grade thresholds are absolute differences; cost and time thresholds are fractions of the old value. Label these units clearly in the Settings page. Validate ranges and record old/new values, handler and time for each platform change.

Use a new `data/allowed_models.json` for numeric input/output prices in dollars per million tokens and supported efforts. For the default product, use the paid catalogue shown in the lab: `openai/gpt-6-luna` (`none`, `low`, `medium`; $0.10/$0.50), `google/gemini-3.5-flash-lite` (`minimal`, `low`; $0.30/$2.50), and `deepseek/deepseek-v4.1-flash` (`low`; $0.30/$1.20). The page lists this catalogue read only. These are the lab's starting inputs to the estimate, not a request to discover or add other models.

A round snapshots improvement settings and allowed models at start. Changing settings later affects future rounds. A changed approval limit applies when a new decision is recorded: retain the limit/sign-off requirement on that decision so already-held or already-approved claims do not change meaning at payment time. Calculations for undecided claims use the latest limit; the backend's decision write is authoritative.

If `MODEL_NAME` is set, it is the only allowed model for both agents, with recorded effort `default` and prices zero. In that mode, seed configuration 1 with this effective model/effort so the configuration page, pinned run and actual execution agree. Omit an explicit provider reasoning setting for `default`; distinguish it from the real effort `none`. Skip Jev and show evaluations as not graded. Build and test this mode, but do not run live improvement rounds on it.

### Judge, scores and the decision rule

Use the lab's F/C/B/A rubric and three questions with `typesafe/jev-1.13`. Include the handler's decision, reason and reference and the agent's decision, reason and section in the judge state. Keep the single Decisions call in `backend/judge.py`; pass its environment-configured origin as `server_url` on every call, with a 10-second timeout and retries disabled using the installed SDK API.

Finishing an evaluation prepares immutable inputs, grades its claims in parallel, persists grades/costs and freezes results idempotently. Release database connections during judge calls so the four-connection pool does not serialize network waits. Keep the whole finish operation under 20 seconds, inside the claim client's existing 30-second timeout. Polling reads saved grades and never calls the judge again.

Store Jev's numerical score, the highest-probability letter and both check probabilities. Show the checks as Yes/No using a documented 0.5 threshold. Never round the score to obtain the letter. An undecided claim receives F/0 without a judge call; a judge failure is Not graded and excluded from the average. A wholly ungraded run has a null grade. In free mode all claims are Not graded because judging is disabled.

Retain existing measures and denominators. Cost per claim for comparison remains claim-agent model cost; judge cost is a separate evaluation total. Round spending includes both. Version aggregates use only completed whole-set evaluations associated with rounds, never tiny runs, dispatches, stopped evaluations or manual Scoreboard evaluations. Average the eligible run-level measures per version and set.

Before proposing, a round fills any missing baseline evaluations: one full evaluation-set run and two full held-out runs. The candidate gets one full evaluation-set run followed by two full held-out runs. Compare its held-out average with the current version's captured eligible average:

- Meet both floors, and have a grade.
- Improve at least one of accuracy, grade, cost per claim or active seconds per claim strictly beyond its noise.
- Worsen none of those measures strictly beyond its tolerance. Equality at a floor or tolerance is allowed; equality at a noise threshold is not an improvement.
- Roll back when evaluations are stopped, unaffordable or insufficient for the comparison. Persist an explanation and the measures behind every verdict.

For accuracy/grade use `new - old`; for cost/time use the lab's multiplicative comparisons against `old`. Implement those inequalities directly so a zero old value needs no division. Null measures must not become zero or produce a false keep.

### Improver execution, approval and recovery

The improver's surrounding instructions describe its goal, its evidence and what a completed round means. Explain that the claim agent has its instructions plus document contents, claim, policy, customer history, document-search/read tools and payout calculation. Changes must generalize and must never name individual claims. The versioned recipe is data and can retain the lab's ordered steps. Neither prompt nor tools grant authority to change objectives, thresholds, budget, model catalogue, approval limit, sets or judge.

Keep exactly `read_results`, `propose_change`, `run_evaluation` and `propose_recipe`. `read_results` returns the current configuration, scores for both sets, all wrong claims and the five lowest-graded right claims from its latest eligible evaluation-set run, handler/agent explanations and both judge checks, prior-round summaries, rule/settings, remaining budget and allowed models. All held-out evidence returned to the improver is aggregate scores only, including earlier-round and evaluation tool responses. Do not expose held-out claim ids, stories, labels, reasons, links or per-claim grades through another tool response or error.

Configure `parallel_tool_calls=False`, a persistent 30-model-call cap per round matching the claim agent's existing cap, and `HumanInTheLoopMiddleware` allowing approve/reject on both propose tools. Validate model/effort proposals before they reach the approval interrupt; return an invalid proposal to the improver as tool feedback without publishing a pending handler action. Verify the installed middleware ordering rather than relying only on validation inside an interrupted tool.

Each invocation uses its own `PostgresSaver`, `durability="sync"` and the same round thread across approvals. Run nested dispatchers in a fresh `contextvars.Context`, retaining the harness's existing fresh context per claim graph, so evaluation work cannot inherit the improver checkpoint thread.

The service owns the work lock and its human-readable owner. A round's active stretch holds it across its evaluations. It releases it at an approval pause, so a dispatch or evaluation may run while the round stays open. Only one round may be open. Starts and proposal decisions during another owner's work return 409 naming that work; internal evaluations never compete with their parent round for admission.

The backend forwards a verified handler's proposal decision to the service. Only after reserving the work slot and validating the pending proposal does the service record the decision/handler with the backend and resume the durable agent. The handler-facing route does not write a duplicate approval record. Use proposal identity and state transitions to prevent duplicate resumes or stale approvals. A rejected proposal carries the handler's reason back to the improver.

Progress covers baseline evaluations 1–3, improver working, configuration approval, candidate evaluations 1–3, verdict, recipe approval, and finished/stopped. A round proposing nothing finishes with no candidate. A waiting approval survives restart and remains resumable. Startup stops an interrupted active stretch and its evaluations; before-verdict stops preserve the old current version, and after-verdict stops preserve the recorded verdict. Accepted recipe versions also remain part of history.

Stopping marks the round stopped in the backend first. Subsequent state/proposal/verdict writes for it are refused. The service starts no more claim work or model calls, lets in-flight work end, clears evaluation checkpoints and releases the lock only after draining. Do not kill ordinary claim work or remove its saved progress to stop an improvement round.

### Spending and attribution

Persist usage idempotently: improver replies belong to the round, claim replies to their evaluation/run, and judge responses to their evaluation. The round total includes all three, including baseline evaluations, retries with reported usage and rejected proposals. Preserve unavailable provider cost as unavailable rather than silently recording zero.

Before each evaluation, estimate its claim count times the highest observed model cost per claim for that model. For an unseen model, scale the current model's observation by the larger input/output price ratio. Include a judge allowance when checking the round's remaining total. Check affordability for the candidate's required evaluation sequence as well as before each run. A newly seeded model has no observation: establish a conservative price-based bootstrap estimate from bounded input/output allowances, test that path, and never silently estimate the baseline as free. This is an implementation detail of the spending guard, not a new handler-controlled setting.

Recheck authoritative spending and stopped state before each improver model call and each claim start, including retry admission. Serialize that admission check where concurrent workers could otherwise use stale totals. Once the limit is reached, start no new claims; retain completed work, mark the cut-short evaluation stopped and roll back an unverified candidate. Account for calls already in flight; the OpenRouter key credit limit remains the external ceiling. A refusal returns the estimate, remaining budget and reason to the improver.

Use one Langfuse session named for each round across all its approval stretches. Tag evaluation claim traces with both round id and run id; retain existing claim/thread identity. Keep model and judge costs separate on evaluation pages and show the combined total on rounds.

### Dashboard and offline checks

Configuration replaces Instructions and retains read-only access to the document contents. Settings shows platform settings, audit history, allowed models and browser-local light/dark/system appearance. Improvement shows the current round, pending diff and rationale, permissions, newest-first history, before/after scores, verdict/cost, recipe history and an SVG accuracy/grade chart with explicit units/scales and a text/table equivalent. Reuse one diff component for configuration and recipe changes.

Build shared headers, aligned tables, action buttons, word-and-color status chips and accessible in-app confirmation dialogs. Confirm proposal decisions and platform-setting changes before sending them. Preserve unsaved inputs, selection, scroll and focus during polls; avoid overlapping requests or replacing the whole page with a spinner. Provide actionable loading, empty and error states. Match the fonts, colors, rounded surfaces and US-dollar formatting in `docs/BRAND.md` in both themes on a laptop.

Tests use stub models, SQLite, in-memory checkpoint stand-ins, a signed-token Supabase Auth stand-in and a judge stand-in. The Auth stand-in serves its own JWKS, password/refresh responses and cross-origin checks; it never uses real credentials. Configure Auth and judge origins once per owning module. Vite loads the root `.env` only for the dev command, not Vitest or the isolated end-to-end servers; test values override inherited shell values. Keep `HANDLER_PASSWORD`, database passwords and provider keys out of browser bundles and saved test artifacts.

## Phase 1: Handler sign-in and authorization

Build sign-in first so every later page is developed behind the same session and permissions. Add the SDK and JWT dependency in the product dependency groups that use them, seed handler emails/permissions, replace the sign-off selector, and make existing tests sign in through local stand-ins.

- [x] Seed tests show Dana can approve over the limit, Marcus/Priya cannot, and all three handler emails are stored. Existing full label records remain intact.
- [x] Auth tests verify valid asymmetric tokens and refuse missing, malformed, expired, wrong-audience, wrong-signature and unknown-handler credentials. Existing dispatch/evaluation/sign-off handler actions require valid sign-in; internal agent operations still work without a handler session.
- [x] API tests show any handler can start dispatch/evaluation work, Marcus gets 403 for a held payout, and Dana's sign-off records Dana from the token with no browser-supplied handler id. Payment remains idempotent and held copies cannot be signed off.
- [x] Vitest covers session loading/restoration, sign-in errors, expiration/sign-out, protected deep links, header identity and the explanatory restricted sign-off state.
- [x] The local Auth stand-in signs tokens, serves JWKS and handles browser CORS/preflight. All existing end-to-end specs sign in and pass without real Auth or `.env`.
- [x] Playwright MCP checks the sign-in page, a protected direct claim URL, sign-in/out, retained destination and handler restrictions using stand-in credentials. There is no sign-up control or unexpected console error.
- [x] Vite and test configuration separate root dev environment loading from Vitest/e2e. No password or provider secret is exposed as a `VITE_` value.
- [x] Relevant documentation describes sign-in and permissions. `npm run check` passes; a fresh code-review subagent checks the diff against `docs/CODE_STANDARDS.md`, blockers are fixed, this phase's criteria and STATUS are updated, and the phase is committed.

## Phase 2: Configuration versions and Settings

Add backend-owned version records and settings before judging or improving anything. Pin configurations on runs, seed the recipe/catalogue/defaults, replace Instructions with Configuration, and build Settings with senior-only edits and appearance controls.

- [x] SQLite seed tests create configuration/recipe 1, current pointers, numeric model prices and all defaults listed above, with no rounds. A second rebuild resets the same state and creates fresh identities. Use temporary databases for these tests; reserve the business database's single reseed for phase 6.
- [x] API tests return current/history/version diffs, model/effort provenance and document contents. The claim execution view contains instructions/configuration only, with no scores or labels. Run records persist the chosen version, and stub prompts/models match it throughout a dispatch and standalone evaluation.
- [x] Model construction, CLI execution and run records share the version path. An explicit `MODEL_NAME` produces the sole allowed model, zero prices and effort `default`, while `none` remains a distinct allowed Luna effort.
- [x] Settings tests validate floors, nonnegative thresholds/budget/limit, grade/accuracy ranges and N=1–25. Dana's changes record old/new values, identity and timestamp; missing sign-in returns 401 and Marcus's write returns 403 with no audit/configuration change.
- [x] A decision recorded before a limit change retains its original sign-off requirement; the next decision uses the new limit. Test increases, decreases, equality at the limit and payment after changing it, without duplicate decisions or payments.
- [x] Vitest/e2e cover Configuration replacing Instructions, selectable versions/diffs and readable document contents; Settings read-only explanations, validation, confirmation/cancel/save/error, allowed-model display and audit history.
- [x] Appearance light/dark/system persists in browser storage, follows system changes only in system mode, and applies before normal page rendering. Playwright MCP checks these pages in both themes with no unexpected console errors.
- [x] API/OpenAPI checks retain explicit required inputs and one client per service. Shared model/checkpoint support is extracted only where needed, without adding pools or changing claim graph nodes/tools.
- [x] Update SPEC, TECH_STACK, CODE_STANDARDS rules 7/13, STATUS and `data/README.md` for the new ownership. `npm run check` passes; fresh code review blockers are fixed, criteria/STATUS updated and the phase committed.

## Phase 3: The explanation judge and version scores

Add grading inside evaluation completion, retaining existing scoring and measurements. Persist results once so later polling, dashboards and round comparisons use the same evidence.

- [x] Judge unit tests verify the lab's three questions/rubric, explicit agent section, per-call `server_url`, disabled retries and 10-second timeout through a stubbed installed SDK boundary. No test reaches OpenRouter.
- [x] A response with score 1.6 and highest probability at A displays letter A, proving the letter is not rounded. Both check probabilities are retained, with deterministic Yes/No display at the chosen boundary.
- [x] An undecided claim gets F/0 with no judge call; a failed judge call is Not graded and excluded from the average; all-ungraded and free-mode evaluations have null grade. One failing claim does not discard other grades.
- [x] A slow-judge stand-in proves grading runs in parallel, the entire finish request stays under 20 seconds, database connections are released during calls, and the 30-second caller does not time out. Startup/stopped-run cleanup also stays within the finish contract.
- [x] Finishing/retrying completion and polling cannot duplicate grading charges or change frozen outcomes. Judge cost is stored separately from claim cost, with a total available for later round accounting.
- [x] Tests distinguish full round evaluations from manual full evaluations, tiny runs and stopped runs; only eligible completed round runs enter a version's set averages. Existing mistake, accuracy, straight-through and time formulas remain unchanged.
- [x] Scoreboard and evaluation detail tests show average score/grade, claim letters, same-grounds/section checks and separate judge cost, including missing-grade states and copied-claim links.
- [x] Sentinel-label tests prove claim prompts/tools/version fetches still receive no handler decisions, reasons, grades or scores. Playwright MCP checks the graded and ungraded pages using the stand-in.
- [x] `npm run check` passes; a fresh code review checks the grading boundary and transaction lifetime, blockers are fixed, criteria/STATUS updated and the phase committed.

## Phase 4: Durable improvement rounds and spending control

Build the backend rule, round/report APIs and improver service on the earlier foundations. The service owns orchestration and admission; the backend owns evidence, spending records and the activation verdict.

- [ ] Hand-calculated score fixtures exercise a keep, missed floors, excess regression, no improvement, equality at thresholds, zero old cost/time and null grade/measure rollback. Cost/time comparisons are relative; accuracy/grade comparisons are absolute.
- [ ] Baseline tests run only missing whole-set round evaluations until the base version has one evaluation and two held-out runs. Candidate testing runs evaluation/held-out/held-out against the approved version; manual/tiny/stopped runs do not satisfy these counts.
- [ ] The restricted report includes the lab's evidence plus both judge checks and current budget. Sentinel tests on every improver response, errors, earlier rounds and tracing show held-out content/labels never enter improver prompts; claim-side clients still have no score access.
- [ ] Stub-agent tests exercise the four exact tool names, goal-based wrapper, original recipe, medium Luna default, `parallel_tool_calls=False` and a persisted per-round model-call cap that does not reset after approval/restart.
- [ ] An unapproved/rejected proposal changes no effective configuration or recipe. A rejection's reason reaches the model. Disallowed model/effort proposals return to the model before an approval pause appears. Approved configuration versions remain candidates until a keep verdict.
- [ ] Starts, stops and proposal decisions require senior authority. Approval/rejection while a named dispatch/evaluation owns the slot returns 409 and writes no approval. Duplicate/stale decisions cannot resume twice. A second open round is refused, while dispatch/evaluation work can run during an approval pause.
- [ ] Budget fixtures cover baseline, improver, candidate and judge spend; model-specific maximum observations; unseen-model price scaling; cold-start estimates; missing/zero prices and idempotent usage. An unaffordable evaluation is refused with estimate/remaining amounts and an unverified candidate rolls back.
- [ ] Concurrent stub workers prove no new claim/retry starts after the spending threshold or stop, cut-short evaluations become stopped and the slot stays held until in-flight work drains. No further improver model call starts once stopped or out of budget.
- [ ] In-memory checkpoint integration tests prove durable approve/reject continuation, independent claim/round threads, fresh `contextvars.Context` for nested dispatch, and separate savers on one bounded pool. A round gets a fresh thread after reseeding.
- [ ] Restart tests preserve pending approvals, stop abandoned active stretches and evaluations, preserve the old current version before a verdict, preserve a committed verdict afterwards, and reject late writes after stop. A no-proposal round finishes with no candidate; an accepted recipe applies to the next round.
- [ ] Trace/usage stand-ins verify one session per round across approvals, evaluation claims tagged with round and run, and round totals without duplicate reply or judge charges.
- [ ] Update PROCESS for the Improve live sequence and CODE_STANDARDS/TECH_STACK for the sixth part and client separation. `npm run check` passes; fresh code review blockers are fixed, criteria/STATUS updated and the phase committed.

## Phase 5: The Improvement page

Make the approval loop and history reviewable by handlers. Reuse the shared diff, dialog, formatting and polling components rather than putting orchestration or scoring rules in the browser.

- [ ] The page shows every round state described in the design, evaluation progress 1–3, settings snapshot, base/candidate/recipe versions, improver rationale and remaining/total spending. Polls preserve page selection, focus, scroll and entered rejection reasons.
- [ ] Pending configuration proposals show colored instruction changes and before/after model/effort; recipe proposals use the same diff component. Approve/reject have in-app confirmation, rejection requires a reason, and failed/busy requests preserve the proposal without suggesting success.
- [ ] Dana can start/stop a round and decide proposals; Marcus/Priya see explicit permission explanations and history. Vitest/API tests cover every new handler action without sign-in and without senior authority, with no unauthorized writes.
- [ ] Newest-first history shows before/after held-out measures, verdict/reason, proposal decisions/handlers/times, recipe changes and attributed costs. Versions tried are retained even after rollback or stop, and no-proposal rounds have a clear empty candidate state.
- [ ] The SVG chart shows held-out accuracy and grade for each tried version with readable labels, explicit scales, missing-value handling and an equivalent table. No chart library is added.
- [ ] End-to-end tests sign in, start a round, play proposal/progress states through the stand-in API, reject with a reason, approve a later proposal, observe verdict/recipe completion and follow Configuration history. A busy dispatch refuses approval with no decision recorded.
- [ ] Playwright MCP checks loading, empty, error/retry, awaiting-approval, running, kept/rolled-back and stopped states in both themes at laptop width. Keyboard focus and dialog cancellation work; there are no unexpected console errors or layout jumps during refresh.
- [ ] `npm run check` passes; a fresh code review checks that the dashboard contains no copied rule or direct service request, blockers are fixed, criteria/STATUS updated and the phase committed.

## Phase 6: Dashboard consistency and the prescribed live checks

Bring all pages to the same visual standard, then verify the finished approval loop with real services. Reuse existing behavior on older pages and keep the real-model sequence to the brief's two rounds and one dispatch.

- [ ] Queue, original/copied claim, Configuration, Scoreboard, evaluation detail, Improvement, Settings, sign-in and not-found pages share clear headers, spacing, active navigation, buttons and explanatory states. Tables align numbers right with visible units; status words accompany colors; diffs clearly distinguish additions/removals.
- [ ] Playwright MCP checks every page in light/dark mode at a laptop viewport, including long tables/diffs and refreshed progress. Keyboard interactions, focus, readable contrast and local scrolling work without document overflow or unexpected console errors. Existing dispatch/sign-off/evaluation workflows retain their behavior.
- [ ] Capture light/dark screenshots of every page. A fresh subagent reviews them once as a senior product designer against BRAND and IMPROVE point 8; fix its findings and retain the review evidence.
- [ ] Before live work, check setup without printing secrets: the three pre-created Supabase users, sign-ups disabled, asymmetric JWKS, `VITE_SUPABASE_URL`, publishable key, `HANDLER_PASSWORD`, database/OpenRouter/Langfuse configuration and the key's credit limit. If setup is missing, record the exact missing prerequisite; do not claim a live pass or create external accounts/resources as part of planning.
- [ ] Unset `MODEL_NAME` in both `.env` and the launching PowerShell environment (`$env:MODEL_NAME = $null`) for default-model verification, preserving the original configuration for restoration. Confirm Luna low for the claim agent and Luna medium for the improver in persisted records.
- [ ] Run `npm run seed` once against the business database, then `npm run dev`. Verify 25 waiting claims, empty Scoreboard/round history and configuration/recipe 1. Do not reseed between the following checks or run additional exploratory real-model evaluations.
- [ ] Through Playwright MCP, sign in as Dana and run two rounds from Improvement. Reject the first proposal with a reason, approve its replacement, and approve subsequent configuration/recipe proposals. Observe live progress, persisted before/after scores, verdicts and cost, plus Configuration versions/diffs and recipe history. Report actual outcomes even if a candidate rolls back.
- [ ] From version 1's two baseline held-out runs, report their difference on accuracy, grade, cost per claim and active time per claim beside the configured noise/tolerance. Explain absolute versus relative comparisons and record database-related timing variability without changing thresholds to manufacture a keep.
- [ ] Use Langfuse's supported API to find each round's improver traces in one session and evaluation claim traces tagged with round/run. Check durable pause/recovery live only if naturally required by these runs; synthetic tests cover deliberate crash cases without extra paid runs.
- [ ] Start the prescribed N=25 dispatch on the resulting current version. As Marcus, verify a held original payout cannot be signed off and Settings cannot be changed. As Dana, sign off that same payout and verify payment and Dana's recorded identity. Evaluate copies remain outside the queue and cannot be signed off.
- [ ] Report live spending split into improver, claim evaluations, judge and dispatch, with combined total. The expected total on Luna is under $1; retain actual usage and report missing usage or an overrun explicitly. Do not launch extra rounds to obtain a preferred verdict.
- [ ] Update SPEC, TECH_STACK, PROCESS, CODE_STANDARDS rules 7/13, STATUS and `data/README.md` to describe the finished product, defaults, sign-in, permissions, settings/version ownership, evidence isolation, costs and real verification results. Record any unavailable external verification precisely, and preserve the separate historical Swarm limitations.
- [ ] Review the complete branch against `main` with a fresh senior-engineer subagent and fix merge blockers. Preserve unrelated student changes. Temporarily move `.env` aside, run the final `npm run check`, and restore it in a `finally` path even on failure; confirm byte-for-byte restoration. All existing lint/type/coverage thresholds remain unchanged, no check is bypassed and no test calls a real service.
- [ ] Tick only verified criteria, update STATUS with final reviews/checks/costs, commit the checked phase changes without bypassing hooks, and report remaining limitations. Never push.

## Implementation and handover

During implementation, complete each phase in order with its tests, Playwright MCP checks where listed, fresh code review and fixes, plan/STATUS updates and commit before moving on. Keep automation cross-platform through `uv run` or `.mjs` scripts. Do not edit the student's lab notebooks or include their unrelated work in these commits.

The student's review authorized implementation. Live verification remains confined to the phase 6 sequence; earlier browser checks use isolated stand-ins and temporary databases.
