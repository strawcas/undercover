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
  await expect(
    page.getByRole("heading", { name: "Running the queue" }),
  ).toBeVisible();
  await expect(page.locator(".instructions pre").first()).toContainText(
    "You are the UnderCover queue agent",
  );
  await expect(page.locator(".instructions pre").last()).toContainText(
    "You are the UnderCover claim agent",
  );
  await expect(page.getByRole("textbox")).toHaveCount(0);
});
