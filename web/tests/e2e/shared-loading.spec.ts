import { expect, test } from "@playwright/test";

const permissions = ["validate_orders", "confirm_procurement", "floor_packing", "manage_billing", "manage_users"];
const cases = [
  { path: "/", endpoint: "/operations/summary", heading: "Today", title: "Loading operations", data: { permissions } },
  { path: "/orders/files", endpoint: "/orders/files", heading: "Order files", title: "Loading order intake", data: { items: [], total: 0, offset: 0, limit: 25, has_more: false } },
  { path: "/packing/orders", endpoint: "/packing/orders", heading: "Packing orders", title: "Loading packing floor", data: { items: [], total: 0, offset: 0, limit: 25, has_more: false } },
  { path: "/billing", endpoint: "/invoicing/orders", heading: "Billing desk", title: "Loading billing", data: [] },
  { path: "/users", endpoint: "/admin/users/register", heading: "Team & access", title: "Loading users", data: [] },
  { path: "/scan/QA-BOX", endpoint: "/packing/scan/QA-BOX", heading: "Box traceability", title: "Loading box record", data: {
    qr_code_uid: "QA-BOX", customer_code: "QA customer", box_number: 1, order_ref_id: "QA-order", route_code: "ALL", shipment_date: "2026-10-05",
    items: [], change_history: [], is_locked: true, can_edit: false,
  } },
];

test.beforeEach(async ({ context }) => {
  const payload = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  await context.addCookies([{ name: "access_token", value: `qa.${payload}.qa`, domain: "127.0.0.1", path: "/" }]);
});

for (const scenario of cases) {
  for (const outcome of ["success", "error"] as const) {
    test(`${scenario.path}: page reads stay silent after ${outcome}`, async ({ page }) => {
      await page.setViewportSize({ width: 390, height: 844 });
      let release!: () => void;
      const pending = new Promise<void>(resolve => { release = resolve; });
      await page.route("**/backend/api/v3/**", async route => {
        const path = new URL(route.request().url()).pathname;
        if (path.endsWith(scenario.endpoint)) {
          await pending;
          await route.fulfill({ status: outcome === "error" ? 500 : 200,
            json: outcome === "error" ? { detail: "Loading service unavailable" } : scenario.data });
        } else await route.fulfill({ json: path.endsWith("/auth/me")
          ? { full_name: "QA", role: "OWNER_ADMIN", must_change_password: false, permissions }
          : path.endsWith("/operations/summary") ? { permissions } : { items: [], total: 0, offset: 0, limit: 25, has_more: false } });
      });
      const request = page.waitForRequest(request => new URL(request.url()).pathname.endsWith(scenario.endpoint));
      await page.goto(scenario.path);
      await request;
      const modal = page.getByRole("dialog", { name: scenario.title });
      await expect(modal).toHaveCount(0);
      await expect(page.getByRole("button", { name: "Menu", exact: true })).toBeVisible();
      if (["/orders/files", "/packing/orders"].includes(scenario.path)) {
        const skeleton = page.getByRole("status", { name: "Loading records…", exact: true });
        await expect(skeleton).toBeVisible();
        await expect(skeleton.locator(".skeleton").first()).toHaveCSS("animation-name", "skeleton-shimmer");
      }
      release();
      await expect(modal).toHaveCount(0);
      await expect(page.getByRole("heading", { name: scenario.heading, exact: true })).toBeVisible();
      if (outcome === "error") await expect(page.getByRole("alert").filter({ hasText: scenario.path === "/" ? "The overview could not be refreshed. Retry to see current totals." : "Loading service unavailable" }).first()).toBeVisible();
      expect(await page.evaluate(() => getComputedStyle(document.body).overflow)).not.toBe("hidden");
    });
  }
}

