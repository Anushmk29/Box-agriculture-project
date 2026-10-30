import { expect, test, type Page, type BrowserContext } from "@playwright/test";

async function setup(page: Page, context: BrowserContext) {
  const payload = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  await context.addCookies([{ name: "access_token", value: `qa.${payload}.qa`, domain: "127.0.0.1", path: "/" }]);
  await page.route("**/backend/api/v3/**", async route => {
    const url = new URL(route.request().url()); const path = url.pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { full_name: "Register Owner", role: "OWNER_ADMIN", permissions: ["validate_orders", "confirm_procurement", "floor_packing", "manage_billing", "manage_users", "edit_buffers", "merge_products"], must_change_password: false } });
    if (path.endsWith("/operations/summary")) return route.fulfill({ json: {} });
    if (path.endsWith("/procurement/batches/chosen")) return route.fulfill({ json: { batch_id: "chosen", batch_reference: "PUR-CHOSEN", status: "HANDOFF_01_SENT", total_skus: 0, items: [], total_ordered_weight_kg: 0, total_buffer_weight_kg: 0, total_required_weight_kg: 0 } });
    if (path.endsWith("/invoicing/invoice-1")) return route.fulfill({ json: { invoice_id: "invoice-1", invoice_number: "INV-1", client_order_id: "order-1", invoice_type: "ON_BEHALF_SUB_CUSTOMER", main_customer_seller: "LY", sub_customer_buyer: "Buyer", subtotal_eur: 10, total_amount_eur: 10, currency: "THB", amount_paid_eur: 0, status: "INVOICE_DRAFT", internal_notes: null, source_order_count: 1, items: [] } });
    const endpoints = ["/procurement/batches", "/orders/files", "/packing/orders", "/invoicing/orders", "/invoicing/records", "/procurement/source-files", "/admin/users/register"];
    if (endpoints.some(endpoint => path.endsWith(endpoint))) {
      const offset = Number(url.searchParams.get("offset")) || 0; const limit = Number(url.searchParams.get("limit")) || 25;
      const q = url.searchParams.get("q") || ""; const total = q === "Nothing" ? 0 : q ? 1 : 52;
      const items = Array.from({ length: Math.max(0, Math.min(limit, total - offset)) }, (_, index) => {
        const number = index + offset + 1;
        return { batch_id: number === 1 ? "chosen" : `batch-${number}`, display_name: q || `Client ${number} · 06 Oct 2026 · BKK`, batch_reference: `PUR-${number}`, customers: ["LY"], files: ["order.xlsx"], shipment_date: "2026-10-06", route_code: "BKK", total_skus: 10, outstanding_items: 2,
          raw_file_id: `file-${number}`, file_name: `order-${number}.xlsx`, customer: "LY", route: "BKK", customer_count: 3, unresolved_rows: 0,
          order_id: `order-${number}`, order_ref_id: `ORD-${number}`, customer_code: `LY-${number}`, customer_name: "Client LY", boxes: 5, outstanding: 2, can_invoice: true,
          invoice_id: `invoice-${number}`, invoice_number: `INV-${number}`, currency: "THB", amount: 10, paid: 0,
          status: path.endsWith("/invoicing/records") ? "INVOICE_DRAFT" : path.endsWith("/invoicing/orders") ? "HANDOFF_02_RETURNED" : path.endsWith("/packing/orders") ? "FLOOR_PACKING" : "PURCHASING_CONSOLIDATED" };
      });
      return route.fulfill({ json: { items, total, offset, limit, has_more: offset + items.length < total } });
    }
    return route.fulfill({ json: [] });
  });
}

