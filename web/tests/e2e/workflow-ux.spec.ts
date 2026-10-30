import { expect, test, type Page } from "@playwright/test";
import { safeDestination } from "../../lib/workflow";

async function setup(page: Page, role = "GENERAL") {
  const permissions = ["validate_orders", "confirm_procurement", "floor_packing", ...(role !== "GENERAL" ? ["manage_billing"] : []), ...(role === "OWNER_ADMIN" ? ["manage_users"] : [])];
  const token = Buffer.from(JSON.stringify({ exp: Date.now() / 1000 + 3600 })).toString("base64url");
  await page.context().addCookies([{ name: "access_token", value: `qa.${token}.qa`, domain: "127.0.0.1", path: "/" }]);
  const requests: string[] = [];
  await page.route("**/backend/api/v3/**", async route => {
    const url = new URL(route.request().url()); const path = url.pathname;
    requests.push(path + url.search);
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { user_id: "ux-user", request_replay_supported: true, full_name: "UX Staff", role, permissions, must_change_password: false } });
    if (path.endsWith("/operations/summary")) return route.fulfill({ json: { awaiting_intake: 1, awaiting_procurement: 1, awaiting_packing: 1, boxes_missing_weights: 2, open_balances_by_currency: { THB: 2500 } } });
    if (path.endsWith("/packing/scan/QR-1")) return route.fulfill({ json: { order_id: "order-1", qr_code_uid: "QR-1", box_number: 1, order_ref_id: "ORD-1", order_status: "FLOOR_PACKING", can_edit: true, customer_code: "SUNNY", route_code: "CT", shipment_date: "2026-10-15", is_locked: false, items: [], change_history: [] } });
    if (path.endsWith("/packing/scan/QR-1/history")) return route.fulfill({ json: { items: [], total: 0, has_more: false } });
    const status = path.endsWith("/orders/files") ? "PARSED" : path.endsWith("/procurement/batches") ? "PURCHASING_CONSOLIDATED" : path.endsWith("/packing/orders") ? "FLOOR_PACKING" : "INVOICE_DRAFT";
    return route.fulfill({ json: { items: [{ raw_file_id: "raw-1", file_name: "Sunny.xlsx", customer: "Sunny", customer_name: "Sunny", customer_code: "SUNNY", batch_id: "batch-1", batch_reference: "PUR-1", display_name: "Sunny batch", customers: ["Sunny"], order_id: "order-1", order_ref_id: "ORD-1", shipment_date: "2026-10-15", route: "CT", status, boxes: 3, outstanding: 2, unresolved_rows: 1, invoice_id: "invoice-1", invoice_number: "INV-1", amount: 2500, paid: 0, currency: "THB" }], total: 1, offset: 0, limit: Number(url.searchParams.get("limit")) || 25, has_more: false } });
  });
  return requests;
}

for (const width of [1440, 390]) {
  test(`grouped navigation separates work, catalogue and administration at ${width}px`, async ({ page }) => {
    await setup(page, "OWNER_ADMIN");
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/");
    if (width <= 860) await page.getByRole("button", { name: "Menu", exact: true }).click();
    const nav = page.getByRole("navigation", { name: "Primary navigation" });
    for (const label of ["Overview", "Operations", "Catalogue", "Administration"]) await expect(nav.getByRole("group", { name: label, exact: true })).toBeVisible();
    const work = nav.getByRole("group", { name: "Operations", exact: true });
    expect(await work.getByRole("link").evaluateAll(links => links.map(link => link.getAttribute("href")))).toEqual(["/orders/files", "/procurement/batches", "/packing/orders", "/scan", "/billing"]);
    const catalogue = nav.getByRole("group", { name: "Catalogue", exact: true });
    await expect(catalogue.getByRole("link")).toHaveCount(1);
    await expect(catalogue.getByRole("link")).toHaveAttribute("href", "/packing/setup");
    await expect(nav.getByRole("group", { name: "Administration", exact: true }).getByRole("link")).toHaveAttribute("href", "/users");
    await page.screenshot({ path: `../outputs/modern-dashboard/navigation-${width}.png`, fullPage: true, animations: "disabled" });
    await catalogue.getByRole("link").click();
    await expect(page).toHaveURL(/\/packing\/setup$/);
    if (width <= 860) await page.getByRole("button", { name: "Menu", exact: true }).click();
    await expect(nav.getByRole("link", { name: "Products & boxes", exact: true })).toHaveAttribute("aria-current", "page");
    await expect(nav.getByRole("link", { name: "Packing", exact: false }).filter({ hasText: /^Packing/ })).not.toHaveAttribute("aria-current", "page");
  });
}

