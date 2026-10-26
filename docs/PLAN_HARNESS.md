# Harness implementation plan

## Goal and scope

Implement `docs/HARNESS.md`: one queue agent chooses its work, maintains a visible to-do list, delegates each claim to a fresh claim agent, looks up the policy wording and claims manual by section, recovers from failures and finishes with a short summary. Support the whole waiting queue, a cap on distinct claims and one named claim.

Keep FastAPI, SQLite, LangChain `create_agent`, the React dashboard and the existing OpenRouter/Ollama configuration. Reuse exact monetary storage, database ownership, HTTP readiness, claim activity, handler sign-off, simulated payments and terminal rebuilding. The default OpenRouter model becomes `deepseek/deepseek-v4.1-flash` with low reasoning effort; `MODEL_NAME` continues to override it.

Apply low reasoning effort when that model is selected implicitly or explicitly. Preserve compatibility with other model overrides instead of forcing an unsupported reasoning parameter onto them.

The current pilot already recovers undecided failed claims to Waiting, preserves committed decisions and payments, and runs one finite startup pass. The harness extends that foundation with automatic retries and agent-led ordering. It replaces the fixed per-claim processing loop described in the pilot plan. `docs/PLAN.md` remains the completed historical plan and is unchanged.

Only this plan is written during planning. Update the specification, stack, instructions, status and code during implementation. Do not open evaluation answer keys, inspect Ed's branches or build later-day features. Supabase, a custom durable LangGraph workflow, concurrent claim agents, evaluation scoring, sign-in, customer messages, real payments and AWS resources remain outside this build.

## Design decisions

### One queue agent and isolated claim helpers

Add a queue entry point alongside the existing `run(claim_id, thread_id)` claim entry point. Both use `create_agent` and the shared model factory. The queue agent has tools to inspect eligible work, write its to-do list, delegate one named claim and check completion. Its instructions tell it to choose an order and keep its plan current. Backend admission checks enforce the run's scope and limits; they do not choose the order.

Each delegation reserves the requested claim atomically, creates a new attempt/thread and invokes a fresh claim agent. The helper receives its claim ID and claim instructions, then obtains that claim's context through HTTP tools. It receives no queue conversation or earlier helper messages. Return one concise line based on stored results: claim ID, outcome, payout and payment/sign-off state, or a sanitized failure. Detailed tool results and evidence remain on the claim. Process one claim at a time, including when a model requests several delegation tools together.

Use small repository-owned tools and middleware for persisted planning, completion checks and activity. LangChain's documented [subagents as tools](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents) support isolated claim work. Its [retry middleware](https://docs.langchain.com/oss/python/langchain/middleware/built-in) supports bounded model and tool retries. These fit inside the existing agent framework. A small persisted `write_todos` tool will support all four business statuses and stable claim links.

### Finite runs, caps and commands

Retain one run per backend startup and the standalone terminal command. Both invoke the same queue coordinator after backend HTTP readiness. Dashboard polling and navigation only read progress. There is no continuous watcher or new dashboard start button.

At run creation, snapshot eligible waiting claim IDs after interrupted-attempt recovery. The queue agent can choose any order within that snapshot. Claims arriving afterward wait for another run. Store the run ID, scope, cap, admitted claim IDs, attempts, status, to-do list, timestamps and final summary in SQLite.

Proposed terminal interface, with UnderCover stopped:

```powershell
uv run -m agent
uv run -m agent --max-claims 5
uv run -m agent --claim-id CLM-1001
```

`--max-claims` is a positive integer. `--claim-id` restricts the run to one eligible named claim; reject combining these flags. Unknown, decided, active or handler-review claims produce a useful response without reassessment. Keep the existing claim entry point available for delegation and tests.

Add optional `UNDERCOVER_MAX_CLAIMS` for startup trials and the terminal default. Unset means the whole snapshot; an explicit CLI cap overrides it. Named-claim mode has a scope of one regardless of that setting. Validate configuration before starting work. Document the setting in `.env.example` without modifying the student's `.env`.

A cap counts distinct claims admitted, including claims that fail. A retry uses the same slot. With a cap of five, no sixth claim can start even if several of the five need another attempt. If fewer claims are eligible, process those available. Atomic admission also rejects claims outside the snapshot and overlapping runs against the same database.

### Observable plans and honest completion

Persist to-do items with stable IDs, order, plain-language text, one linked claim ID and one of `to_do`, `in_progress`, `done` or `left_for_handler`. Each item represents one claim; queue setup and completion are represented by run state and summary. Show these as To do, In progress, Done and Left for a handler. The agent writes and updates the plan; backend validation reconciles statuses with actual attempts and decisions. A list edit cannot hide admitted unfinished work or mark an undecided claim done.

Successful approvals and declines become Done. Recorded referrals, approvals needing sign-off and twice-failed undecided claims become Left for a handler, with the distinct reason visible. A twice-failed claim has no insurance decision or payout. Do not invent a referral to represent a technical failure. Existing handler sign-off and payment remain separate actions.

Before accepting a final answer, reconcile the plan against stored claims and the run snapshot through a completion check. If capacity remains and eligible work was missed, or an admitted claim still needs its permitted retry, return the missing IDs to the queue agent and continue within a bounded coordinator budget. When the cap is reached, unadmitted waiting claims are expected leftovers. A run cannot report success while admitted work is active or unresolved. If the coordinator exhausts its limits, end visibly as incomplete and leave remaining work recoverable.

Store a short handler summary with factual counts derived from the database: distinct claims worked, assessment attempts, approvals, declines, referrals, simulated payments, pending sign-offs, technical handoffs and claims left waiting. Print results as claims settle and the summary at the end. Keep terminal exit codes 0 for a completed workload, including business referrals/sign-off; 1 for setup failure, incomplete work or unresolved technical handoffs; and 130 for interruption. A transient failure recovered by a later successful attempt does not by itself make the run unsuccessful.

### Section lookup and auditable decisions

