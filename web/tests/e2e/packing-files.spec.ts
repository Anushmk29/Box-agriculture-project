import { expect, test, type Page } from "@playwright/test";

async function setup(page: Page) {
  const jwt = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  await page.context().addCookies([{ name: "access_token", value: `qa.${jwt}.qa`, domain: "127.0.0.1", path: "/" }]);
  const files = [
    { raw_file_id: "group-file", file_name: "Tonio group shipment.xlsx", customer: "Tonio", order_count: 31,
      pending_count: 31, completed_count: 0, subcustomers: ["CT-01", "CT-02", "CT-03", "CT-04"], shipment_date: "2026-11-02", route: "CT", created_at: "2026-11-02T08:30:00Z" },
    { raw_file_id: "single-file", file_name: "P-PUI shipment.xlsx", customer: "P-PUI", order_count: 1,
      pending_count: 1, completed_count: 0, subcustomers: ["P-PUI"], shipment_date: "2026-11-02", route: "TETE", created_at: "2026-11-02T09:15:00Z" },
  ];
  const orders = files.flatMap(file => Array.from({ length: file.order_count }, (_, i) => ({
    order_id: `${file.raw_file_id}-${i}`, raw_file_id: file.raw_file_id, file_name: file.file_name,
    customer_code: file.raw_file_id === "single-file" ? "P-PUI" : `CT-${String(i + 1).padStart(2, "0")}`,
    customer_name: file.raw_file_id === "single-file" ? "P-PUI" : `CT-${String(i + 1).padStart(2, "0")}`,
    order_ref_id: `ORD-${file.raw_file_id}-${i}`, shipment_date: file.shipment_date, route: file.route,
    status: "HANDOFF_01_SENT", boxes: 0, outstanding: 0,
  })));
  const requests: URL[] = [];
  await page.route("**/backend/api/v3/**", route => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { full_name: "Packing staff", role: "GENERAL", must_change_password: false, permissions: ["floor_packing"] } });
    if (path.endsWith("/packing/files") || path.endsWith("/packing/orders")) {
      requests.push(url);
      const source = path.endsWith("/packing/files") ? files : orders;
      let rows = source.filter(row => !url.searchParams.get("raw_file_id") || row.raw_file_id === url.searchParams.get("raw_file_id"));
      if (url.searchParams.get("view") === "completed") rows = [];
      const offset = Number(url.searchParams.get("offset") || 0), limit = Number(url.searchParams.get("limit") || 25);
      return route.fulfill({ json: { items: rows.slice(offset, offset + limit), total: rows.length, offset, limit, has_more: offset + limit < rows.length } });
    }
    if (path.endsWith("/packing-list")) {
      const order = orders.find(row => path.includes(`/${row.order_id}/`))!;
      return route.fulfill({ json: { client_order_id: order.order_id, raw_file_id: order.raw_file_id,
        file_name: order.file_name, order_ref_id: order.order_ref_id, status: order.status,
        packing_revision: 1, packing_approval_status: "NONE", packing_review_note: null,
        sub_customer_code: order.customer_code, route_code: order.route, shipment_date: order.shipment_date, total_boxes_count: 0, boxes: [] } });
    }
    return route.fulfill({ json: path.endsWith("/operations/summary") ? {} : [] });
  });
  return requests;
}

for (const width of [1440, 390]) {
  test(`packing groups uploads and retains file context through suborders at ${width}px`, async ({ page }) => {
    const requests = await setup(page);
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/packing/orders?batch_id=buyer-batch&view=pending");
    await expect(page.getByRole("link", { name: "View suborders Tonio group shipment.xlsx", exact: true })).toBeVisible();
    await expect(page.getByText("31 suborders", { exact: true })).toBeVisible();
    await expect(page.getByText("CT-01, CT-02, CT-03, CT-04 +27 more", { exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: /Start packing/ })).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `../outputs/packing-files-${width}.png`, fullPage: true });
    await page.getByRole("link", { name: "View suborders Tonio group shipment.xlsx", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Suborders to pack", exact: true })).toBeVisible();
    await expect(page.locator(".register-scope")).toContainText("Tonio group shipment.xlsx");
    await expect(page.locator(".register-scope")).not.toContainText("group-file");
    await expect(page.getByText("1–25 of 31 records", { exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "Start packing P-PUI", exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Next", exact: true }).click();
    await expect(page.getByText("26–31 of 31 records", { exact: true })).toBeVisible();
    await page.getByRole("link", { name: "Start packing CT-26", exact: true }).click();
    await expect(page.locator(".packing-order-overview")).toContainText("Order file: Tonio group shipment.xlsx");
    await expect(page.locator(".packing-customer h2")).toHaveText("CT-26");
    await page.getByRole("link", { name: "Packing orders", exact: true }).click();
    await expect(page.getByText("26–31 of 31 records", { exact: true })).toBeVisible();
    await expect(page).toHaveURL(/raw_file_id=group-file/);
    await expect(page).toHaveURL(/batch_id=buyer-batch/);
    expect(requests.some(url => url.pathname.endsWith("/packing/orders") && url.searchParams.get("raw_file_id") === "group-file" && url.searchParams.get("batch_id") === "buyer-batch")).toBe(true);
    await page.getByRole("link", { name: "Back to order files", exact: true }).click();
    await page.getByRole("link", { name: "View suborders P-PUI shipment.xlsx", exact: true }).click();
    await expect(page.getByText("1–1 of 1 records", { exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "Start packing P-PUI", exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: /Start packing CT-/ })).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `../outputs/packing-file-suborders-${width}.png`, fullPage: true });
  });
}
