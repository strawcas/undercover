import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "./App";
import { ClaimView } from "./ClaimDetail";
import { ClaimReader } from "./ClaimWorkflow";
import { errorMessage, money, request } from "./api";
import { claim, decision, rows } from "./testData";

function respond(path: string) {
  if (path.endsWith("/events")) return new Response("[]");
  if (path === "/api/claims") return new Response(JSON.stringify(rows));
  if (path === "/api/agent-instructions")
    return new Response(
      JSON.stringify({ text: "Use policy wording to decide each claim." }),
    );
  return new Response(
    JSON.stringify({ ...claim, claim_id: path.split("/").at(-1) }),
  );
}

function mockApi() {
  vi.stubGlobal(
    "fetch",
    vi.fn((path: string) => Promise.resolve(respond(path))),
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("renders the queue, all states and the complete claim", async () => {
  mockApi();
  render(<App />);
  expect(screen.getByText("Loading claims…")).toBeDefined();
  expect(
    await screen.findByRole("heading", { name: claim.summary }),
  ).toBeDefined();
  expect(screen.getAllByRole("button", { name: /^Open CLM-/ })).toHaveLength(4);
  expect(screen.getAllByText("Waiting for sign-off").length).toBeGreaterThan(0);
  expect(screen.getByText(claim.story)).toBeDefined();
  expect(screen.getByText("Airport hotel")).toBeDefined();
  expect(screen.getByText("Carrier notice")).toBeDefined();
  expect(screen.getByText("Crew shortage confirmed.")).toBeDefined();
  expect(screen.getByText("Previous delay")).toBeDefined();
  expect(screen.getByText("Yes")).toBeDefined();
  expect(screen.getAllByText("None").length).toBeGreaterThan(0);
  expect(screen.getByText("Ready for a closer look")).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "Open CLM-1002" }));
  expect(
    await screen.findByRole("article", { name: "Claim CLM-1002" }),
  ).toBeDefined();
});

it("shows read-only instructions and returns to the queue", async () => {
  mockApi();
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "Agent instructions" }));
  expect(
    await screen.findByText("Use policy wording to decide each claim."),
  ).toBeDefined();
  expect(screen.queryByRole("textbox")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Claims queue" }));
  expect(
    await screen.findByRole("heading", { name: "Claims queue" }),
  ).toBeDefined();
});

it("recovers after queue and instructions failures", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockRejectedValue(new Error("Backend offline")),
  );
  render(<App />);
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    expect.stringContaining("Backend offline"),
  );
  mockApi();
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(
    await screen.findByRole("heading", { name: claim.summary }),
  ).toBeDefined();
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue("Offline"));
  fireEvent.click(screen.getByRole("button", { name: "Agent instructions" }));
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    expect.stringContaining("could not be completed"),
  );
  mockApi();
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(
    await screen.findByText("Use policy wording to decide each claim."),
  ).toBeDefined();
});

it("shows an empty queue", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("[]")));
  render(<App />);
  expect(await screen.findByText("No claims in the queue.")).toBeDefined();
  expect(screen.getByText("Your queue is clear.")).toBeDefined();
});

it("handles a missing claim and retries", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ detail: "Claim not found." }), {
          status: 404,
        }),
      ),
    ),
  );
  render(<ClaimReader claimId="missing" />);
  expect(screen.getByText("Loading claim…")).toBeDefined();
  expect(await screen.findByText("Claim not found.")).toBeDefined();
  mockApi();
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(
    await screen.findByRole("heading", { name: claim.summary }),
  ).toBeDefined();
});

it("shows decisions, references, sign-off and simulated payment", () => {
  const view = render(
    <ClaimView claim={{ ...claim, decision, status: "awaiting_sign_off" }} />,
  );
  expect(screen.getByText("Approve")).toBeDefined();
  expect(screen.getByText("$1,250.00")).toBeDefined();
  expect(screen.getByText("Policy section 3")).toBeDefined();
  expect(
    screen.getByText("Payment is held until a handler signs off."),
  ).toBeDefined();
  view.rerender(
    <ClaimView
      claim={{
        ...claim,
        decision,
        sign_off: {
          handler_id: "HND-01",
          handler_name: "Dana Whitfield",
          amount: "1250.00",
          created_at: decision.created_at,
        },
        payment: {
          amount: "1250.00",
          created_at: decision.created_at,
          simulated: true,
        },
      }}
    />,
  );
  expect(screen.getByText(/Signed off by Dana Whitfield/)).toBeDefined();
  expect(screen.getByText(/Simulated payment recorded/)).toBeDefined();
});

it("ignores responses arriving after unmount", async () => {
  let finish: ((response: Response) => void) | undefined;
  vi.stubGlobal(
    "fetch",
    vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    ),
  );
  const view = render(<ClaimReader claimId="CLM-1001" />);
  view.unmount();
  finish?.(new Response(JSON.stringify(claim)));
  await waitFor(() => {
    expect(screen.queryByRole("article")).toBeNull();
  });
});

it("provides useful request errors and exact currency formatting", async () => {
  expect(money("1000.01")).toBe("$1,000.01");
  expect(errorMessage(null)).toContain("could not be completed");
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response("{}", { status: 422 })),
  );
  await expect(request("/api/claims")).rejects.toThrow("Request failed (422).");
});
