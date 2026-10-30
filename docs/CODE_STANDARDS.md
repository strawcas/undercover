# Code standards

1. Keep it simple, do not overcomplicate.
2. Comments only when necessary.
3. Be concise: a short README and no emojis.
4. IMPORTANT: avoid overly defensive code. Handle exceptions only when necessary.
5. Use uv: ALWAYS `uv run`, NEVER `python3`.
6. TypeScript: strict mode, never `any`.
7. Modularize React components when it improves reusability, readability, or maintainability, but avoid unnecessary abstraction by keeping small, simple, single-use components within their parent.
8. Make the business workflow visible in its entry point. Queue completion, delegation, decision/payment ordering and finalization belong in named workflow functions. The queue uses `create_agent` and framework middleware; the claim uses named LangGraph nodes/edges for reasoning, tools, structured decision, approval wait and payment. The service is the fourth agent component beside instructions, tools and orchestration. It owns one sequential execution worker shared by CLI commands and sign-off notifications. Tool adapters expose ordinary named methods; canonical prompts live beside the agent code and the backend serves the same prompts read only.
9. Use named domain types for scopes, attempts, claims, completion checks and run results. Reuse the API contracts and validate data when it crosses HTTP or persistence boundaries. Do not use a generic `Record = dict[str, Any]` for unrelated business objects. Keep dynamic JSON confined to serialization/source-record boundaries and framework-required types. Pass deadlines and cancellation explicitly; do not hide them in context variables.
10. Separate essential state changes from observational work. Saving decisions, payments, evidence, attempts and final run state must surface failures and preserve reconciliation/fencing. Activity-event delivery and terminal observers must not turn successful business operations into failed assessments. Do not replay append-only events after an ambiguous response; report delivery failures through safe diagnostics.
11. Cleanup must preserve the primary failure, cause chain and interruption behavior. Surface cleanup failures when no primary failure exists. A broad exception handler is appropriate only at an execution boundary with an explicit recovery/reporting policy. Unexpected failures need a diagnostic reference linked to exception types and file/function/line locations. Never log exception bodies, locals, provider responses, credentials or customer payloads; handler-facing messages use safe explanations and that reference.
12. Recovery advice and UI labels must match actual eligibility and launch rules: explicit agent commands, at most two failed assessments and then handler review. Restarting the app must never be described as starting or retrying the agent.
