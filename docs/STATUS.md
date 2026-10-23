# Status

Pilot phases 1-3 complete. The FastAPI backend seeds 25 claims and linked context in ignored undercover.db. APIs expose the full queue, claims, policy wording, read-only agent instructions, handlers and company settings. Decisions, activity, sign-offs and simulated payments persist in SQLite.

POST /api/claims/{claim_id}/runs starts a local background agent task. LangChain create_agent reads all context through HTTP, records its decision and requests eligible simulated payments. Progress events are available while it works. Payment above USD 1,000 is blocked until handler sign-off. Failed or interrupted runs return to waiting when no decision exists; committed decisions and payments are preserved.

Setup: uv sync and npm install. Run npm run dev; backend http://localhost:8000, dashboard http://localhost:5173. OPENROUTER_API_KEY is required in .env for real runs; MODEL_NAME optionally overrides openai/gpt-6-luna. UNDERCOVER_DB_PATH and UNDERCOVER_BACKEND_URL optionally configure local storage and agent HTTP connections.

Verification: 64 Python tests pass, with 100% coverage. Ruff and Pyright pass. Automated tests use scripted models and temporary databases. The dashboard remains the health-check skeleton pending phases 4-5. No durable execution, sign-in, real payments, customer messages or cloud resources.
