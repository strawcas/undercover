import { act, cleanup } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { Run } from "./api";
import { summary, panel, renderAt, stubApi } from "./testing";
import { REFRESH_MS } from "./useLoad";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
const running: Run = {
  run_id: "run-1",
  kind: "dispatch",
  concurrency: 3,
  model: "stub",
  reasoning_effort: null,
  set_name: null,
  tiny: null,
  status: "running",
  started_at: "2026-09-29T17:02:13+00:00",
  finished_at: null,
  summary: null,
  llm_cost: 0.000123,
  active_seconds: 6,
  seconds: 3,
  members: [
    {
      claim_id: "CLM-1001",
      customer_name: "Maya Patel",
      status: "selected",
      error: null,
      claim_status: "waiting",
      decision: null,
      payout: null,
      reason: null,
      llm_cost: 0,
      active_seconds: 0,
      started_at: null,
      finished_at: null,
      elapsed_seconds: null,
    },
  ],
};
function showQueue(run: Run | null) {
  const routes = { "GET /api/claims": [summary], "GET /api/runs/latest": run };
  stubApi(routes);
  renderAt("/");
  return routes;
}
it("says when no dispatch exists", async () => {
  showQueue(null);
  expect(
    await (await panel("Current run")).findByText("No run yet."),
  ).toBeDefined();
});
it("shows linked members and precise run measurements", async () => {
  showQueue(running);
  const view = await panel("Current run");
  expect(await view.findByText("Running")).toBeDefined();
  expect(view.getByText(/3 agents at once/)).toBeDefined();
  expect(view.getByText(/Model cost: \$0.000123/)).toBeDefined();
  expect(
    view.getByRole("link", { name: "CLM-1001" }).getAttribute("href"),
  ).toBe("/claims/CLM-1001");
  expect(view.getByText("Waiting")).toBeDefined();
});
it("shows stopped runs, skips, errors and unavailable cost", async () => {
  showQueue({
    ...running,
    status: "stopped",
    llm_cost: null,
    members: [{ ...running.members[0], status: "skipped", error: "Timeout" }],
  });
  const view = await panel("Current run");
  expect(await view.findByText("Stopped")).toBeDefined();
  expect(view.getByText(/Unavailable/)).toBeDefined();
  expect(view.getByText(/Already being resumed/)).toBeDefined();
  expect(view.getByText("Timeout")).toBeDefined();
});
it("refreshes progress and the records-built summary", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const routes = showQueue(running);
  const view = await panel("Current run");
  expect(await view.findByText("Waiting")).toBeDefined();
  routes["GET /api/runs/latest"] = {
    ...running,
    status: "finished",
    finished_at: "2026-09-29T17:03:00+00:00",
    summary: "Worked 1 claim: 1 referred.",
    members: [],
  };
  await act(() => vi.advanceTimersByTimeAsync(REFRESH_MS));
  expect(view.getByText("Finished")).toBeDefined();
  expect(view.getByText("No claims selected.")).toBeDefined();
  expect(view.getByText("Worked 1 claim: 1 referred.")).toBeDefined();
});
