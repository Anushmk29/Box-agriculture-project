import { expect, test } from "@playwright/test";

test("failed global logout stays visible and can be retried before navigating to login", async ({ page, context }) => {
  const payload = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  await context.addCookies([{ name: "access_token", value: `qa.${payload}.qa`, domain: "127.0.0.1", path: "/", httpOnly: true }]);
  let attempts = 0;
  await page.route("**/backend/api/v3/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { full_name: "Finance", role: "GENERAL_ACCOUNTING",
      must_change_password: false, permissions: ["manage_billing", "draft_invoice"] } });
    if (path.endsWith("/auth/logout")) {
      attempts++;
      return route.fulfill(attempts === 1 ? { status: 500, json: { detail: "Database unavailable" } }
        : { status: 204, headers: { "set-cookie": "access_token=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax" } });
    }
    if (path.endsWith("/invoicing/records")) return route.fulfill({ json: { items: [], total: 0, offset: 0, limit: 25, has_more: false } });
    return route.fulfill({ json: {} });
  });
  await page.goto("/billing?tab=invoices");
  await page.getByRole("button", { name: "Sign out everywhere", exact: true }).click();
  await expect(page.getByText("Sign out on all devices could not be confirmed. Please try again.")).toBeVisible();
  await expect(page).toHaveURL(/\/billing\?tab=invoices/);
  expect(attempts).toBe(1);
  await page.getByRole("button", { name: "Sign out everywhere", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Sign in", exact: true })).toBeVisible();
  expect(attempts).toBe(2);
});
