import { expect, test, type Page } from "@playwright/test";

async function packingDesk(page: Page, editable = false, billing = true) {
  const token = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  await page.context().addCookies([{ name: "access_token", value: `qa.${token}.qa`, domain: "127.0.0.1", path: "/" }]);
  const names = ["Sầu riêng vị mềm / DURIAN PEELED", "PINK GUAVA / PINK GUAVA", "rau cau dua trai / AROMATIC COCONUT JELLY (COCO 4U BRAND)", "Bánh da lợn / STEAMED PANDAN LAYER CAKE"];
  const boxes = Array.from({ length: 21 }, (_, index) => ({
    box_id: `box-${index + 1}`, box_number: index + 1, box_type_name: "Mini", box_catalogue_id: "mini", box_material: "CARDBOARD",
    safe_net_capacity_kg: 5, is_overridden: false, actual_net_weight_kg: null, actual_gross_weight_kg: null,
    is_locked: !editable, qr_code_uid: `QR-${index + 1}`, sticker_label: `124-Ly-Thanh-Sarl_Vegetable_for_Monday_07-09-2026 / ${index + 1} of 21`,
    packing_mode: "EL", allocation_explanation: "Products are grouped by compatible material, temperature and handling. This box allows 5 kg net.",
    layers: [{ item_id: `item-${index}-0`, layer: "Top", packed_count: 3, product_name: names[index % names.length], pack_size: "100g" }],
    items: Array.from({ length: index % 3 + 2 }, (_, item) => ({ item_id: `item-${index}-${item}`, product_id: `product-${item}`, product_code: `22-${index}${item}`,
      produce_name: names[(index + item) % names.length], pack_size: "100g", packed_count: 3, net_weight_kg: .3, adjustment_type: "AS_ORDERED" })),
  }));
  const data = { client_order_id: "order", file_name: "Ly-Thanh-Sarl Vegetable for Monday.xlsx", order_ref_id: "ORD-20261006-Ly-Thanh-Sarl_Vegetable_for_Monday_07-09-2026-c91ea5443d1b508a", status: editable ? "FLOOR_PACKING" : "HANDOFF_02_RETURNED", packing_approval_status: "NONE", packing_review_note: null,
    sub_customer_code: "Ly-Thanh-Sarl__Vegetable_for_Monday_07-09-2026", route_code: "GROUP GENERAL", shipment_date: "2026-10-06", total_boxes_count: boxes.length, boxes };
  const state = { finalizes: 0 };
  await page.route("**/backend/api/v3/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { full_name: "Packing staff", role: billing ? "OWNER_ADMIN" : "GENERAL", must_change_password: false,
      permissions: ["floor_packing", "design_packing", ...(billing ? ["manage_billing"] : [])] } });
    if (path.endsWith("/master-data/boxes")) return route.fulfill({ json: [{ id: "mini", size_name: "Mini", box_type: "CARDBOARD", length_cm: 26, width_cm: 32, height_cm: 14 }] });
    if (path.endsWith("/packing-list")) return route.fulfill({ json: data });
    if (path.endsWith("/finalize")) { state.finalizes++; data.status = "HANDOFF_02_RETURNED"; boxes.forEach(box => { box.is_locked = true; }); return route.fulfill({ json: {} }); }
    return route.fulfill({ json: {} });
  });
  return { data, state };
}

test("completed desk shows one next step and keeps order metadata out of the box list", async ({ page }) => {
  const { data } = await packingDesk(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/packing?orderId=order&returnTo=%2Fpacking%2Forders%3Fbatch_id%3Dbatch");
  await expect(page.getByRole("link", { name: "Continue to billing" })).toHaveAttribute("href", "/billing?orderId=order");
  await expect(page.locator(".packing-desk .primary-button")).toHaveCount(1);
  await expect(page.getByRole("link", { name: "Packing orders", exact: true })).toHaveAttribute("href", "/packing/orders?batch_id=batch");
  await expect(page.getByText(data.order_ref_id, { exact: true })).toBeHidden();
  await expect(page.getByRole("navigation", { name: "Shipment workflow" })).toBeHidden();
  await expect(page.locator(".packing-box-details[open]")).toHaveCount(0);
  await expect(page.locator("#box-box-1")).toBeInViewport();
  await page.screenshot({ path: "../outputs/packing-desk-desktop.png", fullPage: false });
  await page.getByText("Order details", { exact: true }).click();
  await expect(page.locator(".packing-order-details").getByText(data.file_name, { exact: true })).toBeVisible();
  await expect(page.getByText(data.order_ref_id, { exact: true })).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "Shipment workflow" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByText(data.order_ref_id, { exact: true })).toBeHidden();
  await expect(page.getByText("Order details", { exact: true })).toBeFocused();
});

