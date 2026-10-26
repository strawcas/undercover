import { defineConfig } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { tmpdir } from "node:os";
import { join } from "node:path";

process.env.UNDERCOVER_TEST_GATE ??= join(
  tmpdir(),
  `undercover-command-${randomUUID()}`,
);

export default defineConfig({
  testDir: "e2e",
  outputDir: "e2e/test-results",
  reporter: "list",
  workers: 1,
  use: { baseURL: "http://localhost:5174" },
  webServer: [
    {
      command: "uv run -m backend.tests.serve",
      url: "http://127.0.0.1:8011/api/health",
      reuseExistingServer: false,
      env: { UNDERCOVER_TEST_GATE: process.env.UNDERCOVER_TEST_GATE },
    },
    {
      command:
        "node node_modules/vite/bin/vite.js dashboard --port 5174 --strictPort",
      url: "http://localhost:5174",
      env: { UNDERCOVER_API_PROXY: "http://127.0.0.1:8011" },
      reuseExistingServer: false,
    },
  ],
});
