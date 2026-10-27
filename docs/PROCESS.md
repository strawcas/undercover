# Process

`uv sync` and `npm install` at the repo root install everything, including a git pre-commit hook. `npm run dev` starts the backend and the dashboard.

`npm run check` runs every check:

- Python: ruff (lint and format), pyright, and pytest with 80% coverage
- TypeScript: ESLint, Prettier, tsc, and vitest with 80% coverage
- Playwright end-to-end tests

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

## Review before committing

Passing checks and meeting a coverage percentage do not replace a code review. For each changed workflow, read the complete changed functions and their callers, then record the review scope and results in `docs/STATUS.md`:

- Trace the entry point through admission, helper execution, tools, decision/payment, attempt finalization and run finalization. The business sequence must be apparent in named functions. Explain the purpose of each middleware callback and shared state object; move business orchestration out of hidden callbacks.
- Check HTTP and stored-data boundaries for domain types and validation. Reject a shared untyped dictionary for different business records. Verify that deadlines/cancellation have visible callers and that the dashboard uses the same API fields and state meanings.
- Classify writes as essential, replay-safe, financial or observational. Verify that activity logging and reporting cannot interrupt a successful decision/payment or a provider retry. Preserve the backend's mandatory evidence, financial checks and durable lifecycle events.
- Review every changed `finally`, broad exception handler and observer. Inject an operation failure together with a cleanup failure; prove the primary cause survives. Also test cleanup failure alone, interruption during cleanup, exhausted retries and failure after a committed financial write. Assert stored state and exit codes, not just exception text.
- Verify safe diagnostics: an unexpected helper failure produces a reference and useful code locations; provider/customer/credential sentinel values appear in neither public errors nor logs. Check recovery instructions against the current CLI and the two-failure handler-review rule.
- Inspect related backend and dashboard callers for the same defect. Retain existing regression assertions, payment boundaries, replay/fencing, persistence, idle startup and read-only polling checks when refactoring; adapt tests individually rather than replacing whole suites.

Use stub models and temporary databases for these failure checks. After the targeted checks pass, run `npm run check`, inspect the final diff, update status with exact results and commit with hooks enabled. If a defect remains, describe it explicitly; do not mark the review complete merely because the automated checks pass.
