import { expect, test } from "./fixtures";

test("a senior handler confirms a setting and remembers the browser theme", async ({
  page,
  request,
}) => {
  await page.getByRole("link", { name: "Settings", exact: true }).click();
  const agents = page.getByLabel("Agents at once in round evaluations");
  await agents.fill("8");
  await page.getByRole("button", { name: "Save settings" }).click();
  const dialog = page.getByRole("dialog", { name: "Save platform settings?" });
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("button", { name: "Save settings" }),
  ).toBeFocused();
  const unchanged = await request.get("/api/settings");
  const before = (await unchanged.json()) as {
    values: { concurrency: number };
  };
  expect(before.values.concurrency).toBe(25);
  await page.getByRole("button", { name: "Save settings" }).click();
  await dialog.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByRole("status")).toHaveText("Settings saved.");
  const saved = await request.get("/api/settings");
  const after = (await saved.json()) as {
    values: { concurrency: number };
    changes: { handler: string }[];
  };
  expect(after.values.concurrency).toBe(8);
  expect(after.changes[0].handler).toBe("Dana Whitfield");
  await page.getByLabel("Theme").selectOption("dark");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.reload();
  await expect(page.getByLabel("Theme")).toHaveValue("dark");
  await page.getByLabel("Theme").selectOption("light");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.getByLabel("Theme").selectOption("system");
  await expect(page.locator("html")).not.toHaveAttribute("data-theme");
});
