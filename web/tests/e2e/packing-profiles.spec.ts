import { expect, test, type Page } from "@playwright/test";
type DraftProfile = { pack_size: string; aliases: string[]; net_weight_kg: number | null; volume_cm3: number | null; reviewed: boolean; evidence?: { volume_source?: string; estimate_kind?: string; estimate_low_cm3?: number; estimate_high_cm3?: number; source_url?: string; source_note?: string; measurement?: { pack_count: number } } };

async function mockSetup(page: Page, initialStatus = "HANDOFF_01_SENT") {
  const jwt = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  await page.context().addCookies([{ name: "access_token", value: `qa.${jwt}.qa`, domain: "127.0.0.1", path: "/" }]);
  const product = { id: "basil", t_code: "BASIL", name_en: "Holy basil", name_vi: "Basil", name_th: "Basil", produce_type: "HERB", box_material: "FOAM", temperature: "COLD", control_status: "EL", weight_class: "LIGHT", botanical_family: "Lamiaceae", firmness: null as string | null, packing_reviewed: false, packing_revision: 1, packing_notes: null, source_rows: [], profiles: [] as Array<{ pack_size: string; net_weight_kg: number; volume_cm3: number; reviewed: boolean }>, limits: [], default_pack_sizes: ["100g"], source_matches: [{ row: 7, name: "Holy basil", notes: "Large foam 4 kg", box_material: "FOAM", temperature: "COLD", control_status: "EL", weight_class: "LIGHT" }], source_conflict: false, ready: false, problems: ["Review measurements"], suggestions: { produce_type: "HERB", firmness: "SOFT" } };
  const boxType = { id: "small", size_name: "Small", box_type: "FOAM", length_cm: 38, width_cm: 28, height_cm: 34, max_safe_net_weight_kg: 5, packing_revision: 1, packing_reviewed: false, is_active: true, usable_volume_cm3: null as number | null };
  const box = { box_id: "box-1", box_number: 1, box_type_name: "Small", box_catalogue_id: "small", box_material: "FOAM", safe_net_capacity_kg: 5, allocation_explanation: "Reviewed profiles and staff-selected rules", is_overridden: false, actual_net_weight_kg: null, actual_gross_weight_kg: null, is_locked: false, qr_code_uid: "QR-1", sticker_label: "Basil box", packing_mode: "EL", needs_replan: false, replan_reasons: ["Packing data changed; preview a reviewed replan"], volume_cm3: 8000, usable_volume_cm3: 16000, volume_utilization_pct: 50, items: [{ item_id: "item", product_id: "basil", product_code: "BASIL", produce_name: "Holy basil", pack_size: "100g", packed_count: 4, net_weight_kg: .4, adjustment_type: "AS_ORDERED" }], layers: [{ item_id: "item", product_code: "BASIL", product_name: "Holy basil", pack_size: "100g", packed_count: 4, firmness: "SOFT", layer: "Top" }] };
  const state = { prepares: 0, modeChanges: 0, previews: 0, applies: 0, stages: 0, failApply: false, status: initialStatus, mode: "", hasBoxes: initialStatus !== "HANDOFF_01_SENT" };
  const research = { version: "2026-10-23", profiles: [] as DraftProfile[], density_conflict: false };
  Object.assign(product, { research });
  await page.route("**/backend/api/v3/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { full_name: "Packing Staff", role: "GENERAL", must_change_password: false, permissions: ["floor_packing", "design_packing"] } });
    if (path.endsWith("/operations/summary")) return route.fulfill({ json: {} });
    if (path.endsWith("/packing-setup/table")) return route.fulfill({ json: { items: [], total: 0, has_more: false, summary: { total: 818, ready: 0, strict: false } } });
    if (path.endsWith("/reference-drafts")) {
      if (route.request().method() === "POST") { state.stages++; return route.fulfill({ json: { profiles_added: 2, products: 1 } }); }
      return route.fulfill({ json: { revision: "a".repeat(64), products: 1, pack_profiles: 2, with_volume_estimate: 1 } });
    }
    if (path.endsWith("/packing-setup/products")) return route.fulfill({ json: { items: [product], total: 1, has_more: false, summary: { total_skus: 818, ready_skus: product.ready ? 1 : 0, matched_skus: 344, conflicting_skus: 33, strict_profiles_enabled: true, reference_rows: 283 } } });
    if (path.endsWith("/packing-setup/products/basil")) {
      const body = route.request().postDataJSON();
      Object.assign(product, body, { packing_revision: product.packing_revision + 1, ready: true });
      return route.fulfill({ json: product });
    }
    if (path.endsWith("/packing-setup/boxes")) return route.fulfill({ json: [boxType] });
    if (path.endsWith("/packing-setup/boxes/small")) {
      const body = route.request().postDataJSON(); Object.assign(boxType, body);
      boxType.usable_volume_cm3 = body.length_cm * body.width_cm * body.height_cm * .92;
      return route.fulfill({ json: boxType });
    }
    if (path.endsWith("/reference")) return route.fulfill({ json: { items: product.source_matches } });
    if (path.endsWith("/master-data/boxes")) return route.fulfill({ json: [boxType] });
    if (path.endsWith("/prepare")) {
      state.prepares++; state.mode = route.request().postDataJSON().packing_mode;
      state.status = "FLOOR_PACKING"; state.hasBoxes = true; box.packing_mode = state.mode;
      return route.fulfill({ json: { status: state.status } });
    }
    if (path.endsWith("/packing-list")) return route.fulfill({ json: { client_order_id: "order", order_ref_id: "ORD-BASIL", status: state.status, packing_approval_status: "NONE", packing_review_note: null, sub_customer_code: "BASIL-SHOP", route_code: "NGA", shipment_date: "2026-10-23", total_boxes_count: state.hasBoxes ? 1 : 0, boxes: state.hasBoxes ? [box] : [] } });
    if (path.endsWith("/packing-mode")) { state.modeChanges++; box.packing_mode = route.request().postDataJSON().packing_mode; return route.fulfill({ json: {} }); }
    if (path.endsWith("/replan-preview")) {
      state.previews++;
      return route.fulfill({ json: { preview_token: `preview-${state.previews}`, packing_mode: route.request().postDataJSON().packing_mode, warning: "Applying replaces box assignments and stickers. Current packed quantities are preserved.", boxes: [{ box_number: 1, box_catalogue_id: "small", net_weight_kg: .4, volume_cm3: 8000, items: [{ product_id: "basil", product_name: "Holy basil", pack_size: "100g", count: 4 }] }] } });
    }
    if (path.endsWith("/replan-apply")) {
      state.applies++;
      if (state.failApply) return route.fulfill({ status: 409, json: { detail: "Packing data changed. Preview again before replacing boxes" } });
      box.box_id = "replacement"; box.needs_replan = false;
      return route.fulfill({ json: {} });
    }
    return route.fulfill({ json: [] });
  });
  return { product, boxType, box, state, research };
}

