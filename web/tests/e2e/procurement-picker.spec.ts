import { expect, test } from "@playwright/test";

for (const role of ["GENERAL", "GENERAL_ACCOUNTING", "OWNER_ADMIN"]) {
  test(`${role}: choose batch by client and enforce procurement controls`, async ({ page, context }) => {
    const payload = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
    await context.addCookies([{ name: "access_token", value: `qa.${payload}.qa`, domain: "127.0.0.1", path: "/" }]);
    const permissions = ["confirm_procurement", "floor_packing", ...(role !== "GENERAL" ? ["manage_billing"] : []),
      ...(role === "OWNER_ADMIN" ? ["edit_buffers", "merge_products", "dispatch_handoff_01", "manage_users"] : [])];
    let searched = false;
    await page.route("**/backend/api/v3/**", async route => {
      const url = new URL(route.request().url());
      if (url.pathname.endsWith("/auth/me")) {
        await route.fulfill({ json: { full_name: "Test User", role, permissions, must_change_password: false } });
      } else if (url.pathname.endsWith("/latest-batch")) {
        await route.fulfill({ json: null });
      } else if (url.pathname.endsWith("/procurement/batches")) {
        searched ||= url.searchParams.get("q") === "Sunny";
        await route.fulfill({ json: { items: [{ batch_id: "chosen", batch_reference: "PUR-CHOSEN", customers: ["Sunny Client"],
          display_name: "Sunny Client", files: ["sunny.xlsx"], shipment_date: "2026-10-06", route_code: "BKK", total_skus: 0, status: "PROCUREMENT_CONFIRMED" }], total: 1, offset: 0, limit: 25, has_more: false } });
      } else if (url.pathname.endsWith("/procurement/batches/chosen")) {
        await route.fulfill({ json: { batch_id: "chosen", batch_reference: "PUR-CHOSEN", status: "PROCUREMENT_CONFIRMED", total_skus: 0,
          total_ordered_weight_kg: 0, total_buffer_weight_kg: 0, total_required_weight_kg: 0, items: [], source_files: [{ file_id: "source", file_name: "sunny.xlsx", customer: "Sunny Client", shipment_date: "2026-10-06", route: "BKK" }] } });
      } else await route.fulfill({ json: {} });
    });
    await page.goto("/procurement");
    await page.getByRole("link", { name: "Choose batch", exact: true }).click();
    await page.getByRole("searchbox", { name: "Search records" }).fill("Sunny");
    const picker = page.getByRole("region", { name: "Purchase batches", exact: true });
    await picker.getByRole("link", { name: "Confirm purchases sunny.xlsx", exact: true }).click();
    expect(searched).toBe(true);
    await expect(picker).toHaveCount(0);
    await expect(page.getByText("sunny.xlsx", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Download Purchase PDF", exact: true })).toHaveCount(0);
    const confirm = page.getByRole("button", { name: "Confirm purchases and send to packing", exact: true });
    await expect(confirm).toBeEnabled();
    await expect(page.getByRole("button", { name: "Dispatch Signed Handoff 01" })).toHaveCount(0);
    if (role === "OWNER_ADMIN") {
      await page.getByText("Batch preparation and catalog tools", { exact: true }).click();
      await expect(page.getByRole("button", { name: "Merge duplicate products" })).toBeVisible();
      await expect(page.getByRole("link", { name: "Team & access", exact: true })).toBeVisible();
    } else {
      await expect(page.getByRole("button", { name: "Merge duplicate products" })).toHaveCount(0);
      await expect(page.getByRole("link", { name: "Team & access", exact: true })).toHaveCount(0);
      await expect(page.getByRole("button", { name: "Adjust buffers", exact: true })).toBeDisabled();
    }
    if (role === "GENERAL") await expect(page.getByRole("link", { name: "Billing", exact: true }).filter({ has: page.locator("svg") })).toHaveCount(0);
  });
}

test("switching batches ignores a late response from the previous selection", async ({ page, context }) => {
  const token = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  await context.addCookies([{ name: "access_token", value: `qa.${token}.qa`, domain: "127.0.0.1", path: "/" }]);
  let release!: () => void;
  const firstGate = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/backend/api/v3/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) await route.fulfill({ json: { full_name: "Buyer", role: "GENERAL", permissions: ["confirm_procurement"] } });
    else if (path.endsWith("/procurement/batches")) await route.fulfill({ json: { items: ["A", "B"].map(id => ({ batch_id: id, batch_reference: `PUR-${id}`, display_name: `Client ${id}`, customers: [`Client ${id}`], files: [`${id}.xlsx`], total_skus: 0 })), total: 2, offset: 0, limit: 25, has_more: false } });
    else if (/\/batches\/[AB]$/.test(path)) {
      const id = path.slice(-1);
      if (id === "A") await firstGate;
      await route.fulfill({ json: { batch_id: id, batch_reference: `PUR-${id}`, status: "PURCHASING_CONSOLIDATED", total_skus: 0, total_ordered_weight_kg: 0, total_buffer_weight_kg: 0, total_required_weight_kg: 0, items: [], source_files: [{ file_id: id, file_name: `${id}.xlsx`, customer: `Client ${id}`, shipment_date: "2026-10-06", route: "BKK" }] } });
    } else await route.fulfill({ json: [] });
  });
  await page.goto("/procurement");
  await page.getByRole("link", { name: "Choose batch", exact: true }).click();
  await page.getByRole("link", { name: "View record A.xlsx", exact: true }).click();
  await expect(page).toHaveURL(/batchId=A/);
  await page.getByRole("link", { name: "Choose batch", exact: true }).click();
  await page.getByRole("link", { name: "View record B.xlsx", exact: true }).click();
  await expect(page.getByText("B.xlsx", { exact: true })).toBeVisible();
  const response = page.waitForResponse("**/procurement/batches/A"); release(); await response;

  await expect(page.getByText("B.xlsx", { exact: true })).toBeVisible();
});
