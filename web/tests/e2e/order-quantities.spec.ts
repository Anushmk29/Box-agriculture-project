import { expect, test } from "@playwright/test";

for (const width of [1440, 390]) {
  test(`keep all issues across pages in one click at ${width}px`, async ({ page, context }) => {
    const jwt = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
    await context.addCookies([{ name: "access_token", value: `qa.${jwt}.qa`, domain: "127.0.0.1", path: "/" }]);
    await page.setViewportSize({ width, height: 900 });
    let reviewed = false;
    const decisions: Record<string, unknown>[] = [];
    const rows = Array.from({ length: 61 }, (_, index) => ({ row_id: `${index}:0`, client_code: `SHOP-${index}`, product_name: "Green papaya", product_code: "T003", quantity: index + 1, unit: "kg", pack_size: "600g+", net_weight_kg: null,
      source: { raw_pack_size: "600g+" }, computed: null as null | { pack_count: number | null; net_weight_kg: number }, issue: "Enter the physical pack size" as string | null,
      can_keep_as_ordered: true, reviewed_unchanged: false }));
    const review = (offset = 0, all = false) => {
      const filtered = all ? rows : rows.filter(row => row.issue);
      return { revision: (reviewed ? "b" : "a").repeat(64), items: filtered.slice(offset, offset + 50), total: 270, issue_count: reviewed ? 0 : 61, keepable_issue_count: reviewed ? 0 : 61, reviewed_count: reviewed ? 61 : 0, unknown_unit_count: 0, has_more: offset + 50 < filtered.length };
    };
    await page.route("**/backend/api/v3/**", async route => {
      const url = new URL(route.request().url());
      if (url.pathname.endsWith("/auth/me")) return route.fulfill({ json: { full_name: "Staff", role: "GENERAL", must_change_password: false, permissions: ["validate_orders"] } });
      if (url.pathname.endsWith("/orders/files/keep-all")) return route.fulfill({ json: { raw_file_id: "keep-all", file_name: "Order.xlsx", total_sub_customers_detected: 61, sub_customer_codes: rows.map(row => row.client_code), sheets_detected: [], status: "PARSED", verification_notice: "Review the source" } });
      if (url.pathname.endsWith("/validate")) return route.fulfill({ json: { unresolved: [], can_split: reviewed, quantity_issue_count: reviewed ? 0 : 61, quantity_review: review() } });
      if (url.pathname.endsWith("/quantity-review/keep")) {
        decisions.push(route.request().postDataJSON()); reviewed = true;
        rows.forEach(row => { row.computed = { pack_count: null, net_weight_kg: row.quantity }; row.issue = null; row.reviewed_unchanged = true; });
        return route.fulfill({ json: { ...review(), changed_count: 61 } });
      }
      if (url.pathname.endsWith("/quantity-review")) return route.fulfill({ json: review(Number(url.searchParams.get("offset") || 0), url.searchParams.get("issues_only") === "false") });
      return route.fulfill({ json: url.pathname.endsWith("/customer-register") ? { items: [], total: 0, has_more: false } : [] });
    });
    await page.goto("/orders?fileId=keep-all");
    const confirm = page.getByRole("button", { name: "Confirm orders and create purchase batch", exact: true });
    await expect(confirm).toBeDisabled();
    await page.getByRole("button", { name: "Review weights and pack counts", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Review order quantities" });
    await expect(dialog.getByRole("button", { name: "Keep all as ordered", exact: true })).toBeVisible();
    await expect(dialog.getByRole("table")).toBeVisible();
    await expect(dialog.getByText("View or edit order lines", { exact: true })).toHaveCount(0);
    await expect(dialog.getByRole("checkbox")).toHaveCount(0);
    await expect(dialog.getByRole("group", { name: "Review decision" })).toHaveCount(0);
    await page.screenshot({ path: `../outputs/quantity-review-simple-${width}.png`, animations: "disabled" });
    await dialog.getByRole("button", { name: "Next quantities", exact: true }).click();
    await expect(dialog.getByRole("button", { name: "Correct quantity 60:0", exact: true })).toBeAttached();
    const keepAll = dialog.getByRole("button", { name: "Keep all as ordered", exact: true });
    await expect(keepAll).toBeEnabled();
    await expect(dialog.getByRole("checkbox", { checked: true })).toHaveCount(0);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `../outputs/quantity-review-all-${width}.png` });
    await keepAll.click();
    await expect(dialog).toBeHidden();
    expect(decisions).toEqual([{ expected_revision: "a".repeat(64), all_eligible: true }]);
    await expect(confirm).toBeEnabled();
    await page.getByRole("button", { name: "Review weights and pack counts", exact: true }).click();
    await expect(dialog).toContainText("61 reviewed and kept as ordered");
    await dialog.getByRole("button", { name: "All lines", exact: true }).click();
    await expect(dialog.getByText("600g+", { exact: true })).toHaveCount(50);
    await expect(dialog.getByText("Reviewed—kept as ordered", { exact: true })).toHaveCount(50);
    await expect(dialog).toContainText("Pack count pending");
    await page.keyboard.press("Escape");
    await expect(confirm).toBeEnabled();
    await page.reload();
    await expect(confirm).toBeEnabled();
  });
}

