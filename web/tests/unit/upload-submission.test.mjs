import assert from "node:assert/strict";
import test from "node:test";
import { fingerprintUpload, requireOriginalUpload } from "../../lib/upload-submission.ts";

test("upload fingerprint compares bytes, name and size before reusing a pending intent", async () => {
  const original = await fingerprintUpload(new File(["original"], "orders.xlsx"));
  const saved = { ...original, channel: "matrix", shipment_date: "2026-10-27", route_line: "CT", main_customer_id: "main" };
  assert.equal(original.sha256, "0682c5f2076f099c34cfdd15a9e063849ed437a49677e6fcc5b4198c76575be5");
  requireOriginalUpload(saved, await fingerprintUpload(new File(["original"], "orders.xlsx")));
  assert.throws(() => requireOriginalUpload(saved, { ...original, filename: "renamed.xlsx" }), /original file/);
  assert.throws(() => requireOriginalUpload(saved, { ...original, sha256: "different" }), /original file/);
});

test("empty and oversized files fail before reading their bytes", async () => {
  await assert.rejects(fingerprintUpload(new File([], "empty.xlsx")), /25 MB/);
  await assert.rejects(fingerprintUpload({ size: 26214401, arrayBuffer: () => { assert.fail("must not allocate an oversized upload"); } }), /25 MB/);
});