Add `data/claims_manual.md` to the backend's explicit source allowlist. Index the manual and policy wording by numbered headings and useful named subsections, such as the manual's interpretation notes. Provide a contents listing, deterministic text search returning section IDs/titles/snippets, and a tool that reads a selected section in full. Include document source, version and section title in responses. This small corpus needs no embeddings, vector database or additional retrieval framework.

Remove the whole-wording tool from the claim agent's toolset. The helper reads relevant wording and manual sections, including the handling procedure, document requirements, history checks, payment calculation and applicable interpretation rules. It reads the entire claim and supporting documents through the existing claim API. No agent reads business files or SQLite directly, and no tool accepts arbitrary filesystem paths.

Persist each section read against its claim and attempt, including source, version, section ID, title, exact returned text and timestamp. Claim activity records searches, reads, retries and outcomes. Keep full evidence separately from the existing short activity-detail field so long sections are not silently truncated. Search snippets alone do not count as a section read. The claim page displays the sections actually returned to the helper.

Retain the decision's policy sections and add manual references and the manual/wording versions used. The backend supplies versions and validates references against known sections read during the active attempt. Existing decisions remain readable with their original references and no invented manual provenance.

Revise `backend/instructions.md` for claim work and add queue instructions. The wording remains the contract; the manual supplies handling and interpretation rules. Cover the manual's third-claim review, wild animals, missing documents, conflicting evidence and meals without receipts, alongside the existing calculation and payment rules. A referral still has a zero payable amount; explain any apparently payable amount and who must review it in the calculation/reason. Do not add messaging or document-request delivery workflows.

Serve both roles' effective instructions through the backend and show them read only in the dashboard. Compose any middleware guidance visibly so the page reflects the instructions actually used. Existing databases must receive the new manual, section index and current instructions through a targeted migration/update; ordinary startup must preserve stored claims, decisions, activity, sign-offs and payments.

### Bounded recovery and financial safeguards

Use bounded retries for transient provider timeouts, connection errors, rate limits and selected server failures. Permit at most two transport retries after the initial call, with short capped backoff, and coordinate SDK retry settings to avoid multiplying attempts. Retry read tools safely. Return validation errors and business conflicts to the agent for correction; blocked or duplicate payments are not transient errors.

After transport retries are exhausted, a helper exception, missing saved decision, call-limit breach or timeout ends that assessment attempt. Clear its active reservation, record the failure and put the undecided claim back in Waiting. The queue agent can choose when to retry it during the same run while continuing other claims. Two failed assessment attempts place the undecided claim in an explicit Handler review workflow state and remove it from automatic eligibility.

Persist the failed-assessment count across process restarts so repeated starts cannot evade the two-failure rule. Start existing claims at zero; do not reinterpret old error logs as new failures. Interrupted active undecided attempts count once when recovered. Resetting the database clears this state along with run history and evidence. A manual retry/review-resolution workflow is outside this brief.

Model/request timeouts, per-attempt call limits, an overall attempt deadline and a coordinator budget bound hangs and loops. Scale the coordinator budget with the workload size. Fence expired attempts before retrying: stale helper writes, including agent payment requests, must be rejected by their attempt/thread ID. Keep handler payment actions available through the existing financial checks. Tests must prove a late response cannot overwrite a decision, record a payment or finish a newer attempt.

If a failure happens after a decision or payment commits, preserve it and do not reassess the claim. Read back stored state after an ambiguous write response. An unpaid committed approval may retry only its exact eligible payment under the same safeguards, or visibly require handler attention if recovery cannot finish. Never replay a decision or payment blindly. A queue-level error or shutdown reconciles active attempts and run/to-do state before releasing ownership; startup recovers abandoned work without resuming model conversations.

Implement the API contracts below. The backend remains responsible for the exact approved amount, duplicate-payment prevention and sign-off above $1,000; neither agent receives a sign-off tool.

## API contracts

These are the proposed interfaces to implement and test. Paths include the `/api` prefix. The startup worker and standalone CLI share these interfaces; no HTTP operation below invokes a model. The queue's delegation tool reserves an attempt through HTTP, invokes the claim helper in the worker process, and finishes that attempt through HTTP in a `finally` block. The API stays responsive while helpers work.

### Conventions and ownership

- Every operation has the `operation_id` listed below, a one-line docstring and a typed response model. Every request field listed is required. New and extended request models reject unknown fields with 422. There are no optional query parameters or request fields; run scopes use a discriminated union with all fields required for the selected mode.
- IDs for runs and threads are UUID strings generated by the runtime, outside the model's tool arguments. Other record IDs are opaque strings. Timestamps are server-generated UTC ISO 8601 strings. USD amounts cross the JSON boundary as decimal strings, retaining the existing precision and amount-validation rules.
- The backend calculates eligibility, remaining capacity, attempt counts, versions, to-do handoff reasons and summary counts. Clients cannot submit those values. A run cap counts distinct admitted claims; transport retries and a second assessment of the same claim do not consume another slot.
- Business errors use `{"detail":"Readable reason."}`. Validation errors retain FastAPI's 422 `detail` array. Unexpected server/provider failures must not expose provider bodies or secrets.
- The existing local pilot has no authentication. Runtime IDs associate and validate state; they are not credentials. Agent toolsets expose only the operations assigned to their role. Handler sign-off/payment operations stay out of both agent toolsets.
- Register `/agent-runs/current` before `/agent-runs/{run_id}` so `current` is not parsed as a UUID. The runtime validates model configuration before the first assessment reservation; an empty run finishes without requiring provider configuration. Invalid configuration finishes the registered run as failed without reserving a claim or calling a model. Database ownership still covers the full backend/terminal process lifetime.

### New run and attempt routes

