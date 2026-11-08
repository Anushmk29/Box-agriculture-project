import { expect, test, type Page } from "@playwright/test";

const qaApi = process.env.QA_API_URL;
test.skip(!qaApi, "Start the disposable QA API and set QA_API_URL; never run mutations against live data.");

async function isolatedApi(page: Page) {
  const failures: string[] = [];
  page.on("pageerror", error => failures.push(error.message));
  // Native browser fetch preserves multipart binary uploads and cookie behavior.
  await page.addInitScript(apiOrigin => {
    const originalFetch = window.fetch.bind(window);
    window.fetch = (input, init) => {
      if (typeof input === "string" && input.startsWith("/backend/api/v3/")) {
        return originalFetch(`${apiOrigin}${input.replace("/backend", "")}`, init);
      }
      return originalFetch(input, init);
    };
  }, qaApi!);
  page.on("response", response => {
    if (response.url().startsWith(qaApi!) && response.status() >= 500) failures.push(`${response.status()} ${new URL(response.url()).pathname}`);
  });
  return failures;
}

async function login(page: Page, email = "owner@browser-qa.invalid") {
  await page.goto("/login");
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("Password", { exact: true }).fill("Browser-QA-Only-2026!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(/\/$/);
}

test("real API: intake through procurement, staff packing finalization and invoicing", async ({ page, request }) => {
  test.setTimeout(90_000);
  const failures = await isolatedApi(page);
  await login(page);
  const fixture = await (await request.get(`${qaApi}/qa-fixtures`)).json();
  expect(fixture.disposable_database).toBe(true);
  await page.goto(`/orders?fileId=${fixture.raw_file_id}`);
  // The seeded file contains two customers and uses the real validator/splitter.
  await expect(page.getByText("2 subcustomers in this order", { exact: true })).toBeVisible();
  await page.locator(".order-file-reference summary").click();
  await page.getByRole("button", { name: "File tools", exact: true }).click();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download original file" }).click();
  expect((await download).suggestedFilename()).toMatch(/\.xlsx$/);
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Confirm orders and create purchase batch" }).click();
  await expect(page.getByText(/Success! 2 atomic sub-customer orders created/)).toBeVisible();
  await page.getByRole("link", { name: "Open purchase batch", exact: true }).click();
  await expect(page.getByRole("button", { name: "Confirm purchases and send to packing", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Confirm purchases and send to packing", exact: true }).click();
  await page.getByRole("link", { name: "Continue to packing", exact: true }).first().click();
  await page.getByRole("row").filter({ hasText: "QA-pending.xlsx" }).getByRole("link", { name: /View suborders/ }).click();
  await page.getByRole("row").filter({ hasText: "CT-QA-A" }).getByRole("link", { name: /Start packing/ }).click();
  await page.getByRole("combobox", { name: "Packing rules", exact: true }).selectOption("EL");
  await page.getByRole("button", { name: "Generate packing boxes", exact: true }).click();
  await expect(page.getByRole("button", { name: "Start packing", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Finalize Packing", exact: true }).click();
  await page.getByRole("link", { name: "Continue to billing", exact: true }).click();
  await page.getByRole("button", { name: "Generate from final pack" }).click();
  await expect(page.getByRole("button", { name: "Approve and create final invoice", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Approve and create final invoice" }).click();
  await expect(page.getByRole("button", { name: "Open printable invoice", exact: true })).toBeVisible();
  await expect(page.getByLabel("Cleared amount")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /receipt|record payment/i })).toHaveCount(0);
  expect(failures).toEqual([]);
});

test("real API: spreadsheet upload, templates and intake validation", async ({ page, request }) => {
  const failures = await isolatedApi(page);
  await login(page);
  await page.goto("/orders");
  const download = page.waitForEvent("download");
  await page.getByRole("link", { name: /Group Matrix Template/ }).click();
  expect((await download).suggestedFilename()).toMatch(/\.xlsx$/);
  const fixture = await (await request.get(`${qaApi}/qa-fixtures`)).json();
  expect(fixture.disposable_database).toBe(true);
  await page.locator('input[type="file"]').setInputFiles(fixture.upload_path);
  await page.getByRole("combobox", { name: "Billing customer", exact: true }).selectOption(fixture.main_customer_id);
  await page.getByLabel("Shipment date").fill("2026-10-03");
  await page.getByRole("button", { name: "Upload and check order" }).click();
  await page.getByRole("button", { name: "Review customer Excel format", exact: true }).click();
  await page.getByRole("button", { name: "Confirm format and re-read order", exact: true }).click();
  await expect(page.getByText("1 subcustomer in this order", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Confirm orders and create purchase batch" })).toBeEnabled();
  expect(failures).toEqual([]);
});

test("real API: owner pages and mobile layouts load without runtime errors", async ({ page }) => {
  const failures = await isolatedApi(page);
  await login(page);
  for (const route of ["/", "/orders", "/procurement", "/packing", "/billing", "/users", "/scan"]) {
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto(route);
      await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
      await expect(page.getByText(/Request failed with status 500/)).toHaveCount(0);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
      expect(overflow, `${route} at ${width}px overflows the viewport`).toBe(false);
      await page.screenshot({ path: `../outputs/browser-qa-${route === "/" ? "operations" : route.slice(1)}-${width}.png`, fullPage: true });
    }
  }
  expect(failures).toEqual([]);
});

test("real API: general staff cannot access owner/accounting routes", async ({ page }) => {
  const failures = await isolatedApi(page);
  await login(page, "floor@browser-qa.invalid");
  const navigation = page.getByRole("navigation", { name: "Primary navigation" });
  await expect(navigation.getByRole("link", { name: "Billing" })).toHaveCount(0);
  await expect(navigation.getByRole("link", { name: "Users" })).toHaveCount(0);
  await page.goto("/users");
  await expect(page.getByRole("heading", { name: "Access unavailable", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Add person", exact: true })).toHaveCount(0);
  await page.goto("/billing");
  await expect(page.getByRole("heading", { name: "Access unavailable", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Generate from final pack", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.goto("/packing");
  await expect(page).toHaveURL(/\/login\?next=/);
  expect(failures).toEqual([]);
});

test("real API: malformed sticker input produces a recoverable error", async ({ page }) => {
  const failures = await isolatedApi(page);
  await login(page);
  await page.goto("/scan");
  await page.getByLabel("Sticker code or QR link").fill("BOX-%invalid");
  await page.getByRole("button", { name: "Open sticker", exact: true }).click();
  await expect(page.locator(".field-error[role=alert]")).toContainText("This code could not be read");
  expect(failures).toEqual([]);
});

test("real API: invoice details return to the invoice queue", async ({ page }) => {
  const failures = await isolatedApi(page);
  await login(page);
  await page.goto("/billing");
  await page.getByRole("tab", { name: "All invoices", exact: true }).click();
  await page.locator(".register-row-link").first().getByRole("link").last().click();
  await expect(page.getByRole("button", { name: "Back to invoice queue", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Back to invoice queue", exact: true }).click();
  await expect(page.getByRole("region", { name: "Invoice register table", exact: true })).toBeVisible();
  expect(failures).toEqual([]);
});

test("real API: owner creates, changes access, resets and removes a disposable user", async ({ page }) => {
  const failures = await isolatedApi(page);
  await login(page);
  await page.goto("/users");
  await page.getByRole("button", { name: "Add person", exact: true }).click();
  await page.getByLabel("Full name", { exact: true }).fill("Browser QA Person");
  await page.getByLabel("Email", { exact: true }).fill("person@browser-qa.invalid");
  await page.getByRole("button", { name: "Create user", exact: true }).click();
  await page.getByRole("dialog", { name: "Temporary password", exact: true }).getByRole("button", { name: "Done", exact: true }).click();
  const row = page.getByRole("row").filter({ hasText: "Browser QA Person" });
  await expect(row).toBeVisible();
  await row.getByRole("combobox").selectOption("GENERAL_ACCOUNTING");
  await page.getByRole("dialog", { name: "Change access level?" }).getByRole("button", { name: "Change access", exact: true }).click();
  await expect(row.getByRole("combobox")).toHaveValue("GENERAL_ACCOUNTING");
  await row.getByRole("button", { name: "Manage" }).click();
  await page.getByRole("dialog", { name: "Manage Browser QA Person" }).getByRole("button", { name: "Reset password", exact: true }).click();
  await page.getByRole("dialog", { name: /Reset password for/ }).getByRole("button", { name: "Reset password", exact: true }).click();
  await expect(page.getByText(/Password reset for Browser QA Person/)).toBeVisible();
  await page.getByRole("button", { name: "Done", exact: true }).click();
  await row.getByRole("button", { name: "Manage" }).click();
  await page.getByRole("button", { name: "Deactivate account", exact: true }).click();
  await page.getByRole("dialog", { name: /Deactivate Browser QA Person/ }).getByRole("button", { name: "Deactivate account", exact: true }).click();
  await expect(row).toContainText("Inactive");
  await row.getByRole("button", { name: "Manage" }).click();
  await page.getByRole("button", { name: "Reactivate account", exact: true }).click();
  await expect(row).not.toContainText("Inactive");
  await row.getByRole("button", { name: "Manage" }).click();
  await page.getByRole("dialog", { name: "Manage Browser QA Person" }).getByRole("button", { name: "Delete permanently", exact: true }).click();
  await page.getByRole("dialog", { name: /Delete Browser QA Person/ }).getByRole("button", { name: "Delete permanently", exact: true }).click();
  await expect(row).toHaveCount(0);
  expect(failures).toEqual([]);
});

test("real API: final invoice documents and a finalized sticker open correctly", async ({ page }) => {
  const failures = await isolatedApi(page);
  await login(page);
  await page.goto("/billing");
  await page.getByRole("tab", { name: "All invoices", exact: true }).click();
  const invoiceResponse = page.waitForResponse(response => /\/api\/v3\/invoicing\/[^/?]+$/.test(response.url()) && response.request().method() === "GET");
  await page.locator(".register-row-link").filter({ hasText: "CT-QA-A" }).getByRole("link").last().click();
  const savedInvoice = await (await invoiceResponse).json();
  for (const button of ["Open printable invoice"]) {
    const popupPromise = page.context().waitForEvent("page");
    await page.getByRole("button", { name: button, exact: true }).click();
    const document = await popupPromise;
    await expect(document.locator("body")).toContainText("CT-QA-A");
    await document.screenshot({ path: `../outputs/browser-qa-${button === "Open receipt" ? "receipt" : "printable-invoice"}.png`, fullPage: true });
    await document.close();
  }
  await page.goto(`/packing?orderId=${savedInvoice.client_order_id}`);
  await page.locator(".packing-box-main").first().click();
  await page.getByRole("button", { name: "Sticker", exact: true }).click();
  const code = await page.getByText(/^BOX-/).textContent();
  expect(code).toBeTruthy();
  await page.goto("/scan");
  await page.getByLabel("Sticker code or QR link").fill(code!.trim());
  await page.getByRole("button", { name: "Open sticker", exact: true }).click();
  await expect(page.getByText("Final orders are read-only", { exact: true })).toBeVisible();
  await expect(page.getByText("Final pack locked", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: /CT-QA-A/ })).toBeVisible();
  await page.screenshot({ path: "../outputs/browser-qa-final-sticker.png", fullPage: true });
  expect(failures).toEqual([]);
});

test("real API: packing starts with contents ready for review", async ({ page }) => {
  await isolatedApi(page);
  await login(page);
  await page.goto("/packing/orders");
  await page.getByRole("link", { name: "View all suborders", exact: true }).click();
  await page.getByRole("row").filter({ hasText: "BUYER-1" }).getByRole("link").last().click();
  await page.getByRole("combobox", { name: "Packing rules", exact: true }).selectOption("EL");
  await page.getByRole("button", { name: "Generate packing boxes", exact: true }).click();
  await expect(page.getByRole("button", { name: "Finalize Packing", exact: true })).toBeEnabled();
  await expect(page.getByLabel(/scale (net|gross)/)).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Start packing", exact: true })).toHaveCount(0);
});