test("box rows open one at a time and preserve label and plan details", async ({ page }) => {
  await packingDesk(page);
  await page.goto("/packing?orderId=order");
  const first = page.locator("#box-box-1");
  const second = page.locator("#box-box-2");
  await first.locator("summary.packing-box-main").click();
  await expect(first.getByRole("table")).toBeVisible();
  await expect(first.getByRole("button", { name: "Edit / substitute" })).toHaveCount(0);
  await expect(first.locator("select")).toHaveCount(0);
  await expect(first.getByText(/Products are grouped/)).toBeHidden();
  await first.getByText("Packing & label details", { exact: true }).click();
  await expect(first.getByText(/Products are grouped/)).toBeVisible();
  await first.getByRole("button", { name: "Sticker", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "Box sticker", exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "Box sticker", exact: true })).toHaveCount(0);
  await second.locator("summary.packing-box-main").focus();
  await page.keyboard.press("Enter");
  await expect(second.locator(".packing-box-details")).toHaveAttribute("open", "");
  await expect(first.locator(".packing-box-details")).not.toHaveAttribute("open");
  await expect(page.locator(".packing-box-details[open]")).toHaveCount(1);
  await page.screenshot({ path: "../outputs/packing-desk-expanded.png", fullPage: false });
});

test("editable boxes keep their controls and finalization advances to the next step", async ({ page }) => {
  const { state } = await packingDesk(page, true);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/packing?orderId=order");
  const first = page.locator("#box-box-1");
  await first.locator("summary.packing-box-main").click();
  await expect(first.getByLabel("Box 1 packing rules", { exact: true })).toBeEnabled();
  await expect(first.getByRole("button", { name: "Edit / substitute" }).first()).toBeEnabled();
  await expect(first.getByRole("button", { name: "Add packing item" })).toBeEnabled();
  await page.screenshot({ path: "../outputs/packing-desk-editable-desktop.png", fullPage: false });
  await page.getByRole("button", { name: "Finalize Packing", exact: true }).click();
  await expect(page.getByRole("link", { name: "Continue to billing" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Finalize Packing", exact: true })).toHaveCount(0);
  await expect(first.getByRole("button", { name: "Edit / substitute" })).toHaveCount(0);
  expect(state.finalizes).toBe(1);
});

test("phone scan opens the right box without overflow and respects reduced motion", async ({ page }) => {
  await packingDesk(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/packing?orderId=order&box=QR-19");
  const box = page.locator("#box-box-19");
  await expect(box).toBeVisible();
  await expect(box.locator(".packing-box-details")).toHaveAttribute("open", "");
  await expect(box.locator(".packing-box-main")).toHaveCSS("animation-name", "none");
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect(page.getByRole("button", { name: "Next box", exact: true })).toBeVisible();
  await expect.poll(() => box.locator(".packing-items-scroll").evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await page.screenshot({ path: "../outputs/packing-desk-mobile.png", fullPage: false });
});

test("staff without billing access get a packing action and can find supporting tools", async ({ page }) => {
  await packingDesk(page, false, false);
  await page.goto("/packing?orderId=order");
  await expect(page.getByRole("link", { name: "Continue to billing" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Pack next order", exact: true }).first()).toBeVisible();
  await page.getByText("Order tools", { exact: true }).click();
  await expect(page.getByRole("button", { name: "Export vehicle load" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Products & box settings" })).toBeVisible();
});

for (const width of [1024, 390]) {
test(`editable contents stay within the ${width}px viewport`, async ({ page }) => {
  await packingDesk(page, true);
  await page.setViewportSize({ width, height: 1000 });
  await page.goto("/packing?orderId=order");
  await page.locator("#box-box-1 > details > summary").click();
  await expect(page.getByLabel("Box 1 packing rules", { exact: true })).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect.poll(() => page.locator("#box-box-1 .packing-items-scroll").evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await expect(page.getByLabel("Box 1 packing rules", { exact: true })).toHaveCSS("min-height", "44px");
  const search = page.getByRole("searchbox", { name: "Find box or product", exact: true });
  const searchBounds = await search.boundingBox();
  const iconBounds = await page.locator(".packing-box-search > svg").boundingBox();
  expect(searchBounds!.x).toBeGreaterThan(iconBounds!.x + iconBounds!.width);
  await page.screenshot({ path: `../outputs/packing-desk-editable-${width}.png`, fullPage: false });
  const edit = page.locator("#box-box-1").getByRole("button", { name: "Edit / substitute" }).first();
  await edit.scrollIntoViewIfNeeded();
  await expect(edit).toBeInViewport();
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
}
