import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { ClaimDetail, ClaimEvent } from "./api";
import { ClaimReader } from "./ClaimWorkflow";
import { claim, decision, evidence } from "./testData";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function api(initial: ClaimDetail) {
  let current = initial;
  let events: ClaimEvent[] = [];
  const fetcher = vi.fn((path: string, options?: RequestInit) => {
    if (path.endsWith("/evidence"))
      return Promise.resolve(new Response(JSON.stringify(evidence)));
    if (path.endsWith("/handlers"))
      return Promise.resolve(
        new Response(
          JSON.stringify([
            {
              handler_id: "HND-01",
              name: "Dana Whitfield",
              can_approve_over_limit: true,
            },
          ]),
        ),
      );
    if (path.endsWith("/events"))
      return Promise.resolve(new Response(JSON.stringify(events)));
    if (options?.method === "POST") {
      if (path.endsWith("/sign-off"))
        current = {
          ...current,
          status: "decided",
          sign_off: {
            handler_id: "HND-01",
            handler_name: "Dana Whitfield",
            amount: "1250.00",
            created_at: decision.created_at,
          },
        };
      if (path.endsWith("/pay"))
        current = {
          ...current,
          payment: {
            amount: decision.payout,
            created_at: decision.created_at,
            simulated: true,
          },
        };
      return Promise.resolve(new Response("{}", { status: 201 }));
    }
    return Promise.resolve(new Response(JSON.stringify(current)));
  });
  vi.stubGlobal("fetch", fetcher);
  return {
    fetcher,
    update: (next: ClaimDetail, activity: ClaimEvent[] = []) => {
      current = next;
      events = activity;
    },
  };
}

it("follows an explicit assessment from waiting through completion", async () => {
  vi.useFakeTimers();
  const backend = api(claim);
  const onChange = vi.fn();
  await act(async () => {
    render(<ClaimReader claimId="CLM-1001" onChange={onChange} />);
    await Promise.resolve();
  });
  expect(screen.getByText("Waiting for assessment")).toBeDefined();
  expect(screen.queryByRole("button", { name: "Run agent" })).toBeNull();
  backend.update({ ...claim, status: "in_progress", run_active: true });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });
  expect(screen.getByText("Agent is reviewing this claim")).toBeDefined();
  backend.update({ ...claim, decision, status: "decided" }, [
    {
      id: 1,
      claim_id: "CLM-1001",
      thread_id: "thread",
      action: "Read policy",
      status: "completed",
      detail: "Policy section 3 read.",
      created_at: decision.created_at,
    },
  ]);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });
  expect(screen.getByText("Policy section 3 read.")).toBeDefined();
  expect(screen.getByText("Approve")).toBeDefined();
  const count = backend.fetcher.mock.calls.length;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(backend.fetcher).toHaveBeenCalledTimes(count);
  expect(
    backend.fetcher.mock.calls.some((call) => call[1]?.method === "POST"),
  ).toBe(false);
});

it("requires handler selection, signs off, then permits a separate payment", async () => {
  const backend = api({ ...claim, decision, status: "awaiting_sign_off" });
  const onChange = vi.fn();
  render(<ClaimReader claimId="CLM-1001" onChange={onChange} />);
  const sign = await screen.findByRole("button", { name: "Sign off payout" });
  expect(sign).toHaveProperty("disabled", true);
  expect(
    screen.queryByRole("button", { name: "Record simulated payment" }),
  ).toBeNull();
  await screen.findByRole("option", { name: "Dana Whitfield" });
  fireEvent.change(screen.getByLabelText("Reviewing handler"), {
    target: { value: "HND-01" },
  });
  fireEvent.click(sign);
  expect(await screen.findByText(/Signed off by Dana Whitfield/)).toBeDefined();
  fireEvent.click(
    screen.getByRole("button", { name: "Record simulated payment" }),
  );
  expect(await screen.findByText(/Simulated payment recorded/)).toBeDefined();
  expect(backend.fetcher).toHaveBeenCalledWith(
    "/api/claims/CLM-1001/pay",
    expect.objectContaining({ body: JSON.stringify({ amount: "1250.00" }) }),
  );
  expect(onChange).toHaveBeenCalledTimes(2);
});

