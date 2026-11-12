import { expect, test, type Page } from "@playwright/test";
import { translateText } from "../../lib/translations";

const products = [
  { id: "durian", t_code: "D001", name_en: "Durian", name_vi: "Sau rieng", name_th: "ทุเรียน" },
  { id: "guava", t_code: "G001", name_en: "Guava", name_vi: "Oi", name_th: "ฝรั่ง" },
  { id: "basil", t_code: "B001", name_en: "Holy basil", name_vi: "Hung", name_th: "กะเพรา" },
  { id: "papaya", t_code: "P001", name_en: "Green papaya", name_vi: "Du du", name_th: "มะละกอ" },
];

async function setup(page: Page, options: { locked?: boolean; conflict?: boolean; readFailure?: boolean; searchFailure?: boolean } = {}) {
  const token = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  await page.context().addCookies([{ name: "access_token", value: `qa.${token}.qa`, domain: "127.0.0.1", path: "/" }]);
  const data = {
    box_id: "box-1", packing_revision: 7, qr_code_uid: "QR-1", box_number: 1, order_id: "order-1",
    file_name: "Order YACHI.xlsx", order_ref_id: "internal-id", customer_code: "YACHI", route_code: "TETE", shipment_date: "2026-11-06",
    order_status: options.locked ? "HANDOFF_02_RETURNED" : "FLOOR_PACKING", can_edit: !options.locked, is_locked: Boolean(options.locked),
    actual_net_weight_kg: null, actual_gross_weight_kg: null, needs_replan: false, layers: [], change_history: [],
    items: [{ item_id: "item-1", product_id: "durian", product_code: "D001", produce_name: "Durian", pack_size: "500g", packed_count: 5, net_weight_kg: 2.5 },
      { item_id: "item-2", product_id: "guava", product_code: "G001", produce_name: "Guava", pack_size: "100g", packed_count: 3, net_weight_kg: .3 }],
  };
  const writes: Array<{ expected_revision: number; item_id?: string; product_id: string; pack_size: string; quantity: number; net_weight_kg: number }> = [];
  let reads = 0, searchFailed = false;
  await page.route("**/backend/api/v3/**", async route => {
    const url = new URL(route.request().url()), path = url.pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { full_name: "Packer", role: "GENERAL", permissions: ["floor_packing"] } });
    if (path.includes("/packing/scan/")) {
      reads++;
      if (options.readFailure && writes.length) return route.fulfill({ status: 503, json: { detail: "Reload unavailable" } });
      return route.fulfill({ json: data });
    }
    if (path.endsWith("/master-data/products")) {
      if (options.searchFailure && !searchFailed) { searchFailed = true; return route.fulfill({ status: 503, json: { detail: "Search unavailable" } }); }
      return route.fulfill({ json: products });
    }
    if (path.endsWith("/boxes/box-1/items")) {
      const body = route.request().postDataJSON();
      writes.push(body);
      if (options.conflict) { data.packing_revision++; return route.fulfill({ status: 409, json: { detail: { code: "STALE_REVISION", message: "Someone changed this record. Reload and review." } } }); }
      expect(body.expected_revision).toBe(data.packing_revision);
      if (body.quantity === 0) data.items = data.items.filter(item => item.item_id !== body.item_id);
      else {
        const product = products.find(product => product.id === body.product_id)!;
        const item = { item_id: body.item_id || `added-${writes.length}`, product_id: product.id, product_code: product.t_code, produce_name: product.name_en, pack_size: body.pack_size, packed_count: body.quantity, net_weight_kg: body.net_weight_kg };
        const index = data.items.findIndex(item => item.item_id === body.item_id);
        if (index < 0) data.items.push(item); else data.items[index] = item;
      }
      data.packing_revision++;
      return route.fulfill({ json: { item_id: body.item_id || `added-${writes.length}` } });
    }
    return route.fulfill({ json: {} });
  });
  return { data, writes, reads: () => reads };
}

