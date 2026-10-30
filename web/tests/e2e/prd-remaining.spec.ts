import { expect, test, type Page, type Route } from "@playwright/test";

test.use({ baseURL: process.env.E2E_BASE_URL || "http://127.0.0.1:3000" });

async function session(page: Page, role: { value: string }, respond: (path: string, route: Route) => Promise<unknown>) {
  const payload = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  await page.context().addCookies([{ name: "access_token", value: `test.${payload}.test`, domain: "127.0.0.1", path: "/", httpOnly: true }]);
  await page.route("**/backend/api/v3/**", async route => {
    const path = new URL(route.request().url()).pathname.replace("/backend/api/v3", "");
    const permissions = ["validate_orders", "confirm_procurement", "design_packing", "floor_packing"];
    if (role.value === "OWNER_ADMIN") permissions.push("lock_final_pack", "edit_buffers", "merge_products", "draft_invoice", "manage_billing", "manage_users", "dispatch_handoff_01", "return_handoff_02");
    const result = path === "/auth/me" ? { role: role.value, full_name: "PRD Tester", permissions, must_change_password: false, user_id: "upload-fixture-user", upload_replay_supported: true }
      : path === "/operations/summary" ? { permissions }
      : await respond(path, route);
    const payload = path === "/master-data/customer-register" && Array.isArray(result)
      ? { items: result, total: result.length, offset: 0, limit: 25, has_more: false } : result;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(payload === undefined ? [] : payload) });
  });
}

test("order intake rejects image files without sending an upload", async ({ page }) => {
  let uploads = 0;
  await session(page, { value: "OWNER_ADMIN" }, async (path, route) => {
    if (path === "/master-data/customer-register") return { items: [{ id: "main", client_code: "MAIN", company_name: "Main customer" }], total: 1, offset: 0, limit: 25, has_more: false };
    if (route.request().method() === "POST" && path.startsWith("/orders/upload-")) uploads++;
    return [];
  });
  await page.goto("/orders");
  const input = page.locator('input[type="file"]');
  await expect(input).toHaveAttribute("accept", ".xlsx,.xls,.xlsm,.pdf");
  await page.getByRole("combobox", { name: /Billing customer/ }).selectOption("main");
  await input.setInputFiles({ name: "993L.png", mimeType: "image/png", buffer: Buffer.from("image") });
  await page.getByRole("button", { name: "Upload and check order" }).click();
  await expect(page.getByText("Unsupported file format. Choose an Excel (.xlsx, .xlsm, .xls) or PDF order file.", { exact: true })).toBeVisible();
  expect(uploads).toBe(0);
});

