# Status

The harness in `docs/HARNESS.md` is implemented with explicit command-only launch. Verification is tracked in `docs/PLAN_HARNESS.md`; `docs/PLAN.md` remains the completed pilot plan.

## What is built

One queue agent chooses the order within a finite snapshot, writes a claim-linked to-do list and delegates each claim to a fresh helper. Helpers read the complete claim and selected policy/manual sections through HTTP, record a decision and make an eligible simulated payment. The queue receives concise results; full activity and exact versioned section text remain on each claim. The dashboard shows the current/latest plan, all five claim states, evidence and both roles' read-only instructions.

FastAPI/SQLite exposes 25 typed API operations: the existing 12 plus eight run/attempt and five reference/evidence/agent-payment operations. Run creation records a scope and never launches a model. The runtime performs delegation. The backend enforces atomic reservations, distinct-claim caps, stable truthful to-dos, completion checks, stale-attempt fencing, exact money, duplicate prevention and handler sign-off above $1,000. Neither agent has a sign-off tool. Existing databases migrate without reseeding saved work; legacy decisions retain their original references without invented manual provenance.

The agent runs only from an explicit terminal command. Backend startup/restart initializes storage, recovers interrupted state and serves APIs without creating a worker, run or model call. Claims added after a run's snapshot, and cap-excluded claims, wait for another explicit command. Polling and refresh only read progress. A run's finished summary is a completion-time snapshot and does not change after later handler actions.

## Run locally

Use Python 3.12, uv and Node 26. From the repository root:

```powershell
uv sync
npm install
npm run dev
```

Open http://localhost:5173. The backend is at http://localhost:8000, API documentation at `/docs`, and health at `/api/health`. Configure the existing root `.env`; never commit it. Environment variables in the terminal take precedence. Normal startup preserves saved claims, decisions, sign-offs, payments, activity, evidence and failure counts.

## Model configuration

OpenRouter is the default provider. It requires `OPENROUTER_API_KEY` and uses `deepseek/deepseek-v4.1-flash` with low reasoning effort. Selecting that same model explicitly also uses low effort. `MODEL_NAME` overrides the model; other names are not forced to accept its reasoning setting.

```dotenv
MODEL_PROVIDER=openrouter
OPENROUTER_API_KEY=your-openrouter-key
# Optional override:
# MODEL_NAME=openrouter/free
```

For local Ollama, install and keep the Ollama server running, download a tool-calling model and configure its exact installed tag:

```powershell
ollama pull qwen3:8b
ollama list
Invoke-RestMethod http://localhost:11434/api/tags
```

```dotenv
MODEL_PROVIDER=ollama
MODEL_NAME=qwen3:8b
OLLAMA_BASE_URL=http://localhost:11434
# Optional positive context-token override; default 16384:
# OLLAMA_NUM_CTX=32768
```

Ollama uses its native API address without `/v1`, temperature zero and no OpenRouter key. Choose model/context sizes that fit local memory. A missing model tag, invalid provider/context setting or missing required key fails before any claim is reserved. An empty run needs no model configuration. Ollama connection, missing-model, tool-support and truncation failures produce actionable messages; there is no automatic provider fallback. Each explicit command reads its model settings. Database/backend address settings must match the running app; restart the app when changing those settings. Provider changes affect future attempts; saved decisions remain unchanged.

## Full, capped and named terminal runs

Leave `npm run dev` running to watch assessments in the dashboard. In another terminal with the same database/backend settings, run one of:

```powershell
uv run -m agent
uv run -m agent --cap 5
uv run -m agent --claim-id CLM-1001
```

The command uses the existing local backend if it serves the same configured database. When the app is stopped, it starts its own temporary backend. It works the registered scope, prints each attempt's saved outcome, payout, explanation, policy/manual references and payment/sign-off state, then prints backend-derived totals and exits. An attached app stays running. The queue agent chooses the order; database queue order remains stable for display.

Without flags, the command works the full eligible waiting snapshot. `--cap 5` admits up to five distinct claims and leaves the rest Waiting; `--max-claims` remains a compatibility alias. The old `UNDERCOVER_MAX_CLAIMS` environment setting is ignored. Named mode accepts only one existing eligible claim. Do not combine cap and named mode. Unknown, decided, active or Handler review claims are rejected without reassessment. Retries consume the same slot. Claims outside the snapshot cannot enter that run.

Exit codes are 0 for completed work, including business referrals and pending sign-offs; 1 for setup/incomplete execution, technical handoffs or unresolved unpaid approvals; and 130 for Ctrl+C. A recovered transient failure alone does not cause exit 1. Totals distinguish distinct claims from assessment attempts and count recorded outcomes and payments, pending sign-offs, technical handoffs and claims left waiting. A failure after a saved decision remains visible without erasing that decision.

## Recovery and limits

