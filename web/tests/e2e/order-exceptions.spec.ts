import { expect, test, type Page } from "@playwright/test";

async function setup(page: Page, permissions = ["resolve_orders", "manage_billing"]) {
  const payload = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  await page.context().addCookies([{ name: "access_token", value: `qa.${payload}.qa`, domain: "127.0.0.1", path: "/" }]);
  await page.route("**/backend/api/v3/**", route => route.fulfill({ json:
    new URL(route.request().url()).pathname.endsWith("/auth/me")
      ? { user_id: "internal-staff", full_name: "Staff", role: "GENERAL_ACCOUNTING", must_change_password: false,
          permissions, request_replay_supported: true }
      : { items: [], total: 0, offset: 0, limit: 25, has_more: false } }));
}

test("full shortage resolution survives a lost response and refresh using its original key", async ({ page }) => {
  await setup(page);
  const record = { order_id: "short", order_ref_id: "ORDER-SHORT", customer_name: "Shop", revision: 1,
    status: "HANDOFF_01_SENT", can_close_unavailable: true, can_cancel: false, can_record_manual_cancellation: false };
  const keys: string[] = []; const bodies: string[] = [];
  await page.route("**/backend/api/v3/order-exceptions/short", route => route.fulfill({ json: record }));
  await page.route("**/backend/api/v3/order-exceptions/short/resolve", route => {
    keys.push(route.request().headers()["idempotency-key"]); bodies.push(route.request().postData() || "");
    record.status = "UNAVAILABLE"; record.revision = 2; record.can_close_unavailable = false;
    return keys.length === 1 ? route.abort("connectionreset") : route.fulfill({ json: { order_id: "short", status: "UNAVAILABLE" } });
  });
  await page.goto("/exceptions?orderId=short");
  await expect(page).toHaveURL(/\/orders\/attention\?orderId=short/);
  await page.getByLabel("Reason / manual resolution details").fill("All requested produce unavailable today");
  await page.getByRole("button", { name: "Close as unavailable", exact: true }).click();
  await expect(page.getByRole("button", { name: "Retry pending resolution" })).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "Retry pending resolution" }).click();
  await expect(page).toHaveURL(/\/orders\/attention\?view=completed/);
  expect(keys).toHaveLength(2); expect(keys[0]).toBeTruthy(); expect(keys[1]).toBe(keys[0]);
  expect(bodies[1]).toBe(bodies[0]);
});

for (const width of [1440, 390]) {
  test(`one Orders menu opens files and Needs attention at ${width}px`, async ({ page }) => {
    await setup(page, ["validate_orders", "resolve_orders"]);
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/orders/files");
    if (width === 390) await page.getByRole("button", { name: "Menu", exact: true }).click();
    const navigation = page.getByRole("navigation", { name: "Primary navigation", exact: true }).filter({ visible: true });
    await expect(navigation.getByRole("link", { name: /^Orders/ })).toHaveCount(1);
    await expect(navigation.getByRole("link", { name: "Order exceptions", exact: true })).toHaveCount(0);
    await expect(navigation.getByRole("link", { name: /^Orders/ })).toHaveAttribute("href", "/orders");
    await expect(navigation.getByRole("link", { name: /^Orders/ })).not.toHaveAttribute("aria-current");
    await expect(navigation.getByRole("link", { name: "Browse order files", exact: true })).toHaveAttribute("aria-current", "page");
    if (width === 390) await page.keyboard.press("Escape");
    const sections = page.getByRole("navigation", { name: "Order sections", exact: true });
    await sections.getByRole("link", { name: "Needs attention", exact: true }).click();
    await expect(page).toHaveURL(/\/orders\/attention$/);
    await expect(page.getByRole("heading", { name: "Orders needing attention", exact: true })).toBeVisible();
    await expect(page.locator(".register-empty")).toContainText("No orders need shortage resolution");
    await expect(page.getByRole("region", { name: "Next step guidance", exact: true })).toContainText("Find orders needing attention");
    await expect(sections.getByRole("link", { name: "Needs attention", exact: true })).toHaveAttribute("aria-current", "page");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `../outputs/orders-unified-attention-${width}.png`, fullPage: true });
    await sections.getByRole("link", { name: "Order files", exact: true }).click();
    await expect(page).toHaveURL(/\/orders\/files$/);
    await expect(page.getByRole("heading", { name: "Order files", exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "Upload order", exact: true })).toBeVisible();
  });
}

