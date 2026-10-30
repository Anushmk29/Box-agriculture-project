import { expect, test, type Page } from "@playwright/test";

async function setup(page: Page, count = 26, suggestionCount = 1) {
  const token = Buffer.from(JSON.stringify({ exp: Date.now() / 1000 + 3600 })).toString("base64url");
  await page.context().addCookies([{ name: "access_token", value: `qa.${token}.qa`, domain: "127.0.0.1", path: "/" }]);
  const mappings: Record<string, unknown>[] = [];
  let searchFailures = 0;
  const suggestion = { id: "suggested", t_code: "GUAVA-ALT", name_en: "Pink guava", name_vi: "Guava", score: .75 };
  await page.route("**/backend/api/v3/**", async route => {
    const url = new URL(route.request().url()); const path = url.pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { user_id: "match-user", full_name: "Order staff", role: "GENERAL", permissions: ["validate_orders"], must_change_password: false } });
    if (path.endsWith("/customer-register")) return route.fulfill({ json: { items: [], total: 0, has_more: false } });
    if (path.endsWith("/orders/files/match-file")) return route.fulfill({ json: { raw_file_id: "match-file", file_name: "Guava order.xlsx", main_customer_id: "main", status: "PARSED", total_sub_customers_detected: 1, sub_customer_codes: ["SHOP"], sheets_detected: [], verification_notice: "Review this order." } });
    if (path.endsWith("/validate")) return route.fulfill({ json: { can_split: mappings.length > 0, quantity_issue_count: 0, unresolved: mappings.length ? [] : [{ product_code: "T298", name_vi: "Order guava", name_en: "RED GUAVA", pack_size: "loose", suggestions: Array.from({ length: suggestionCount }, (_, i) => ({ ...suggestion, id: i ? `suggested-${i}` : suggestion.id, name_en: i ? `Guava variety ${i}` : suggestion.name_en })) }] } });
    if (path.endsWith("/master-data/products")) {
      const query = url.searchParams.get("q");
      if (query === "error" && searchFailures++ === 0) return route.fulfill({ status: 503, json: { detail: "Catalog is temporarily unavailable" } });
      if (query === "no-results") return route.fulfill({ json: [] });
      const offset = Number(url.searchParams.get("offset")) || 0;
      return route.fulfill({ json: Array.from({ length: offset ? 1 : count }, (_, i) => ({ id: `product-${offset + i}`, t_code: `T${String(offset + i + 1).padStart(3, "0")}`, name_en: `RED GUAVA ${offset + i + 1}`, name_vi: `Guava variety ${offset + i + 1}` })) });
    }
    if (path.endsWith("/master-data/sku-mappings")) { mappings.push(route.request().postDataJSON()); return route.fulfill({ json: {} }); }
    return route.fulfill({ json: path.endsWith("/operations/summary") ? {} : [] });
  });
  await page.goto("/orders?fileId=match-file");
  await page.getByRole("button", { name: "Match product", exact: true }).click();
  return mappings;
}

test("matching keeps source, choices and save action visible across screen sizes", async ({ page }) => {
  const writes = await setup(page);
  await page.emulateMedia({ reducedMotion: "reduce" });
  const dialog = page.getByRole("dialog", { name: "Match to a catalog product", exact: true });
  await expect(dialog.getByRole("radio")).toHaveCount(26);
  await expect(dialog.getByRole("region", { name: "Next step guidance" })).toHaveCount(0);
  await expect(dialog.locator(".mapping-steps [aria-current]")).toHaveText("2Choose a catalog product");
  for (const [width, height] of [[1366, 768], [1536, 730], [1024, 768], [390, 844]]) {
    await page.setViewportSize({ width, height });
    await expect(dialog.getByRole("button", { name: "Save match", exact: true })).toBeInViewport({ ratio: 1 });
    await expect(dialog.getByRole("searchbox", { name: "Search the catalog", exact: true })).toBeInViewport({ ratio: 1 });
    const dimensions = await dialog.evaluate(element => ({
      right: element.getBoundingClientRect().right, bottom: element.getBoundingClientRect().bottom,
      bodyOverflow: element.querySelector(".task-dialog-body")!.scrollHeight - element.querySelector(".task-dialog-body")!.clientHeight,
      resultsHeight: element.querySelector(".mapping-result-list")!.clientHeight,
    }));
    expect(dimensions.right).toBeLessThanOrEqual(width);
    expect(dimensions.bottom).toBeLessThanOrEqual(height);
    expect(dimensions.bodyOverflow).toBeLessThanOrEqual(2);
    expect(dimensions.resultsHeight).toBeGreaterThan(70);
    expect(await dialog.getByRole("searchbox").evaluate(element => parseFloat(getComputedStyle(element).paddingLeft))).toBeGreaterThanOrEqual(38);
    await page.screenshot({ path: `../outputs/product-match-${width}.png`, animations: "disabled" });
  }
  await dialog.getByRole("radio").first().check();
  await expect(dialog.locator(".mapping-steps [aria-current]")).toHaveText("3Save the match");
  await expect(dialog.locator(".mapping-save-status")).toContainText("T001");
  await expect(dialog.getByRole("button", { name: "Save match", exact: true })).toBeEnabled();
  expect(writes).toEqual([]);
});