test("buyer combines client files and explicitly reviews a duplicate SKU", async ({ page }) => {
  let combined = false;
  let merged = false;
  const products = [{ id: "duplicate", t_code: "DUP", name_en: "White mushrooms", name_vi: "Duplicate" }, { id: "canonical", t_code: "MASTER", name_en: "White mushroom", name_vi: "Master" }];
  const batch = { batch_id: "combined", batch_reference: "PUR-COMBINED", status: "PURCHASING_CONSOLIDATED", total_skus: 0, total_ordered_weight_kg: 3, total_buffer_weight_kg: 0, total_required_weight_kg: 3, items: [] };
  await session(page, { value: "OWNER_ADMIN" }, async (path, route) => {
    if (path === "/procurement/source-files") return { items: ["Sophie", "James"].map((name, i) => ({ raw_file_id: String(i), customer: name, file_name: `${name}.xlsx`, shipment_date: "2026-10-02", route: "CT", customer_count: 1 })), total: 2, offset: 0, limit: 25, has_more: false };
    if (path === "/procurement/latest-batch") return null;
    if (path === "/master-data/products") return merged ? products.slice(1) : products;
    if (path === "/procurement/combine-and-consolidate") {
      expect(route.request().postDataJSON().raw_file_ids).toEqual(["0", "1"]); combined = true; return batch;
    }
    if (path === "/master-data/products/merge") {
      expect(route.request().postDataJSON()).toEqual({ source_id: "duplicate", target_id: "canonical" }); merged = true; return { message: "Merged" };
    }
    if (path.startsWith("/procurement/batches/")) return batch;
    return [];
  });
  await page.goto("/procurement/combine");
  await page.getByRole("checkbox", { name: "Select Sophie.xlsx", exact: true }).check();
  await page.getByRole("checkbox", { name: "Select James.xlsx", exact: true }).check();
  await expect(page.locator(".combine-review")).toContainText("source purchase batches will be replaced");
  await page.getByRole("button", { name: "Combine 2 files", exact: true }).click();
  await expect.poll(() => combined).toBe(true);
  await expect(page).toHaveURL(/batchId=combined/);
  await page.getByText("Batch preparation and catalog tools", { exact: true }).click();
  await page.getByRole("button", { name: "Merge duplicate products", exact: true }).click();
  await page.getByLabel("Duplicate to merge").selectOption("duplicate");
  await page.getByLabel("Keep this master SKU").selectOption("canonical");
  await page.getByLabel("I reviewed these SKUs and they are the same product.").check();
  await page.getByRole("button", { name: "Merge duplicate SKU", exact: true }).click();
  await expect.poll(() => merged).toBe(true);
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("floor adjustment supports immediate staff finalization", async ({ page }) => {
  const role = { value: "GENERAL" };
  let adjusted = false;
  const item = { item_id: "item", product_id: "herb", product_code: "HERB", produce_name: "Herb", pack_size: "1kg", packed_count: 2, net_weight_kg: 2, adjustment_type: "AS_ORDERED" };
  const box = { box_id: "box", box_number: 1, box_catalogue_id: "small", box_type_name: "Small", box_material: "FOAM", safe_net_capacity_kg: 5, is_overridden: false, actual_net_weight_kg: 2 as number | null, actual_gross_weight_kg: 2.5 as number | null, is_locked: false, qr_code_uid: "QR-BOX", sticker_label: "CT-BUYER / 1 of 1", items: [item] };
  const packing = { client_order_id: "order", order_ref_id: "ORD-BUYER", status: "FLOOR_PACKING", packing_approval_status: "NONE", packing_review_note: null, sub_customer_code: "BUYER", route_code: "CT", shipment_date: "2026-10-02", total_boxes_count: 1, boxes: [box] };
  await session(page, role, async (path, route) => {
    if (path === "/master-data/boxes") return [{ id: "small", size_name: "Small", box_type: "FOAM", length_cm: 40, width_cm: 30, height_cm: 20 }];
    if (path === "/master-data/products" && new URL(route.request().url()).searchParams.get("q") === "missing") return [];
    if (path === "/master-data/products") return [{ id: "herb", t_code: "HERB", name_en: "Herb", name_vi: "Herb" }];
    if (path.endsWith("/sub-packing-lists")) return { group_name: "CT", total_sub_customers: 0, total_weight_kg: 0, total_boxes_count: 0, sub_customers: [] };
    if (path.endsWith("/prepare")) { packing.status = "FLOOR_PACKING"; return { status: packing.status }; }
    if (path.endsWith("/packing-list")) return packing;
    if (path === "/packing/boxes/box/items") {
      expect(route.request().postDataJSON()).toMatchObject({ item_id: "item", product_id: "herb", quantity: 3, net_weight_kg: 3 });
      adjusted = true; item.packed_count = 3; item.net_weight_kg = 3; box.actual_net_weight_kg = null; box.actual_gross_weight_kg = null;
      return { item_id: "item" };
    }
    if (path.endsWith("/weights")) { box.actual_net_weight_kg = 3; box.actual_gross_weight_kg = 3.5; return { ready_for_lock: true }; }
    if (path.endsWith("/finalize")) { packing.status = "HANDOFF_02_RETURNED"; box.is_locked = true; return { status: packing.status }; }
    return [];
  });
  await page.goto("/packing?orderId=order");
  await expect(page.getByRole("button", { name: "Finalize Packing", exact: true })).toBeEnabled();
  if (process.env.UI_SCREENSHOTS) { await page.screenshot({ path: "../outputs/packing-workspace-boxes.png", fullPage: true }); await page.setViewportSize({ width: 390, height: 844 }); expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true); await page.screenshot({ path: "../outputs/packing-workspace-boxes-mobile.png", fullPage: true }); await page.setViewportSize({ width: 1280, height: 900 }); }
  await page.locator(".packing-box-details").first().evaluate(element => { (element as HTMLDetailsElement).open = true; }); await page.getByRole("button", { name: "Edit / substitute" }).click();
  if (process.env.UI_SCREENSHOTS) await page.screenshot({ path: "../outputs/packing-item-dialog.png" });
  await expect(page.getByLabel("Master product")).toHaveValue("herb");
  const search = page.waitForResponse(response => response.url().includes("q=missing"));
  await page.getByLabel("Find product").fill("missing");
  await search;
  await expect(page.getByLabel("Master product")).toHaveValue("herb");
  await expect(page.getByLabel("Master product").locator("option:checked")).toHaveText("HERB · Herb");
  await page.getByLabel("Packed quantity").fill("3");
  await page.getByLabel("Total item net weight (kg)").fill("3");
  await page.getByRole("button", { name: "Save packing adjustment" }).click();
  await expect.poll(() => adjusted).toBe(true);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByRole("button", { name: "Finalize Packing", exact: true }).click();
  await page.locator(".packing-box-details").first().evaluate(element => { (element as HTMLDetailsElement).open = true; }); await expect(page.getByRole("button", { name: "Edit / substitute" })).toHaveCount(0);
  await expect(page.getByText("Packing complete. Ready for accounting.", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /manager/i })).toHaveCount(0);

});

