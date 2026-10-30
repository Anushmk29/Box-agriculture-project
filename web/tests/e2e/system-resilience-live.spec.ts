import { expect, test } from "@playwright/test";

const qaApi = process.env.QA_API_URL;
test.skip(qaApi !== "http://127.0.0.1:8106", "Requires this audit's disposable loopback API on port 8106.");

test("audit: real disposable workflow from intake through final invoice", async ({ page, request }) => {
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
  await page.goto(`/login?next=${encodeURIComponent(`/orders?fileId=${fixture.raw_file_id}`)}`);
  await page.getByLabel("Email", { exact: true }).fill("owner@browser-qa.invalid");
  await page.getByLabel("Password", { exact: true }).fill("Browser-QA-Only-2026!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.getByRole("button", { name: "Confirm orders and create purchase batch", exact: true }).click();
  await page.getByRole("link", { name: "Open purchase batch", exact: true }).first().click();
  await page.getByRole("button", { name: "Confirm purchases and send to packing", exact: true }).click();
  await page.getByRole("link", { name: "View related packing orders", exact: true }).click();
  await page.getByRole("row").filter({ hasText: "CT-QA-A" }).getByRole("link", { name: /Start packing/ }).click();
  await page.getByRole("combobox", { name: "Packing rules", exact: true }).selectOption("EL");
  await page.getByRole("button", { name: "Generate packing boxes", exact: true }).click();
  await page.getByRole("button", { name: "Finalize Packing", exact: true }).click();
  await page.getByRole("link", { name: "Continue to billing", exact: true }).click();
  await page.getByRole("button", { name: "Generate from final pack", exact: true }).click();
  await expect(page.getByRole("spinbutton", { name: "Unit price for T001", exact: true })).toHaveValue("2.5");
  await page.getByRole("button", { name: "Approve and create final invoice", exact: true }).click();
  await expect(page.getByRole("button", { name: "Open printable invoice", exact: true })).toBeVisible();
  await expect(page.getByLabel("Cleared amount")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /receipt|record payment/i })).toHaveCount(0);
  expect(errors).toEqual([]);
});
