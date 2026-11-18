# Plan for securing UnderCover before deployment

Follow the four steps in [SECURE.md](SECURE.md) in order. Each phase gets its own offline checks, fresh code review and commit under [PROCESS.md](PROCESS.md). Run the live checks together after phase 4 using one reseed. Packaging and verification take place locally.

**Course sidenote.** Read [Week 3 Day 2 Lab 1](../labs/week3day2_lab1.ipynb) and build the security check in the style of its structured-output model check. Read [Week 3 Day 2 Lab 2](../labs/week3day2_lab2.ipynb) for the deployment contract, with its cells left unexecuted. Follow [SECURE.md](SECURE.md) wherever the notebooks differ from the brief. Both notebooks were read during planning with their cells left unexecuted.

## Design

### Request authorization

`agent/backend_client.py` attaches `X-API-Key` at request time. The existing claim and improver clients share this transport, so the CLI can load `.env` after imports and still send the key. `agent/service.py` checks for the key before initializing checkpoints or attempting recovery. The dashboard continues to send its Supabase bearer token through `dashboard/src/api.ts`.

`backend/auth.py` owns constant-time key validation and reuses the existing Supabase verification for handler and shared-read dependencies. Apply dependencies to the operations in `backend/main.py` and `backend/improvement_routes.py` based on their callers.

| Access | Operations |
| --- | --- |
| Public | `GET /api/health` |
| Agent key or signed-in handler | Queue and claim detail, policy, customer claim history, current configuration, and the phase 3 agent OpenAPI document |
| Agent key | Claim start, failure, decisions, payout calculation, payments, step writes, work slices, model-call receipts and admission. Document search and section reads, pinned configuration lookup, and internal run creation, lookup, selection, progress, closure and checkpoint lookup. All `/api/improver/` operations, including round creation and proposal decisions submitted by the service |
| Signed-in handler | Identity, handler list, claim step reads, configuration history, settings reads, latest dispatch and active-run status, Scoreboard and comparisons, round and recipe history, and dispatch/evaluation starts |
| Senior handler | Payout sign-off, setting changes, round start/stop and proposal review |

The agent key grants service access only. Handler permissions and attribution continue to come from the verified Supabase identity on dashboard actions. The service records the forwarded senior identity using its own credential. Read the key through the request in the dependency so it adds no OpenAPI parameters.

The backend also sends the key when calling private agent-service endpoints. The service checks it before acting on any forwarded handler identity.

### Claim screening

A screening module owns the Lab 1 prompt, structured result and bounded parse retry. It reads the claim through `agent/api.py` and presents the customer fields as one JSON record using the claim API's field names. Include summary, story, incident details, every item and the full document text and metadata. The screen's goal is to identify directions to whoever handles the claim or purported internal approval or waiver, while allowing ordinary descriptions, requests and reported conversations. The schema puts `reason` first and `instructions_found` second.

`agent/harness.py` adds screening at the start of the existing checkpointed graph. Save a valid result before applying it so replay can use the result without another model call. Record one security step per screened attempt through the existing step API, using thread identity to make the write replay-safe. A pass goes to the normal claim model. A flag records a zero-payout referral with the returned reason and the section `Security check`, then ends. A failed screen follows existing claim error handling before adjudication. The same entry path covers the CLI, dispatches and evaluation copies, including improvement evaluations. Saved progress continues at its recorded node, and a fresh first-message attempt goes through screening.

Use `agent/models.py` for provider construction and share the existing `MODEL_NAME` resolution in `backend/configuration_defaults.py`. The screen uses Luna at effort `none`, or the explicit override with the provider effort omitted. The screen's instructions and model selection live outside the versioned claim configuration. Apply run admission before each call and use `with_structured_output` with `include_raw=True` for model-call accounting, including replies that fail to parse. Record each attempt against its claim, thread and run, with unavailable usage when the provider returns none. Disable provider retries on the checker so the parser's three-attempt limit and each physical call are visible.

### Agent API and payments

Generate the Gateway contract from a FastAPI route allowlist by operation ID. FastAPI supplies the operation definitions and their referenced schemas. The application serves the contract and Lab 2 supplies its server address.

`backend/main.py` reads the current setting and checks the submitted amount before looking up the claim in `pay_claim`. The existing approved-amount and held-payment checks still apply after that gate. A decision keeps its captured limit. A senior sign-off records the signer, payment and decided state together under the claim lock and returns the paid record. If a lower current limit newly blocks an older, unpaid original approval, let the same senior action settle it while retaining the decision's saved limit and amount. An original already signed off under the earlier workflow can be settled through that backend action with its saved signer and time. Evaluation copies retain their current completion rules.