test("accounting generates and issues separate customer invoices together", async ({ page }) => {
  let drafts: Array<Record<string, unknown>> = [];
  let approved = false;
  await session(page, { value: "OWNER_ADMIN" }, async (path, route) => {
    if (path === "/invoicing/orders") return { items: ["A", "B"].map(code => ({ order_id: code, order_ref_id: `ORD-${code}`, customer_code: code, customer: `Customer ${code}`, status: "HANDOFF_02_RETURNED", can_invoice: true })), total: 2, offset: 0, limit: 25, has_more: false };
    if (path === "/invoicing/records") return { items: drafts.map(invoice => ({ ...invoice, amount: 10, paid: 0, currency: "THB" })), total: drafts.length, offset: 0, limit: 25, has_more: false };
    if (path === "/invoicing") return drafts;
    if (path === "/invoicing/generate-bulk") {
      expect(route.request().postDataJSON().order_ids).toEqual(["A", "B"]);
      drafts = ["A", "B"].map(code => ({ invoice_id: `invoice-${code}`, client_order_id: code, invoice_number: `INV-${code}`, invoice_type: "ON_BEHALF_SUB_CUSTOMER", sub_customer_buyer: code, total_amount_eur: 10, status: "INVOICE_DRAFT", source_order_count: 1, items: [] }));
      return drafts;
    }
    if (path === "/invoicing/approve-bulk") {
      expect(route.request().postDataJSON().invoice_ids).toEqual(["invoice-A", "invoice-B"]); approved = true;
      drafts = drafts.map(invoice => ({ ...invoice, status: "INVOICE_SENT" })); return drafts;
    }
    return [];
  });
  await page.goto("/billing");
  if (process.env.UI_SCREENSHOTS) await page.screenshot({ path: "../outputs/billing-create-dialog.png" });
  await page.getByRole("checkbox", { name: "Select A", exact: true }).click();
  await page.getByRole("checkbox", { name: "Select B", exact: true }).click();
  await page.getByRole("button", { name: "Create 2 separate drafts" }).click();
  await page.getByRole("checkbox", { name: "Select INV-A", exact: true }).check();
  await page.getByRole("checkbox", { name: "Select INV-B", exact: true }).check();
  await page.getByRole("button", { name: "Approve 2 reviewed drafts", exact: true }).click();
  await expect(page.getByRole("button", { name: "Approve and issue 2 invoices" })).toBeVisible();
  if (process.env.UI_SCREENSHOTS) await page.screenshot({ path: "../outputs/billing-approve-dialog.png" });
  await page.getByRole("button", { name: "Approve and issue 2 invoices" }).click();
  await expect.poll(() => approved).toBe(true);
  await expect(page.getByText("2 separate invoices approved and issued.", { exact: true })).toBeVisible();
});


