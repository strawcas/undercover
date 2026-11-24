# Process

`uv sync` and `npm install` at the repo root install everything, including a git pre-commit hook. `npm run dev` starts the backend, agent service, context service and dashboard. `npm run seed` rebuilds the database used by dev, on Supabase when `DATABASE_URL` is in `.env`. The agent always needs `DATABASE_URL` for its checkpoints. The local context service needs only the governance files in its package.

`npm run check` runs every check:

- The backend, context server and Runtime entrypoint import in separate isolated environments containing the locked `server`, `context` and `runtime` groups. The staged Runtime import includes only the platform entrypoint and agent package. uv uses its local cache.
- Python: ruff (lint and format), pyright, and pytest with 80% coverage
- TypeScript: ESLint, Prettier, tsc, and vitest with 80% coverage
- Playwright end-to-end tests

The checks need no network and never read `.env`: the tests use SQLite, a stub model, and stand-ins for Langfuse and the agent's service. Tables and connections on Supabase can be checked through the Supabase MCP server, which is read-only.

The pre-commit hook runs the format, lint, type and unit test checks on every commit.

## Planning

When asked to plan, as in "Please plan the pilot", write the plan to `docs/PLAN.md` and write no code.

- Start with the design: the modules, what each one is for, and how they depend on each other. Check it against `docs/CODE_STANDARDS.md` before you write the phases. Describe the agent's instructions by their goals, never as a list of steps.
- Split the work into phases, usually 5 or 6, each building on the last.
- Give each phase detailed success criteria that you can verify yourself: tests that pass, commands and their output, API calls and their responses, and pages you check in the browser with the Playwright MCP server. Write each one as a checkbox.
- Then stop and wait until you are asked to implement the plan.

## Implementing

When asked to implement the plan, as in "Please implement the plan for the pilot", build every phase in order without stopping. For each phase:

1. Build it, with tests.
2. Check every success criterion and fix whatever fails.
3. Review the phase's changes, as described below, and fix what the review finds.
4. Tick off its criteria in `docs/PLAN.md` and update `docs/STATUS.md`.
5. Commit. The pre-commit hook runs the checks; if it fails, fix the problem and commit again.

When the last phase is done, review the whole branch against `main` in the same way and fix what the review finds. Then run `npm run check` and report the results, including what the reviews found and what you changed.

## Reviewing

The checks show that the code works, but not that it is fit to merge. Every code change gets a review before it is committed.

- The reviewer is a fresh subagent that didn't write the code. Give it `docs/CODE_STANDARDS.md` and the diff, and ask it to review as a strict senior engineer deciding whether to merge the PR.
- It checks every standard, in particular: one way to call each service, with each URL written once; modules that keep to their own job and don't borrow each other's helpers; the framework's own way of doing things over hand-rolled code; functions that do one job; and agent instructions written as goals.
- Fix everything it would block the PR for before you commit. If you disagree with a finding, say so in your report instead of skipping it quietly.

## Rules

- Add tests with each change. Tests never call a model; use a stub model.
- For real-model verification, follow the active brief. The Swarm sequence below replaces the retired five-claim queue cap.
- Outside a plan, commit each change once you have checked and reviewed it.
- Run `npm run check` before you hand work over.
- Never use `git commit --no-verify`.
- Never lower a threshold, turn off a rule or exclude a file to get a green run. If a rule looks wrong, stop and ask.
- Keep `docs/STATUS.md` up to date.
- Never push.
- Don't open `data/evals/` unless you're asked to.
- Everything must work on Windows: write `package.json` scripts as `uv run` commands or `.mjs` files.

## Runtime live verification

Follow the final sequence in [PLAN_RUNTIME.md](PLAN_RUNTIME.md). Work against disposable data, preserve the configured environment, and run the full offline checks with `.env` temporarily absent. The production health check runs `runtime.main` on port 8080 after the dev services stop.

## Context live verification

