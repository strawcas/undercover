import { expect, test } from "./fixtures";
import { decide, post, resumed } from "./api";
import { agentHeaders } from "./agent_credentials";

// These tests stand in for the agent by calling the API through the
// dashboard's proxy, and watch the claim page update without a reload.

test("the claim page shows the agent at work", async ({ page, request }) => {
  await page.goto("/claims/CLM-1003");
  const heading = page.locator(".page-header");
  await expect(heading.getByText("Waiting")).toBeVisible();
  await expect(page.getByText(/No steps yet/)).toBeVisible();

  await post(request, "/api/claims/CLM-1003/start", { thread_id: "e2e-1" });
  await post(request, "/api/claims/CLM-1003/steps", {
    kind: "security",
    thread_id: "e2e-1",
    content: "Passed. Ordinary submission.",
  });
  await post(request, "/api/claims/CLM-1003/steps", {
    kind: "tool_call",
    content: 'get_claim(claim_id="CLM-1003")',
    thread_id: "e2e-1",
  });
  await post(request, "/api/claims/CLM-1003/steps", {
    kind: "tool_result",
    content: 'get_claim: {"claim_id":"CLM-1003"}',
    thread_id: "e2e-1",
  });
  const lookup = {
    kind: "lookup",
    thread_id: "e2e-1",
    content: {
      subject: "Claims manual, section 14",
      sources: [
        {
          file: "claims_manual.md",
          system: "Claims operations",
          owner: "Head of claims operations",
          reviewed: "2026-09-01",
        },
      ],
    },
  };
  await post(request, "/api/claims/CLM-1003/steps", lookup);
  await post(request, "/api/claims/CLM-1003/steps", lookup);
  await post(request, "/api/claims/CLM-1003/steps", {
    kind: "refusal",
    thread_id: "e2e-1",
    content: {
      source: "App",
      reason: "Claims manual section 14 requires a calculation.",
    },
  });
  await post(request, "/api/claims/CLM-1003/steps", {
    kind: "memory_search",
    thread_id: "e2e-1",
    content: {
      query: "Earlier baggage claims",
      results: ["A previous baggage loss"],
    },
  });
  await post(request, "/api/claims/CLM-1003/prepare-memory", {
    thread_id: "e2e-1",
  });
  await expect(heading.getByText("In progress")).toBeVisible();
  await expect(page.locator(".steps li").first()).toContainText(
    "Security check Passed. Ordinary submission.",
  );
  await expect(
    page.getByText("The agent is working on this claim."),
  ).toBeVisible();
  await expect(page.getByText('get_claim(claim_id="CLM-1003")')).toBeVisible();
  await expect(
    page.getByText('get_claim: {"claim_id":"CLM-1003"}').first(),
  ).toBeVisible();
  const context = page.getByRole("region", { name: "Context used" });
  await expect(context.getByText("Claims manual, section 14")).toHaveCount(1);
  await expect(context.getByText(/Head of claims operations/)).toBeVisible();
  await expect(page.getByText("Refused by App")).toBeVisible();
  await expect(page.getByText("A previous baggage loss")).toBeVisible();

  await decide(request, "CLM-1003", {
    decision: "approve",
    payout: 500,
    section: "Section 7, Emergency Medical",
    reason: "The treatment was necessary and covered.",
  });
  const decision = page.locator("section", {
    has: page.getByRole("heading", { name: "Decision" }),
  });
  await expect(decision.getByText("Approved")).toBeVisible();
  await expect(
    decision.getByText("UnderCover Agent (configuration 1)"),
  ).toBeVisible();
  await expect(decision.getByText("2026.2")).toBeVisible();
  await expect(
    decision.getByRole("table", { name: "Payout calculation" }),
  ).toContainText("Less the deductible");
  await post(request, "/api/claims/CLM-1003/finish-memory", {
    thread_id: "e2e-1",
    messages: [
      { role: "customer", text: "Customer summary contents" },
      { role: "agent", text: "Recorded approval contents" },
    ],
  });
  await expect(page.getByText("Saved to memory")).toBeVisible();
  await page.getByText("Agent decision", { exact: true }).click();
  await expect(page.getByText("Recorded approval contents")).toBeVisible();
  await expect(heading.getByText("In progress")).toBeVisible();
  await post(request, "/api/claims/CLM-1003/payment", { amount: 500 });
  await post(request, "/api/claims/CLM-1003/steps", {
    kind: "message",
    content: "Approved and paid $500.00.",
    thread_id: "e2e-1",
  });
  await expect(heading.getByText("Decided")).toBeVisible();
  await expect(decision.getByText("Paid $500.00")).toBeVisible();
  await expect(page.getByText("Approved and paid $500.00.")).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  const widths = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    document: document.documentElement.scrollWidth,
  }));
  expect(widths.document).toBeLessThanOrEqual(widths.viewport);
});

test("a sign-off on the dashboard records a payment and the signer", async ({
  page,
  request,
}) => {
  await post(request, "/api/claims/CLM-1020/start", { thread_id: "e2e-2" });
  await decide(request, "CLM-1020", {
    decision: "approve",
    payout: 1350,
    section: "Section 5, Baggage and Personal Effects",
    reason: "The loss is covered.",
  });
  const refused = await request.post("/api/claims/CLM-1020/payment", {
    data: { amount: 1350 },
    headers: agentHeaders,
  });
  expect(refused.status()).toBe(403);

  await page.goto("/claims/CLM-1020");
  const heading = page.locator(".page-header");
  await expect(heading.getByText("Waiting for sign-off")).toBeVisible();
  expect(await resumed(request)).not.toContain("CLM-1020");
  await page.getByRole("button", { name: "Sign off" }).click();
  await expect(heading.getByText("Decided")).toBeVisible();
  await expect(
    page.getByText(/^Paid \$1,350\.00, signed off by Dana Whitfield at /),
  ).toBeVisible();
  expect(await resumed(request)).not.toContain("CLM-1020");
});
