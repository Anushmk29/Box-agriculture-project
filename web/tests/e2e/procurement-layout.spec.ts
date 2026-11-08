import { expect, test } from "@playwright/test";

for (const width of [1920, 1440, 1024, 390]) {
  test(`purchase quantities stay readable and editable at ${width}px`, async ({ page, context }) => {
    const jwt = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
    await context.addCookies([{ name: "access_token", value: `qa.${jwt}.qa`, domain: "127.0.0.1", path: "/" }]);
    await page.setViewportSize({ width, height: 1000 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    const names = ["Củ nghệ / Turmeric", "มะขามเทศ / Manila Tamarind with a longer product name", "Đậu bắp / OKRA", "Cóc đắng / HOG PLUM", "Ngò rí / Mùi thái", "Riềng trắng / Galanga white"];
    const items = Array.from({ length: 59 }, (_, index) => ({ item_id: `item-${index}`, product_id: `product-${index}`, product_code: `T${String(index + 1).padStart(3, "0")}`, product_name: names[index % names.length],
      produce_type: index === 4 ? "HERB" : "FRUIT", pack_size: index === 4 ? "80g" : "100g tray", ordered_pack_count: index < 6 ? 0 : 45, pack_count_pending: index < 6,
      ordered_weight_kg: 4.5, buffer_percentage: 8, buffer_pack_count: 4, buffer_weight_kg: .36,
      total_purchase_target_packs: index < 6 ? 0 : 49, total_purchase_target_weight_kg: 4.86, required_packs: index < 6 ? 0 : 49, required_weight_kg: 4.86 }));
    await page.route("**/backend/api/v3/**", async route => {
      const url = new URL(route.request().url());
      if (url.pathname.endsWith("/auth/me")) return route.fulfill({ json: { full_name: "Market Buyer", role: "OWNER_ADMIN", permissions: ["confirm_procurement", "edit_buffers", "floor_packing"], must_change_password: false } });
      if (url.pathname.endsWith("/batches/layout-batch")) return route.fulfill({ json: { batch_id: "layout-batch", batch_reference: "PUR-20261016-7e442e77-0ce5-46af-9255-9ea95fcc31db", status: "PURCHASING_CONSOLIDATED", substitutions_supported: true,
        source_files: [{ file_id: "source", file_name: "Order Tonio 10.07.26 FULL.xlsx", customer: "Tonio", shipment_date: "2026-10-16", route: "TETE" }], total_skus: 59,
        total_ordered_weight_kg: 685.8, total_buffer_weight_kg: 58.468, total_required_weight_kg: 744.268, items } });
      return route.fulfill({ json: [] });
    });
    await page.goto("/procurement?batchId=layout-batch");
    const purchaseList = page.getByRole("region", { name: "Purchase list", exact: true });
    await expect(purchaseList).toBeVisible();
    await expect(page.getByRole("region", { name: "Next step guidance" })).toHaveCount(0);
    await expect(page.getByRole("navigation", { name: "Shipment workflow" })).toHaveCount(0);
    await expect(purchaseList.getByRole("columnheader")).toHaveCount(5);
    await expect(purchaseList.getByText("Exact Match", { exact: true })).toHaveCount(0);
    await expect(purchaseList.getByText("Ready to confirm", { exact: true })).toHaveCount(0);
    await expect(page.getByLabel("Purchase quantities", { exact: true })).toContainText("744.268 kg");
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    const packs = page.getByRole("spinbutton", { name: "Actual packs for T001", exact: true });
    await expect(packs).toHaveAttribute("aria-invalid", "true");
    expect((await packs.boundingBox())?.height).toBeGreaterThanOrEqual(44);
    await page.screenshot({ path: `../outputs/purchasing-clean-${width}.png`, animations: "disabled" });
    await packs.fill("49");
    await expect(packs).toHaveAttribute("aria-invalid", "false");
    const weight = page.getByRole("spinbutton", { name: "Actual kilograms for T001", exact: true });
    await weight.fill("4.25");
    await expect(purchaseList.getByRole("row").filter({ has: packs }).getByText("Needs Attention", { exact: true })).toBeVisible();
    await page.getByRole("searchbox", { name: "Search products", exact: true }).fill("T005");
    await expect(purchaseList.getByRole("row")).toHaveCount(2);
    await expect(purchaseList).toContainText("Ngò rí / Mùi thái");
    await page.getByRole("searchbox", { name: "Search products", exact: true }).fill("");
    await page.getByRole("button", { name: "Batch details", exact: true }).click();
    const details = page.getByRole("dialog", { name: "Batch details", exact: true });
    await expect(details).toContainText("Order Tonio 10.07.26 FULL.xlsx");
    await expect(details).toContainText("PUR-20261016-7e442e77-0ce5-46af-9255-9ea95fcc31db");
    await page.keyboard.press("Escape");
    await expect(details).toBeHidden();
  });
}