Follow the final sequence in [PLAN_CONTEXT.md](PLAN_CONTEXT.md). Use the default model with `MODEL_NAME` unset and run the unsigned local context route with memory disabled. Preserve the environment and record the seed completion and run IDs. Reseed once, work CLM-1005 and CLM-1006, then run one N=25 dispatch and one five-claim evaluation of the evaluation set. Use Playwright MCP to inspect both originals' decision records and provenance. Report claim-agent and judge charges from the stored usage. Run the final offline suite with `.env` temporarily absent and restore it in a cleanup block.

## Secure live verification

Use [PLAN_SECURE.md](PLAN_SECURE.md) for the approved sequence. Preserve the local environment and clear `MODEL_NAME` from the file and launching shell. Reseed the business database once before the CLI claim, dispatch, tiny evaluation and senior sign-off. Then run the built backend by itself and check the Supabase transaction pooler with the Lambda marker set.

Assemble the locked server group and Linux wheels in a temporary package directory. Record the zip size and provider-reported spend. Restore the intended environment and run the final offline suite with `.env` temporarily absent. Record unavailable live evidence in the plan and status.

## Swarm live verification

This is the historical Swarm sequence. The active Improve brief uses the sequence below instead; do not repeat these paid runs for Improve.

Use the default model with MODEL_NAME unset in both `.env` and the launching shell (`$env:MODEL_NAME = $null` in PowerShell). Confirm the accepted run records `openai/gpt-6-luna` and low effort. After one `npm run seed`, start `npm run dev` and verify 25 waiting claims and an empty scoreboard. Do not reseed between these runs:

1. Start N=3 through the queue, capture partial claim progress, then force-kill every verified agent process on 8002, including reloader and worker. On Windows use `Get-NetTCPConnection`, inspect process ancestry with `Get-CimInstance Win32_Process`, then `Stop-Process` on verified agent ids. Confirm the dev runner stops the other services.
2. Restart dev and verify the abandoned dispatch is stopped. Start N=25; compare saved attempts/threads, overlapping work intervals, wall versus active time and per-claim speed. Keep at most 4 connections per process; observe at most 12 postgres-user sessions through the read-only Supabase MCP server. Sign off a held original and verify payment within 5 seconds.
3. Run the full evaluation set at N=25 and held-out tiny set at N=5 through the Scoreboard. Queue counts must stay unchanged, held copies need no sign-off and their checkpoints are cleared. Check Langfuse's API for claim traces tagged with the N=25 dispatch id.

Automated checks remain offline with stub models. For final verification temporarily move `.env` aside, run `npm run check`, and restore the file even if a check fails. Never claim unavailable external verification passed; record the exact remaining requirement in STATUS and the active plan.

## Improve live verification

Check pre-created Supabase users, disabled signup, asymmetric JWKS and the configured Auth, database, OpenRouter and Langfuse prerequisites without printing secrets. Preserve `.env` and unset `MODEL_NAME` in both the file and launching shell. With the default model, reseed the business database once, then start dev. Do not reseed between these checks or run exploratory paid evaluations:

1. Sign in as Dana through Playwright MCP. Run two rounds from Improvement. Reject the first proposal with a reason, approve its replacement and subsequent configuration/recipe proposals. Record progress, versions, verdicts, held-out before/after scores and attributed cost, including rollbacks.
2. Compare version 1's two baseline held-out runs beside the snapshotted noise/tolerance. Accuracy/grade use absolute differences; cost/time use fractions of the earlier measure. Check the supported Langfuse API for one session per round and round/run tags on evaluation claim traces.
3. Start one N=25 dispatch on the resulting current version. Marcus cannot sign off a held original payout or change Settings. Dana signs off that same payout; verify payment and Dana's recorded identity. Evaluation copies stay outside the queue.
4. Report actual improver, evaluation, judge and dispatch spend. Capture every page in both themes and obtain the brief's fresh product-design review. Record unavailable external verification precisely. Restore the original environment, then run the final offline checks with `.env` temporarily absent and confirm byte-for-byte restoration in `finally`.

Retain a local seed-completed marker and the original environment hash so an interrupted verification cannot accidentally reseed or repeat paid work. The supported Langfuse observations API is `/api/public/v2/observations`; request basic, trace-context, usage and model fields without fetching claim inputs/outputs. An external failure is evidence to report, not permission to add exploratory rounds or change acceptance thresholds.
