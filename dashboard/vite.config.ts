import { existsSync, readFileSync } from "node:fs";
import { parseEnv } from "node:util";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const PUBLIC_ENV = ["VITE_SUPABASE_URL", "VITE_SUPABASE_PUBLISHABLE_KEY"];

function buildEnv() {
  const file = new URL("../.env", import.meta.url);
  const values = existsSync(file) ? parseEnv(readFileSync(file, "utf8")) : {};
  return Object.fromEntries(
    PUBLIC_ENV.map((key) => [
      `import.meta.env.${key}`,
      JSON.stringify(values[key] ?? process.env[key] ?? ""),
    ]),
  );
}

export default defineConfig(({ command, mode }) => ({
  define: command === "build" && mode !== "test" ? buildEnv() : {},
  envPrefix: command === "build" && mode !== "test" ? [] : "VITE_",
  build: {
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            { name: "supabase", test: /node_modules[\\/]@supabase[\\/]/ },
          ],
        },
      },
    },
  },
  envDir:
    command === "serve" &&
    mode === "development" &&
    process.env.NODE_ENV !== "test"
      ? ".."
      : false,
  plugins: [react()],
  server: {
    proxy: { "/api": process.env.UNDERCOVER_API ?? "http://127.0.0.1:8000" },
  },
  test: {
    environment: "jsdom",
    coverage: {
      include: ["src/**"],
      exclude: ["src/main.tsx"],
      thresholds: { statements: 80 },
    },
  },
}));
