import { defineConfig } from "@playwright/test";

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
    },
    {
      command: "npx vite dashboard --port 5174 --strictPort",
      url: "http://localhost:5174",
      env: { UNDERCOVER_API_PROXY: "http://127.0.0.1:8011" },
      reuseExistingServer: false,
    },
  ],
});
