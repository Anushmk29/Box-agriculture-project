import { expect, test, type Page } from "@playwright/test";

async function mockFloor(page: Page, count = 2) {
  const jwt = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  await page.context().addCookies([{ name: "access_token", value: `qa.${jwt}.qa`, domain: "127.0.0.1", path: "/" }]);
  const state = { prepares: 0, saves: 0, finalizes: 0, failFinalize: false, failSearch: false, createdAt: new Date().toISOString() };
  const boxes = Array.from({ length: count }, (_, index) => index + 1).map(n => ({ box_id: `box-${n}`, box_number: n, box_type_name: "Small", box_catalogue_id: "small", box_material: "FOAM", safe_net_capacity_kg: 5, allocation_explanation: "Compatible cold herbs; safe capacity 5 kg.", is_overridden: false, actual_net_weight_kg: null as number | null, actual_gross_weight_kg: null as number | null, is_locked: false, qr_code_uid: `QR-${n}`, sticker_label: `Box ${n}`, items: [{ item_id: `item-${n}`, product_id: "basil", product_code: "BASIL", produce_name: "Basil", pack_size: "1kg", packed_count: 1, net_weight_kg: 1, adjustment_type: "AS_ORDERED" }] }));
  const packing = { client_order_id: "order", order_ref_id: "ORD-SUNNY", status: "FLOOR_PACKING", packing_approval_status: "PENDING", packing_review_note: "Old review", sub_customer_code: "SUNNY", route_code: "CT", shipment_date: "2026-10-09", total_boxes_count: count, boxes };
  await page.route("**/backend/api/v3/**", async route => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.endsWith("/auth/me")) await route.fulfill({ json: { full_name: "Floor Staff", role: "GENERAL", must_change_password: false, permissions: ["floor_packing", "design_packing"] } });
    else if (path.endsWith("/packing/orders")) await route.fulfill({ json: { total: 1, offset: 0, limit: 25, has_more: false, items: [{ order_id: "order", order_ref_id: "ORD-SUNNY", customer_code: "SUNNY", customer_name: "Sunny shop", created_at: state.createdAt, status: packing.status, boxes: boxes.length, outstanding: boxes.filter(box => box.actual_net_weight_kg === null).length, shipment_date: packing.shipment_date, route: "CT" }] } });
    else if (path.endsWith("/sub-packing-lists")) await route.fulfill({ json: { group_name: "ALL", sub_customers: [{ client_order_id: "order", order_ref_id: "ORD-SUNNY", sub_customer_code: "SUNNY", company_name: "Sunny shop", created_at: state.createdAt, status: packing.status, boxes_count: boxes.length, outstanding_boxes: boxes.filter(b => b.actual_net_weight_kg === null).length, total_estimated_net_weight_kg: 2 }] } });
    else if (path.endsWith("/prepare")) {
      state.prepares++;
      if (packing.status === "HANDOFF_01_SENT") packing.status = "FLOOR_PACKING";
      await route.fulfill({ json: { status: packing.status } });
    } else if (path.endsWith("/packing-list")) await route.fulfill({ json: packing });
    else if (path.endsWith("/weights")) {
      state.saves++;
      await route.fulfill({ status: 404, json: { detail: "Removed endpoint" } });
    } else if (path.endsWith("/finalize")) {
      state.finalizes++;
      if (state.failFinalize) await route.fulfill({ status: 409, json: { detail: "Handoff failed: packing remains editable" } });
      else { packing.status = "HANDOFF_02_RETURNED"; boxes.forEach(b => b.is_locked = true); await route.fulfill({ json: { status: packing.status, handoff_id: "handoff" } }); }
    } else if (path.endsWith("/master-data/products")) {
      if (state.failSearch) await route.fulfill({ status: 503, json: { detail: "Catalogue unavailable" } });
      else await route.fulfill({ json: url.searchParams.get("q")?.includes("missing") ? [] : [{ id: "basil", t_code: "BASIL", name_en: "Basil", name_vi: "" }, { id: "mint", t_code: "MINT", name_en: "Mint", name_vi: "" }] });
    } else if (path.endsWith("/items")) {
      const body = route.request().postDataJSON();
      const box = boxes.find(b => path.includes(b.box_id))!;
      box.items[0].product_id = body.product_id;
      box.items[0].net_weight_kg = body.net_weight_kg;
      box.items[0].packed_count = body.quantity;
      box.items[0].produce_name = body.product_id === "mint" ? "Mint" : "Basil";
      box.actual_net_weight_kg = box.actual_gross_weight_kg = null;
      await route.fulfill({ json: { item_id: box.items[0].item_id } });
    } else await route.fulfill({ json: [] });
  });
  return state;
}

