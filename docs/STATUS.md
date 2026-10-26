# Status

The harness in `docs/HARNESS.md` is implemented. Verification is tracked in `docs/PLAN_HARNESS.md`; `docs/PLAN.md` remains the completed pilot plan.

## What is built

One queue agent chooses the order within a finite snapshot, writes a claim-linked to-do list and delegates each claim to a fresh helper. Helpers read the complete claim and selected policy/manual sections through HTTP, record a decision and make an eligible simulated payment. The queue receives concise results; full activity and exact versioned section text remain on each claim. The dashboard shows the current/latest plan, all five claim states, evidence and both roles' read-only instructions.

FastAPI/SQLite exposes 25 typed API operations: the existing 12 plus eight run/attempt and five reference/evidence/agent-payment operations. Run creation records a scope and never launches a model. The runtime performs delegation. The backend enforces atomic reservations, distinct-claim caps, stable truthful to-dos, completion checks, stale-attempt fencing, exact money, duplicate prevention and handler sign-off above $1,000. Neither agent has a sign-off tool. Existing databases migrate without reseeding saved work; legacy decisions retain their original references without invented manual provenance.

There is one run at backend startup, after HTTP readiness. No continuous watcher or dashboard start button is present. Claims added after the snapshot, and cap-excluded claims, wait for another startup or terminal run. Polling and refresh only read progress. A run's finished summary is a completion-time snapshot and does not change after later handler actions.

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
# Optional cap on distinct claims per startup/terminal run:
# UNDERCOVER_MAX_CLAIMS=5
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

Ollama uses its native API address without `/v1`, temperature zero and no OpenRouter key. Choose model/context sizes that fit local memory. A missing model tag, invalid provider/context setting or missing required key fails before any claim is reserved. An empty run needs no model configuration. Ollama connection, missing-model, tool-support and truncation failures produce actionable messages; there is no automatic provider fallback. Stop UnderCover before changing settings, then restart it. Provider changes affect future attempts; saved decisions remain unchanged.

## Full, capped and named terminal runs

Stop the app with Ctrl+C first. Run one of:

```powershell
uv run -m agent
uv run -m agent --max-claims 5
uv run -m agent --claim-id CLM-1001
```

The command starts its own local HTTP backend, works the registered scope, prints each attempt's saved outcome, payout, explanation, policy/manual references and payment/sign-off state, then prints backend-derived totals and exits. No dashboard or separately running backend is needed. The queue agent chooses the order; database queue order remains stable for display.

A positive `UNDERCOVER_MAX_CLAIMS` applies to startup and the CLI default. Unset means the full eligible snapshot. An explicit CLI cap overrides it. Named mode ignores it and accepts only one existing eligible claim. Do not combine the flags. Unknown, decided, active or Handler review claims are rejected without reassessment. A cap counts distinct admitted claims, including failures; retries consume the same slot. Claims outside the snapshot cannot enter that run.

Exit codes are 0 for completed work, including business referrals and pending sign-offs; 1 for setup/incomplete execution, technical handoffs or unresolved unpaid approvals; and 130 for Ctrl+C. A recovered transient failure alone does not cause exit 1. Totals distinguish distinct claims from assessment attempts and count recorded outcomes and payments, pending sign-offs, technical handoffs and claims left waiting. A failure after a saved decision remains visible without erasing that decision.

## Recovery and limits

A failed assessment without a decision returns to Waiting. The queue retries it during the same run while continuing other work. Two undecided failures place it in Handler review with no invented referral or payout. Failure counts persist across runs/restarts, so restarting cannot silently give it a third attempt. There is no manual review-resolution/reset-failure workflow in this build.

Provider and replay-safe HTTP operations retry transient connection/timeout, 429 and selected 5xx errors at most twice after the initial request, with 0.25/0.5-second backoff. OpenRouter SDK retries are disabled. Validation errors, business conflicts and financial POSTs are not blindly replayed. A lost decision/payment response triggers stored-state read-back; a second financial write is allowed only if the first did not commit and the same attempt remains active. Unrecoverable unpaid approvals visibly require handler attention.

Each claim assessment has a ten-minute overall deadline, forty model calls and eighty tool calls. Model requests time out after 120 seconds. The coordinator has a workload-scaled call budget and checks for missed/retryable work before accepting a final answer. Its old queue snapshots are discarded to bound conversation growth. Detailed claim evidence never enters the parent conversation.

Shutdown stops new admissions and allows active work five seconds to settle, then cancels the attempt. The owning runtime closes active run/to-do state before releasing database ownership, including when HTTP cleanup fails. Server-side attempt IDs and deadlines reject late helper decisions, evidence, events and payments. Restart closes abandoned runs once and starts fresh conversations for eligible work; it does not resume model execution. Saved decisions, sign-offs and payments survive recovery.

## Rebuild and ownership

Use one backend/terminal/reset process per database. The backend keeps ownership while serving handler APIs even after its queue task finishes. All commands honor `UNDERCOVER_DB_PATH`, defaulting to the root `undercover.db`, and reject competing owners. `UNDERCOVER_BACKEND_URL` defaults to `http://localhost:8000`; a custom backend's listening address must match it. The terminal command accepts local HTTP addresses and rejects occupied ports.

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
$env:UNDERCOVER_MAX_CLAIMS = '5'
uv run -m backend.reset_db
npm run dev
```

Reseed before every live trial and keep the cap at five. Stop the app afterward and close that terminal to discard its overrides. Do not run another live startup against the same trial results expecting the original cap to cover both runs; each invocation has its own cap.

## Verification

The queue/recovery phase passed Ruff, Pyright and 194 Python tests with 97.77% coverage. The dashboard passed Prettier, ESLint, TypeScript and 21 component tests with 99.33% statement / 92.72% branch coverage. Playwright MCP verified retries, technical handoff, full evidence, both prompts, handler sign-off/payment and frozen summaries, with keyboard access and no overflow/browser errors at desktop and 390 pixels in light/dark modes. Final API/full-stack checks are in progress.

The October 26 live trial used a freshly seeded separate database and the specified default model. It completed five distinct claims in five attempts: five approvals, four simulated payments, one awaiting sign-off, zero failed assessments/technical handoffs/unpaid approvals, and twenty claims left Waiting. This verifies operation and observability, not accuracy against the held-out answer key. The student's `.env` and working `undercover.db` were preserved.
