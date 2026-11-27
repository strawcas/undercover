import {
  act,
  cleanup,
  fireEvent,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { evaluation, Refusal, renderAt, stubApi, type Routes } from "./testing";
import { REFRESH_MS } from "./useLoad";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it("shows the empty scoreboard and forwards set, size and concurrency", async () => {
  const fetch = stubApi({
    "GET /api/evaluations": [],
    "POST /api/evaluations": { status: "accepted" },
  });
  renderAt("/scoreboard");
  expect(await screen.findByText(/No evaluations yet/)).toBeDefined();
  const n = screen.getByRole("spinbutton", { name: "Agents at once" });
  expect((n as HTMLInputElement).value).toBe("3");
  fireEvent.change(screen.getByLabelText("Claim set"), {
    target: { value: "held_out" },
  });
  fireEvent.change(screen.getByLabelText("Selection"), {
    target: { value: "tiny" },
  });
  fireEvent.change(n, { target: { value: "5" } });
  fireEvent.click(screen.getByRole("button", { name: "Start evaluation" }));
  expect(await screen.findByText("Evaluation accepted.")).toBeDefined();
  expect(fetch).toHaveBeenCalledWith(
    "/api/evaluations",
    expect.objectContaining({
      body: JSON.stringify({
        set_name: "held_out",
        tiny: true,
        concurrency: 5,
      }),
    }),
  );
});

it("shows all evaluation measures and refreshes running results", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const routes: Routes = {
    "GET /api/evaluations": [
      {
        ...evaluation,
        status: "running",
        finished_at: null,
        reasoning_effort: "low",
      },
    ],
  };
  stubApi(routes);
  renderAt("/scoreboard");
  expect(await screen.findByText("Running")).toBeDefined();
  expect(screen.getByText(/Reasoning: low/)).toBeDefined();
  const list = screen.getByRole("list", { name: "Evaluations" });
  for (const label of [
    "Decision match",
    "Average explanation grade",
    "Total judge cost",
    "Accuracy within $1",
    "Cost of mistakes",
    "Straight through",
    "Model cost / claim",
    "Active time / claim",
    "Run elapsed time",
    "Total model cost",
    "Total active claim time",
  ]) {
    expect(within(list).getByText(label)).toBeDefined();
  }
  expect(within(list).getByText("$0.000500")).toBeDefined();
  routes["GET /api/evaluations"] = [{ ...evaluation, status: "stopped" }];
  await act(() => vi.advanceTimersByTimeAsync(REFRESH_MS));
  expect(screen.getByText("Stopped")).toBeDefined();
  expect(screen.getByText(/Reasoning: default/)).toBeDefined();
});

it.each([409, 503])("shows evaluation start refusal %s", async (status) => {
  stubApi({
    "GET /api/evaluations": [],
    "POST /api/evaluations": new Refusal(status, "Service refusal"),
  });
  renderAt("/scoreboard");
  fireEvent.click(
    await screen.findByRole("button", { name: "Start evaluation" }),
  );
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Service refusal",
  );
});

it("shows load errors", async () => {
  stubApi({
    "GET /api/evaluations": new Refusal(500, "Cannot load evaluations"),
  });
  renderAt("/scoreboard");
  expect(
    within(await screen.findByRole("alert")).getByText(
      "Cannot load evaluations",
    ),
  ).toBeDefined();
});

it("shows readable matches, misses and absent decisions beside handlers", async () => {
  stubApi({ "GET /api/evaluations/eval-1": evaluation });
  renderAt("/scoreboard/eval-1");
  expect(await screen.findByText("Evaluation results")).toBeDefined();
  const table = screen.getByRole("table");
  expect(within(table).getByText("Miss")).toBeDefined();
  expect(within(table).getByText("Match")).toBeDefined();
  expect(
    within(table).getByText("No decision (scored as referred)"),
  ).toBeDefined();
  expect(
    within(table).getByText("The policy excludes this event."),
  ).toBeDefined();
  expect(within(table).getByText("Unavailable")).toBeDefined();
  expect(within(table).getByText("A").className).toBe("chip grade-A");
  expect(within(table).getByText("1.60 / 3")).toBeDefined();
  expect(within(table).getByText("F").className).toBe("chip grade-F");
  expect(within(table).getByText("0.00 / 3")).toBeDefined();
  expect(within(table).getByText("Same grounds: Yes")).toBeDefined();
  expect(within(table).getAllByText("Same section: No")).toHaveLength(2);
  expect(within(table).getByText("The delay is covered.")).toBeDefined();
  expect(
    within(table).getByRole("link", { name: "CLM-1001" }).getAttribute("href"),
  ).toBe("/claims/EVAL-1");
});

it("identifies missing evaluations", async () => {
  stubApi({});
  renderAt("/scoreboard/missing");
  expect(await screen.findByText("Evaluation not found")).toBeDefined();
});

it("shows unavailable grading without inventing a score or a check", async () => {
  const comparisons = evaluation.comparisons.map((item) => ({
    ...item,
    grade: null,
    letter: null,
    same_grounds: null,
    same_section: null,
  }));
  stubApi({
    "GET /api/evaluations/eval-1": {
      ...evaluation,
      scores: { ...evaluation.scores, grade: null },
      judge_cost: null,
      comparisons,
    },
  });
  renderAt("/scoreboard/eval-1");
  await screen.findByText("Evaluation results");
  const table = screen.getByRole("table");
  expect(within(table).getAllByText("Not graded")).toHaveLength(2);
  expect(within(table).getAllByText("Same grounds: Not graded")).toHaveLength(
    2,
  );
  expect(within(table).queryByText("0.00 / 3")).toBeNull();
});
