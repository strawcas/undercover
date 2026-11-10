import { act, cleanup, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { Run, RunMember, RunStatus } from "./api";
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

it.each([42.5, 0])(
  "shows eight columns, ordered rows and a completed duration of %s",
  async (duration) => {
    showQueue({
      ...running,
      members: [
        {
          ...running.members[0],
          status: "finished",
          claim_status: "decided",
          decision: "decline",
          payout: 0,
          started_at: running.started_at,
          finished_at: "2026-09-29T17:02:55.500+00:00",
          elapsed_seconds: duration,
          llm_cost: 0.000123,
        },
        {
          ...running.members[0],
          claim_id: "CLM-1002",
          customer_name: "Tom Becker",
          llm_cost: null,
        },
      ],
    });
    const view = await panel("Current run");
    const table = within(
      await view.findByRole("table", { name: "Claims in this dispatch" }),
    );
    const headers = table.getAllByRole("columnheader");
    expect(headers.map((header) => header.textContent)).toEqual([
      "Claim",
      "Customer",
      "State",
      "Decision",
      "Payout",
      "Started",
      "Time to finish",
      "Model cost",
    ]);
    expect(
      headers.every((header) => header.getAttribute("scope") === "col"),
    ).toBe(true);
    const rows = table.getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    const completed = within(rows[0]);
    expect(completed.getByRole("link", { name: "CLM-1001" })).toBeDefined();
    expect(
      completed.getAllByRole("cell").map((cell) => cell.textContent),
    ).toEqual([
      "CLM-1001",
      "Maya Patel",
      "Decided",
      "Declined",
      "$0.00",
      expect.stringContaining("2026"),
      `${duration.toFixed(1)} s`,
      "$0.000123",
    ]);
    expect(rows[0].querySelector("time")?.getAttribute("datetime")).toBe(
      running.started_at,
    );
    expect(
      within(rows[1])
        .getAllByRole("cell")
        .map((cell) => cell.textContent),
    ).toEqual([
      "CLM-1002",
      "Tom Becker",
      "Waiting",
      "Pending",
      "—",
      "Not started",
      "Not started",
      "Unavailable",
    ]);
    expect(view.getByText(/Start times use your local time/)).toBeDefined();
    expect(view.getByRole("button", { name: "Start dispatch" })).toBeDefined();
  },
);

it("shows held approvals and their payout without a completed duration", async () => {
  showQueue({
    ...running,
    status: "finished",
    members: [
      {
        ...running.members[0],
        status: "finished",
        claim_status: "waiting_for_sign_off",
        decision: "approve",
        payout: 1350,
        started_at: running.started_at,
      },
    ],
  });
  const view = await panel("Current run");
  const table = within(
    await view.findByRole("table", { name: "Claims in this dispatch" }),
  );
  expect(table.getByText("Waiting for sign-off")).toBeDefined();
  expect(table.getByText("Approved")).toBeDefined();
  expect(table.getByText("$1,350.00")).toBeDefined();
  expect(table.getByText("Awaiting sign-off")).toBeDefined();
  expect(table.getByText("$0.000000")).toBeDefined();
});

const unfinished: {
  changes: Partial<RunMember>;
  runStatus: RunStatus;
  duration: string;
}[] = [
  { changes: {}, runStatus: "running", duration: "Not started" },
  {
    changes: { status: "skipped" },
    runStatus: "finished",
    duration: "Not worked",
  },
  {
    changes: { claim_status: "in_progress", started_at: running.started_at },
    runStatus: "running",
    duration: "In progress",
  },
  {
    changes: {
      claim_status: "in_progress",
      started_at: running.started_at,
      decision: "approve",
      payout: 420,
    },
    runStatus: "finished",
    duration: "Awaiting payment",
  },
  {
    changes: {
      claim_status: "left_for_handler",
      started_at: running.started_at,
    },
    runStatus: "finished",
    duration: "Needs handler",
  },
  {
    changes: { claim_status: "in_progress", started_at: running.started_at },
    runStatus: "stopped",
    duration: "Not completed",
  },
  {
    changes: { claim_status: "decided", decision: "refer", payout: 0 },
    runStatus: "finished",
    duration: "Unavailable",
  },
  {
    changes: {
      claim_status: "decided",
      decision: "approve",
      payout: 420,
      finished_at: running.started_at,
    },
    runStatus: "finished",
    duration: "Unavailable",
  },
];

it.each(unfinished)(
  "labels unfinished or historical timing: $duration",
  async ({ changes, runStatus, duration }) => {
    showQueue({
      ...running,
      status: runStatus,
      members: [{ ...running.members[0], ...changes }],
    });
    const view = await panel("Current run");
    const table = within(
      await view.findByRole("table", { name: "Claims in this dispatch" }),
    );
    const cells = within(table.getAllByRole("row")[1]).getAllByRole("cell");
    expect(cells[6].textContent).toBe(duration);
  },
);
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
