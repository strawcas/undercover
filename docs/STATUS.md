# Status

Pilot phases 1-4 complete. The backend and HTTP-only LangChain agent persist and process the 25 pilot claims. The dashboard shows the queue, all claim and document details, linked policy and customer records, decisions, and read-only agent instructions in UnderCover's light and dark styles.

Verification: 64 Python tests with 100% coverage. Eight dashboard tests pass with 100% statement coverage; formatting, lint and type checks pass. Playwright MCP verified the live queue, all records, exact instruction text, keyboard focus and desktop/390px light and dark layouts with no horizontal overflow. Screenshots are in the ignored .playwright-mcp directory.

Setup: uv sync and npm install. Run npm run dev; backend http://localhost:8000, dashboard http://localhost:5173. OPENROUTER_API_KEY in .env enables real agent runs; MODEL_NAME overrides openai/gpt-6-luna. The ignored SQLite file is undercover.db. UNDERCOVER_DB_PATH and UNDERCOVER_BACKEND_URL optionally configure local storage and agent HTTP connections.

Next: dashboard actions, live activity and full-stack tests. Payments remain simulated. The backend blocks payment above USD 1,000 until handler sign-off. No sign-in, durable agent execution, evaluation scoring, customer messages or cloud resources.