test("original file download errors remain visible on the order page", async ({ page }) => {
  const ack = { raw_file_id: "file", file_name: "Orders.xlsx", total_sub_customers_detected: 0, sub_customer_codes: [], sheets_detected: [], group_summary: {}, sub_customer_details: [], verification_notice: "Review this upload", status: "UPLOADED", delivery: { status: "FAILED", recipient: "client@example.com" } };
  await session(page, { value: "OWNER_ADMIN" }, async path => {
    if (path === "/orders/intake-queue") return [{ raw_file_id: "file", file_name: "Orders.xlsx", shipment_target_date: "2026-10-02", route_line: "CT", total_sub_customers_detected: 0, unresolved_product_rows: 0, can_split: false }];
    if (path === "/orders/files/file") return ack;
    if (path === "/orders/file/validate") return { unresolved: [], can_split: false };
    return [];
  });
  await page.goto("/orders?fileId=file");
  await page.locator(".order-file-reference summary").click();
  await page.route("**/backend/api/v3/orders/file/source", route => route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Original file unavailable. Try again later." }) }));
  await expect(page.getByRole("button", { name: "Retry delivery" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Refresh delivery status" })).toHaveCount(0);
  await page.getByRole("button", { name: "Download original file" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Original file unavailable. Try again later." })).toBeVisible();
  await expect(page.getByRole("button", { name: "View acknowledgment" })).toHaveCount(0);
});


test("packing workspace starts with a customer picker and explains the next action", async ({ page }) => {
  const packing = { client_order_id: "order", order_ref_id: "ORD-BUYER", status: "HANDOFF_01_SENT", packing_approval_status: "NONE", packing_review_note: null, sub_customer_code: "BUYER", route_code: "CT", shipment_date: "2026-10-02", total_boxes_count: 0, boxes: [] };
  await session(page, { value: "GENERAL" }, async path => {
    if (path === "/packing/orders") return { items: [{ order_id: "order", order_ref_id: "ORD-BUYER", customer_code: "BUYER", customer: "Buyer shop", status: "HANDOFF_01_SENT", boxes: 0 }], total: 1, offset: 0, limit: 25, has_more: false };
    if (path.endsWith("/sub-packing-lists")) return { group_name: "ALL", sub_customers: [{ client_order_id: "order", sub_customer_code: "BUYER", company_name: "Buyer shop", order_ref_id: "ORD-BUYER", boxes_count: 0, total_estimated_net_weight_kg: 2 }] };
    if (path.endsWith("/prepare")) { packing.status = "FLOOR_PACKING"; return { status: packing.status }; }
    if (path.endsWith("/packing-list")) return packing;
    return [];
  });
  await page.goto("/packing/orders");
  await page.getByLabel("Search records").fill("Buyer shop");
  await page.getByRole("link", { name: "Start packing BUYER", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Packing desk", exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "Current task" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Prepare boxes", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Start packing", exact: true })).toHaveCount(0);
  if (process.env.UI_SCREENSHOTS) await page.screenshot({ path: "../outputs/packing-workspace-selected.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  if (process.env.UI_SCREENSHOTS) await page.screenshot({ path: "../outputs/packing-workspace-mobile.png", fullPage: true });
});


test("packing register ignores a previous search response", async ({ page }) => {
  let finishCT: () => void = () => {};
  const ctGate = new Promise<void>(resolve => { finishCT = resolve; });
  let ctRequested = false;
  await session(page, { value: "GENERAL" }, async (path, route) => {
    if (path !== "/packing/orders") return [];
    const q = new URL(route.request().url()).searchParams.get("q");
    if (q === "CT") { ctRequested = true; await ctGate; }
    const items = q ? [{ order_id: q, customer_code: `${q}-CUSTOMER`, order_ref_id: `ORD-${q}`, status: "FLOOR_PACKING", boxes: 1, outstanding: 1 }] : [];
    return { items, total: items.length, offset: 0, limit: 25, has_more: false };
  });
  await page.goto("/packing/orders");
  await page.getByLabel("Search records").fill("CT");
  await expect.poll(() => ctRequested).toBe(true);
  await page.getByLabel("Search records").fill("BE");
  await expect(page.getByRole("link", { name: "Resume packing BE-CUSTOMER" })).toBeVisible();
  finishCT();
  await expect(page.getByRole("link", { name: "Resume packing CT-CUSTOMER" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Resume packing BE-CUSTOMER" })).toBeVisible();
});
