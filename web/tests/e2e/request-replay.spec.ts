import { expect, test, type Page } from "@playwright/test";

async function setup(page: Page) {
  const payload = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  await page.context().addCookies([{ name: "access_token", value: `qa.${payload}.qa`, domain: "127.0.0.1", path: "/" }]);
  await page.route("**/backend/api/v3/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { user_id: "retry-staff", full_name: "Retry Staff",
      role: "OWNER_ADMIN", must_change_password: false, request_replay_supported: true,
      permissions: ["manage_billing", "validate_orders"] } });
    if (path.endsWith("/orders/files/retry-file")) return route.fulfill({ json: { raw_file_id: "retry-file", file_name: "Review.xlsx",
      total_sub_customers_detected: 1, sub_customer_codes: ["CT-A"], sheets_detected: [], group_summary: { TETE: 1 },
      status: "PARSED", verification_notice: "Review" } });
    if (path.endsWith("/validate")) return route.fulfill({ json: { can_split: true, quantity_issue_count: 0, unresolved: [] } });
    if (path.endsWith("/customer-register")) return route.fulfill({ json: { items: [], total: 0, has_more: false } });
    return route.fulfill({ json: path.endsWith("/operations/summary") ? {} : [] });
  });
}

test("invoice-only workflow never offers payment submission", async ({ page }) => {
  await setup(page);
  await page.route("**/backend/api/v3/invoicing/final", route => route.fulfill({ json: {
    invoice_id: "final", revision: 1, client_order_id: "order", invoice_number: "INV-FINAL",
    invoice_type: "ON_BEHALF_SUB_CUSTOMER", main_customer_seller: "Seller", sub_customer_buyer: "Buyer", currency: "THB",
    subtotal_eur: 100, total_amount_eur: 100, amount_paid_eur: 0, status: "INVOICE_SENT", items: [], source_order_count: 1, internal_notes: null,
  } }));
  await page.goto("/billing?invoiceId=final");
  await expect(page.getByText(/Invoice creation is complete/)).toBeVisible();
  await expect(page.getByLabel("Cleared amount")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /record payment|receipt/i })).toHaveCount(0);
});

test("a pending order addition survives refresh and uses a new key only after confirmation", async ({ page }) => {
  await setup(page);
  const keys: string[] = [];
  const bodies: string[] = [];
  await page.route("**/backend/api/v3/orders/retry-file/add-adhoc-order", route => {
    keys.push(route.request().headers()["idempotency-key"]); bodies.push(route.request().postData() ?? "");
    if (keys.length === 1) return route.abort("connectionreset");
    return route.fulfill({ json: { message: "Addition confirmed", customer_code: "CT-A" } });
  });
  await page.goto("/orders?fileId=retry-file");
  await page.locator(".order-disclosure summary").filter({ hasText: "Add More Products" }).click();
  await page.getByRole("button", { name: "Add More Products", exact: true }).click();
  await page.getByLabel("Subcustomer", { exact: true }).fill("CT-A");
  await page.getByLabel("Produce Item (Code or Name)").fill("T001");
  await page.getByLabel("Quantity (Packs)").fill("2");
  await page.getByRole("button", { name: "+ Add order", exact: true }).click();
  await expect(page.getByRole("button", { name: "Retry pending addition", exact: true })).toBeVisible();
  await page.reload();
  await expect(page.locator(".order-disclosure").filter({ has: page.locator("summary").filter({ hasText: "Add More Products" }) })).toHaveAttribute("open", "");
  await expect(page.getByRole("img", { name: "Needs attention", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Add More Products", exact: true }).click();
  await page.getByRole("button", { name: "Retry pending addition", exact: true }).click();
  await expect(page.getByRole("button", { name: "+ Add order", exact: true })).toBeVisible();
  expect(keys).toHaveLength(2); expect(keys[1]).toBe(keys[0]); expect(bodies[1]).toBe(bodies[0]);
  await page.getByLabel("Produce Item (Code or Name)").fill("T001");
  await page.getByRole("button", { name: "+ Add order", exact: true }).click();
  await expect.poll(() => keys.length).toBe(3); expect(keys[2]).not.toBe(keys[0]);
});
