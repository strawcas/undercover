# Status

## Durable state

The durable-state implementation is built locally on the harness. `docs/PLAN_DURABLE.md` tracks verified criteria and the remaining live acceptance. The pilot and harness plans remain historical records.

The backend payout calculator uses exact Decimal/cents arithmetic and stored plan, policy, add-on and company limits. It covers quantities, item/cash/electronics caps, hotel rooms/nights, unreceipted meals, third-party payments before deductibles, benefit/trip-cost caps, rounding and the zero floor. It is read only; the backend still enforces every financial gate.

The queue remains on `create_agent`. Each claim uses a synchronous LangGraph with explicit reasoning, tools, structured decision, approval interrupt and payment nodes. Every approval reaches payment; the backend determines whether sign-off is required. Neither model has a payment or sign-off tool. Canonical instructions and exact versioned reference sections remain visible read only in the dashboard.

The agent service owns one sequential worker for CLI requests and sign-off notifications. A handler sign-off commits identity, amount and time before a bounded notification. The claim stays In progress until paid. The graph resumes its saved thread, rechecks backend sign-off and copies attribution into the payment. Failed delivery leaves recoverable saved state. Duplicate notifications and matching decision/payment replays do not duplicate financial records or lifecycle events.

## Run locally

Use Python 3.12, uv and Node 26 from the repo root:

```powershell
uv sync
npm install
npm run dev
```

The dashboard is at `http://localhost:5173`, backend/API docs at `http://localhost:8000/docs`, and agent docs at `http://127.0.0.1:8002/docs`. Backend `/api/health` still returns `{"status":"ok"}`. Development startup admits no claims. Refresh, navigation and polling are read only.

Configure the existing root `.env` using `.env.example`; terminal variables take precedence. Imports and tests never load that file. Development and terminal entry points load it explicitly.

| Setting | Behavior |
| --- | --- |
| `DATABASE_URL` | Supabase session pooler, synchronous psycopg, port 5432, `search_path=undercover`. Leave absent for SQLite. A configured database error never falls back. |
| `UNDERCOVER_DB_PATH` | SQLite file; defaults to root `undercover.db`. Local checkpoints use this same file. |
| `UNDERCOVER_BACKEND_URL` | Defaults to `http://localhost:8000`. Tools and service use this backend. |
| `UNDERCOVER_AGENT` | Defaults to `http://127.0.0.1:8002`. Sign-off notifications and CLI requests use this service. |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | Enable claim tracing when both are set. Missing keys disable export locally. |
| `LANGFUSE_BASE_URL` | Langfuse host; defaults to `https://cloud.langfuse.com`. |

Backend and service must select the same database. Health identity headers contain a safe hash, not credentials. CLI attachment requires matching local HTTP services; it rejects an unrelated service/database. Those checks are not authentication. Normal startup preserves saved records; switching to Supabase does not move historical SQLite work or overwrite its file.

## Model configuration

OpenRouter is the default provider and requires `OPENROUTER_API_KEY`. Its default model is `deepseek/deepseek-v4.1-flash` with low reasoning effort; selecting that exact name explicitly retains low effort. `MODEL_NAME=openrouter/free` selects the course's free option. Other model names are not forced to accept the default's reasoning setting.

For Ollama, set `MODEL_PROVIDER=ollama` and `MODEL_NAME` to a downloaded tool-calling model. `OLLAMA_BASE_URL` defaults to `http://localhost:11434` without `/v1`; `OLLAMA_NUM_CTX` defaults to 16384 and must be a positive integer. Ollama needs no OpenRouter key. There is no provider fallback.

The running agent service owns model configuration. Restart it after changing provider, model or database settings; changing only the attached CLI's environment does not reconfigure that service. Missing/invalid model settings fail before fresh claim admission. An empty queue and payment-only recovery need no model construction.

## Explicit commands and recovery

With matching services running, use another terminal:

```powershell
uv run -m agent
uv run -m agent --cap 5
uv run -m agent --claim-id CLM-1001
```

