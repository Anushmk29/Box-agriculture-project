import { expect, test } from "@playwright/test";

test("staff resolve order weights before confirmation and see ten 100g packs per kg", async ({ page, context }) => {
  const jwt = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  await context.addCookies([{ name: "access_token", value: `qa.${jwt}.qa`, domain: "127.0.0.1", path: "/" }]);
  let corrected = false;
  let correction: Record<string, unknown> | undefined;
  const ack = { raw_file_id: "raw-qty", file_name: "YACHI.xlsx", main_customer_id: null, total_sub_customers_detected: 1, sub_customer_codes: ["YACHI"], sheets_detected: [], group_summary: { YACHI: 1 }, status: "PARSED", verification_notice: "Review the source order" };
  const row = { row_id: "0:0", client_code: "YACHI", product_name: "Lemongrass", product_code: "11-001", quantity: 1.05, unit: "kg", pack_size: "100g", net_weight_kg: null, source: { raw_quantity: 1.05, raw_unit: "KG", raw_pack_size: 100 }, computed: null as null | { pack_count: number; net_weight_kg: number }, issue: "10.5 packs is not a whole number" as string | null };
  await page.route("**/backend/api/v3/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { full_name: "Staff", role: "GENERAL", must_change_password: false, permissions: ["validate_orders"] } });
    if (path.endsWith("/orders/files/raw-qty")) return route.fulfill({ json: ack });
    if (path.endsWith("/validate")) return route.fulfill({ json: { unresolved: [], can_split: corrected, quantity_issue_count: corrected ? 0 : 1 } });
    if (path.endsWith("/quantity-review/0:0")) {
      correction = route.request().postDataJSON(); corrected = true; row.quantity = 1; row.computed = { pack_count: 10, net_weight_kg: 1 }; row.issue = null;
      return route.fulfill({ json: { revision: "b".repeat(64), items: [row], total: 1, issue_count: 0, unknown_unit_count: 0, has_more: false } });
    }
    if (path.endsWith("/quantity-review")) return route.fulfill({ json: { revision: "a".repeat(64), items: [row], total: 1, issue_count: 1, unknown_unit_count: 0, has_more: false } });
    return route.fulfill({ json: path.endsWith("/customer-register") ? { items: [], total: 0, has_more: false } : [] });
  });
  await page.goto("/orders?fileId=raw-qty");
  const confirm = page.getByRole("button", { name: "Confirm orders and create purchase batch", exact: true });
  await expect(confirm).toBeDisabled();
  await page.getByRole("button", { name: "Review weights and pack counts", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Review order quantities" });
  await expect(dialog).toContainText("10.5 packs is not a whole number");
  await dialog.getByRole("button", { name: "Correct quantity 0:0" }).click();
  await dialog.getByRole("spinbutton", { name: "Order amount", exact: true }).fill("1");
  await dialog.getByLabel("Reason for correction").fill("Customer confirmed one kilogram");
  await dialog.getByRole("button", { name: "Save quantity correction" }).click();
  await expect(dialog).toContainText("10 packs · 1 kg");
  expect(correction).toMatchObject({ quantity: 1, unit: "kg", pack_size: "100g" });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(async () => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await dialog.getByRole("button", { name: "Close Review order quantities", exact: true }).click();
  await expect(confirm).toBeEnabled();
});

test("quantity review opens from validation, groups similar issues and previews one bulk correction", async ({ page, context }) => {
  const jwt = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  await context.addCookies([{ name: "access_token", value: `qa.${jwt}.qa`, domain: "127.0.0.1", path: "/" }]);
  let quantityReads = 0;
  let saves = 0;
  const requests: Record<string, unknown>[] = [];
  const rows = [1, 2].map((quantity, index) => ({ row_id: `${index}:0`, client_code: `SHOP-${index}`, product_name: "Lemongrass", product_code: "11-001", quantity, unit: "kg", pack_size: "100g+", net_weight_kg: null, source: { sheet: "Orders", row: index + 5 }, computed: null as null | { pack_count: number; net_weight_kg: number }, issue: "Enter the physical pack size" as string | null }));
  const review = () => ({ revision: (saves ? "b" : "a").repeat(64), items: rows.filter(row => row.issue), total: 270, filtered_total: saves ? 0 : 2, issue_count: saves ? 0 : 2, unknown_unit_count: 0, has_more: false });
  await page.route("**/backend/api/v3/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { full_name: "Staff", role: "GENERAL", must_change_password: false, permissions: ["validate_orders"] } });
    if (path.endsWith("/orders/files/bulk-qty")) return route.fulfill({ json: { raw_file_id: "bulk-qty", file_name: "Order.xlsx", total_sub_customers_detected: 2, sub_customer_codes: ["SHOP-0", "SHOP-1"], sheets_detected: [], group_summary: {}, status: "PARSED", verification_notice: "Review the source" } });
    if (path.endsWith("/validate")) return route.fulfill({ json: { unresolved: [], can_split: false, quantity_issue_count: 2, quantity_review: review() } });
    if (path.endsWith("/quantity-review/bulk")) {
      const payload = route.request().postDataJSON(); requests.push(payload);
      const changed = rows.map(row => ({ ...row, pack_size: "100g", issue: null, computed: { pack_count: row.quantity * 10, net_weight_kg: row.quantity } }));
      if (payload.preview_only) return route.fulfill({ json: { revision: "a".repeat(64), items: changed, changed_count: 2 } });
      saves++; rows.forEach((row, index) => Object.assign(row, changed[index]));
      return route.fulfill({ json: { ...review(), changed_count: 2, changed_rows: changed } });
    }
    if (path.endsWith("/quantity-review")) {
      quantityReads++;
      const all = new URL(route.request().url()).searchParams.get("issues_only") === "false";
      return route.fulfill({ json: all ? { ...review(), items: rows, filtered_total: rows.length } : review() });
    }
    return route.fulfill({ json: path.endsWith("/customer-register") ? { items: [], total: 0, has_more: false } : [] });
  });
  await page.goto("/orders?fileId=bulk-qty");
  await page.getByRole("button", { name: "Review weights and pack counts", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Review order quantities" });
  await expect(dialog.getByRole("button", { name: "Issues only", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(dialog).toContainText("2 issues across 270 ordered lines");
  expect(quantityReads).toBe(0);
  await dialog.getByRole("button", { name: "Select 2 similar issues for 0:0", exact: true }).click();
  await dialog.getByLabel("New physical pack size", { exact: true }).fill("100g");
  await dialog.getByLabel("Reason for these corrections", { exact: true }).fill("Customer confirmed 100 gram bags");
  await dialog.getByRole("button", { name: "Preview 2 corrections", exact: true }).click();
  await expect(dialog).toContainText("10 packs · 1 kg");
  await expect(dialog).toContainText("20 packs · 2 kg");
  expect(saves).toBe(0);
  await page.screenshot({ path: "../outputs/quantity-bulk-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: "../outputs/quantity-bulk-mobile.png", fullPage: true });
  await dialog.getByRole("button", { name: "Save 2 corrections", exact: true }).click();
  await expect(dialog).toContainText("Saved 2 corrections");
  await expect(dialog).toContainText("No line issues to correct");
  expect(requests).toHaveLength(2);
  expect(requests[0]).toMatchObject({ row_ids: ["0:0", "1:0"], pack_size: "100g", unit: null, preview_only: true });
  expect(requests[1]).toMatchObject({ preview_only: false, expected_revision: "a".repeat(64) });
  await dialog.getByRole("button", { name: "Close Review order quantities", exact: true }).click();
  await page.getByRole("button", { name: "Review weights and pack counts", exact: true }).click();
  await expect(dialog).toContainText("0 issues across 270 ordered lines");
  expect(quantityReads).toBe(0);
  await dialog.getByRole("button", { name: "All lines", exact: true }).click();
  await expect(dialog.getByRole("button", { name: "Correct quantity 0:0", exact: true })).toBeVisible();
  await expect(dialog).toContainText("20 packs · 2 kg");
  expect(quantityReads).toBe(1);
});
