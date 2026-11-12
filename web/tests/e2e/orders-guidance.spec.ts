import { expect, test, type Page, type BrowserContext } from "@playwright/test";

async function setup(page: Page, context: BrowserContext, options: { confirmed?: boolean; products?: boolean; quantities?: boolean; format?: boolean } = {}) {
  const jwt = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  await context.addCookies([{ name: "access_token", value: `qa.${jwt}.qa`, domain: "127.0.0.1", path: "/" }]);
  await page.route("**/backend/api/v3/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { full_name: "Order staff", role: "GENERAL", must_change_password: false, permissions: ["validate_orders"] } });
    if (path.endsWith("/customer-register")) return route.fulfill({ json: { items: [{ id: "alpha", client_code: "ALPHA", company_name: "Alpha Trading" }, { id: "beta", client_code: "BETA", company_name: "Beta Export" }], total: 2, has_more: false } });
    if (path.endsWith("/orders/files/order-ui")) return route.fulfill({ json: { raw_file_id: "order-ui", file_name: "Order P-PUI shipment ETD 07.08.2026.xlsx", total_sub_customers_detected: 1, sub_customer_codes: ["P-PUI"], sheets_detected: [], group_summary: { TETE: 1 }, status: options.confirmed ? "SPLIT_AND_VALIDATED" : "PARSED", verification_notice: "Check the original order before confirming." } });
    if (path.endsWith("/validate")) return route.fulfill({ json: { can_split: !options.products && !options.quantities && !options.format, quantity_issue_count: options.quantities || options.format ? 1 : 0, quantity_review: options.format ? { revision: "format-review", items: [], total: 1, issue_count: 1, unknown_unit_count: 0, has_more: false, template_issues: ["Review and confirm the customer Excel format before procurement"] } : undefined, unresolved: options.products ? [{ product_code: "UNKNOWN", name_en: "Sweet basil", pack_size: "100g" }, { product_code: "UNKNOWN-2", name_en: "Lemongrass", pack_size: "100g" }] : [] } });
    if (path.endsWith("/order-ui/orders")) return route.fulfill({ json: { items: [{ order_id: "child-1", customer_name: "P-PUI", customer_code: "P-PUI", order_ref_id: "ORD-20261025-P-PUI-001", shipment_date: "2026-10-25", route: "TETE", status: "FLOOR_PACKING", created_at: "2026-10-25T09:00:00Z" }], total: 1, offset: 0, limit: 25, has_more: false } });
    return route.fulfill({ json: path.endsWith("/operations/summary") ? {} : [] });
  });
}