Without a cap, a new run snapshots eligible Waiting claims. `--cap N` limits distinct claims; retries use the same slot. `--max-claims N` remains an alias. `UNDERCOVER_MAX_CLAIMS` is ignored. Named mode selects one eligible claim and cannot be combined with a cap. Later arrivals and cap-excluded claims wait for another command.

The CLI attaches to existing services or starts temporary backend/agent child processes. It prints saved outcomes, explanations, references and backend-derived totals, then exits. An attached app stays available. Exit codes are 0 for completed work, including referrals and pending sign-offs; 1 for setup/incomplete work, technical handoffs or unresolved unpaid approvals; and 130 for Ctrl+C.

A stopped queue retains its original snapshot, admitted IDs, to-dos and cap. The next command continues that scope, reports continuation and does not start another fresh snapshot afterward. Default invocation continues any saved scope; an incompatible explicit cap or named claim is refused. Interrupted and signed-off claims continue before new admissions, using their existing slots. Unsigned held approvals remain paused so the queue can finish. A completed parent's summary and attempt views stay completion-time snapshots; live claim/execution records show later sign-off/payment.

Interruption preserves the attempt number, thread, evidence, checkpoints, failure count and remaining active-work budget. Genuine undecided errors create clean attempts with new threads. Two failed assessments require Handler review; restart does not grant a third. A saved decision is never reassessed. Historical decisions without checkpoints remain readable; eligible approvals enter payment-only continuation without inventing an earlier conversation.

Each assessment allows thirty durable model requests, including retries and structured-decision requests, eighty tool calls and ten minutes of active work. Human waiting and process downtime do not consume that allowance. Provider requests time out after 120 seconds; transient requests retry at most twice with 0.25/0.5-second backoff. Durable reservations prevent a crash from restoring spent calls. Queue middleware bounds history and calls; backend completion checks prevent missed work from being called complete.

Execution leases renew while work is active, and fresh execution IDs fence late writes even when a thread is unchanged. Paused queues require an explicit continuation before admitting or resuming undecided work. Already approved continuations remain valid after their parent queue ends. The service waits for cancelled helper threads before reusing or closing the shared saver.

Service startup first acquires exclusive ownership, then reconciles the previous owner's running queue and active claim executions through the existing backend APIs. Registration before the first checkpoint and active continuation after a completed parent are covered. Startup preserves the saved scope/attempt and admits no claims; the next explicit command or saved-sign-off notification continues the work.

## Storage, tracing and reset

SQLAlchemy Core provides the shared business query surface. Supabase business and checkpoint tables are confined to `undercover`. Backend pools allow at most four connections with no overflow; the agent's synchronous saver pool allows at most four, including service ownership. A separate setup/reset process also allows at most four. The CLI and dashboard open no database connections. Ownership uses SQLite file locks or Postgres advisory locks, and business transactions never span network/model calls or human waits.

Graph checkpoints use `durability="sync"` with synchronous Postgres/SQLite savers. Langfuse callbacks group all executions of a claim/thread in session `claim_id:thread_id`, with claim/run/attempt metadata. Setup/export/flush and activity/reporting are observational; errors produce safe diagnostic references without undoing a decision or payment. Mandatory evidence, financial and lifecycle writes still surface failures and reconcile ambiguous responses.

Stop backend and agent before reseeding:

```powershell
uv run -m backend.reset_db
```

Reset validates the permitted source allowlist first. SQLite replaces only the selected file and its included checkpoints. Postgres clears owned product/checkpoint data transactionally, preserving checkpointer migration metadata and other schemas. It restores the source queue and clears decisions, sign-offs, payments, activity, failures, runs, attempts, to-dos, evidence and applicable checkpoints. Active owners block reset. Normal startup never reseeds existing claims.

## Verification and review

On October 31, 2026, `npm run check` exited 0 with `.env` absent and inherited external-service variables removed. The original file and process settings were restored in `finally`. No Supabase, Langfuse or real-model access was needed.

