# Code standards

## General

1. Keep it simple, do not overcomplicate.
2. Comments only when necessary.
3. Be concise: a short README and no emojis.
4. IMPORTANT: avoid overly defensive code. Handle exceptions only when necessary.
5. Use uv: ALWAYS `uv run`, NEVER `python3`.
6. TypeScript: strict mode, never `any`.

## Structure

Modularize React components when it improves reusability, readability, or maintainability, but avoid unnecessary abstraction by keeping small, simple, single-use components within their parent.

7. Give each route one client. `agent/api.py` owns app operations and uses `agent/backend_client.py` for direct requests and the shared app refusal type. The improver gets results through `agent/improver_api.py` on that same direct transport. `agent/gateway.py` owns signed Gateway calls and tool names. `agent/context_client.py` owns direct context access and the choice of context route. Both MCP routes share `agent/mcp_client.py`. `agent/memory.py` owns AgentCore Memory access. The dashboard uses `dashboard/src/api.ts` with one function per operation and `dashboard/src/supabase.ts` for Auth. The backend calls the agent service through `backend/agent_service.py` and Jev through `backend/judge.py`. Keep URLs, request shapes and service-specific error handling in their owning clients. Share page headers, model comparisons and resource loading through their components and hooks.
8. Each module has one job, and its name says what it is. A module uses another through what that module is for, and never borrows its helpers. A helper two modules need goes in a module of its own. No circular imports. Tests follow the same rule: shared test helpers live in their own module, never in another test file.
9. Write a literal once. A URL, a path, a status or a message used in more than one place is named once, in the module that owns it.
10. Use the framework's own way before writing your own: FastAPI dependencies and models, LangChain's `context_schema` and `ToolRuntime`, LangGraph's claim `StateGraph`, `ToolNode`, `interrupt()` and checkpointer, a dispatcher `StateGraph` with `Send`, reducers, `RetryPolicy` and an `error_handler`, SQLAlchemy Core, and React hooks/shared components. Check the installed framework APIs.
11. A function does one job. Split one that mixes levels of detail, such as setting up a run, driving the agent and recording the result.
12. When you extend code, bring it up to these standards first. Never copy a pattern they rule out.

## The agent

13. The agent has six parts, kept apart: the claim client, claim tools, claim harness, dispatcher, AgentCore entrypoint and `improver.py`. The improver has four tools and its own results client. `harness.py` retains the claim graph and measurements. Shared `checkpoints.py` owns one pool of at most four connections, with separate savers for claims and rounds. Shared `models.py` constructs pinned models; `run_execution.py` executes both standalone runs and nested round evaluations in fresh contexts. `entrypoint.py` validates invocations and tracks background tasks. The backend owns work admission, recovery and handler reviews. The agent reports heartbeats for the work it takes. The backend owns claim/payment rules, round spending, evidence and activation verdicts. Claim execution never receives labels or scores; the improver receives held-out aggregates only. Retain each run's configuration version and round attribution throughout execution, measurements and tracing; approval alone cannot activate a candidate.
14. Instructions give an agent its goal, what it has to work from and what done looks like. They don't script its steps: it plans those itself. Anything that must always happen, or happen in a fixed order, is done by the code.
15. Bind app record identifiers to the claim graph's runtime context. The graph supplies claim, policy, customer, thread and maker inputs where needed. The backend owns decision completeness, the current attempt's calculation and payment. The memory adapter scopes searches to the customer and saves only the approved event fields after a cleared original reaches a decision.
16. The Copilot has its own graph, handler client, tools, worker and AgentCore app. Keep the bearer in request and graph runtime context. The backend owns conversation access, leases, run receipts and revision-bound payout confirmation. The graph renders the pilot figures from app records. Its Memory adapter scopes facts and preferences to the verified handler.
