# Status

The original claims pilot is complete. Implementation of the revised `docs/PLAN.md` is in progress. Phase 1 is verified: persisted queue order, atomic reservation and process ownership are built, with 72 Python tests passing. Automatic processing and the two terminal commands are still pending. The run instructions below describe the current manual-start pilot.

## Run locally

Use Python 3.12, uv and Node 26. From the repository root:

```powershell
uv sync
npm install
npm run dev
```

Open http://localhost:5173 for the dashboard. The backend is at http://localhost:8000, with API documentation at `/docs` and health at `/api/health`.

Add `OPENROUTER_API_KEY` to the existing root `.env` for real agent runs. The default model is `openai/gpt-6-luna` with low reasoning effort. `MODEL_NAME` can override it, for example `openrouter/free`. `.env.example` lists optional settings. Never commit `.env`.

## Planned database and queue commands

These commands are specified in `docs/PLAN.md` but are not implemented yet. After implementation, stop `npm run dev` with Ctrl+C and run them from the repository root.

Rebuild the database from `data/`, returning every claim in `data/claims.json` to Waiting:

```powershell
uv run -m backend.reset_db
```

This clears previous decisions, sign-offs, simulated payments and activity, reloads the permitted source records, and prints the database path and restored claim count. It leaves the claims waiting until processing starts.

Process every waiting claim in queue order:

```powershell
uv run -m agent
```

This starts its own local backend, runs the configured agent one claim at a time, prints each decision, payout, explanation and payment/sign-off state, then prints totals and exits. Failed claims are reported and later claims continue. Existing decided claims are skipped. No dashboard or separately running backend is needed.

Both commands use the configured `UNDERCOVER_DB_PATH`, defaulting to `undercover.db`, and refuse to run while another process owns that database. Processing uses `OPENROUTER_API_KEY` and the optional `MODEL_NAME` from `.env`. Payouts over $1,000 still require a handler's sign-off.

## What is built

- FastAPI and SQLite seed the 25 pilot claims, linked policies and customers, plans, add-ons, handlers, company settings and policy wording. No evaluation or security claims, claims manual or manual-only settings are exposed.
- The dashboard shows the queue's four workflow states, every claim item and document, policy and customer history, decisions and calculations, policy references, read-only instructions and timestamped activity. It supports light and dark browser preferences and narrow screens.
- Run agent starts one local background assessment using LangChain `create_agent`. All agent context and writes go through backend HTTP tools. Model output is assessed against policy wording, without a separate claims rules engine.
- Approve, decline and refer are distinct from payment state. An approved payout up to and including $1,000 can be paid automatically. Above $1,000, a handler must sign off before the separate payment operation succeeds. The backend checks the approved amount, sign-off and duplicate payment within a transaction.
- Sign-off records the selected handler, amount and timestamp. All payments are simulated. Eligible unpaid approvals can also be paid from the dashboard.
- Records persist in the ignored root `undercover.db`. `UNDERCOVER_DB_PATH` optionally changes its location. Seeding preserves existing work. `UNDERCOVER_BACKEND_URL` optionally changes the agent's HTTP destination.
- A failed or interrupted run returns to waiting when no decision was recorded. A server restart preserves committed decisions, sign-offs, payments and activity; it does not resume model execution.

## Verification

- `uv sync`, `npm install` and `npm run dev` succeed on Windows; live health returns `{"status":"ok"}` and the queue contains 25 claims.
- `npm run check` passes under Node 26.12.1: Ruff formatting and lint, Pyright, 65 pytest tests with 100% Python coverage, Prettier, ESLint, strict TypeScript, 14 Vitest tests with 99.21% statement coverage, and six Playwright tests.
- `vite build dashboard` succeeds. Compatible development-tool dependency patches were applied; `npm audit` reports zero vulnerabilities.
- Full-stack tests start isolated services on backend port 8011 and dashboard port 5174. They use a temporary database and scripted models, with no paid model calls or development database changes.
- Tests cover approval/payment, decline, referral, the exact $1,000 boundary, blocked payment even when the agent attempts it, handler sign-off, duplicates and competing calls, retries, polling cleanup, read-only instructions and persistence after refresh and a new app/store instance.
- Playwright MCP browser checks verified full records, exact instruction text, live progress, handler sign-off and a $1,250 simulated payment, persistence after refresh, keyboard focus, and desktop/390px light and dark layouts without horizontal overflow or unexpected console errors. Screenshots are in the ignored `.playwright-mcp` directory.

## Pilot limits

There is no sign-in; everyone sees the same data, and selecting a handler records attribution rather than authenticated identity. There are no real payments, customer messages, referral-resolution workflow, evaluations, durable workers or cloud resources. Decisions are final in this pilot. Live-model accuracy has not been measured against the answer key, which remains unopened.
