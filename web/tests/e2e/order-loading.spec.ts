import { expect, test, type Page, type BrowserContext } from "@playwright/test";

function gate() {
  let release!: () => void;
  const promise = new Promise<void>(resolve => { release = resolve; });
  return { promise, release: () => release() };
}

const ack = (id: string) => ({ raw_file_id: id, file_name: `${id}.xlsx`, total_sub_customers_detected: 1, sub_customer_codes: [id], group_summary: {}, sheets_detected: [], status: "PARSED", verification_notice: "Check this saved order." });
const validated = { unresolved: [], can_split: true, quantity_issue_count: 0 };

async function session(page: Page, context: BrowserContext) {
  const jwt = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  await context.addCookies([{ name: "access_token", value: `qa.${jwt}.qa`, domain: "127.0.0.1", path: "/" }]);
  await page.route("**/backend/api/v3/**", route => route.fulfill({ json: new URL(route.request().url()).pathname.endsWith("/auth/me")
    ? { full_name: "Order staff", role: "GENERAL", must_change_password: false, permissions: ["validate_orders"] }
    : { items: [], total: 0, offset: 0, limit: 25, has_more: false } }));
}

test("saved order stays on an animated skeleton through opening and validation, with navigation available", async ({ page, context }) => {
  await session(page, context);
  const opening = gate(); const checking = gate();
  await page.route("**/orders/files/pending-order", async route => { await opening.promise; await route.fulfill({ json: ack("pending-order") }); });
  await page.route("**/orders/pending-order/validate", async route => { await checking.promise; await route.fulfill({ json: validated }); });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/orders?fileId=pending-order");
  const loading = page.getByRole("status", { name: "Opening order…", exact: true });
  await expect(loading).toBeVisible();
  await expect(loading.locator(".skeleton").first()).toHaveCSS("animation-name", "skeleton-shimmer");
  await expect(page.getByRole("heading", { name: "New customer order", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Upload and check order", exact: true })).toHaveCount(0);
  await expect(page.locator("[data-next-action='true']")).toHaveCount(0);
  await expect(page.locator(".loading-dialog")).toHaveCount(0);
  await page.getByRole("button", { name: "Menu", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "Workspaces", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Close Workspaces", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "Workspaces", exact: true })).toHaveCount(0);
  opening.release();
  const validating = page.getByRole("status", { name: "Checking products and quantities…", exact: true });
  await expect(validating).toBeVisible();
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(validating.locator(".skeleton").first()).toHaveCSS("animation-name", "none");
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: "../outputs/order-loading-mobile.png", fullPage: true });
  checking.release();
  await expect(page.locator(".loading-skeleton")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Review this order", exact: true })).toBeVisible();
  await expect(page.locator(".order-file-name")).toHaveText("pending-order.xlsx");
});

for (const failingStep of ["opening", "checking"] as const) {
  test(`${failingStep} errors replace the skeleton with a working retry instead of an empty upload`, async ({ page, context }) => {
    await session(page, context);
    let fail = true;
    await page.route("**/orders/files/retry-order", route => route.fulfill(fail && failingStep === "opening"
      ? { status: 503, json: { detail: "Order service unavailable" } } : { json: ack("retry-order") }));
    await page.route("**/orders/retry-order/validate", route => route.fulfill(fail && failingStep === "checking"
      ? { status: 503, json: { detail: "Quantity check unavailable" } } : { json: validated }));
    await page.goto("/orders?fileId=retry-order");
    await expect(page.locator(".order-load-error")).toContainText(failingStep === "opening" ? "Order service unavailable" : "Quantity check unavailable");
    await expect(page.locator(".loading-skeleton")).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "New customer order", exact: true })).toHaveCount(0);
    fail = false;
    await page.getByRole("button", { name: "Retry opening order", exact: true }).click();
    await expect(page.locator(".order-file-name")).toHaveText("retry-order.xlsx");
    await expect(page.locator(".order-load-error")).toHaveCount(0);
  });
}

test("switching order files shows a fresh skeleton and ignores the previous slow response", async ({ page, context }) => {
  await session(page, context);
  const oldRead = gate(); const newRead = gate();
  let oldValidationRequests = 0;
  await page.route("**/orders/files/old-order", async route => { await oldRead.promise; await route.fulfill({ json: ack("old-order") }); });
  await page.route("**/orders/files/new-order", async route => { await newRead.promise; await route.fulfill({ json: ack("new-order") }); });
  await page.route("**/orders/*/validate", route => {
    if (route.request().url().includes("/old-order/")) oldValidationRequests++;
    return route.fulfill({ json: validated });
  });
  const oldRequest = page.waitForRequest("**/orders/files/old-order");
  await page.goto("/orders?fileId=old-order");
  await oldRequest;
  await page.evaluate(() => window.history.pushState(null, "", "/orders?fileId=new-order"));
  await expect(page.getByRole("status", { name: "Opening order…", exact: true })).toBeVisible();
  newRead.release();
  await expect(page.locator(".order-file-name")).toHaveText("new-order.xlsx");
  const oldResponse = page.waitForResponse("**/orders/files/old-order");
  oldRead.release(); await oldResponse;
  await expect(page.locator(".order-file-name")).toHaveText("new-order.xlsx");
  expect(oldValidationRequests).toBe(0);
});
