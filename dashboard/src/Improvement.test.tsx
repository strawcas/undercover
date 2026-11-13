import {
  act,
  cleanup,
  fireEvent,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, type Round } from "./api";
import {
  changeProposal,
  configuration,
  evaluation,
  mockDialogs,
  recipe,
  recipeProposal,
  Refusal,
  renderAt,
  round,
  signedIn,
  stubApi,
  type Routes,
} from "./testing";
import { REFRESH_MS } from "./useLoad";

beforeEach(mockDialogs);
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function routes(rounds: Round[] = [round]): Routes {
  return {
    "GET /api/rounds": rounds,
    "GET /api/recipes": [recipe],
    "GET /api/configurations": [configuration],
  };
}
async function selectedRound() {
  return within(await screen.findByRole("region", { name: "Selected round" }));
}
async function dialog() {
  return within(await screen.findByRole("dialog"));
}

it("shows the empty history, recipe and explicit chart scales", async () => {
  stubApi(routes([]));
  renderAt("/improvement");
  expect(await screen.findByText(/No rounds yet/)).toBeDefined();
  expect(screen.getByRole("button", { name: "Start round" })).toHaveProperty(
    "disabled",
    false,
  );
  expect(
    screen.getByRole("img", { name: "Held-out accuracy (0–100%)" }),
  ).toBeDefined();
  expect(
    screen.getByRole("img", { name: "Held-out judge grade (0–3)" }),
  ).toBeDefined();
  expect(
    screen.getByRole("table", { name: "Version performance values" }),
  ).toBeDefined();
  expect(screen.getByText("Recipe version 1 · UnderCover")).toBeDefined();
});

it("confirms and cancels a new round before sending it", async () => {
  const data = routes([]);
  data["POST /api/rounds"] = { status: "accepted", round_id: round.round_id };
  const fetch = stubApi(data);
  renderAt("/improvement");
  await screen.findByText(/No rounds yet/);
  fireEvent.click(screen.getByRole("button", { name: "Start round" }));
  fireEvent.click((await dialog()).getByRole("button", { name: "Cancel" }));
  expect(
    fetch.mock.calls.filter(([, init]) => init?.method === "POST"),
  ).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "Start round" }));
  data["GET /api/rounds"] = [
    { ...round, status: "baseline", proposals: [], progress: 1 },
  ];
  fireEvent.click(
    (await dialog()).getByRole("button", { name: "Start round" }),
  );
  expect(await screen.findByText(/Round accepted/)).toBeDefined();
  expect(await screen.findByRole("progressbar")).toHaveProperty("value", 1);
  expect(fetch).toHaveBeenCalledWith(
    "/api/rounds",
    expect.objectContaining({ method: "POST", body: "{}" }),
  );
});

it("requires rejection feedback and preserves it through cancellation and a busy refusal", async () => {
  const data = routes();
  data["POST /api/rounds/round-current/proposals/proposal-one/decision"] =
    new Refusal(409, "A dispatch is running");
  const fetch = stubApi(data);
  renderAt("/improvement");
  const panel = await selectedRound();
  expect(panel.getByRole("button", { name: "Reject proposal" })).toHaveProperty(
    "disabled",
    true,
  );
  fireEvent.change(panel.getByLabelText("Rejection reason"), {
    target: { value: "Keep the change general" },
  });
  fireEvent.click(panel.getByRole("button", { name: "Reject proposal" }));
  expect((await dialog()).getByText(/Keep the change general/)).toBeDefined();
  fireEvent.click((await dialog()).getByRole("button", { name: "Cancel" }));
  expect(panel.getByLabelText("Rejection reason")).toHaveProperty(
    "value",
    "Keep the change general",
  );
  fireEvent.click(panel.getByRole("button", { name: "Reject proposal" }));
  fireEvent.click(
    (await dialog()).getByRole("button", { name: "Reject proposal" }),
  );
  expect(await screen.findByText("A dispatch is running")).toBeDefined();
  expect(screen.queryByText(/Rejection and feedback recorded/)).toBeNull();
  expect(fetch).toHaveBeenCalledWith(
    "/api/rounds/round-current/proposals/proposal-one/decision",
    expect.objectContaining({
      body: JSON.stringify({
        decision: "reject",
        reason: "Keep the change general",
      }),
    }),
  );
});

it.each(["approve", "reject"] as const)(
  "records a confirmed %s decision and waits for progress",
  async (decision) => {
    const data = routes();
    data["POST /api/rounds/round-current/proposals/proposal-one/decision"] = {
      status: "accepted",
    };
    stubApi(data);
    renderAt("/improvement");
    const panel = await selectedRound();
    if (decision === "reject")
      fireEvent.change(panel.getByLabelText("Rejection reason"), {
        target: { value: "Explain the rule" },
      });
    fireEvent.click(
      panel.getByRole("button", {
        name: decision === "approve" ? "Approve testing" : "Reject proposal",
      }),
    );
    fireEvent.click(
      (await dialog()).getByRole("button", {
        name: decision === "approve" ? "Approve proposal" : "Reject proposal",
      }),
    );
    expect(
      await screen.findByText(
        decision === "approve"
          ? /Approval recorded/
          : /Rejection and feedback recorded/,
      ),
    ).toBeDefined();
  },
);

