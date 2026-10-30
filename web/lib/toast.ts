// Action feedback shown in one place. Same external-store shape as lib/loading.ts.
export type ToastTone = "success" | "error" | "info";
export type ToastNext = { label: string; href: string };
export type Toast = { id: number; tone: ToastTone; message: string; next?: ToastNext };

const empty: Toast[] = [];
let toasts: Toast[] = empty;
let sequence = 0;
const listeners = new Set<() => void>();

function publish() { listeners.forEach(listener => listener()); }

function push(tone: ToastTone, message: string, next?: ToastNext) {
  // The same message from two places on one page should show once.
  const existing = toasts.find(item => item.message === message && item.tone === tone);
  if (existing) return existing.id;
  const id = ++sequence;
  // Keep at most three on screen; the oldest leaves first.
  toasts = [...toasts.slice(-2), { id, tone, message, next }];
  publish();
  return id;
}

export const toast = {
  success: (message: string, next?: ToastNext) => push("success", message, next),
  error: (message: string, next?: ToastNext) => push("error", message, next),
  info: (message: string, next?: ToastNext) => push("info", message, next),
};

export function dismissToast(id: number) {
  toasts = toasts.filter(item => item.id !== id);
  publish();
}

export function subscribeToasts(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export const getToasts = () => toasts;
export const getServerToasts = () => empty;