A failed assessment without a decision returns to Waiting. The queue retries it during the same run while continuing other work. Two undecided failures place it in Handler review with no invented referral or payout. Failure counts persist across runs/restarts, so restarting cannot silently give it a third attempt. There is no manual review-resolution/reset-failure workflow in this build.

Provider and replay-safe HTTP operations retry transient connection/timeout, 429 and selected 5xx errors at most twice after the initial request, with 0.25/0.5-second backoff. OpenRouter SDK retries are disabled. Validation errors, business conflicts and financial POSTs are not blindly replayed. A lost decision/payment response triggers stored-state read-back; a second financial write is allowed only if the first did not commit and the same attempt remains active. Unrecoverable unpaid approvals visibly require handler attention.

Each claim assessment has a ten-minute overall deadline, forty model calls and eighty tool calls. Model requests time out after 120 seconds. The coordinator has a workload-scaled call budget and checks for missed/retryable work before accepting a final answer. Its old queue snapshots are discarded to bound conversation growth. Detailed claim evidence never enters the parent conversation.

Ctrl+C stops new admissions and cancels the CLI's active work, closing its run/to-do state. The standalone command shuts down its temporary backend; an attached app stays available. Backend shutdown also fences any active explicit run before releasing database ownership. Server-side attempt IDs and deadlines reject late helper decisions, evidence, events and payments. Restart closes abandoned runs once and leaves eligible work Waiting until another explicit command; it does not resume model execution. Saved decisions, sign-offs and payments survive recovery.

## Rebuild and ownership

Use one backend owner per database. The explicit CLI attaches through HTTP and the backend rejects overlapping agent runs. A standalone command owns its temporary backend for the run. Reset still requires stopping the app. All commands honor `UNDERCOVER_DB_PATH`, defaulting to the root `undercover.db`. `UNDERCOVER_BACKEND_URL` defaults to `http://localhost:8000`; a custom backend's listening address must match it. The CLI accepts only local HTTP addresses and verifies the configured database identity using health headers before attaching; an unrelated service/database is rejected without assessment.

Stop UnderCover before rebuilding:

```powershell
uv run -m backend.reset_db
```

This validates the permitted seed files and replaces only the configured database. It restores all source claims in source order and clears decisions, sign-offs, simulated payments, events, failure counts, runs, attempts, to-dos and evidence. Invalid input leaves an existing database intact. It starts no agent; evaluation/security claims are never imported. Ordinary startup updates the section index/current instructions without resetting claims.

For a low-cost live trial, use a dedicated PowerShell terminal and a separate database:

```powershell
$env:UNDERCOVER_DB_PATH = Join-Path (Get-Location) 'undercover-harness-trial.db'
$env:MODEL_PROVIDER = 'openrouter'
$env:MODEL_NAME = 'deepseek/deepseek-v4.1-flash'
uv run -m backend.reset_db
uv run -m agent --cap 5
```

Reseed before every live trial and keep the cap at five. To watch the trial in the dashboard, start `npm run dev` first in a separate terminal with the same database setting, then execute the agent command from the terminal above. Stop the app before reseeding. Each explicit invocation has its own cap; starting the app alone consumes no model calls. Close the dedicated terminals afterward to discard their environment overrides.

## Verification

`npm run check` passes for the explicit-launch revision: Ruff, Pyright, 204 Python tests with 97.90% coverage, Prettier, ESLint, TypeScript, 21 component tests with 99.33% statement / 92.72% branch coverage, and eight Playwright tests. The production dashboard build also passes. The synthetic API acceptance sequence verifies five distinct claims, seven attempts, three failures, one technical handoff, one pending sign-off and one cap-excluded Waiting claim. CLI integration verifies standalone and attached commands, full/capped/named scopes, cross-database rejection, interruption, rebuilding and preserved outcomes.

Playwright MCP verified that the app starts idle and stays idle across refresh and navigation: all 25 isolated test claims remained Waiting, with `{"run":null}` before and after, and no console errors. Full-stack tests then explicitly invoke the stubbed CLI to follow plan progress, outcomes, retries, handoff, evidence and handler actions. Earlier harness browser checks cover full evidence, both prompts, frozen summaries, keyboard access and desktop/390-pixel layouts in light/dark modes. Automated checks use stub models and temporary databases. Coverage thresholds and hooks remain enabled. No real agent run was launched for the explicit-command revision.

The historical October 26 live trial, before the explicit-launch revision, used a freshly seeded separate database and the specified default model. It completed five distinct claims in five attempts: five approvals, four simulated payments, one awaiting sign-off, zero failed assessments/technical handoffs/unpaid approvals, and twenty claims left Waiting. All five claims retained wording and manual evidence. Health, live plan updates, full reads, exact read-only prompts, final totals and refresh persistence were checked in Playwright MCP. This verifies operation and observability, not accuracy against the held-out answer key. The trial servers are stopped and results remain in ignored `undercover-harness-trial.db`; the student's `.env` and working `undercover.db` were preserved.
