import { expect, test, type Page } from "@playwright/test";

const permissions = ["validate_orders", "resolve_orders", "confirm_procurement", "floor_packing", "design_packing", "manage_billing", "manage_users"];
const product = { id: "herb", t_code: "T001", name_en: "Holy basil", name_vi: "Basil", name_th: "", packing_revision: 1,
  box_material: "FOAM", temperature: "COLD", control_status: "EL", produce_type: "HERB", weight_class: "LIGHT", firmness: "HARD",
  volume_l_per_kg: 6, volume_estimated: false, ready: true, problems: [], limits: [], source_choices: [], grouping_row: null, identity_choices: [], limit_issues: [] };

async function setup(page: Page, rows = 1) {
  const token = Buffer.from(JSON.stringify({ exp: Date.now() / 1000 + 3600 })).toString("base64url");
  await page.context().addCookies([{ name: "access_token", value: `qa.${token}.qa`, domain: "127.0.0.1", path: "/" }]);
  await page.route("**/backend/api/v3/**", route => {
    const url = new URL(route.request().url()); const path = url.pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { user_id: "compact-staff", full_name: "Operations staff", role: "OWNER_ADMIN", must_change_password: false, permissions } });
    if (path.endsWith("/operations/summary")) return route.fulfill({ json: { awaiting_intake: 1, awaiting_procurement: 1, awaiting_packing: 1, awaiting_billing: 1 } });
    if (path.endsWith("/master-data/products") || path.endsWith("/master-data/boxes") || path.endsWith("/access-levels") || path.endsWith("/packing-setup/boxes")) return route.fulfill({ json: [] });
    if (path.endsWith("/customer-register")) return route.fulfill({ json: { items: [{ id: "main", client_code: "SUNNY", company_name: "Sunny Trading", is_main_customer: true }], total: 1, offset: 0, limit: 25, has_more: false } });
    if (path.endsWith("/packing-setup/table")) return route.fulfill({ json: { items: Array.from({ length: rows }, (_, i) => ({ ...product, id: `product-${i}`, t_code: `T${String(i + 1).padStart(3, "0")}` })), total: 818, has_more: true, summary: { total: 818, ready: 0, cold: 332, ambient: 17, temperature_unknown: 469, grouping_matched: 349, strict: false } } });
    if (path.endsWith("/orders/files/intake")) return route.fulfill({ json: { raw_file_id: "intake", file_name: "Customer order.xlsx", status: "PARSED", total_sub_customers_detected: 1, sub_customer_codes: ["SUNNY"], sheets_detected: [], group_summary: { CT: 1 }, verification_notice: "Check the source before confirming." } });
    if (path.endsWith("/intake/validate")) return route.fulfill({ json: { can_split: true, unresolved: [], quantity_issue_count: 0 } });
    if (path.endsWith("/procurement/batches/batch-1")) return route.fulfill({ json: { batch_id: "batch-1", batch_reference: "PUR-20261029-SUNNY", status: "PURCHASING_CONSOLIDATED", total_skus: 1, total_ordered_weight_kg: 2, total_buffer_weight_kg: 0, total_required_weight_kg: 2,
      source_files: [{ file_id: "source-1", file_name: "Sunny.xlsx", customer: "Sunny Trading", shipment_date: "2026-10-29", route: "CT" }], items: [{ item_id: "item-1", product_id: "herb", product_code: "T001", product_name: "Holy basil", produce_type: "HERB", pack_size: "1kg", ordered_pack_count: 2, ordered_weight_kg: 2, buffer_percentage: 0, buffer_pack_count: 0, buffer_weight_kg: 0, total_purchase_target_packs: 2, total_purchase_target_weight_kg: 2, required_packs: 2, required_weight_kg: 2, confirmed_purchased_pack_count: 2, confirmed_purchased_weight_kg: 2 }] } });
    if (path.endsWith("/packing-list")) return route.fulfill({ json: { client_order_id: "order-1", order_ref_id: "ORD-20261029-SUNNY", status: "FLOOR_PACKING", packing_revision: 1, sub_customer_code: "SUNNY", route_code: "CT", shipment_date: "2026-10-29", total_boxes_count: 4,
      boxes: Array.from({ length: 4 }, (_, i) => ({ box_id: `box-${i}`, box_number: i + 1, box_type_name: "Small foam", box_catalogue_id: "small", box_material: "FOAM", safe_net_capacity_kg: 5, is_overridden: false, actual_net_weight_kg: 2, actual_gross_weight_kg: 2.5, is_locked: false, qr_code_uid: `QA-${i}`, packing_mode: "EL", sticker_label: `SUNNY ${i + 1}/4`, items: [{ item_id: `item-${i}`, product_id: "herb", product_code: "T001", produce_name: "Holy basil", pack_size: "1kg", packed_count: 2, net_weight_kg: 2, adjustment_type: "AS_ORDERED" }] })) } });
    if (path.endsWith("/invoicing/invoice-1")) return route.fulfill({ json: { invoice_id: "invoice-1", revision: 1, client_order_id: "order-1", invoice_number: "INV-20261029-SUNNY", invoice_type: "ON_BEHALF_SUB_CUSTOMER", main_customer_seller: "Sunny Trading", sub_customer_buyer: "Sunny Shop", currency: "THB", status: "INVOICE_DRAFT", total_amount_eur: 5, amount_paid_eur: 0, subtotal_eur: 5, source_order_count: 1, internal_notes: null,
      items: [{ line_item_id: "line-1", source_order_id: "order-1", source_order_ref: "ORD-20261029-SUNNY", source_customer_code: "SUNNY", product_code: "T001", product_name: "Holy basil", box_number: 1, pack_size: "1kg", billed_packs: 2, billed_weight_kg: 2, unit_price_eur: 2.5, line_total_eur: 5 }] } });
    if (path.endsWith("/order-record/order-1")) return route.fulfill({ json: { order_id: "order-1", order_ref_id: "ORD-20261029-SUNNY", customer_code: "SUNNY", customer_name: "Sunny Shop", order_status: "HANDOFF_02_RETURNED", invoice_id: null } });
    if (path.endsWith("/price-check")) return route.fulfill({ json: { issues: [{ order_id: "order-1", order_ref: "ORD-20261029-SUNNY", customer_name: "Sunny Shop", shipment_date: "2026-10-29", items: [{ product_id: "herb", product_code: "T001", product_name: "Holy basil", pack_size: "1kg" }] }] } });
    if (path.endsWith("/packing/scan/QA-BOX")) return route.fulfill({ json: { order_id: "order-1", qr_code_uid: "QA-BOX", box_number: 1, order_ref_id: "ORD-20261029-SUNNY", order_status: "FLOOR_PACKING", can_edit: true, customer_code: "SUNNY", route_code: "CT", shipment_date: "2026-10-29", is_locked: false, items: [], change_history: [] } });
    if (path.endsWith("/packing/scan/QA-BOX/history")) return route.fulfill({ json: { items: [], total: 0, has_more: false } });
    const status = path.endsWith("/orders/files") ? "PARSED" : path.endsWith("/procurement/batches") ? "PURCHASING_CONSOLIDATED" : path.endsWith("/invoicing/orders") ? "HANDOFF_02_RETURNED" : path.endsWith("/packing/orders") ? "FLOOR_PACKING" : "INVOICE_DRAFT";
    const offset = Number(url.searchParams.get("offset")) || 0; const limit = Number(url.searchParams.get("limit")) || 25;
    return route.fulfill({ json: { items: Array.from({ length: Math.min(rows - offset, limit) }, (_, i) => ({
      id: `person-${i}`, full_name: "Operations Staff", email: "staff@example.invalid", role: "GENERAL", is_active: true, permissions: [],
      raw_file_id: `file-${i}`, file_name: "Order Tonio 10.07.26 FULL.xlsx", customer: "Tonio Trade Europe BV", customer_count: 68, unresolved_rows: 8,
      batch_id: `batch-${i}`, batch_reference: "PUR-20261029-SUNNY", display_name: "Sunny export shipment", customers: ["Sunny Trading"],
      order_id: `order-${i}`, order_ref_id: "ORD-20261029-SUNNY-68526a79a3395117", customer_code: "SUNNY", customer_name: "Sunny Shop", shipment_date: "2026-10-29", route: "CT", status, boxes: 4, outstanding: 4, can_invoice: true,
      invoice_id: `invoice-${i}`, invoice_number: "INV-20261029-SUNNY", amount: 5, paid: 0, currency: "THB",
    })), total: rows, offset, limit, has_more: offset + limit < rows } });
  });
}