The agent uses the backend outcome to finish a resumed, signed-off claim without calling payment. Keep checkpoint compatibility at the pause and payment nodes, including a saved node that predates sign-off. Retire the automatic backend-to-agent sign-off handoff.

### Serving and packaging

`backend/main.py` remains the web-server app and exports `handler = Mangum(app, lifespan="off")`. A static-serving module resolves files from `dashboard/dist` relative to the package. When present, the build supplies assets and the dashboard's navigation fallback. API routing keeps its JSON 404s. The dev setup continues to use Vite as a separate server.

`backend/database.py` selects Lambda pooling when `AWS_LAMBDA_FUNCTION_NAME` is set, keeping the engine reusable and closing connections after each transaction. Use `NullPool`, psycopg `prepare_threshold=None`, and resolve the `undercover` schema within transactions so it works with the Supabase shared pooler on port 6543. Preserve local startup seeding and existing web-server pooling. [Supabase connection modes](https://supabase.com/docs/guides/database/connecting-to-postgres) · [Psycopg prepared statement settings](https://supabase.com/kb/troubleshooting/disabling-prepared-statements-qL8lEL)

## Phase 1 — Require the agent key

Add `UNDERCOVER_API_KEY` to the local `.env` during implementation if absent. Generate it with `secrets.token_urlsafe(32)` and keep it out of output and source control. Apply the authorization rules above and update test clients and the Playwright stand-ins to use explicit fake credentials.

- [x] Agent-only operations return 401 for a missing or wrong key and accept the right key. A valid handler token by itself receives 401 on service-only operations. Tests cover claim writes, run telemetry and service-created rounds and reviews, and audit the route inventory for complete coverage.
- [x] Shared reads accept a valid key or verified handler token, return 401 with neither and preserve Supabase verification. Handler-only reads and actions reject a key by itself. Senior-only actions still refuse ordinary handlers and use the verified senior identity.
- [x] With the key unset, the backend starts, health returns 200, signed-in dashboard requests work and service-only requests return 401. Agent-service startup reports the missing variable before attempting recovery. A CLI-path test proves that the key loaded after module import reaches the backend.
- [x] Playwright tests use a fake key for agent traffic and the Auth stand-in for signed-in handler traffic. Run `npm run check`.

## Phase 2 — Screen customer claims before adjudication

Add the graph entry check and display its result as a labeled security step in the existing claim timeline. Extend `backend/database.py` to seed the additional queue claim, policy and customer from `data/security/`. Keep `data/claims.json` as the original evaluation source and leave the security decision in the lab fixture.

- [ ] A flagged stub result writes a referral for $0 with the screen's reason, `Security check` as the section, and no payment. The screen is the first visible step and the adjudication model receives zero calls. A pass records the first step and proceeds through the existing claim path.
- [ ] Stub tests verify the complete customer input, result field order, Luna with effort `none` and the override with effort omitted. Changing the pinned claim configuration leaves screening behavior intact.
- [ ] Two unreadable results followed by a valid result produce three attributable call records and one screening step. Three unreadable results, a model error and a timeout all block adjudication and follow the existing failure or worker retry rules. Recorded failures identify the security check. Tests check reported costs and unavailable usage, including the evaluation totals.
- [ ] Checkpoint tests resume a passed screen and a paused claim without calling the checker again. Restarting from a first message screens again. Replaying a saved result records one security step and at most one decision for that attempt.
- [ ] A disposable seed has 26 waiting originals, including CLM-1901 with its linked records. Neither evaluation set, full or tiny, includes CLM-1901, and seeded evaluation membership and labels match the baseline. Run `npm run check`.

## Phase 3 — Publish the agent contract and pay on sign-off

Serve `GET /api/agent/openapi.json` with the shared-read dependency. Keep one-line docstrings and required inputs. Move payment on sign-off into the backend transaction, then align the agent's resume behavior and the dashboard's sign-off feedback with that response.

- [ ] The document contains exactly `get_claim`, `get_policy`, `get_claim_history`, `search_documents`, `read_section`, `calculate_payout`, `record_decision` and `pay_claim`. All references resolve, the API rules check passes, payment still takes `amount`, and the document has neither `servers` nor an API-key input. The endpoint accepts either credential and returns 401 with neither.
- [ ] Above-limit payments return the same limit-naming 403 for an unapproved, approved or unknown claim. A changed setting changes that ceiling. An amount exactly at the limit proceeds to normal claim checks. A raised limit leaves a previously held payout subject to its recorded sign-off requirement.
- [ ] Dana signs off a held original with the agent service stopped. The response and a fresh claim read show `decided`, the one payment and Dana's identity and time. A repeat or concurrent sign-off creates no second payment. A regular handler and an evaluation copy cannot use the action. Test the lowered-limit and legacy unpaid sign-off cases.
- [ ] Resuming a saved sign-off pause or stale payment node after backend sign-off records zero payment calls and finishes using the paid backend outcome. Within-limit approvals still pay through the normal path. Playwright verifies the held-to-paid transition and the retained signer. Run `npm run check`.

## Phase 4 — Build and verify the Lambda package

Add `npm run build` for Vite's `dashboard/dist` output. Supply the two public Supabase values from the root `.env` or shell environment and keep the dev and test modes distinct. Keep backend-only credentials out of the browser build. Serve the packaged dashboard and API from one app. Use the existing `server` dependency group and make optional provider initialization lazy where needed so the app imports and serves with the three deployment variables.

- [ ] `npm run build` completes without warnings in both configuration modes and emits the dashboard to `dashboard/dist`. `npm run dev` still starts the three local services. Verify the bundle contains the public Supabase settings and excludes a sentinel backend secret.
- [ ] Synthetic Function URL events return health JSON, the built page for `/` and a deep link such as `/claims/CLM-1001`, the correct asset bytes and content type, and a JSON 404 for an unknown `/api/` route. Verify the Lambda handler serves a database-backed request on a pre-seeded test database with the startup hook skipped and the package directory read-only.
- [ ] With the Lambda marker set, two database-backed requests use separate connections, close them and set `prepare_threshold=None`. Without the marker, local Postgres still uses its bounded pool and SQLite checks pass.
- [ ] Add `uv run --offline --locked --isolated --only-group server python -c "import backend.main"` to `npm run check`. Verify the import succeeds without model credentials and with the project `.venv` intact. [uv command reference](https://docs.astral.sh/uv/reference/cli/#uv-run)
- [ ] In a temporary directory, follow Lab 2's local packaging recipe. Export the locked server group, install Linux x86-64 wheels for Python 3.12 and archive the dependencies, `backend/`, the application data and `dashboard/dist/`. Verify there are no secrets, project virtual environment or agent sources in the archive. Report the actual zip size below 50 MB.
- [ ] Update `docs/SPEC.md` for auth, screening and payment on sign-off, and `data/README.md` for the seed and evaluation membership. Update `docs/TECH_STACK.md` with the build command, output path, package contents, Lambda handler, uvicorn entry point and environment. Update `docs/PROCESS.md` and `docs/STATUS.md` with the checks and evidence. Run `npm run check`.

### Final live verification after phase 4

- [ ] Preserve the local environment, unset `MODEL_NAME` in the file and launching shell, and run `npm run seed` once with the default model. Start `npm run dev`. Confirm 26 waiting claims and an empty Scoreboard after signing in through Playwright MCP. An unauthenticated `curl.exe -s -o NUL -w "%{http_code}" http://127.0.0.1:8000/api/runs/check/threads` in PowerShell prints `401`. On macOS, use `curl` and `/dev/null`.
- [ ] Run `uv run -m agent CLM-1001` and verify its security step and ordinary decision. Start one dispatch for the remaining 25 originals. Inspect CLM-1901 in the dashboard for the first security step and the zero-payout referral with the returned reason. Check the other originals' recorded decisions and report any exceptions.
- [ ] Run one tiny evaluation of the evaluation set. Verify all five copies pass the screen, their model costs include screening, and all originals remain in the queue. Report actual CLI, dispatch and evaluation claim-model spend, judge spend and the combined total.
- [ ] After the dispatch, sign off a held original as Dana. Verify the page shows one payment with her name and the claim is decided. Confirm the signed-off claim's saved payout and the recorded payment match.
- [ ] Stop dev and run the built app alone with `uv run -m uvicorn backend.main:app` at `http://127.0.0.1:8000`. Through Playwright MCP, sign in as Dana, see the queue and refresh a claim deep link. Run it again with `AWS_LAMBDA_FUNCTION_NAME` set and only the function's three application variables, `DATABASE_URL` on the Supabase transaction pooler's port 6543, `VITE_SUPABASE_URL` and `UNDERCOVER_API_KEY`. Verify a signed-in claims read against the same already-seeded database.
- [ ] After the full-branch review, run `npm run check` with `.env` temporarily moved aside. Restore the intended local `.env` byte for byte even if a check fails. Record the observed results, package size, spending and any pending live checks in `docs/STATUS.md` and here.