test("navigation groups omit inaccessible pages and empty administration sections", async ({ page }) => {
  await setup(page);
  await page.goto("/");
  const nav = page.getByRole("navigation", { name: "Primary navigation" });
  await expect(nav.getByRole("group", { name: "Administration", exact: true })).toHaveCount(0);
  await expect(nav.getByRole("link", { name: /Billing|Team & access/ })).toHaveCount(0);
  await expect(nav.getByRole("group", { name: "Catalogue", exact: true })).toBeVisible();
});

test("sidebar groups remain reachable on a short screen with account controls visible", async ({ page }) => {
  await setup(page, "OWNER_ADMIN");
  await page.setViewportSize({ width: 1366, height: 600 });
  await page.goto("/");
  const sidebar = page.getByRole("complementary", { name: "Primary sidebar" });
  await sidebar.getByRole("link", { name: "Team & access", exact: true }).focus();
  await expect(sidebar.getByRole("link", { name: "Team & access", exact: true })).toBeInViewport({ ratio: 1 });
  await expect(sidebar.getByRole("button", { name: "Sign out everywhere", exact: true })).toBeInViewport({ ratio: 1 });
});

test("staff Today has actionable bounded queues without accounting data", async ({ page }) => {
  const requests = await setup(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Today", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Resume packing", exact: true })).toHaveAttribute("href", /orderId=order-1/);
  await expect(page.getByRole("region", { name: "Unpaid invoice balances" })).toHaveCount(0);
  expect(requests.some(path => path.includes("/invoicing/"))).toBe(false);
  expect(new Set(requests.filter(path => path.includes("limit=5"))).size).toBe(3);
  await page.goto("/billing");
  await expect(page.getByRole("heading", { name: "Access unavailable" })).toBeVisible();
  await expect(page).toHaveURL(/\/billing$/);
  await expect(page.getByRole("link", { name: "Return to Today" })).toHaveAttribute("href", "/");
});

for (const width of [1440, 768, 390]) {
  test(`owner overview and labeled navigation work at ${width}px`, async ({ page }) => {
    await setup(page, "OWNER_ADMIN"); await page.setViewportSize({ width, height: 900 }); await page.goto("/");
    await expect(page.getByRole("region", { name: "Shipment overview" })).toContainText("Orders awaiting packing");
    await expect(page.getByRole("region", { name: "Unpaid invoice balances" })).toHaveCount(0);
    if (width <= 860) await page.getByRole("button", { name: "Menu", exact: true }).click();
    await expect(page.getByRole("navigation", { name: "Primary navigation" }).getByRole("link", { name: "Packing", exact: false })).toBeVisible();
    if (width <= 860) await page.keyboard.press("Escape");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `../outputs/ux-today-${width}.png`, fullPage: true });
  });
}

test("accounting starts with billing queues and tabs request correct states", async ({ page }) => {
  const requests = await setup(page, "GENERAL_ACCOUNTING"); await page.goto("/");
  await expect(page.locator(".today-queue h3").first()).toContainText("Ready to invoice");
  await page.goto("/billing"); await page.getByRole("tab", { name: "Drafts", exact: true }).click();
  await expect(page).toHaveURL(/invoice_group=drafts/);
  await expect.poll(() => requests.some(path => path.includes("/invoicing/records") && path.includes("invoice_group=drafts") && path.includes("view=all"))).toBe(true);
  await page.getByRole("tab", { name: "Final invoices", exact: true }).click();
  await expect.poll(() => requests.some(path => path.includes("/invoicing/records") && path.includes("invoice_group=final"))).toBe(true);
});