test("format review comes before product matching and line corrections", async ({ page, context }) => {
  await setup(page, context, { format: true, products: true, quantities: true });
  await page.goto("/orders?fileId=order-ui");
  const next = page.locator("[data-next-action='true']");
  await expect(next).toHaveCount(1);
  await expect(next).toHaveText("Review customer Excel format");
  await expect(page.getByRole("region", { name: "Current task" })).toContainText("Confirm the customer Excel format first");
  await expect(page.locator(".order-review-check").first()).toContainText("Required first.");
  await expect(page.locator(".order-review-check").last()).toContainText("Waiting for Excel format.");
  await expect(page.getByRole("button", { name: "Review weights and pack counts", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Match product", exact: true }).first()).toBeDisabled();
  await expect(page.getByRole("button", { name: "Confirm orders and create purchase batch", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Show next action", exact: true }).click();
  await expect(next).toBeFocused();
  await expect(page.getByText("Confirm the customer Excel format first", { exact: true })).toHaveCount(2);
});

test("pausing hints survives reloading and opening another order", async ({ page, context }) => {
  await setup(page, context, { products: true });
  await page.goto("/orders?fileId=order-ui");
  await page.getByRole("button", { name: "Pause hints", exact: true }).click();
  await page.reload();
  await expect(page.getByRole("button", { name: "Resume hints", exact: true })).toBeVisible();
  await expect(page.locator("[data-next-action='true']")).toHaveCSS("animation-name", "none");
  await page.goto("/orders");
  await expect(page.getByRole("button", { name: "Resume hints", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Resume hints", exact: true }).click();
  await page.reload();
  await expect(page.getByRole("button", { name: "Pause hints", exact: true })).toBeVisible();
});

test("a confirmed file clearly separates the next action, linked orders and expandable original details", async ({ page, context }) => {
  await setup(page, context, { confirmed: true });
  await page.setViewportSize({ width: 1440, height: 1050 });
  await page.goto("/orders?fileId=order-ui");
  const next = page.locator("[data-next-action='true']");
  await expect(next).toHaveCount(1);
  await expect(next).toHaveText("Open purchase batch");
  await expect(page.getByRole("heading", { name: "Linked customer orders", exact: true })).toBeVisible();
  await expect(page.getByText("Check the original order before confirming.")).toHaveCount(0);
  const subcustomers = page.locator(".order-shops");
  await expect(subcustomers).not.toHaveAttribute("open");
  await subcustomers.locator("summary").focus();
  await page.keyboard.press("Enter");
  await expect(subcustomers).toHaveAttribute("open", "");
  await expect(subcustomers.locator(".code-cloud")).toContainText("P-PUI");
  await subcustomers.locator("summary").click();
  await expect(subcustomers).not.toHaveAttribute("open");
  await page.screenshot({ path: "../outputs/orders-ui-confirmed-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: "../outputs/orders-ui-confirmed-mobile.png", fullPage: true });
});

test("guidance highlights the actual upload field, then the customer, then the ready upload button", async ({ page, context }) => {
  await setup(page, context);
  await page.goto("/orders");
  const next = page.locator("[data-next-action='true']");
  await expect(next).toHaveCount(1);
  await expect(next).toHaveClass("file-drop");
  await page.getByRole("button", { name: "Show next action" }).click();
  await expect(page.locator("input[type=file]")).toBeFocused();
  await page.locator("input[type=file]").setInputFiles({ name: "order.xlsx", mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer: Buffer.from("mock file for the file-selection interface") });
  await expect(next).toHaveAccessibleName("Billing customer");
  await page.getByRole("combobox", { name: "Billing customer", exact: true }).selectOption("alpha");
  await expect(next).toHaveText("Upload and check order");
  await expect(next).toBeEnabled();
  await next.scrollIntoViewIfNeeded();
  await page.mouse.move(0, 0);
  await expect(next).toHaveAttribute("data-guidance-visible", "true");
  await expect(next).toHaveCSS("animation-name", "order-next-action");
  await page.getByRole("button", { name: "Pause hints", exact: true }).click();
  await expect(next).toHaveCSS("animation-name", "none");
  await page.getByRole("button", { name: "Resume hints", exact: true }).click();
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(next).toHaveCSS("animation-name", "none");
  await expect(next).toHaveCSS("outline-style", "solid");
  await expect(page.getByRole("combobox", { name: "Billing customer", exact: true })).toHaveCSS("appearance", "none");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: "../outputs/orders-ui-upload-mobile.png", fullPage: true });
});

for (const state of ["products", "quantities", "ready"] as const) {
  test(`pending ${state}: guide one available action and keep blocked confirmation quiet`, async ({ page, context }) => {
    await setup(page, context, { products: state === "products", quantities: state !== "ready" });
    await page.goto("/orders?fileId=order-ui");
    const next = page.locator("[data-next-action='true']");
    await expect(next).toHaveCount(1);
    await expect(next).toHaveText(state === "products" ? "Match product" : state === "quantities" ? "Review weights and pack counts" : "Confirm orders and create purchase batch");
    await expect(page.locator("#order-review .order-review-check")).toHaveCount(1);
    await expect(page.locator("#order-review").getByRole("button", { name: "Review customer Excel format", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Review weights and pack counts", exact: true })).toBeEnabled();
    if (state === "ready") {
      await expect(page.locator(".order-review-check").last()).toContainText("No quantity issues.");
      await expect(page.locator(".order-review-check").last()).toContainText("viewing is optional");
    } else {
      await expect(page.locator(".order-review-check").last()).toContainText("Required: 1 issue.");
      await expect(page.locator(".order-review-check").last()).toContainText("before confirming");
    }
    const confirm = page.getByRole("button", { name: "Confirm orders and create purchase batch", exact: true });
    if (state !== "ready") {
      await expect(confirm).toBeDisabled();
      await expect(confirm).not.toHaveAttribute("data-next-action");
    } else await expect(confirm).toBeEnabled();
    if (state === "products") {
      await next.hover();
      await expect(next).toHaveCSS("animation-name", "none");
    }
    await page.getByRole("button", { name: "Show next action", exact: true }).click();
    await expect(next).toBeFocused();
    await expect(next).toHaveCSS("animation-name", "none");
    await expect(page.locator(".order-shops")).not.toHaveAttribute("open");
    if (state === "products") {
      await page.screenshot({ path: "../outputs/orders-ui-review-desktop.png", fullPage: true });
      await next.click();
      await expect(page.getByRole("dialog", { name: "Match to a catalog product", exact: true })).toBeVisible();
      await expect(next).toHaveCSS("animation-name", "none");
      await page.keyboard.press("Escape");
      await expect(next).toBeFocused();
    }
  });
}

test("a large pending order keeps review work and support tools in one desktop viewport", async ({ page, context }) => {
  await setup(page, context, { products: true, quantities: true });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.route("**/backend/api/v3/orders/files/order-ui", route => route.fulfill({ json: {
    raw_file_id: "order-ui", file_name: "Order Tonio 10.07.26 FULL.xlsx", status: "PARSED",
    total_sub_customers_detected: 68, sub_customer_codes: Array.from({ length: 68 }, (_, i) => `SHOP-${i + 1}`),
    sheets_detected: [], group_summary: { BE: 19, CT: 10, PL: 8, OTHER: 31 },
    verification_notice: "Found 68 subcustomers across 11 sheets. Please verify before confirming split.",
  } }));
  await page.route("**/backend/api/v3/orders/order-ui/validate", route => route.fulfill({ json: {
    can_split: false, quantity_issue_count: 20,
    unresolved: Array.from({ length: 8 }, (_, i) => ({ product_code: `T${i + 1}`, name_en: `Product requiring catalog match ${i + 1}`, pack_size: "100g" })),
  } }));
  for (const [width, height] of [[1366, 768], [1536, 730], [1920, 900], [1100, 700]]) {
    await page.setViewportSize({ width, height });
    await page.goto("/orders?fileId=order-ui");
    await expect(page.locator(".unmatched-products li")).toHaveCount(8);
    const confirm = page.getByRole("button", { name: "Confirm orders and create purchase batch", exact: true });
    await expect(confirm).toBeInViewport({ ratio: 1 });
    await expect(page.getByRole("button", { name: "Review weights and pack counts", exact: true })).toBeInViewport({ ratio: 1 });
    await expect(page.locator(".order-shops")).not.toHaveAttribute("open");
    const dimensions = await page.evaluate(() => ({ height: document.documentElement.scrollHeight, width: document.documentElement.scrollWidth }));
    expect(dimensions.height, `${width}px page height`).toBeLessThanOrEqual(height + 2);
    expect(dimensions.width).toBeLessThanOrEqual(width);
    expect(await page.locator(".unmatched-products ul").evaluate(el => el.clientHeight)).toBeGreaterThan(140);
    const checks = await page.locator(".order-review-check").evaluateAll(elements => elements.map(element => ({
      button: element.querySelector("button")!.getBoundingClientRect().toJSON(),
      text: element.querySelector(":scope > p")!.getBoundingClientRect().toJSON(),
    })));
    expect(checks).toHaveLength(1);
    expect(checks[0].button.width).toBeGreaterThan(200);
    expect(Math.abs(checks[0].button.top + checks[0].button.height / 2 - checks[0].text.top - checks[0].text.height / 2)).toBeLessThan(2);
    await page.getByRole("button", { name: "Show next action", exact: true }).click();
    await expect(page.locator("[data-next-action='true']")).toBeFocused();
    await page.locator(".order-shops summary").click();
    await expect(page.locator(".order-shops .code-cloud span")).toHaveCount(68);
    expect(await page.evaluate(() => document.documentElement.scrollHeight)).toBeLessThanOrEqual(height + 2);
    await page.locator(".order-shops summary").click();
    await page.screenshot({ path: `../outputs/order-review-desk-${width}.png`, animations: "disabled" });
  }
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({ path: "../outputs/order-review-desk-mobile.png", fullPage: true, animations: "disabled" });
});

for (const width of [1440, 390]) {
  test(`support actions open focused modals and preserve drafts at ${width}px`, async ({ page, context }) => {
    await setup(page, context);
    await page.setViewportSize({ width, height: 900 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/orders?fileId=order-ui");
    for (const title of ["Upload another order", "Add More Products", "Order file format guide", "File tools"]) {
      const section = page.locator(".order-disclosure").filter({ has: page.locator("summary").filter({ hasText: title }) });
      await expect(section).not.toHaveAttribute("open");
      await expect(section.locator("summary")).toHaveText(title);
      await expect(section.locator(".order-disclosure-description")).not.toBeVisible();
      await section.locator("summary").click();
      await expect(section.locator(".order-disclosure-description")).toBeVisible();
      const trigger = page.getByRole("button", { name: title, exact: true });
      await expect(trigger).toHaveAttribute("aria-haspopup", "dialog");
      await trigger.click();
      const dialog = page.getByRole("dialog", { name: title, exact: true });
      await expect(dialog).toBeVisible();
      expect(await dialog.evaluate(el => el.matches(":modal"))).toBe(true);
      await expect.poll(() => dialog.evaluate(el => el.contains(document.activeElement))).toBe(true);
      if (title === "Upload another order") {
        await dialog.locator("input[type=file]").setInputFiles({ name: "another.xlsx", mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer: Buffer.from("draft") });
        await dialog.getByRole("combobox", { name: "Billing customer", exact: true }).selectOption("alpha");
      }
      if (title === "Add More Products") await dialog.getByLabel("Produce Item (Code or Name)").fill("T001");
      if (title === "Order file format guide") await expect(dialog.getByRole("heading", { name: "Single customer order", exact: true })).toBeVisible();
      if (title === "File tools") await expect(dialog.getByRole("button", { name: "Download original file", exact: true })).toBeVisible();
      expect(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
      await page.keyboard.press("Escape");
      await expect(dialog).toHaveCount(0);
      await expect(trigger).toBeFocused();
    }
    await page.getByRole("button", { name: "Upload another order", exact: true }).click();
    const upload = page.getByRole("dialog", { name: "Upload another order", exact: true });
    await expect(upload.getByText("another.xlsx", { exact: true })).toBeVisible();
    await expect(upload.getByRole("combobox", { name: "Billing customer", exact: true })).toHaveValue("alpha");
    expect(await upload.locator("input[type=file]").evaluate(el => (el as HTMLInputElement).validity.valid)).toBe(true);
    await upload.getByRole("button", { name: "Close Upload another order", exact: true }).click();
    await page.getByRole("button", { name: "Add More Products", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "Add More Products", exact: true }).getByLabel("Produce Item (Code or Name)")).toHaveValue("T001");
    await page.screenshot({ path: `../outputs/order-action-modal-${width}.png`, animations: "disabled" });
  });

  test(`optional Excel format is tucked into File tools at ${width}px`, async ({ page, context }) => {
    await setup(page, context);
    await page.setViewportSize({ width, height: 900 });
    await page.route("**/backend/api/v3/order-templates/order-ui", route => route.fulfill({ json: { revision: "format", formats: [], detected: [], issues: [] } }));
    await page.goto("/orders?fileId=order-ui");
    await expect(page.getByRole("button", { name: "Confirm orders and create purchase batch", exact: true })).toBeEnabled();
    await expect(page.locator("#order-review").getByText("Optional.", { exact: true })).toHaveCount(0);
    const tools = page.locator(".order-file-reference");
    await expect(tools).not.toHaveAttribute("open");
    await tools.locator("summary").click();
    await expect(tools.getByRole("button", { name: "File tools", exact: true })).toContainText("File tools");
    await tools.getByRole("button", { name: "File tools", exact: true }).click();
    await page.getByRole("dialog", { name: "File tools", exact: true }).getByRole("button", { name: "Adjust Excel format", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "Customer Excel format", exact: true })).toBeVisible();
  });

  test(`Orders has a nested file-browser link that restores list filters at ${width}px`, async ({ page, context }) => {
    await setup(page, context);
    await page.setViewportSize({ width, height: 900 });
    const returnTo = "/orders/files?view=all&search=Tonio";
    await page.goto(`/orders?fileId=order-ui&returnTo=${encodeURIComponent(returnTo)}`);
    await expect(page.locator(".order-summary-bottom")).toBeVisible();
    await expect(page.locator("main").getByRole("link", { name: "Browse order files", exact: true })).toHaveCount(0);
    if (width === 390) await page.getByRole("button", { name: "Menu", exact: true }).click();
    const navigation = page.getByRole("navigation", { name: "Primary navigation", exact: true }).filter({ visible: true });
    await expect(navigation.getByRole("link", { name: /^Orders/ })).toHaveAttribute("aria-current", "page");
    await navigation.getByRole("link", { name: "Browse order files", exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/orders/files\\?view=all&search=Tonio$`));
    if (width === 390) {
      await expect(page.getByRole("dialog", { name: "Workspaces", exact: true })).toHaveCount(0);
      await page.getByRole("button", { name: "Menu", exact: true }).click();
    }
    await expect(navigation.getByRole("link", { name: "Browse order files", exact: true })).toHaveAttribute("aria-current", "page");
    await expect(navigation.getByRole("link", { name: /^Orders/ })).not.toHaveAttribute("aria-current");
  });
}
