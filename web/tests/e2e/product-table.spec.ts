import { expect, test, type Page } from "@playwright/test";

async function setup(page: Page) {
  const jwt = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  await page.context().addCookies([{ name: "access_token", value: `qa.${jwt}.qa`, domain: "127.0.0.1", path: "/" }]);
  const rows = ["11-001", "11-002"].map((t_code, i) => ({ id: String(i), t_code, name_en: i ? "Kale" : "Holy basil", name_th: "ผักสด", packing_revision: 1, box_material: "FOAM", temperature: "COLD", control_status: i ? null : "EL", produce_type: "HERB", weight_class: "LIGHT", firmness: null, volume_l_per_kg: 6, volume_estimated: true, ready: false, problems: ["firmness"], limits: [], source_choices: [], grouping_row: null, identity_choices: [], limit_issues: [] }));
  const state = { conflict: false, delay: 0, saves: [] as unknown[], rows };
  await page.route("**/backend/api/v3/**", async route => {
    const url = new URL(route.request().url()), path = url.pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { id: "staff", email: "staff@example.com", full_name: "Staff", role: "OWNER_ADMIN", permissions: ["floor_packing", "design_packing"], must_change_password: false } });
    if (path.endsWith("/packing-setup/boxes")) return route.fulfill({ json: [] });
    if (path.endsWith("/packing-setup/table/bulk")) {
      const body = route.request().postDataJSON();
      state.saves.push(body);
      return route.fulfill({ json: { results: body.edits.map((edit: { id: string; changes: object }) => state.conflict ? { id: edit.id, status: 409, error: "Changed by another staff member. Reload this row and reapply your changes.", current: rows.find(r => r.id === edit.id) } : { id: edit.id, product: Object.assign(rows.find(r => r.id === edit.id)!, edit.changes, { packing_revision: 2 }) }) } });
    }
    if (path.endsWith("/packing-setup/table") && route.request().method() === "POST") return route.fulfill({ status: 201, json: {} });
    if (path.endsWith("/packing-setup/table")) {
      if (state.delay) await new Promise(resolve => setTimeout(resolve, state.delay));
      const q = url.searchParams.get("q") || "";
      const items = rows.filter(r => r.t_code.includes(q) || r.name_en.toLowerCase().includes(q.toLowerCase()));
      return route.fulfill({ json: { items, total: items.length, has_more: false, summary: { total: 818, ready: 0, strict: false } } });
    }
    return route.fulfill({ json: [] });
  });
  return state;
}

test("six columns support keyboard edits and save multiple rows without pack-size fields", async ({ page }) => {
  const state = await setup(page);
  await page.goto("/packing/setup");
  await expect(page.getByRole("columnheader")).toHaveText(["Product", "Box / temperature", "EL", "Handling", "VolumeOccupied space per kg", "Box limits"]);
  await page.getByLabel("11-001 litres per kg").fill("7");
  await page.getByLabel("11-001 litres per kg").press("Tab");
  await expect(page.getByLabel("11-002 confirm settings")).toBeFocused();
  await page.getByLabel("11-002 Firmness").selectOption("SOFT");
  await expect(page.getByRole("status").filter({ hasText: "2 unsaved rows" })).toBeVisible();
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(page.getByText("2 products saved.", { exact: true })).toBeVisible();
  expect(state.saves).toHaveLength(1);
  expect(JSON.stringify(state.saves)).not.toMatch(/profiles|source_url|aliases|pack_size/);
  await expect(page.getByText("Source URL", { exact: true })).toHaveCount(0);
});

test("unknown EL stays indeterminate until staff choose and estimates need explicit confirmation", async ({ page }) => {
  const state = await setup(page);
  await page.goto("/packing/setup");
  await expect(page.getByLabel("11-002 EL classification")).toHaveAttribute("aria-checked", "mixed");
  await page.getByRole("button", { name: "Set Non-EL" }).click();
  await page.getByLabel("11-002 Firmness").selectOption("SOFT");
  await page.getByLabel("11-002 confirm settings").check();
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(page.getByText("1 product saved.", { exact: true })).toBeVisible();
  expect(JSON.stringify(state.saves)).toContain('"control_status":"COMBINABLE"');
  expect(JSON.stringify(state.saves)).toContain('"confirm":true');
});

