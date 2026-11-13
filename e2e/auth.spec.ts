import { expect, test } from "@playwright/test";

test("a protected deep link signs in and signs out without registration", async ({
  page,
}) => {
  await page.goto("/claims/CLM-1001");
  await expect(
    page.getByRole("heading", { name: "Sign in to UnderCover" }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: /sign up/i })).toHaveCount(0);
  await page
    .getByLabel("Email", { exact: true })
    .fill("marcus.oyelaran@undercover.example");
  await page.getByLabel("Password", { exact: true }).fill("incorrect");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveText("Invalid email or password");
  await page.getByLabel("Password", { exact: true }).fill("offline-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(
    page.getByText("Marcus Oyelaran", { exact: true }),
  ).toBeVisible();
  await expect(page).toHaveURL("/claims/CLM-1001");
  await page.reload();
  await expect(
    page.getByText("Marcus Oyelaran", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(
    page.getByRole("heading", { name: "Sign in to UnderCover" }),
  ).toBeVisible();
});
