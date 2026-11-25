import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { renderAt, signedIn, stubApi } from "./testing";

const base = "/api/copilot/conversations";
const identity = {
  conversation_id: "conversation",
  handler_id: "HND-01",
  title: "What needs me?",
  updated_at: "2026-11-25",
};

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.unstubAllGlobals();
});

it("opens by default and remembers collapse", async () => {
  stubApi({});
  renderAt("/nowhere");
  const sidebar = screen.getByRole("complementary", {
    name: "UnderCover Copilot",
  });
  expect(
    await within(sidebar).findByRole("heading", { name: "Hi, Dana." }),
  ).toBeDefined();
  fireEvent.click(
    within(sidebar).getByRole("button", { name: "Collapse Copilot" }),
  );
  expect(
    screen.queryByRole("complementary", { name: "UnderCover Copilot" }),
  ).toBeNull();
  cleanup();
  renderAt("/scoreboard");
  fireEvent.click(screen.getByRole("button", { name: "Copilot" }));
  expect(
    await screen.findByRole("heading", { name: "UnderCover Copilot" }),
  ).toBeDefined();
});

it("uses the refreshed bearer and sends only on Enter without Shift", async () => {
  const token = vi.fn().mockResolvedValue("renewed");
  const fetch = stubApi({
    [`GET ${base}`]: [identity],
    [`GET ${base}/conversation`]: { ...identity, turns: [] },
    [`POST ${base}/conversation/messages`]: { turn_id: "turn" },
  });
  renderAt("/nowhere", { ...signedIn, copilotToken: token });
  await screen.findByRole("heading", { name: "Hi, Dana." });
  const composer = screen.getByRole("textbox", { name: "Ask the Copilot" });
  fireEvent.change(composer, { target: { value: "What needs me?" } });
  fireEvent.keyDown(composer, { key: "Enter", shiftKey: true });
  expect(token).not.toHaveBeenCalled();
  fireEvent.keyDown(composer, { key: "Enter" });
  await vi.waitFor(() => {
    const submitted = fetch.mock.calls.find(
      ([path, init]) => path.endsWith("/messages") && init?.method === "POST",
    );
    expect(new Headers(submitted?.[1]?.headers).get("Authorization")).toBe(
      "Bearer renewed",
    );
  });
});
