import { expect, test } from "@playwright/test";

test("history review uses available languages and requires physical fit confirmation", async ({ page }) => {
  const jwt = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  await page.context().addCookies([{ name: "access_token", value: `qa.${jwt}.qa`, domain: "127.0.0.1", path: "/" }]);
  const product = { id: "produce", t_code: "11-001", name_th: "ตะไคร้", name_vi: "Sả", name_en: "" };
  const boxes = [{ id: "box", size_name: "Flat", box_type: "CARDBOARD", packing_reviewed: true, is_active: true,
    length_cm: 50, width_cm: 32, height_cm: 18, max_safe_net_weight_kg: 10, packing_revision: 1, usable_volume_cm3: 26496 }];
  let saved: { verified_fit: boolean; items: unknown[]; status: string } | null = null;
  await page.route("**/backend/api/v3/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { id: "staff", full_name: "Staff", role: "GENERAL", permissions: ["floor_packing"], must_change_password: false } });
    if (path.endsWith("/packing-setup/boxes")) return route.fulfill({ json: boxes });
    if (path.endsWith("/packing-setup/table")) return route.fulfill({ json: { items: [], total: 0, has_more: false, summary: { total: 0, ready: 0 } } });
    if (path.endsWith("/feedback/summary")) return route.fulfill({ json: { observations: 0, boxes: 0, outcomes: {}, population: "Only boxes with feedback" } });
    if (path.endsWith("/packing-history")) return route.fulfill({ json: { source_version: "source-v1", total: 1, has_more: false,
      summary: { examples: 1, label_pages: 1, items: 1, approved: 0, examples_needing_source_review: 0 }, items: [{
        id: "example", shipment: "TETE 31.08.26", subcustomer: "BUYER", box_number: 1, box_name: "ก.แบน",
        source: "TETE/labels.pdf", pages: [1], delivery_date: "2026-08-31", issues: [], review: null, box_catalogue_id: "box",
        items: [{ ...product, code: "11001", product_id: "produce", pack_size: "100 G", pack_count: 10, net_weight_kg: 1,
          raw_weight: 1, raw_pack_quantity: 10, issues: [], candidates: [product],
          matching: { status: "MATCHED", matched_languages: ["th", "vi"], missing_languages: ["en"], conflicting_languages: [] } }],
      }] } });
    if (path.endsWith("/review")) {
      saved = route.request().postDataJSON();
      return route.fulfill({ json: { revision: 1, status: "APPROVED" } });
    }
    return route.fulfill({ json: [] });
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/packing/setup");
  await page.getByRole("tab", { name: "Packing history", exact: true }).click();
  await page.getByRole("button", { name: "Review box 1", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText(/languages matched: th, vi; unavailable: en/)).toBeVisible();
  await expect(dialog.getByLabel("Matched product")).toContainText("ตะไคร้ / Sả");
  await expect(dialog.getByRole("button", { name: "Approve checked combination" })).toBeDisabled();
  await dialog.getByLabel("Review note").fill("Staff verified the complete packing combination.");
  await dialog.getByLabel("Packing staff physically verified", { exact: false }).check();
  await page.screenshot({ path: "../outputs/packing-history-review-mobile.png", fullPage: true });
  await dialog.getByRole("button", { name: "Approve checked combination" }).click();
  await expect(page.getByText("Packing example review saved.", { exact: true })).toBeVisible();
  expect(saved).toMatchObject({ status: "APPROVED", verified_fit: true, items: [{ product_id: "produce", pack_count: 10, net_weight_kg: 1 }] });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: "../outputs/packing-history-mobile.png", fullPage: true });
});
