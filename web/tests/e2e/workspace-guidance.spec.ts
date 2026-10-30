import { expect, test } from "@playwright/test";

const permissions = ["validate_orders", "confirm_procurement", "floor_packing", "design_packing", "manage_billing", "manage_users", "resolve_orders"];
const empty = { items: [], total: 0, offset: 0, limit: 25, has_more: false };

test.beforeEach(async ({ page, context }) => {
  const jwt = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  await context.addCookies([{ name: "access_token", value: `qa.${jwt}.qa`, domain: "127.0.0.1", path: "/" }]);
  await page.route("**/backend/api/v3/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { full_name: "Guide tester", role: "OWNER_ADMIN", must_change_password: false, permissions } });
    if (path.endsWith("/operations/summary")) return route.fulfill({ json: { awaiting_intake: 0, awaiting_procurement: 0, awaiting_packing: 0 } });
    if (path.endsWith("/packing-setup/table")) return route.fulfill({ json: { ...empty, summary: { total: 0, ready: 0, strict: true } } });
    return route.fulfill({ json: /register$|\/files$|\/batches$|\/orders$|\/records$|\/order-exceptions$|\/source-files$/.test(path) ? empty : [] });
  });
});

for (const [path, title] of [
  ["/", "Upload order"], ["/orders/files", "Upload order"],
  ["/procurement/batches", "Find purchase batches"], ["/procurement", "Choose batch"],
  ["/procurement/combine", "Find combine order files"], ["/packing/orders", "Find packing orders"],
  ["/packing", "Browse customer orders"], ["/packing/setup", "Find the product to review"],
  ["/billing", "Find orders to invoice"], ["/scan", "Scan the box sticker"],
  ["/users", "Add a team member"], ["/orders/attention", "Find orders needing attention"],
]) {
  test(`${path} guides one available action without submitting work`, async ({ page }) => {
    const writes: string[] = [];
    page.on("request", request => { if (request.url().includes("/backend/") && request.method() !== "GET") writes.push(request.url()); });
    await page.goto(path);
    const guide = page.getByRole("region", { name: "Next step guidance" });
    await expect(guide).toContainText(title);
    const target = page.locator("[data-guidance-current='true']");
    await expect(target).toHaveCount(1);
    await expect(target).toBeEnabled();
    await guide.getByRole("button", { name: "Show next action" }).click();
    await expect(target).toBeFocused();
    expect(writes).toEqual([]);
  });
}

test("scan guidance follows typed input, pulses only in view and respects shared pause and reduced motion", async ({ page }) => {
  await page.goto("/scan");
  const target = page.locator("[data-guidance-current='true']");
  await expect(target).toHaveText("Scan QR with camera");
  await target.scrollIntoViewIfNeeded(); await page.mouse.move(0, 0);
  await expect(target).toHaveAttribute("data-guidance-visible", "true");
  await expect(target).toHaveCSS("animation-name", "order-next-action");
  await page.getByLabel("Sticker code or QR link").fill("BOX-TEST");
  await expect(target).toHaveText("Open sticker");
  await page.getByRole("button", { name: "Pause hints", exact: true }).click();
  await expect(target).toHaveCSS("animation-name", "none");
  await page.goto("/users");
  await expect(page.getByRole("button", { name: "Resume hints", exact: true })).toBeVisible();
  await expect(target).toHaveCSS("animation-name", "none");
  await page.getByRole("button", { name: "Resume hints", exact: true }).click();
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(target).toHaveCSS("animation-name", "none");
  await expect(target).toHaveCSS("outline-style", "solid");
  await expect(target).toHaveCSS("background-color", "rgb(232, 242, 230)");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: "../outputs/workspace-guidance-mobile.png", fullPage: true });
});