| Check | Result |
| --- | --- |
| Ruff format/lint and Pyright | Passed; zero type errors/warnings |
| Python | 305 tests passed; 94.97% coverage |
| Prettier, ESLint and strict TypeScript | Passed |
| Component tests | 24 passed; 99.35% statement / 93.85% branch coverage |
| Playwright | Eight passed, using the isolated port-8003 receiver |

The service phase also committed with hooks enabled after 301 Python and 24 component tests passed. Final handover checks include the subsequent startup, connection and routing-identity fixes described below. The final commit runs the required hooks again.

Four real subprocess termination cases cover a saved early graph step, decision/payment committed before checkpoint, and graph completion before attempt finalization. They retain the same attempt/thread and original named scope, with one decision/payment and no new failures. Other tests cover the exact payout boundaries, thirty-call reservations, paused budgets, stale execution/coordinator fencing, matching/different financial replays, completed-parent continuation, saved caps/later arrivals, ownership/reset exclusion and primary/cleanup failures. Tests use stub models, temporary SQLite and no external tracing or database access.

The port-8003 E2E receiver verifies sign-off was saved before notification, exactly one notification, attribution after refresh, In progress UI and no competing manual payment action. Python tests verify actual graph resume/payment. Browser review at 1440 and 390 pixels in light/dark modes confirmed keyboard sign-off, readable USD values, activity, both read-only prompts, refreshed pending payment and no console errors before preview shutdown. Windows `uv sync --locked`, `npm install` and isolated three-service `npm run dev` startup passed, with no run admitted. Temporary services were stopped and the original environment restored.

Review traced admission through service/queue ownership, helper budgets and cancellation, graph routing, financial reconciliation, attempt/run finalization, notifications, dashboard polling/actions, migrations and reset. It retained the harness review's safe-diagnostic and observational-write protections. Cleanup injection covers primary plus cleanup errors, cleanup alone and interruption; public logs exclude private sentinels while retaining diagnostic locations. Coverage thresholds, hooks and existing regression assertions remain enabled. The finite-queue browser wait uses forty seconds inside its existing forty-five-second test budget; retries are scripted after another claim continues, and all full-snapshot/later-arrival assertions remain.

Final connection review moved matching financial replay responses outside their transactions. Each request now releases its transaction connection before reading the stored claim, avoiding nested connection checkout under a saturated bounded pool. A connection-nesting guard covers both decision and payment replay, and four concurrent matching payments still produce one financial event. Startup reconciliation tests cover orphan registration, reservation without an initial checkpoint, a completed-parent active continuation and ownership release after a recovery failure.

Database identity includes the pooler routing username inside its hash, so projects sharing a session-pooler host remain distinct. Password rotation and driver spelling do not change that identity. No username, password or URL is exposed in the health header.

## Pending live acceptance

No Supabase write or real-model trial has been performed for this durable build. Read-only inspection found no `undercover` tables in the connected project, and safe configuration inspection matched `DATABASE_URL` to project `tqjlbjkndxvmdxiqkloh`.

Automatic approval review rejected initialization/reseeding, including a second request limited to the verified project and the explicitly fictional course records. The rejected action would upload those records and create/reset the project's owned `undercover` data. Explicit approval for that destination and payload is still required. No remote records or resources were changed.

After approval, stop the app and agent, reseed the selected Supabase database, select the default OpenRouter model in the service terminal, then start `npm run dev`. In a matching second terminal run `uv run -m agent --cap 5`. Each independent live trial must start from a reseed and stay capped at five; do not reseed between killing that trial and continuing its saved scope.

Still unverified: live Supabase initialization/reseed and schema isolation; a default-model capped run; dashboard sign-off to Paid within five seconds while the worker is free; live kill/restart with the same attempt/thread; no more than twelve observed Postgres connections; and authenticated Langfuse session traces before/after sign-off. Pool settings and offline mocks do not substitute for those observations. No later-day features, evaluation data, AWS resources or course notebooks were changed. Existing notebook edits remain the student's work, and nothing has been pushed.