| Method and path | `operation_id`; caller | Required request | Success response |
| --- | --- | --- | --- |
| `POST /api/agent-runs` | `create_agent_run`; startup/CLI runtime | `run_id`, `scope`, as defined below | 201 `Run`; exact replay returns 200 with the existing `Run` |
| `GET /api/agent-runs/current` | `get_current_agent_run`; dashboard | No body | 200 `{"run": Run}` for the active run, otherwise the latest run; `{"run":null}` before the first run |
| `GET /api/agent-runs/{run_id}` | `get_agent_run`; runtime, queue tools, dashboard | Path `run_id`; no body | 200 `Run`, including its to-dos and attempts |
| `PUT /api/agent-runs/{run_id}/todos` | `update_run_todos`; queue's `write_todos` tool | `{"items":[TodoInput]}` | 200 `{"run_id":"...","items":[Todo],"updated_at":"..."}` |
| `POST /api/agent-runs/{run_id}/attempts` | `start_claim_attempt`; delegation runtime | `{"claim_id":"TEST-001","thread_id":"<UUID>"}` | 201 `Attempt`; exact replay returns 200 with the same attempt |
| `POST /api/agent-runs/{run_id}/attempts/{thread_id}/finish` | `finish_claim_attempt`; delegation runtime | `{"error":""}` on normal return, or a sanitized error of 1-2,000 characters | 200 finalized `Attempt`, including the resulting claim state |
| `GET /api/agent-runs/{run_id}/completion-check` | `check_run_completion`; queue tool and completion guard | Path `run_id`; no body | 200 `CompletionCheck` |
| `POST /api/agent-runs/{run_id}/finish` | `finish_agent_run`; startup/CLI runtime | `{"status":"completed","detail":""}`; status also permits `failed` or `interrupted`, which require a sanitized detail of 1-2,000 characters | 200 finalized `Run` with its stored summary |

`POST /agent-runs` accepts exactly one of these scopes:

```json
{"run_id":"11111111-1111-4111-8111-111111111111","scope":{"mode":"all"}}
```

```json
{"run_id":"11111111-1111-4111-8111-111111111111","scope":{"mode":"limited","max_claims":5}}
```

```json
{"run_id":"11111111-1111-4111-8111-111111111111","scope":{"mode":"claim","claim_id":"TEST-001"}}
```

`limited.max_claims` must be an integer greater than zero; zero, negative numbers, booleans and fractional values are invalid. `all` snapshots every eligible waiting claim and sets `claim_limit` to the snapshot size. `limited` snapshots the same candidates and sets `claim_limit` to the smaller of the cap and snapshot size. `claim` snapshots only the named eligible claim and sets the limit to one. An empty queue is valid for `all`/`limited`: create the run with limit zero, then finish it without invoking a model. Creating a run records state and never starts an agent or clears existing work.

Repeat creation with the same run ID and identical scope returns the original run without resnapshotting. A different scope for that ID, or creation while another run is active, returns 409. A missing named claim returns 404; an existing ineligible named claim returns 409. The runtime uses `limited` for a configured CLI/environment cap, `claim` for `--claim-id`, and `all` when no cap is configured.

### Run, to-do, attempt and completion response shapes

`Run` has all these fields:

| Field | Type and meaning |
| --- | --- |
| `run_id`, `scope` | UUID string and the exact accepted scope object |
| `status` | `running`, `completed`, `failed` or `interrupted` |
| `claim_limit` | Effective nonnegative limit calculated at creation |
| `snapshot_claim_ids`, `admitted_claim_ids` | String arrays; snapshot in persisted queue order, admissions in the agent's chosen order |
| `capacity_remaining` | `claim_limit - number of admitted distinct claims` |
| `started_at`, `finished_at` | UTC strings; `finished_at` is null while running |
| `todos`, `attempts` | Ordered arrays of `Todo` and `Attempt`; attempts in start order |
| `counts` | `Counts`, calculated from stored records during execution and frozen at finish |
| `summary` | Null while running; then `{"text":"...","detail":"...","counts":Counts}` with backend-generated text/counts and the supplied finish detail |

`Counts` contains only nonnegative integers: `worked` (distinct admitted claims), `assessment_attempts` (reservations), `failed_assessments` (finalized failed attempts), `approved`, `declined`, `referred`, `simulated_payments`, `awaiting_sign_off`, `handler_review`, `unpaid_approvals` and `waiting_remaining`. Outcome/payment counts cover admitted claims only. `unpaid_approvals` excludes approvals still awaiting sign-off. `waiting_remaining` counts all Waiting claims in the database, including cap-excluded claims and later arrivals. Transport retries do not increase assessment counts. The claim's own `failed_attempts` counter increases only for an assessment that finishes without a decision.

A newly created capped run against six synthetic claims returns this complete example:

```json
{
  "run_id":"11111111-1111-4111-8111-111111111111",
  "scope":{"mode":"limited","max_claims":5},
  "status":"running",
  "claim_limit":5,
  "snapshot_claim_ids":["TEST-001","TEST-002","TEST-003","TEST-004","TEST-005","TEST-006"],
  "admitted_claim_ids":[],
  "capacity_remaining":5,
  "started_at":"2026-10-26T00:00:00Z",
  "finished_at":null,
  "todos":[],
  "attempts":[],
  "counts":{"worked":0,"assessment_attempts":0,"failed_assessments":0,"approved":0,"declined":0,"referred":0,"simulated_payments":0,"awaiting_sign_off":0,"handler_review":0,"unpaid_approvals":0,"waiting_remaining":6},
  "summary":null
}
```

`TodoInput` contains `id` (nonempty stable string, at most 100 characters), `claim_id`, `text` (1-300 characters) and `status` (`to_do`, `in_progress`, `done` or `left_for_handler`). `Todo` returns the same fields plus `handoff_reason`, which is null or one of `referral`, `sign_off`, `technical_failure`, `unpaid_approval`. Array order is display order; no separate position field is accepted. Example write:

```json
{"items":[{"id":"claim:TEST-001","claim_id":"TEST-001","text":"Assess TEST-001","status":"to_do"}]}
```

