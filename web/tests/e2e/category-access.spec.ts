import { expect, test, type Page } from "@playwright/test";

const generalPermissions = ["validate_orders", "confirm_procurement", "design_packing", "floor_packing"];

async function mockSession(page: Page, session: () => { role: string; permissions: string[] }) {
  const payload = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  await page.context().addCookies([{
    name: "access_token", value: `header.${payload}.test`, domain: "127.0.0.1", path: "/", httpOnly: true,
  }]);
  await page.route("**/backend/api/v3/**", route => route.fulfill({ json: [] }));
  await page.route("**/backend/api/v3/operations/summary", route => route.fulfill({ json: { permissions: session().permissions } }));
  await page.route("**/backend/api/v3/auth/me", route => route.fulfill({ json: {
    full_name: "Category Tester", must_change_password: false, ...session(),
  } }));
}

test("General cannot mount billing or account management pages through a direct URL", async ({ page }) => {
  await mockSession(page, () => ({ role: "GENERAL", permissions: generalPermissions }));
  const restrictedRequests: string[] = [];
  page.on("request", request => {
    if (/\/api\/v3\/(invoicing|admin\/users)/.test(request.url())) restrictedRequests.push(request.url());
  });
  for (const route of ["/billing", "/users"]) {
    await page.goto(route);
    await expect(page).toHaveURL(new RegExp(`${route}$`));
    await expect(page.getByRole("heading", { name: "Access unavailable" })).toBeVisible();
    await expect(page.getByRole("navigation").getByRole("link", { name: "Billing" })).toHaveCount(0);
    await expect(page.getByRole("navigation").getByRole("link", { name: "Team & access" })).toHaveCount(0);
  }
  expect(restrictedRequests).toEqual([]);
});

test("an active account loses the Users page after a category downgrade", async ({ page }) => {
  let role = "OWNER_ADMIN";
  await mockSession(page, () => ({ role, permissions: role === "OWNER_ADMIN" ? [...generalPermissions, "manage_users"] : generalPermissions }));
  await page.goto("/users");
  await expect(page.getByRole("heading", { name: "Team & access" })).toBeVisible();
  role = "GENERAL";
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.getByRole("heading", { name: "Access unavailable" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Team & access" })).toHaveCount(0);
  await expect(page.getByRole("navigation").getByRole("link", { name: "Team & access" })).toHaveCount(0);
});

test("account creation previews the permissions of its assigned category", async ({ page }) => {
  await mockSession(page, () => ({ role: "OWNER_ADMIN", permissions: [...generalPermissions, "manage_users"] }));
  await page.route("**/backend/api/v3/admin/users/access-levels", route => route.fulfill({ json: [
    { role: "GENERAL", permissions: generalPermissions },
    { role: "GENERAL_ACCOUNTING", permissions: [...generalPermissions, "draft_invoice", "manage_billing"] },
    { role: "OWNER_ADMIN", permissions: [...generalPermissions, "draft_invoice", "manage_billing", "manage_users", "lock_final_pack"] },
  ] }));
  await page.goto("/users");
  await page.getByRole("button", { name: "Add person", exact: true }).click();
  const preview = page.locator("form p[aria-live]");
  await expect(preview).toContainText("Order intake");
  await expect(preview).not.toContainText("Billing and payments");
  await page.getByLabel("Access level", { exact: true }).selectOption("GENERAL_ACCOUNTING");
  await expect(preview).toContainText("Billing and payments");
  await expect(preview).not.toContainText("Manage accounts");
  await page.getByLabel("Access level", { exact: true }).selectOption("OWNER_ADMIN");
  await expect(preview).toContainText("Manage accounts");
  await expect(preview).toContainText("Approve final packing");
});