for (const width of [1440, 390]) {
  test(`scan edits, substitutes, adds and removes physical contents at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    const state = await setup(page);
    const ui = (text: string) => translateText(text, width === 390 ? "th" : "en");
    await page.goto("/scan/QR-1");
    if (width === 390) await page.getByRole("button", { name: "ภาษาไทย", exact: true }).filter({ visible: true }).click();
    await page.getByRole("button", { name: ui("Edit contents for Durian"), exact: true }).click();
    let dialog = page.getByRole("dialog", { name: ui("Adjust packing item"), exact: true });
    await dialog.getByLabel(ui("Packed quantity"), { exact: true }).fill("4");
    await dialog.getByLabel(ui("Total item net weight (kg)"), { exact: true }).fill("2");
    await dialog.getByRole("button", { name: ui("Save packing adjustment"), exact: true }).click();
    await expect(page.getByRole("row").filter({ hasText: "Durian" })).toContainText("2.00");
    await page.getByRole("button", { name: ui("Edit contents for Guava"), exact: true }).click();
    dialog = page.getByRole("dialog", { name: ui("Adjust packing item"), exact: true });
    await dialog.getByRole("combobox", { name: ui("Master product"), exact: true }).selectOption("basil");
    await dialog.getByRole("button", { name: ui("Save packing adjustment"), exact: true }).click();
    await expect(page.getByRole("row").filter({ hasText: "Holy basil" })).toBeVisible();
    await page.getByRole("button", { name: ui("Add packing item"), exact: true }).click();
    dialog = page.getByRole("dialog", { name: ui("Add packing item"), exact: true });
    await dialog.getByRole("combobox", { name: ui("Master product"), exact: true }).selectOption("papaya");
    await dialog.getByLabel(ui("Pack size"), { exact: true }).fill("200g");
    await dialog.getByLabel(ui("Packed quantity"), { exact: true }).fill("2");
    await dialog.getByLabel(ui("Total item net weight (kg)"), { exact: true }).fill("0.4");
    await dialog.getByRole("button", { name: ui("Save packing adjustment"), exact: true }).click();
    await expect(page.getByRole("row").filter({ hasText: "Green papaya" })).toBeVisible();
    await page.getByRole("button", { name: ui("Edit contents for Durian"), exact: true }).click();
    dialog = page.getByRole("dialog", { name: ui("Adjust packing item"), exact: true });
    await dialog.getByRole("button", { name: ui("Remove item"), exact: true }).click();
    await page.getByRole("dialog", { name: ui("Remove this packing item?"), exact: true }).getByRole("button", { name: ui("Remove item"), exact: true }).click();
    await expect(page.getByRole("row").filter({ hasText: "Durian" })).toHaveCount(0);
    expect(state.writes.map(write => write.expected_revision)).toEqual([7, 8, 9, 10]);
    expect(state.writes[3]).toMatchObject({ item_id: "item-1", product_id: "durian", quantity: 0, net_weight_kg: 0 });
    expect(state.data.items.map(item => item.product_id)).toEqual(["basil", "papaya"]);
    expect(state.reads()).toBe(5);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.evaluate(() => { (document.activeElement as HTMLElement)?.blur(); window.scrollTo(0, 0); });
    await page.screenshot({ path: `../outputs/scan-editing-${width}.png`, fullPage: true });
  });
}

test("stale scan edits keep the draft and require a reviewed reload", async ({ page }) => {
  const state = await setup(page, { conflict: true });
  await page.goto("/scan/QR-1");
  await page.getByRole("button", { name: "Edit contents for Durian", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Adjust packing item", exact: true });
  await dialog.getByLabel("Packed quantity", { exact: true }).fill("2");
  await dialog.getByRole("button", { name: "Save packing adjustment", exact: true }).click();
  await expect(dialog).toContainText("Someone changed this record");
  await expect(dialog.getByLabel("Packed quantity", { exact: true })).toHaveValue("2");
  await expect(dialog.getByRole("button", { name: "Save packing adjustment", exact: true })).toBeDisabled();
  await dialog.getByRole("button", { name: "Reload latest packing for review", exact: true }).click();
  await page.getByRole("dialog", { name: "Discard unsaved packing changes?", exact: true }).getByRole("button", { name: "Discard and reload", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await page.getByRole("button", { name: "Edit contents for Durian", exact: true }).click();
  await expect(dialog.getByLabel("Packed quantity", { exact: true })).toHaveValue("5");
  expect(state.writes).toHaveLength(1);
});

test("a saved item followed by a failed refresh blocks further writes", async ({ page }) => {
  const state = await setup(page, { readFailure: true });
  await page.goto("/scan/QR-1");
  await page.getByRole("button", { name: "Edit contents for Durian", exact: true }).click();
  await page.getByRole("dialog", { name: "Adjust packing item", exact: true }).getByRole("button", { name: "Save packing adjustment", exact: true }).click();
  await expect(page.getByText("Reload unavailable", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Add packing item", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /^Edit contents for/ })).toHaveCount(0);
  await page.getByRole("button", { name: "Reload latest packing for review", exact: true }).click();
  expect(state.writes).toHaveLength(1);
});

test("locked scans expose no product edits", async ({ page }) => {
  const state = await setup(page, { locked: true });
  await page.goto("/scan/QR-1");
  await expect(page.getByText("Final pack locked", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Add packing item", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /^Edit contents for/ })).toHaveCount(0);
  expect(state.writes).toEqual([]);
});

test("catalogue search retry preserves the current product", async ({ page }) => {
  await setup(page, { searchFailure: true });
  await page.goto("/scan/QR-1");
  await page.getByRole("button", { name: "Edit contents for Durian", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Adjust packing item", exact: true });
  await expect(dialog.getByRole("combobox", { name: "Master product", exact: true })).toHaveValue("durian");
  await dialog.getByRole("button", { name: "Retry search", exact: true }).click();
  await dialog.getByRole("combobox", { name: "Master product", exact: true }).selectOption("basil");
});
