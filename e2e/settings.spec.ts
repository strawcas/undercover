import { expect, test } from "./fixtures";

test("a senior handler confirms a setting and remembers the browser theme", async ({
  page,
  request,
}) => {
  await page.getByRole("link", { name: "Settings", exact: true }).click();
  const agents = page.getByLabel("Agents at once in round evaluations");
  await agents.fill("8");
  const accuracy = page.getByLabel("Held-out accuracy floor (%)", {
    exact: true,
  });
  await expect(accuracy).toHaveValue("60");
  await accuracy.fill("72.5");
  await page.getByLabel("Accuracy noise (percentage points)").fill("7.25");
  await page.getByLabel("Cost per claim tolerance (%)").fill("125");
  await page.getByRole("button", { name: "Save settings" }).click();
  const dialog = page.getByRole("dialog", { name: "Save platform settings?" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("72.5%", { exact: true })).toBeVisible();
  await expect(
    dialog.getByText("7.25 percentage points", { exact: true }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("button", { name: "Save settings" }),
  ).toBeFocused();
  const unchanged = await request.get("/api/settings");
  const before = (await unchanged.json()) as {
    values: { concurrency: number; objectives: { accuracy: number } };
  };
  expect(before.values.concurrency).toBe(25);
  expect(before.values.objectives.accuracy).toBeCloseTo(0.6, 10);
  await page.getByRole("button", { name: "Save settings" }).click();
  await dialog.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByRole("status")).toHaveText("Settings saved.");
  const saved = await request.get("/api/settings");
  const after = (await saved.json()) as {
    values: {
      concurrency: number;
      objectives: { accuracy: number };
      noise: { accuracy: number };
      tolerance: { cost_per_claim: number };
    };
    changes: { handler: string }[];
  };
  expect(after.values.concurrency).toBe(8);
  expect(after.values.objectives.accuracy).toBeCloseTo(0.725, 10);
  expect(after.values.noise.accuracy).toBeCloseTo(0.0725, 10);
  expect(after.values.tolerance.cost_per_claim).toBe(1.25);
  expect(after.changes[0].handler).toBe("Dana Whitfield");
  const history = page.getByRole("region", { name: "Settings history" });
  await history.getByText("View changes", { exact: true }).click();
  await expect(
    history
      .getByRole("region", { name: "Before settings change" })
      .getByText("60%", { exact: true }),
  ).toBeVisible();
  await expect(
    history
      .getByRole("region", { name: "After settings change" })
      .getByText("72.5%", { exact: true }),
  ).toBeVisible();
  await page.getByLabel("Theme").selectOption("dark");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.reload();
  await expect(accuracy).toHaveValue("72.5");
  await expect(page.getByLabel("Theme")).toHaveValue("dark");
  await page.getByLabel("Theme").selectOption("light");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.getByLabel("Theme").selectOption("system");
  await expect(page.locator("html")).not.toHaveAttribute("data-theme");
});
