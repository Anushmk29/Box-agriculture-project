/** One unresolved intent per account/resource in this browser tab.
 * Persist before sending; fail closed when storage is unavailable. Never expire
 * an uncertain intent or silently replace it with an edited submission.
 */
export type Submission = { actor: string; key: string; path: string; body: string };

type Store = Pick<Storage, "getItem" | "setItem" | "removeItem">;

function slot(actor: string | undefined, path: string) {
  if (!actor) throw new Error("Reload your account session before submitting.");
  return `box-submission:v1:${actor}:${path}`;
}

export function pendingSubmission(store: Store, actor: string | undefined, path: string): Submission | null {
  const raw = store.getItem(slot(actor, path));
  if (!raw) return null;
  const value = JSON.parse(raw);
  if (!value || value.actor !== actor || value.path !== path || typeof value.body !== "string" ||
      typeof value.key !== "string" || !/^[A-Za-z0-9_-]{16,128}$/.test(value.key)) {
    throw new Error("The pending submission needs review. Check the order or payment before entering it again.");
  }
  return value;
}

export function beginSubmission(store: Store, actor: string | undefined, path: string, body: string): Submission {
  const pending = pendingSubmission(store, actor, path);
  if (pending) {
    if (pending.body !== body) throw new Error("Finish retrying the pending submission before changing its details.");
    return pending;
  }
  const intent = { actor: actor ?? "", key: crypto.randomUUID(), path, body };
  store.setItem(slot(actor, path), JSON.stringify(intent));
  return intent;
}

export function finishSubmission(store: Store, actor: string | undefined, intent: Submission) {
  const pending = pendingSubmission(store, actor, intent.path);
  if (pending?.key === intent.key) store.removeItem(slot(actor, intent.path));
}