const registers = ["/orders/files", "/orders/attention", "/procurement/batches", "/packing/orders", "/billing", "/billing?tab=invoices", "/users"];
for (const [width, height] of [[1366, 768], [1536, 864], [1920, 1080]]) {
  test(`desktop registers keep filters, records and pagination together at ${width}x${height}`, async ({ page }) => {
    await setup(page, 25);
    await page.setViewportSize({ width, height });
    for (const route of registers) {
      await page.goto(route);
      await page.waitForLoadState("networkidle");
      await expect(page.getByText("1–25 of 25 records", { exact: true })).toBeVisible();
      const pagination = page.locator(".register-pagination").last();
      await expect(pagination.getByRole("button", { name: "Refresh", exact: true })).toBeInViewport({ ratio: 1 });
      const geometry = await page.evaluate(() => ({ width: innerWidth, height: innerHeight, scrollWidth: document.documentElement.scrollWidth, scrollHeight: document.documentElement.scrollHeight,
        tableTop: document.querySelector(".register-table-scroll")!.getBoundingClientRect().top,
        footerBottom: document.querySelector(".register-pagination")!.getBoundingClientRect().bottom }));
      expect(geometry.scrollWidth, route).toBeLessThanOrEqual(width);
      expect(geometry.scrollHeight, `${route}: page should fit the viewport`).toBeLessThanOrEqual(height + 2);
      expect(geometry.tableTop, `${route}: records start in the upper half`).toBeLessThan(height / 2 + 20);
      expect(geometry.footerBottom, `${route}: pagination remains visible`).toBeLessThanOrEqual(height);
      await page.screenshot({ path: `../outputs/compact-layout/${route.slice(1).replaceAll(/[/ ?=]/g, "-")}-${width}.png`, fullPage: false, animations: "disabled" });
    }
  });
}