test("dialog guidance advances through required fields and returns to the page on close", async ({ page }) => {
  await page.goto("/users");
  await page.getByRole("button", { name: "Add person", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Add person", exact: true });
  const target = page.locator("[data-guidance-current='true']");
  await expect(target).toHaveCount(1);
  await expect(target).toHaveAccessibleName("Full name");
  await dialog.getByLabel("Full name").fill("Packing staff");
  await expect(target).toHaveAccessibleName("Email");
  await dialog.getByLabel("Email", { exact: true }).fill("staff@example.com");
  await expect(target).toHaveText("Create user");
  await page.screenshot({ path: "../outputs/workspace-guidance-dialog-desktop.png", fullPage: true });
  await dialog.getByRole("button", { name: "Close Add person", exact: true }).click();
  await expect(target).toHaveText("Add person");
  await expect(target).toHaveCount(1);
});

test("login stays free of workspace guidance", async ({ page, context }) => {
  await context.clearCookies(); await page.goto("/login");
  await expect(page.getByRole("region", { name: "Next step guidance" })).toHaveCount(0);
  await page.getByRole("textbox", { name: "Email", exact: true }).fill("staff@example.com");
  await page.getByLabel("Password", { exact: true }).fill("Example-password-only");
  await expect(page.locator("[data-guidance-current='true']")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeEnabled();
});

test("required select guidance reads the field label without its option list", async ({ page }) => {
  await page.goto("/users");
  await page.getByRole("button", { name: "Add person", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Add person", exact: true });
  await dialog.getByLabel("Full name").fill("Packing staff");
  await dialog.getByLabel("Email", { exact: true }).fill("staff@example.com");
  await dialog.evaluate(element => {
    const label = document.createElement("label"); label.textContent = "Catalog product";
    const select = document.createElement("select"); select.required = true;
    select.append(new Option("Choose a product", ""));
    for (let i = 0; i < 26; i++) select.append(new Option(`Long catalog option ${i}`, String(i)));
    label.append(select); element.querySelector(".task-dialog-body")!.append(label);
  });
  const guide = dialog.getByRole("region", { name: "Next step guidance" });
  await expect(guide.locator("strong")).toHaveText("Complete Catalog product");
  await expect(guide).not.toContainText("Long catalog option");
});

test("purchasing guides review before confirmation and never confirms automatically", async ({ page }) => {
  const item = { item_id: "one", product_id: "basil", product_code: "BASIL", product_name: "Basil", produce_type: "HERB", pack_size: "1kg", ordered_pack_count: 10, ordered_weight_kg: 10, buffer_percentage: 0, buffer_pack_count: 0, buffer_weight_kg: 0, total_purchase_target_packs: 10, total_purchase_target_weight_kg: 10, required_packs: 10, required_weight_kg: 10, confirmed_purchased_pack_count: 10, confirmed_purchased_weight_kg: 10 };
  await page.route("**/backend/api/v3/procurement/batches/guide-batch", route => route.fulfill({ json: { batch_id: "guide-batch", batch_reference: "PUR-GUIDE", status: "PURCHASING_CONSOLIDATED", total_skus: 1, total_ordered_weight_kg: 10, total_buffer_weight_kg: 0, total_required_weight_kg: 10, items: [item] } }));
  await page.goto("/procurement?batchId=guide-batch");
  const target = page.locator("[data-guidance-current='true']");
  await expect(target).toHaveText("Review purchase quantities");
  await page.getByRole("region", { name: "Next step guidance" }).getByRole("button", { name: "Show next action" }).click();
  await target.click(); // This is the review anchor; it only focuses the worksheet.
  await expect(target).toHaveText("Confirm purchases and send to packing");
  await expect(target).toBeEnabled();
});

test("packing guides rule choice and box generation while the final action is blocked", async ({ page }) => {
  await page.route("**/backend/api/v3/packing/orders/guide-order/packing-list", route => route.fulfill({ json: { client_order_id: "guide-order", packing_revision: 1, order_ref_id: "ORD-GUIDE", status: "HANDOFF_01_SENT", sub_customer_code: "SHOP", route_code: "TETE", shipment_date: "2026-10-29", total_boxes_count: 0, boxes: [] } }));
  await page.goto("/packing?orderId=guide-order");
  const target = page.locator("[data-guidance-current='true']");
  await expect(target).toHaveAccessibleName("Packing rules");
  await target.selectOption("NON_EL");
  await expect(target).toHaveText("Generate packing boxes");
  await expect(target).toBeEnabled();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.screenshot({ path: "../outputs/workspace-guidance-packing-desktop.png", fullPage: false });
});

test("missing selling prices guide each amount before saving", async ({ page }) => {
  await page.route("**/backend/api/v3/invoicing/price-check", route => route.fulfill({ json: { issues: [{ order_id: "order", order_ref: "ORD-PRICE", customer_name: "Shop", shipment_date: "2026-10-29", items: [{ product_id: "basil", product_code: "BASIL", product_name: "Basil", pack_size: "100g" }, { product_id: "leek", product_code: "LEEK", product_name: "Leek", pack_size: "200g" }] }] } }));
  await page.goto("/billing/resolve-prices?orderId=order");
  const target = page.locator("[data-guidance-current='true']");
  await expect(target).toHaveAccessibleName("Selling price for BASIL 100g Shop");
  await target.fill("10");
  await expect(target).toHaveAccessibleName("Selling price for LEEK 200g Shop");
  await target.fill("20");
  await expect(target).toHaveText("Save prices and return to billing");
});

test("invoice guidance requires review and points to saving after an edit", async ({ page }) => {
  await page.route("**/backend/api/v3/invoicing/guide-invoice", route => route.fulfill({ json: {
    invoice_id: "guide-invoice", revision: 1, client_order_id: "order", invoice_number: "INV-GUIDE", invoice_type: "ON_BEHALF_SUB_CUSTOMER", main_customer_seller: "Seller", sub_customer_buyer: "Shop", currency: "THB", subtotal_eur: 10, total_amount_eur: 10, amount_paid_eur: 0, status: "INVOICE_DRAFT", internal_notes: null,
    items: [{ line_item_id: "line", product_code: "BASIL", product_name: "Basil", pack_size: "100g", billed_packs: 10, billed_weight_kg: 1, unit_price_eur: 1 }],
  } }));
  await page.goto("/billing?invoiceId=guide-invoice");
  const target = page.locator("[data-guidance-current='true']");
  await expect(target).toHaveText("Review invoice details");
  await target.click();
  await expect(target).toHaveText("Approve and create final invoice");
  await page.getByRole("spinbutton", { name: "Unit price for BASIL", exact: true }).fill("2");
  await expect(target).toHaveText("Save changes");
});

test("temporary password setup guides valid fields before saving", async ({ page }) => {
  await page.route("**/backend/api/v3/auth/me", route => route.fulfill({ json: { full_name: "New staff", role: "GENERAL", must_change_password: true, permissions: [] } }));
  await page.goto("/change-password");
  const target = page.locator("[data-guidance-current='true']");
  await expect(target).toHaveAccessibleName("Temporary password");
  await target.fill("temporary-value");
  await expect(target).toHaveAccessibleName("New password");
  await target.fill("short");
  await expect(target).toHaveAccessibleName("New password");
  await target.fill("A-longer-example-password");
  await expect(target).toHaveText("Save password");
});

test("failed record retrieval guides retry before choosing a record", async ({ page }) => {
  await page.route("**/backend/api/v3/packing/orders?**", route => route.fulfill({ status: 503, json: { detail: "Records temporarily unavailable" } }));
  await page.goto("/packing/orders");
  const target = page.locator("[data-guidance-current='true']");
  await expect(target).toHaveText("Retry");
  await expect(page.getByRole("region", { name: "Next step guidance" })).toContainText("Retry loading these records");
});
