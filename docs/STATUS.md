# Status

Pilot phase 1 complete: SQLite storage and an explicit seed loader for the 25 queue claims and their linked records. Decisions, sign-offs, payments and activity have persistent storage. Money is stored as exact cents. The agent and dashboard are still the health-check skeleton.

Verification: 19 Python tests pass, with 99% coverage; Ruff and Pyright pass. Seed tests verify file access restrictions, exact amounts, rollback and preservation of existing work.

Run from the repository root with uv sync, npm install, then npm run dev. The existing health route is GET /api/health. Product APIs follow in phase 2.
