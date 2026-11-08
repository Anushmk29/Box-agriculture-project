import { expect, test, type BrowserContext, type Page } from "@playwright/test";

async function setup(page: Page, context: BrowserContext) {
  const token = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  await context.addCookies([{ name: "access_token", value: `qa.${token}.qa`, domain: "127.0.0.1", path: "/" }]);
  const writes: unknown[] = [];
  const row = { batch_id: "language-batch", files: ["Order  YACHI ลูกค้า.xlsx"], display_name: "Today", customers: ["Today"], shipment_date: "2026-11-06", route_code: "TETE", status: "PURCHASING_CONSOLIDATED", total_skus: 1, outstanding_items: 1 };
  await page.route("**/backend/api/v3/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() !== "GET") writes.push(route.request().postDataJSON());
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { full_name: "Today", role: "OWNER_ADMIN", must_change_password: false, permissions: ["confirm_procurement", "floor_packing", "validate_orders", "manage_billing", "manage_users"] } });
    if (path.endsWith("/batches/language-batch")) return route.fulfill({ json: { ...row, total_ordered_weight_kg: 10, total_buffer_weight_kg: 1, total_required_weight_kg: 11, items: [{ item_id: "item", product_id: "product", product_code: "EL-047", product_name: "Herbs", produce_type: "HERB", pack_size: "1kg", ordered_pack_count: 10, ordered_weight_kg: 10, buffer_percentage: 10, buffer_pack_count: 1, buffer_weight_kg: 1, total_purchase_target_packs: 11, total_purchase_target_weight_kg: 11, required_packs: 11, required_weight_kg: 11 }] } });
    if (path.endsWith("/master-purchase/preview")) return route.fulfill({ json: { batch_ids: [row.batch_id], total_target_kg: 11, sections: [{ customer_id: "customer", customer_name: "Today", customer_code: "Today", total_target_kg: 11, lines: [{ batch_reference: "PUR-internal", status: row.status, product_code: "EL-047", product_name: "Herbs", pack_size: "1kg", source_files: row.files, shipment_dates: [row.shipment_date], ordered_packs: 10, ordered_kg: 10, buffer_kg: 1, target_packs: 11, target_kg: 11, actual_packs: null, actual_kg: null, notes: "Customer note" }] }] } });
    if (path.endsWith("/procurement/batches")) return route.fulfill({ json: { items: [row], total: 1, offset: 0, limit: 25, has_more: false } });
    if (path.endsWith("/customer-register")) return route.fulfill({ json: { items: [{ id: "customer", client_code: "YACHI", company_name: "Today" }], total: 1, has_more: false } });
    if (path.endsWith("/master-data/order-routes")) return route.fulfill({ json: ["TETE"] });
    if (path.endsWith("/language-batch/confirm")) return route.fulfill({ json: { status: "HANDOFF_01_SENT" } });
    if (path.endsWith("/operations/summary")) return route.fulfill({ json: {} });
    if (path.endsWith("/packing-setup/boxes") || path.endsWith("/admin/users/access-levels")) return route.fulfill({ json: [] });
    if (path.endsWith("/packing-setup/table")) return route.fulfill({ json: { items: [], total: 0, has_more: false, summary: { total: 0, ready: 0, strict: false } } });
    return route.fulfill({ json: { items: [], total: 0, offset: 0, limit: 25, has_more: false } });
  });
  return writes;
}

const switcher = (page: Page) => page.getByRole("group", { name: "Interface language / ภาษาที่แสดง", exact: true }).filter({ visible: true });

for (const width of [1440, 390]) {
  test(`Thai switch keeps Master Purchase selection and original record names at ${width}px`, async ({ page, context }) => {
    await page.setViewportSize({ width, height: 900 });
    const writes = await setup(page, context);
    await page.goto("/procurement/master-purchase");
    await page.getByRole("checkbox", { name: "Select Order  YACHI ลูกค้า.xlsx", exact: true }).check();
    await switcher(page).getByRole("button", { name: "ภาษาไทย", exact: true }).click();
    await expect(page.locator("html")).toHaveAttribute("lang", "th");
    await expect(switcher(page).getByRole("button", { name: "ภาษาไทย" })).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByRole("heading", { name: "จัดซื้อรวม", exact: true })).toBeVisible();
    await expect(page.getByRole("checkbox", { name: "เลือก Order  YACHI ลูกค้า.xlsx", exact: true })).toBeChecked();
    await expect(page.locator(".chip").getByText("รอจัดซื้อ", { exact: true })).toBeVisible();
    expect(writes).toEqual([]);
    await page.getByRole("button", { name: "สร้างรายการจัดซื้อรวม", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "รายการจัดซื้อรวม", exact: true });
    await expect(dialog.getByRole("heading", { name: "Today", exact: true })).toBeVisible();
    await expect(dialog.getByText("Herbs", { exact: true })).toBeVisible();
    await expect(dialog.getByText("Order  YACHI ลูกค้า.xlsx", { exact: true })).toBeVisible();
    await expect(dialog.getByRole("button", { name: "ดาวน์โหลด PDF จัดซื้อรวม", exact: true })).toBeVisible();
    expect(writes).toEqual([{ batch_ids: ["language-batch"] }]);
    await page.screenshot({ path: `../outputs/language-thai-master-${width}.png`, fullPage: true });
    await dialog.getByRole("button", { name: "ปิด รายการจัดซื้อรวม", exact: true }).click();
    const tabs = page.getByRole("navigation", { name: "เมนูจัดซื้อ", exact: true });
    await tabs.getByRole("link", { name: "รายการจัดซื้อ", exact: true }).click();
    await expect(page.getByRole("heading", { name: "ชุดจัดซื้อ", exact: true })).toBeVisible();
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("lang", "th");
    await expect(page.getByRole("heading", { name: "ชุดจัดซื้อ", exact: true })).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await switcher(page).getByRole("button", { name: "English", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Purchase batches", exact: true })).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
  });
}

test("changing language preserves login fields and does not submit the form", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 850 });
  let submissions = 0;
  await page.route("**/backend/api/v3/auth/login", route => { submissions++; return route.fulfill({ status: 401, json: { detail: "Unable to sign in" } }); });
  await page.goto("/login");
  await page.getByLabel("Email", { exact: true }).fill("buyer@example.test");
  await page.getByLabel("Password", { exact: true }).fill("draft-password-123");
  await switcher(page).getByRole("button", { name: "ภาษาไทย" }).click();
  await expect(page.getByRole("heading", { name: "เข้าสู่ระบบ", exact: true })).toBeVisible();
  await expect(page.getByLabel("อีเมล", { exact: true })).toHaveValue("buyer@example.test");
  await expect(page.getByLabel("รหัสผ่าน", { exact: true })).toHaveValue("draft-password-123");
  expect(submissions).toBe(0);
  await page.getByRole("button", { name: "เข้าสู่ระบบ", exact: true }).click();
  await expect(page.locator(".message.error[role=alert]")).toContainText("เข้าสู่ระบบไม่สำเร็จ");
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: "../outputs/language-thai-login-320.png", fullPage: true });
});

