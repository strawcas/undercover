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
import { claim, decision } from "./testData";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function api(initial: ClaimDetail) {
  let current = initial;
  let events: ClaimEvent[] = [];
  const fetcher = vi.fn((path: string, options?: RequestInit) => {
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
      if (path.endsWith("/runs"))
        current = { ...current, status: "in_progress", run_active: true };
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

it("starts a claim, polls live activity, and stops after the decision", async () => {
  vi.useFakeTimers();
  const backend = api(claim);
  const onChange = vi.fn();
  await act(async () => {
    render(<ClaimReader claimId="CLM-1001" onChange={onChange} />);
    await Promise.resolve();
  });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Run agent" }));
    await Promise.resolve();
  });
  expect(screen.getByText("Agent is reviewing this claim")).toBeDefined();
  backend.update({ ...claim, decision, status: "decided" }, [
    {
      id: 1,
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
  expect(onChange).toHaveBeenCalled();
});

it("requires handler selection, signs off, then permits a separate payment", async () => {
  const backend = api({ ...claim, decision, status: "awaiting_sign_off" });
  render(<ClaimReader claimId="CLM-1001" />);
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
});

it("shows backend rejection and refreshes authoritative claim state", async () => {
  const backend = api(claim);
  render(<ClaimReader claimId="CLM-1001" />);
  const run = await screen.findByRole("button", { name: "Run agent" });
  backend.fetcher.mockImplementationOnce(() =>
    Promise.resolve(
      new Response(
        JSON.stringify({ detail: "This claim is already running." }),
        { status: 409 },
      ),
    ),
  );
  fireEvent.click(run);
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    expect.stringContaining("already running"),
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
