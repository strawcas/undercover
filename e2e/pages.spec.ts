import { expect, test } from "./fixtures";

test("the queue lists every claim", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Claims queue" }),
  ).toBeVisible();
  const queue = page.getByRole("table").filter({
    has: page.getByRole("columnheader", { name: "Claimed (USD)", exact: true }),
  });
  await expect(queue.locator("tbody tr")).toHaveCount(26);
  const last = page.getByRole("row").filter({ hasText: "CLM-1901" });
  await expect(last.getByText("Waiting")).toBeVisible();
});

test("a claim opens from the queue", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "CLM-1001" }).click();
  await expect(page).toHaveURL("/claims/CLM-1001");
  await expect(page.getByText(/My flight home from Rome/)).toBeVisible();
  await expect(
    page.getByText("Hotel Aurelia Fiumicino", { exact: false }),
  ).toBeVisible();
  await expect(page.getByText("POL-2001")).toBeVisible();
});

test("the header opens the configuration", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "Configuration" }).click();
  await expect(page).toHaveURL("/configuration");
  await expect(
    page.getByRole("region", { name: "Running the queue" }),
  ).toHaveCount(0);
  const claim = page.getByRole("region", { name: "Working a claim" });
  await expect(
    claim.getByText(/10\. How we work out your payment/),
  ).toBeVisible();
  await expect(claim.getByText(/9\. Repeat claims/)).toBeVisible();
});
