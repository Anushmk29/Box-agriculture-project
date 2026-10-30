import { expect, test } from "@playwright/test";

test("field validation failures show an actionable message without echoing credentials", async ({ page }) => {
  await page.route("**/backend/api/v3/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/login")) {
      await route.fulfill({ status: 422, json: { detail: [{ type: "value_error",
        loc: ["body", "password"], msg: "Value error, Password requires valid Unicode characters" }] } });
    } else if (path.endsWith("/auth/me")) {
      await route.fulfill({ status: 401, json: { detail: "Valid authentication is required" } });
    } else await route.fulfill({ json: {} });
  });
  await page.goto("/login");
  await page.getByLabel("Email", { exact: true }).fill("validation@example.invalid");
  await page.getByLabel("Password", { exact: true }).fill("private-password-123");
  await page.getByRole("button", { name: /sign in/i }).click();
  await expect(page.getByText("Password: Password requires valid Unicode characters", { exact: true })).toBeVisible();
  await expect(page.getByText("Request failed with status 422", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("main").getByRole("alert")).not.toContainText("private-password-123");
});