test("cancellation opens from the attention list and closing detail preserves its filters", async ({ page }) => {
  await setup(page, ["validate_orders", "resolve_orders"]);
  const record = { order_id: "cancel", order_ref_id: "ORDER-CANCEL", customer: "Shop", customer_name: "Shop", revision: 3,
    status: "PURCHASING_CONSOLIDATED", can_close_unavailable: false, can_cancel: true, can_record_manual_cancellation: false };
  await page.route("**/backend/api/v3/order-exceptions?*", route => route.fulfill({ json: { items: [record], total: 1, offset: 0, limit: 25, has_more: false } }));
  await page.route("**/backend/api/v3/order-exceptions/cancel", route => route.fulfill({ json: record }));
  let saved: unknown;
  await page.route("**/backend/api/v3/order-exceptions/cancel/resolve", route => {
    saved = route.request().postDataJSON();
    return route.fulfill({ json: { order_id: "cancel", status: "CANCELLED" } });
  });
  await page.goto("/orders/attention?view=all&q=Shop");
  await page.getByRole("link", { name: /^Review order/ }).click();
  await expect(page.getByRole("dialog", { name: "ORDER-CANCEL", exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL(/\/orders\/attention\?view=all&q=Shop$/);
  await page.getByRole("link", { name: /^Review order/ }).click();
  await page.getByLabel("Reason / manual resolution details").fill("Customer cancelled before buying");
  await page.getByRole("button", { name: "Cancel before buying", exact: true }).click();
  await expect(page).toHaveURL(/\/orders\/attention\?view=completed$/);
  expect(saved).toEqual({ outcome: "CANCELLED", reason: "Customer cancelled before buying", expected_revision: 3, manual_resolution_confirmed: false });
});

test("file reviewers cannot access resolution actions without resolution permission", async ({ page }) => {
  await setup(page, ["validate_orders"]);
  const resolutionRequests: string[] = [];
  page.on("request", request => { if (request.url().includes("/order-exceptions")) resolutionRequests.push(request.url()); });
  await page.goto("/orders/files");
  await expect(page.getByRole("navigation", { name: "Order sections" }).getByRole("link", { name: "Needs attention", exact: true })).toHaveCount(0);
  await page.goto("/orders/attention?orderId=short");
  await expect(page.getByRole("heading", { name: "Access unavailable", exact: true })).toBeVisible();
  expect(resolutionRequests).toEqual([]);
});

test("resolution-only staff keep one Orders entry and legacy links retain their record and filters", async ({ page }) => {
  await setup(page, ["resolve_orders"]);
  await page.route("**/backend/api/v3/order-exceptions/legacy", route => route.fulfill({ json: {
    order_id: "legacy", order_ref_id: "LEGACY-ORDER", customer_name: "Shop", status: "CANCELLED", revision: 1,
  } }));
  await page.goto("/exceptions?orderId=legacy&view=all&q=Shop");
  await expect(page).toHaveURL(/\/orders\/attention\?orderId=legacy&view=all&q=Shop$/);
  await expect(page.getByRole("dialog", { name: "LEGACY-ORDER", exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  const sections = page.getByRole("navigation", { name: "Order sections", exact: true });
  await expect(sections.getByRole("link", { name: "Order files", exact: true })).toHaveCount(0);
  await expect(sections.getByRole("link", { name: "Needs attention", exact: true })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Primary navigation", exact: true }).getByRole("link", { name: /^Orders/ })).toHaveAttribute("href", "/orders/attention");
  await page.goto(`/exceptions?orderId=legacy&returnTo=${encodeURIComponent("/exceptions?view=completed&q=Shop")}`);
  await expect(page.getByRole("dialog", { name: "LEGACY-ORDER", exact: true })).toBeVisible();
  expect(new URL(page.url()).searchParams.get("returnTo")).toBe("/orders/attention?view=completed&q=Shop");
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL(/\/orders\/attention\?view=completed&q=Shop$/);
});

test("final invoice has print access and no payment or receipt actions", async ({ page }) => {
  await setup(page);
  await page.route("**/backend/api/v3/invoicing/final", route => route.fulfill({ json: {
    invoice_id: "final", revision: 2, client_order_id: "order", invoice_number: "INV-FINAL", invoice_type: "ON_BEHALF_SUB_CUSTOMER",
    main_customer_seller: "Seller", sub_customer_buyer: "Shop", subtotal_eur: 10, total_amount_eur: 10,
    amount_paid_eur: 0, currency: "THB", status: "INVOICE_SENT", items: [], internal_notes: null, source_order_count: 1,
  } }));
  await page.goto("/billing?invoiceId=final");
  await expect(page.getByText(/Invoice creation is complete/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Open printable invoice", exact: true })).toBeVisible();
  await expect(page.getByLabel("Cleared amount")).toHaveCount(0);
  await expect(page.getByRole("tab", { name: /^(Paid|Unpaid)$/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /record payment|receipt/i })).toHaveCount(0);
});
