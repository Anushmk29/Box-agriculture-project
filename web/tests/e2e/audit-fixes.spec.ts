import { test, expect, Page } from "@playwright/test";

async function session(page: Page) {
  const payload = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  await page.context().addCookies([{ name: "access_token", value: `test.${payload}.signature`, domain: "127.0.0.1", path: "/" }]);
}

test("billing preserves historical invoice currency in every displayed amount", async ({ page }) => {
  await session(page);
  const invoice = { invoice_id: "legacy", client_order_id: "order", invoice_number: "INV-LEGACY",
    invoice_type: "ON_BEHALF_SUB_CUSTOMER", currency: "EUR", main_customer_seller: "Seller",
    sub_customer_buyer: "Buyer", subtotal_eur: 10, total_amount_eur: 10, amount_paid_eur: 0,
    status: "INVOICE_SENT", source_order_count: 1, internal_notes: null,
    items: [{ line_item_id: "line", source_order_id: "order", source_order_ref: "ORD",
      source_customer_code: "BUYER", product_code: "HERB", product_name: "Herb",
      box_number: 1, pack_size: "1kg", billed_packs: 2, billed_weight_kg: 2,
      unit_price_eur: 5, line_total_eur: 10 }] };
  await page.route("**/backend/api/v3/**", async route => {
    const path = new URL(route.request().url()).pathname;
    const data = path.endsWith("/auth/me") ? { role: "OWNER_ADMIN", full_name: "Owner", permissions: ["manage_billing"] }
      : path.endsWith("/invoicing/work-queue") ? []
      : path.endsWith("/invoicing") ? [invoice]
      : path.endsWith("/invoicing/legacy") ? invoice : {};
    await route.fulfill({ json: data });
  });
  await page.goto("/billing?invoiceId=legacy");
  const modal = page.locator(".invoice-paper");
  await expect(modal).toContainText("€5.00");
  await expect(modal).toContainText("€10.00");
  await expect(modal).not.toContainText("THB");
  await expect(page.getByLabel("Cleared amount")).toHaveCount(0);
});

test("dashboard omits payment balances in invoice-only scope", async ({ page }) => {
  await session(page);
  await page.route("**/backend/api/v3/**", async route => {
    await route.fulfill({ json: new URL(route.request().url()).pathname.endsWith("/auth/me")
      ? { role: "OWNER_ADMIN", full_name: "Owner", permissions: ["manage_billing"] }
      : { permissions: ["manage_billing"], active_orders: 0, closed_orders: 0,
          awaiting_intake: 0, awaiting_procurement: 0, awaiting_packing: 0, awaiting_billing: 0,
          boxes_missing_weights: 0, open_balances_by_currency: { EUR: 10, THB: 20 } } });
  });
  await page.goto("/");
  await expect(page.getByRole("region", { name: "Unpaid invoice balances" })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Unpaid invoice balances" })).toHaveCount(0);
});

test("password change returns the operator to sign in", async ({ page }) => {
  await session(page);
  await page.route("**/backend/api/v3/**", async route => {
    if (new URL(route.request().url()).pathname.endsWith("/auth/change-password")) {
      await route.fulfill({ status: 204, headers: { "set-cookie": "access_token=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax" } });
    } else {
      await route.fulfill({ json: { role: "GENERAL", full_name: "Staff", must_change_password: true, permissions: [] } });
    }
  });
  await page.goto("/change-password");
  await page.getByLabel("Temporary password").fill("temporary-password-123");
  await page.getByLabel("New password").fill("replacement-password-123");
  await page.getByRole("button", { name: "Save password" }).click();
  await expect(page).toHaveURL(/\/login\?next=/);
});