There is at most one item per claim and at most `claim_limit` items. Every claim must belong to the snapshot. Duplicate item/claim IDs and invalid text/status fields return 422; an unknown claim returns 404; a claim outside the snapshot returns 409. Too many items returns 409 with `To-do list exceeds the run claim limit.` Unadmitted items may be replaced or reordered; an admitted claim's item cannot be removed or assigned a different ID/claim. Admission requires an existing To do item. Lifecycle operations update its status atomically, and subsequent agent edits must agree with that stored state:

| Stored claim/attempt condition | Required to-do status; reason |
| --- | --- |
| Unadmitted claim, or first failed undecided attempt returned to Waiting | `to_do`; null |
| Active attempt, including the interval between saving a decision and finishing payment work | `in_progress`; null |
| Finished decline, or finished approval with payment recorded | `done`; null |
| Finished referral | `left_for_handler`; `referral` |
| Finished approval awaiting sign-off | `left_for_handler`; `sign_off` |
| Two undecided failed assessments | `left_for_handler`; `technical_failure` |
| Finished approval with payment eligible but still missing after recovery | `left_for_handler`; `unpaid_approval` |

`Attempt` contains `run_id`, `claim_id`, `thread_id`, `attempt_number` (per claim, including earlier harness runs), `status` (`active`, `completed` or `failed`), `started_at`, `deadline_at`, `finished_at`, `error`, `claim_status`, `failed_attempts`, `outcome`, `payout` and `payment_status`. `finished_at`/`error` are null until applicable; `outcome`/`payout` are null until a decision exists. `payment_status` retains `paid`, `eligible`, `blocked` or `unavailable`. Final attempt responses are snapshots; a later retry does not rewrite them.

Admission atomically checks running scope, an unused or matching thread ID, available capacity for a new claim, no active assessment, no decision, fewer than two prior undecided failures and the required to-do item. After schema/resource validation, check exact replays first, then closed-run/thread conflicts, scope membership, claim eligibility, another active claim, capacity and the to-do item. It reserves only the requested claim and returns a server deadline. The delegation tool passes only the claim ID to the model; it supplies the run/thread IDs itself. Replaying a reservation with the same run/claim/thread returns its existing attempt, consumes no slot, and never invokes a helper through the API. Reusing a thread for another claim/run is a conflict. The wrapper reconciles a lost reservation response before invoking the helper once for that thread.

Finishing an attempt derives its outcome from saved claim records. An empty `error` with no saved decision becomes a failed attempt with `No decision was recorded.` An eligible unpaid approval also fails with `Approved payment was not recorded.` A first undecided failure returns to Waiting; the second changes the claim to `handler_review`. Existing decisions/payments survive errors. An exact repeat finish returns the recorded result without incrementing a counter; a conflicting finish returns 409. An expired/superseded thread cannot mutate a newer attempt. Closed-attempt replay only returns its saved result.

`CompletionCheck` contains `run_id`, `complete` (boolean), `capacity_remaining`, `missing_claim_ids`, `retryable_claim_ids`, `active_claim_ids`, `unresolved_todo_ids` and `counts`. The ID fields are arrays, empty when no matching work remains. `missing_claim_ids` lists unadmitted eligible snapshot claims only while capacity remains; it is empty after the cap is used. `retryable_claim_ids` lists admitted Waiting claims with one failed assessment. `unresolved_todo_ids` identifies admitted items inconsistent with their stored outcome. `complete` is true only when all four work arrays are empty. Candidate lists use persisted order for repeatable responses; the agent still chooses which candidate to work next.

Finishing with `completed` runs this check inside the same transaction and rejects unfinished work with 409. Finishing with `failed` or `interrupted` invalidates any active attempt, applies undecided-failure recovery once, reconciles to-dos and stores an honest partial summary. No to-do remains In progress afterward. Exact finish replays return the same final run; a conflicting final status/detail returns 409. Completed runs are handover snapshots: later handler sign-off/payment updates the claim, while run counts, to-dos and summary retain their completion-time values. The dashboard labels finished summaries accordingly. Completion checks on a closed run return its stored final check.

Before a new startup run is registered, recovery closes any abandoned `running` run as `interrupted` with detail `Backend restarted during this run.` The startup lifecycle calls the same finish service before HTTP readiness, applies each interrupted-attempt failure once and then snapshots the recovered queue. It does not leave an old running record that would block every subsequent `POST /agent-runs`. Legacy pilot activity is retained without inventing historical harness attempts.

### New reference, evidence and agent-payment routes

| Method and path | `operation_id`; caller | Required request | Success response |
| --- | --- | --- | --- |
| `GET /api/reference-documents/{source}/sections` | `list_reference_sections`; claim tool | Path `source`; no body | 200 `{"source":"...","version":"...","sections":[{"section_id":"...","title":"...","parent_id":null}]}` |
| `POST /api/claims/{claim_id}/reference-search` | `search_claim_references`; claim tool | `{"thread_id":"<UUID>","source":"claims_manual","query":"wild animals"}` | 200 `{"claim_id":"...","thread_id":"...","source":"...","version":"...","query":"...","matches":[{"section_id":"7.wild-animals","title":"Wild animals","snippet":"..."}]}` |
| `POST /api/claims/{claim_id}/reference-reads` | `read_claim_reference`; claim tool | `{"thread_id":"<UUID>","source":"claims_manual","section_id":"7.wild-animals"}` | 201 `Evidence` on first read; 200 with the same evidence on a repeated read in that attempt |
| `GET /api/claims/{claim_id}/evidence` | `list_claim_evidence`; dashboard | Path `claim_id`; no body | 200 `{"claim_id":"...","reads":[Evidence]}` in first-read order; empty array before any read |
| `POST /api/claims/{claim_id}/agent-pay` | `pay_claim_as_agent`; claim tool | `{"thread_id":"<UUID>","amount":"420.00"}` | 201 with the existing payment shape: `{"amount":"420.00","created_at":"...","simulated":true}` |

