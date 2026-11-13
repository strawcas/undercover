import { test as base, expect } from "@playwright/test";
import { AUTH_STAND_IN } from "./addresses";

export { expect } from "@playwright/test";

export const test = base.extend({
  request: async ({ playwright, baseURL }, use) => {
    const auth = await playwright.request.newContext();
    const response = await auth.post(
      `${AUTH_STAND_IN}/auth/v1/token?grant_type=password`,
      {
        data: {
          email: "dana.whitfield@undercover.example",
          password: "offline-password",
        },
      },
    );
    const data = (await response.json()) as { access_token: string };
    const request = await playwright.request.newContext({
      baseURL,
      extraHTTPHeaders: { Authorization: `Bearer ${data.access_token}` },
    });
    await use(request);
    await request.dispose();
    await auth.dispose();
  },
  page: async ({ page }, use) => {
    await page.goto("/");
    await page
      .getByLabel("Email", { exact: true })
      .fill("dana.whitfield@undercover.example");
    await page.getByLabel("Password", { exact: true }).fill("offline-password");
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
    await use(page);
  },
});
