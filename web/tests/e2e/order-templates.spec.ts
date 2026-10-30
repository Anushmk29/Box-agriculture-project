import { expect, test } from "@playwright/test";

test("recognized Excel formats skip setup and keep adjustments under File tools", async ({ page, context }) => {
  const jwt = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  await context.addCookies([{ name: "access_token", value: `qa.${jwt}.qa`, domain: "127.0.0.1", path: "/" }]);
  let formatRequests = 0;
  await page.route("**/backend/api/v3/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.includes("/order-templates/")) formatRequests++;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { full_name: "Staff", role: "GENERAL", must_change_password: false, permissions: ["validate_orders"] } });
    if (path.endsWith("/orders/files/auto-format")) return route.fulfill({ json: {
      raw_file_id: "auto-format", file_name: "Order Hungfat 2026-W32.xlsx", main_customer_id: "tonio",
      total_sub_customers_detected: 4, sub_customer_codes: ["HUNGFAT-101", "HUNGFAT-103", "HUNGFAT-110", "HUNGFAT-116"],
      sheets_detected: [], status: "PARSED", verification_notice: "Review order details",
    } });
    if (path.endsWith("/validate")) return route.fulfill({ json: { unresolved: [], can_split: true, quantity_issue_count: 0,
      quantity_review: { templates: [{ key: "HUNGFAT-MATRIX", status: "MATCHED", confirmed: false, automatic: true, issues: [] }],
        template_issues: [], revision: "a".repeat(64), items: [], issue_count: 0, total: 1, has_more: false } } });
    return route.fulfill({ json: path.endsWith("/customer-register") ? { items: [], total: 0, has_more: false } : [] });
  });
  await page.goto("/orders?fileId=auto-format");
  await expect(page.getByRole("button", { name: "Confirm orders and create purchase batch", exact: true })).toBeEnabled();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Review customer Excel format", exact: true })).toHaveCount(0);
  expect(formatRequests).toBe(0);
  await page.locator(".order-file-reference summary").click();
  await expect(page.getByRole("button", { name: "Adjust Excel format", exact: true })).toBeVisible();
});

test("staff confirm a recurring format once and retain its unit on reopening", async ({ page, context }) => {
  const jwt = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  await context.addCookies([{ name: "access_token", value: `qa.${jwt}.qa`, domain: "127.0.0.1", path: "/" }]);
  let confirmed = false;
  let saved: Record<string, unknown> | undefined;
  const ack = { raw_file_id: "raw-format", file_name: "77H.xlsx", main_customer_id: "nga", total_sub_customers_detected: 1, sub_customer_codes: ["77H"], sheets_detected: [], group_summary: { NGA: 1 }, status: "PARSED", verification_notice: "Review order details" };
  const definition = { key: "77H", sample: "77H.xlsx", main_customer_code: "NGA", client_code: "77H", default_unit: null as string | null, numeric_pack_unit: null, sheets: [{ reference_sheet: "Ark1", anchors: { "2": "Product Name", "4": "Order" }, fields: { code: 1, name: 2, quantity: 4 }, customers: null }] };
  await page.route("**/backend/api/v3/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { full_name: "Staff", role: "GENERAL", must_change_password: false, permissions: ["validate_orders"] } });
    if (path.endsWith("/orders/files/raw-format")) return route.fulfill({ json: ack });
    if (path.endsWith("/validate")) return route.fulfill({ json: { unresolved: [], can_split: confirmed, quantity_issue_count: confirmed ? 0 : 1, quantity_review: confirmed ? undefined : { revision: "format-review", items: [], total: 1, issue_count: 1, unknown_unit_count: 0, has_more: false, template_issues: ["Confirm the customer format before procurement"] } } });
    if (path.endsWith("/order-templates/raw-format/confirm")) {
      saved = route.request().postDataJSON();
      definition.default_unit = String(saved?.default_unit); confirmed = true;
      return route.fulfill({ json: {} });
    }
    if (path.endsWith("/order-templates/raw-format")) return route.fulfill({ json: { revision: "a".repeat(64), formats: [{ id: confirmed ? "profile-2" : "profile-1", version: confirmed ? 2 : 1, confirmed, definition }], detected: [{ key: "77H", status: "MATCHED", confirmed, issues: [] }], issues: confirmed ? [] : ["Confirm the customer format before procurement"] } });
    return route.fulfill({ json: path.endsWith("/customer-register") ? { items: [], total: 0, has_more: false } : [] });
  });
  await page.goto("/orders?fileId=raw-format");
  const confirm = page.getByRole("button", { name: "Confirm orders and create purchase batch", exact: true });
  await expect(confirm).toBeDisabled();
  await page.getByRole("button", { name: "Review customer Excel format", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Customer Excel format", exact: true });
  await expect(dialog).toContainText("77H.xlsx");
  await dialog.getByRole("combobox", { name: "Quantity unit when the order does not print one", exact: true }).selectOption("kg");
  await dialog.getByRole("button", { name: "Confirm format and re-read order", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(saved).toMatchObject({ template_id: "profile-1", expected_version: 1, default_unit: "kg" });
  await expect(confirm).toBeEnabled();
  await expect(page.locator("#order-review").getByRole("button", { name: "Review customer Excel format", exact: true })).toHaveCount(0);
  await page.locator(".order-file-reference summary").click();
  await page.getByRole("button", { name: "Adjust Excel format", exact: true }).click();
  await expect(dialog.getByRole("combobox", { name: "Quantity unit when the order does not print one", exact: true })).toHaveValue("kg");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(async () => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("staff can remove a legacy weight exception and retain the temperature rule after reload", async ({ page, context }) => {
  const jwt = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  await context.addCookies([{ name: "access_token", value: `qa.${jwt}.qa`, domain: "127.0.0.1", path: "/" }]);
  const product = { id: "garlic", t_code: "11-035", name_en: "Garlic", name_th: "", packing_revision: 1, box_material: "CARDBOARD", temperature: "NORMAL", control_status: "COMBINABLE", produce_type: "VEGETABLE", weight_class: "HEAVY", firmness: "HARD", volume_l_per_kg: 2, foam_below_kg: 1 as number | null, volume_estimated: false, ready: true, problems: [], limits: [], source_choices: [], grouping_row: null, identity_choices: [], limit_issues: [] };
  let edit: Record<string, unknown> | undefined;
  await page.route("**/backend/api/v3/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { full_name: "Staff", role: "GENERAL", must_change_password: false, permissions: ["floor_packing"] } });
    if (path.endsWith("/packing-setup/table/bulk")) {
      edit = route.request().postDataJSON().edits[0];
      product.foam_below_kg = (edit?.changes as { foam_below_kg: number | null }).foam_below_kg; product.packing_revision++;
      return route.fulfill({ json: { results: [{ id: product.id, product }] } });
    }
    if (path.endsWith("/packing-setup/table")) return route.fulfill({ json: { items: [product], total: 1, has_more: false, summary: { total: 1, ready: 1, strict: true } } });
    return route.fulfill({ json: [] });
  });
  await page.goto("/packing/setup");
  await expect(page.getByText("Existing weight exception: foam below 1 kg.", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Use temperature rule only", exact: true }).click();
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "All changes saved" })).toBeVisible();
  expect(edit).toMatchObject({ id: "garlic", expected_revision: 1, changes: { foam_below_kg: null } });
  await page.reload();
  await expect(page.getByRole("combobox", { name: "11-035 box and temperature", exact: true })).toHaveValue("NORMAL");
  await expect(page.getByRole("button", { name: "Use temperature rule only", exact: true })).toHaveCount(0);
});
