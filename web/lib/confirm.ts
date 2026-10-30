// A styled replacement for window.confirm. One request at a time; ConfirmHost renders it.
export type ConfirmRequest = {
  title: string;
  body: string;
  confirmLabel: string;
  tone?: "default" | "danger";
  resolve: (value: boolean) => void;
};

let current: ConfirmRequest | null = null;
const listeners = new Set<() => void>();

function publish() { listeners.forEach(listener => listener()); }

export function confirmAction(options: Omit<ConfirmRequest, "resolve">): Promise<boolean> {
  current?.resolve(false);
  return new Promise(resolve => {
    current = { ...options, resolve };
    publish();
  });
}

export function settleConfirm(value: boolean) {
  const request = current;
  current = null;
  publish();
  request?.resolve(value);
}

export function subscribeConfirm(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export const getConfirm = () => current;
export const getServerConfirm = () => null;