test("batch register preserves filters and page through opening, Back and reload", async ({ page, context }) => {
  await setup(page, context); await page.goto("/procurement/batches?view=all&limit=25&offset=25");
  await expect(page.getByText("26–50 of 52 records")).toBeVisible();
  await page.screenshot({ path: "../outputs/register-desktop.png", fullPage: true });
  await page.getByLabel("Search records").fill("Sunny");
  await expect(page.getByText("1–1 of 1 records")).toBeVisible();
  expect(new URL(page.url()).searchParams.has("offset")).toBe(false);
  await page.getByRole("link", { name: "Confirm purchases Sunny" }).click();
  await expect(page.getByLabel("Batch Reference:", { exact: true })).toHaveValue("PUR-CHOSEN");
  await page.getByRole("link", { name: "Back to register" }).click();
  await expect(page.getByLabel("Search records")).toHaveValue("Sunny");
  await page.reload(); await expect(page.getByLabel("Search records")).toHaveValue("Sunny");
  await page.getByRole("link", { name: "Confirm purchases Sunny" }).click();
  await expect(page).toHaveURL(/\/procurement\?batchId=chosen/);
  await expect(page.getByLabel("Batch Reference:", { exact: true })).toHaveValue("PUR-CHOSEN");
  await page.goBack();
  await expect(page.getByLabel("Search records")).toHaveValue("Sunny");
});

for (const [path, heading] of [["/orders/files", "Order files"], ["/packing/orders", "Packing orders"], ["/billing", "Orders to invoice"]]) {
  test(`${heading} uses bounded pages, filters, sorting and empty states`, async ({ page, context }) => {
    await setup(page, context); await page.goto(path);
    await expect(page.getByRole("heading", { name: heading, exact: true })).toBeVisible();
    // Billing uses its own single tab row ("Ready to invoice" already means pending).
    if (path !== "/billing") await expect(page.getByRole("button", { name: "Pending work", exact: true })).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByText("1–25 of 52 records")).toBeVisible();
    await page.getByRole("button", { name: "Next", exact: true }).click(); await expect(page.getByText("26–50 of 52 records")).toBeVisible();
    await page.getByLabel("Rows per page").selectOption("50"); await expect(page.getByText("1–50 of 52 records")).toBeVisible();
    if (path !== "/billing") {
      await page.getByRole("button", { name: "Completed", exact: true }).click();
      await expect(page.getByRole("button", { name: "Completed", exact: true })).toHaveAttribute("aria-pressed", "true");
    }
    await page.getByLabel("Search records").fill("Nothing"); await expect(page.getByText(/No matching records/)).toBeVisible();
    await page.getByRole("button", { name: "Clear filters", exact: true }).click(); await expect(page.getByText("1–50 of 52 records")).toBeVisible();
  });
}

test("bulk file selection persists across pages and resets when filtering", async ({ page, context }) => {
  await setup(page, context); await page.goto("/procurement/combine");
  await expect(page.getByText("1–25 of 52 records")).toBeVisible();
  await page.getByRole("checkbox", { name: "Select order-1.xlsx", exact: true }).check();
  await page.getByRole("button", { name: "Next", exact: true }).click(); await expect(page.getByText("26–50 of 52 records")).toBeVisible();
  await page.getByRole("checkbox", { name: "Select order-26.xlsx", exact: true }).check();
  await expect(page.getByText("2 selected", { exact: true })).toBeVisible();
  await page.getByRole("checkbox", { name: "Select this page" }).check(); await expect(page.getByText("26 selected", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "More filters" }).click(); await page.getByLabel("Customer", { exact: true }).fill("LY"); await expect(page.getByText("0 selected", { exact: true })).toBeVisible();
  await page.getByLabel("Customer", { exact: true }).fill(""); await expect(page.getByText("0 selected", { exact: true })).toBeVisible();
});

