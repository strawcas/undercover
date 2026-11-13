import { expect, test } from "./fixtures";
import { post, put, AGENT_STAND_IN } from "./api";
import {
  internalRound,
  playEvaluations,
  playProposal,
} from "./improvement_driver";

// These tests stand in for the dispatcher and a claim agent by calling the
// API through the dashboard's proxy, and watch the pages update without a
// reload. Starts are kept in this file so the shared service slot is sequential.

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
    configuration_version: 1,
    round_id: null,
    set_name: null,
    tiny: null,
  });
  const { run_id } = (await response.json()) as { run_id: string };
  await put(request, `/api/runs/${run_id}/selection`, {
    claim_ids: ["CLM-1013", "CLM-1014"],
  });
  const table = panel.getByRole("table", { name: "Claims in this dispatch" });
  await expect(table.getByRole("columnheader")).toHaveText([
    "Claim",
    "Customer",
    "State",
    "Decision",
    "Payout",
    "Started",
    "Time to finish",
    "Model cost",
  ]);
  const row = table.getByRole("row").filter({ hasText: "CLM-1013" });
  const original = await request.get("/api/claims/CLM-1013");
  const { customer_name } = (await original.json()) as {
    customer_name: string;
  };
  await expect(row.getByRole("cell").nth(1)).toHaveText(customer_name);
  await expect(row.getByRole("cell").nth(6)).toHaveText("Not started");
  await post(request, "/api/claims/CLM-1013/start", {
    thread_id: "dispatch-e2e",
  });
  await put(request, "/api/claims/CLM-1013/work-slices/dispatch-work", {
    run_id,
    seconds: 10,
  });
  await post(request, "/api/claims/CLM-1013/model-calls", {
    run_id,
    reply_id: "dispatch-reply",
    input_tokens: 10,
    output_tokens: 5,
    total_tokens: 15,
    cost: 0.000123,
  });
  await expect(row.getByRole("cell").nth(6)).toHaveText("In progress");
  await expect(row.locator("time")).toHaveAttribute("datetime", /T/);
  await expect(row.getByRole("cell").nth(7)).toHaveText("$0.000123");
  await post(request, "/api/claims/CLM-1014/start", {
    thread_id: "held-dispatch-e2e",
  });
  await put(request, "/api/claims/CLM-1014/work-slices/held-work", {
    run_id,
    seconds: 5,
  });
  await post(request, "/api/claims/CLM-1014/decision", {
    decision: "approve",
    payout: 1350,
    section: "3",
    reason: "Covered, needs sign-off",
  });
  const held = table.getByRole("row").filter({ hasText: "CLM-1014" });
  await expect(held.getByRole("cell").nth(6)).toHaveText("Awaiting sign-off");
  await post(request, "/api/claims/CLM-1013/decision", {
    decision: "refer",
    payout: 0,
    section: "3",
    reason: "Needs a handler",
  });
  await post(request, `/api/runs/${run_id}/finish`, {});
  await expect(panel.getByText("Finished", { exact: true })).toBeVisible();
  await expect(row.getByRole("cell").nth(3)).toHaveText("Referred");
  await expect(row.getByRole("cell").nth(4)).toHaveText("$0.00");
  await expect(row.getByRole("cell").nth(6)).toHaveText(/^\d+\.\d s$/);
  await expect(row.getByRole("cell").nth(7)).toHaveText("$0.000123");
  await expect(held.getByRole("cell").nth(6)).toHaveText("Awaiting sign-off");
  await expect(held.getByRole("cell").nth(4)).toHaveText("$1,350.00");
  await expect(
    panel.getByText(/Worked 2 claims: 1 approved, 0 declined, 1 referred\./),
  ).toBeVisible();
  await panel.getByRole("link", { name: "CLM-1013" }).click();
  await expect(page).toHaveURL("/claims/CLM-1013");
  await post(request, `${AGENT_STAND_IN}/api/release`, {});
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

