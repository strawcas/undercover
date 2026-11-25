import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router";
import type { AuthChangeEvent, Session } from "@supabase/supabase-js";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AuthProvider } from "./AuthProvider";
import { App } from "./App";
import { api, setAccessToken } from "./api";
import { Refusal, renderAt, signedIn, stubApi } from "./testing";

const sdk = vi.hoisted(() => ({
  getSession: vi.fn(),
  onAuthStateChange: vi.fn(),
  signInWithPassword: vi.fn(),
  signOut: vi.fn(),
  unsubscribe: vi.fn(),
}));
vi.mock("./supabase", () => ({ authClient: () => ({ auth: sdk }) }));
let listener: (event: AuthChangeEvent, session: Session | null) => void;
const session: Session = {
  access_token: "local-token",
  refresh_token: "refresh",
  token_type: "bearer",
  expires_in: 3600,
  user: {
    id: "HND-01",
    email: signedIn.handler?.email,
    app_metadata: {},
    user_metadata: {},
    aud: "authenticated",
    created_at: "2026-01-01T00:00:00Z",
  },
};

beforeEach(() => {
  sdk.getSession.mockResolvedValue({ data: { session: null }, error: null });
  sdk.signInWithPassword.mockResolvedValue({ error: null });
  sdk.signOut.mockResolvedValue({ error: null });
  sdk.onAuthStateChange.mockImplementation((callback: typeof listener) => {
    listener = callback;
    return { data: { subscription: { unsubscribe: sdk.unsubscribe } } };
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  setAccessToken(null);
});

function show(path = "/") {
  const fetch = stubApi({
    "GET /api/me": signedIn.handler,
    "GET /api/claims": [],
    "GET /api/runs/latest": null,
  });
  render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <App />
      </AuthProvider>
    </MemoryRouter>,
  );
  return fetch;
}

it("protects deep links, restores a session and attaches its token", async () => {
  sdk.getSession.mockResolvedValue({ data: { session }, error: null });
  const fetch = show("/nowhere");
  expect(screen.getByText(/Restoring your sign-in/)).toBeDefined();
  expect(await screen.findByText("Page not found")).toBeDefined();
  const headers = fetch.mock.calls[0][1]?.headers as Headers;
  expect(headers.get("Authorization")).toBe("Bearer local-token");
  expect(screen.getByText("Dana Whitfield")).toBeDefined();
  await act(async () => {
    listener("SIGNED_OUT", null);
    await Promise.resolve();
  });
  expect(
    await screen.findByRole("heading", { name: "Sign in to UnderCover" }),
  ).toBeDefined();
  expect(screen.queryByText("Page not found")).toBeNull();
});

it("passes password sign-in to the SDK and reports errors", async () => {
  show();
  fireEvent.change(await screen.findByLabelText("Email"), {
    target: { value: "dana@example.com" },
  });
  fireEvent.change(screen.getByLabelText("Password"), {
    target: { value: "bad-password" },
  });
  sdk.signInWithPassword.mockResolvedValueOnce({
    error: new Error("Invalid credentials"),
  });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "Invalid credentials",
  );
  expect(sdk.signInWithPassword).toHaveBeenCalledWith({
    email: "dana@example.com",
    password: "bad-password",
  });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
  await waitFor(() => {
    expect(sdk.signInWithPassword).toHaveBeenCalledTimes(2);
  });
  await act(async () => {
    listener("SIGNED_IN", session);
    await Promise.resolve();
  });
  expect(await screen.findByRole("button", { name: "Sign out" })).toBeDefined();
});

it("keeps the page mounted when its access token refreshes and signs out", async () => {
  sdk.getSession.mockResolvedValue({ data: { session }, error: null });
  show();
  const heading = await screen.findByRole("heading", { name: "Claims queue" });
  await act(async () => {
    listener("TOKEN_REFRESHED", { ...session, access_token: "refreshed" });
    await Promise.resolve();
  });
  expect(screen.getByRole("heading", { name: "Claims queue" })).toBe(heading);
  fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
  expect(await screen.findByRole("button", { name: "Sign in" })).toBeDefined();
  expect(sdk.signOut).toHaveBeenCalledOnce();
});