test("batch scope reaches the API and survives opening a packing order", async ({ page }) => {
  const requests = await setup(page); await page.goto("/packing/orders?batch_id=batch-1&view=all");
  await expect(page.locator(".register-scope")).toContainText("batch-1");
  await expect(page.getByRole("link", { name: "Resume packing SUNNY", exact: true })).toHaveAttribute("href", /returnTo=.*batch_id/);
  expect(requests.some(path => path.includes("/packing/orders?") && path.includes("batch_id=batch-1"))).toBe(true);
});

test("malformed sticker input recovers and valid scan targets exact box", async ({ page }) => {
  await setup(page); await page.goto("/scan");
  await page.getByLabel("Sticker code or QR link").fill("%broken"); await page.getByRole("button", { name: "Open sticker" }).click();
  await expect(page.locator(".field-error")).toContainText("Copy the original printed code");
  await page.getByLabel("Sticker code or QR link").fill("QR-1"); await page.getByRole("button", { name: "Open sticker" }).click();
  await expect(page.getByRole("link", { name: "Open this box in packing" })).toHaveAttribute("href", "/packing?orderId=order-1&box=QR-1");
});

test("return destination rejects external redirects and retains record filters", () => {
  for (const value of ["//evil.example", "/\\evil.example", "https://evil.example", "/login?next=/billing", "/unknown", "/\n/evil"]) expect(safeDestination(value)).toBe("/");
  expect(safeDestination("/packing?orderId=one&box=QR-1")).toBe("/packing?orderId=one&box=QR-1");
});

test("phone menu reveals permitted workspaces and returns focus when dismissed", async ({ page }) => {
  await setup(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  const menu = page.getByRole("button", { name: "Menu", exact: true });
  await menu.click();
  const dialog = page.getByRole("dialog", { name: "Workspaces" });
  await expect(dialog.getByRole("link", { name: /Purchasing/ })).toBeVisible();
  await expect(dialog.getByRole("link", { name: /Billing|Team & access/ })).toHaveCount(0);
    await expect(dialog.getByRole("button", { name: "Sign out everywhere", exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(menu).toBeFocused();
  await menu.click();
  await dialog.getByRole("link", { name: /Packing/ }).click();
  await expect(page).toHaveURL(/\/packing\/orders/);
  await expect(dialog).toHaveCount(0);
});

test("Today labels retained queue data when a refresh fails", async ({ page }) => {
  await setup(page); await page.goto("/");
  await expect(page.getByText(/Last successful refresh:/)).toBeVisible();
  await expect(page.getByRole("link", { name: "Resume packing", exact: true })).toBeVisible();
  await page.route("**/backend/api/v3/packing/orders?*", route => route.fulfill({ status: 503, json: { detail: "Offline" } }));
  await page.getByRole("button", { name: "Refresh work", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Previous results are shown and may be outdated." })).toBeVisible();
  await expect(page.getByRole("link", { name: "Resume packing", exact: true })).toBeVisible();
});

test("invoice-only workflow never offers payment submission", async ({ page }) => {
  await setup(page, "GENERAL_ACCOUNTING");
  await page.route("**/backend/api/v3/invoicing/final", route => route.fulfill({ json: {
    invoice_id: "final", revision: 1, client_order_id: "order", invoice_number: "INV-FINAL",
    invoice_type: "ON_BEHALF_SUB_CUSTOMER", main_customer_seller: "Seller", sub_customer_buyer: "Buyer", currency: "THB",
    subtotal_eur: 100, total_amount_eur: 100, amount_paid_eur: 0, status: "INVOICE_SENT", items: [], source_order_count: 1, internal_notes: null,
  } }));
  await page.goto("/billing?invoiceId=final");
  await expect(page.getByText(/Invoice creation is complete/)).toBeVisible();
  await expect(page.getByLabel("Cleared amount")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /record payment|receipt/i })).toHaveCount(0);
  await page.getByRole("link", { name: "Review final invoice", exact: true }).click();
  await expect(page.locator("#invoice-details")).toBeFocused();
});

