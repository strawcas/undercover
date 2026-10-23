# Status

Pilot phases 1 and 2 complete. The backend seeds and persists the 25 pilot claims and linked context in SQLite. Read APIs expose the queue, claim details, policy wording, instructions, handlers and company settings. Write APIs record decisions, activity, handler sign-offs and simulated payments.

The payment operation enforces the approved amount, one payment per claim, and handler sign-off above USD 1,000. Transactions protect competing requests. Evaluation data and manual-only settings are not exposed.

Verification: 49 Python tests pass with 99% coverage; Ruff and Pyright pass. Live API checks return 25 waiting claims and full context for CLM-1001.

Setup: uv sync and npm install from the root. Run npm run dev for backend http://localhost:8000 and dashboard http://localhost:5173. The database is undercover.db (ignored by git). The dashboard and agent remain the health-check skeleton until subsequent phases.