test("changing language preserves unsaved purchasing packs and kilograms", async ({ page, context }) => {
  const writes = await setup(page, context);
  await page.goto("/procurement?batchId=language-batch");
  await page.getByLabel("Actual packs for EL-047", { exact: true }).fill("12");
  await page.getByLabel("Actual kilograms for EL-047", { exact: true }).fill("12.5");
  await switcher(page).getByRole("button", { name: "ภาษาไทย" }).click();
  await expect(page.getByLabel("จำนวนแพ็คจริงสำหรับ EL-047", { exact: true })).toHaveValue("12");
  await expect(page.getByLabel("กิโลกรัมจริงสำหรับ EL-047", { exact: true })).toHaveValue("12.5");
  await expect(page.getByText("Herbs", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "ยืนยันการจัดซื้อและส่งไปแพ็ค", exact: true })).toBeVisible();
  expect(writes).toEqual([]);
  await switcher(page).getByRole("button", { name: "English" }).click();
  await expect(page.getByLabel("Actual packs for EL-047", { exact: true })).toHaveValue("12");
  await switcher(page).getByRole("button", { name: "ภาษาไทย" }).click();
  await page.getByRole("button", { name: "ยืนยันการจัดซื้อและส่งไปแพ็ค", exact: true }).click();
  expect(writes).toEqual([{ items: [{ item_id: "item", purchased_pack_count: 12, purchased_weight_kg: 12.5 }] }]);
  await expect(page.getByRole("heading", { name: "ถัดไป: แพ็คสินค้า", exact: true })).toBeVisible();
});

test("changing language preserves the selected upload and customer", async ({ page, context }) => {
  const writes = await setup(page, context);
  await page.goto("/orders");
  await page.locator("input[type=file]").setInputFiles({ name: "Order YACHI.xlsx", mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer: Buffer.from("mock") });
  await page.getByRole("combobox", { name: "Billing customer", exact: true }).selectOption("customer");
  await switcher(page).getByRole("button", { name: "ภาษาไทย" }).click();
  await expect(page.getByRole("heading", { name: "คำสั่งซื้อ", exact: true })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "ลูกค้าสำหรับออกบิล", exact: true })).toHaveValue("customer");
  expect(await page.locator("input[type=file]").evaluate((e: HTMLInputElement) => e.files?.[0]?.name)).toBe("Order YACHI.xlsx");
  expect(writes).toEqual([]);
});

test("Thai browser preference works when storage is unavailable", async ({ browser }) => {
  const context = await browser.newContext({ locale: "th-TH" });
  const page = await context.newPage();
  await page.addInitScript(() => {
    Storage.prototype.getItem = () => { throw new Error("Storage disabled"); };
    Storage.prototype.setItem = () => { throw new Error("Storage disabled"); };
  });
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "เข้าสู่ระบบ", exact: true })).toBeVisible();
  await switcher(page).getByRole("button", { name: "English" }).click();
  await expect(page.getByRole("heading", { name: "Sign in", exact: true })).toBeVisible();
  await context.close();
});

test("language choice synchronizes between open tabs", async ({ page, context }) => {
  await page.goto("/login");
  const second = await context.newPage();
  await second.goto("/login");
  await switcher(page).getByRole("button", { name: "ภาษาไทย" }).click();
  await expect(second.getByRole("heading", { name: "เข้าสู่ระบบ", exact: true })).toBeVisible();
  await second.close();
});

test("Thai stays selected across the main operational screens", async ({ page, context }) => {
  await setup(page, context);
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/procurement/master-purchase");
  await switcher(page).getByRole("button", { name: "ภาษาไทย" }).click();
  for (const [path, heading] of [["/", "วันนี้"], ["/orders/files", "ไฟล์คำสั่งซื้อ"], ["/packing/orders", "คำสั่งซื้อสำหรับแพ็ค"], ["/packing/setup", "สินค้าและกล่อง"], ["/billing", "การออกใบแจ้งหนี้"], ["/scan", "สแกนสติกเกอร์กล่อง"], ["/users", "ทีมและสิทธิ์เข้าถึง"]]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { name: heading, exact: true })).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("lang", "th");
  }
  expect(errors).toEqual([]);
});
