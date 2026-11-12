import { expect, test, type Page } from "@playwright/test";

const qaApi = process.env.QA_API_URL;
test.skip(qaApi !== "http://127.0.0.1:8106", "Requires the disposable loopback fixture API on port 8106.");

async function layouts(page: Page, name: string) {
  for (const width of [390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${name} at ${width}px`).toBe(true);
    await page.screenshot({ path: `../outputs/client-journey-2026-10-29/${name}-${width}.png`, fullPage: true });
  }
}

test("client journey: upload, format review, purchasing, packing, sticker, final invoice and sign out", async ({ page, request }) => {
  test.setTimeout(180_000);
  page.setDefaultTimeout(15_000);
  const response = await request.get(`${qaApi}/qa-fixtures`);
  expect(response.ok()).toBe(true);
  const fixture = await response.json();
  expect(fixture.disposable_database).toBe(true);
  expect(fixture.upload_path).toContain("resilience-browser-");
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("response", response => {
    if (response.url().startsWith(qaApi!) && response.status() >= 500) errors.push(`${response.status()} ${response.url()}`);
  });
  await page.addInitScript(origin => {
    const original = window.fetch.bind(window);
    window.fetch = (input, init) => original(typeof input === "string" && input.startsWith("/backend/api/v3/")
      ? `${origin}${input.replace("/backend", "")}` : input, init);
  }, qaApi!);
  // A missed API redirect must never fall through to the configured live backend.
  await page.route("**/backend/api/v3/**", route => route.abort("blockedbyclient"));
  await page.goto("/login?next=%2Forders");
  await page.getByLabel("Email", { exact: true }).fill("owner@browser-qa.invalid");
  await page.getByLabel("Password", { exact: true }).fill("Browser-QA-Only-2026!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(/\/orders$/);
  await expect(page.locator("[data-next-action='true']")).toHaveClass("file-drop");
  await layouts(page, "01-upload");
  await page.locator("input[type=file]").setInputFiles(fixture.upload_path);
  await page.getByRole("combobox", { name: "Billing customer", exact: true }).selectOption(fixture.main_customer_id);
  await page.getByLabel("Shipment date", { exact: true }).fill("2026-10-29");
  await page.getByLabel("Route", { exact: true }).selectOption("__custom__");
  await page.getByLabel("New route", { exact: true }).fill("CT");
  await page.getByRole("button", { name: "Upload and check order", exact: true }).click();
  await expect(page.locator("[data-next-action='true']")).toHaveText("Review customer Excel format", { timeout: 45_000 });
  await page.getByRole("button", { name: "Show next action", exact: true }).click();
  await expect(page.getByRole("button", { name: "Review customer Excel format", exact: true })).toBeFocused();
  await page.getByRole("button", { name: "Review customer Excel format", exact: true }).click();
  await layouts(page, "02-format-review");
  await page.getByRole("button", { name: "Confirm format and re-read order", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.locator("[data-next-action='true']")).toHaveText("Confirm orders and create purchase batch");
  await page.locator(".order-file-reference summary").click();
  await page.getByRole("button", { name: "File tools", exact: true }).click();
  const sourceDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download original file", exact: true }).click();
  expect((await sourceDownload).suggestedFilename()).toMatch(/\.xlsx$/);
  await page.keyboard.press("Escape");
  await layouts(page, "03-order-review");
  await page.getByRole("button", { name: "Confirm orders and create purchase batch", exact: true }).click();
  await page.getByRole("link", { name: "Open purchase batch", exact: true }).click();
  await expect(page.getByRole("button", { name: "Confirm purchases and send to packing", exact: true })).toBeEnabled();
  const batchId = new URL(page.url()).searchParams.get("batchId");
  expect(batchId).toBeTruthy();
  await layouts(page, "04-purchasing");
  await expect(page.getByRole("region", { name: "Purchase quantities table", exact: true })).toBeVisible();
  await expect(page.locator("#purchase-quantities input").first()).toBeVisible();
  const buyerSheet = page.waitForEvent("popup");
  await page.getByRole("button", { name: "Export Buyer Sheet", exact: true }).click();
  const buyerDocument = await buyerSheet;
  await buyerDocument.waitForLoadState();
  await expect(buyerDocument.locator("body")).toContainText("T001");
  await buyerDocument.screenshot({ path: "../outputs/client-journey-2026-10-29/04-buyer-sheet.png", fullPage: true });
  await buyerDocument.close();
  await page.getByRole("button", { name: "Confirm purchases and send to packing", exact: true }).click();
  await page.getByRole("link", { name: "Continue to packing", exact: true }).first().click();
  await expect(page).toHaveURL(new RegExp(`batch_id=${batchId}`));
  await page.getByRole("row").filter({ hasText: "QA-JOURNEY.xlsx" }).getByRole("link", { name: /View suborders/ }).click();
  await page.getByRole("row").filter({ hasText: "CT-QA-UPLOAD" }).getByRole("link", { name: /Start packing/ }).click();
  await page.getByRole("combobox", { name: "Packing rules", exact: true }).selectOption("EL");
  await page.getByRole("button", { name: "Generate packing boxes", exact: true }).click();
  await expect(page.getByRole("button", { name: "Finalize Packing", exact: true })).toBeEnabled();
  await layouts(page, "05-packing");
  await page.locator(".packing-box-main").first().click();
  await page.getByRole("button", { name: "Sticker", exact: true }).click();
  const sticker = page.getByRole("dialog", { name: "Box sticker", exact: true });
  await expect(sticker.getByRole("button", { name: "Print sticker", exact: true })).toBeVisible();
  const code = await sticker.locator("code").innerText();
  await layouts(page, "06-sticker");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Finalize Packing", exact: true }).click();
  const packingUrl = page.url();
  await expect(page.getByRole("link", { name: "Continue to billing", exact: true })).toBeVisible();
  await layouts(page, "07-packing-complete");
  await page.getByRole("link", { name: "Continue to billing", exact: true }).click();
  await page.getByRole("button", { name: "Generate from final pack", exact: true }).click();
  await page.getByRole("link", { name: "Review invoice details", exact: true }).click();
  await expect(page.getByRole("spinbutton", { name: "Unit price for T001", exact: true })).toBeFocused();
  await layouts(page, "08-invoice-draft");
  await page.getByRole("button", { name: "Approve and create final invoice", exact: true }).click();
  await expect(page.getByRole("button", { name: "Open printable invoice", exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Review final invoice", exact: true }).click();
  await expect(page.locator("#invoice-details")).toBeFocused();
  await layouts(page, "09-final-invoice");
  const popup = page.waitForEvent("popup");
  await page.getByRole("button", { name: "Open printable invoice", exact: true }).click();
  const document = await popup;
  await document.waitForLoadState();
  await expect(document.locator("body")).toContainText("CT-QA-UPLOAD");
  await document.screenshot({ path: "../outputs/client-journey-2026-10-29/10-printable-invoice.png", fullPage: true });
  await document.close();
  await page.goto("/scan");
  await page.getByLabel("Sticker code or QR link", { exact: true }).fill(code);
  await page.getByRole("button", { name: "Open sticker", exact: true }).click();
  await expect(page.getByRole("link", { name: "View this box in packing", exact: true })).toHaveAttribute("href", /box=/);
  await layouts(page, "11-scan");
  await page.getByRole("link", { name: "View this box in packing", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(new URL(packingUrl).searchParams.get("orderId")!));
  await expect(page.locator(".scanned-box")).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Finalize Packing", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Sign out everywhere", exact: true }).click();
  await expect(page).toHaveURL(/\/login$/);
  expect(errors).toEqual([]);
});

test("client workspaces load on desktop and phone, and packing staff retain their access limits", async ({ page, request }) => {
  test.setTimeout(120_000);
  const fixture = await (await request.get(`${qaApi}/qa-fixtures`)).json();
  expect(fixture.disposable_database).toBe(true);
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.addInitScript(origin => {
    const original = window.fetch.bind(window);
    window.fetch = (input, init) => original(typeof input === "string" && input.startsWith("/backend/api/v3/")
      ? `${origin}${input.replace("/backend", "")}` : input, init);
  }, qaApi!);
  await page.route("**/backend/api/v3/**", route => route.abort("blockedbyclient"));
  await page.goto("/login");
  await page.getByLabel("Email", { exact: true }).fill("owner@browser-qa.invalid");
  await page.getByLabel("Password", { exact: true }).fill("Browser-QA-Only-2026!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(/\/$/);
  for (const route of ["/", "/orders/files", "/procurement/batches", "/packing/orders", "/packing/setup", "/billing", "/users", "/orders/attention", "/scan"]) {
    await page.goto(route);
    await expect(page.locator("h1").first()).toBeVisible();
    await expect(page.locator("main").getByRole("alert").filter({ hasText: /failed|unavailable|request deadline/i })).toHaveCount(0);
    await layouts(page, `workspace-${route === "/" ? "today" : route.slice(1).replaceAll("/", "-")}`);
  }
  await page.getByRole("button", { name: "Sign out everywhere", exact: true }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.getByLabel("Email", { exact: true }).fill("floor@browser-qa.invalid");
  await page.getByLabel("Password", { exact: true }).fill("Browser-QA-Only-2026!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("navigation", { name: "Primary navigation", exact: true }).getByRole("link", { name: /Billing|Team & access/ })).toHaveCount(0);
  for (const route of ["/billing", "/users"]) {
    await page.goto(route);
    await expect(page.getByRole("heading", { name: "Access unavailable", exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "Return to Today", exact: true })).toHaveAttribute("href", "/");
  }
  expect(errors).toEqual([]);
});