for (const width of [1440, 390]) {
  test(`keep fifteen kg in eighty gram packs with one button at ${width}px`, async ({ page, context }) => {
    const jwt = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
    await context.addCookies([{ name: "access_token", value: `qa.${jwt}.qa`, domain: "127.0.0.1", path: "/" }]);
    await page.setViewportSize({ width, height: 900 });
    let reviewed = false;
    const decisions: Record<string, unknown>[] = [];
    const rows = [{ row_id: "0:0", client_code: "Saigon City", product_name: "Ngo ri / Mui thai", product_code: "T003", quantity: 15, unit: "kg", pack_size: "80g", net_weight_kg: null,
      source: { raw_pack_size: "80g" }, computed: null as null | { pack_count: number | null; net_weight_kg: number }, issue: "187.5 packs is not a whole number" as string | null,
      can_keep_as_ordered: true, reviewed_unchanged: false }];
    const review = (all = false) => ({ revision: (reviewed ? "b" : "a").repeat(64), items: all ? rows : rows.filter(row => row.issue), total: 270, issue_count: reviewed ? 0 : 1, keepable_issue_count: reviewed ? 0 : 1, reviewed_count: reviewed ? 19 : 18, unknown_unit_count: 0, has_more: false });
    await page.route("**/backend/api/v3/**", async route => {
      const url = new URL(route.request().url());
      if (url.pathname.endsWith("/auth/me")) return route.fulfill({ json: { full_name: "Staff", role: "GENERAL", must_change_password: false, permissions: ["validate_orders"] } });
      if (url.pathname.endsWith("/orders/files/keep-qty")) return route.fulfill({ json: { raw_file_id: "keep-qty", file_name: "Order.xlsx", total_sub_customers_detected: 2, sub_customer_codes: ["SHOP-0", "SHOP-1"], sheets_detected: [], status: "PARSED", verification_notice: "Review the source" } });
      if (url.pathname.endsWith("/validate")) return route.fulfill({ json: { unresolved: [], can_split: reviewed, quantity_issue_count: reviewed ? 0 : 1, quantity_review: review() } });
      if (url.pathname.endsWith("/quantity-review/keep")) {
        decisions.push(route.request().postDataJSON()); reviewed = true;
        rows.forEach(row => { row.computed = { pack_count: null, net_weight_kg: row.quantity }; row.issue = null; row.reviewed_unchanged = true; });
        return route.fulfill({ json: { ...review(), changed_rows: rows, changed_count: 1 } });
      }
      if (url.pathname.endsWith("/quantity-review")) return route.fulfill({ json: review(url.searchParams.get("issues_only") === "false") });
      return route.fulfill({ json: url.pathname.endsWith("/customer-register") ? { items: [], total: 0, has_more: false } : [] });
    });
    await page.goto("/orders?fileId=keep-qty");
    const confirm = page.getByRole("button", { name: "Confirm orders and create purchase batch", exact: true });
    await expect(confirm).toBeDisabled();
    await page.getByRole("button", { name: "Review weights and pack counts", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Review order quantities" });
    await expect(dialog.getByRole("checkbox")).toHaveCount(0);
    await expect(dialog.getByLabel("Reason for these corrections", { exact: true })).toHaveCount(0);
    await page.screenshot({ path: `../outputs/quantity-keep-decision-${width}.png` });
    await dialog.getByRole("button", { name: "Keep all as ordered", exact: true }).click();
    await expect(dialog).toBeHidden();
    await expect(confirm).toBeEnabled();
    expect(decisions).toEqual([{ expected_revision: "a".repeat(64), all_eligible: true }]);
    await page.getByRole("button", { name: "Review weights and pack counts", exact: true }).click();
    await dialog.getByRole("button", { name: "All lines", exact: true }).click();
    await expect(dialog.getByText("80g", { exact: true })).toHaveCount(1);
    await expect(dialog.getByText("15 kg", { exact: true })).toHaveCount(1);
    await expect(dialog.getByText("Reviewed—kept as ordered", { exact: true })).toHaveCount(1);
    await expect(dialog).toContainText("Pack count pending");
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `../outputs/quantity-reviewed-unchanged-${width}.png` });
    await page.keyboard.press("Escape");
    await expect(confirm).toBeEnabled();
    await page.reload();
    await expect(confirm).toBeEnabled();
    await page.getByRole("button", { name: "Review weights and pack counts", exact: true }).click();
    await expect(dialog).toContainText("19 reviewed and kept as ordered");
  });
}

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

test("quantity review opens from validation, selects shown issues and previews one bulk correction", async ({ page, context }) => {
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
  await expect(dialog).toContainText("270 order lines");
  expect(quantityReads).toBe(0);
  await dialog.getByText("Edit several lines", { exact: true }).click();
  await dialog.getByRole("checkbox", { name: "Select all shown issues", exact: true }).check();
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
  await expect(dialog).toContainText("Quantities reviewed");
  expect(quantityReads).toBe(0);
  await dialog.getByRole("button", { name: "All lines", exact: true }).click();
  await expect(dialog.getByRole("button", { name: "Correct quantity 0:0", exact: true })).toBeVisible();
  await expect(dialog).toContainText("20 packs · 2 kg");
  expect(quantityReads).toBe(1);
});
