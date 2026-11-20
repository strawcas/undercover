import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Link, MemoryRouter } from "react-router";
import { App } from "./App";
import { AuthContext } from "./auth";
import type { Claim, Decision, Step } from "./api";
import { time } from "./format";
import {
  Refusal,
  type Routes,
  claim,
  history,
  panel,
  policy,
  renderAt,
  stubApi,
  signedIn,
} from "./testing";
import { REFRESH_MS } from "./useLoad";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const decidedAt = "2026-09-27T17:06:16+00:00";

const steps: Step[] = [
  {
    step_id: 1,
    thread_id: "t-1",
    kind: "tool_call",
    content: 'get_claim(claim_id="CLM-1002")',
    created_at: decidedAt,
  },
  {
    step_id: 2,
    thread_id: "t-1",
    kind: "tool_result",
    content: `get_claim: {"claim_id":"CLM-1002","story":"${"A long story. ".repeat(10)}"}`,
    created_at: decidedAt,
  },
  {
    step_id: 3,
    thread_id: "t-1",
    kind: "message",
    content: "Approved and paid.",
    created_at: decidedAt,
  },
];

function showClaim(shown: Claim, stepsShown: Step[] = []) {
  const routes: Routes = {
    "GET /api/claims/CLM-1002": shown,
    "GET /api/claims/CLM-1002/steps": stepsShown,
    "GET /api/policies/POL-2002": policy,
    "GET /api/customers/CUS-3002/claims": history,
  };
  const fetchMock = stubApi(routes);
  renderAt("/claims/CLM-1002");
  return { routes, fetchMock };
}

const decisionPanel = () => panel("Decision");

const approval: Decision = {
  decision: "approve",
  payout: 420,
  section: "Section 5, Baggage and Personal Effects",
  reason: "The suitcase was lost by the carrier.",
  decided_at: decidedAt,
  signed_off_by: null,
  signed_off_at: null,
  gateway_hold: null,
};

const approved: Claim = {
  ...claim,
  status: "decided",
  thread_id: "t-1",
  decision: approval,
  payment: {
    amount: 420,
    paid_at: decidedAt,
    signed_off_by: null,
    signed_off_at: null,
  },
};

const held: Claim = {
  ...approved,
  status: "waiting_for_sign_off",
  decision: { ...approval, payout: 1350 },
  payment: null,
};

it("removes the previous payout and sign-off action when navigating to a missing claim", async () => {
  stubApi({ "GET /api/claims/CLM-1002": held });
  render(
    <MemoryRouter initialEntries={["/claims/CLM-1002"]}>
      <AuthContext.Provider value={signedIn}>
        <Link to="/claims/CLM-9999">Next claim</Link>
        <App />
      </AuthContext.Provider>
    </MemoryRouter>,
  );
  await screen.findByRole("button", { name: "Sign off" });
  expect(
    screen
      .getByRole("navigation", { name: "Main" })
      .querySelector('[aria-current="page"]')?.textContent,
  ).toBe("Queue");
  fireEvent.click(screen.getByRole("link", { name: "Next claim" }));
  expect(screen.queryByRole("button", { name: "Sign off" })).toBeNull();
  await screen.findByRole("heading", { name: "Claim not found" });
  expect(screen.queryByText("$1,350.00")).toBeNull();
  expect(screen.queryByRole("button", { name: "Sign off" })).toBeNull();
});

it("says when the agent hasn't started", async () => {
  showClaim(claim);
  const panel = await decisionPanel();
  expect(
    panel.getByText("The agent hasn't started on this claim."),
  ).toBeDefined();
  expect(await screen.findByText(/No steps yet/)).toBeDefined();
});

it("says when a claim is back in the queue after a failed attempt", async () => {
  showClaim({ ...claim, attempts: 1 });
  const panel = await decisionPanel();
  expect(
    panel.getByText("Back in the queue after a failed attempt."),
  ).toBeDefined();
});

it("says when a claim is left for a handler", async () => {
  showClaim({ ...claim, status: "left_for_handler", attempts: 2 });
  const panel = await decisionPanel();
  expect(
    panel.getByText(
      "The agent couldn't finish this claim in 2 attempts, so it's left for a handler.",
    ),
  ).toBeDefined();
});