`source` is exactly `policy_wording` or `claims_manual`. Section IDs use the document's numbered section, such as `3` or `9`, and a normalized suffix for named subsections, such as `7.wild-animals`. The contents response supplies all IDs; clients do not construct filesystem paths. A subsection's `parent_id` is its numbered parent; top-level sections have null parents. Versions come from the indexed backend document (`2026.1` for the current wording, `2026.2` for the current manual).

Search requires a nonblank query of at most 200 characters and an active, unexpired thread for the named claim. Return at most ten matches, ranked by matched query terms with document order breaking ties. Snippets contain at most 240 characters and never stand in for an audited section read. A valid search with no matches returns 200 with `matches:[]`; the tool directs the helper to the contents listing. The backend records the query and search result IDs in claim activity.

`Evidence` has `evidence_id` (server UUID), `run_id`, `claim_id`, `thread_id`, `source`, `version`, `section_id`, `title`, `text` (the exact full section) and `read_at` (UTC). The read request accepts no text/version from the client. Read and audit persistence happen together, with a uniqueness key on claim/thread/source/version/section. Repeating the read returns the same evidence ID/text/first-read timestamp and records another read activity event. Searches do not create evidence. Reading an unknown section returns 404; an inactive or wrong-claim thread returns 409.

Agent payment checks the active thread/deadline and all existing financial rules in one transaction. A successful payment must equal the approved amount; above $1,000 it still requires stored handler sign-off. A duplicate payment returns 409 with `Payment has already been recorded.` A stale helper cannot use this route after its attempt closes. The agent's payment tool switches to this route; the handler payment route continues to accept its existing amount-only body. These are separate callers of the same financial service, with no duplicated payment logic.

### Changes to existing routes and schemas

| Existing method and path | Concrete change |
| --- | --- |
| `GET /api/claims` | Add `failed_attempts` (integer 0-2) and `review_reason` (null, `two_failed_assessments` or `payment_failed`) to each row. Extend `status` with `handler_review`. Keep existing fields and persisted display order. |
| `GET /api/claims/{claim_id}` | Add `run_id` (latest harness run UUID or null), `failed_attempts` and `review_reason`; extend the decision response as below. Keep full claim context and existing financial records. Full section text is obtained from the separate evidence route. |
| `POST /api/claims/{claim_id}/decision` | Keep `thread_id`, `outcome`, `payout`, `calculation`, `explanation` and `policy_sections`; add required `manual_sections`, a nonempty array of known manual section IDs. Validate that each cited wording/manual section was read by this active attempt. Return 201 with the saved decision and backend-supplied document versions. |
| `GET /api/agent-instructions` | Return `{"text":"<effective claim instructions>","queue_text":"<effective queue instructions>"}`. Keep `text` as the claim prompt so the existing reader continues to work until the dashboard is updated. No edit operation is added. |
| `POST /api/claims/{claim_id}/events` | Keep its existing required body. Extend active-thread checks to enforce the attempt deadline and owning run's running state. A stale helper event returns 409. |

`review_reason` is `two_failed_assessments` for an undecided claim in `handler_review`. It is `payment_failed` when a committed approval remains eligible but unpaid after its attempt finishes unsuccessfully; that claim keeps its approval and normal decided state. Successful handler payment clears `payment_failed`. Other claims return null. Legacy claims have `run_id:null`, `failed_attempts:0` and `review_reason:null` until a harness attempt associates them with a run.

The extended decision response retains all existing fields and adds `manual_sections`, `policy_wording_version` and `claims_manual_version`. Newly assessed decisions contain nonempty manual references and non-null versions derived from the actual section evidence; client-supplied version fields are rejected. Legacy decisions serialize with `manual_sections:[]` and both version fields null unless provenance was already stored; migration does not invent a past read. The decision's `thread_id` and `created_at` remain server-validated/stored as today.

For example, after this thread reads wording sections `3` and `10` and manual section `6`, it can submit:

```json
{
  "thread_id":"22222222-2222-4222-8222-222222222222",
  "outcome":"approve",
  "payout":"420.00",
  "calculation":"Eligible expenses of $420.00; no deductible applies.",
  "explanation":"The documented delay meets the plan's waiting period. The supported expenses are payable under section 3.",
  "policy_sections":["3","10"],
  "manual_sections":["6"]
}
```

The response contains those fields plus `created_at`, `policy_wording_version:"2026.1"` and `claims_manual_version:"2026.2"`. Missing `manual_sections` is 422; an existing but unread cited section is 409; an unknown well-formed section ID is 404. Repeat decision submission remains 409; the tool reads the stored decision after an ambiguous response instead of submitting a second decision. Likewise, a lost payment response is reconciled by reading the claim before any further payment request.

`GET /api/health`, `/api/company`, `/api/handlers`, `/api/policy-wording`, `GET /api/claims/{claim_id}/events`, and the handler sign-off/payment routes keep their current request/response contracts and operation IDs. The whole-wording route remains available for existing clients but is removed from the claim agent's toolset. No reset HTTP endpoint, dashboard run-start endpoint or handler review-resolution endpoint is added. The run-creation route above only registers the runtime's work; it does not launch an assessment.

### Error cases and acceptance sequence

| Condition | HTTP status and business-error detail |
| --- | --- |
| Unknown run, claim, attempt or reference section | 404; `Run not found.`, `Claim not found.`, `Claim attempt not found.` or `Reference section not found.` |
| Missing fields, extra request keys, invalid scope/source/status, invalid UUID/cap/amount | 422; FastAPI validation `detail` array identifying the field |
| New run while one is active | 409; `Another agent run is active.` |
| Same run ID with a different scope | 409; `Run ID is already used with a different scope.` |
| New claim after the cap is used | 409; `Run claim limit reached.` |
| Claim outside this snapshot | 409; `Claim is outside this run's scope.` |
| Decided/active/handler-review claim requested as new work | 409; `Claim is not eligible for assessment.` |
| A different claim is already being assessed | 409; `Another claim is already running.` |
| Wrong, expired or superseded thread on a claim write | 409; `Claim attempt is no longer active.` |
| Invalid to-do transition, missing admitted item or changed stable identity | 409; `To-do list does not match stored claim progress.` |
| Citing a known section not read during this attempt | 409; `Decision references must be read by the active attempt.` |
| Completing a run with missed/retryable/active work | 409; `Run has unfinished work.`; caller reads `completion-check` for the IDs |
| Mutation of a closed run, other than an exact create/reservation/finish replay | 409; `Agent run is already finished.` |
| Conflicting reuse of an attempt identity or finish payload | 409; `Attempt request conflicts with the recorded attempt.` |
| Conflicting run finish payload | 409; `Run finish conflicts with the recorded result.` |