it("shows backend rejection and refreshes authoritative claim state", async () => {
  const backend = api({ ...claim, decision, status: "decided" });
  render(<ClaimReader claimId="CLM-1001" />);
  const pay = await screen.findByRole("button", {
    name: "Record simulated payment",
  });
  backend.fetcher.mockImplementationOnce(() =>
    Promise.resolve(
      new Response(
        JSON.stringify({ detail: "Payment has already been recorded." }),
        { status: 409 },
      ),
    ),
  );
  fireEvent.click(pay);
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    expect.stringContaining("already been recorded"),
  );
  fireEvent.click(screen.getByRole("button", { name: "Refresh claim" }));
  await waitFor(() => {
    expect(
      backend.fetcher.mock.calls.filter(
        (call) => call[0] === "/api/claims/CLM-1001",
      ).length,
    ).toBeGreaterThan(1);
  });
});

it("explains how to retry a failed pass without offering a manual start", async () => {
  const backend = api(claim);
  backend.update(claim, [
    {
      id: 1,
      claim_id: "CLM-1001",
      thread_id: "thread",
      action: "Run agent",
      status: "failed",
      detail: "Agent run failed. Retry in a new processing session.",
      created_at: decision.created_at,
    },
  ]);
  render(<ClaimReader claimId="CLM-1001" />);
  expect(await screen.findByText("Assessment can be retried")).toBeDefined();
  expect(screen.getByText(/The queue can retry it once/)).toBeDefined();
  expect(screen.getByText(/Agent run failed/)).toBeDefined();
  expect(screen.queryByRole("button", { name: "Run agent" })).toBeNull();
});

it("shows a technical handoff without inventing a referral and preserves full evidence", async () => {
  api({
    ...claim,
    status: "handler_review",
    failed_attempts: 2,
    review_reason: "two_failed_assessments",
  });
  render(<ClaimReader claimId="CLM-1001" />);
  expect(await screen.findByText("Handler review required")).toBeDefined();
  expect(
    screen.getByText(/No insurance decision or payout was recorded/),
  ).toBeDefined();
  expect(screen.getByText(evidence.reads[0].text)).toBeDefined();
  expect(screen.getByText(`Attempt ${decision.thread_id}`)).toBeDefined();
  expect(screen.queryByRole("region", { name: "Agent decision" })).toBeNull();
});

it("keeps a later arrival waiting without starting processing while polling", async () => {
  vi.useFakeTimers();
  const backend = api({ ...claim, claim_id: "TEST-LATER" });
  await act(async () => {
    render(<ClaimReader claimId="TEST-LATER" />);
    await Promise.resolve();
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(screen.getByText("Waiting for assessment")).toBeDefined();
  expect(
    screen.getByText(
      "Start an assessment with uv run -m agent, or use uv run -m agent --cap 5 for up to five claims.",
    ),
  ).toBeDefined();
  expect(backend.fetcher.mock.calls.length).toBeGreaterThan(2);
  expect(
    backend.fetcher.mock.calls.some((call) => call[1]?.method === "POST"),
  ).toBe(false);
});

it.each(["decline", "refer"] as const)(
  "offers no payment or sign-off for %s",
  async (outcome) => {
    api({
      ...claim,
      decision: { ...decision, outcome, payout: "0" },
      status: "decided",
    });
    render(<ClaimReader claimId="CLM-1001" />);
    await screen.findByRole("region", { name: "Agent decision" });
    expect(
      screen.queryByRole("button", { name: /payment|Sign off/ }),
    ).toBeNull();
  },
);

it("continues polling after a transient failure and cleans up on unmount", async () => {
  vi.useFakeTimers();
  const backend = api({ ...claim, run_active: true, status: "in_progress" });
  let view: ReturnType<typeof render> | undefined;
  await act(async () => {
    view = render(<ClaimReader claimId="CLM-1001" />);
    await Promise.resolve();
  });
  backend.fetcher.mockRejectedValueOnce(new Error("Temporarily offline"));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });
  expect(screen.getByRole("alert")).toHaveProperty(
    "textContent",
    expect.stringContaining("Temporarily offline"),
  );
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });
  expect(screen.queryByRole("alert")).toBeNull();
  view?.unmount();
  const calls = backend.fetcher.mock.calls.length;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(backend.fetcher).toHaveBeenCalledTimes(calls);
});
