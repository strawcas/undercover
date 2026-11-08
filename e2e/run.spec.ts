import { expect, test } from "@playwright/test";
import { post, put, AGENT_STAND_IN } from "./api";

// These tests stand in for the dispatcher and a claim agent by calling the
// API through the dashboard's proxy, and watch the pages update without a
// reload. Only this file starts runs, and it uses claims no other test touches.

test("the queue shows dispatch members and a records-built summary", async ({
  page,
  request,
}) => {
  await page.goto("/");
  const panel = page.locator("section", {
    has: page.getByRole("heading", { name: "Current run" }),
  });
  await page.getByLabel("Agents at once").fill("25");
  await page.getByRole("button", { name: "Start dispatch" }).click();
  await expect(panel.getByText("Dispatch accepted.")).toBeVisible();
  const starts = await request.get(`${AGENT_STAND_IN}/api/starts`);
  expect(await starts.json()).toContainEqual({ concurrency: 25 });
  const response = await post(request, "/api/runs", {
    kind: "dispatch",
    concurrency: 3,
    model: "stub",
    reasoning_effort: null,
    set_name: null,
    tiny: null,
  });
  const { run_id } = (await response.json()) as { run_id: string };
  await put(request, `/api/runs/${run_id}/claims/CLM-1013`, {
    status: "selected",
    error: null,
  });
  await expect(panel.getByRole("link", { name: "CLM-1013" })).toBeVisible();
  await post(request, "/api/claims/CLM-1013/start", {
    thread_id: "dispatch-e2e",
  });
  await post(request, "/api/claims/CLM-1013/decision", {
    decision: "refer",
    payout: 0,
    section: "3",
    reason: "Needs a handler",
  });
  await post(request, `/api/runs/${run_id}/finish`, {});
  await expect(panel.getByText("Finished", { exact: true })).toBeVisible();
  await expect(
    panel.getByText("Worked 1 claim: 0 approved, 0 declined, 1 referred."),
  ).toBeVisible();
  await panel.getByRole("link", { name: "CLM-1013" }).click();
  await expect(page).toHaveURL("/claims/CLM-1013");
});

test("a claim that fails twice is left for a handler", async ({
  page,
  request,
}) => {
  await page.goto("/claims/CLM-1016");
  const heading = page.locator(".claim-heading");
  await expect(heading.getByText("Waiting")).toBeVisible();

  await post(request, "/api/claims/CLM-1016/start", { thread_id: "e2e-3" });
  await post(request, "/api/claims/CLM-1016/steps", {
    kind: "reading",
    content: "Policy wording, section 7: Emergency Medical",
  });
  await post(request, "/api/claims/CLM-1016/failure", {
    reason: "RuntimeError: The model timed out",
  });
  await expect(
    page.getByText("Back in the queue after a failed attempt."),
  ).toBeVisible();

  await post(request, "/api/claims/CLM-1016/start", { thread_id: "e2e-4" });
  await post(request, "/api/claims/CLM-1016/failure", {
    reason: "RuntimeError: The model timed out again",
  });
  await expect(heading.getByText("Left for a handler")).toBeVisible();
  await expect(
    page.getByText(
      "The agent couldn't finish this claim in 2 attempts, so it's left for a handler.",
    ),
  ).toBeVisible();

  const read = page.locator(".sections-read");
  await expect(read.getByRole("listitem")).toHaveText([
    "Policy wording, section 7: Emergency Medical",
  ]);
  const failed = page.locator(".step", { hasText: "Failed" });
  await expect(failed).toHaveText([
    /Failed RuntimeError: The model timed out$/,
    /Failed RuntimeError: The model timed out again$/,
  ]);
});
