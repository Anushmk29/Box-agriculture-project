import { expect, test } from "@playwright/test";

for (const pending of [false, true, "mixed"] as const) {
test(`staff confirm ${pending === "mixed" ? "mixed pack and weight orders" : pending ? "weight orders with actual packs" : "target defaults"} and dispatch in one action`, async ({ page, context }) => {
  const payload = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  await context.addCookies([{ name: "access_token", value: `qa.${payload}.qa`, domain: "127.0.0.1", path: "/" }]);
  let status = "PURCHASING_CONSOLIDATED";
  let dispatches = 0;
  let confirmations = 0;
  const item = {
    item_id: "handoff-item", product_id: "handoff-product", product_code: "QA001", product_name: "Handoff basil",
    produce_type: "HERB", pack_size: pending ? "600g+" : "1kg", ordered_pack_count: pending === "mixed" ? 5 : pending ? 0 : 10, ordered_weight_kg: 10, pack_count_pending: Boolean(pending),
    buffer_percentage: 10, buffer_pack_count: 1, buffer_weight_kg: 1,
    total_purchase_target_packs: pending ? 0 : 11, total_purchase_target_weight_kg: 11, required_packs: pending ? 0 : 11, required_weight_kg: 11,
    // Legacy prefilled values must not count as buyer approval.
    confirmed_purchased_pack_count: 11, confirmed_purchased_weight_kg: 11,
  };
  await page.route("**/backend/api/v3/**", async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.endsWith("/auth/me")) {
      await route.fulfill({ json: { full_name: "Handoff Tester", role: "OWNER_ADMIN", must_change_password: false,
        permissions: ["confirm_procurement", "dispatch_handoff_01", "floor_packing"] } });
    } else if (pathname.endsWith("/batches/handoff-batch")) {
      await route.fulfill({ json: { batch_id: "handoff-batch", batch_reference: "PUR-HANDOFF-QA", status, total_skus: 1,
        total_ordered_weight_kg: 10, total_buffer_weight_kg: 1, total_required_weight_kg: 11, items: [item] } });
    } else if (pathname.endsWith("/procurement/batches")) {
      await route.fulfill({ json: { items: [{ batch_id: "handoff-batch", batch_reference: "PUR-HANDOFF-QA", customers: ["Sunny"], files: [], total_skus: 1, outstanding_items: 1 }], has_more: false } });
    } else if (pathname.endsWith("/handoff-batch/confirm")) {
      expect(route.request().postDataJSON()).toEqual({ items: [{ item_id: item.item_id, purchased_pack_count: 11, purchased_weight_kg: 11 }] });
      confirmations++;
      status = "HANDOFF_01_SENT";
      await route.fulfill({ json: { status, batch_reference: "PUR-HANDOFF-QA" } });
    } else if (pathname.endsWith("/handoff-01/dispatch")) {
      expect(status).toBe("PROCUREMENT_CONFIRMED");
      expect(route.request().postDataJSON()).toEqual({ batch_id: "handoff-batch" });
      dispatches++;
      status = "HANDOFF_01_SENT";
      await route.fulfill({ json: { handoff_id: "test-handoff-01", payload_sha256: "a".repeat(64) } });
    } else await route.fulfill({ json: {} });
  });
  await page.goto("/procurement");
  await expect(page.getByRole("region", { name: "Purchase list" })).toHaveCount(0);
  await page.goto("/procurement?batchId=handoff-batch");
  const row = page.getByRole("row").filter({ hasText: "Handoff basil" });
  const packs = row.getByTitle("Confirmed Pack Count", { exact: true });
  const weight = row.getByTitle("Confirmed Total Weight (kg)", { exact: true });
  await expect(page.getByRole("link", { name: "Continue to packing", exact: true })).toHaveCount(0);
  const confirm = page.getByRole("button", { name: "Confirm purchases and send to packing", exact: true });
  await expect(packs).toHaveValue(pending ? "" : "11");
  if (pending) {
    await expect(row).toContainText(pending === "mixed" ? "5 packs + weight orders" : "Ordered by weight");
    await expect(row).toContainText("Record actual packs");
    await packs.fill("11");
  }
  // An older backend must not expose the new substitution action during rollout.
  await expect(page.getByRole("button", { name: "Choose substitute for QA001" })).toHaveCount(0);
  await expect(weight).toHaveValue("11");
  await weight.fill("");
  await confirm.click();
  await expect(page.getByText(/Correct the highlighted pending products/)).toBeVisible();
  expect(confirmations).toBe(0);
  await page.getByRole("button", { name: /^All Items/ }).click();
  await weight.fill("10");
  await expect(row.getByText("Needs Attention", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Back to register" }).click();
  await page.getByRole("dialog", { name: "Leave this record?" }).getByRole("button", { name: "Cancel" }).click();
  await expect(page).toHaveURL(/batchId=handoff-batch/);
  await expect(weight).toHaveValue("10");
  await weight.fill("11");
  await confirm.click();
  await expect(page.getByRole("link", { name: "Continue to packing", exact: true }).first()).toBeVisible();
  await expect(confirm).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Next: Packing", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Continue to packing", exact: true }).first()).toHaveAttribute("href", "/packing/orders?batch_id=handoff-batch&view=all&sort=shipment_date&direction=asc");
  await expect(page.getByRole("button", { name: "Fill actuals from targets", exact: true })).toHaveCount(0);
  await expect(packs).toBeDisabled();
  await expect(page.getByRole("button", { name: "Dispatch Signed Handoff 01" })).toHaveCount(0);
  expect(confirmations).toBe(1);
  expect(dispatches).toBe(0);
});

}
