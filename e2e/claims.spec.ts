import { expect, test } from "@playwright/test";
import { ok } from "node:assert";
import { writeFile } from "node:fs/promises";

test("approves, shows live activity and records an eligible payment", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await expect(page.getByText(/No run yet/)).toBeVisible();
  await page.reload();
  await expect(page.getByText(/No run yet/)).toBeVisible();
  const gate = process.env.UNDERCOVER_TEST_GATE;
  ok(gate, "The explicit test command gate is missing.");
  await writeFile(gate, "uv run -m agent");
  await expect(page.getByRole("button", { name: "Run agent" })).toHaveCount(0);
  await expect(
    page.getByRole("list", { name: "Claim activity" }),
  ).toContainText("Run agent", { timeout: 10000 });
  const duplicate = await request.post("/api/claims/CLM-1001/runs");
  expect(duplicate.status()).toBe(404);
  await expect(page.getByText(/Simulated payment recorded/)).toBeVisible({
    timeout: 10000,
  });
  await expect(
    page.getByText("Policy section 3", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Queue run progress" }),
  ).toContainText("assessment attempts");
  await expect(
    page.getByRole("list", { name: "Run to-do list" }),
  ).toContainText("CLM-1001");
  await page
    .getByRole("region", { name: "Sections read" })
    .getByText(/Claims manual · 6/)
    .click();
  await expect(
    page.getByRole("region", { name: "Sections read" }),
  ).toContainText("Version 2026.2");
  await page.reload();
  await expect(page.getByText(/Simulated payment recorded/)).toBeVisible();
  const eventsBefore = await request.get("/api/claims/CLM-1001/events");
  const repeat = await request.post("/api/claims/CLM-1001/pay", {
    data: { amount: "420" },
  });
  expect(repeat.status()).toBe(201);
  const eventsAfter = await request.get("/api/claims/CLM-1001/events");
  expect(await eventsAfter.json()).toEqual(await eventsBefore.json());
});

for (const [id, outcome] of [
  ["CLM-1002", "Decline"],
  ["CLM-1004", "Refer"],
]) {
  test(`records a ${outcome} decision without payment`, async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: `Open ${id}` }).click();
    await expect(
      page
        .getByRole("region", { name: "Agent decision" })
        .getByRole("heading", { name: outcome }),
    ).toBeVisible({ timeout: 10000 });
    await expect(
      page.getByRole("button", { name: /payment|Sign off/ }),
    ).toHaveCount(0);
  });
}

test("retries a failed claim while other claims continue", async ({
  page,
  request,
}) => {
  test.setTimeout(45000);
  await page.goto("/");
  await page.getByRole("button", { name: "Open CLM-1005" }).click();
  await expect(page.getByText(/Agent run failed/)).toBeVisible({
    timeout: 10000,
  });
  await expect(page.getByRole("button", { name: "Run agent" })).toHaveCount(0);
  await expect
    .poll(async () => {
      const next = await request.get("/api/claims/CLM-1007");
      const detail = (await next.json()) as { status: string };
      return detail.status;
    })
    .toBe("decided");
  await expect(page.getByText(/Agent run failed/)).toBeVisible();
  await expect(page.getByText(/Simulated payment recorded/)).toBeVisible({
    timeout: 35000,
  });
  const detail = await request.get("/api/claims/CLM-1005");
  expect(
    ((await detail.json()) as { failed_attempts: number }).failed_attempts,
  ).toBe(1);
});

test("hands two failed assessments to a handler without an insurance decision", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Open CLM-1006" }).click();
  await expect(page.getByText("Handler review required")).toBeVisible({
    timeout: 10000,
  });
  await expect(
    page.getByRole("region", { name: "Agent decision" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("region", { name: "Queue run progress" }),
  ).toContainText("Two failed assessments; no decision recorded");
});

test("leaves later arrivals waiting after the explicit command and browser refresh", async ({
  page,
  request,
}) => {
  test.setTimeout(45000);
  await expect
    .poll(
      async () => {
        const response = await request.get("/api/claims/CLM-1025");
        const detail = (await response.json()) as {
          status: string;
          run_active: boolean;
        };
        return detail.status === "decided" && !detail.run_active;
      },
      { timeout: 40000 },
    )
    .toBe(true);
  await page.goto("/");
  await expect(page.getByText(/Completion-time snapshot/)).toBeVisible();
  await page.getByRole("button", { name: "Open TEST-LATER" }).click();
  await expect(page.getByText("Waiting for assessment")).toBeVisible();
  await expect(page.getByRole("button", { name: "Run agent" })).toHaveCount(0);
  await page.getByRole("button", { name: "Agent instructions" }).click();
  await page.getByRole("button", { name: "Claims queue", exact: true }).click();
  await expect(page.getByText("Waiting for assessment")).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "Open TEST-LATER" }).click();
  await expect(page.getByText("Waiting for assessment")).toBeVisible();
  const events = await request.get("/api/claims/TEST-LATER/events");
  expect(await events.json()).toEqual([]);
});

test("saves handler sign-off and notifies the isolated agent service", async ({
  page,
  request,
}) => {
  await expect
    .poll(
      async () => {
        const response = await request.get("/api/agent-runs/current");
        const result = (await response.json()) as {
          run: { status: string } | null;
        };
        return result.run?.status;
      },
      { timeout: 10000 },
    )
    .toBe("completed");
  await page.goto("/");
  await page.getByRole("button", { name: "Open CLM-1003" }).click();
  await expect(
    page.getByRole("heading", { name: "Handler sign-off required" }),
  ).toBeVisible({ timeout: 10000 });
  await expect(
    page.getByRole("button", { name: "Record simulated payment" }),
  ).toHaveCount(0);
  const blocked = await request.post("/api/claims/CLM-1003/pay", {
    data: { amount: "1250.00" },
  });
  expect(blocked.status()).toBe(409);
  await page.getByLabel("Reviewing handler").selectOption("HND-01");
  await page.getByRole("button", { name: "Sign off payout" }).click();
  await expect(page.getByText(/Signed off by Dana Whitfield/)).toBeVisible();
  await expect(page.getByText("Payment in progress")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Record simulated payment" }),
  ).toHaveCount(0);
  await expect
    .poll(async () => {
      const response = await request.get("http://127.0.0.1:8003/notifications");
      return (await response.json()) as unknown[];
    })
    .toHaveLength(1);
  const notices = await request.get("http://127.0.0.1:8003/notifications");
  const saved = (await notices.json()) as {
    claim_id: string;
    sign_off: { handler_name: string; created_at: string };
  }[];
  expect(saved).toHaveLength(1);
  expect(saved[0].claim_id).toBe("CLM-1003");
  expect(saved[0].sign_off.handler_name).toBe("Dana Whitfield");
  expect(saved[0].sign_off.created_at).toBeTruthy();
  await page.reload();
  await page.getByRole("button", { name: "Open CLM-1003" }).click();
  await expect(page.getByText(/Signed off by Dana Whitfield/)).toBeVisible();
  await expect(page.getByText("Payment in progress")).toBeVisible();
});