Retain the existing payment error details for wrong amounts, absent sign-off and duplicates. These examples are contract tests for future implementation, not calls to make against the student's working database:

1. Seed a temporary database with six synthetic Waiting claims. `GET /api/agent-runs/current` returns 200 `{"run":null}`. Create a limited run with cap five; expect 201, limit/capacity five and all six snapshot IDs. Repeat creation; expect 200 with the same snapshot and no new run.
2. Write five claim-linked to-dos with `PUT .../todos`; expect 200. Reserve TEST-001 with `POST .../attempts`; expect 201, a new deadline, its item In progress and capacity four. Repeat the same reservation; expect 200, one attempt and unchanged capacity. Reserve another claim while it is active; expect 409. A completion check reports TEST-001 active, and a `completed` run finish is rejected with 409.
3. On TEST-001's first attempt, search/read the required sections, recording the returned evidence ID/text. Re-read a section; expect 200 with the same evidence record. Attempt a decision citing an unread section; expect 409 and no decision. Finish with a simulated timeout before saving a decision; expect Waiting and one failed assessment, with TEST-001 in `retryable_claim_ids`. Reserve its retry with a new thread, re-read the cited sections in that thread and save an approval of $420.00; expect 201 with exact references/versions. An eligible agent payment returns 201; duplicate payment returns 409. Finish the retry successfully, with capacity unchanged by that retry. An early completion check now has no active claim but reports the unworked candidates in `missing_claim_ids`; completing the run still returns 409.
4. Continue with stub helpers: TEST-002 fails twice; TEST-003 approves $1,250 pending sign-off; TEST-004 declines; and TEST-005 refers. Verify each first undecided failure returns to Waiting and retry does not reduce capacity. The second TEST-002 failure yields `handler_review`, two failed attempts, no decision/payment and a Left for a handler item.
5. After all five are settled, request `completion-check`; expect `complete:true`, empty work arrays and capacity zero. Attempt TEST-006; expect 409 `Run claim limit reached.` and no new attempt.
6. Finish the run; expect 200 with `worked:5`, `assessment_attempts:7`, `failed_assessments:3`, `approved:2`, `declined:1`, `referred:1`, `simulated_payments:1`, `awaiting_sign_off:1`, `handler_review:1`, `unpaid_approvals:0` and `waiting_remaining:1`. TEST-006 remains Waiting. The run is completed with a technical handoff, so the CLI exits 1. Repeating the same finish leaves all counts unchanged.
7. Send stale decision/event/read/payment/finish requests after an attempt expires or is superseded; expect 409 except exact closed-attempt finish replays, which return the saved result without mutation. Verify a new thread's records do not change. Sign off/pay TEST-003 through the handler routes; its claim updates, while the finished run's handover summary stays unchanged.

Add these contracts to OpenAPI during implementation and cover them with temporary-database HTTP tests using stub models. No harness endpoints exist yet; this section defines what the implementation must deliver.

## Phase 1: Store runs, attempts, plans and section evidence

Extend `backend/storage.py`, lifecycle services, models and routes with the shared run model and atomic scoped admission. Add run/to-do storage, failed-attempt counters, Handler review, reference sections and evidence. Add targeted migrations for an existing database. Build this first so later tools have a tested source of truth for scope, progress and recovery.

Success criteria:

- [x] Temporary-database tests migrate an existing pilot database without changing saved decisions, exact amounts, queue positions, sign-offs, payments or activity. Repeated initialization is idempotent and installs the manual and current instructions without reseeding claims.
- [x] Fresh initialization and `uv run -m backend.reset_db` on a temporary path load the permitted sources and clear run/attempt/to-do/evidence state. File-access tests prove evaluation and security files remain unopened.
- [x] Synthetic API calls admit requested claims in a non-source order, reject overlapping/out-of-scope/decided claims with clear 409 responses, and reject a sixth distinct claim under a cap of five. Retrying an admitted claim consumes no new slot.
- [x] A valid to-do update is stored and returned in order with all four statuses. Unknown IDs, invalid statuses, hiding unfinished admitted work and falsely completing an undecided claim are rejected.
- [x] Attempt completion is idempotent. Two undecided failures, including failures separated by a restart, yield Handler review with no decision/payment; decided claims are preserved. Recovery records an interrupted attempt only once.
- [x] Restart recovery closes an abandoned running run before registering a new one, preserves its partial handover record and returns eligible first-failure claims to the new snapshot. Repeating recovery changes neither failed-attempt counts nor the prior run's final record.
- [x] Contract tests in `backend/tests/test_harness_api.py` exercise all eight run/attempt routes in the API table: exact methods, paths, operation IDs, required inputs, response shapes and 200/201/404/409/422 cases. They cover all three scope variants, empty runs, exact replays, conflicting IDs/finishes, closed-run writes and `/current` route precedence. OpenAPI marks every variant's fields required and includes the documented response models.
- [x] `GET /api/agent-runs/current` returns `{"run":null}` before a run and the latest stored run afterward. Run reads include exact to-do/attempt/count fields; to-do writes cannot supply handoff reasons or replace an admitted item's identity. A completed finish is rejected while the completion check reports unfinished work.
- [x] `npm run check:python` passes with the existing coverage threshold and temporary storage only.

## Phase 2: Give claim helpers section tools and manual-aware instructions