test("evaluations show scores, copied decisions and stopped runs without changing the queue", async ({
  page,
  request,
}) => {
  const queueBefore = await request.get("/api/claims");
  const original = (await queueBefore.json()) as { claim_id: string }[];
  await page.goto("/scoreboard");
  await page.getByLabel("Claim set").selectOption("held_out");
  await page.getByLabel("Selection").selectOption("tiny");
  await page.getByLabel("Agents at once").fill("5");
  await page.getByRole("button", { name: "Start evaluation" }).click();
  await expect(page.getByText("Evaluation accepted.")).toBeVisible();
  const starts = await request.get(`${AGENT_STAND_IN}/api/starts`);
  expect(await starts.json()).toContainEqual({
    set_name: "held_out",
    tiny: true,
    concurrency: 5,
  });
  const busy = await request.post("/api/dispatches", {
    data: { concurrency: 25 },
  });
  expect(busy.status()).toBe(409);
  const response = await post(request, "/api/runs", {
    kind: "evaluation",
    set_name: "held_out",
    tiny: true,
    concurrency: 5,
    configuration_version: 1,
    round_id: null,
  });
  const run = (await response.json()) as {
    run_id: string;
    members: { claim_id: string }[];
  };
  const claimId = run.members[0].claim_id;
  await page.getByRole("link", { name: "Queue", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Start dispatch" }),
  ).toBeDisabled();
  await page.getByRole("link", { name: "Scoreboard", exact: true }).click();
  await expect(page.getByText("Running", { exact: true })).toBeVisible();
  await page
    .getByRole("list", { name: "Evaluations" })
    .getByRole("link")
    .first()
    .click();
  await expect(
    page.getByRole("table").getByText("No decision (scored as referred)"),
  ).toHaveCount(5);
  await post(request, `/api/claims/${claimId}/start`, {
    thread_id: "eval-e2e",
  });
  await post(request, `/api/claims/${claimId}/decision`, {
    decision: "approve",
    payout: 1350,
    section: "3",
    reason: "Evaluation rationale",
  });
  await post(request, `/api/claims/${claimId}/model-calls`, {
    run_id: run.run_id,
    reply_id: "reply",
    input_tokens: 10,
    output_tokens: 5,
    total_tokens: 15,
    cost: 0.001,
  });
  await put(request, `/api/claims/${claimId}/work-slices/work`, {
    run_id: run.run_id,
    seconds: 10,
  });
  await expect(page.getByText("Evaluation rationale")).toBeVisible();
  await expect(
    page.getByRole("table").getByText("No decision (scored as referred)"),
  ).toHaveCount(4);
  await page
    .getByRole("link", { name: "Scoreboard", exact: true })
    .first()
    .click();
  await expect(page.getByText("Running", { exact: true })).toBeVisible();
  await post(request, `/api/runs/${run.run_id}/finish`, {});
  await post(request, `${AGENT_STAND_IN}/api/release`, {});
  const evaluations = page.getByRole("list", { name: "Evaluations" });
  await expect(
    evaluations.getByText("Finished", { exact: true }),
  ).toBeVisible();
  await expect(evaluations.getByText("$0.000200")).toHaveCount(2);
  await evaluations.getByRole("link").first().click();
  await expect(
    page.getByRole("heading", { name: "Evaluation results" }),
  ).toBeVisible();
  await expect(page.getByText("Evaluation rationale")).toBeVisible();
  const comparison = page.getByRole("table");
  await expect(comparison.getByText("A", { exact: true })).toHaveCount(1);
  await expect(comparison.getByText("F", { exact: true })).toHaveCount(4);
  await expect(comparison.getByText("Same grounds: Yes")).toBeVisible();
  await expect(comparison.getByText("Same section: No")).toHaveCount(5);
  await expect(
    comparison.getByText("No decision (scored as referred)"),
  ).toHaveCount(4);
  await comparison.getByRole("link").first().click();
  await expect(page.getByText(/Evaluation claim/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign off" })).toHaveCount(0);
  await expect(page.getByText(/complete without sign-off/)).toBeVisible();
  const queueAfter = await request.get("/api/claims");
  const after = (await queueAfter.json()) as { claim_id: string }[];
  expect(after.map((c) => c.claim_id)).toEqual(original.map((c) => c.claim_id));
  const stopped = await post(request, "/api/runs", {
    kind: "evaluation",
    set_name: "evaluation",
    tiny: true,
    concurrency: 3,
    configuration_version: 1,
    round_id: null,
  });
  expect(stopped.ok()).toBe(true);
  await post(request, "/api/runs/stop", {});
  await page.getByRole("link", { name: "Scoreboard", exact: true }).click();
  await expect(page.getByText("Stopped", { exact: true })).toBeVisible();
});

test("improvement approvals retain feedback, refuse busy work and show the verdict and recipe", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  await page.goto("/improvement");
  await page.getByRole("button", { name: "Start round", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog
    .getByRole("button", { name: "Start round", exact: true })
    .click();
  await expect(page.getByText(/Round accepted/)).toBeVisible();
  const response = await request.get("/api/rounds");
  const rounds = (await response.json()) as { round_id: string }[];
  const identity = rounds[0].round_id;
  const detail = page.getByRole("region", { name: "Selected round" });
  await expect(
    detail.getByText("Baseline evaluations", { exact: true }),
  ).toBeVisible();
  await playEvaluations(request, identity, 1, "baseline");
  await playProposal(request, identity, "first-change");
  await expect(
    detail.getByRole("heading", { name: "Proposed configuration" }),
  ).toBeVisible();
  const feedback = "Keep the policy change general and explain the evidence";
  await detail.getByLabel("Rejection reason").fill(feedback);
  await detail.getByRole("button", { name: "Reject proposal" }).click();
  await dialog.getByRole("button", { name: "Reject proposal" }).click();
  await expect(page.getByText(/Rejection and feedback recorded/)).toBeVisible();
  await playProposal(request, identity, "replacement-change");
  await expect(
    detail.getByRole("button", { name: "Approve testing" }),
  ).toBeVisible();
  await post(request, `${AGENT_STAND_IN}/api/dispatches`, { concurrency: 25 });
  await detail.getByRole("button", { name: "Approve testing" }).click();
  await dialog.getByRole("button", { name: "Approve proposal" }).click();
  await expect(dialog.getByText(/already running/)).toBeVisible();
  const stillPending = await request.get(internalRound(identity));
  const pending = (await stillPending.json()) as {
    proposals: { proposal_id: string; decision: string | null }[];
  };
  expect(
    pending.proposals.find(
      (value) => value.proposal_id === "replacement-change",
    )?.decision,
  ).toBeNull();
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await post(request, `${AGENT_STAND_IN}/api/release`, {});
  await detail.getByRole("button", { name: "Approve testing" }).click();
  await dialog.getByRole("button", { name: "Approve proposal" }).click();
  await expect(page.getByText(/Approval recorded/)).toBeVisible();
  await playEvaluations(request, identity, 2, "evaluating");
  await expect(detail.getByText("Rolled back", { exact: true })).toBeVisible();
  await playProposal(request, identity, "next-recipe", "recipe");
  await detail.getByRole("button", { name: "Approve recipe" }).click();
  await dialog.getByRole("button", { name: "Approve proposal" }).click();
  await expect(dialog).toBeHidden();
  await post(request, `${internalRound(identity)}/finish`, {
    stopped: false,
    reason: "Offline approval loop complete",
  });
  await expect(detail.getByText("Finished", { exact: true })).toBeVisible();
  await detail.getByText("Configuration · Rejected").click();
  await expect(detail.getByText(`Handler feedback: ${feedback}`)).toBeVisible();
  await expect(
    page.getByText("Recipe version 2 · Dana Whitfield"),
  ).toBeVisible();
  await detail
    .getByRole("link", { name: "Review configuration history" })
    .click();
  await expect(
    page.getByRole("heading", { name: "Configuration", exact: true }),
  ).toBeVisible();
  await page.getByRole("combobox").selectOption("2");
  await expect(
    page.getByText("+Use the evidence to support the general policy rule.", {
      exact: true,
    }),
  ).toBeVisible();
});
