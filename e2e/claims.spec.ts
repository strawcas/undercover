import { expect, test } from "@playwright/test";

test("approves, shows live activity and records an eligible payment", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Run agent" })).toHaveCount(0);
  await expect(
    page.getByRole("list", { name: "Claim activity" }),
  ).toContainText("Run agent");
  const duplicate = await request.post("/api/claims/CLM-1001/runs");
  expect(duplicate.status()).toBe(404);
  await expect(page.getByText(/Simulated payment recorded/)).toBeVisible({
    timeout: 10000,
  });
  await expect(
    page.getByText("Policy section 3", { exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(page.getByText(/Simulated payment recorded/)).toBeVisible();
  const repeat = await request.post("/api/claims/CLM-1001/pay", {
    data: { amount: "420" },
  });
  expect(repeat.status()).toBe(409);
});

test("requires handler sign-off before paying more than USD 1,000", async ({
  page,
  request,
}) => {
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
  await page.getByRole("button", { name: "Record simulated payment" }).click();
  await expect(page.getByText(/Simulated payment recorded/)).toContainText(
    "$1,250.00",
  );
  await page.reload();
  await page.getByRole("button", { name: "Open CLM-1003" }).click();
  await expect(page.getByText(/Signed off by Dana Whitfield/)).toBeVisible();
  await expect(page.getByText(/Simulated payment recorded/)).toBeVisible();
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

test("shows a failed attempt while automatic processing continues", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Open CLM-1005" }).click();
  await expect(page.getByText(/Agent run failed/)).toBeVisible({
    timeout: 10000,
  });
  await expect(page.getByText("Assessment paused")).toBeVisible();
  await expect(page.getByRole("button", { name: "Run agent" })).toHaveCount(0);
  await expect
    .poll(async () => {
      const next = await request.get("/api/claims/CLM-1006");
      const detail = (await next.json()) as { status: string };
      return detail.status;
    })
    .toBe("decided");
  await expect(page.getByText(/Agent run failed/)).toBeVisible();
});
