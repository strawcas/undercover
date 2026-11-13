import {
  act,
  cleanup,
  fireEvent,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  configuration,
  mockDialogs,
  Refusal,
  renderAt,
  settings,
  signedIn,
  stubApi,
} from "./testing";
import { applyTheme, getTheme, setTheme } from "./theme";
import { REFRESH_MS } from "./useLoad";

beforeEach(() => {
  mockDialogs();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  localStorage.clear();
  delete document.documentElement.dataset.theme;
});

it("shows business units, read-only allowed models and senior restrictions", async () => {
  stubApi({ "GET /api/settings": settings });
  renderAt("/settings", {
    ...signedIn,
    handler: signedIn.handler
      ? { ...signedIn.handler, can_approve_over_limit: false }
      : null,
  });
  const input = await screen.findByLabelText("Approval limit (USD)");
  expect(input).toHaveProperty("value", "1000");
  expect(input.closest("fieldset")).toHaveProperty("disabled", true);
  expect(screen.queryByRole("button", { name: "Save settings" })).toBeNull();
  expect(
    screen.getByText(/A senior handler is required to change/),
  ).toBeDefined();
  expect(screen.getByText("openai/gpt-6-luna")).toBeDefined();
  expect(screen.getByText(/0.15 means 15%/)).toBeDefined();
  expect(screen.getByText("No platform settings have changed.")).toBeDefined();
});

it("requires confirmation, supports cancellation and records saved settings", async () => {
  const updated = {
    ...settings,
    values: { ...settings.values, concurrency: 8 },
    changes: [
      {
        id: 1,
        handler: "Dana Whitfield",
        changed_at: "2026-01-01T00:00:00Z",
        before: settings.values,
        after: { ...settings.values, concurrency: 8 },
      },
    ],
  };
  const routes = {
    "GET /api/settings": settings,
    "PUT /api/settings": updated,
  };
  const fetch = stubApi(routes);
  renderAt("/settings");
  fireEvent.change(
    await screen.findByLabelText("Agents at once in round evaluations"),
    { target: { value: "8" } },
  );
  fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
  const dialog = screen.getByRole("dialog", {
    name: "Save platform settings?",
  });
  expect(dialog).toHaveProperty("open", true);
  fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(fetch.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(
    false,
  );
  fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
  routes["GET /api/settings"] = updated;
  fireEvent.click(within(dialog).getByRole("button", { name: "Save changes" }));
  expect(await screen.findByText("Settings saved.")).toBeDefined();
  expect(fetch).toHaveBeenCalledWith(
    "/api/settings",
    expect.objectContaining({
      method: "PUT",
      body: JSON.stringify(updated.values),
    }),
  );
  expect(await screen.findByText("View changes")).toBeDefined();
});

it("preserves unsaved inputs and displays a settings refusal", async () => {
  stubApi({
    "GET /api/settings": settings,
    "PUT /api/settings": new Refusal(409, "Settings could not be saved"),
  });
  renderAt("/settings");
  const input = await screen.findByLabelText("Approval limit (USD)");
  fireEvent.change(input, { target: { value: "1200" } });
  fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "Settings could not be saved",
  );
  expect(input).toHaveProperty("value", "1200");
});

it("remembers light, dark and computer appearance in the browser", async () => {
  setTheme("dark");
  stubApi({ "GET /api/settings": settings });
  renderAt("/settings");
  const select = screen.getByLabelText("Theme");
  expect(select).toHaveProperty("value", "dark");
  fireEvent.change(select, { target: { value: "light" } });
  expect(document.documentElement.dataset.theme).toBe("light");
  expect(getTheme()).toBe("light");
  fireEvent.change(select, { target: { value: "system" } });
  expect(document.documentElement.dataset.theme).toBeUndefined();
  localStorage.setItem("undercover-theme", "unknown");
  applyTheme();
  expect(getTheme()).toBe("system");
  await screen.findByLabelText("Approval limit (USD)");
});

