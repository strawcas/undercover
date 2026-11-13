import { expect, test } from "./fixtures";
import { post, resumed } from "./api";

// These tests stand in for the agent by calling the API through the
// dashboard's proxy, and watch the claim page update without a reload.

test("the claim page shows the agent at work", async ({ page, request }) => {
  await page.goto("/claims/CLM-1003");
  const heading = page.locator(".page-header");
  await expect(heading.getByText("Waiting")).toBeVisible();
  await expect(page.getByText(/No steps yet/)).toBeVisible();

  await post(request, "/api/claims/CLM-1003/start", { thread_id: "e2e-1" });
  await post(request, "/api/claims/CLM-1003/steps", {
    kind: "tool_call",
    content: 'get_claim(claim_id="CLM-1003")',
  });
  await post(request, "/api/claims/CLM-1003/steps", {
    kind: "tool_result",
    content: 'get_claim: {"claim_id":"CLM-1003"}',
  });
  await expect(heading.getByText("In progress")).toBeVisible();
  await expect(
    page.getByText("The agent is working on this claim."),
  ).toBeVisible();
  await expect(page.getByText('get_claim(claim_id="CLM-1003")')).toBeVisible();
  await expect(
    page.getByText('get_claim: {"claim_id":"CLM-1003"}').first(),
  ).toBeVisible();

  await post(request, "/api/claims/CLM-1003/decision", {
    decision: "approve",
    payout: 500,
    section: "Section 7, Emergency Medical",
    reason: "The treatment was necessary and covered.",
  });
  const decision = page.locator("section", {
    has: page.getByRole("heading", { name: "Decision" }),
  });
  await expect(decision.getByText("Approved")).toBeVisible();
  await expect(heading.getByText("In progress")).toBeVisible();
  await post(request, "/api/claims/CLM-1003/payment", { amount: 500 });
  await post(request, "/api/claims/CLM-1003/steps", {
    kind: "message",
    content: "Approved and paid $500.00.",
  });
  await expect(heading.getByText("Decided")).toBeVisible();
  await expect(decision.getByText("Paid $500.00")).toBeVisible();
  await expect(page.getByText("Approved and paid $500.00.")).toBeVisible();
});

test("a sign-off on the dashboard reaches the agent's service", async ({
  page,
  request,
}) => {
  await post(request, "/api/claims/CLM-1020/start", { thread_id: "e2e-2" });
  await post(request, "/api/claims/CLM-1020/decision", {
    decision: "approve",
    payout: 1350,
    section: "Section 5, Baggage and Personal Effects",
    reason: "The loss is covered.",
  });
  const refused = await request.post("/api/claims/CLM-1020/payment", {
    data: { amount: 1350 },
  });
  expect(refused.status()).toBe(403);

  await page.goto("/claims/CLM-1020");
  const heading = page.locator(".page-header");
  await expect(heading.getByText("Waiting for sign-off")).toBeVisible();
  expect(await resumed(request)).not.toContain("CLM-1020");
  await page.getByRole("button", { name: "Sign off" }).click();
  await expect(heading.getByText("In progress")).toBeVisible();
  await expect(page.getByText(/Signed off by Dana Whitfield/)).toBeVisible();
  await expect.poll(() => resumed(request)).toContain("CLM-1020");

  // The test plays the agent, which pays the claim once it is told.
  await post(request, "/api/claims/CLM-1020/payment", { amount: 1350 });
  await expect(heading.getByText("Decided")).toBeVisible();
  await expect(
    page.getByText(/^Paid \$1,350\.00, signed off by Dana Whitfield at /),
  ).toBeVisible();
});
