import assert from "node:assert/strict";
import test from "node:test";
import { thai, translateText } from "../../lib/translations.ts";

test("Thai messages retain their substitution fields", () => {
  const fields = text => (text.match(/\{\d+\}/g) || []).sort();
  for (const [source, target] of Object.entries(thai)) {
    assert.ok(target.trim(), source);
    assert.deepEqual(fields(target), fields(source), source);
    if (!fields(source).length) assert.equal(translateText(source, "th"), target, source);
  }
});

test("billing correction and sign-in recovery messages contain Thai", () => {
  for (const source of ["Customer details", "Save customer details", "Reload customer details", "Too many login attempts. Please try again later.", "Try again in {0}.", "Show upload help", "Hide upload help"]) {
    assert.match(thai[source], /[\u0e00-\u0e7f]/u, source);
  }
});

test("English and unknown text remain unchanged", () => {
  for (const text of ["Orders", "ลูกค้า อรุณ.xlsx", "EL-047", "Order YACHI shipment ETD 07.08.2026.xlsx", "Unrecognized server detail"]) {
    assert.equal(translateText(text, "en"), text);
  }
  assert.equal(translateText("EL-047", "th"), "EL-047");
  assert.equal(translateText("Order YACHI shipment ETD 07.08.2026.xlsx", "th"), "Order YACHI shipment ETD 07.08.2026.xlsx");
});

test("Thai templates preserve names, spacing, quantities and punctuation", () => {
  assert.equal(translateText("Select Order  ลูกค้า [1].xlsx", "th"), "เลือก Order  ลูกค้า [1].xlsx");
  assert.equal(translateText("Actual packs for EL-047", "th"), "จำนวนแพ็คจริงสำหรับ EL-047");
  assert.equal(translateText("1–25 of 200 records", "th"), "รายการ 1–25 จาก 200");
  assert.equal(translateText("12 packs / 2.4 kg", "th"), "12 แพ็ค / 2.4 กก.");
  assert.equal(translateText("Close รายการจัดซื้อรวม", "th"), "ปิด รายการจัดซื้อรวม");
  assert.equal(translateText("  Sign in ", "th"), "  เข้าสู่ระบบ ");
});
