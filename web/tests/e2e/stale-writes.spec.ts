import { expect, test, type BrowserContext } from "@playwright/test";

async function signIn(context: BrowserContext) {
  const payload = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  await context.addCookies([{ name: "access_token", value: `qa.${payload}.qa`, domain: "127.0.0.1", path: "/" }]);
}

const conflict = { detail: { code: "STALE_REVISION", current_revision: 2,
  message: "Someone changed this record. Your changes were not saved. Reload and review the latest record before trying again." } };
const user = { full_name: "Reviewer", role: "OWNER_ADMIN", must_change_password: false,
  permissions: ["manage_billing", "draft_invoice", "floor_packing", "design_packing"] };

test("invoice conflict keeps entered prices until an explicit confirmed reload", async ({ page, context }) => {
  await signIn(context);
  const invoice = { invoice_id: "invoice", revision: 1, client_order_id: "order", invoice_number: "INV-REVIEW",
    invoice_type: "ON_BEHALF_SUB_CUSTOMER", main_customer_seller: "Seller", sub_customer_buyer: "Buyer",
    subtotal_eur: 10, total_amount_eur: 10, currency: "THB", amount_paid_eur: 0, status: "INVOICE_DRAFT",
    internal_notes: null, source_order_count: 1, items: [{ line_item_id: "line", source_order_id: "order",
      source_order_ref: "ORD-REVIEW", source_customer_code: "BUYER", product_code: "HERB", product_name: "Herb",
      box_number: 1, pack_size: "1kg", billed_packs: 2, billed_weight_kg: 2, unit_price_eur: 5, line_total_eur: 10 }] };
  let reads = 0;
  let writes = 0;
  await page.route("**/backend/api/v3/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: user });
    if (path.endsWith("/invoice/draft")) {
      writes++;
      expect(route.request().postDataJSON().expected_revision).toBe(1);
      expect(route.request().postDataJSON().items[0].unit_price_eur).toBe(7);
      return route.fulfill({ status: 409, json: conflict });
    }
    if (path.endsWith("/invoicing/invoice")) {
      reads++;
      return route.fulfill({ json: reads === 1 ? invoice : { ...invoice, revision: 2,
        items: [{ ...invoice.items[0], unit_price_eur: 6 }] } });
    }
    return route.fulfill({ json: [] });
  });
  await page.goto("/billing?invoiceId=invoice");
  const price = page.getByRole("spinbutton", { name: "Unit price for HERB", exact: true });
  await price.fill("7");
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(page.getByText(/Someone changed this record/)).toBeVisible();
  await expect(price).toHaveValue("7");
  await expect(page.getByRole("button", { name: "Save changes", exact: true })).toBeDisabled();
  expect(reads).toBe(1);
  expect(writes).toBe(1);
  await page.getByRole("button", { name: "Reload latest invoice for review" }).click();
  const confirmation = page.getByRole("dialog", { name: "Discard invoice changes?" });
  await expect(confirmation).toBeVisible();
  await confirmation.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(price).toHaveValue("7");
  expect(reads).toBe(1);
  await page.getByRole("button", { name: "Reload latest invoice for review" }).click();
  await page.getByRole("dialog", { name: "Discard invoice changes?" }).getByRole("button", { name: "Discard changes", exact: true }).click();
  await expect(price).toHaveValue("6");
  await expect(page.getByRole("button", { name: "Save changes", exact: true })).toBeEnabled();
  expect(reads).toBe(2);
});

test("packing edit keeps the revision captured when its dialog opened", async ({ page, context }) => {
  await signIn(context);
  const item = { item_id: "line", product_id: "herb", product_code: "HERB", produce_name: "Herb", pack_size: "1kg",
    packed_count: 2, net_weight_kg: 2, adjustment_type: "AS_ORDERED" };
  const packing = { client_order_id: "order", packing_revision: 1, order_ref_id: "ORD-REVIEW", status: "FLOOR_PACKING",
    packing_approval_status: "NONE", packing_review_note: null, sub_customer_code: "BUYER", route_code: "TETE",
    shipment_date: "2026-10-27", total_boxes_count: 1, boxes: [{ box_id: "box", box_number: 1,
      box_type_name: "Mini", box_catalogue_id: "mini", box_material: "FOAM", safe_net_capacity_kg: 5,
      is_overridden: false, actual_net_weight_kg: null, actual_gross_weight_kg: null, is_locked: false,
      qr_code_uid: "QR-REVIEW", sticker_label: "BUYER / 1 of 1", packing_mode: "EL", items: [item] }] };
  let reads = 0;
  await page.route("**/backend/api/v3/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: user });
    if (path.endsWith("/packing-list")) { reads++; return route.fulfill({ json: packing }); }
    if (path.endsWith("/master-data/products")) return route.fulfill({ json: [{ id: "herb", t_code: "HERB", name_en: "Herb", name_vi: "Herb" }] });
    if (path.endsWith("/master-data/boxes")) return route.fulfill({ json: [{ id: "mini", size_name: "Mini", box_type: "FOAM", length_cm: 30, width_cm: 20, height_cm: 24 }] });
    if (path.endsWith("/box/items")) {
      expect(route.request().postDataJSON().expected_revision).toBe(1);
      return route.fulfill({ status: 409, json: conflict });
    }
    return route.fulfill({ json: {} });
  });
  await page.goto("/packing?orderId=order");
  await page.locator("#box-box").locator("summary.packing-box-main").click();
  await page.getByRole("button", { name: "Edit / substitute", exact: true }).click();
  const editor = page.getByRole("dialog", { name: "Adjust packing item" });
  const quantity = editor.getByRole("spinbutton", { name: "Packed quantity" });
  await quantity.fill("1");
  await editor.getByRole("spinbutton", { name: "Total item net weight (kg)" }).fill("1");
  await editor.getByRole("button", { name: "Save packing adjustment" }).click();
  await expect(editor.getByText(/Someone changed this record/)).toBeVisible();
  await expect(quantity).toHaveValue("1");
  expect(reads).toBe(2); // Selection checks existing preparation, then loads the desk.
  await expect(editor.getByRole("button", { name: "Reload latest packing for review" })).toBeVisible();
});
