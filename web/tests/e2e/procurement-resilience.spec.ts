import { expect, test, type BrowserContext, type Page } from "@playwright/test";

async function buyerDesk(page: Page, context: BrowserContext, dispatched = false) {
  const payload = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  await context.addCookies([{ name: "access_token", value: `qa.${payload}.qa`, domain: "127.0.0.1", path: "/" }]);
  const original = { id: "original", t_code: "ORIGINAL", name_en: "Ordered herb", name_vi: "Herb" };
  const substitute = { id: "replacement", t_code: "REPLACEMENT", name_en: "Substitute herb", name_vi: "Herb" };
  let status = dispatched ? "HANDOFF_01_SENT" : "PURCHASING_CONSOLIDATED";
  const submissions: unknown[] = [];
  await page.route("**/backend/api/v3/**", async route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/auth/me")) {
      await route.fulfill({ json: { full_name: "Market Buyer", role: "GENERAL", must_change_password: false,
        permissions: ["confirm_procurement", "floor_packing"] } });
    } else if (url.pathname.endsWith("/batches/resilient")) {
      await route.fulfill({ json: { batch_id: "resilient", batch_reference: "PUR-RESILIENT", status,
        substitutions_supported: true,
        total_skus: 1, total_ordered_weight_kg: 10, total_buffer_weight_kg: 0,
        total_required_weight_kg: 10, items: [{ item_id: "line", product_id: original.id,
          product_code: original.t_code, product_name: original.name_en, produce_type: "HERB", pack_size: "1kg",
          ordered_pack_count: 10, ordered_weight_kg: 10, buffer_percentage: 0, buffer_pack_count: 0,
          buffer_weight_kg: 0, total_purchase_target_packs: 10, total_purchase_target_weight_kg: 10,
          required_packs: 10, required_weight_kg: 10, confirmed_purchased_pack_count: dispatched ? 8 : null,
          confirmed_purchased_weight_kg: dispatched ? 8 : null,
          ...(dispatched ? { replacement_product_id: substitute.id, replacement_product_code: substitute.t_code,
            replacement_product_name: substitute.name_en } : {}),
        }] } });
    } else if (url.pathname.endsWith("/master-data/products")) {
      expect(url.searchParams.get("limit")).toBe("50");
      await route.fulfill({ json: [original, substitute] });
    } else if (url.pathname.endsWith("/resilient/confirm")) {
      submissions.push(route.request().postDataJSON());
      status = "HANDOFF_01_SENT";
      await route.fulfill({ json: { status, batch_reference: "PUR-RESILIENT" } });
    } else await route.fulfill({ json: [] });
  });
  await page.goto("/procurement?batchId=resilient");
  await expect(page.getByTitle("Confirmed Pack Count", { exact: true })).toHaveValue(dispatched ? "8" : "10");
  return submissions;
}

test("buyer approves a substitute, keeps original visible and submits actual identity", async ({ page, context }) => {
  const submissions = await buyerDesk(page, context);
  await page.getByRole("button", { name: "Choose substitute for ORIGINAL" }).click();
  const dialog = page.getByRole("dialog", { name: "Choose substitute", exact: true });
  await expect(dialog.getByRole("button", { name: "Approve ORIGINAL Ordered herb" })).toHaveCount(0);
  await dialog.getByRole("button", { name: "Approve REPLACEMENT Substitute herb" }).click();
  await expect(page.getByText("Buyer-approved substitute:")).toContainText("REPLACEMENT Substitute herb");
  await expect(page.getByRole("cell").filter({ hasText: "Ordered herb" }).first()).toBeVisible();
  await page.getByRole("link", { name: "Back to register" }).click();
  await page.getByRole("dialog", { name: "Leave this record?" }).getByRole("button", { name: "Cancel" }).click();
  await page.getByTitle("Confirmed Pack Count", { exact: true }).fill("8");
  await page.getByTitle("Confirmed Total Weight (kg)", { exact: true }).fill("8");
  await page.getByRole("button", { name: "Confirm purchases and send to packing", exact: true }).click();
  await expect(page.getByRole("link", { name: "Continue to packing", exact: true }).first()).toBeVisible();
  expect(submissions).toEqual([{ items: [{ item_id: "line", purchased_pack_count: 8,
    purchased_weight_kg: 8, replacement_product_id: "replacement" }] }]);
  await expect(page.getByRole("button", { name: "Choose substitute for ORIGINAL" })).toHaveCount(0);
});

test("contradictory zero quantities are blocked before submission", async ({ page, context }) => {
  const submissions = await buyerDesk(page, context);
  await page.getByTitle("Confirmed Total Weight (kg)", { exact: true }).fill("0");
  await page.getByRole("button", { name: "Confirm purchases and send to packing", exact: true }).click();
  await expect(page.getByText(/Correct the highlighted pending products/)).toBeVisible();
  expect(submissions).toHaveLength(0);
  await page.getByTitle("Confirmed Pack Count", { exact: true }).fill("0");
  await page.getByRole("button", { name: "Confirm purchases and send to packing", exact: true }).click();
  await expect(page.getByRole("link", { name: "Continue to packing", exact: true }).first()).toBeVisible();
  expect(submissions).toEqual([{ items: [{ item_id: "line", purchased_pack_count: 0, purchased_weight_kg: 0 }] }]);
});

for (const width of [1440, 390]) {
test(`confirmed batch shows the packing next step on reload at ${width}px`, async ({ page, context }) => {
  await page.setViewportSize({ width, height: 1000 });
  const submissions = await buyerDesk(page, context, true);
  await expect(page.getByText("Buyer-approved substitute:")).toContainText("REPLACEMENT Substitute herb");
  await expect(page.getByRole("button", { name: "Choose substitute for ORIGINAL" })).toHaveCount(0);
  await expect(page.getByTitle("Confirmed Pack Count", { exact: true })).toBeDisabled();
  const nextStep = page.getByRole("region", { name: "Next: Packing", exact: true });
  await expect(nextStep).toContainText("Purchasing complete");
  const continueLink = nextStep.getByRole("link", { name: "Continue to packing", exact: true });
  await expect(continueLink).toBeVisible();
  await expect(continueLink).toHaveAttribute("href", "/packing/orders?batch_id=resilient&view=all&sort=shipment_date&direction=asc");
  await expect(page.getByRole("button", { name: "Confirm purchases and send to packing", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Adjust buffers", exact: true })).toHaveCount(0);
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `../outputs/purchasing-next-step-${width}.png`, animations: "disabled" });
  await continueLink.click();
  await expect(page).toHaveURL(/\/packing\/orders\?batch_id=resilient&view=all/);
  expect(submissions).toHaveLength(0);
});
}
