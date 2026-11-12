import { expect, test, type Page } from "@playwright/test";

const qaApi = process.env.QA_API_URL;
test.skip(!qaApi, "Requires the disposable browser QA API.");

async function reviewLayouts(page: Page, name: string) {
  for (const width of [390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await page.evaluate(() => window.scrollTo(0, 0));
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `../outputs/ui-${name}-${width}.png`, fullPage: true });
  }
}

test("real workflow retains shipment through buying, packing and payment", async ({ page, request }) => {
  test.setTimeout(120_000);
  const fixture = await (await request.get(`${qaApi}/qa-fixtures`)).json();
  expect(fixture.disposable_database).toBe(true);
  const failures: string[] = [];
  page.on("pageerror", error => failures.push(error.message));
  await page.addInitScript(origin => {
    const original = window.fetch.bind(window);
    window.fetch = (input, init) => original(typeof input === "string" && input.startsWith("/backend/api/v3/") ? `${origin}${input.replace("/backend", "")}` : input, init);
  }, qaApi!);
  await page.goto(`/login?next=${encodeURIComponent(`/orders?fileId=${fixture.raw_file_id}`)}`);
  await page.getByLabel("Email", { exact: true }).fill("owner@browser-qa.invalid");
  await page.getByLabel("Password", { exact: true }).fill("Browser-QA-Only-2026!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`fileId=${fixture.raw_file_id}`));
  await expect(page.getByRole("button", { name: "Confirm orders and create purchase batch", exact: true })).toBeEnabled();
  await reviewLayouts(page, "orders");
  await page.getByRole("button", { name: "Confirm orders and create purchase batch", exact: true }).click();
  const buying = page.getByRole("link", { name: "Open purchase batch", exact: true }).first();
  await expect(buying).toHaveAttribute("href", /batchId=/);
  await buying.click();
  await expect(page).toHaveURL(/\/procurement\?batchId=/);
  const batchId = new URL(page.url()).searchParams.get("batchId");
  expect(batchId).toBeTruthy();
  await expect(page.getByRole("button", { name: "Confirm purchases and send to packing", exact: true })).toBeEnabled();
  await reviewLayouts(page, "purchasing");
  await page.getByRole("button", { name: "Confirm purchases and send to packing", exact: true }).click();
  await page.getByRole("link", { name: "Continue to packing", exact: true }).first().click();
  await expect(page).toHaveURL(new RegExp(`batch_id=${batchId}`));
  const order = page.getByRole("row").filter({ hasText: "CT-QA-A" });
  await order.getByRole("link", { name: /Start packing/ }).click();
  await expect(page).toHaveURL(/\/packing\?orderId=/);
  const orderId = new URL(page.url()).searchParams.get("orderId");
  await page.getByRole("button", { name: "Finalize Packing", exact: true }).click();
  await page.getByRole("link", { name: "Continue to billing", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`orderId=${orderId}`));
  await page.getByRole("button", { name: "Generate from final pack", exact: true }).click();
  await page.getByRole("button", { name: "Approve and create final invoice", exact: true }).click();
  await expect(page.getByRole("button", { name: "Open printable invoice", exact: true })).toBeVisible();
  await reviewLayouts(page, "invoice");
  await expect(page.getByRole("button", { name: "Open printable invoice", exact: true })).toBeVisible();
  await expect(page.getByLabel("Cleared amount")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /receipt|record payment/i })).toHaveCount(0);
  expect(failures).toEqual([]);
});
