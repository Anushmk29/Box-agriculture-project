import { expect, test } from "@playwright/test";

const permissions = ["validate_orders", "confirm_procurement", "floor_packing", "design_packing", "manage_billing", "manage_users"];
const emptyPage = { items: [], total: 0, offset: 0, limit: 25, has_more: false };

test.beforeEach(async ({ context, page }) => {
  const payload = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  await context.addCookies([{ name: "access_token", value: `qa.${payload}.qa`, domain: "127.0.0.1", path: "/" }]);
  await page.route("**/backend/api/v3/**", route => route.fulfill({ json: new URL(route.request().url()).pathname.endsWith("/auth/me")
    ? { full_name: "QA", role: "OWNER_ADMIN", permissions, must_change_password: false }
    : emptyPage }));
});

for (const path of ["/orders/files", "/procurement/batches", "/packing/orders", "/billing", "/billing?tab=invoices", "/users"]) {
  test(`${path} skeletons match table columns and resolve after a delayed empty response`, async ({ page }) => {
    let release!: () => void;
    const pending = new Promise<void>(resolve => { release = resolve; });
    await page.route("**/backend/api/v3/**", async route => {
      if (!new URL(route.request().url()).pathname.endsWith("/auth/me")) {
        await pending;
        return route.fulfill({ json: emptyPage });
      }
      await route.fallback();
    });
    await page.goto(path);
    const table = page.locator(".record-register table");
    const loading = page.getByRole("status", { name: "Loading records…", exact: true });
    await expect(loading).toBeVisible();
    await expect(table.locator("tbody tr")).toHaveCount(5);
    expect(await table.locator("tbody tr").first().locator("td").count()).toBe(await table.locator("thead th").count());
    await expect(loading.locator(".skeleton").first()).toHaveCSS("animation-name", "skeleton-shimmer");
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(loading).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await expect(page.getByRole("button", { name: "Menu", exact: true })).toBeVisible();
    if (path === "/orders/files") await page.screenshot({ path: "../outputs/table-skeleton-mobile.png", fullPage: true });
    await page.emulateMedia({ reducedMotion: "reduce" });
    await expect(loading.locator(".skeleton").first()).toHaveCSS("animation-name", "none");
    release();
    await expect(loading).toHaveCount(0);
    await expect(table.locator(".table-skeleton-row")).toHaveCount(0);
    await expect(page.locator(".register-table-scroll")).toHaveAttribute("aria-busy", "false");
  });
}

for (const outcome of ["empty", "error"] as const) {
  test(`product and box skeletons resolve to ${outcome}`, async ({ page }) => {
    let release!: () => void;
    const pending = new Promise<void>(resolve => { release = resolve; });
    await page.route("**/packing-setup/**", async route => {
      await pending;
      return route.fulfill(outcome === "error" ? { status: 503, json: { detail: "Product service unavailable" } }
        : { json: new URL(route.request().url()).pathname.endsWith("/boxes") ? []
          : { ...emptyPage, summary: { total: 0, ready: 0, strict: false } } });
    });
    await page.goto("/packing/setup");
    const profiles = page.getByRole("region", { name: "Editable product worksheet" });
    await expect(page.getByRole("status", { name: "Loading product settings…" })).toBeVisible();
    await expect(profiles.locator("thead th")).toHaveCount(6);
    await expect(profiles.locator("tbody tr")).toHaveCount(5);
    await page.setViewportSize({ width: 390, height: 844 });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.getByRole("tab", { name: "Box catalogue", exact: true }).click();
    await expect(page.getByRole("status", { name: "Loading box catalogue…" })).toBeVisible();
    await page.getByRole("tab", { name: "Products" }).click();
    release();
    await expect(page.locator(".table-skeleton-row")).toHaveCount(0);
    if (outcome === "error") {
      await expect(page.getByRole("alert").filter({ hasText: "Product service unavailable" })).toBeVisible();
      await expect(page.getByText("No products match these filters.")).toHaveCount(0);
    } else await expect(page.getByText("No products match these filters.")).toBeVisible();
  });
}

test("product filtering replaces stale rows with skeletons during the debounce and read", async ({ page }) => {
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/packing-setup/**", async route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/boxes")) return route.fulfill({ json: [] });
    if (url.searchParams.get("q")) await pending;
    return route.fulfill({ json: { ...emptyPage, total: 1, items: [{ id: "basil", t_code: "BASIL", name_en: "Holy basil", name_th: "", produce_type: "HERB", firmness: "SOFT", profiles: [], ready: false, source_matches: [], packing_revision: 1, box_material: "FOAM", temperature: "COLD", control_status: "EL", weight_class: "LIGHT", volume_l_per_kg: 6, volume_estimated: true, limits: [], source_choices: [], grouping_row: null, identity_choices: [], limit_issues: [], problems: [] }],
      summary: { total: 1, ready: 0, strict: false } } });
  });
  await page.goto("/packing/setup");
  await expect(page.getByLabel("BASIL litres per kg")).toBeVisible();
  await page.getByLabel("Find product").fill("basil");
  await expect(page.getByRole("status", { name: "Loading product settings…" })).toBeVisible();
  await expect(page.getByLabel("BASIL litres per kg")).toHaveCount(0);
  await page.screenshot({ path: "../outputs/product-table-skeleton.png", fullPage: true });
  release();
  await expect(page.getByRole("status", { name: "Loading product settings…" })).toHaveCount(0);
  await expect(page.getByLabel("BASIL litres per kg")).toBeVisible();
});

for (const detail of [
  { path: "/procurement?batchId=slow", endpoint: "**/procurement/batches/slow", label: "Loading selected batch", columns: 7 },
  { path: "/packing?orderId=slow", endpoint: "**/packing/orders/slow/packing-list", label: "Loading packing order", columns: 6 },
  { path: "/scan/slow", endpoint: "**/packing/scan/slow?*", label: "Loading box record", columns: 5 },
  { path: "/billing?invoiceId=slow", endpoint: "**/invoicing/slow", label: "Loading invoice details…", columns: 6 },
]) {
  test(`${detail.path} shows table skeletons and removes them on failure`, async ({ page }) => {
    let release!: () => void;
    const pending = new Promise<void>(resolve => { release = resolve; });
    await page.route(detail.endpoint, async route => {
      await pending;
      return route.fulfill({ status: 503, json: { detail: "Details unavailable" } });
    });
    await page.goto(detail.path);
    const skeleton = page.getByRole("status", { name: detail.label, exact: true });
    await expect(skeleton).toBeVisible();
    await expect(skeleton.locator("thead th")).toHaveCount(detail.columns);
    await expect(skeleton.locator("tbody tr")).toHaveCount(5);
    release();
    await expect(skeleton).toHaveCount(0);
    await expect(page.getByRole("alert").filter({ hasText: "Details unavailable" })).toBeVisible();
  });
}