test("every workspace and working record stays readable at desktop and phone widths", async ({ page }) => {
  test.setTimeout(180_000);
  await setup(page);
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(`${page.url()}: ${error.message}`));
  const routes = ["/", "/orders", "/orders?fileId=intake", "/procurement?batchId=batch-1", "/procurement/master-purchase", "/packing?orderId=order-1", "/packing/setup", "/billing?orderId=order-1", "/billing?invoiceId=invoice-1", "/billing/resolve-prices?orderId=order-1&mode=individual", "/scan", "/scan/QA-BOX", "/users"];
  for (const width of [1366, 1024, 390]) {
    await page.setViewportSize({ width, height: 900 });
    for (const [i, route] of routes.entries()) {
      await page.goto(route);
      await expect(page.locator("h1").first()).toBeVisible();
      await page.waitForLoadState("networkidle");
      await expect(page.locator(".skeleton:visible")).toHaveCount(0);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${route} at ${width}px`).toBe(true);
      await page.screenshot({ path: `../outputs/compact-layout/workspace-${i}-${width}.png`, fullPage: true, animations: "disabled" });
    }
  }
  expect(errors).toEqual([]);
});

test("one-record queues fit a short laptop viewport without page scrolling", async ({ page }) => {
  await setup(page);
  await page.setViewportSize({ width: 1366, height: 700 });
  for (const route of registers) {
    await page.goto(route);
    await page.waitForLoadState("networkidle");
    await expect(page.getByText("1–1 of 1 records", { exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollHeight <= innerHeight + 2), route).toBe(true);
  }
});


test("working forms keep the next action visible on a laptop", async ({ page }) => {
  await setup(page);
  await page.setViewportSize({ width: 1366, height: 768 });
  for (const route of ["/orders", "/procurement?batchId=batch-1", "/packing?orderId=order-1", "/packing/setup"]) {
    await page.goto(route);
    await page.waitForLoadState("networkidle");
    if (route === "/orders") await expect(page.getByRole("button", { name: "Upload and check order" })).toBeInViewport({ ratio: 1 });
    if (route.startsWith("/procurement")) await expect(page.getByRole("button", { name: "Confirm purchases and send to packing" })).toBeInViewport({ ratio: 1 });
    if (route === "/packing?orderId=order-1") await expect(page.getByRole("button", { name: "Finalize Packing", exact: true })).toBeInViewport({ ratio: 1 });
    if (route === "/packing/setup") {
      await expect(page.getByRole("button", { name: "Save changes", exact: true })).toBeInViewport({ ratio: 1 });
      await expect(page.getByRole("button", { name: "Refresh", exact: true }).last()).toBeInViewport({ ratio: 1 });
      await expect(page.getByLabel("T001 Firmness", { exact: true })).toBeInViewport({ ratio: 1 });
    }
    await page.screenshot({ path: `../outputs/compact-layout/laptop-${route.slice(1).replaceAll(/[/ ?=]/g, "-")}.png`, animations: "disabled" });
  }
});


test("Today remains a dashboard with bounded previews and keyboard access", async ({ page }) => {
  await setup(page, 5);
  await page.setViewportSize({ width: 1366, height: 768 });
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  await expect(page.getByRole("heading", { name: "Shipment overview" })).toBeInViewport({ ratio: 1 });
  await expect(page.getByRole("table")).toHaveCount(0);
  const queues = page.getByRole("region", { name: "Team work queues" });
  await expect(queues.locator(".today-queue").first()).toBeInViewport();
  await expect(queues.locator(".today-queue").first().locator("li")).toHaveCount(3);
  await queues.focus();
  await page.keyboard.press("End");
  await expect(queues.locator(".today-queue").last().getByRole("link").last()).toBeInViewport({ ratio: 1 });
});


for (const [width, height] of [[1366, 768], [1536, 730], [1920, 1080]]) {
  test(`Product settings give most of the screen to the table at ${width}x${height}`, async ({ page }) => {
    await setup(page, 25);
    await page.setViewportSize({ width, height });
    for (const route of ["/packing/setup"]) {
      await page.goto(route);
      await page.waitForLoadState("networkidle");
      const table = page.getByRole("region", { name: route === "/" ? "Team work queues" : "Editable product worksheet" });
      await expect(table).toBeInViewport({ ratio: 1 });
      const bounds = (await table.boundingBox())!;
      expect(bounds.height, `${route}: the table occupies most of the viewport`).toBeGreaterThan(height * .6);
      expect(bounds.y, `${route}: short header area`).toBeLessThan(height * .34);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth && document.documentElement.scrollHeight <= innerHeight + 2)).toBe(true);
      await expect(page.getByRole("region", { name: "Next step guidance" })).toBeVisible();
      await expect(page.locator(".main-content > .workspace-guide")).toHaveCount(0);
      await expect(page.getByText("Temperature determines the box", { exact: true })).toHaveCount(0);
      await expect(page.getByText("Quick actions", { exact: true })).toHaveCount(0);
      await page.screenshot({ path: `../outputs/table-first/${route === "/" ? "today" : "products"}-${width}.png`, animations: "disabled" });
    }
  });
}


for (const width of [1366, 1536, 390]) {
  test(`Today dashboard has summary cards and separate work queues at ${width}px`, async ({ page }) => {
    await setup(page);
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await expect(page.getByRole("table")).toHaveCount(0);
    await expect(page.locator(".operations-stat")).toHaveCount(3);
    await expect(page.locator(".dashboard-queues > .today-queue")).toHaveCount(5);
    await expect(page.getByRole("region", { name: "Team work queues" }).getByRole("link", { name: "Resume packing", exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `../outputs/today-dashboard/today-${width}.png`, fullPage: true, animations: "disabled" });
  });
}
