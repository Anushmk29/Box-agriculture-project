import assert from "node:assert/strict";
import test from "node:test";
import { beginSubmission, pendingSubmission, finishSubmission } from "../../lib/submission.ts";

function storage() {
  const rows = new Map();
  return { getItem: key => rows.get(key) ?? null, setItem: (key, value) => rows.set(key, value), removeItem: key => rows.delete(key) };
}

test("lost responses and new page instances reuse the persisted key; changed details cannot replace it", () => {
  const store = storage();
  const first = beginSubmission(store, "account", "/order/one", '{"packs":2}');
  assert.deepEqual(pendingSubmission(store, "account", "/order/one"), first);
  assert.equal(beginSubmission(store, "account", "/order/one", first.body).key, first.key);
  assert.throws(() => beginSubmission(store, "account", "/order/one", '{"packs":3}'), /pending submission/);
  finishSubmission(store, "account", first);
  assert.notEqual(beginSubmission(store, "account", "/order/one", first.body).key, first.key);
});

test("accounts and target resources do not share pending intent", () => {
  const store = storage();
  const one = beginSubmission(store, "one", "/payment/one", "{}");
  assert.equal(pendingSubmission(store, "two", one.path), null);
  assert.equal(pendingSubmission(store, "one", "/payment/two"), null);
  assert.notEqual(beginSubmission(store, "two", one.path, "{}").key, one.key);
});

test("missing identity and failed persistence prevent submission; stale completion cannot clear a newer intent", () => {
  const store = storage();
  assert.throws(() => beginSubmission(store, undefined, "/payment", "{}"), /account session/);
  assert.throws(() => beginSubmission({ ...store, setItem: () => { throw new Error("storage denied"); } }, "one", "/payment", "{}"), /storage denied/);
  const first = beginSubmission(store, "one", "/payment", "{}");
  finishSubmission(store, "one", first);
  const second = beginSubmission(store, "one", "/payment", "{}");
  finishSubmission(store, "one", first);
  assert.deepEqual(pendingSubmission(store, "one", "/payment"), second);
});

test("corrupt or transplanted pending data cannot silently become a new submission", () => {
  const store = storage();
  const intent = beginSubmission(store, "one", "/payment", "{}");
  store.setItem("box-submission:v1:one:/payment", "invalid-json");
  assert.throws(() => beginSubmission(store, "one", "/payment", "{}"));
  store.setItem("box-submission:v1:two:/payment", JSON.stringify(intent));
  assert.throws(() => beginSubmission(store, "two", "/payment", "{}"), /needs review/);
});
