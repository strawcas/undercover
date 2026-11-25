import { expect, test } from "./fixtures";
import { decide, post } from "./api";

test("a handler can reload and answer a Copilot confirmation", async ({
  page,
  request,
}) => {
  for (const claimId of ["CLM-1021", "CLM-1023"]) {
    await post(request, `/api/claims/${claimId}/start`, {
      thread_id: `copilot-${claimId}`,
    });
    await decide(request, claimId, {
      decision: "approve",
      payout: 1350,
      section: "Section 7",
      reason: "The policy covers this loss.",
    });
  }
  const sidebar = page.getByRole("complementary", {
    name: "UnderCover Copilot",
  });
  await expect(
    sidebar.getByRole("heading", { name: "Hi, Dana." }),
  ).toBeVisible();
  const composer = sidebar.getByRole("textbox", { name: "Ask the Copilot" });
  await composer.fill("Look up and sign off");
  await composer.press("Enter");
  await expect(sidebar.getByText("Copilot is working")).toBeVisible();
  const card = sidebar.getByRole("region", { name: "Confirm payout" });
  await expect(card.getByText("$1,350.00", { exact: false })).toBeVisible();
  await expect(composer).toBeDisabled();
  await sidebar.getByText("lookup_term", { exact: true }).click();
  await expect(sidebar.getByText(/glossary.yaml/)).toBeVisible();
  await expect(sidebar.getByText("Refused", { exact: false })).toBeVisible();
  await page.reload();
  await card.getByRole("button", { name: "Sign off and pay" }).click();
  await expect(sidebar.getByRole("table")).toBeVisible();
  await expect(
    sidebar.getByRole("link", { name: "CLM-1021" }).first(),
  ).toHaveAttribute("href", "/claims/CLM-1021");
  await expect(
    page.getByRole("main").getByRole("row").filter({ hasText: "CLM-1021" }),
  ).toContainText("Decided");
  await expect(sidebar.getByText(/2 model calls/)).toBeVisible();
  await sidebar.getByRole("button", { name: "New conversation" }).click();
  await composer.fill("Another sign-off");
  await composer.press("Enter");
  await card.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(
    sidebar.getByText(/What would you like me to do next/),
  ).toBeVisible();
  await sidebar.getByRole("button", { name: "Collapse Copilot" }).click();
  await page.reload();
  await expect(sidebar).toHaveCount(0);
  await page.getByRole("button", { name: "Copilot", exact: true }).click();
  await expect(
    sidebar.getByRole("heading", { name: "UnderCover Copilot" }),
  ).toBeVisible();
});
