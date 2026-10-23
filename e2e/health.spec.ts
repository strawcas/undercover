import { expect, test } from "@playwright/test";

test("the dashboard loads the real pilot queue and read-only instructions", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByRole("button", { name: /^Open CLM-/ })).toHaveCount(
    25,
  );
  await expect(
    page.getByRole("heading", { name: "Claims desk." }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Agent instructions" }).click();
  await expect(page.locator(".instructions pre")).toContainText(
    "You are the UnderCover Agent",
  );
  await expect(page.getByRole("textbox")).toHaveCount(0);
});
