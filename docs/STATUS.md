# Status

Pilot phases 1-5 complete. The claims dashboard now runs the agent, polls live activity, displays decisions and policy references, collects handler sign-off, and records eligible simulated payments. The SQLite backend independently enforces exact amounts, a single payment and sign-off above USD 1,000.

Verification: 64 Python tests pass with 100% coverage; 14 dashboard tests pass with 99.21% statement coverage; six full-stack Playwright tests pass. Browser checks confirmed live progress, sign-off by Dana Whitfield, a USD 1,250 simulated payment, persistence after refresh, and zero browser console errors. Tests use temporary databases and scripted models with no provider calls.

Setup: uv sync and npm install. Run npm run dev for backend http://localhost:8000 and dashboard http://localhost:5173. OPENROUTER_API_KEY in .env enables real runs; MODEL_NAME optionally overrides openai/gpt-6-luna. Local SQLite storage is undercover.db (ignored). UNDERCOVER_DB_PATH and UNDERCOVER_BACKEND_URL optionally configure local storage and agent HTTP connections.

Playwright starts isolated services on backend port 8011 and dashboard port 5174, separate from development. Progress polling stops after completion and on navigation. Interrupted runs can be retried; committed decisions and payments survive. No sign-in, durable execution, real payments, customer messages, evaluation scoring or cloud resources. Remaining: final review, setup verification and handover documentation.