it("shows settings load errors", async () => {
  stubApi({ "GET /api/settings": new Refusal(503, "Settings unavailable") });
  renderAt("/settings");
  expect(
    within(await screen.findByRole("alert")).getByText("Settings unavailable"),
  ).toBeDefined();
});

it("selects version diffs and keeps that selection through polling", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const second = {
    ...configuration,
    version: 2,
    parent_version: 1,
    reasoning_effort: "medium",
    prompt: "Revised instructions",
    diff: "--- Before\n+++ After\n-Old goal\n+New goal",
  };
  stubApi({
    "GET /api/configurations/current": configuration,
    "GET /api/configurations": [second, configuration],
  });
  renderAt("/configuration");
  const select = await screen.findByLabelText("Version");
  fireEvent.change(select, { target: { value: "2" } });
  expect(screen.getByText("Revised instructions")).toBeDefined();
  expect(screen.getByText("+New goal").className).toBe("diff-add");
  expect(screen.getByText("-Old goal").className).toBe("diff-remove");
  await act(async () => {
    await vi.advanceTimersByTimeAsync(REFRESH_MS);
  });
  expect(screen.getByLabelText("Version")).toBe(select);
  expect(select).toHaveProperty("value", "2");
});

it("explains empty configuration history", async () => {
  stubApi({
    "GET /api/configurations/current": configuration,
    "GET /api/configurations": [],
  });
  renderAt("/configuration");
  expect(
    await screen.findByText(/No configurations are available/),
  ).toBeDefined();
});

it("shows before and after models and effort when only model settings change", async () => {
  const second = {
    ...configuration,
    version: 2,
    parent_version: 1,
    model: "google/gemini-3.5-flash-lite",
    reasoning_effort: "minimal",
    diff: "",
  };
  stubApi({
    "GET /api/configurations/current": second,
    "GET /api/configurations": [second, configuration],
  });
  renderAt("/configuration");
  const changes = within(
    await screen.findByRole("region", { name: "Changes" }),
  );
  expect(
    changes.getByText(
      `${configuration.model} · ${configuration.reasoning_effort}`,
    ),
  ).toBeDefined();
  expect(
    changes.getByText("google/gemini-3.5-flash-lite · minimal"),
  ).toBeDefined();
  expect(changes.getByText("Before · version 1")).toBeDefined();
  expect(changes.getByText("After · version 2")).toBeDefined();
});

it.each([false, true])(
  "refreshes pristine settings after a server change (senior=%s)",
  async (senior) => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const routes = { "GET /api/settings": settings };
    stubApi(routes);
    renderAt("/settings", {
      ...signedIn,
      handler: signedIn.handler
        ? { ...signedIn.handler, can_approve_over_limit: senior }
        : null,
    });
    const input = await screen.findByLabelText("Approval limit (USD)");
    routes["GET /api/settings"] = {
      ...settings,
      values: { ...settings.values, payout_approval_limit: 1800 },
    };
    await act(async () => {
      await vi.advanceTimersByTimeAsync(REFRESH_MS);
    });
    expect(screen.getByLabelText("Approval limit (USD)")).toBe(input);
    expect(input).toHaveProperty("value", "1800");
  },
);

it("keeps dirty settings and focus through a server refresh", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const routes = { "GET /api/settings": settings };
  stubApi(routes);
  renderAt("/settings");
  const input = await screen.findByLabelText("Approval limit (USD)");
  input.focus();
  fireEvent.change(input, { target: { value: "1200" } });
  routes["GET /api/settings"] = {
    ...settings,
    values: { ...settings.values, payout_approval_limit: 1800 },
  };
  await act(async () => {
    await vi.advanceTimersByTimeAsync(REFRESH_MS);
  });
  expect(input).toHaveProperty("value", "1200");
  expect(document.activeElement).toBe(input);
});
