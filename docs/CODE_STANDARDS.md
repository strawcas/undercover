# Code standards

## General

1. Keep it simple, do not overcomplicate.
2. Comments only when necessary.
3. Be concise: a short README and no emojis.
4. IMPORTANT: avoid overly defensive code. Handle exceptions only when necessary.
5. Use uv: ALWAYS `uv run`, NEVER `python3`.
6. TypeScript: strict mode, never `any`.

## Structure

7. One way to do each thing. The agent reaches the backend only through `agent/api.py` and the dashboard only through `dashboard/src/api.ts`, each with one function per backend operation, named after it. The backend reaches the agent's service only through `backend/agent_service.py`. The base URL and each path are written once, there, and no other module builds a URL or sends a request.
8. Each module has one job, and its name says what it is. A module uses another through what that module is for, and never borrows its helpers. A helper two modules need goes in a module of its own. No circular imports. Tests follow the same rule: shared test helpers live in their own module, never in another test file.
9. Write a literal once. A URL, a path, a status or a message used in more than one place is named once, in the module that owns it.
10. Use the framework's own way before writing your own: FastAPI dependencies and models, LangChain's `context_schema` and `ToolRuntime`, LangGraph's claim `StateGraph`, `ToolNode`, `interrupt()` and checkpointer, a dispatcher `StateGraph` with `Send`, reducers, `RetryPolicy` and an `error_handler`, SQLAlchemy Core, and React hooks/shared components. Check the installed framework APIs.
11. A function does one job. Split one that mixes levels of detail, such as setting up a run, driving the agent and recording the result.
12. When you extend code, bring it up to these standards first. Never copy a pattern they rule out.

## The agent

13. The agent has five parts, kept apart. `api.py` is its only backend client. `tools.py` holds the claim tools, contexts and state. `harness.py` builds and runs the durable claim graph, owns its pool and records measurements. `dispatcher.py` selects and fans out work without a model, calling one harness function per claim and run. `service.py` admits and schedules runs, handles startup/cleanup and sign-off resumes. The backend enforces claim and payment rules.
14. Instructions give an agent its goal, what it has to work from and what done looks like. They don't script its steps: it plans those itself. Anything that must always happen, or happen in a fixed order, is done by the code.
