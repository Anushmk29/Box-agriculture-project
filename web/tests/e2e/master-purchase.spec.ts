import { expect, test } from "@playwright/test";

for (const width of [1440, 390]) {
test(`Master Purchase tab coordinates selection, customer preview and PDF at ${width}px`, async ({ page, context }) => {
  await page.setViewportSize({ width, height: 900 });
  const token = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  await context.addCookies([{ name: "access_token", value: `qa.${token}.qa`, domain: "127.0.0.1", path: "/" }]);
  const submitted: string[][] = [];
  const masterReads: string[] = [];
  let failDownload = true;
  const rows = ["TETE", "P-PUI"].map((customer, index) => ({
    batch_id: `batch-${index}`, batch_reference: `PUR-${index}`, files: [`${customer} order.xlsx`],
    display_name: customer, customers: [customer], shipment_date: "2026-11-04", route_code: customer,
    status: index ? "HANDOFF_01_SENT" : "PURCHASING_CONSOLIDATED", total_skus: 1, outstanding_items: 1,
  }));
  const report = { generated_at: "2026-11-04T10:00:00Z", batch_ids: ["batch-0", "batch-1"], total_target_kg: 16,
    sections: ["TETE", "P-PUI"].map((customer, index) => ({
      customer_id: `customer-${index}`, customer_code: customer, customer_name: customer,
      total_target_kg: index ? 6 : 10, lines: [{ batch_reference: `PUR-${index}`, status: "PURCHASING_CONSOLIDATED",
        product_code: "T001", product_name: "Tomatoes", pack_size: "1kg", source_files: [`${customer} order.xlsx`],
        shipment_dates: ["2026-11-04"], ordered_packs: index ? 6 : 10, pack_count_pending: false,
        ordered_kg: index ? 6 : 10, buffer_kg: 0, target_packs: index ? 6 : 10,
        target_kg: index ? 6 : 10, actual_packs: null, actual_kg: null, notes: "" }],
    })),
  };
  await page.route("**/backend/api/v3/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { full_name: "Buyer", role: "GENERAL", permissions: ["confirm_procurement"] } });
    if (path.endsWith("/procurement/batches")) {
      const params = new URL(route.request().url()).searchParams;
      if (new URL(route.request().frame().url()).pathname === "/procurement/master-purchase") masterReads.push(params.get("view") || "");
      const items = params.get("view") === "pending" ? rows.slice(0, 1) : rows;
      return route.fulfill({ json: { items, total: items.length, offset: 0, limit: Number(params.get("limit") || 25), has_more: false } });
    }
    if (path.endsWith("/master-purchase/preview")) {
      submitted.push(route.request().postDataJSON().batch_ids);
      return route.fulfill({ json: report });
    }
    if (path.endsWith("/master-purchase/pdf")) {
      submitted.push(route.request().postDataJSON().batch_ids);
      if (failDownload) { failDownload = false; return route.fulfill({ status: 409, json: { detail: "A selected purchase changed. Review and try again." } }); }
      return route.fulfill({ contentType: "application/pdf", body: "%PDF-1.7\nTest PDF" });
    }
    if (route.request().method() !== "GET") throw new Error(`Unexpected write: ${path}`);
    return route.fulfill({ json: [] });
  });
  await page.goto("/procurement/batches?raw_file_id=source-tete&view=all");
  await expect(page.getByRole("button", { name: "Create Master Purchase List", exact: true })).toHaveCount(0);
  await expect(page.getByRole("checkbox")).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Combine order files", exact: true })).toHaveCount(0);
  const tabs = page.getByRole("navigation", { name: "Purchasing sections", exact: true });
  await expect(tabs.getByRole("link", { name: "Purchases", exact: true })).toHaveAttribute("aria-current", "page");
  await tabs.getByRole("link", { name: "Master Purchase", exact: true }).click();
  await expect(page).toHaveURL(/\/procurement\/master-purchase$/);
  await expect(page.getByRole("heading", { name: "Master Purchase", exact: true })).toBeVisible();
  await expect(tabs.getByRole("link", { name: "Master Purchase", exact: true })).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("button", { name: "All", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("button", { name: "Create Master Purchase List", exact: true })).toBeDisabled();
  await page.getByRole("checkbox", { name: "Select TETE order.xlsx", exact: true }).check();
  await page.getByRole("checkbox", { name: "Select P-PUI order.xlsx", exact: true }).check();
  expect(masterReads[0]).toBe("all");
  await expect(page.getByRole("region", { name: "Next step guidance", exact: true })).toContainText("Preview the Master Purchase List");
  await page.evaluate(() => { (document.activeElement as HTMLElement)?.blur(); window.scrollTo(0, 0); });
  await page.screenshot({ path: `../outputs/master-purchase-list-${width}.png`, fullPage: true });
  await page.getByRole("button", { name: "Create Master Purchase List", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Master Purchase List", exact: true });
  await expect(dialog.getByRole("region", { name: "TETE", exact: true })).toContainText("10 kg");
  await expect(dialog.getByRole("region", { name: "P-PUI", exact: true })).toContainText("6 kg");
  await expect(dialog.getByText("Tomatoes", { exact: true })).toHaveCount(2);
  await dialog.getByRole("button", { name: "Download Master Purchase PDF", exact: true }).click();
  await expect(dialog).toContainText("A selected purchase changed");
  const downloaded = page.waitForEvent("download");
  await dialog.getByRole("button", { name: "Download Master Purchase PDF", exact: true }).click();
  expect((await downloaded).suggestedFilename()).toBe("master-purchase-list.pdf");
  expect(submitted).toEqual([["batch-0", "batch-1"], ["batch-0", "batch-1"], ["batch-0", "batch-1"]]);
  await expect(dialog.getByRole("button", { name: "Download Master Purchase PDF", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(await dialog.locator(".task-dialog-body").evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await page.screenshot({ path: `../outputs/master-purchase-preview-${width}.png`, fullPage: true });
  await dialog.getByRole("button", { name: "Close Master Purchase List", exact: true }).click();
  if (width === 390) await page.getByRole("button", { name: "Menu", exact: true }).click();
  const navigation = page.getByRole("navigation", { name: "Primary navigation", exact: true }).filter({ visible: true });
  await expect(navigation.getByRole("link", { name: "Master Purchase", exact: true })).toHaveAttribute("aria-current", "page");
  await navigation.getByRole("link", { name: "Master Purchase", exact: true }).click();
  if (width === 390) await expect(page.getByRole("dialog", { name: "Workspaces", exact: true })).toHaveCount(0);
  if (width === 1440) {
    const response = await page.goto("/procurement/combine");
    expect(response?.status()).toBe(404);
  }
});
}