test("staff review and finalize without scale entry, including handoff retry", async ({ page }) => {
  const state = await mockFloor(page);
  await page.goto("/packing/orders");
  await page.getByRole("link", { name: "Resume packing SUNNY", exact: true }).click();
  await expect(page.locator("#box-box-1")).toBeVisible();
  await expect(page.getByLabel(/scale (net|gross)/)).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Save verified weights", exact: true })).toHaveCount(0);
  expect(state.prepares).toBe(0);
  state.failFinalize = true;
  await page.getByRole("button", { name: "Finalize Packing", exact: true }).click();
  await expect(page.getByText("Handoff failed: packing remains editable", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Finalize Packing", exact: true })).toBeEnabled();
  state.failFinalize = false;
  await page.getByRole("button", { name: "Finalize Packing", exact: true }).click();
  await expect(page.getByText("Packing complete. Ready for accounting.", { exact: true })).toBeVisible();
  await page.locator(".packing-box-details").first().evaluate(element => { (element as HTMLDetailsElement).open = true; });
  await expect(page.getByRole("button", { name: "Edit / substitute" })).toHaveCount(0);
  expect(state.finalizes).toBe(2);
  expect(state.saves).toBe(0);
});

test("product search and substitutions allow immediate finalization", async ({ page }) => {
  const state = await mockFloor(page);
  await page.goto("/packing?orderId=order");
  await page.locator(".packing-box-details").first().evaluate(element => { (element as HTMLDetailsElement).open = true; });
  await page.getByRole("button", { name: "Edit / substitute" }).first().click();
  await page.getByLabel("Find product", { exact: true }).fill("missing");
  await expect(page.getByText(/No matching products/)).toBeVisible();
  state.failSearch = true;
  await page.getByLabel("Find product", { exact: true }).fill("min");
  await expect(page.getByText("Catalogue unavailable", { exact: true })).toBeVisible();
  state.failSearch = false;
  await page.getByLabel("Find product", { exact: true }).fill("mint");
  await page.getByLabel("Master product").selectOption("mint");
  await page.getByLabel("Total item net weight (kg)").fill("1.25");
  await page.getByRole("button", { name: "Save packing adjustment" }).click();
  await expect(page.getByRole("row").filter({ hasText: "Mint" }).first()).toBeVisible();
  await expect(page.locator("#box-box-1 .packing-box-saved")).toContainText("1.25 kg");
  await expect(page.getByRole("button", { name: "Finalize Packing", exact: true })).toBeEnabled();
  expect(state.saves).toBe(0);
});

test("repeated finalization clicks submit only one operation", async ({ page }) => {
  const state = await mockFloor(page);
  await page.goto("/packing?orderId=order");
  const finalize = page.getByRole("button", { name: "Finalize Packing", exact: true });
  await expect(finalize).toBeEnabled();
  await finalize.evaluate(button => {
    button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  await expect(page.getByText("Packing complete. Ready for accounting.", { exact: true })).toBeVisible();
  expect(state.finalizes).toBe(1);
  expect(state.saves).toBe(0);
});

test("scanned box beyond first page is selected on a phone and across layouts", async ({ page }) => {
  await mockFloor(page, 30);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/packing?orderId=order&box=QR-27");
  await expect(page.locator("#box-box-27")).toBeVisible();
  await expect(page.locator("#box-box-27")).toHaveClass(/scanned-box/);
  await expect(page.getByLabel(/scale (net|gross)/)).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.setViewportSize({ width: 1440, height: 900 });
  await expect(page.locator("#box-box-27")).toBeVisible();
  await page.setViewportSize({ width: 768, height: 900 });
  await expect(page.locator("#box-box-27")).toBeVisible();
});

test("box pagination and search retain access to finalization", async ({ page }) => {
  const state = await mockFloor(page, 35);
  await page.goto("/packing?orderId=order");
  await expect(page.locator("#box-box-1")).toBeVisible();
  await page.getByRole("button", { name: "Next boxes", exact: true }).click();
  await expect(page.locator("#box-box-26")).toBeVisible();
  await page.getByRole("button", { name: "Previous boxes", exact: true }).click();
  await expect(page.locator("#box-box-1")).toBeVisible();
  await page.getByLabel("Find box or product").fill("missing");
  await expect(page.getByText("No matching boxes. Clear the search to see all boxes.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Finalize Packing", exact: true })).toBeEnabled();
  await page.getByLabel("Find box or product").fill("");
  await expect(page.locator("#box-box-1")).toBeVisible();
  expect(state.saves).toBe(0);
});

test.describe("order creation dates", () => {
  test.use({ timezoneId: "Asia/Calcutta" });
  test("age uses local calendar dates across midnight", async ({ page }) => {
    const state = await mockFloor(page);
    await page.clock.setFixedTime(new Date("2026-10-09T00:30:00+05:30"));
    for (const [created, label] of [
      ["2026-10-08T19:15:00Z", "Today"],
      ["2026-10-08T18:15:00Z", "Yesterday"],
      ["2026-10-06T18:15:00Z", "3 days ago"],
    ]) {
      state.createdAt = created;
      await page.goto("/packing");
      await page.getByRole("link", { name: "Choose an order", exact: true }).click();
      await expect(page.getByRole("row").filter({ hasText: "Sunny shop" })).toContainText(label);
    }
  });
});
