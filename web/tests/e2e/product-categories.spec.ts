import { expect, test } from "@playwright/test";

test("map ground chili using extended categories and explicit packing settings", async ({ page, context }) => {
  const payload = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  await context.addCookies([{ name: "access_token", value: `qa.${payload}.qa`, domain: "127.0.0.1", path: "/" }]);
  let created: Record<string, unknown> | undefined;
  let mapped = false;
  const missing = { product_code: "53244", name_en: "GROUND CHILI (TON POE BRAND)", pack_size: "250g" };
  await page.route("**/backend/api/v3/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { full_name: "QA", role: "GENERAL", must_change_password: false, user_id: "upload-fixture-user", upload_replay_supported: true, permissions: ["validate_orders"] } });
    if (path.endsWith("/master-data/customer-register")) return route.fulfill({ json: { items: [{ id: "main", client_code: "MAIN", company_name: "Main customer", is_main_customer: true }], total: 1, offset: 0, limit: 25, has_more: false } });
    if (path.endsWith("/orders/upload-matrix")) return route.fulfill({ json: {
      raw_file_id: "category-file", file_name: "category.xlsx", main_customer_id: "main", total_sub_customers_detected: 1,
      sub_customer_codes: ["TEST"], sheets_detected: [], group_summary: {}, sub_customer_details: [],
      unresolved_products: [missing], can_split: false, verification_notice: "Map missing products", status: "UPLOADED",
    } });
    if (path.endsWith("/category-file/validate")) return route.fulfill({ json: { unresolved: mapped ? [] : [missing], can_split: mapped } });
    if (path.endsWith("/master-data/products")) {
      if (route.request().method() === "POST") {
        created = route.request().postDataJSON();
        return route.fulfill({ status: 201, json: { id: "chili", ...created } });
      }
      return route.fulfill({ json: [] });
    }
    if (path.endsWith("/master-data/sku-mappings")) { mapped = true; return route.fulfill({ json: {} }); }
    return route.fulfill({ json: path.endsWith("/operations/summary") ? {} : [] });
  });
  await page.goto("/orders");
  await page.getByRole("combobox", { name: /Billing customer/ }).selectOption("main");
  await page.locator('input[type="file"]').setInputFiles({ name: "category.xlsx", mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer: Buffer.from("test workbook") });
  await page.getByRole("button", { name: "Upload and check order" }).click();
  await page.getByRole("button", { name: "Match product", exact: true }).click();
  await page.getByRole("button", { name: /New catalog product/ }).click();
  const category = page.getByLabel("Category", { exact: true });
  await expect(category).toHaveValue("");
  for (const value of ["VEGETABLE", "SPICE", "PACKAGED_FOOD", "PERSONAL_CARE", "OTHER"]) {
    await expect(category.locator(`option[value="${value}"]`)).toHaveCount(1);
  }
  const material = page.getByLabel("Box Material", { exact: true });
  const temperature = page.getByLabel("Temp Zone", { exact: true });
  await expect(material).toHaveValue("");
  await expect(temperature).toHaveValue("");
  await page.getByRole("button", { name: "Create product and match", exact: true }).click();
  expect(created).toBeUndefined();
  await category.selectOption("SPICE");
  await material.selectOption("CARDBOARD");
  await temperature.selectOption("NORMAL");
  await page.getByRole("button", { name: "Create product and match", exact: true }).click();
  await expect(page.getByText(/Successfully created master SKU/)).toBeVisible();
  expect(created).toMatchObject({ t_code: "53244", produce_type: "SPICE", box_material: "CARDBOARD", temperature: "NORMAL", default_pack_sizes: ["250g"] });
  expect(mapped).toBe(true);
});