it("shows the sections read once each, and failed attempts", async () => {
  const read = (step_id: number, content: string): Step => ({
    step_id,
    thread_id: "t-1",
    kind: "reading",
    content,
    created_at: decidedAt,
  });
  showClaim({ ...claim, attempts: 1 }, [
    read(1, "Policy wording, section 3: Trip Delay"),
    read(2, "Claims manual, section 10: Meals without receipts"),
    read(3, "Policy wording, section 3: Trip Delay"),
    {
      step_id: 4,
      thread_id: "t-1",
      kind: "error",
      content: "RuntimeError: The model timed out",
      created_at: decidedAt,
    },
  ]);
  const heading = await screen.findByRole("heading", { name: "Sections read" });
  const list = within(heading.parentElement ?? document.body).getAllByRole(
    "listitem",
  );
  expect(list.map((item) => item.textContent)).toEqual([
    "Policy wording, section 3: Trip Delay",
    "Claims manual, section 10: Meals without receipts",
  ]);
  const reading = screen.getAllByText("Read");
  expect(reading).toHaveLength(3);
  const failed = screen.getByText("Failed");
  expect(failed.className).toBe("chip step-failed");
  expect(failed.parentElement?.textContent).toBe(
    "Failed RuntimeError: The model timed out",
  );
});

it("lists no sections when the agent has read none", async () => {
  showClaim(approved, steps);
  expect(
    await screen.findByText('get_claim(claim_id="CLM-1002")'),
  ).toBeDefined();
  expect(screen.queryByText("Sections read")).toBeNull();
});

it("says when the agent is working", async () => {
  showClaim({ ...claim, status: "in_progress", thread_id: "t-1" });
  const panel = await decisionPanel();
  expect(panel.getByText("The agent is working on this claim.")).toBeDefined();
});

it("shows an approval and its payment", async () => {
  showClaim(approved, steps);
  const panel = await decisionPanel();
  expect(panel.getByText("Approved")).toBeDefined();
  expect(panel.getAllByText("$420.00")).toHaveLength(2);
  expect(panel.getByText(approval.section)).toBeDefined();
  expect(panel.getByText(approval.reason)).toBeDefined();
  expect(panel.getByText(/^Paid/).textContent).toBe("Paid $420.00");
});

it("says the agent will pay an approval in progress", async () => {
  showClaim({ ...approved, status: "in_progress", payment: null });
  const panel = await decisionPanel();
  expect(panel.getByText("Waiting for the agent to pay it.")).toBeDefined();
});

it("shows a payment with its sign-off", async () => {
  const signOff = { signed_off_by: "Dana Whitfield", signed_off_at: decidedAt };
  showClaim({
    ...held,
    status: "decided",
    decision: { ...approval, payout: 1350, ...signOff },
    payment: { amount: 1350, paid_at: decidedAt, ...signOff },
  });
  const panel = await decisionPanel();
  expect(panel.getByText(/^Paid/).textContent).toBe(
    `Paid $1,350.00, signed off by Dana Whitfield at ${time(decidedAt)}`,
  );
});

it("shows a decline without a payout", async () => {
  showClaim({
    ...approved,
    decision: { ...approval, decision: "decline", payout: 0 },
    payment: null,
  });
  const panel = await decisionPanel();
  expect(panel.getByText("Declined")).toBeDefined();
  expect(panel.queryByText("$0.00")).toBeNull();
  expect(panel.queryByText(/paid/i)).toBeNull();
});

it("shows each kind of step, with results folded", async () => {
  showClaim(approved, steps);
  expect(
    await screen.findByText('get_claim(claim_id="CLM-1002")'),
  ).toBeDefined();
  const result = screen.getAllByText(/^get_claim: /)[0].closest("details");
  expect(result?.open).toBe(false);
  expect(result?.querySelector("summary")?.textContent).toMatch(/\.\.\.$/);
  expect(result?.querySelector("pre")?.textContent).toBe(steps[1].content);
  expect(screen.getByText("Approved and paid.")).toBeDefined();
});

