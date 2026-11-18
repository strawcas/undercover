import { defineConfig } from "@playwright/test";
import { TEST_AGENT_KEY } from "./e2e/agent_credentials";
import {
  AGENT_STAND_IN,
  AUTH_STAND_IN,
  JUDGE_STAND_IN,
  TEST_BACKEND,
} from "./e2e/addresses";

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
      url: `${TEST_BACKEND}/api/health`,
      // An empty DATABASE_URL keeps the tests on SQLite, whatever the shell has.
      env: {
        UNDERCOVER_DB: "e2e/undercover.db",
        DATABASE_URL: "",
        UNDERCOVER_AGENT: AGENT_STAND_IN,
        UNDERCOVER_API_KEY: TEST_AGENT_KEY,
        VITE_SUPABASE_URL: AUTH_STAND_IN,
        OPENROUTER_DECISIONS_URL: JUDGE_STAND_IN,
        OPENROUTER_API_KEY: "offline",
        MODEL_NAME: "",
      },
    },
    {
      command: "node e2e/auth_stand_in.ts",
      url: `${AUTH_STAND_IN}/auth/v1/.well-known/jwks.json`,
    },
    {
      command: "node e2e/judge_stand_in.ts",
      url: `${JUDGE_STAND_IN}/health`,
    },
    {
      command: "node e2e/agent_stand_in.ts",
      url: `${AGENT_STAND_IN}/api/health`,
      env: { PORT: new URL(AGENT_STAND_IN).port, UNDERCOVER_API: TEST_BACKEND },
    },
    {
      command: "npx vite dashboard --port 5174 --strictPort",
      url: "http://localhost:5174",
      env: {
        UNDERCOVER_API: TEST_BACKEND,
        VITE_SUPABASE_URL: AUTH_STAND_IN,
        VITE_SUPABASE_PUBLISHABLE_KEY: "offline-publishable",
        NODE_ENV: "test",
      },
    },
  ],
});
