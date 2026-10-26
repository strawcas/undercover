import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { RunProgress } from "./RunProgress";
import { run } from "./testData";
import type { AgentRun, Todo } from "./api";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it("restores a changing stored plan and a completion-time summary without starting work", async () => {
  vi.useFakeTimers();
  let current = run;
  const fetcher = vi.fn(() =>
    Promise.resolve(new Response(JSON.stringify({ run: current }))),
  );
  vi.stubGlobal("fetch", fetcher);
  const select = vi.fn();
  await act(async () => {
    render(<RunProgress onSelect={select} />);
    await Promise.resolve();
  });
  expect(screen.getByText(/has not added its first to-do/)).toBeDefined();
  const statuses: Todo["status"][] = [
    "to_do",
    "in_progress",
    "done",
    "left_for_handler",
  ];
  current = {
    ...run,
    todos: statuses.map((status, index) => ({
      id: String(index),
      claim_id: `TEST-${String(index)}`,
      text: "Assess claim",
      status,
      handoff_reason:
        status === "left_for_handler" ? "technical_failure" : null,
    })),
  };
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });
  for (const status of ["To do", "In progress", "Done", "Left for a handler"])
    expect(screen.getByText(status)).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "Review TEST-3" }));
  expect(select).toHaveBeenCalledWith("TEST-3");
  expect(screen.getByText(/Two failed assessments/)).toBeDefined();
  current = {
    ...current,
    status: "completed",
    finished_at: run.started_at,
    summary: {
      text: "Worked 5 claims. One needs handler review.",
      detail: "",
      counts: run.counts,
    },
  };
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });
  expect(screen.getByText("Latest run")).toBeDefined();
  expect(screen.getByText(/Completion-time snapshot/)).toBeDefined();
  expect(fetcher).toHaveBeenCalledWith("/api/agent-runs/current", undefined);
});

it("shows distinct business handoffs and an interrupted named run", async () => {
  const handoffs: Todo["handoff_reason"][] = [
    "sign_off",
    "referral",
    "unpaid_approval",
  ];
  const current: AgentRun = {
    ...run,
    scope: { mode: "claim", claim_id: "TEST-1" },
    status: "interrupted",
    todos: handoffs.map((reason, index) => ({
      id: String(index),
      claim_id: String(index),
      text: "Review claim",
      status: "left_for_handler",
      handoff_reason: reason,
    })),
    summary: {
      text: "Partial results",
      detail: "Queue execution was interrupted.",
      counts: run.counts,
    },
  };
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response(JSON.stringify({ run: current }))),
  );
  render(<RunProgress onSelect={vi.fn()} />);
  expect(await screen.findByText(/One claim: TEST-1/)).toBeDefined();
  expect(screen.getByText("Payout needs handler sign-off")).toBeDefined();
  expect(
    screen.getByText("Referred for judgment or more information"),
  ).toBeDefined();
  expect(
    screen.getByText("Approved payout needs payment review"),
  ).toBeDefined();
  expect(screen.getByText("Queue execution was interrupted.")).toBeDefined();
});

it("recovers from a failed read and stops polling after unmount", async () => {
  vi.useFakeTimers();
  const fetcher = vi
    .fn()
    .mockRejectedValueOnce(new Error("Offline"))
    .mockImplementation(() =>
      Promise.resolve(new Response(JSON.stringify({ run: null }))),
    );
  vi.stubGlobal("fetch", fetcher);
  let view: ReturnType<typeof render> | undefined;
  await act(async () => {
    view = render(<RunProgress onSelect={vi.fn()} />);
    await Promise.resolve();
  });
  expect(screen.getByRole("alert").textContent).toContain("Offline");
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Reload run" }));
    await Promise.resolve();
  });
  expect(screen.getByText(/No run yet/)).toBeDefined();
  view?.unmount();
  const calls = fetcher.mock.calls.length;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(fetcher).toHaveBeenCalledTimes(calls);
});
