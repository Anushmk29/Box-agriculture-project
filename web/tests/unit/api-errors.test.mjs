import assert from "node:assert/strict";
import test from "node:test";

import { ApiError, isRevisionConflict } from "../../lib/api-errors.ts";

test("validation errors name the field and human line number", () => {
  const error = new ApiError(422, [{ loc: ["body", "items", 1, "unit_price_eur"],
    msg: "Value error, Enter a finite positive amount" }]);
  assert.equal(error.message, "Line 2: Unit price: Enter a finite positive amount");
  assert.equal(error.status, 422);
});

test("revision conflicts are distinct from other business and network failures", () => {
  assert.equal(isRevisionConflict(new ApiError(409, { code: "STALE_REVISION", current_revision: 4 })), true);
  assert.equal(isRevisionConflict(new ApiError(428, { code: "REVISION_REQUIRED" })), true);
  assert.equal(isRevisionConflict(new ApiError(409, { code: "MISSING_SELLING_PRICES" })), false);
  assert.equal(isRevisionConflict(new Error("Network failure")), false);
});

test("validation messages do not echo raw credential inputs", () => {
  const error = new ApiError(422, [{ loc: ["body", "password"], msg: "Password is too long",
    input: "private-password", ctx: { private: "private-password" } }]);
  assert.equal(error.message, "Password: Password is too long");
  assert.equal(error.message.includes("private-password"), false);
});

test("existing business errors and unstructured failures stay readable", () => {
  assert.equal(new ApiError(409, { code: "MISSING_SELLING_PRICES", message: "Enter the selling price" }).message,
    "Enter the selling price");
  assert.equal(new ApiError(400, "Already recorded").message, "Already recorded");
  assert.equal(new ApiError(502, null).message, "Request failed with status 502");
  assert.equal(new ApiError(422, [null, "bad shape"]).message, "Request failed with status 422");
});

test("large validation lists produce a bounded useful message", () => {
  const detail = Array.from({ length: 20 }, (_, index) => ({ loc: ["body", "items", index, "amount_paid"], msg: "Required" }));
  const message = new ApiError(422, detail).message;
  assert.match(message, /Line 1: Amount paid: Required/);
  assert.match(message, /Line 3: Amount paid: Required/);
  assert.equal(message.includes("Line 4:"), false);
});