test("five suggestions remain available when the initial search finds no results", async ({ page }) => {
  const writes = await setup(page, 0, 5);
  const dialog = page.getByRole("dialog", { name: "Match to a catalog product", exact: true });
  const suggestions = dialog.getByRole("region", { name: "Suggested products", exact: true });
  await expect(suggestions.getByRole("button")).toHaveCount(5);
  await expect(suggestions.getByRole("button").first()).toBeInViewport();
  await expect(dialog.getByRole("button", { name: "Save match", exact: true })).toBeDisabled();
  await expect(dialog.getByRole("button", { name: "Next products", exact: true })).toHaveCount(0);
  await page.setViewportSize({ width: 1366, height: 768 });
  await page.screenshot({ path: "../outputs/product-match-suggestions.png", animations: "disabled" });
  await suggestions.getByRole("button").last().click();
  await expect(dialog.locator(".mapping-save-status")).toContainText("Guava variety 4");
  await expect(dialog.getByRole("button", { name: "Save match", exact: true })).toBeInViewport({ ratio: 1 });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(dialog.getByRole("button", { name: "Save match", exact: true })).toBeInViewport({ ratio: 1 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  expect(writes).toEqual([]);
});

test("selection survives catalog paging and is saved only by explicit confirmation", async ({ page }) => {
  const writes = await setup(page, 100);
  const dialog = page.getByRole("dialog", { name: "Match to a catalog product", exact: true });
  await dialog.getByRole("radio").first().check();
  await dialog.getByRole("button", { name: "Next products", exact: true }).click();
  await expect(dialog.getByRole("radio")).toHaveCount(1);
  await expect(dialog.getByRole("radio")).toHaveValue("product-100");
  await expect(dialog.locator(".mapping-save-status")).toContainText("T001");
  expect(writes).toEqual([]);
  await dialog.getByRole("button", { name: "Save match", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(writes).toEqual([expect.objectContaining({ product_id: "product-0", client_sku_code: "T298", main_customer_id: "main", standard_pack_size: "loose" })]);
});

test("a suggestion can be chosen without being in the current results", async ({ page }) => {
  const writes = await setup(page);
  const dialog = page.getByRole("dialog", { name: "Match to a catalog product", exact: true });
  await expect(dialog.getByRole("region", { name: "Suggested products", exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: /GUAVA-ALT/ }).click();
  await expect(dialog.locator(".mapping-save-status")).toContainText("GUAVA-ALT");
  await dialog.getByRole("button", { name: "Save match", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(writes[0]).toMatchObject({ product_id: "suggested" });
});

test("catalog failures offer retry and empty searches explain the next action", async ({ page }) => {
  await setup(page);
  const dialog = page.getByRole("dialog", { name: "Match to a catalog product", exact: true });
  const search = dialog.getByRole("searchbox", { name: "Search the catalog", exact: true });
  await search.fill("error");
  await expect(dialog.getByRole("alert")).toContainText("Catalog is temporarily unavailable");
  await expect(dialog.getByRole("button", { name: "Add it as a new product", exact: true })).toHaveCount(0);
  await dialog.getByRole("button", { name: "Retry search", exact: true }).click();
  await expect(dialog.getByRole("radio")).toHaveCount(26);
  await search.fill("no-results");
  await expect(dialog.getByText("No results for this search", { exact: true })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Previous products", exact: true })).toHaveCount(0);
  await dialog.getByRole("button", { name: "Show all products", exact: true }).click();
  await expect(search).toHaveValue("");
  await expect(dialog.getByRole("radio")).toHaveCount(26);
  await search.fill("no-results");
  await expect(dialog.getByText("No results for this search", { exact: true })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Save match", exact: true })).toBeDisabled();
  await dialog.getByRole("button", { name: "Add it as a new product", exact: true }).click();
  await expect(dialog.getByRole("group", { name: "Product identity", exact: true })).toBeVisible();
  await expect(dialog.getByRole("group", { name: "Packing & storage", exact: true })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Create product and match", exact: true })).toBeInViewport({ ratio: 1 });
  await page.screenshot({ path: "../outputs/product-match-create-desktop.png", animations: "disabled" });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(dialog.getByRole("button", { name: "Create product and match", exact: true })).toBeInViewport({ ratio: 1 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({ path: "../outputs/product-match-create-mobile.png", animations: "disabled" });
});

test("a late catalog response cannot replace a newer search", async ({ page }) => {
  await setup(page);
  let releaseSlow!: () => void;
  const gate = new Promise<void>(resolve => { releaseSlow = resolve; });
  let finishSlow!: () => void;
  const finished = new Promise<void>(resolve => { finishSlow = resolve; });
  await page.route("**/backend/api/v3/master-data/products?*", async route => {
    const query = new URL(route.request().url()).searchParams.get("q");
    if (query === "slow") {
      await gate;
      await route.fulfill({ json: [{ id: "stale", t_code: "OLD", name_en: "Stale result", name_vi: "" }] }).catch(() => {});
      finishSlow(); return;
    }
    if (query === "latest") return route.fulfill({ json: [{ id: "latest", t_code: "NEW", name_en: "Latest result", name_vi: "" }] });
    await route.fallback();
  });
  const search = page.getByRole("searchbox", { name: "Search the catalog", exact: true });
  const slowRequest = page.waitForRequest(request => request.url().includes("q=slow"));
  await search.fill("slow"); await slowRequest;
  await search.fill("latest");
  await expect(page.getByRole("radio", { name: /Latest result/ })).toBeVisible();
  releaseSlow(); await finished;
  await expect(page.getByRole("radio", { name: /Latest result/ })).toHaveCount(1);
  await expect(page.getByRole("radio", { name: /Stale result/ })).toHaveCount(0);
});
