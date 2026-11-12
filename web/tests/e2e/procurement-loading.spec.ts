import { expect, test } from "@playwright/test";

for (const outcome of ["items", "empty", "error"] as const) {
  test(`silent procurement loading resolves to ${outcome}`, async ({ page, context }) => {
    await page.setViewportSize({ width: outcome === "empty" ? 390 : 1440, height: 900 });
    const payload = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
    await context.addCookies([{ name: "access_token", value: `qa.${payload}.qa`, domain: "127.0.0.1", path: "/" }]);
    let release!: () => void;
    const pending = new Promise<void>(resolve => { release = resolve; });
    await page.route("**/backend/api/v3/**", async route => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname.endsWith("/procurement/batches/qa-batch")) {
        await pending;
        await route.fulfill({ status: outcome === "error" ? 500 : 200, json: outcome === "error"
          ? { detail: "Batch service unavailable" }
          : {
            batch_id: "qa-batch", batch_reference: "PUR-LOADING-QA", status: "DRAFT", total_skus: 1,
            total_ordered_weight_kg: 10, total_buffer_weight_kg: 1, total_required_weight_kg: 11,
            items: outcome === "empty" ? [] : [{ item_id: "qa-item", product_id: "qa-product", product_code: "QA001", product_name: "Loading test basil",
              produce_type: "HERB", pack_size: "1 kg", ordered_pack_count: 10, ordered_weight_kg: 10,
              buffer_percentage: 10, buffer_pack_count: 1, buffer_weight_kg: 1,
              total_purchase_target_packs: 11, total_purchase_target_weight_kg: 11, required_packs: 11, required_weight_kg: 11 }],
          } });
      } else if (pathname.endsWith("/procurement/batches")) {
        await route.fulfill({ json: { items: [{ batch_id: "qa-batch", batch_reference: "PUR-LOADING-QA", customers: ["Sunny"], files: [], total_skus: 1 }], has_more: false } });
      } else {
        await route.fulfill({ json: pathname.endsWith("/auth/me")
          ? { full_name: "QA User", role: "OWNER_ADMIN", must_change_password: false, permissions: ["confirm_procurement"] }
          : {} });
      }
    });
    const request = page.waitForRequest("**/procurement/batches/qa-batch");
    const selection = page.goto("/procurement?batchId=qa-batch");
    await request;
    const modal = page.getByRole("dialog", { name: "Loading procurement batch" });
    await expect(modal).toHaveCount(0);
    if (outcome === "empty") await expect(page.getByRole("button", { name: "Menu", exact: true })).toBeVisible();
    else await expect(page.getByRole("navigation", { name: "Primary navigation" })).toBeVisible();

    await expect(page.getByRole("region", { name: "Purchase list" })).toHaveCount(0);
    release();
    await selection;
    await expect(modal).toHaveCount(0);
    if (outcome !== "error") {
      if (outcome === "items") await expect(page.getByText("Loading test basil", { exact: true })).toBeVisible();
      await expect(page.getByRole("region", { name: "Purchase list" })).toBeVisible();
    } else {
      await expect(page.getByText("Choose a purchase batch", { exact: true })).toBeVisible();
      if (outcome === "error") await expect(page.getByText("Batch service unavailable", { exact: true })).toBeVisible();
    }

  });
}
