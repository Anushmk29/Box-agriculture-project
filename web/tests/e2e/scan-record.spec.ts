import { expect, test, type Page } from "@playwright/test";

const box = {
  order_id: "order-1", qr_code_uid: "BOX-GROUP GENERAL-20261006-001-67ced9ad", box_number: 1,
  order_ref_id: "ORD-20261006-Ly-Thanh-Sarl__Vegetable_for_Monday_07-09-2026-c91ea5443d1b508a",
  file_name: "Ly-Thanh-Sarl Vegetable for Monday 07-09-2026.xlsx",
  order_status: "FINAL_PACK_LOCKED", can_edit: false,
  customer_code: "Ly-Thanh-Sarl__Vegetable_for_Monday_07-09-2026", route_code: "GROUP GENERAL",
  shipment_date: "2026-10-06", is_locked: true, actual_net_weight_kg: 2.8, actual_gross_weight_kg: 3,
  items: [
    { product_code: "22-206", produce_name: "DURIAN PEELED", pack_size: "~500Gr.", packed_count: 5, net_weight_kg: 2.5 },
    { product_code: "22-291", produce_name: "PINK GUAVA", pack_size: "100g", packed_count: 3, net_weight_kg: 0.3 },
  ],
  layers: [
    { item_id: "1", layer: "Needs review", packed_count: 5, product_name: "DURIAN PEELED", pack_size: "~500Gr." },
    { item_id: "2", layer: "Needs review", packed_count: 3, product_name: "PINK GUAVA", pack_size: "100g" },
  ],
  change_history: [],
};

async function mockSession(page: Page, data: Omit<typeof box, "actual_gross_weight_kg"> & { actual_gross_weight_kg: number | null } = box) {
  const payload = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  await page.context().addCookies([{ name: "access_token", value: `qa.${payload}.qa`, domain: "127.0.0.1", path: "/" }]);
  const permissions = ["validate_orders", "confirm_procurement", "floor_packing", "manage_billing", "manage_users"];
  await page.route("**/backend/api/v3/**", route => {
    const path = new URL(route.request().url()).pathname;
    return route.fulfill({ json: path.endsWith("/auth/me") ? { full_name: "Admin System", role: "OWNER_ADMIN", permissions }
      : path.endsWith("/operations/summary") ? { permissions }
      : path.includes("/packing/scan/") && !path.endsWith("/history") ? data
      : { items: [], total: 0, offset: 0, limit: 25, has_more: false } });
  });
}

for (const width of [390, 768, 1440, 1920]) {
  test(`scan record keeps essentials readable at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    await mockSession(page);
    let historyRequests = 0;
    page.on("request", request => { if (request.url().includes("/history?")) historyRequests++; });
    await page.goto("/scan/QA-BOX");
    await expect(page.getByText("Final pack locked", { exact: true })).toBeVisible();
    await expect(page.getByText("2 products · 8 packs", { exact: true })).toBeVisible();
    await expect(page.getByRole("row", { name: /DURIAN PEELED/ })).toContainText("2.50 kg");
    await expect(page.getByText("Final orders are read-only", { exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "View this box in packing" })).toHaveAttribute("href", "/packing?orderId=order-1&box=BOX-GROUP%20GENERAL-20261006-001-67ced9ad");
    const guide = page.getByRole("region", { name: "Next step guidance" });
    await expect(guide).toHaveClass(/workspace-guide-inline/);
    expect(historyRequests).toBe(0);
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: `../outputs/scan-record-${width}.png`, fullPage: true });
    await page.locator("summary").filter({ hasText: "Order file & box details" }).click();
    await expect(page.getByText(box.file_name, { exact: true })).toBeVisible();
    await expect(page.getByText(box.order_ref_id, { exact: true })).toHaveCount(0);
    await page.locator("summary").filter({ hasText: "Change history" }).click();
    await expect(page.getByText("No recorded changes yet.")).toBeVisible();
    expect(historyRequests).toBeGreaterThan(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
}

test("editable empty box distinguishes missing weights from zero", async ({ page }) => {
  await mockSession(page, { ...box, can_edit: true, is_locked: false, items: [], layers: [], actual_net_weight_kg: 0, actual_gross_weight_kg: null });
  await page.goto("/scan/QA-BOX");
  await expect(page.getByText("Open for packing", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Open this box in packing" })).toBeVisible();
  await expect(page.getByText("No products recorded in this box yet.")).toBeVisible();
  const weights = page.getByRole("definition");
  await expect(weights.filter({ hasText: "0 kg" })).toBeVisible();
  await expect(page.getByText("Not finalized", { exact: true })).toBeVisible();
  await expect(page.getByText("Not finalized kg", { exact: true })).toHaveCount(0);
});

test("expanded history supports retry and pagination", async ({ page }) => {
  await mockSession(page);
  let fail = true;
  await page.route("**/packing/scan/*/history?*", route => {
    if (fail) { fail = false; return route.fulfill({ status: 500, json: { detail: "History unavailable" } }); }
    const offset = Number(new URL(route.request().url()).searchParams.get("offset"));
    return route.fulfill({ json: { items: [{ event_type: "BOX_WEIGHT_RECORDED", actor_name: offset ? "Second packer" : "First packer", changed_at: "2026-10-06T10:00:00Z", before: {}, after: { actual_net_weight_kg: 2.8 } }], total: 26, offset, limit: 25, has_more: !offset } });
  });
  await page.goto("/scan/QA-BOX");
  await page.locator("summary").filter({ hasText: "Change history" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "History unavailable" })).toBeVisible();
  await page.getByRole("button", { name: "Retry history" }).click();
  await expect(page.getByText(/First packer/)).toBeVisible();
  await page.getByRole("button", { name: "Next changes" }).click();
  await expect(page.getByText(/Second packer/)).toBeVisible();
  await page.getByRole("button", { name: "Previous changes" }).click();
  await expect(page.getByText(/First packer/)).toBeVisible();
});