Add a small reference-document service and extend `agent/tools.py` with contents, search and claim-bound section reads. Update decision provenance and instructions. Preserve the claim entry point and add the shared default-model change. This makes a single isolated helper correct and observable before introducing queue orchestration.

Success criteria:

- [x] Reference tests retrieve wording and manual sections, including named interpretation notes; responses contain stable IDs, source, version and complete text. Unknown sections and arbitrary source/path requests fail clearly. Empty searches guide the agent to the contents listing.
- [x] Stub-driven claim runs use real local HTTP calls to read claim context and selected sections, then record a decision. No full manual or full wording is injected into the prompt or exposed through the helper's tools.
- [x] Evidence tests prove exact returned section text is stored against the correct claim/thread, survives refresh and can exceed the activity-detail limit. Searches and reads are distinguishable; invalid or unread decision references are rejected.
- [x] Synthetic fixtures exercise the workflow for repeat-claim referral, wild-animal interpretation and meals without receipts. Instructions and cited evidence follow the source manual, and tests make no claim to measure live reasoning accuracy.
- [x] Instruction API tests return the exact queue and claim prompts used, including middleware guidance. Existing decisions render without new provenance fields being fabricated.
- [x] `backend/tests/test_references.py` verifies the five reference/evidence/agent-payment routes against their API table, including complete section text, empty matches, repeated reads, wrong/expired threads and exact payment results. Decision/instruction tests verify required `manual_sections`, rejected client-supplied versions, the preserved `text` field, added `queue_text`, and legacy decision null/empty provenance.
- [x] Model-factory tests select `deepseek/deepseek-v4.1-flash` with low reasoning effort both by default and when named explicitly, preserve other `MODEL_NAME` overrides, and retain Ollama selection, context settings and no-key local operation. Invalid configuration makes no model calls or reservations.
- [x] `npm run check:python` passes using injected stub models only.

## Phase 3: Replace the fixed queue loop with the planning agent

Add queue tools and an entry point, then adapt `backend/worker.py`, `backend/batch.py` and `agent/__main__.py` to use it. Keep ownership/readiness/shutdown infrastructure; move ordering and delegation decisions into the queue agent. Persist plan updates during execution and reconcile completion against backend state.

Success criteria:

- [ ] A scripted queue model lists eligible work, writes its plan, selects claims in its own non-source order, delegates them and updates the plan before producing a short summary. Startup and terminal modes exercise the same coordinator through HTTP tools.
- [ ] Agent integration tests capture the actual HTTP sequence: register run -> read queue/completion state -> write to-dos -> reserve requested claim -> invoke isolated helper -> finish attempt -> check completion -> finish run. Creating/reserving through the API alone makes zero model calls; the delegation wrapper invokes a helper once after reconciling a lost reservation response.
- [ ] Recorded helper inputs prove each claim and retry starts with a clean message history and new thread. The queue receives only concise persisted outcomes/errors, while each claim retains its full activity and evidence. Parallel delegation requests cannot create overlapping assessments.
- [ ] CLI tests cover uncapped, `--max-claims 5`, `--claim-id`, CLI/environment precedence, invalid/zero/negative caps, incompatible flags and unknown/ineligible named claims. Startup honors `UNDERCOVER_MAX_CLAIMS` before any assessment starts.
- [ ] With at least six synthetic waiting claims, a capped run admits exactly five distinct claims; attempts on the remaining claims are rejected and those claims remain Waiting. Retries do not change that distinct count. Fewer available claims complete normally.
- [ ] A scripted agent deliberately overlooks a claim and tries to finish early. The completion guard identifies the omission and continues work. Cap-excluded claims are correctly reported as waiting, and unresolved admitted work prevents a success report.
- [ ] An empty queue finishes with zero totals and no model calls. Later arrivals remain waiting; refreshes and navigation do not start runs. A second run skips decided, awaiting-sign-off and Handler review claims.
- [ ] Terminal output and stored summaries match database counts, distinguish distinct claims from attempts, preserve outcome/payment detail and follow the documented exit codes. `npm run check:python` passes.

## Phase 4: Recover automatically without duplicating decisions or payments

Add the retry, timeout, error-reporting and completion middleware around queue and helper execution. Keep recovery in shared lifecycle services so model behavior cannot bypass limits. Extend existing ownership, restart, payment and interruption tests instead of replacing their safeguards.

Success criteria:

- [ ] Stub providers/read tools fail transiently and then succeed within the configured transport retry limit. Tests use fake delays/clocks and prove backoff is bounded, SDK retries do not multiply the budget, and validation/payment conflicts are not automatically replayed.
- [ ] A helper failure before a decision returns its claim to Waiting, records an observable retry and allows another claim to finish. The queue retries the failed claim successfully in the same run without exceeding its cap.
- [ ] A second failed assessment leaves the claim for a handler with both failures visible, and processing continues. Subsequent startup/terminal runs cannot silently attempt it a third time. No technical failure is converted into an insurance referral.
- [ ] Missing-decision replies, model/tool limits, timeout, exhausted provider retries and coordinator failures all leave explicit run/claim states. An early-final guard cannot loop indefinitely, and incomplete runs retain accurate partial summaries.
- [ ] Deadline/interruption tests fence the old thread before another attempt starts. Late decision, evidence, payment and finish requests from that thread cannot affect newer work. Shutdown and restart leave no abandoned In progress claim or active to-do spinner.
- [ ] API recovery tests repeat reservation, section read, attempt finish and run finish after simulated lost HTTP responses and assert the documented replay response and unchanged counts. Conflicting payloads return 409. Exact closed-attempt finish replay returns its saved result; all other stale helper mutations are rejected.
- [ ] Simulated lost responses after decision/payment commits are reconciled from storage. The decision is recorded once; each exact approved payment is recorded at most once. Saved outcomes survive subsequent exceptions, restarts and retries.
- [ ] Regression tests retain the $1,000 boundary, blocked payment above it, authorized handler sign-off, wrong-amount/duplicate-payment rejection and continued queue work while sign-off is pending. `npm run check:python` passes without relaxed rules.