test("staff use fixed workbook external dimensions and review safe capacity without internal measurements", async ({ page }) => {
  const { boxType } = await mockSetup(page);
  await page.setViewportSize({ width: 1440, height: 1050 });
  await page.goto("/packing/setup");
  await page.getByRole("tab", { name: "Box catalogue", exact: true }).click();
  await expect(page.getByRole("button", { name: "Add box type", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Edit box Small FOAM" }).click();
  for (const [axis, value] of [["length", "38"], ["width", "28"], ["height", "34"]]) {
    await expect(page.getByLabel(`External ${axis} (cm)`)).toHaveValue(value);
    await expect(page.getByLabel(`External ${axis} (cm)`)).toHaveAttribute("readonly", "");
  }
  await expect(page.getByText("Internal packing dimensions (cm)", { exact: true })).toHaveCount(0);
  await page.screenshot({ path: "../outputs/external-box-editor-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await expect(page.getByLabel("External length (cm)")).toBeVisible();
  await page.screenshot({ path: "../outputs/external-box-editor-mobile.png", fullPage: true });
  await page.getByLabel("I reviewed the safe weight capacity").check();
  await page.getByRole("button", { name: "Save box type", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(boxType.length_cm).toBe(38);
  expect(boxType.usable_volume_cm3).toBeCloseTo(33281.92);
});

test("staff choose EL mode before generation and can change a box on any country route", async ({ page }) => {
  const { state } = await mockSetup(page);
  await page.goto("/packing?orderId=order");
  const generate = page.getByRole("button", { name: "Generate packing boxes", exact: true });
  await expect(generate).toBeDisabled();
  expect(state.prepares).toBe(0);
  await page.getByRole("combobox", { name: "Packing rules", exact: true }).selectOption("NON_EL");
  await generate.click();
  await expect(page.locator("#box-box-1")).toBeVisible();
  expect(state.mode).toBe("NON_EL");
  expect(state.prepares).toBe(1);
  await page.locator(".packing-box-details").evaluate(element => { (element as HTMLDetailsElement).open = true; });
  await expect(page.getByText("Pack from bottom to top", { exact: true })).toBeVisible();
  await page.getByLabel("Box 1 packing rules", { exact: true }).selectOption("EL");
  await expect(page.getByLabel("Box 1 packing rules", { exact: true })).toHaveValue("EL");
  expect(state.modeChanges).toBe(1);
  await expect(page.getByLabel(/scale (net|gross)/)).toHaveCount(0);
});

test("stale plans require review and a failed apply preserves the current boxes", async ({ page }) => {
  const { box, state } = await mockSetup(page, "FLOOR_PACKING");
  box.needs_replan = true; state.failApply = true;
  await page.goto("/packing?orderId=order");
  await expect(page.getByRole("button", { name: "Finalize Packing", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Preview replan", exact: true }).click();
  await page.getByRole("button", { name: "Apply reviewed plan and replace boxes" }).click();
  await expect(page.getByRole("dialog")).toContainText("Packing data changed. Preview again before replacing boxes");
  expect(box.box_id).toBe("box-1");
  await page.getByRole("button", { name: "Keep current boxes" }).click();
  state.failApply = false;
  await page.getByRole("button", { name: "Preview replan", exact: true }).click();
  await page.getByRole("button", { name: "Apply reviewed plan and replace boxes" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.locator("#box-replacement")).toBeVisible();
  await expect(page.getByRole("button", { name: "Finalize Packing", exact: true })).toBeEnabled();
});

test("packing setup and layer controls remain usable on a phone", async ({ page }) => {
  await mockSetup(page, "FLOOR_PACKING");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/packing/setup");
  await expect(page.getByRole("region", { name: "Editable product worksheet" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.goto("/packing?orderId=order");
  await page.locator(".packing-box-details").evaluate(element => { (element as HTMLDetailsElement).open = true; });
  await expect(page.getByLabel("Box 1 packing rules", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: test.info().outputPath("packing-profiles-phone.png"), fullPage: true });
});
