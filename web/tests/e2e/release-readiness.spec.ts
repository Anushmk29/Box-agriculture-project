import { expect, test } from "@playwright/test";

test("sign-in explains the wait, preserves entered fields and enables retry when due", async ({ page }) => {
  await page.clock.install();
  let submissions = 0;
  await page.route("**/backend/api/v3/auth/login", route => {
    submissions++;
    return route.fulfill({ status: 429, headers: { "Retry-After": "5" }, json: { detail: "Too many login attempts. Please try again later." } });
  });
  await page.goto("/login");
  await page.getByLabel("Email", { exact: true }).fill("staff@example.test");
  await page.getByLabel("Password", { exact: true }).fill("correct-password-example");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("timer")).toContainText("0:05");
  await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "ภาษาไทย", exact: true }).click();
  await expect(page.locator(".message.error[role=alert]")).toContainText("มีการพยายามเข้าสู่ระบบมากเกินไป");
  await expect(page.getByRole("timer")).toContainText("0:05");
  await page.clock.fastForward(5_000);
  await expect(page.getByRole("timer")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "เข้าสู่ระบบ", exact: true })).toBeEnabled();
  await expect(page.getByLabel("อีเมล", { exact: true })).toHaveValue("staff@example.test");
  expect(submissions).toBe(1);
});

test("phone upload puts the file chooser first and keeps optional guidance available", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 });
  await page.addInitScript(() => localStorage.setItem("freshflow-language", "en"));
  const token = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  await page.context().addCookies([{ name: "access_token", value: `qa.${token}.qa`, domain: "127.0.0.1", path: "/" }]);
  await page.route("**/backend/api/v3/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { full_name: "Buyer", role: "GENERAL", user_id: "qa", permissions: ["validate_orders"], must_change_password: false } });
    if (path.endsWith("/order-routes")) return route.fulfill({ json: ["CT"] });
    if (path.endsWith("/customer-register")) return route.fulfill({ json: { items: [], total: 0, has_more: false } });
    return route.fulfill({ json: {} });
  });
  await page.goto("/orders");
  const browse = page.getByRole("button", { name: /Choose an order file.*Browse files/ });
  await expect(browse).toBeInViewport();
  await expect(page.getByRole("button", { name: "Show upload help" })).toHaveAttribute("aria-expanded", "false");
  await page.getByRole("button", { name: "Show upload help" }).click();
  await expect(page.getByRole("button", { name: "Hide upload help" })).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByRole("button", { name: "Show next action", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("accounting completes missing customer details and issues the invoice without leaving its workflow", async ({ page, request }) => {
  test.skip(process.env.QA_API_URL !== "http://127.0.0.1:8106", "Requires the disposable fixture API");
  await page.setViewportSize({ width: 390, height: 900 });
  const qaApi = process.env.QA_API_URL!;
  const response = await request.post(`${qaApi}/qa-fixtures/missing-customer-details`);
  expect(response.ok()).toBe(true);
  const fixture = await response.json();
  expect(fixture.disposable_database).toBe(true);
  await page.addInitScript(origin => {
    const original = window.fetch.bind(window);
    window.fetch = (input, init) => original(typeof input === "string" && input.startsWith("/backend/api/v3/")
      ? `${origin}${input.replace("/backend", "")}` : input, init);
  }, qaApi);
  await page.route("**/backend/api/v3/**", route => route.abort("blockedbyclient"));
  await page.goto(`/login?next=${encodeURIComponent(`/billing?invoiceId=${fixture.invoice_id}`)}`);
  await page.getByLabel("Email", { exact: true }).fill("owner@browser-qa.invalid");
  await page.getByLabel("Password", { exact: true }).fill("Browser-QA-Only-2026!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.getByRole("button", { name: "Approve and create final invoice", exact: true }).click();
  const editor = page.getByRole("dialog", { name: "Customer details", exact: true });
  await expect(editor).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: "../outputs/release-billing-customer-details-phone.png", fullPage: true });
  await editor.getByLabel(`Country for ${fixture.customer_code}`, { exact: true }).fill("Thailand");
  await editor.getByLabel(`Billing email for ${fixture.customer_code}`, { exact: true }).fill("orders@example.test");
  await editor.getByRole("button", { name: "Save customer details", exact: true }).click();
  await expect(editor).toHaveCount(0);
  await page.getByRole("button", { name: "Approve and create final invoice", exact: true }).click();
  await expect(page.getByRole("button", { name: "Open printable invoice", exact: true })).toBeVisible();
});
