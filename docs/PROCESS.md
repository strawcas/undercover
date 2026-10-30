# Process

`uv sync` and `npm install` at the repo root install everything, including a git pre-commit hook. `npm run dev` starts the backend, agent service and dashboard on Windows or macOS. Development/terminal entry points load the root `.env` explicitly; tests and imported product modules never load it. Terminal variables take precedence.

Start a queue explicitly with `uv run -m agent`, `--cap N` or `--claim-id ID`. The command attaches to matching local services or owns temporary backend/agent child processes. An interrupted queue keeps its scope, cap, to-dos and admitted claims; the next command continues it before admitting fresh work. Restarting services alone admits no fresh claims. Sign-off notifications resume already approved claims through the same sequential worker.

`npm run check` runs every check:

- Python: ruff (lint and format), pyright, and pytest with 80% coverage
- TypeScript: ESLint, Prettier, tsc, and vitest with 80% coverage
- Playwright end-to-end tests

Checks use temporary SQLite databases and stub models. Browser tests run a stand-in agent on port 8003 and point only their isolated backend at it. They must not use the development service on port 8002, Supabase, Langfuse or a real model. For durable-state handover, run the full command with `.env` temporarily absent and external-service variables removed, restoring both in `finally` even on failure.

The pre-commit hook runs the format, lint, type and unit test checks on every commit.

## Planning

When asked to plan, as in "Please plan the pilot", write the plan to `docs/PLAN.md` and write no code.

- Split the work into phases, usually 5 or 6, each building on the last.
- Give each phase detailed success criteria that you can verify yourself: tests that pass, commands and their output, API calls and their responses, and pages you check in the browser with the Playwright MCP server. Write each one as a checkbox.
- Then stop and wait until you are asked to implement the plan.

## Implementing

When asked to implement the plan, as in "Please implement the plan for the pilot", build every phase in order without stopping. For each phase:

1. Build it, with tests.
2. Check every success criterion and fix whatever fails.
3. Tick off its criteria in `docs/PLAN.md` and update `docs/STATUS.md`.
4. Commit. The pre-commit hook runs the checks; if it fails, fix the problem and commit again.

When the last phase is done, run `npm run check` and report the results.

## Rules

- Add tests with each change. Tests never call a model; use a stub model.
- Outside a plan, commit each change once you have checked it.
- Run `npm run check` before you hand work over.
- Never use `git commit --no-verify`.
- Never lower a threshold, turn off a rule or exclude a file to get a green run. If a rule looks wrong, stop and ask.
- Keep `docs/STATUS.md` up to date.
- Never push.
- Don't open `data/evals/` unless you're asked to.
- Everything must work on Windows: write `package.json` scripts as `uv run` commands or `.mjs` files.
- When asked a question about the code, explain the issue and its cause without modifying any code unless I explicitly ask you to make changes.

## Durable-state acceptance

Stop the backend and agent before `uv run -m backend.reset_db`. It validates permitted source records before replacing SQLite or transactionally reseeding owned Supabase tables/checkpoints in `undercover`. Normal startup preserves saved work. Switching to Supabase does not transfer or overwrite historical SQLite work.

Every real-model trial starts with a reseed and uses the default model with `uv run -m agent --cap 5`. Keep the service running for dashboard sign-off. Test kill/restart recovery inside that same capped run without reseeding between interruption and continuation. A second independent trial needs a fresh reseed. Never reset the student's working database as part of automated tests.

Verify one payment with saved handler/time, the same attempt/thread after recovery, at most twelve observed Postgres connections across processes, and Langfuse session traces before/after sign-off. Each database-using process must have at most four connections, including ownership sessions. Record live evidence and explicit pending items in status; offline mocks do not establish these live results.

## Review before committing

Passing checks and meeting a coverage percentage do not replace a code review. For each changed workflow, read the complete changed functions and their callers, then record the review scope and results in `docs/STATUS.md`:

- Trace the entry point through admission, helper execution, tools, decision/payment, attempt finalization and run finalization. The business sequence must be apparent in named functions. Explain the purpose of each middleware callback and shared state object; move business orchestration out of hidden callbacks.
- Check HTTP and stored-data boundaries for domain types and validation. Reject a shared untyped dictionary for different business records. Verify that deadlines/cancellation have visible callers and that the dashboard uses the same API fields and state meanings.
- Classify writes as essential, replay-safe, financial or observational. Verify that activity logging and reporting cannot interrupt a successful decision/payment or a provider retry. Preserve the backend's mandatory evidence, financial checks and durable lifecycle events.
- Review every changed `finally`, broad exception handler and observer. Inject an operation failure together with a cleanup failure; prove the primary cause survives. Also test cleanup failure alone, interruption during cleanup, exhausted retries and failure after a committed financial write. Assert stored state and exit codes, not just exception text.
- Verify safe diagnostics: an unexpected helper failure produces a reference and useful code locations; provider/customer/credential sentinel values appear in neither public errors nor logs. Check recovery instructions against the current CLI and the two-failure handler-review rule.
- Inspect related backend and dashboard callers for the same defect. Retain existing regression assertions, payment boundaries, replay/fencing, persistence, idle startup and read-only polling checks when refactoring; adapt tests individually rather than replacing whole suites.

Use stub models and temporary databases for these failure checks. After the targeted checks pass, run `npm run check`, inspect the final diff, update status with exact results and commit with hooks enabled. If a defect remains, describe it explicitly; do not mark the review complete merely because the automated checks pass.
