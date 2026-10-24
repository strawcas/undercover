# Status

The startup-pass revision in `docs/PLAN.md` is in progress. The backend now processes one startup snapshot and finishes, with 103 Python tests passing. Both terminal commands and the prior safeguards are preserved; dashboard wording and final integration follow-up are pending.

## Run locally

Use Python 3.12, uv and Node 26. From the repository root:

```powershell
uv sync
npm install
npm run dev
```

Open http://localhost:5173 for the dashboard. The backend is at http://localhost:8000, with API documentation at `/docs` and health at `/api/health`.

Add `OPENROUTER_API_KEY` to the existing root `.env` for real agent runs. The default model is `openai/gpt-6-luna` with low reasoning effort. `MODEL_NAME` overrides it, for example `openrouter/free`. Your existing model selection is preserved. Never commit `.env`.

At each backend startup, the app snapshots waiting claims, processes them one at a time once its HTTP API is ready, then ends the processing task. Claims arriving during or after that pass wait for another backend start or explicit terminal run. Dashboard refreshes do not trigger processing. Select a claim to follow its activity and decision; there is no Run agent button.

## Rebuild the database

Stop `npm run dev` with Ctrl+C first. From the repository root:

```powershell
uv run -m backend.reset_db
```

This rebuilds the configured database from the permitted source files in `data/`, returning every claim in `data/claims.json` to Waiting in source-file order. It clears previous decisions, sign-offs, simulated payments, thread IDs and activity, then prints the database path and restored claim count. It starts no agent runs; claims remain waiting until processing starts.

A missing database is created. Invalid or incomplete source records leave an existing database intact. Evaluation data, security claims and the claims manual are never imported. Changing source files takes effect on a fresh database or a rebuild; ordinary startup preserves saved work.

## Process every waiting claim from the terminal

Stop `npm run dev` with Ctrl+C first, then run:

```powershell
uv run -m agent
```

This starts its own local HTTP backend, processes the waiting queue present at startup in persisted order, then shuts down and exits. No dashboard or separately running backend is needed. It uses the same configured model, instructions, tools and payment checks as automatic processing.

After each attempt, it prints the claim ID, recorded outcome, USD payout, explanation, policy references and payment/sign-off state. Failures are printed and processing continues with later claims. The final line reports attempted and decided counts, approved/declined/referred counts, failed runs and approvals awaiting sign-off. A failure after a saved decision contributes to both the decided and failed-run counts.

Decided claims and approvals awaiting sign-off are skipped. An empty queue prints zero totals without calling a model. Exit codes are 0 for a successful pass, 1 for a setup error or any failed run, and 130 for Ctrl+C. Interruption prints partial totals and leaves undecided work recoverable.

## Database ownership and retries

Use one backend process or terminal command per database. All three execution modes honor `UNDERCOVER_DB_PATH`, defaulting to the root `undercover.db`, and reject competing owners. The terminal agent also refuses an occupied HTTP address. Lock files are ignored by Git and do not block reuse after the owning process exits.

`UNDERCOVER_BACKEND_URL` defaults to `http://localhost:8000`. The standalone agent binds this local HTTP address. When starting the normal backend at a custom address, its listening host/port must match the URL used by agent tools.

Each session attempts a claim once. Failed attempts without a decision return to Waiting and are skipped for the rest of that session. Restart the backend, or stop it and run the terminal agent, to retry them. Shutdown stops new reservations and allows the active run five seconds to finish. If it takes longer, the database remains owned until that run finishes or its process exits. Restart retries abandoned undecided work from the beginning and preserves saved decisions, sign-offs and payments; it does not resume model execution.

## What is built

- SQLite stores the source claims and their linked policies and customers, plans, add-ons, handlers, company settings and policy wording. A persisted queue position controls both dashboard display and worker selection. Existing databases migrate without clearing work.
- The LangChain agent reads instructions and claim context and records activity, decisions and payments through backend HTTP tools. Startup processing atomically reserves one claim at a time from a fixed snapshot, then terminates while the API remains available.
- The dashboard follows waiting and active claims automatically, including idle gaps and new stored arrivals. It shows complete records, decisions, calculations, references, errors, read-only instructions and timestamped activity. Polling cleans up on navigation.
- Approval, decline and referral remain distinct from payment state. Approvals up to and including $1,000 can be paid automatically. Larger approvals require handler sign-off before the separate payment operation can succeed; the worker continues with other claims while sign-off is pending.
- Handler sign-off and eligible simulated payment remain available in the dashboard. The backend enforces exact approved amounts, sign-off and duplicate-payment checks in transactions.
- Terminal commands share configuration, database ownership, source validation and queue execution with the backend. There is no reset HTTP endpoint or per-claim start-run endpoint.

## Verification

- `uv sync`, `npm install`, development startup and the production dashboard build succeed on Windows. Both terminal entry points were also run against separate test databases.
- `npm run check` passes: Ruff, Pyright, 101 pytest tests with 99.60% Python coverage, Prettier, ESLint, strict TypeScript, 16 Vitest tests with 99.18% statement coverage, and six Playwright tests. Compatible development dependency updates were applied; `npm audit` reports zero vulnerabilities.
- Automated checks use temporary databases and stub models, including actual local HTTP tool calls, queue migration/order, exclusive ownership and crash recovery, failures before/after decisions, Ctrl+C, rebuild validation, totals, empty queues and repeated drains.
- A complete terminal integration test rebuilds the temporary source queue, processes all 25 claims in order, verifies the printed results and counts, confirms a repeated drain attempts zero claims, then rebuilds the queue again with all previous results cleared. A running backend blocks rebuilding; a slow stopping worker retains ownership until its active attempt finishes.
- Playwright MCP checks verified automatic updates, handler sign-off and a $1,250 simulated payment, exact read-only instructions, keyboard focus and desktop/390-pixel layouts in light and dark modes without horizontal overflow or browser console errors after services were ready. Screenshots are in the ignored `.playwright-mcp` directory.
- The working database and `.env` have not been reset or changed during this build. Evaluation answer keys remain unopened. No automated test calls a live model.

## Pilot limits

There is no sign-in; everyone sees the same records, and handler selection records attribution rather than authenticated identity. Payments are simulated. There are no customer messages, customer submission screen, live source-file import, referral-resolution workflow, evaluation scoring, agent swarm, durable model execution or cloud resources. Live-model accuracy has not been measured against the answer key.
