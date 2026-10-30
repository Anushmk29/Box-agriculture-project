import { expect, test } from "@playwright/test";

test("missing selling prices navigate to resolution and return to invoice generation", async ({ page, context }) => {
  const token = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  await context.addCookies([{ name: "access_token", value: `qa.${token}.qa`, domain: "127.0.0.1", path: "/" }]);
  let saved = false;
  let generated = false;
  await page.route("**/backend/api/v3/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { full_name: "Accounts", role: "OWNER_ADMIN", permissions: ["draft_invoice", "manage_billing"], must_change_password: false } });
    if (path.endsWith("/order-record/order-a")) return route.fulfill({ json: { order_id: "order-a", order_ref_id: "ORD-A", customer_code: "LY", customer_name: "LY Customer", order_status: "HANDOFF_02_RETURNED", invoice_id: null } });
    if (path.endsWith("/generate-on-behalf")) {
      if (!saved) return route.fulfill({ status: 409, json: { detail: { code: "MISSING_SELLING_PRICES", order_id: "order-a", message: "Missing selling prices" } } });
      generated = true;
      return route.fulfill({ json: { invoice_id: "invoice-a", client_order_id: "order-a", invoice_number: "INV-A", invoice_type: "ON_BEHALF_SUB_CUSTOMER", currency: "THB", status: "INVOICE_DRAFT", total_amount_eur: 25.5, amount_paid_eur: 0, items: [] } });
    }
    if (path.endsWith("/invoicing/invoice-a")) return route.fulfill({ json: { invoice_id: "invoice-a", client_order_id: "order-a", invoice_number: "INV-A", invoice_type: "ON_BEHALF_SUB_CUSTOMER", currency: "THB", status: "INVOICE_DRAFT", total_amount_eur: 25.5, amount_paid_eur: 0, items: [] } });
    if (path.endsWith("/price-check")) {
      expect(route.request().postDataJSON()).toEqual({ order_ids: ["order-a"] });
      return route.fulfill({ json: { issues: [{ order_id: "order-a", order_ref: "ORD-A", customer_name: "LY Customer", shipment_date: "2026-10-06", items: [{ product_id: "leek", product_code: "11-039", product_name: "Chinese leek", pack_size: "150Gr." }] }] } });
    }
    if (path.endsWith("/resolve-prices")) {
      expect(route.request().postDataJSON()).toEqual({ items: [{ order_id: "order-a", product_id: "leek", pack_size: "150Gr.", selling_price: 12.75 }] });
      saved = true;
      return route.fulfill({ json: { saved: 1 } });
    }
    return route.fulfill({ json: [] });
  });
  await page.goto("/billing?orderId=order-a");
  await page.getByRole("button", { name: "Generate from final pack" }).click();
  await expect(page).toHaveURL(/\/billing\/resolve-prices\?/);
  await expect(page.getByRole("heading", { name: "Complete selling prices" })).toBeVisible();
  await expect(page.getByText(/Prices do not expire automatically/)).toBeVisible();
  await expect(page.getByText("Effective shipment date: 2026-10-06")).toBeVisible();
  await page.getByRole("spinbutton", { name: "Selling price for 11-039 150Gr. LY Customer" }).fill("12.75");
  await page.getByRole("button", { name: "Save prices and return to billing" }).click();
  await expect(page).toHaveURL(/pricesResolved=1/);
  await expect(page.getByText("Selling prices saved. Generate the invoice from the completed final pack.")).toBeVisible();
  await page.getByRole("button", { name: "Generate from final pack" }).click();
  await expect(page.getByRole("heading", { name: "INV-A", exact: true })).toBeVisible();
  expect(generated).toBe(true);
});

test("resolution page preserves all orders for a master invoice", async ({ page, context }) => {
  const token = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  await context.addCookies([{ name: "access_token", value: `qa.${token}.qa`, domain: "127.0.0.1", path: "/" }]);
  await page.route("**/backend/api/v3/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { full_name: "Accounts", role: "OWNER_ADMIN", permissions: ["draft_invoice", "manage_billing"] } });
    if (path.endsWith("/price-check")) {
      expect(route.request().postDataJSON()).toEqual({ order_ids: ["a", "b"] });
      return route.fulfill({ json: { issues: [] } });
    }
    return route.fulfill({ json: [] });
  });
  await page.goto("/billing/resolve-prices?mode=master&orderId=a&orderId=b");
  await expect(page.getByRole("link", { name: "Continue to billing" })).toHaveAttribute("href", "/billing?mode=master&orderId=a&orderId=b");
});