it("keeps a verified handler signed in when Lambda throttles a session recheck", async () => {
  sdk.getSession.mockResolvedValue({ data: { session }, error: null });
  const fetch = show("/nowhere");
  const heading = await screen.findByText("Page not found");
  fetch.mockResolvedValueOnce(
    new Response("Too Many Requests", { status: 429 }),
  );
  await act(async () => {
    listener("TOKEN_REFRESHED", { ...session, access_token: "refreshed" });
    await Promise.resolve();
  });
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "The app is busy. Try again in a moment.",
  );
  expect(screen.getByText("Page not found")).toBe(heading);
  expect(screen.getByText("Dana Whitfield")).toBeDefined();
  await expect(api.getCurrentHandler()).resolves.toEqual(signedIn.handler);
  const headers = fetch.mock.lastCall?.[1]?.headers as Headers;
  expect(headers.get("Authorization")).toBe("Bearer refreshed");
});

it("requires fresh verification when a different handler signs in during throttling", async () => {
  sdk.getSession.mockResolvedValue({ data: { session }, error: null });
  const fetch = show("/nowhere");
  await screen.findByText("Page not found");
  fetch.mockResolvedValueOnce(
    new Response("Too Many Requests", { status: 429 }),
  );
  await act(async () => {
    listener("SIGNED_IN", {
      ...session,
      access_token: "other-token",
      user: { ...session.user, id: "HND-02" },
    });
    await Promise.resolve();
  });
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "The app is busy. Try again in a moment.",
  );
  expect(screen.queryByText("Dana Whitfield")).toBeNull();
  expect(screen.queryByRole("navigation")).toBeNull();
});

it("refuses a session that the backend cannot verify", async () => {
  sdk.getSession.mockResolvedValue({ data: { session }, error: null });
  vi.spyOn(api, "getCurrentHandler").mockRejectedValue(
    new Error("Unknown handler"),
  );
  show();
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "Unknown handler",
  );
  expect(screen.queryByRole("navigation")).toBeNull();
});

it("reports restoration errors and removes subscriptions on unmount", async () => {
  sdk.getSession.mockResolvedValue({
    data: { session: null },
    error: new Error("Session unavailable"),
  });
  show();
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "Session unavailable",
  );
  cleanup();
  expect(sdk.unsubscribe).toHaveBeenCalled();
});

it("explains the non-senior sign-off restriction", async () => {
  const { claim, policy, history } = await import("./testing");
  stubApi({
    "GET /api/claims/CLM-1002": {
      ...claim,
      status: "waiting_for_sign_off",
      decision: {
        decision: "approve",
        payout: 1350,
        section: "Section 3",
        reason: "Covered",
        decided_at: "2026-01-01T00:00:00Z",
        signed_off_by: null,
        signed_off_at: null,
      },
    },
    "GET /api/policies/POL-2002": policy,
    "GET /api/customers/CUS-3002/claims": history,
  });
  renderAt("/claims/CLM-1002", {
    ...signedIn,
    handler: signedIn.handler
      ? { ...signedIn.handler, can_approve_over_limit: false }
      : null,
  });
  expect(
    await screen.findByText(/You cannot sign off this payout/),
  ).toBeDefined();
  expect(screen.queryByRole("button", { name: "Sign off" })).toBeNull();
});

it("preserves API refusals with an authenticated action", async () => {
  setAccessToken("expired");
  stubApi({
    "POST /api/claims/CLM-1002/sign-off": new Refusal(401, "Sign in again"),
  });
  await expect(api.signOff("CLM-1002")).rejects.toThrow("Sign in again");
});

it("clears a previously verified handler when the same user's recheck fails", async () => {
  sdk.getSession.mockResolvedValue({ data: { session }, error: null });
  show();
  await screen.findByRole("heading", { name: "Claims queue" });
  vi.spyOn(api, "getCurrentHandler").mockRejectedValue(
    new Error("Account removed"),
  );
  await act(async () => {
    listener("TOKEN_REFRESHED", session);
    await Promise.resolve();
  });
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "Account removed",
  );
  expect(screen.queryByRole("navigation")).toBeNull();
});

it("removes protected content when an action returns 401", async () => {
  sdk.getSession.mockResolvedValue({ data: { session }, error: null });
  show();
  await screen.findByRole("heading", { name: "Claims queue" });
  stubApi({ "POST /api/dispatches": new Refusal(401, "Expired") });
  await act(async () => {
    await expect(api.startDispatch(3)).rejects.toThrow("Expired");
  });
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "Your sign-in expired. Sign in again.",
  );
  expect(screen.queryByRole("navigation")).toBeNull();
});

it("shows sign-out failures without hiding the current identity", async () => {
  sdk.getSession.mockResolvedValue({ data: { session }, error: null });
  sdk.signOut.mockResolvedValue({
    error: new Error("Sign-out unavailable. Try again."),
  });
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Sign out" }));
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "Sign-out unavailable. Try again.",
  );
  expect(screen.getByText("Dana Whitfield")).toBeDefined();
});
