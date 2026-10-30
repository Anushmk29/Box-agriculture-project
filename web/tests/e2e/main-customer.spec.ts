import { expect, test } from "@playwright/test";

test("search, select and create main customer accounts from order intake", async ({ page, context }) => {
  const payload = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  await context.addCookies([{ name: "access_token", value: `qa.${payload}.qa`, domain: "127.0.0.1", path: "/" }]);
  const customers = [
    { id: "alpha", client_code: "ALPHA", company_name: "Alpha Trading", route_group: "CD", is_main_customer: true },
    { id: "beta", client_code: "BETA", company_name: "Beta Export", route_group: "DTD", is_main_customer: true },
  ];
  let createdPayload: Record<string, unknown> | undefined;
  let rejectCreate = true;
  await page.route("**/backend/api/v3/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/master-data/order-routes")) return route.fulfill({ json: ["CD", "DTD", "ITM", "JCT", "LYT", "MLD", "NGA", "PARIS", "PRD", "TETE"] });
    if (path.endsWith("/master-data/customer-register")) {
      const params = new URL(route.request().url()).searchParams;
      const q = params.get("q")?.toLowerCase() || "";
      const matching = customers.filter(customer => `${customer.company_name} ${customer.client_code}`.toLowerCase().includes(q));
      return route.fulfill({ json: { items: matching, total: matching.length, offset: 0, limit: 25, has_more: false } });
    }
    if (path.endsWith("/master-data/customers")) {
      if (route.request().method() === "POST") {
        createdPayload = route.request().postDataJSON();
        if (rejectCreate) return route.fulfill({ status: 409, json: { detail: "Customer code already exists" } });
        return route.fulfill({ status: 201, json: { id: "new", ...createdPayload, is_main_customer: true } });
      }
      return route.fulfill({ json: customers });
    }
    await route.fulfill({ json: path.endsWith("/auth/me")
      ? { full_name: "QA", role: "GENERAL", must_change_password: false, permissions: ["validate_orders"] }
      : path.endsWith("/operations/summary") ? {} : [] });
  });
  await page.goto("/orders");
  const date = page.getByLabel("Shipment date");
  const routeLine = page.getByRole("combobox", { name: "Route", exact: true });
  await expect(routeLine.locator("option[value='NGA']")).toHaveCount(1);
  await expect(routeLine.locator("option[value='PRD']")).toHaveCount(1);
  const dateBounds = await date.boundingBox();
  const routeBounds = await routeLine.boundingBox();
  expect(dateBounds!.height).toBe(40);
  expect(routeBounds!.height).toBe(40);
  expect(Math.abs(dateBounds!.y - routeBounds!.y)).toBeLessThan(2);
  const search = page.getByRole("searchbox", { name: "Find billing customer" });
  const select = page.getByRole("combobox", { name: "Billing customer", exact: true });
  await expect(select.locator("option")).toHaveCount(3);
  await search.fill("Beta");
  await expect(select.locator("option")).toHaveCount(2);
  await select.selectOption("beta");
  await expect(routeLine).toHaveValue("DTD");
  await routeLine.selectOption("MLD");
  await expect(routeLine).toHaveValue("MLD");
  await search.fill("New Trading");
  await expect(select).toHaveValue("beta");
  await expect(routeLine).toHaveValue("MLD");
  await routeLine.selectOption("__custom__");
  const customRoute = page.getByRole("textbox", { name: "New route", exact: true });
  await customRoute.fill(" new-lane ");
  await date.focus();
  await expect(customRoute).toHaveValue("NEW-LANE");
  await page.getByRole("button", { name: "Add billing customer", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Add billing customer" });
  await expect(dialog.getByLabel("Company name")).toHaveValue("New Trading");
  await dialog.getByLabel("Customer code").fill("NEW-MAIN");
  await dialog.getByLabel("Destination country").fill("France");
  await dialog.getByRole("button", { name: "Add and select account" }).click();
  await expect(dialog.getByText("Customer code already exists", { exact: true })).toBeVisible();
  rejectCreate = false;
  await dialog.getByRole("button", { name: "Add and select account" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(select).toHaveValue("new");
  await expect(search).toHaveValue("");
  await expect(routeLine).toHaveValue("NEW-LANE");
  expect(createdPayload).toMatchObject({ client_code: "NEW-MAIN", company_name: "New Trading", destination_country: "France", route_group: "NEW-LANE" });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(routeLine).toBeVisible();
  const mobileBounds = await routeLine.boundingBox();
  expect(mobileBounds!.height).toBe(44);
  expect((await date.boundingBox())!.height).toBe(44);
  expect(mobileBounds!.x + mobileBounds!.width).toBeLessThanOrEqual(390);
});

test("document customers fill their route and route loading can be retried", async ({ page, context }) => {
  const jwt = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  await context.addCookies([{ name: "access_token", value: `qa.${jwt}.qa`, domain: "127.0.0.1", path: "/" }]);
  const customers = [
    { id: "dan", client_code: "DTD-MAIN", company_name: "DAN", route_group: "DTD", is_main_customer: true },
    { id: "pinto", client_code: "MLD-MAIN", company_name: "PINTO THAI/AHMED ALMAN", route_group: "MLD", is_main_customer: true },
    { id: "paris", client_code: "PARIS-MAIN", company_name: "Paris Store", route_group: "PARIS", is_main_customer: true },
  ];
  let failRoutes = true;
  await page.route("**/backend/api/v3/**", async route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/order-routes")) return failRoutes
      ? route.fulfill({ status: 503, json: { detail: "Unavailable" } })
      : route.fulfill({ json: ["CD", "DTD", "ITM", "JCT", "LYT", "MLD", "NGA", "PARIS", "PRD", "TETE"] });
    if (url.pathname.endsWith("/customer-register")) {
      const q = (url.searchParams.get("q") || "").toLowerCase();
      const items = customers.filter(c => `${c.company_name} ${c.client_code}`.toLowerCase().includes(q));
      return route.fulfill({ json: { items, total: items.length, has_more: false } });
    }
    return route.fulfill({ json: url.pathname.endsWith("/auth/me")
      ? { full_name: "Order staff", role: "GENERAL", must_change_password: false, permissions: ["validate_orders"] }
      : url.pathname.endsWith("/operations/summary") ? {} : [] });
  });
  await page.goto("/orders");
  await expect(page.getByText("Saved routes could not be loaded.", { exact: false })).toBeVisible();
  failRoutes = false;
  await page.getByRole("button", { name: "Retry routes", exact: true }).click();
  const routeSelect = page.getByRole("combobox", { name: "Route", exact: true });
  await expect(routeSelect.locator("option[value='PRD']")).toHaveCount(1);
  const customer = page.getByRole("combobox", { name: "Billing customer", exact: true });
  await customer.selectOption("dan");
  await expect(routeSelect).toHaveValue("DTD");
  await routeSelect.selectOption("CD");
  await customer.selectOption("pinto");
  await expect(routeSelect).toHaveValue("MLD");
  await page.getByRole("searchbox", { name: "Find billing customer" }).fill("Paris");
  await expect(customer.locator("option[value='paris']")).toHaveCount(1);
  await customer.selectOption("paris");
  await expect(routeSelect).toHaveValue("PARIS");
  await page.getByRole("searchbox", { name: "Find billing customer" }).clear();
  await expect(customer).toBeEnabled();
  await expect(routeSelect).toHaveValue("PARIS");
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.screenshot({ path: "../outputs/order-customer-routes-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await routeSelect.scrollIntoViewIfNeeded();
  await page.screenshot({ path: "../outputs/order-customer-routes-mobile.png", fullPage: true });
});