test("invoice register loads details only after opening a row", async ({ page, context }) => {
  await setup(page, context); const detailRequests: string[] = [];
  page.on("request", request => { if (request.url().includes("/invoicing/invoice-")) detailRequests.push(request.url()); });
  await page.goto("/billing?tab=invoices"); await expect(page.getByText("1–25 of 52 records")).toBeVisible();
  expect(detailRequests).toHaveLength(0);
  await page.getByRole("link", { name: "Review invoice INV-1", exact: true }).click();
  await expect(page.getByRole("heading", { name: /INV-1/ })).toBeVisible(); expect(detailRequests).toHaveLength(1);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByLabel("Internal notes").fill("Unsaved invoice note");
  await page.getByRole("link", { name: "Back to register" }).click();
  await page.getByRole("dialog", { name: "Leave this record?" }).getByRole("button", { name: "Cancel" }).click();
  await expect(page).toHaveURL(/invoiceId=invoice-1/);
  await page.getByRole("link", { name: "Back to register" }).click();
  await page.getByRole("dialog", { name: "Leave this record?" }).getByRole("button", { name: "Leave and discard" }).click(); await expect(page.getByText("1–25 of 52 records")).toBeVisible();
});

test("completed intake shows source details and linked orders without validation or split actions", async ({ page, context }) => {
  await setup(page, context); let validations = 0;
  page.on("request", request => { if (request.url().includes("/validate")) validations++; });
  await page.route("**/backend/api/v3/master-data/customer-register?**", route => route.fulfill({ json: { items: [], total: 0, offset: 0, limit: 25, has_more: false } }));
  await page.route("**/backend/api/v3/orders/files/file-closed", route => route.fulfill({ json: { raw_file_id: "file-closed", file_name: "Historical.xlsx", status: "HANDOFF_01_SENT", total_sub_customers_detected: 1, sub_customer_codes: ["LY"], sheets_detected: [], verification_notice: "Historical acknowledgment" } }));
  await page.route("**/backend/api/v3/orders/files/file-closed/orders?**", route => route.fulfill({ json: { items: [{ order_id: "historic-order", customer_code: "LY", customer_name: "Historic Client", order_ref_id: "ORD-HISTORIC", status: "HANDOFF_01_SENT" }], total: 1, offset: 0, limit: 25, has_more: false } }));
  await page.goto("/orders?fileId=file-closed");
  await expect(page.getByRole("heading", { name: "Original file details", exact: true })).toBeVisible();
  await expect(page.getByText("ORD-HISTORIC", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Confirm orders and create purchase batch" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "View acknowledgment", exact: true })).toHaveCount(0);
  await page.locator(".order-file-reference summary").click();
  await expect(page.getByRole("button", { name: "Download original file", exact: true })).toBeVisible();
  expect(validations).toBe(0);
});

test("register recovers after records remove the last page and offers retry", async ({ page, context }) => {
  await setup(page, context);
  let fail = true;
  await page.route("**/backend/api/v3/orders/files?**", async route => {
    if (fail) { fail = false; return route.fulfill({ status: 500, json: { detail: "Temporary register error" } }); }
    return route.fulfill({ json: { items: [], total: 0, offset: Number(new URL(route.request().url()).searchParams.get("offset")), limit: 25, has_more: false } });
  });
  await page.goto("/orders/files?offset=50"); await expect(page.locator(".register-error")).toContainText("Temporary register error");
  await page.getByRole("button", { name: "Retry", exact: true }).click(); await expect(page.getByText(/Nothing waiting for review/)).toBeVisible();
  expect(new URL(page.url()).searchParams.get("offset")).toBe("0");
});

test("narrow-screen packing records keep keyboard access without sideways scrolling", async ({ page, context }) => {
  await setup(page, context); await page.setViewportSize({ width: 390, height: 844 }); await page.goto("/packing/orders");
  await expect(page.getByText("1–25 of 52 records")).toBeVisible();
  const region = page.getByRole("region", { name: "Packing orders table" });
  await page.screenshot({ path: "../outputs/register-mobile.png", fullPage: true });
  expect(await region.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await region.focus(); await expect(region).toBeFocused();
  await page.getByRole("link", { name: "Resume packing LY-1", exact: true }).focus(); await expect(page.getByRole("link", { name: "Resume packing LY-1", exact: true })).toBeFocused();
});
