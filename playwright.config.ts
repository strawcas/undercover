import { defineConfig } from "@playwright/test";
import { AGENT_STAND_IN, AUTH_STAND_IN } from "./e2e/addresses";

// The tests run their own backend and dashboard, on their own ports and
// database, so they never touch the servers started by `npm run dev`.
export default defineConfig({
  testDir: "e2e",
  outputDir: "e2e/test-results",
  reporter: "list",
  use: { baseURL: "http://localhost:5174" },
  webServer: [
    {
      command:
        "uv run -m backend.seed && uv run -m uvicorn backend.main:app --port 8001",
      url: "http://127.0.0.1:8001/api/health",
      // An empty DATABASE_URL keeps the tests on SQLite, whatever the shell has.
      env: {
        UNDERCOVER_DB: "e2e/undercover.db",
        DATABASE_URL: "",
        UNDERCOVER_AGENT: AGENT_STAND_IN,
        VITE_SUPABASE_URL: AUTH_STAND_IN,
      },
    },
    {
      command: "node e2e/auth_stand_in.ts",
      url: `${AUTH_STAND_IN}/auth/v1/.well-known/jwks.json`,
    },
    {
      command: "node e2e/agent_stand_in.ts",
      url: `${AGENT_STAND_IN}/api/health`,
      env: { PORT: new URL(AGENT_STAND_IN).port },
    },
    {
      command: "npx vite dashboard --port 5174 --strictPort",
      url: "http://localhost:5174",
      env: {
        UNDERCOVER_API: "http://127.0.0.1:8001",
        VITE_SUPABASE_URL: AUTH_STAND_IN,
        VITE_SUPABASE_PUBLISHABLE_KEY: "offline-publishable",
        NODE_ENV: "test",
      },
    },
  ],
});
