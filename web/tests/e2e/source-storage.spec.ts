import { expect, test } from "@playwright/test";

test("a lost upload response can be recovered after refresh without uploading again", async ({ page, context }) => {
  const token = Buffer.from(JSON.stringify({ exp: Date.now() / 1000 + 3600 })).toString("base64url");
  await context.addCookies([{ name: "access_token", value: `qa.${token}.qa`, domain: "127.0.0.1", path: "/" }]);
  let submissions = 0;
  let key = "";
  const ack = { raw_file_id: "stored-file", file_name: "orders.xlsx", main_customer_id: "main", status: "UPLOADED",
    total_sub_customers_detected: 1, sub_customer_codes: ["CT-A"], sheets_detected: [], group_summary: { CT: 1 }, verification_notice: "Review the file." };
  await page.route("**/backend/api/v3/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { user_id: "upload-staff", full_name: "Upload Staff", role: "GENERAL",
      permissions: ["validate_orders"], must_change_password: false, upload_replay_supported: true } });
    if (path.endsWith("/customer-register")) return route.fulfill({ json: { items: [{ id: "main", client_code: "MAIN", company_name: "Main customer" }], total: 1, has_more: false } });
    if (path.endsWith("/orders/upload-matrix")) {
      submissions++; key = route.request().headers()["idempotency-key"];
      expect(key).toBeTruthy(); return route.abort("connectionreset");
    }
    if (path.includes("/orders/upload-results/")) {
      expect(path.endsWith(key)).toBe(true); return route.fulfill({ json: ack });
    }
    if (path.endsWith("/orders/files/stored-file")) return route.fulfill({ json: ack });
    if (path.endsWith("/validate")) return route.fulfill({ json: { can_split: true, unresolved: [], quantity_issue_count: 0 } });
    return route.fulfill({ json: path.endsWith("/operations/summary") ? {} : [] });
  });
  await page.goto("/orders");
  await page.getByRole("combobox", { name: "Billing customer", exact: true }).selectOption("main");
  await page.locator('input[type="file"]').setInputFiles({ name: "orders.xlsx", mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer: Buffer.from("mock document bytes") });
  await page.getByRole("button", { name: "Upload and check order", exact: true }).click();
  await expect(page.getByRole("button", { name: "Recover pending upload", exact: true })).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "Recover pending upload", exact: true }).click();
  await page.locator(".order-file-reference summary").click();
  await expect(page.getByRole("button", { name: "Download original file", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Recover pending upload", exact: true })).toHaveCount(0);
  expect(submissions).toBe(1);
});