test("conflicting saves preserve the edited value and show a reload action", async ({ page }) => {
  const state = await setup(page); state.conflict = true;
  await page.goto("/packing/setup");
  await page.getByLabel("11-001 litres per kg").fill("9");
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Changed by another staff member" })).toBeVisible();
  await expect(page.getByLabel("11-001 litres per kg")).toHaveValue("9");
  await page.getByRole("button", { name: "Reload this row" }).click();
  await expect(page.getByLabel("11-001 litres per kg")).toHaveValue("6");
});

test("draft edits survive search and new products use an inline row", async ({ page }) => {
  await setup(page);
  await page.goto("/packing/setup");
  await page.getByLabel("11-001 litres per kg").fill("8");
  await page.getByLabel("Find product").fill("Kale");
  await expect(page.getByLabel("11-001 litres per kg")).toHaveCount(0);
  await page.getByLabel("Find product").fill("");
  await expect(page.getByLabel("11-001 litres per kg")).toHaveValue("8");
  await page.getByRole("button", { name: "Add product", exact: true }).click();
  await page.getByLabel("New SKU").fill("NEW");
  await page.getByLabel("New English name").fill("New item");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Save new product" })).toBeEnabled();
});

test("worksheet skeleton and horizontal scrolling work on a phone", async ({ page }) => {
  const state = await setup(page); state.delay = 700;
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/packing/setup");
  await expect(page.getByRole("region", { name: "Editable product worksheet" })).toHaveAttribute("aria-busy", "true");
  await expect(page.getByLabel("11-001 litres per kg")).toBeAttached();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const region = page.getByRole("region", { name: "Editable product worksheet" });
  await region.evaluate(node => { node.scrollLeft = node.scrollWidth; });
  await expect(page.getByRole("columnheader", { name: "Box limits", exact: true })).toBeVisible();
  await page.screenshot({ path: "../outputs/product-table-mobile.png", fullPage: true });
  await page.setViewportSize({ width: 1600, height: 1000 });
  await region.evaluate(node => { node.scrollLeft = 0; });
  await page.screenshot({ path: "../outputs/product-table-desktop.png", fullPage: true });
});

test("temperature selects its material and never offers an incompatible pair", async ({ page }) => {
  const state = await setup(page);
  await page.goto("/packing/setup");
  const temperature = page.getByLabel("11-001 box and temperature");
  await expect(temperature.locator("option")).toHaveText(["Choose temperature", "Cold / Foam", "Normal (ambient) / Cardboard"]);
  await temperature.selectOption("NORMAL");
  await expect(page.locator("tr").filter({ has: temperature }).locator(".product-material-badge")).toContainText("Cardboard box");
  await expect(page.getByText("Foam allowed below (kg)", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(page.getByText("1 product saved.", { exact: true })).toBeVisible();
  expect(state.saves).toEqual([{ edits: [{ id: "0", expected_revision: 1, changes: { confirm: false, temperature: "NORMAL", box_material: "CARDBOARD" } }] }]);
});

test("temperature coverage filters unknown products without guessing from defaults", async ({ page }) => {
  const state = await setup(page);
  const requests: string[] = [];
  await page.route("**/backend/api/v3/packing-setup/table?**", route => {
    const temperature = new URL(route.request().url()).searchParams.get("temperature");
    requests.push(temperature || "all");
    const items = [
      { ...state.rows[0], source_choices: [{ row: 7, name: "Holy basil" }], temperature_source: "WORKBOOK" },
      { ...state.rows[1], temperature: null, box_material: null, temperature_source: "UNRESOLVED" },
    ].filter(row => temperature === "unknown" ? row.temperature === null : true);
    return route.fulfill({ json: { items, total: items.length, has_more: false, summary: { total: 818, ready: 0, strict: false, cold: 332, ambient: 17, temperature_unknown: 469, grouping_matched: 349 } } });
  });
  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.goto("/packing/setup");
  await expect(page.getByText("Grouping list row 7", { exact: true })).toBeVisible();
  await page.screenshot({ path: "../outputs/product-temperature-desktop.png", fullPage: true });
  await page.getByRole("combobox", { name: "Temperature", exact: true }).selectOption("unknown");
  await expect(page.getByLabel("11-001 box and temperature")).toHaveCount(0);
  await expect(page.getByLabel("11-002 box and temperature")).toHaveValue("");
  expect(requests).toContain("unknown");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: "../outputs/product-temperature-mobile.png", fullPage: true });
});