## Phase 5: Show the run plan, evidence and both instruction sets

Extend the queue page with a compact current/latest-run panel and to-do list. Extend claim details with the sections read and a clear Handler review state. Update the read-only instructions page for both roles. Reuse `useResource`, existing components and the brand's fonts, colors, surfaces and USD formatting.

Success criteria:

- [ ] Component tests show a to-do list filling in and changing through all four statuses while claims progress. Run identity, cap, partial/final summary and the distinction between sign-off, referral and technical handoff are visible.
- [ ] The queue shows a useful empty state before a run, active progress during it and the latest result after it ends. Refresh and navigation restore stored progress; polling cleans up on unmount and recovers visibly from request failures.
- [ ] Dashboard API fixtures match `Run`, `Todo`, `Counts`, `Evidence` and the extended claim/instruction schemas exactly. Handler review appears as the fifth queue state. Finished run summaries are labeled as completion-time snapshots and stay unchanged when a handler later signs off/pays a claim.
- [ ] Claim details show searches/reads/retries in activity and readable full evidence with source, version, title and attempt. Policy and manual references in decisions are distinct, and older decisions remain readable.
- [ ] Instructions display the exact backend queue and claim prompts read only. Both roles' instructions are accessible by keyboard and require no editing controls.
- [ ] Existing claim details, automatic refresh, handler sign-off and simulated-payment controls remain usable during and after the queue run. New waiting/review messages describe actual retry and handoff behavior.
- [ ] In Playwright MCP, inspect the queue plan, one retry, a handler handoff, section reads and instructions at desktop and 390-pixel widths in light/dark modes. There is no horizontal overflow, inaccessible status-only color, unexpected console error or browser-triggered assessment.
- [ ] `npm run check:ts` passes with strict TypeScript, no `any` and the existing Vitest coverage thresholds.

## Phase 6: Verify the complete harness and document the handover

Extend `backend/tests/serve.py`, CLI integration tests and Playwright coverage with scripted queue and claim models. Update `docs/SPEC.md`, `docs/TECH_STACK.md`, `docs/STATUS.md`, `.env.example` and the short README to describe the implemented harness. Remove the manual's exclusion from the specification and update the default model, run modes, caps, retry/handoff rules and read-only instructions.

Success criteria:

- [ ] Full-stack stub tests cover the stored/shown to-do list, cap, named claim, isolated helpers, failed-claim continuation, same-run retry, two-failure handoff, missed-claim check, section evidence and preserved financial safeguards. All test databases are temporary; no automated test calls a real model.
- [ ] Run the seven-step API acceptance sequence above with six synthetic claims and assert the exact final counts, status codes, remaining Waiting claim and preserved handover summary. The published OpenAPI schema matches the final route inventory and required request/response fields; every existing route is accounted for as extended or unchanged.
- [ ] CLI integration rebuilds a temporary database, processes a capped workload, verifies printed/stored results and leftovers, and proves a later run processes only eligible remaining work. Reset and ownership regressions still pass on Windows.
- [ ] `npm run check` exits 0 with Ruff, Pyright, pytest, Prettier, ESLint, TypeScript, Vitest and Playwright, retaining all existing thresholds and hooks. `node node_modules/vite/bin/vite.js build dashboard` succeeds.
- [ ] Complete the live acceptance trial below after reseeding its separate database. Five distinct claims are worked using the brief's default model, the other source claims remain Waiting, and the dashboard visibly updates the plan and claim evidence. Report any technical handoffs or incomplete work honestly and fix/reseed/recheck if needed.
- [ ] In Playwright MCP, verify live health returns `{"status":"ok"}`, observe the trial's to-do progress and sections read, inspect the final summary and both instruction sets, and verify refresh preserves results. This trial checks operation and observability; it does not score model accuracy against an answer key.
- [ ] Documentation matches the completed behavior, includes Windows commands for full/capped/named runs and reseeding, and explains cost limits, retries, Handler review and lack of resumable model execution. `docs/PLAN.md`, the student's `.env` and working database remain unchanged.
- [ ] Inspect the final diff for unrelated files, secrets, databases, notebooks, evaluation data and later-day features. Tick only verified criteria, commit each implementation phase with hooks enabled, and report final checks and remaining limitations. Never push or create AWS resources.

### Live acceptance trial

Use a separate ignored database so the student's saved claims are preserved. Stop any app using the chosen local HTTP address. In a dedicated PowerShell terminal, ensure `OPENROUTER_API_KEY` is available from the existing environment or `.env`, then run:

```powershell
$env:UNDERCOVER_DB_PATH = Join-Path (Get-Location) 'undercover-harness-trial.db'
$env:MODEL_PROVIDER = 'openrouter'
$env:MODEL_NAME = 'deepseek/deepseek-v4.1-flash'
$env:UNDERCOVER_MAX_CLAIMS = '5'
uv run -m backend.reset_db
npm run dev
```

The explicit model name selects the brief's model even if the student's `.env` overrides the normal default. Ensure that this model also receives low reasoning effort. Inspect the dashboard at `http://localhost:5173` while the capped startup run executes. Preserve the trial results for review and close the dedicated terminal afterward to discard its environment overrides.

For a live standalone CLI check, stop the trial app, reseed that same separate database again, then run `uv run -m agent --max-claims 5`. Every live run must begin with reseeding and stay capped at five; do not perform a live full-queue run or a separate uncapped named-claim experiment. Automated tests cover other modes with stubs.

## Implementation procedure

After this plan is reviewed and implementation is requested, build phases in order without stopping between them. Add focused tests, verify each phase's criteria, tick them here, update `docs/STATUS.md` and commit with the pre-commit hook enabled. Keep `docs/PLAN.md` unchanged. Use uv and Windows-compatible scripts, retain unrelated student work and never lower checks or bypass hooks. Run the full check suite before the final handover. Planning does not launch a model, reset a database or implement product changes.
