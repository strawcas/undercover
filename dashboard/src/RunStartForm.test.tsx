import {
  act,
  cleanup,
  fireEvent,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Refusal, renderAt, stubApi } from "./testing";
import { api } from "./api";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("keeps the start disabled until admission answers", async () => {
  let accept: ((answer: { status: string }) => void) | undefined;
  vi.spyOn(api, "startDispatch").mockReturnValue(
    new Promise((resolve) => {
      accept = resolve;
    }),
  );
  showStart();
  fireEvent.click(
    await screen.findByRole("button", { name: "Start dispatch" }),
  );
  const pending = screen.getByRole("button", { name: "Starting…" });
  expect((pending as HTMLButtonElement).disabled).toBe(true);
  await act(async () => {
    accept?.({ status: "accepted" });
    await Promise.resolve();
  });
  expect(await screen.findByText("Dispatch accepted.")).toBeDefined();
});

function showStart(
  answer: unknown = { status: "accepted" },
  active: unknown = null,
) {
  const fetch = stubApi({
    "GET /api/claims": [],
    "GET /api/runs/latest": null,
    "GET /api/runs/active": active,
    "POST /api/dispatches": answer,
  });
  renderAt("/");
  return fetch;
}

it("defaults to three and forwards a chosen concurrency", async () => {
  const fetch = showStart();
  const input = await screen.findByRole("spinbutton", {
    name: "Agents at once",
  });
  expect((input as HTMLInputElement).value).toBe("3");
  expect(input.getAttribute("min")).toBe("1");
  expect(input.getAttribute("max")).toBe("25");
  fireEvent.change(input, { target: { value: "25" } });
  fireEvent.click(screen.getByRole("button", { name: "Start dispatch" }));
  expect(await screen.findByText("Dispatch accepted.")).toBeDefined();
  expect(fetch).toHaveBeenCalledWith(
    "/api/dispatches",
    expect.objectContaining({ body: JSON.stringify({ concurrency: 25 }) }),
  );
});

it.each([409, 503])("shows the service refusal %s", async (status) => {
  showStart(
    new Refusal(
      status,
      status === 409 ? "Already running" : "The agent's service is unavailable",
    ),
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "Start dispatch" }),
  );
  expect((await screen.findByRole("alert")).textContent).toContain(
    status === 409 ? "Already running" : "unavailable",
  );
});

it("disables starts while either kind of run is active", async () => {
  showStart(undefined, { run_id: "active", kind: "evaluation" });
  const button = await screen.findByRole("button", { name: "Start dispatch" });
  await waitFor(() => {
    expect((button as HTMLButtonElement).disabled).toBe(true);
  });
  expect(
    screen.getByText("A dispatch or evaluation is running."),
  ).toBeDefined();
});