it("shows recipe proposals through the same diff and confirms approval", async () => {
  const data = routes([
    { ...round, status: "awaiting_recipe", proposals: [recipeProposal] },
  ]);
  data["POST /api/rounds/round-current/proposals/recipe-one/decision"] = {
    status: "accepted",
  };
  stubApi(data);
  renderAt("/improvement");
  const panel = await selectedRound();
  expect(panel.getByText("+Use development evidence.")).toHaveProperty(
    "className",
    "diff-add",
  );
  fireEvent.click(panel.getByRole("button", { name: "Approve recipe" }));
  expect(
    (await dialog()).getByText(/available to future rounds/),
  ).toBeDefined();
  fireEvent.click(
    (await dialog()).getByRole("button", { name: "Approve proposal" }),
  );
  expect(await screen.findByText(/Approval recorded/)).toBeDefined();
});

it("confirms stopping without implying in-flight work is already drained", async () => {
  const data = routes();
  data["POST /api/rounds/round-current/stop"] = { status: "accepted" };
  stubApi(data);
  renderAt("/improvement");
  fireEvent.click(
    (await selectedRound()).getByRole("button", { name: "Stop round" }),
  );
  fireEvent.click((await dialog()).getByRole("button", { name: "Stop round" }));
  expect(
    await screen.findByText(/Work already in hand will drain/),
  ).toBeDefined();
});

it("explains senior permissions and provides history without action controls", async () => {
  stubApi(routes());
  renderAt("/improvement", {
    ...signedIn,
    handler: signedIn.handler
      ? { ...signedIn.handler, can_approve_over_limit: false }
      : null,
  });
  await selectedRound();
  expect(
    screen.getByText(/A senior handler is required to start or stop/),
  ).toBeDefined();
  expect(
    screen.queryByRole("button", {
      name: /Approve|Reject|Start round|Stop round/,
    }),
  ).toBeNull();
  expect(screen.queryByLabelText("Rejection reason")).toBeNull();
});

it.each(["baseline", "working", "evaluating", "finished", "stopped"] as const)(
  "renders the %s state with saved versions and spending",
  async (status) => {
    stubApi(
      routes([
        { ...round, status, progress: 2, candidate_version: 2, proposals: [] },
      ]),
    );
    renderAt("/improvement");
    const panel = await selectedRound();
    expect(panel.getByText("$0.033000")).toBeDefined();
    expect(panel.getByText("$0.467000")).toBeDefined();
    expect(panel.getByText("v2")).toBeDefined();
    fireEvent.click(panel.getByText("Settings captured for this round"));
    expect(panel.getByText("Accuracy (percentage points)")).toBeDefined();
  },
);

it("keeps selected history and unsaved feedback focused during polls", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const data = routes();
  stubApi(data);
  renderAt("/improvement");
  const input = (await selectedRound()).getByLabelText("Rejection reason");
  input.focus();
  fireEvent.change(input, { target: { value: "Keep my feedback" } });
  data["GET /api/rounds"] = [
    { ...round, spending: { ...round.spending, total: 0.04 } },
  ];
  await act(async () => {
    await vi.advanceTimersByTimeAsync(REFRESH_MS);
  });
  expect(document.activeElement).toBe(input);
  expect(input).toHaveProperty("value", "Keep my feedback");
  expect((await selectedRound()).getByText("$0.040000")).toBeDefined();
});

it.each(["baseline", "evaluating"] as const)(
  "shows %s evaluation progress out of three",
  async (status) => {
    stubApi(routes([{ ...round, status, progress: 2, proposals: [] }]));
    renderAt("/improvement");
    expect((await selectedRound()).getByRole("progressbar")).toHaveProperty(
      "value",
      2,
    );
  },
);

it("keeps an explicitly selected older round during refresh", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const older: Round = {
    ...round,
    round_id: "older-round",
    status: "finished",
    proposals: [],
    candidate_version: null,
  };
  const data = routes([round, older]);
  stubApi(data);
  renderAt("/improvement");
  await selectedRound();
  fireEvent.click(screen.getByRole("button", { name: "Review older-ro" }));
  expect(
    (await selectedRound()).getByRole("heading", { name: "Round older-ro" }),
  ).toBeDefined();
  data["GET /api/rounds"] = [{ ...round, progress: 1 }, older];
  await act(async () => {
    await vi.advanceTimersByTimeAsync(REFRESH_MS);
  });
  expect(
    (await selectedRound()).getByRole("heading", { name: "Round older-ro" }),
  ).toBeDefined();
  expect(
    screen.getByRole("button", { name: "Review open round" }),
  ).toBeDefined();
});