it("shows new steps and the decision as the agent works", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const { routes } = showClaim({
    ...claim,
    status: "in_progress",
    thread_id: "t-1",
  });
  expect(await screen.findByText(/No steps yet/)).toBeDefined();

  routes["GET /api/claims/CLM-1002/steps"] = steps.slice(0, 1);
  await act(() => vi.advanceTimersByTimeAsync(REFRESH_MS));
  expect(screen.getByText('get_claim(claim_id="CLM-1002")')).toBeDefined();

  routes["GET /api/claims/CLM-1002"] = approved;
  routes["GET /api/claims/CLM-1002/steps"] = steps;
  await act(() => vi.advanceTimersByTimeAsync(REFRESH_MS));
  expect(screen.getByText("Approved and paid.")).toBeDefined();
  expect(screen.getAllByText("Decided").length).toBeGreaterThan(0);
});

it("identifies a Gateway hold and offers senior sign-off", async () => {
  showClaim({
    ...held,
    decision: { ...approval, payout: 1350, gateway_hold: "Policy refused" },
  });
  const panel = await decisionPanel();
  expect(panel.getByText(/The Gateway refused the payment of/)).toBeDefined();
  expect(panel.getByRole("button", { name: "Sign off" })).toBeDefined();
});

it("lets a handler sign off a held payment", async () => {
  const { routes, fetchMock } = showClaim(held);
  const panel = await decisionPanel();
  expect(panel.getByText(/waits for a handler's sign-off/)).toBeDefined();
  const button = panel.getByRole("button", { name: "Sign off" });
  expect(button).toHaveProperty("disabled", false);
  expect(
    panel.getByText("Sign off as Dana Whitfield to pay the approved amount."),
  ).toBeDefined();
  expect(panel.queryByRole("combobox")).toBeNull();

  const signedOff: Claim = {
    ...held,
    status: "decided",
    decision: {
      ...approval,
      payout: 1350,
      signed_off_by: "Dana Whitfield",
      signed_off_at: decidedAt,
    },
    payment: {
      amount: 1350,
      paid_at: decidedAt,
      signed_off_by: "Dana Whitfield",
      signed_off_at: decidedAt,
    },
  };
  routes["POST /api/claims/CLM-1002/sign-off"] = signedOff;
  routes["GET /api/claims/CLM-1002"] = new Refusal(
    503,
    "Claim refresh unavailable",
  );
  fireEvent.click(button);

  const paid = await panel.findByText(/signed off by Dana Whitfield/);
  expect(paid.textContent).toBe(
    `Paid $1,350.00, signed off by Dana Whitfield at ${time(decidedAt)}`,
  );
  expect(await screen.findByText("Claim refresh unavailable")).toBeDefined();
  expect(panel.queryByRole("button", { name: "Sign off" })).toBeNull();
  const [, init] = fetchMock.mock.calls.find(
    ([path, request]) =>
      path === "/api/claims/CLM-1002/sign-off" && request?.method === "POST",
  ) ?? [undefined, undefined];
  expect(init?.body).toBe(JSON.stringify({}));
});

it("shows why a sign-off was refused", async () => {
  const { routes } = showClaim(held);
  const panel = await decisionPanel();
  routes["POST /api/claims/CLM-1002/sign-off"] = new Refusal(
    409,
    "Claim CLM-1002 is not waiting for sign-off",
  );
  fireEvent.click(panel.getByRole("button", { name: "Sign off" }));
  expect((await panel.findByRole("alert")).textContent).toBe(
    "Claim CLM-1002 is not waiting for sign-off",
  );
});

it("offers sign-off when a lowered limit holds an older unpaid approval", async () => {
  showClaim({ ...held, status: "in_progress", sign_off_available: true });
  const panel = await decisionPanel();
  expect(panel.getByRole("button", { name: "Sign off" })).toBeDefined();
  expect(panel.queryByText("Waiting for the agent to pay it.")).toBeNull();
});

it("labels an evaluation copy and suppresses sign-off", async () => {
  const { fetchMock } = showClaim({
    ...held,
    evaluation_id: "eval-1",
    source_id: "CLM-1002",
  });
  expect(await screen.findByText(/Evaluation claim/)).toBeDefined();
  expect(screen.getByText("Evaluation complete · payment held")).toBeDefined();
  const view = await decisionPanel();
  expect(view.getByText(/complete without sign-off/)).toBeDefined();
  expect(view.queryByRole("button", { name: "Sign off" })).toBeNull();
  expect(
    screen
      .getByRole("link", { name: "View evaluation results" })
      .getAttribute("href"),
  ).toBe("/scoreboard/eval-1");
  expect(fetchMock.mock.calls.some(([path]) => path === "/api/handlers")).toBe(
    false,
  );
});
