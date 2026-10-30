// One shared counter keeps parallel requests and chained loads in the same modal.
let pending = 0;
let visible = false;
let completionTimer: ReturnType<typeof setTimeout> | undefined;
const listeners = new Set<() => void>();

function publish(next: boolean) {
  if (visible === next) return;
  visible = next;
  listeners.forEach(listener => listener());
}

export function beginLoading() {
  if (typeof window === "undefined") return () => {};
  clearTimeout(completionTimer);
  pending += 1;
  publish(true);
  let finished = false;
  return () => {
    if (finished) return;
    finished = true;
    pending -= 1;
    if (pending === 0) {
      // Let callers commit their results or start the next request first.
      completionTimer = setTimeout(() => {
        if (pending === 0) publish(false);
      }, 0);
    }
  };
}

export function subscribeLoading(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export const getLoadingSnapshot = () => visible;
export const getServerLoadingSnapshot = () => false;