it("waits for a deferred poll before refreshing a confirmed approval", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  let finish: ((value: Round[]) => void) | undefined;
  const pending = new Promise<Round[]>((resolve) => {
    finish = resolve;
  });
  const data = routes();
  data["POST /api/rounds/round-current/proposals/proposal-one/decision"] = {
    status: "accepted",
  };
  stubApi(data);
  const updated: Round = {
    ...round,
    status: "working",
    proposals: [{ ...changeProposal, decision: "approve", version: 2 }],
  };
  const reads = vi
    .spyOn(api, "listRounds")
    .mockResolvedValueOnce([round])
    .mockReturnValueOnce(pending)
    .mockResolvedValue([updated]);
  renderAt("/improvement");
  const panel = await selectedRound();
  await act(() => vi.advanceTimersByTimeAsync(REFRESH_MS));
  expect(reads).toHaveBeenCalledTimes(2);
  fireEvent.click(panel.getByRole("button", { name: "Approve testing" }));
  fireEvent.click(
    (await dialog()).getByRole("button", { name: "Approve proposal" }),
  );
  await screen.findByText(/Approval recorded/);
  expect(reads).toHaveBeenCalledTimes(2);
  await act(async () => {
    finish?.([round]);
    await pending;
  });
  expect(reads).toHaveBeenCalledTimes(3);
  expect((await selectedRound()).getByText("Improver working")).toBeDefined();
});

it("retains rollback decisions, unavailable usage and missing grade alongside zero", async () => {
  const data = routes([
    {
      ...round,
      status: "finished",
      candidate_version: 2,
      verdict: "rolled_back",
      explanation: "The grade was unavailable",
      after: { ...evaluation.scores, accuracy: 0, grade: null },
      spending: { ...round.spending, unknown: true },
      proposals: [
        {
          ...changeProposal,
          decision: "reject",
          feedback: "Broaden the instructions",
          handler: "Dana Whitfield",
          decided_at: round.started_at,
        },
      ],
    },
  ]);
  data["GET /api/configurations"] = [
    {
      ...configuration,
      held_out_scores: { ...evaluation.scores, accuracy: 0, grade: 0 },
    },
    { ...configuration, version: 2 },
  ];
  stubApi(data);
  renderAt("/improvement");
  const panel = await selectedRound();
  expect(panel.getByText("The grade was unavailable")).toBeDefined();
  expect(panel.getByText(/known minimum/)).toBeDefined();
  fireEvent.click(panel.getByText("Configuration · Rejected"));
  expect(panel.getByText(/Broaden the instructions/)).toBeDefined();
  expect(screen.getAllByText("0.00 / 3").length).toBeGreaterThan(0);
  expect(screen.getAllByText("Not graded").length).toBeGreaterThan(0);
});

it("loads actionable errors and retries without losing the page", async () => {
  const data = routes();
  data["GET /api/rounds"] = new Refusal(503, "Temporarily unavailable");
  stubApi(data);
  renderAt("/improvement");
  expect(
    await screen.findByText(/Could not refresh improvement history/),
  ).toBeDefined();
  data["GET /api/rounds"] = [round];
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(await selectedRound()).toBeDefined();
});

it.each(["finished", "stopped"] as const)(
  "uses terminal labels for a %s round without a candidate",
  async (status) => {
    stubApi(
      routes([
        {
          ...round,
          status,
          before: null,
          after: null,
          proposals: status === "stopped" ? [changeProposal] : [],
        },
      ]),
    );
    renderAt("/improvement");
    await selectedRound();
    expect(screen.queryByText("Awaiting approval")).toBeNull();
    expect(screen.queryByText("Pending", { exact: true })).toBeNull();
    expect(screen.getAllByText("Not evaluated").length).toBeGreaterThan(0);
    expect(screen.getAllByText("No version tried").length).toBeGreaterThan(0);
  },
);

it("retains rejected model-only changes in proposal history", async () => {
  const rejected = {
    ...changeProposal,
    kind: "configuration" as const,
    decision: "reject" as const,
    handler: "Dana Whitfield",
    decided_at: round.started_at,
    diff: "",
    arguments: {
      instructions: configuration.instructions,
      model: "google/gemini-3.5-flash-lite",
      reasoning_effort: "minimal",
      reason: "Try a different model",
    },
  };
  stubApi(routes([{ ...round, status: "finished", proposals: [rejected] }]));
  renderAt("/improvement");
  const panel = await selectedRound();
  fireEvent.click(panel.getByText("Configuration · Rejected"));
  expect(
    panel.getByText("google/gemini-3.5-flash-lite · minimal"),
  ).toBeDefined();
  expect(panel.getByText("openai/gpt-6-luna · low")).toBeDefined();
});