test("switching tabs stays interactive while procurement is still fetching", async ({ page }) => {
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/backend/api/v3/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/procurement/batches")) await pending;
    await route.fulfill({ json: path.endsWith("/auth/me")
      ? { full_name: "QA", role: "OWNER_ADMIN", must_change_password: false, permissions }
      : path.endsWith("/operations/summary") ? { permissions }
      : path.endsWith("/source-orders") ? [] : [] });
  });
  await page.goto("/billing");
  await expect(page.getByRole("heading", { name: "Billing desk", exact: true })).toBeVisible();
  const nav = page.getByRole("navigation", { name: "Primary navigation" });
  const request = page.waitForRequest("**/procurement/batches?*");
  await nav.getByRole("link", { name: "Purchasing", exact: true }).click();
  await request;
  await expect(page.locator(".loading-dialog")).toHaveCount(0);
  await nav.getByRole("link", { name: "Billing", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Billing desk", exact: true })).toBeVisible();
  await expect(page.locator(".loading-dialog")).toHaveCount(0);
  release();
});

test("parallel order reads keep navigation available until both finish", async ({ page }) => {
  let releaseQueue!: () => void;
  let releaseCustomers!: () => void;
  const queue = new Promise<void>(resolve => { releaseQueue = resolve; });
  const customers = new Promise<void>(resolve => { releaseCustomers = resolve; });
  await page.route("**/backend/api/v3/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/operations/summary")) await queue;
    if (path.endsWith("/master-data/customer-register")) await customers;
    await route.fulfill({ json: path.endsWith("/auth/me")
      ? { full_name: "QA", role: "OWNER_ADMIN", must_change_password: false, permissions }
      : path.endsWith("/operations/summary") ? { permissions } : { items: [], total: 0, offset: 0, limit: 25, has_more: false } });
  });
  const requests = Promise.all([
    page.waitForRequest("**/operations/summary"),
    page.waitForRequest("**/master-data/customer-register?*"),
  ]);
  await page.goto("/orders");
  await requests;
  const modal = page.getByRole("dialog", { name: "Loading order intake" });
  await expect(modal).toHaveCount(0);
  const response = page.waitForResponse("**/operations/summary");
  releaseQueue();
  await response;
  await expect(modal).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "Primary navigation" })).toBeVisible();
  releaseCustomers();
  await expect(modal).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Orders" })).toBeVisible();
});

test("sign-in uses the shared modal and reveals request errors", async ({ page }) => {
  await page.context().clearCookies();
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/backend/api/v3/auth/login", async route => {
    await pending;
    await route.fulfill({ status: 401, json: { detail: "Invalid email or password" } });
  });
  await page.goto("/login");
  await page.getByLabel("Email").fill("qa@example.invalid");
  await page.getByLabel("Password").fill("test-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  const modal = page.getByRole("dialog", { name: "Signing in" });
  await expect(modal).toBeVisible();
  release();
  await expect(modal).toHaveCount(0);
  await expect(page.getByText("Invalid email or password", { exact: true })).toBeVisible();
});

test("batch register reads stay silent and keep navigation available", async ({ page }) => {
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/backend/api/v3/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/procurement/batches")) {
      await pending;
      await route.fulfill({ json: { items: [], has_more: false } });
    } else await route.fulfill({ json: path.endsWith("/auth/me")
      ? { full_name: "QA", role: "OWNER_ADMIN", must_change_password: false, permissions }
      : path.endsWith("/operations/summary") ? { permissions }
      : path.endsWith("/latest-batch") ? null : [] });
  });
  await page.goto("/procurement");
  const request = page.waitForRequest("**/procurement/batches?*");
  await page.getByRole("link", { name: "Choose batch", exact: true }).click();
  await request;
  const modal = page.getByRole("dialog", { name: "Loading procurement batch" });
  await expect(modal).toHaveCount(0);
  release();
  await expect(modal).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Purchase batches", exact: true })).toBeVisible();
  expect(await page.evaluate(() => getComputedStyle(document.body).overflow)).not.toBe("hidden");
  await page.getByRole("navigation", { name: "Primary navigation" }).getByRole("link", { name: "Billing", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Billing desk", exact: true })).toBeVisible();
});
