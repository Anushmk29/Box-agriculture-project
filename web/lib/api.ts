import { beginLoading } from "./loading";
import { safeDestination } from "./workflow";
import { ApiError } from "./api-errors";

export { ApiError };

const API = "/backend/api/v3";

let sessionExpiry: Promise<void> | null = null;

function expireSession() {
  if (typeof window === "undefined" || sessionExpiry || window.location.pathname === "/login") return;
  // The cookie is HttpOnly, so use the server's logout endpoint to clear it.
  // Coalesce concurrent 401s into one logout and navigation.
  const next = safeDestination(window.location.pathname + window.location.search + window.location.hash);
  sessionExpiry = fetch(`${API}/auth/logout`, {
    method: "POST",
    credentials: "include",
    signal: AbortSignal.timeout(5000),
  }).then(() => {}, () => {}).finally(() => {
    window.location.replace(`/login?next=${encodeURIComponent(next)}`);
  });
}

export async function api<T>(path: string, init: RequestInit = {}, options: { background?: boolean; blob?: boolean } = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  // Page reads stay silent so tab navigation never opens a blocking dialog.
  const background = options.background ?? (method === "GET" || method === "HEAD");
  const finish = background ? () => {} : beginLoading();
  try {
    const headers = new Headers(init.headers);
    if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
    const response = await fetch(`${API}${path}`, {
      ...init,
      signal: init.signal
        ? AbortSignal.any([init.signal, AbortSignal.timeout(method === "GET" || method === "HEAD" ? 20_000 : 60_000)])
        : AbortSignal.timeout(method === "GET" || method === "HEAD" ? 20_000 : 60_000),
      headers,
      credentials: "include",
    });
    if (response.ok && options.blob) return await response.blob() as T;
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      if (response.status === 401 && path !== "/auth/login" && typeof window !== "undefined") {
        expireSession();
      }
      throw new ApiError(response.status, payload?.detail);
    }
    return payload as T;
  } finally {
    finish();
  }
}

/** Documents and reports: same request and error handling, read as a file, with the loading dialog. */
export function apiBlob(path: string): Promise<Blob> {
  return api<Blob>(path, {}, { background: false, blob: true });
}
